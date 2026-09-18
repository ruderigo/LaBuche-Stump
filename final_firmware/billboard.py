# Project Stump -- Billboard (walk-up bulletin board)
# Place at: firmware/billboard.py
#
# Read-and-post local notices. Internal flash storage, not the SD card --
# SD mounting is untested this project, so v1 avoids that dependency;
# swapping storage later doesn't change this file's shape.
#
# Deliberately NOT wired to RNS/LXMF -- this is the walk-up "Substance"
# layer, separate from mesh messaging. The mailbox (the piece that does
# talk to RNS) is the next module, not this one.
#
# This module is pure logic: storage, rendering, escaping. barkeep.py
# owns the HTTP server and serves /billboard and /post by calling the
# functions here. See the note at the bottom of this file for why there
# isn't a second server in here.

import os
import time
import uasyncio as asyncio
import i18n

SD_STORAGE_FILE = "/sd/billboard.txt"
FLASH_STORAGE_FILE = "/billboard.txt"
MAX_ENTRY_LEN = 200
MAX_ENTRIES_SHOWN = 50

# Same retention model as rrc.py's DMs, by explicit request: a real,
# time-based expiry as the primary rule, with a count-based ceiling only
# as a last-resort safety valve, not the everyday mechanism. Genuinely
# different storage underneath, though -- DMs are in-memory and vanish
# on reboot by architecture; billboard posts are appended to a
# persistent file (SD or flash) that, before this, grew forever with no
# pruning at all. MAX_ENTRIES_SHOWN already existed but only ever
# limited what _render_page() displays -- the underlying file kept
# every post ever made, unbounded. This adds a real prune, not just a
# tighter display slice.
BILLBOARD_TTL_SECONDS = 72 * 3600
# Safety-valve ceiling only, matching MAX_DMS_PER_USER's own role and
# reasoning in rrc.py -- this is intentionally larger than
# MAX_ENTRIES_SHOWN (a display concern) since normal use across a real
# 72-hour window, from many different visitors, shouldn't run into a
# storage ceiling sized as though it were the only limit.
MAX_ENTRIES_STORED = 150


def _sd_available():
    """True when the SD card is mounted and writable.

    Checked directly rather than by importing fserv -- fserv already
    imports THIS module (for _extract_ip), so importing it back would be
    a circular import. os.stat on the mountpoint is enough: /sd only
    exists once something has actually been mounted there."""
    try:
        os.stat("/sd")
        return True
    except OSError:
        return False


def storage_file():
    """Where posts live. The SD card when it's available, internal flash
    otherwise.

    SD is preferred because flash has a limited erase/write budget and
    the billboard is the most frequently written thing on the node --
    every post rewrites it. Falling back rather than failing means the
    billboard still works with no card in the slot, which matters: it's
    the only shared, persistent surface a walk-up visitor has."""
    return SD_STORAGE_FILE if _sd_available() else FLASH_STORAGE_FILE


def migrate_to_sd():
    """Moves an existing flash-stored billboard onto the SD card once it
    becomes available. Called at boot after the card is mounted, so
    posts written before a card was fitted aren't stranded on flash and
    silently replaced by an empty SD-backed board.

    Append rather than overwrite: if both exist (card fitted, removed,
    posts written to flash, card refitted) neither set is lost."""
    if not _sd_available():
        return False
    try:
        os.stat(FLASH_STORAGE_FILE)
    except OSError:
        return False  # nothing on flash to migrate
    try:
        with open(FLASH_STORAGE_FILE) as src:
            data = src.read()
        if data.strip():
            with open(SD_STORAGE_FILE, "a") as dst:
                dst.write(data)
        os.remove(FLASH_STORAGE_FILE)
        print("[billboard] migrated existing posts from flash to SD")
        return True
    except Exception as e:
        print("[billboard] migration failed (posts left on flash):", e)
        return False


def _extract_ip(peername):
    """writer.get_extra_info('peername') on this MicroPython build's
    uasyncio returns a raw sockaddr_in bytearray, not a (ip, port) tuple
    like CPython's asyncio -- confirmed by direct testing against the
    real interpreter, not assumed. peer[0] on that bytearray returns an
    int (the first raw byte of the struct), not the IP string every
    caller of this expected -- that's what crashed every billboard post
    outright, and would have silently mistracked (actually also crashed,
    since Python evaluates default arguments eagerly) fserv's credit
    ledger too. Bytes 4-7 are the actual IPv4 address in the standard
    sockaddr_in layout, confirmed by decoding a real captured value
    (127.0.0.1 came back exactly at that offset). Falls back to treating
    it as an already-correct (ip, port) tuple, in case this ever runs
    under a different MicroPython port or CPython where get_extra_info
    behaves the standard way.

    Lives here (not in barkeep.py or fserv.py) because both of those
    need it and fserv.py can't import barkeep.py without a circular
    import -- barkeep.py already imports fserv.py."""
    if isinstance(peername, (bytes, bytearray)) and len(peername) >= 8:
        return "%d.%d.%d.%d" % (peername[4], peername[5], peername[6], peername[7])
    if isinstance(peername, tuple) and peername:
        return peername[0]
    return "unknown"


def _esc(s):
    """Minimal HTML escaping -- MicroPython has no html.escape built in.

    Escapes both quote characters, not just double quotes. Every
    attribute in this codebase is written single-quoted (value='...'),
    and a value containing an apostrophe -- confirmed directly against
    a real admin password -- closed that attribute early, silently
    truncating whatever came before the apostrophe. The truncated value
    is what a browser then actually submits back, which fails
    server-side re-validation and looks exactly like "delete doesn't
    work" from the outside: no error a user would notice, just a
    password that quietly isn't the one they typed. &#39; (the decimal
    numeric reference) rather than &apos;, since the latter isn't
    recognized in all HTML parsers -- the numeric form always is.
    """
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;")
             .replace("'", "&#39;"))


def _url_decode(s):
    s = s.replace("+", " ")
    out = ""
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                out += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out += s[i]
        i += 1
    return out


def _signature(identifier):
    """Short, consistent tag for an identifier -- not the identifier
    itself. A raw IP next to every post would be a real privacy
    regression from the project's own anonymous-entries premise; this
    just lets you tell 'same poster as before' apart from a stranger,
    deterministically, without publishing anything identifying."""
    h = 0
    for ch in identifier:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return "%04x" % (h & 0xFFFF)


def _read_entries():
    """Returns a list of (signature, text, timestamp) tuples.

    Three storage generations, all handled without dropping anything --
    matching this file's own established rule (see the older comment
    this one extends): a format change is never a reason to lose a
    post, even a very old one.
      - newest: "sig\\tts\\ttext"  (this version -- has a real timestamp)
      - older:  "sig\\ttext"        (had signatures, no timestamp yet)
      - oldest: "text"              (written before signatures existed)
    timestamp is None for either older generation, since there's no way
    to know how old they actually are -- _prune_expired_entries()
    treats that as "just posted now" and stamps it going forward,
    rather than either keeping it forever or deleting it outright the
    moment this ships.
    """
    try:
        with open(storage_file()) as f:
            out = []
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    sig, ts_str, text = parts
                    try:
                        ts = float(ts_str)
                    except ValueError:
                        ts = None
                elif len(parts) == 2:
                    sig, text = parts
                    ts = None
                else:
                    sig, text = "?", line
                    ts = None
                out.append((sig, text, ts))
            return out
    except OSError:
        return []


def _prune_and_write(new_entry=None):
    """Rewrites the storage file, keeping only entries within
    BILLBOARD_TTL_SECONDS -- the primary rule, checked first and given
    priority over MAX_ENTRIES_STORED, matching rrc.py's own DM
    retention exactly (see that module's _prune_dms for the same
    reasoning applied there). Entries with an unknown age (older
    storage generations -- see _read_entries's own docstring) are
    treated as posted right now rather than either immortal or
    instantly expired, so upgrading to this version doesn't silently
    wipe an existing board's whole history the moment someone posts
    again -- that content gets a fresh 72-hour window from here,
    not deleted outright and not kept forever either.

    new_entry, if given, is a (sig, text, ts) tuple appended AFTER
    time-based pruning but BEFORE the MAX_ENTRIES_STORED ceiling is
    applied -- doing the ceiling check inclusive of the entry about to
    be written, in the same pass, rather than pruning first and
    appending separately afterward. The separate-steps version of this
    had a real off-by-one: pruning to exactly the ceiling and then
    appending on top of that still ends one over, exactly like
    rrc.py's send_dm() re-checks the count AFTER appending rather than
    only before -- confirmed directly by testing the exact boundary
    (ceiling reached precisely, then one more post) before finding
    this.

    Writes to a temp file and renames over the original rather than
    truncating and rewriting the live file directly -- a rewrite
    (unlike the plain append this replaces entirely) has a real window
    where the file could be left empty or half-written if power drops
    mid-write; the rename is atomic on the filesystems this runs on,
    so the original file is never observably incomplete.
    """
    entries = _read_entries()
    now = time.time()
    kept = []
    stamped = 0
    for sig, text, ts in entries:
        if ts is None:
            # A tiny, strictly increasing offset per entry stamped in
            # THIS call -- confirmed directly as a real, reported bug
            # without it: several untimestamped entries (an old board's
            # posts, never pruned before this feature existed) all
            # landed on the exact same `now` value, since it's computed
            # once per call and was being reused verbatim for every one
            # of them. delete_entry() identifies a post by its
            # timestamp alone, so two posts sharing one meant deleting
            # "the one at that timestamp" deleted both -- selecting one
            # checkbox deleted every post that collided onto that same
            # stamp. A microsecond-scale offset keeps each one unique
            # while staying "now" for everything that reads it: the
            # 72-hour TTL check two lines down, and display order.
            effective_ts = now + stamped * 0.000001
            stamped += 1
        else:
            effective_ts = ts
        if now - effective_ts <= BILLBOARD_TTL_SECONDS:
            kept.append((sig, text, effective_ts))
    if new_entry is not None:
        kept.append(new_entry)
    # Safety-valve ceiling only, applied after time-based pruning above
    # -- oldest-first, matching MAX_DMS_PER_USER's own eviction order
    # and its own reasoning for why this is secondary, not primary.
    if len(kept) > MAX_ENTRIES_STORED:
        kept = kept[-MAX_ENTRIES_STORED:]

    target = storage_file()
    tmp = target + ".tmp"
    try:
        with open(tmp, "w") as f:
            for sig, text, ts in kept:
                f.write(sig + "\t" + str(ts) + "\t" + text + "\n")
        os.rename(tmp, target)
    except OSError:
        # Best-effort: a full card or similar mid-rewrite failure
        # leaves the original file untouched rather than risking it --
        # the next post's prune attempt just tries again.
        try:
            os.remove(tmp)
        except OSError:
            pass


def _append_entry(text, identifier="unknown"):
    text = text[:MAX_ENTRY_LEN].replace("\n", " ").replace("\r", "")
    if not text.strip():
        return False
    new_entry = (_signature(identifier), text, time.time())
    _prune_and_write(new_entry)
    return True


def delete_entry(ts_str):
    """Removes one post, identified by the exact timestamp string
    _read_entries's own float(ts_str) was parsed from -- confirmed
    directly that str(float(x)) round-trips exactly for real
    timestamps on this MicroPython build, so comparing as strings
    here (never re-parsing to float and comparing floats) sidesteps
    any float-formatting mismatch risk entirely.

    Only ever removes the FIRST matching line, never every line that
    matches -- this used to remove all of them, which is a real,
    reported bug: this docstring previously claimed a timestamp
    collision would need two separate HTTP requests landing within
    microseconds of each other, and dismissed that as no real risk.
    That reasoning missed a case entirely -- _prune_and_write()
    stamping SEVERAL untimestamped entries together in one call, not
    two requests at all, and before its own fix that meant every one
    of them got the exact same value. Selecting one checkbox for
    deletion then matched and removed every post that happened to
    share that stamp, which is exactly what got reported: one post
    checked, the whole board emptied. _prune_and_write() no longer
    produces that collision, but this stays a first-match-only removal
    regardless, as a second, independent layer: if a collision ever
    happens again from some cause this hasn't anticipated either, one
    checkbox can still only ever remove one post, not silently take
    out everything sharing its identifier.

    Returns True only if a matching entry genuinely existed and was
    removed -- a caller can't tell "already gone" from "never existed"
    from this, which is fine for its one use (an admin deleting
    something they can currently see listed).

    Same temp-file-then-rename safety as _prune_and_write: a rewrite
    has a real window where the file could be left incomplete if power
    drops mid-write, which the rename avoids by only ever replacing the
    original in one atomic step.
    """
    target = storage_file()
    try:
        with open(target) as f:
            lines = f.readlines()
    except OSError:
        return False

    kept = []
    found = False
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped.strip():
            continue
        parts = stripped.split("\t", 2)
        if not found and len(parts) == 3 and parts[1] == ts_str:
            found = True
            continue
        kept.append(stripped)

    if not found:
        return False

    tmp = target + ".tmp"
    try:
        with open(tmp, "w") as f:
            for line in kept:
                f.write(line + "\n")
        os.rename(tmp, target)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def _render_page(lang=None):
    if lang is None:
        lang = i18n.DEFAULT_LANG
    entries = _read_entries()[-MAX_ENTRIES_SHOWN:]
    entries.reverse()  # newest first
    items = "".join(
        "<li>" + _esc(text) + " <small>&mdash; " + sig + "</small></li>"
        for sig, text, ts in entries
    ) or ("<li>" + i18n.t("billboard_nothing_yet", lang) + "</li>")
    return (
        "<h1>" + i18n.t("billboard_title", lang) + "</h1>"
        + i18n.switcher_html(lang, "/billboard") +
        "<p class='sub'>" + i18n.t("billboard_intro", lang) + "</p>"
        "<form method='POST' action='/post' class='row'>"
        "<input name='entry' maxlength='" + str(MAX_ENTRY_LEN) + "' placeholder='" +
        i18n.t("billboard_post_placeholder", lang) + "'>"
        "<button type='submit'>" + i18n.t("billboard_post_button", lang) + "</button>"
        "</form>"
        "<div class='panel'>"
        "<ul>" + items + "</ul>"
        "</div>"
    )



# NOTE: this module deliberately has NO HTTP server of its own.
# barkeep.py is the single server on port 80 and calls the pure
# functions above (_render_page, _append_entry, _esc, _url_decode,
# _extract_ip) directly. A second handler here would be unreachable --
# it could never bind the same port -- and a duplicate copy of the same
# request-parsing code is exactly what caused three separate divergence
# bugs on this project: a bytes-vs-str send bug, a stale credit-weights
# path, and unguarded request parsing that had been fixed in barkeep but
# not here. One handler, one place to fix.
