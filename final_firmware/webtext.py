# Small text helpers the pages share: HTML escaping, URL encoding and
# decoding, and whether the SD card is there. One copy here, instead of
# one per module (billboard, fserv, rrc_ui, i18n, barkeep, plugins and
# stumpid each had their own, and they had drifted: one escaper missed
# the apostrophe, one URL encoder wrote characters past U+00FF as
# "%2019"). Imports nothing of the Stump's own, so any module can use it
# without an import loop.

import os


def esc(s):
    """Text made safe inside HTML and inside an attribute, quoted either
    way: both quote characters are escaped (every attribute here is
    single-quoted, and an apostrophe once cut an admin password short).
    &#39; rather than &apos;, which not every parser knows."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"


def url_encode(s):
    """Percent-encoding for a query string (MicroPython has no
    urllib.parse.quote): each UTF-8 byte, so url_decode() is its exact
    inverse and accented file names keep working."""
    out = ""
    for ch in s:
        if ch in _SAFE:
            out += ch
        else:
            for b in ch.encode("utf-8"):
                out += "%%%02X" % b
    return out


def url_decode(s):
    """application/x-www-form-urlencoded text, decoded as UTF-8 (browsers
    send "é" as %C3%A9: each %XX is a byte, not a character). Falls back
    to one character per byte if it isn't UTF-8, so a malformed request
    can't raise."""
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


def text(b):
    """Bytes from a request (a line, a header, a body) as text: UTF-8, or
    Latin-1 when it isn't valid UTF-8 -- every byte maps to a character
    there, so this can't fail. A plain .decode() raised on a body that
    isn't UTF-8 and the request ended with no answer at all (found by
    the web fuzz test, step 3 of the roadmap; first seen in a header
    from the Android app)."""
    try:
        return b.decode()
    except UnicodeError:
        return "".join(chr(c) for c in b)


def same(a, b):
    """a == b for secrets (passwords, tags), in a time that doesn't tell
    how much of a guess was right. Text or bytes."""
    if a is None or b is None:
        return False
    if isinstance(a, str):
        a = a.encode()
    if isinstance(b, str):
        b = b.encode()
    # The length isn't secret; looping over the guess keeps a wrong length
    # from returning faster than a wrong character would.
    r = len(a) ^ len(b)
    for i in range(len(a)):
        r |= a[i] ^ b[i % len(b)] if b else 1
    return r == 0


def sd_available():
    """True when something is mounted at /sd."""
    try:
        os.stat("/sd")
        return True
    except OSError:
        return False
