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
# Posts are a title (always shown) plus an optional body (shown when
# the title is tapped). Both are bounded so the worst case fits the
# server's 8 KB form-body limit even fully percent-encoded: 600 chars
# of 3-byte UTF-8 is 5400 bytes as %XX, plus a 240-char title's 2160.
MAX_TITLE_LEN = 80
MAX_BODY_LEN = 600
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
    """Decodes application/x-www-form-urlencoded text as UTF-8.

    Browsers percent-encode each UTF-8 BYTE of a character -- "é" is
    sent as %C3%A9. The previous version turned each %XX into its own
    character, so "café" arrived as "cafÃ©". French is this node's
    default language, so this garbled every accented billboard post,
    and anything else decoded here (admin passwords, file-delete
    checkboxes, download names). Collects the bytes first and decodes
    them once. Falls back to one character per byte only if the input
    isn't valid UTF-8 at all, so a malformed request can't raise.
    """
    s = s.replace("+", " ")
    buf = bytearray()
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "%" and i + 2 < n:
            try:
                buf.append(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        buf.extend(s[i].encode("utf-8"))
        i += 1
    try:
        return bytes(buf).decode("utf-8")
    except Exception:
        return "".join(chr(b) for b in buf)


def _escape_body(text):
    """Bodies may span lines, but each post is one line on disk.
    Backslash first, so an escaped newline can't be forged by typing
    a literal backslash-n."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _unescape_body(text):
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            nxt = text[i + 1]
            out.append("\n" if nxt == "n" else nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


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
    """Returns a list of (signature, title, body, timestamp, post_id)
    tuples.

    Current line format: "sig\\tid\\ttitle\\tbody" (body escaped by
    _escape_body). Posts written before titles existed have three
    fields; their whole text becomes the title and the body is empty,
    so they keep displaying exactly as they did.

    post_id is the ts field EXACTLY as written on disk -- a string, and
    the only thing that identifies a post (checkbox values, deletion).
    timestamp is that same string parsed to a float, used for the TTL
    check ONLY. The two are deliberately separate: this board's
    MicroPython build uses single-precision floats (~7 significant
    digits), so a float parsed from "1789740233" and turned back into
    text comes out as "1.78974e+09" -- the same string for every post
    within about two minutes of each other. Using str(float) for
    identity is what made a real, reported admin delete of entry #2
    remove entry #1 instead.

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
                parts = line.split("\t", 3)
                raw = None
                body = ""
                if len(parts) >= 3:
                    sig, ts_str, text = parts[0], parts[1], parts[2]
                    if len(parts) == 4:
                        body = _unescape_body(parts[3])
                    try:
                        ts = float(ts_str)
                        raw = ts_str
                    except ValueError:
                        ts = None
                elif len(parts) == 2:
                    sig, text = parts
                    ts = None
                else:
                    sig, text = "?", line
                    ts = None
                out.append((sig, text, body, ts, raw))
            return out
    except OSError:
        return []


def _rand9():
    """9 random decimal digits. os.urandom is hardware RNG on the ESP32;
    the fallbacks only matter off-device (tests, desktop Python)."""
    try:
        n = int.from_bytes(os.urandom(4), "big")
    except Exception:
        try:
            import random
            n = random.getrandbits(30)
        except Exception:
            n = int(time.time() * 1000)
    return n % 1000000000


def _new_post_id(seconds, taken):
    """A post id: whole seconds, then 9 random digits after a dot --
    e.g. "1789740233.004821917". Still parses as a number (so the TTL
    check keeps working, to within float precision, which is plenty
    for a 72-hour window), but its identity is the STRING, which no
    float round-trip ever touches again. The random part is what makes
    it unique rather than time: two posts in the same second, or an
    off-grid board whose clock restarts from zero on every reboot,
    would otherwise collide."""
    while True:
        pid = "%d.%09d" % (int(seconds), _rand9())
        if pid not in taken:
            taken.add(pid)
            return pid


def _clock_floor():
    """The earliest reading a correctly-set clock can give: 2025-01-01,
    computed with the device's own mktime so it's right whichever epoch
    the firmware counts from. None if mktime isn't available, in which
    case the clock is trusted as before."""
    for t in ((2025, 1, 1, 0, 0, 0, 0, 0), (2025, 1, 1, 0, 0, 0, 0, 0, 0)):
        try:
            return time.mktime(t)
        except Exception:
            pass
    return None


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

    new_entry, if given, is a (sig, title, body) tuple appended AFTER
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
    floor = _clock_floor()
    clock_ok = floor is None or now >= floor
    kept = []
    taken = set()
    for sig, text, body, ts, pid in entries:
        # Expiry only trusts timestamps from a clock that was actually
        # set. The CAM's clock is set by NTP when it joins the router
        # at boot, and a failed sync is tolerated -- the clock then
        # counts from the epoch, so posts that session get tiny
        # timestamps. After the next boot WITH a sync, those posts
        # looked decades old and every prune (each new post, each
        # admin page load) silently removed all of them at once -- a
        # real, reported "delete doesn't remove what I selected".
        trusted = ts is not None and (floor is None or ts >= floor)
        if clock_ok:
            if not trusted:
                # Stamped before the clock was ever right (or never
                # stamped): its age is unknowable, so it counts as
                # posted now -- the same rule as pre-timestamp posts --
                # and gets a fresh id stamped with the real time.
                effective_ts = now
                pid = None
            else:
                effective_ts = ts
            if now - effective_ts > BILLBOARD_TTL_SECONDS:
                continue
        else:
            # No valid clock this boot: ages can't be measured at all,
            # so nothing expires by time (the ceiling below still holds).
            effective_ts = ts if ts is not None else now
        # Each post's on-disk id is carried through VERBATIM -- never
        # regenerated from the parsed float. The previous version
        # wrote str(ts) back here, which on this board's single-
        # precision floats collapsed every recent post to the same
        # "1.78974e+09" on every single rewrite (every new post, every
        # admin page load). A post with no id yet (older storage
        # formats) gets one; so does a DUPLICATE id, which is exactly
        # what boards already running the previous version have on
        # disk right now -- this repairs them on the first rewrite.
        if pid is None or pid in taken:
            pid = _new_post_id(effective_ts, taken)
        else:
            taken.add(pid)
        kept.append((sig, text, body, pid))
    if new_entry is not None:
        kept.append((new_entry[0], new_entry[1], new_entry[2], _new_post_id(now, taken)))
    # Safety-valve ceiling only, applied after time-based pruning above
    # -- oldest-first, matching MAX_DMS_PER_USER's own eviction order
    # and its own reasoning for why this is secondary, not primary.
    if len(kept) > MAX_ENTRIES_STORED:
        kept = kept[-MAX_ENTRIES_STORED:]

    target = storage_file()
    tmp = target + ".tmp"
    try:
        with open(tmp, "w") as f:
            for sig, text, body, pid in kept:
                f.write(sig + "\t" + pid + "\t" + text + "\t" + _escape_body(body) + "\n")
        os.rename(tmp, target)
    except OSError:
        # Best-effort: a full card or similar mid-rewrite failure
        # leaves the original file untouched rather than risking it --
        # the next post's prune attempt just tries again.
        try:
            os.remove(tmp)
        except OSError:
            pass


def _clean_title(text):
    return " ".join(text.replace("\t", " ").replace("\r", " ").replace("\n", " ").split())


def _append_entry(title, body="", identifier="unknown"):
    """Adds a post. A title is required; the body is optional.

    A post with a body but no title (a client that only filled one
    field) takes the body's first line as its title rather than being
    rejected. Tabs become spaces everywhere, since tab is the field
    separator on disk; the body keeps its line breaks."""
    body = body.replace("\r", "").replace("\t", " ").strip()[:MAX_BODY_LEN]
    title = _clean_title(title)[:MAX_TITLE_LEN]
    if not title and body:
        title = _clean_title(body.split("\n", 1)[0])[:MAX_TITLE_LEN]
    if not title:
        return False
    _prune_and_write((_signature(identifier), title, body))
    return True


def delete_entry(ts_str):
    """Removes one post, identified by its post id -- the ts field
    exactly as stored on disk, compared as a string, never parsed.

    An earlier version of this docstring claimed str(float(x))
    round-trips exactly for real timestamps. That was only ever
    tested on desktop MicroPython, which uses double precision; this
    board's ESP32 build uses SINGLE precision, where it does not --
    see _read_entries() for the real, reported bug that caused.

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
            found = parts[2].split("\t", 1)[0] or True
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
    # The removed post's title (truthy), so the caller can say exactly
    # what was deleted instead of just how many.
    return found


def _post_item(sig, title, body):
    """One billboard row. With a body, the title is a native
    <details>/<summary> toggle -- tap to expand, no JavaScript, works
    on the kiosk and with screen readers. Without one, a plain row:
    nothing to expand, so nothing that looks tappable."""
    head = _esc(title) + " <small>&mdash; " + _esc(sig) + "</small>"
    if not body:
        return "<li>" + head + "</li>"
    return ("<li><details class='post'><summary>" + head + "</summary>"
            "<div class='post-body'>" + _esc(body) + "</div></details></li>")


def _render_page(lang=None):
    if lang is None:
        lang = i18n.DEFAULT_LANG
    entries = _read_entries()[-MAX_ENTRIES_SHOWN:]
    entries.reverse()  # newest first
    items = "".join(
        _post_item(sig, title, body) for sig, title, body, ts, pid in entries
    ) or ("<li>" + i18n.t("billboard_nothing_yet", lang) + "</li>")
    # Every translated string that lands inside a quoted attribute goes
    # through _esc -- an apostrophe in a translation would otherwise end
    # the attribute early, the same bug class this project has hit twice.
    return (
        "<h1>" + i18n.t("billboard_title", lang) + "</h1>"
        + i18n.switcher_html(lang, "/billboard") +
        "<p class='sub'>" + i18n.t("billboard_intro", lang) + "</p>"
        "<form method='POST' action='/post' class='post-form'>"
        "<input name='title' required maxlength='" + str(MAX_TITLE_LEN) + "' placeholder='"
        + _esc(i18n.t("billboard_title_placeholder", lang)) + "'>"
        "<textarea name='body' rows='3' maxlength='" + str(MAX_BODY_LEN) + "' placeholder='"
        + _esc(i18n.t("billboard_body_placeholder", lang)) + "'></textarea>"
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
