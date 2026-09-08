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
import uasyncio as asyncio
import i18n

SD_STORAGE_FILE = "/sd/billboard.txt"
FLASH_STORAGE_FILE = "/billboard.txt"
MAX_ENTRY_LEN = 200
MAX_ENTRIES_SHOWN = 50


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
    """Minimal HTML escaping -- MicroPython has no html.escape built in."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


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
    """Returns a list of (signature, text) tuples. Old-format entries
    (written before signatures existed) have no tab separator -- they're
    shown with a '?' signature rather than dropped."""
    try:
        with open(storage_file()) as f:
            out = []
            for line in f:
                line = line.rstrip("\n")
                if not line.strip():
                    continue
                if "\t" in line:
                    sig, text = line.split("\t", 1)
                else:
                    sig, text = "?", line
                out.append((sig, text))
            return out
    except OSError:
        return []


def _append_entry(text, identifier="unknown"):
    text = text[:MAX_ENTRY_LEN].replace("\n", " ").replace("\r", "")
    if not text.strip():
        return False
    with open(storage_file(), "a") as f:
        f.write(_signature(identifier) + "\t" + text + "\n")
    return True


def _render_page(lang=None):
    if lang is None:
        lang = i18n.DEFAULT_LANG
    entries = _read_entries()[-MAX_ENTRIES_SHOWN:]
    entries.reverse()  # newest first
    items = "".join(
        "<li>" + _esc(text) + " <small>&mdash; " + sig + "</small></li>"
        for sig, text in entries
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
