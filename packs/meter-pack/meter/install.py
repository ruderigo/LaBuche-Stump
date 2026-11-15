# Meter -- a tester's counter. How much text the Stump sends people's
# apps over LXMF, how many bytes that takes as sent, and how many of
# those messages fit one LoRa packet. The same pack on any firmware
# since 1.0.0-beta.53, so a kit can be measured before and after an
# update: before 1.0.0-beta.74 text goes as it is; from then on it goes
# packed to the apps that read it (urns/ztext.py), and the numbers say
# by how much.
#
#   /meter          the numbers since the Stump started (or the last reset)
#   /meter 1|2|3    sends you a fixed text of 1, 2 or 3 lines to measure
#   /meter last     what your last test took
#   /meter reset    back to zero
#
# It only counts: the mesh_out hook (plugin API 2) looks at each message
# to a person's app and returns None, so nothing sent is changed. It
# keeps nothing on the SD card and starts no task. Messages between
# Stumps don't go through the hook and aren't counted.

import time

VERSION = "1.0"
STUMP_API = 2

# One opportunistic LXMF packet: LXMessage.ENCRYPTED_PACKET_MAX_CONTENT.
# Past it the message needs a link and several packets.
ONE_PACKET = 295
MAX_TESTERS = 64       # people whose last test is remembered
COOLDOWN_S = 20        # between two tests by the same person (airtime)
MARK = "meter 1/"      # how a test's first line starts

# The test: the same three lines for everyone, every time, in the
# Stump's three languages. Ordinary chat, nothing chosen to pack well.
LINES = (
    "The ferry was late again this morning, so we walked up to the lookout and waited there until the fog cleared off the lake.",
    "Le vieux poêle chauffe encore très bien, mais il faudra fendre du bois avant la fin de la semaine si le froid continue comme ça.",
    "Mi hermana dice que el puente sigue cerrado y que el autobús sale a las siete en punto desde la plaza del mercado.",
)

_api = []
_n = {}                # the counters (see _zero)
_tests = {}            # LXMF address (hex) -> (lines, text bytes, sent bytes, one packet?, at)
_asked = {}            # LXMF address (hex) -> when it last asked for a test


def setup(stump):
    _api.append(stump)
    _zero()
    stump.command("meter", _meter, help=lambda lang: stump.t("help", lang), mesh_lines=3, trigger=False)
    # After every pack that rewords (Simple is 50): what is counted is
    # what is sent.
    stump.mesh_out(_out, priority=95)


def _zero():
    _n.clear()
    _n.update({"since": time.time(), "msgs": 0, "text": 0, "sent": 0, "packed": 0, "one": 0, "one_plain": 0})
    _tests.clear()


def _router():
    try:
        from urns import lxmf
        return lxmf.ROUTER[0]
    except Exception:
        return None


def _packing():
    """None on a firmware without packed text (before 1.0.0-beta.74);
    else (on?, how many addresses here read it)."""
    r = _router()
    if r is None or not hasattr(r, "packs_for"):
        return None
    try:
        from urns import lxmf
        return bool(lxmf.PACKED_TEXT[0]), len(r.packs)
    except Exception:
        return None


def _sent_size(dest_hex, data):
    """The bytes `data` takes as the message's content to this address:
    packed where this firmware packs for it, else as it is."""
    r = _router()
    try:
        if r is not None and hasattr(r, "packs_for"):
            dest = bytes.fromhex(dest_hex)
            if r.packs_for(dest):
                return r.packed_size(dest, data)
    except Exception:
        pass
    return len(data)


def _bin(n):
    # msgpack: a byte string's header is 2 bytes up to 255, then 3.
    return n + (2 if n < 256 else 3)


def _one_packet(title_len, content_len):
    """True when a message with this title and content goes as a single
    LXMF packet. Its payload is [timestamp, title, content, {}]: 1 + 9
    bytes, the two byte strings, 1; LXMF counts that less 16. (Stamps,
    off unless config.LXMF_STAMPS, would take 18 to 34 bytes more.)"""
    return 11 + _bin(title_len) + _bin(content_len) - 16 <= ONE_PACKET


def _out(dest_hex, text, title):
    """Counts one message to a person's app. Never changes it."""
    try:
        data = text.encode() if isinstance(text, str) else bytes(text or b"")
        tlen = len((title or "").encode()) if isinstance(title, str) else len(title or b"")
        plain = len(data)
        sent = _sent_size(dest_hex, data)
        one = _one_packet(tlen, sent)
        _n["msgs"] += 1
        _n["text"] += plain
        _n["sent"] += sent
        if sent < plain:
            _n["packed"] += 1
        if one:
            _n["one"] += 1
        if _one_packet(tlen, plain):
            _n["one_plain"] += 1
        if isinstance(text, str) and MARK in text:
            if dest_hex not in _tests:
                while len(_tests) >= MAX_TESTERS:
                    _tests.pop(next(iter(_tests)))
            lines = sum(1 for l in text.split("\n") if l.startswith("meter "))
            _tests[dest_hex] = (lines, plain, sent, one, time.time())
    except Exception as e:
        try:
            _api[0].log("count:", e)
        except Exception:
            pass
    return None


def _pct(sent, text):
    return (sent * 100 + text // 2) // text if text else 100


def _release():
    try:
        import rrc
        return str((rrc.NODE_INFO or {}).get("release") or "?")
    except Exception:
        return "?"


def _stats(ctx):
    p = _packing()
    if p is None:
        state = ctx.t("state_none")
    elif not p[0]:
        state = ctx.t("state_off")
    else:
        state = ctx.t("state_on", n=p[1])
    mins = max(0, int(time.time() - _n["since"])) // 60
    return [
        ctx.t("head", rel=_release(), state=state),
        ctx.t("bytes", msgs=_n["msgs"], mins=mins, text=_n["text"], sent=_n["sent"],
              pct=_pct(_n["sent"], _n["text"]), packed=_n["packed"]),
        ctx.t("packets", one=_n["one"], msgs=_n["msgs"], plain=_n["one_plain"]),
    ]


def _last(ctx):
    t = _tests.get(ctx.client_id)
    if t is None:
        return [ctx.t("last_none")]
    lines, plain, sent, one, _ = t
    return [ctx.t("last", lines=lines, text=plain, sent=sent, pct=_pct(sent, plain),
                  how=ctx.t("how_one" if one else "how_link"))]


def _test(ctx, count):
    now = time.time()
    last = _asked.get(ctx.client_id)
    if last is not None and 0 <= now - last < COOLDOWN_S:
        return [ctx.t("wait", secs=int(COOLDOWN_S - (now - last)) + 1)]
    if ctx.client_id not in _asked:
        while len(_asked) >= MAX_TESTERS:
            _asked.pop(next(iter(_asked)))
    _asked[ctx.client_id] = now
    return ["meter %d/%d: %s" % (i + 1, count, LINES[i]) for i in range(count)]


def _meter(ctx, arg):
    a = (arg or "").strip().lower()
    if a in ("1", "2", "3"):
        return _test(ctx, int(a))
    if a == "last":
        return _last(ctx)
    if a == "reset":
        _zero()
        return [ctx.t("reset")]
    if a in ("", "stats"):
        return _stats(ctx)
    return [ctx.t("usage")]
