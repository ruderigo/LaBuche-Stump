# Meter -- a tester's counter. How much text the Stump sends people's
# apps over LXMF, how many bytes that takes as sent, and how many of
# those messages fit one LoRa packet. The same pack on any firmware
# since 1.0.0-beta.53, so a kit can be measured before and after an
# update: before 1.0.0-beta.82 text goes as it is; from then on it goes
# packed to the apps that read it (urns/ztext.py), and the numbers say
# by how much.
#
#   /meter          the numbers since the Stump started (or the last reset):
#                   to apps, from apps, to other Stumps (radio / chain)
#   /meter info     the firmware, whether it packs text, for how long it has counted
#   /meter 1|2|3    sends you a fixed text of 1, 2 or 3 lines to measure
#   /meter last     what your last test took
#   /meter room     an estimate from this room's latest lines
#   /meter reset    back to zero
#
# It only counts: the mesh_out hook (plugin API 2) looks at each message
# to a person's app and returns None, so nothing sent is changed. It
# keeps nothing on the SD card.
#
# What a Stump sends another Stump (a room's lines over the chain or the
# radio, the #lora-mesh broadcast) doesn't go through that hook. To count
# it too (1.2), the pack puts a counter in front of rrc_mesh._send_item,
# the one function every queued message leaves by: it counts, then calls
# the real one with the same item, whatever happened while counting. On a
# firmware where that function isn't there, Stumps just aren't counted.
#
# Nothing shown on the Wi-Fi page is counted (that isn't LXMF): asked
# from there, a test and /meter room give an estimate instead.

import time

VERSION = "1.3"
STUMP_API = 2

# One opportunistic LXMF packet: LXMessage.ENCRYPTED_PACKET_MAX_CONTENT.
# Past it the message needs a link and several packets.
ONE_PACKET = 295
MAX_TESTERS = 64       # people whose last test is remembered
COOLDOWN_S = 20        # between two tests by the same person (airtime)
MARK = "meter 1/"      # how a test's first line starts
ROOM_LINES = 20        # /meter room: at most this many of the room's latest lines,
ROOM_BYTES = 1500      # and this many bytes of them (each one is packed to measure it)

# The test: the same three lines for everyone, every time, in the
# Stump's three languages. Ordinary chat, nothing chosen to pack well.
LINES = (
    "The ferry was late again this morning, so we walked up to the lookout and waited there until the fog cleared off the lake.",
    "Le vieux poêle chauffe encore très bien, mais il faudra fendre du bois avant la fin de la semaine si le froid continue comme ça.",
    "Mi hermana dice que el puente sigue cerrado y que el autobús sale a las siete en punto desde la plaza del mercado.",
)

_api = []
_n = {}                # the counters (see _zero)
_wrapped = []          # [the firmware's own rrc_mesh._send_item], once ours is in front of it
_tests = {}            # who -> (lines, text bytes, sent bytes, one packet?, an estimate?)
_asked = {}            # LXMF address (hex) -> when it last asked for a test


def setup(stump):
    _api.append(stump)
    _zero()
    stump.command("meter", _meter, help=lambda lang: stump.t("help", lang), mesh_lines=3, trigger=False)
    # After every pack that rewords (Simple is 50): what is counted is
    # what is sent.
    stump.mesh_out(_out, priority=95)
    stump.every(60, _minute)
    # What people's apps send (1.3): each line a person on an app writes
    # in a room. Looked at, never answered.
    stump.on_message(_in, priority=95)
    _watch_stumps()


def _zero():
    _n.clear()
    _n.update({"mins": 0, "msgs": 0, "text": 0, "sent": 0, "packed": 0, "one": 0, "one_plain": 0,
               "f_msgs": 0, "f_text": 0, "f_sent": 0, "f_one": 0, "f_one_plain": 0,
               "r_msgs": 0, "r_text": 0, "r_sent": 0, "c_msgs": 0, "c_text": 0, "c_sent": 0})
    _tests.clear()


def _watch_stumps():
    """Our counter in front of rrc_mesh._send_item, once."""
    if _wrapped:
        return
    try:
        import rrc_mesh
        real = rrc_mesh._send_item
    except Exception:
        return                      # another firmware: Stumps aren't counted

    def counted(item):
        try:
            _to_stump(item)
        except Exception:
            pass
        return real(item)

    _wrapped.append(real)
    rrc_mesh._send_item = counted


def _active():
    try:
        import plugins
        return plugins._active("meter")
    except Exception:
        return True


def _by_radio(item):
    """True when this queued message leaves by the radio: the broadcast
    always; an LXMF message sent on the radio itself, or one left to
    its route when that route isn't the chain and a radio is on."""
    import rrc_mesh
    if item[0] == "lb":
        return True
    radio = rrc_mesh._radio()
    via = item[5] if len(item) > 5 else None
    if via is not None:
        return via is radio
    return radio is not None and rrc_mesh._chain_iface(item[1]) is None


def _to_stump(item):
    """Counts one queued message if it is for another Stump: the radio
    broadcast ("lb", text, interface[, packed]) or an LXMF message
    (router, address, text, ...) to an address that is a Stump's. One
    to a person's app is counted by _out, not here. Radio and chain
    apart (1.3): bytes only matter on the first."""
    if not _active():
        return
    if item[0] == "lb":
        data = item[1].encode()
        sent = len(data)
        if len(item) > 3 and item[3]:
            from urns import ztext
            sent = len(ztext.pack(data))
    else:
        import rrc, rrc_mesh
        h = bytes(item[1]).hex()
        p = rrc_mesh._peers.get(h)
        if not ((p and p.get("stump")) or rrc.is_stump(h)):
            return
        text = item[2]
        data = text.encode() if isinstance(text, str) else bytes(text or b"")
        sent = _sent_size(h, data)
    k = "r_" if _by_radio(item) else "c_"
    _n[k + "msgs"] += 1
    _n[k + "text"] += len(data)
    _n[k + "sent"] += sent


def _in(ctx):
    """Counts one room line written by a person on an app: its bytes as
    text, and as that app sent them (packed when this firmware and the
    app both pack: the same packing on both sides, so the same bytes)."""
    try:
        if not ctx.is_mesh or not isinstance(ctx.text, str):
            return
        data = ctx.text.encode()
        sent = _sent_size(ctx.client_id, data)
        _n["f_msgs"] += 1
        _n["f_text"] += len(data)
        _n["f_sent"] += sent
        if _one_packet(0, sent):
            _n["f_one"] += 1
        if _one_packet(0, len(data)):
            _n["f_one_plain"] += 1
    except Exception as e:
        try:
            _api[0].log("count in:", e)
        except Exception:
            pass


def _minute():
    # Counted, not read off the clock: a board starts in the year 2000
    # and jumps to today once it learns the time.
    _n["mins"] += 1


def _router():
    try:
        from urns import lxmf
        return lxmf.ROUTER[0]
    except Exception:
        return None


def _packing():
    """None on a firmware without packed text (before 1.0.0-beta.82);
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


def _estimate(data):
    """What `data` would take to an app that reads packed text, on this
    firmware: packed where it packs, else as it is."""
    p = _packing()
    if p is not None and p[0]:
        try:
            from urns import ztext
            return len(ztext.pack(data))
        except Exception:
            pass
    return len(data)


def _remember(who, entry):
    if who not in _tests:
        while len(_tests) >= MAX_TESTERS:
            _tests.pop(next(iter(_tests)))
    _tests[who] = entry


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
            lines = sum(1 for l in text.split("\n") if l.startswith("meter "))
            _remember(dest_hex, (lines, plain, sent, one, False))
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


def _info(ctx):
    p = _packing()
    if p is None:
        state = ctx.t("state_none")
    elif not p[0]:
        state = ctx.t("state_off")
    else:
        state = ctx.t("state_on", n=p[1])
    return [ctx.t("head", rel=_release(), state=state, mins=_n["mins"])]


def _stats(ctx):
    out = [
        ctx.t("apps", msgs=_n["msgs"], text=_n["text"], sent=_n["sent"], pct=_pct(_n["sent"], _n["text"]),
              packed=_n["packed"], one=_n["one"], plain=_n["one_plain"]),
        ctx.t("from", msgs=_n["f_msgs"], text=_n["f_text"], sent=_n["f_sent"], pct=_pct(_n["f_sent"], _n["f_text"]),
              one=_n["f_one"], plain=_n["f_one_plain"]),
        ctx.t("stumps", rm=_n["r_msgs"], rt=_n["r_text"], rs=_n["r_sent"], rp=_pct(_n["r_sent"], _n["r_text"]),
              cm=_n["c_msgs"], ct=_n["c_text"], cs=_n["c_sent"]) if _wrapped else ctx.t("stumps_none"),
    ]
    if not ctx.is_mesh:
        # The Wi-Fi page has room for more than the mesh's three lines.
        out = _info(ctx) + out + [ctx.t("wifi")]
    return out


def _last(ctx):
    t = _tests.get(ctx.client_id)
    if t is None:
        return [ctx.t("last_none")]
    lines, plain, sent, one, est = t
    line = ctx.t("last", lines=lines, text=plain, sent=sent, pct=_pct(sent, plain),
                 how=ctx.t("how_one" if one else "how_link"))
    return [line + (" " + ctx.t("est") if est else "")]


def _room(ctx):
    """This room's latest lines as they would go to an app, each on its
    own: an estimate from real traffic, wherever it is asked from."""
    try:
        import rrc
        msgs = [m for m in rrc.since(ctx.room, 0) if m.get("kind") != "system" and m.get("body")]
    except Exception:
        msgs = []
    n = text = sent = 0
    for m in reversed(msgs[-ROOM_LINES:]):
        data = ("<%s> %s" % (m.get("nick") or "?", m["body"])).encode()
        if text + len(data) > ROOM_BYTES and n:
            break
        n += 1
        text += len(data)
        sent += _estimate(data)
    if not n:
        return [ctx.t("room_none")]
    p = _packing()
    if p is None or not p[0]:
        return [ctx.t("room_plain", n=n, text=text)]
    return [ctx.t("room", n=n, text=text, sent=sent, pct=_pct(sent, text))]


def _test(ctx, count):
    now = time.time()
    last = _asked.get(ctx.client_id)
    if last is not None and 0 <= now - last < COOLDOWN_S:
        return [ctx.t("wait", secs=int(COOLDOWN_S - (now - last)) + 1)]
    if ctx.client_id not in _asked:
        while len(_asked) >= MAX_TESTERS:
            _asked.pop(next(iter(_asked)))
    _asked[ctx.client_id] = now
    out = ["meter %d/%d: %s" % (i + 1, count, LINES[i]) for i in range(count)]
    if not ctx.is_mesh:
        # On the Wi-Fi page nothing goes over LXMF, so there is nothing
        # for the hook to count: worked out here instead, as an estimate.
        data = "\n".join(out).encode()
        sent = _estimate(data)
        _remember(ctx.client_id, (count, len(data), sent, _one_packet(0, sent), True))
    return out


def _meter(ctx, arg):
    a = (arg or "").strip().lower()
    if a in ("1", "2", "3"):
        return _test(ctx, int(a))
    if a == "last":
        return _last(ctx)
    if a == "room":
        return _room(ctx)
    if a == "info":
        return _info(ctx)
    if a == "reset":
        _zero()
        return [ctx.t("reset")]
    if a in ("", "stats"):
        return _stats(ctx)
    return [ctx.t("usage")]
