# Bavard -- a bot that never stops talking, to load-test a Stump's
# guardrails (plugin posting budget, timers, links, SD logging) for days.
#
# Alone, it posts a line of numbers at a variable pace, each with a UTC
# date and time:   [2026-11-09 05:12:03.417Z] #000123 4821-9055-1770-2246
# The number is a scramble of the line's sequence number: unique for
# this bot, no lexicon, no memory to speak of.
#
# When it hears another Bavard on another Stump (a Reticulum announce of
# bavard.bot), the two open a link and play Molière, réplique by
# réplique, in the original French: each bot takes half the characters
# and posts its own lines on its own Stump, then passes the turn. Bot
# lines aren't relayed between Stumps (rrc.post bot=True), so the turn
# goes over the link and the whole play is in each node's transcript.
# Plays loop: the default collection lasts well past 72 hours.
#
# Everything goes through BarKeep's plugin posting budget (6 room lines in
# any rolling minute), with stump.post(..., wait=False): what it refuses is
# counted, logged as DROP and retried, never worked around. That's the
# point -- see README.md, "What it measures". A test tool: it starts
# paused unless BAVARD_ON = "on" (or /admin -> Bavard -> Resume).
# Plugin API 4 (stump.post; stump.node / on_announce / connect from 3).

import time

try:
    import urandom as random
except ImportError:
    import random

try:
    import ujson as json
except ImportError:
    import json

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

VERSION = "1.3"
STUMP_API = 4

APP, ASPECT = "bavard", "bot"
PROTO = 2              # 1.2: turns acknowledged ("ga"); one duet at a time
ANNOUNCE_TAG = b"bavard/1"

STALL_S = 90           # no word from the partner on their turn: nudge them
DEAD_S = 300           # nothing at all for this long: back to numbers
PING_S = 30
GO_RESEND_S = 8        # a turn passed and not acknowledged: passed again
HOLD_S = 600           # a bot whose duet broke offers its place in the play this long
FAILED_S = 120         # a bot that didn't answer a call isn't called again for this long
HELLO_S = 30           # a call (either way) with no hello after this long is given up
FLUSH_S = 10
KEEP_DAYS = 7          # transcript files kept on the card
LOG_DAY_MAX = 16 * 1024 * 1024
LAST_N = 20            # lines on the status page
LOG_MAX = 1000         # transcript lines held between flushes (RAM)
MAX_BURST = 20         # numbers posted in one second at most (before the budget)

# Pace: a delay before each line, in ms, drawn from weighted bands.
PACES = {
    # mostly quick, some near instant, some long silences
    "variable": ((40, 0, 250), (30, 500, 3000), (20, 3000, 15000), (10, 15000, 90000)),
    "burst": ((100, 0, 250),),                     # hammers the budget
    "quick": ((50, 0, 250), (50, 250, 2000)),
    "polite": ((100, 10500, 15000),),              # stays under 6 a minute
}

_api = []
_st = {
    "mode": "solo",          # "solo" (numbers) or "duet" (Molière)
    "paused": False,
    "seq": 0,                # numbers posted
    "tried": 0, "posted": 0, "dropped": 0, "retries": 0,
    "started": 0, "next_ms": 0,
    "lines_played": 0, "plays_done": 0,
    "links": 0, "link_drops": 0, "peers_heard": 0,
    "max_gap_ms": 0, "last_post_ms": 0,
    "rtt_ms": None, "busy": False, "errors": 0, "log_lost": 0,
}
_cur = {"p": 0, "i": 0, "n": 0}     # play, line in the play, lines played in all
_peer = {}                           # the partner: hex, nick, link, side...
_heard = {}                          # door hex -> [nick, last heard ms, state ("f", "h", "d")]
_duets = {}                          # "hexA+hexB" (sorted) -> last heard ms: duets going elsewhere
_failed = {}                         # door hex -> when a call to it failed
_door = [None]
_plays = []                          # [(slug, title, lines)]
_play = {}                           # the loaded play
_log = []                            # transcript lines waiting for the card
_last = []                           # last LAST_N lines, for the page
_dirty = [False]
_salt = [0]


# ── setup ────────────────────────────────────────────────────────────

def setup(stump):
    _api.append(stump)
    stump.nick = stump.setting("NICK", "Bavard")
    _st["started"] = _now()
    _st["next_ms"] = time.ticks_add(_now(), 15000)
    _st["paused"] = str(stump.setting("ON", "off") or "off").lower() not in ("on", "1", "true", "yes")
    _load_state()
    _load_index()
    d = _door[0] = stump.node(APP, ASPECT)
    d.on_link(_incoming)
    stump.on_announce(APP, (ASPECT,), _announce_heard)
    stump.every(1, _tick)
    stump.every(int(stump.setting("ANNOUNCE", 300) or 300), _announce)
    stump.every(FLUSH_S, _flush)
    stump.every(30, _save_state)
    h = lambda key: (lambda lang: stump.t(key, lang))
    stump.command(stump.name, _cmd, help=h("help"))
    stump.page("", _page, nav="Bavard", sub=lambda lang: stump.t("card_sub", lang))
    stump.page("stats.json", _stats_json, raw=True)
    stump.admin_panel("Bavard", _panel, _panel_post)
    _note("start", "Bavard %s, pace %s, room #%s" % (VERSION, _pace_name(), _room()))


def _now():
    return time.ticks_ms()


def _room():
    return str(_api[0].setting("ROOM", "bavard") or "bavard").lstrip("#")


def _pace_name():
    p = str(_api[0].setting("PACE", "variable") or "variable")
    return p if p in PACES else "variable"


def _rand(n):
    return random.getrandbits(30) % n if n > 0 else 0


def _delay():
    bands = PACES[_pace_name()]
    r = _rand(100)
    for w, lo, hi in bands:
        if r < w:
            return lo + _rand(hi - lo + 1)
        r -= w
    return bands[-1][1]


def _my_hex():
    d = _door[0]
    return d.hexhash if d is not None else None


# ── time ─────────────────────────────────────────────────────────────

def _synced():
    try:
        from urns.transport import Transport
        return bool(getattr(Transport, "_clock_synced", True))
    except Exception:
        return True


def _stamp():
    """2026-11-09 05:12:03.417Z (UTC; "~" in front while the node's clock
    hasn't been set by the network yet)."""
    try:
        ns = time.time_ns()
        s, ms = ns // 1000000000, (ns // 1000000) % 1000
    except Exception:
        s, ms = int(time.time()), 0
    t = time.gmtime(s)
    return "%s%04d-%02d-%02d %02d:%02d:%02d.%03dZ" % (
        "" if _synced() else "~", t[0], t[1], t[2], t[3], t[4], t[5], ms)


def _day():
    t = time.gmtime(int(time.time()))
    return "%04d%02d%02d" % (t[0], t[1], t[2])


# ── numbers ──────────────────────────────────────────────────────────

MULT = 7046029254386353131       # odd, not a multiple of 5: a bijection mod 10^16
MOD = 10 ** 16


def _numbers(seq):
    """Seq scrambled into 16 digits: never the same twice for this bot."""
    x = (seq * MULT + _salt[0]) % MOD
    s = "%016d" % x
    return "-".join((s[0:4], s[4:8], s[8:12], s[12:16]))


def _number_line():
    if _salt[0] == 0 and _my_hex():
        _salt[0] = int(_my_hex()[:12], 16) % MOD     # this bot's own numbers
    seq = _st["seq"] + 1
    return seq, "[%s] #%06d %s" % (_stamp(), seq, _numbers(seq))


# ── posting (through the plugin budget, always) ──────────────────────

def _post(text):
    """True if the line went into the room; False if refused (the
    plugin's posting budget, most of the time)."""
    s = _api[0]
    room = _room()
    _st["tried"] += 1
    ok = False
    try:
        import rrc
        if not rrc.room_exists(room):
            rrc.create_room(room)
        ok = s.post(text, room, wait=False) is True     # refused now, not queued: counted

    except Exception as e:
        s.log("post failed:", e)
    now = _now()
    if ok:
        _st["posted"] += 1
        if _st["last_post_ms"]:
            gap = time.ticks_diff(now, _st["last_post_ms"])
            if gap > _st["max_gap_ms"]:
                _st["max_gap_ms"] = gap
        _st["last_post_ms"] = now
        _last.append(text)
        del _last[:-LAST_N]
    else:
        _st["dropped"] += 1
    _keep(("OK  " if ok else "DROP") + " " + text)
    return ok


def _keep(line):
    if len(_log) >= LOG_MAX:
        del _log[:LOG_MAX // 4]                  # the card is slow or gone: forget the oldest
        _st["log_lost"] += LOG_MAX // 4
    _log.append(line)


def _note(kind, text):
    _keep("--   [%s] %s: %s" % (_stamp(), kind, text))


# ── the corpus ───────────────────────────────────────────────────────

def _corpus_dir():
    d = _api[0].setting("CORPUS", None)
    if d:
        return d
    try:
        import fserv
        if not fserv.sd_ok:
            return None
        return fserv.SD_MOUNT + "/tools/bavard"
    except Exception:
        return None


def _load_index():
    _plays[:] = []
    d = _corpus_dir()
    if d:
        try:
            with open(d + "/plays.txt", "rb") as f:
                for raw in f:
                    bits = raw.decode("utf-8").rstrip("\r\n").split("\t")
                    if len(bits) >= 3:
                        _plays.append((bits[0], bits[1], int(bits[2])))
        except Exception:
            _plays[:] = []
    if not _plays:
        import bavard.scene as scene
        _plays.append(("", scene.TITLE, len(scene.LINES)))
    if _cur["p"] >= len(_plays):
        _cur["p"], _cur["i"] = 0, 0


def _signature():
    return "%d:%d" % (len(_plays), sum(p[2] for p in _plays))


def _builtin_only():
    """Partners whose corpora differ both fall back to the built-in scene."""
    _plays[:] = []
    import bavard.scene as scene
    _plays.append(("", scene.TITLE, len(scene.LINES)))
    _cur["p"], _cur["i"] = 0, 0
    _play.clear()


async def _load_play(p):
    """Reads play p's line offsets, scenes and speakers (a few KB of RAM;
    the text stays on the card). Yields as it goes."""
    slug, title, _ = _plays[p]
    pl = {"p": p, "slug": slug, "title": title, "off": [], "scenes": [], "order": {}}
    if not slug:
        import bavard.scene as scene
        pl["lines"] = scene.LINES
        pl["scenes"] = [(0, scene.SCENE)]
        for who, _t in scene.LINES:
            if who not in pl["order"]:
                pl["order"][who] = len(pl["order"])
    else:
        pos, k = 0, 0
        with open(_corpus_dir() + "/" + slug + ".txt", "rb") as f:
            for raw in f:
                if raw[:2] == b"L\t":
                    who = raw[2:raw.index(b"\t", 2)].decode("utf-8")
                    if who not in pl["order"]:
                        pl["order"][who] = len(pl["order"])
                    pl["off"].append(pos)
                elif raw[:2] == b"S\t":
                    pl["scenes"].append((len(pl["off"]), raw[2:].decode("utf-8").strip()))
                pos += len(raw)
                k += 1
                if k % 64 == 0:
                    await asyncio.sleep(0)
    _play.clear()
    _play.update(pl)


def _count():
    return len(_play["lines"]) if "lines" in _play else len(_play.get("off", ()))


def _line(i):
    """(speaker, text) of line i of the loaded play."""
    if "lines" in _play:
        return _play["lines"][i]
    with open(_corpus_dir() + "/" + _play["slug"] + ".txt", "rb") as f:
        f.seek(_play["off"][i])
        bits = f.readline().decode("utf-8").rstrip("\r\n").split("\t", 2)
    return bits[1], bits[2]


def _scene_of(i):
    label = ""
    for start, lab in _play["scenes"]:
        if start > i:
            break
        label = lab
    return label


def _side_of(who):
    """0: the bot with the smaller destination hash; 1: the other. The
    characters alternate between them in the order they first speak."""
    return _play["order"].get(who, 0) % 2


def _my_side():
    me, other = _my_hex() or "", _peer.get("hex") or ""
    return 0 if me < other else 1


# ── the partner ──────────────────────────────────────────────────────

# One duet at a time, among all the Bavards that hear each other (1.2).
# Each one's announce says what it's doing: "f" free, "h<p>,<i>,<n>" free
# and holding a place in a play (its duet just broke), "d<hexA><hexB>" in
# a duet (the two, sorted). It announces again whenever that changes.
#  - A free bot that knows of a duet elsewhere stays out (no call, no answer).
#  - Two duets that formed at once: the one with the smaller pair stays,
#    the other breaks up.
#  - Who plays next is worked out the same way by everyone: a bot holding a
#    place first (the smallest), with the smallest other free bot; else the
#    two smallest free bots. The smaller of the two calls. So nobody races,
#    and a duet whose bot dropped goes on, with another, from where it was.

def _state():
    if _st["mode"] == "duet" and _peer.get("up"):
        return "d" + _pair()
    if _holding():
        return "h%d,%d,%d" % (_cur["p"], _cur["i"], _cur["n"])
    return "f"


def _pair(a=None, b=None):
    a = a or _my_hex() or ""
    b = b or _peer.get("hex") or ""
    return min(a, b) + max(a, b)


def _holding():
    h = _st.get("hold_until")
    return bool(h) and time.ticks_diff(h, _now()) > 0


def _seen_s():
    return 2 * int(_api[0].setting("ANNOUNCE", 300) or 300) + 60


def _announce():
    d = _door[0]
    if d is None or _st["paused"]:
        return
    nick = (_api[0].nick or "Bavard")[:16]
    d.announce(ANNOUNCE_TAG + b"|" + nick.encode("utf-8") + b"|" + _state().encode())


def _announce_heard(dest_hex, app_data):
    me = _my_hex()
    if not dest_hex or dest_hex == me:
        return
    data = bytes(app_data or b"")
    if not data.startswith(ANNOUNCE_TAG):
        return
    parts = data.split(b"|")
    nick = parts[1].decode("utf-8")[:16] if len(parts) > 1 else "?"
    state = parts[2].decode() if len(parts) > 2 else "f"      # (1.1: no state)
    if dest_hex not in _heard:
        _st["peers_heard"] += 1
        _note("heard", "%s (%s)" % (nick, dest_hex[:8]))
    now = _now()
    _heard[dest_hex] = [nick, now, state]
    if state.startswith("d") and len(state) == 65:
        pair = state[1:]
        _duets[pair] = now
        if _st["mode"] == "duet" and _peer.get("up") and pair != _pair() and pair < _pair():
            _note("duet", "another duet (%s+%s) was first: ours stops" % (pair[:8], pair[32:40]))
            _send({"k": "bye"})
            _solo(hold=False)
    else:
        for pr in [pr for pr in _duets if dest_hex in (pr[:32], pr[32:])]:
            del _duets[pr]                      # its duet is over


def _duet_elsewhere():
    me, now, lim = _my_hex() or "", _now(), _seen_s() * 1000
    for pr, at in list(_duets.items()):
        if time.ticks_diff(now, at) > lim:
            del _duets[pr]
        elif me not in (pr[:32], pr[32:]):
            return True
    return False


def _next_pair():
    """(caller, called) for the next duet, the same for every bot that
    heard the same announces; None if there's none to make."""
    me, now, lim = _my_hex() or "", _now(), _seen_s() * 1000
    pin = str(_api[0].setting("PARTNER", "") or "")
    free, holders = [me], ([me] if _holding() else [])
    for h, v in _heard.items():
        st = v[2] if len(v) > 2 else "f"
        if time.ticks_diff(now, v[1]) > lim or st.startswith("d"):
            continue
        if h in _failed and time.ticks_diff(now, _failed[h]) < FAILED_S * 1000:
            continue
        if pin and h != pin:
            continue
        free.append(h)
        if st.startswith("h"):
            holders.append(h)
    if len(free) < 2:
        return None
    free.sort()
    if holders:
        a = min(holders)
        b = [h for h in free if h != a][0]
    else:
        a, b = free[0], free[1]
    return (min(a, b), max(a, b))


def _match():
    """Called every second while solo: call the partner, or wait for its call."""
    if _st["paused"] or _st["mode"] == "duet" or _peer.get("link") is not None or _duet_elsewhere():
        return
    pr = _next_pair()
    me = _my_hex()
    if not pr or me not in pr:
        if _peer.get("waiting_since"):
            _peer.clear()
        return
    other = pr[1] if pr[0] == me else pr[0]
    nick = (_heard.get(other) or ["?"])[0]
    if pr[0] == me:
        _dial(other, nick)                      # the smaller one calls
    elif _peer.get("hex") != other:
        _peer.clear()
        _peer.update({"hex": other, "nick": nick, "waiting_since": _now()})


def _dial(dest_hex, nick):
    s = _api[0]
    if _peer.get("link") is not None and _peer.get("hex") == dest_hex:
        return
    r = s.connect(dest_hex, APP, (ASPECT,), identify=True)
    _peer.clear()
    _peer.update({"hex": dest_hex, "nick": nick, "link": r, "up": False,
                  "last_rx": _now(), "dialled": _now()})
    r.on_packet(_rx)
    r.on_close(_closed)
    _note("call", "%s (%s)" % (nick, dest_hex[:8]))
    _hello()


def _incoming(session):
    """The other bot called us. Who it is, its hello says -- checked
    against the identity it proved on the link (and BAVARD_PARTNER)."""
    if _peer.get("up") or _peer.get("link") is not None or _duet_elsewhere() or _st["paused"]:
        # One partner at a time; a call of ours under way isn't dropped
        # for it (it used to be, its link left open and still talking);
        # and no second duet.
        session.close()
        return
    _peer.clear()
    _peer.update({"link": session, "last_rx": _now(), "incoming": True})
    session.on_packet(_rx)


def _dest_of(identity_hex):
    """The bavard.bot destination of a Stump's identity (hex)."""
    from urns.identity import Identity
    from urns import const
    name = Identity.full_hash((APP + "." + ASPECT).encode())[:const.NAME_HASH_LENGTH // 8]
    return Identity.full_hash(name + bytes.fromhex(identity_hex))[:const.TRUNCATED_HASHLENGTH // 8].hex()


def _caller_ok(claimed):
    """An incoming link's hello: the destination it claims has to be the
    one of the identity it proved, and the pinned partner if there is one."""
    link = _peer.get("link")
    try:
        rid = link.remote_hex() if link is not None else None
        real = _dest_of(rid) if rid else None
    except Exception:
        real = None
    if not real or real != claimed:
        _note("refused", "a caller that didn't prove it is %s" % str(claimed)[:8])
        return False
    pin = str(_api[0].setting("PARTNER", "") or "")
    if pin and pin != claimed:
        _note("refused", "%s isn't BAVARD_PARTNER" % claimed[:8])
        return False
    return True


def _send(obj):
    link = _peer.get("link")
    if link is None:
        return False
    try:
        return link.send(json.dumps(obj).encode("utf-8")) is not False
    except Exception as e:
        _api[0].log("send failed:", e)
        return False


def _hello():
    _send({"k": "hi", "v": PROTO, "d": _my_hex(), "nick": (_api[0].nick or "")[:16],
           "c": _signature(), "cur": [_cur["p"], _cur["i"], _cur["n"]]})


def _adopt(cur):
    """Takes the partner's cursor if it is further along."""
    try:
        p, i, n = int(cur[0]), int(cur[1]), int(cur[2])
    except Exception:
        return False
    if n <= _cur["n"] or not 0 <= p < len(_plays) or i < 0:
        return False
    if p != _cur["p"]:
        _play.clear()
    _cur.update({"p": p, "i": i, "n": n})
    _dirty[0] = True
    return True


def _rx(data):
    try:
        m = json.loads(bytes(data).decode("utf-8"))
        k = m.get("k")
    except Exception:
        return
    _peer["last_rx"] = _now()
    if k == "hi":
        if m.get("v") != PROTO:
            _note("refused", "protocol %s" % m.get("v"))
            return
        if _peer.get("incoming") and not _peer.get("up") and not _caller_ok(m.get("d")):
            _solo()
            return
        if m.get("d") and _peer.get("incoming"):
            _peer["hex"] = m["d"]               # (proven just above; when we called, we know)
        _peer["nick"] = str(m.get("nick") or "?")[:16]
        first = not _peer.get("up")
        if m.get("c") != _signature() and _plays[0][0]:
            _note("corpus", "the partner's differs (%s vs %s): built-in scene" % (m.get("c"), _signature()))
            _builtin_only()
        _adopt(m.get("cur") or ())
        if first:
            if _duet_elsewhere():
                _note("refused", "a duet is going elsewhere")
                _send({"k": "bye"})
                _solo(hold=False)
                return
            _peer["up"] = True
            _st["links"] += 1
            _st["mode"] = "duet"
            _st["hold_until"] = 0
            _failed.pop(_peer.get("hex"), None)
            _note("duet", "with %s (%s)" % (_peer["nick"], (_peer.get("hex") or "")[:8]))
            _hello()                            # so they have our cursor too
            _announce()                         # the others: a duet is going
        _peer["kick"] = True                    # whoever's turn it is, goes
    elif k == "go":
        if not _peer.get("up"):
            return
        _send({"k": "ga", "n": (m.get("cur") or (0, 0, 0))[2]})     # got it
        if not _adopt(m.get("cur") or ()) and int((m.get("cur") or (0, 0, 0))[2]) < _cur["n"]:
            _peer["resend"] = True              # they're behind: our last turn was lost
        _peer["kick"] = True
    elif k == "bye":
        _note("link", "the partner left; numbers again")
        _solo()
    elif k == "ga":
        _peer["go_acked"] = True
    elif k == "pg":
        _send({"k": "po", "t": m.get("t")})
    elif k == "po":
        try:
            _st["rtt_ms"] = time.ticks_diff(_now(), int(m.get("t")))
        except Exception:
            pass


def _closed():
    if _peer.get("up"):
        _st["link_drops"] += 1
        _note("link", "closed; numbers again")
    elif _peer.get("dialled") and _peer.get("hex"):
        _failed[_peer["hex"]] = _now()          # it didn't take the call: someone else next
    _solo()


def _solo(hold=True):
    """Back to numbers. A duet that broke: this bot holds its place in the
    play for HOLD_S, so another bot takes over from there."""
    was = _st["mode"] == "duet"
    link = _peer.get("link")
    _peer.clear()
    if link is not None:
        try:
            link.close()
        except Exception:
            pass
    _st["mode"] = "solo"
    _st["next_ms"] = time.ticks_add(_now(), _delay())
    if was:
        _st["hold_until"] = time.ticks_add(_now(), HOLD_S * 1000) if hold else 0
        try:
            _announce()                         # the others: free again
        except Exception:
            pass


def _pass_turn():
    _send({"k": "go", "cur": [_cur["p"], _cur["i"], _cur["n"]], "t": _now()})
    _peer["sent_go"] = _now()
    _peer["go_acked"] = False


# ── the clock ────────────────────────────────────────────────────────

async def _tick():
    if _st["busy"] or _st["paused"]:
        return
    _st["busy"] = True
    try:
        if _st["mode"] == "duet":
            await _duet()
        else:
            await _solo_tick()
    except Exception as e:
        # Logged and counted, never raised: three failures in a row would
        # switch the timer off, and a bot that stops is a test that ends.
        _st["errors"] += 1
        _api[0].log("tick:", e)
        _note("error", str(e))
        await asyncio.sleep(1)
    finally:
        _st["busy"] = False


def _others_around():
    """Another Bavard heard lately (on any Stump), free or in a duet."""
    now, lim = _now(), _seen_s() * 1000
    return any(time.ticks_diff(now, v[1]) <= lim for v in _heard.values())


def _room_shared():
    try:
        import rooms
        return rooms.scope(_room()) != "local"
    except Exception:
        return False


async def _solo_tick():
    now = _now()
    if _peer.get("link") is not None and not _peer.get("up"):
        since = _peer.get("dialled") or _peer.get("last_rx") or now
        if time.ticks_diff(now, since) > HELLO_S * 1000:
            if _peer.get("dialled") and _peer.get("hex"):
                _failed[_peer["hex"]] = now
            _note("call", "no answer from %s" % (_peer.get("hex") or "?")[:8])
            _solo()                             # (it used to wait on that link for ever)
    _match()
    w = _peer.get("waiting_since")
    if w and not _peer.get("link") and time.ticks_diff(now, w) > 180000 and _peer.get("hex"):
        _dial(_peer["hex"], _peer.get("nick", "?"))    # they never called: we do
    if _room_shared() and _others_around():
        # The room goes to other Stumps and other Bavards are about: only
        # the duet shows there, not every bot's numbers. A Bavard with no
        # other to play with says its numbers there too (1.3: it went
        # quiet, as if broken, alone in a room shared over a chain).
        _st["next_ms"] = time.ticks_add(_now(), _delay())
        return
    for _ in range(MAX_BURST):
        wait = time.ticks_diff(_st["next_ms"], _now())
        if wait > 1000 or _st["mode"] != "solo":
            return
        if wait > 0:
            await asyncio.sleep(wait / 1000)
        seq, text = _number_line()
        if _post(text):
            _st["seq"] = seq
            _dirty[0] = True
        _st["next_ms"] = time.ticks_add(_now(), _delay())


async def _duet():
    now = _now()
    quiet = time.ticks_diff(now, _peer.get("last_rx", now))
    if not _peer.get("up"):
        _solo()
        return
    if quiet > DEAD_S * 1000:
        _st["link_drops"] += 1
        _note("link", "partner silent %d s; numbers again" % (quiet // 1000))
        _solo()
        return
    if time.ticks_diff(now, _peer.get("pinged", 0)) > PING_S * 1000:
        _peer["pinged"] = now
        _send({"k": "pg", "t": now})
    if not _play or _play.get("p") != _cur["p"]:
        await _load_play(_cur["p"])
    if _cur["i"] >= _count():
        _next_play()
        return
    who, _ = _line(_cur["i"])
    if _side_of(who) == _my_side():
        _peer["kick"] = False
        await _speak()
        return
    if _peer.pop("resend", False) or (_peer.pop("kick", False) and not _peer.get("sent_go")):
        _pass_turn()
    elif _peer.get("sent_go") and not _peer.get("go_acked") and \
            time.ticks_diff(now, _peer["sent_go"]) > GO_RESEND_S * 1000:
        _pass_turn()                             # not acknowledged: lost on the way
    elif quiet > STALL_S * 1000 and time.ticks_diff(now, _peer.get("sent_go", 0)) > STALL_S * 1000:
        _pass_turn()                             # nudge: maybe our last go was lost


async def _speak():
    """Our characters' lines, one by one, until it's the partner's turn."""
    while _st["mode"] == "duet" and not _st["paused"]:
        if _cur["i"] >= _count():
            _next_play()
            if not _play or _play.get("p") != _cur["p"]:
                await _load_play(_cur["p"])
        who, text = _line(_cur["i"])
        if _side_of(who) != _my_side():
            _pass_turn()
            return
        await asyncio.sleep(_delay() / 1000)
        if _st["mode"] != "duet":
            return
        line = "[%s] %s %s · %s — %s" % (_stamp(), _short(_play["title"]),
                                         _scene_of(_cur["i"]), who, text)
        if not _post(line):
            _st["retries"] += 1
            await asyncio.sleep(2)                   # the budget: wait, try the same line again
            continue
        _cur["i"] += 1
        _cur["n"] += 1
        _st["lines_played"] += 1
        _dirty[0] = True
        if _cur["i"] >= _count():
            _next_play()
            _pass_turn()                         # the partner may own the new play's first line
            return


def _next_play():
    _st["plays_done"] += 1
    _note("play", "end of %s" % _plays[_cur["p"]][1])
    _cur["p"] = (_cur["p"] + 1) % len(_plays)
    _cur["i"] = 0
    _play.clear()
    _dirty[0] = True


def _short(title):
    return title if len(title) <= 28 else title[:27] + "…"


# ── storage ──────────────────────────────────────────────────────────

def _state_path():
    d = _api[0].storage()
    return d + "/state.json" if d else None


def _load_state():
    p = _state_path()
    if p:
        try:
            with open(p) as f:
                d = json.loads(f.read())
            c = d.get("cur") or [0, 0, 0]
            _cur.update({"p": int(c[0]), "i": int(c[1]), "n": int(c[2])})
            _st["seq"] = int(d.get("seq", 0))
        except Exception:
            pass
    try:
        _salt[0] = int((_api[0].setting("SALT", None) or "0"), 16) % MOD
    except Exception:
        pass


def _save_state():
    if not _dirty[0]:
        return
    _dirty[0] = False
    p = _state_path()
    if not p:
        return
    try:
        with open(p, "w") as f:
            f.write(json.dumps({"cur": [_cur["p"], _cur["i"], _cur["n"]], "seq": _st["seq"]}))
    except Exception as e:
        _api[0].log("state not saved:", e)


def _flush():
    """The transcript, appended to log-YYYYMMDD.txt on the card."""
    if not _log:
        return
    lines, _log[:] = _log[:], []
    d = _api[0].storage()
    if not d or str(_api[0].setting("LOG", "on")).lower() == "off":
        return
    import os
    path = "%s/log-%s.txt" % (d, _day())
    try:
        try:
            if os.stat(path)[6] > LOG_DAY_MAX:
                return                           # today's file is full
        except OSError:
            _prune(d)
        with open(path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        _api[0].log("transcript not written:", e)


def _prune(d):
    import os
    try:
        logs = sorted(n for n in os.listdir(d) if n.startswith("log-"))
        for n in logs[:-KEEP_DAYS]:
            os.remove(d + "/" + n)
    except Exception:
        pass


# ── what people see ──────────────────────────────────────────────────

def _stats():
    up = time.ticks_diff(_now(), _st["started"]) // 1000
    p = _plays[_cur["p"]] if _cur["p"] < len(_plays) else ("", "?", 0)
    return {
        "version": VERSION, "mode": _st["mode"], "paused": _st["paused"], "pace": _pace_name(),
        "room": _room(), "uptime_s": up, "clock_synced": _synced(), "now": _stamp(),
        "tried": _st["tried"], "posted": _st["posted"], "dropped_by_budget": _st["dropped"],
        "duet_retries": _st["retries"], "numbers_posted": _st["seq"],
        "lines_played": _st["lines_played"], "plays_finished": _st["plays_done"],
        "max_gap_s": _st["max_gap_ms"] // 1000, "links": _st["links"],
        "link_drops": _st["link_drops"], "rtt_ms": _st["rtt_ms"], "errors": _st["errors"], "log_lines_lost": _st["log_lost"],
        "bots_heard": len(_heard), "me": _my_hex(),
        "partner": ({"hex": _peer.get("hex"), "nick": _peer.get("nick"), "up": bool(_peer.get("up"))}
                    if _peer else None),
        "play": {"title": p[1], "line": _cur["i"], "of": p[2], "total_played": _cur["n"],
                 "plays": len(_plays), "builtin": not p[0]},
    }


def _cmd(ctx, arg):
    s = _stats()
    if s["mode"] == "duet":
        return ctx.t("status_duet", nick=s["partner"]["nick"], play=s["play"]["title"],
                     line=s["play"]["line"], of=s["play"]["of"], ok=s["posted"],
                     drop=s["dropped_by_budget"])
    return ctx.t("status_solo", seq=s["numbers_posted"], ok=s["posted"],
                 drop=s["dropped_by_budget"], heard=s["bots_heard"])


def _page(req):
    s = _api[0]
    e = s.esc
    t = lambda k, **kw: s.t(k, req.lang, **kw)
    st = _stats()
    rows = [
        (t("p_mode"), t("mode_" + st["mode"]) + (" — " + t("paused") if st["paused"] else "")),
        (t("p_pace"), st["pace"]), (t("p_room"), "#" + st["room"]),
        (t("p_up"), "%dh %02dm" % (st["uptime_s"] // 3600, st["uptime_s"] // 60 % 60)),
        (t("p_clock"), st["now"] + ("" if st["clock_synced"] else " (" + t("unsynced") + ")")),
        (t("p_posted"), "%d / %d" % (st["posted"], st["tried"])),
        (t("p_dropped"), str(st["dropped_by_budget"])),
        (t("p_gap"), "%d s" % st["max_gap_s"]),
        (t("p_heard"), str(st["bots_heard"])),
        (t("p_partner"), (st["partner"] or {}).get("nick") or "—"),
        (t("p_links"), "%d / %d" % (st["links"], st["link_drops"])),
        (t("p_rtt"), "%s ms" % st["rtt_ms"] if st["rtt_ms"] is not None else "—"),
        (t("p_play"), "%s — %d / %d (%d)" % (st["play"]["title"], st["play"]["line"],
                                             st["play"]["of"], st["play"]["total_played"])),
    ]
    out = ["<h1>Bavard</h1><p class='sub'>" + e(t("page_intro")) + "</p>",
           "<div class='panel'><ul class='files'>"]
    out += ["<li>%s <b>%s</b></li>" % (e(k), e(v)) for k, v in rows]
    out.append("</ul></div><h2>" + e(t("p_last")) + "</h2><div class='panel'><ul class='files'>")
    out += ["<li><small>%s</small></li>" % e(l) for l in reversed(_last)]
    out.append("</ul></div><p class='sub'><a href='/p/bavard/stats.json'>stats.json</a></p>")
    return "".join(out)


def _stats_json(req):
    return 200, json.dumps(_stats()), "application/json"


def _panel(lang, hidden):
    s = _api[0]
    t = lambda k: s.esc(s.t(k, lang))
    btn = lambda a, k: ("<form method='post' action='/admin/panel' style='display:inline'>%s"
                        "<input type='hidden' name='do' value='%s'><button>%s</button></form> "
                        % (hidden, a, t(k)))
    return ("<p>" + t("panel_intro") + "</p><p>" +
            (btn("resume", "b_resume") if _st["paused"] else btn("pause", "b_pause")) +
            btn("solo", "b_solo") + btn("rewind", "b_rewind") + "</p>")


def _panel_post(fields, lang):
    s = _api[0]
    do = (fields.get("do") or "").strip()
    if do == "pause":
        if _st["mode"] == "duet":
            _send({"k": "bye"})                 # the partner holds the place: another bot takes over
        _solo(hold=False)
        _st["paused"] = True
    elif do == "resume":
        _st["paused"] = False
        _st["next_ms"] = _now()
        _announce()
    elif do == "solo":
        _send({"k": "bye"})
        _solo()
    elif do == "rewind":
        _cur.update({"p": 0, "i": 0, "n": 0})
        _play.clear()
        _dirty[0] = True
    else:
        return s.t("panel_what", lang)
    _note("admin", do)
    return s.t("panel_done", lang, what=do)
