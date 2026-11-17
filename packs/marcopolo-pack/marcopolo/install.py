# Marco Polo -- a tester's call and answer between Stumps. One Stump
# says "marco" in a shared room, every other Stump with the pack answers
# "polo", and the caller times each answer: how long a room line takes
# to cross and come back, and how many never do. The same pack on any
# firmware since 1.0.0-beta.53, so the same run is timed before and after
# an update (packed text, 1.0.0-beta.82: fewer pieces for a long line).
#
#   /marco                         where the run is, and the times so far
#   /marco start [rounds] [size] [gap]
#                                  e.g. /marco start 10 3 30: ten calls, the
#                                  long line, 30 s apart (defaults: 10 0 30)
#   /marco stop
#
# The lines are real room lines, sent the way a person's are: that's the
# path being measured. So unlike other bots' lines they do go to the
# other Stumps (and to the apps in the room), written with rrc.post();
# the pack paces itself instead of BarKeep's budget: at least 15 s
# between calls, 30 calls a run, one run at a time, 20 answers a minute.
# Nothing is on the air until someone writes /marco start. Nothing is
# stored. Plugin API 2.
#
#   <Marco> marco 3 a1b2 /3                      the call: number, run, end mark
#   <Marco@Other> polo 3 a1b2                    the answer
#   <Marco> marco 3 a1b2 The ferry was ... /3    sizes 1 to 3: the same, longer
#
# A long call may arrive in pieces (the radio cuts a line at 200 bytes):
# the answer goes when the piece ending in "/3" is in, so the time is
# the whole line's.

import time

try:
    import urandom as random
except ImportError:
    import random

VERSION = "1.0"
STUMP_API = 2

ROUNDS_DEFAULT, ROUNDS_MAX = 10, 30
GAP_DEFAULT, GAP_MIN, GAP_MAX = 30, 15, 600     # seconds between two calls
LOST_S = 120           # a call not answered after this long is lost
POLOS_A_MINUTE = 20    # answers at most, whoever calls
SEEN_MAX = 64          # calls remembered as answered
STUMPS_MAX = 16        # Stumps whose answers (or calls) are counted

# What a call carries after its number, by size: ordinary sentences, the
# same every time. 0: nothing. The whole line is then 15, 125, 247 or
# 363 bytes (two more from the tenth call on; a room line holds 400
# characters).
PAD = (
    "The ferry was late again this morning, so we walked up to the lookout and waited there until the fog cleared.",
    "Le vieux poêle chauffe encore très bien, mais il faudra fendre du bois avant la fin de la semaine si le froid continue.",
    "Mi hermana dice que el puente sigue cerrado y que el autobús sale a las siete en punto desde la plaza del mercado.",
)

_api = []
_st = {"last_id": {}, "run": None, "heard": {}, "seen": [], "polos": [], "pending": {}, "skipped": 0}


def setup(stump):
    _api.append(stump)
    stump.nick = str(stump.setting("NICK", "Marco") or "Marco")
    stump.command("marco", _cmd, help=lambda lang: stump.t("help", lang), mesh_lines=3, trigger=False)
    stump.every(1, _tick)
    _skip_history()


def _room():
    return str(_api[0].setting("ROOM", "lora-mesh") or "lora-mesh").lstrip("#")


def _now():
    return time.ticks_ms()


def _secs(t0):
    return time.ticks_diff(_now(), t0) // 1000


def _skip_history():
    """What is in the room already isn't a call to answer."""
    try:
        import rrc
        room = _room()
        msgs = rrc.since(room, 0)
        _st["last_id"][room] = msgs[-1]["id"] if msgs else 0
    except Exception:
        pass


def _say(text):
    """A room line as a person's: it goes to the other Stumps. None if
    the room isn't there."""
    import rrc
    return rrc.post(_room(), _api[0].nick, text)


def _line(seq, tag, size):
    pad = " ".join(PAD[:size])
    return "marco %d %s%s /%d" % (seq, tag, (" " + pad) if pad else "", seq)


# ---- the caller ----

def _start(rounds, size, gap):
    tag = "%04x" % random.getrandbits(16)
    _st["run"] = {"tag": tag, "rounds": rounds, "size": size, "gap": gap, "sent": 0,
                  "at": {}, "back": {}, "next": _now(), "error": None, "stopped": False,
                  "bytes": len(_line(1, tag, size).encode())}


def _call(run):
    seq = run["sent"] + 1
    if _say(_line(seq, run["tag"], run["size"])) is None:
        run["error"] = "room"
        run["stopped"] = True
        return
    run["sent"] = seq
    run["at"][seq] = _now()
    run["next"] = time.ticks_add(_now(), run["gap"] * 1000)


def _polo_in(run, nick, seq, tag):
    if tag != run["tag"] or seq not in run["at"]:
        return
    stump = nick.split("@", 1)[1]
    back = run["back"].get(stump)
    if back is None:
        if len(run["back"]) >= STUMPS_MAX:
            return
        back = run["back"][stump] = {}
    if seq not in back:
        back[seq] = time.ticks_diff(_now(), run["at"][seq])


def _running(run):
    return run is not None and not run["stopped"] and run["sent"] < run["rounds"]


# ---- the one who answers ----

def _marco_in(nick, body):
    """A call from another Stump, whole or its first piece; the answer
    goes when its end mark is in."""
    parts = body.split(" ")
    if len(parts) < 3 or not parts[1].isdigit() or len(parts[1]) > 3 or len(parts[2]) != 4:
        return
    seq, tag = int(parts[1]), parts[2]
    try:
        int(tag, 16)
    except ValueError:
        return
    if nick not in _st["pending"]:
        while len(_st["pending"]) >= STUMPS_MAX:
            _st["pending"].pop(next(iter(_st["pending"])))
    _st["pending"][nick] = (seq, tag)
    _maybe_polo(nick, body)


def _maybe_polo(nick, body):
    p = _st["pending"].get(nick)
    if p is None or not body.endswith(" /%d" % p[0]):
        return
    seq, tag = _st["pending"].pop(nick)
    key = (nick, tag, seq)
    if key in _st["seen"]:
        return
    _st["seen"].append(key)
    del _st["seen"][:-SEEN_MAX]
    now = _now()
    _st["polos"] = [t for t in _st["polos"] if time.ticks_diff(now, t) < 60000]
    if len(_st["polos"]) >= POLOS_A_MINUTE:
        _st["skipped"] += 1
        return
    _st["polos"].append(now)
    stump = nick.split("@", 1)[1]
    if stump in _st["heard"] or len(_st["heard"]) < STUMPS_MAX:
        _st["heard"][stump] = _st["heard"].get(stump, 0) + 1
    _say("polo %d %s" % (seq, tag))


# ---- every second ----

def _tick():
    try:
        _listen()
    except Exception as e:
        _api[0].log("listen:", e)
    run = _st["run"]
    if _running(run) and time.ticks_diff(_now(), run["next"]) >= 0:
        try:
            _call(run)
        except Exception as e:
            run["error"] = "post"
            run["stopped"] = True
            _api[0].log("call:", e)


def _listen():
    import rrc
    room = _room()
    last = _st["last_id"].get(room)
    if last is None:
        _skip_history()
        return
    for m in rrc.since(room, last):
        _st["last_id"][room] = m["id"]
        nick, body = m.get("nick") or "", m.get("body") or ""
        # Only what another Stump relayed here: a person or a bot there.
        if m.get("kind") == "system" or not m.get("via") or "@" not in nick:
            continue
        if body.startswith("marco "):
            _marco_in(nick, body)
        elif body.startswith("polo "):
            parts = body.split(" ")
            if _st["run"] is not None and len(parts) == 3 and parts[1].isdigit() and len(parts[1]) < 4:
                _polo_in(_st["run"], nick, int(parts[1]), parts[2])
        elif nick in _st["pending"]:
            _maybe_polo(nick, body)             # a later piece of a long call


# ---- /marco ----

def _due(run):
    """How many calls are answered or past waiting for."""
    n = 0
    for seq, t in run["at"].items():
        if _secs(t) >= LOST_S:
            n += 1
    return n


def _fmt(ms):
    s10 = (ms + 50) // 100
    return "%d.%d" % (s10 // 10, s10 % 10)


def _status(ctx):
    run = _st["run"]
    heard = sum(_st["heard"].values())
    if run is None:
        return [ctx.t("idle", room=_room(), heard=heard)]
    if run["error"]:
        state = ctx.t("err_" + run["error"], room=_room())
    elif _running(run):
        state = ctx.t("running")
    elif run["stopped"]:
        state = ctx.t("stopped")
    else:
        state = ctx.t("done")
    out = [ctx.t("run", room=_room(), tag=run["tag"], sent=run["sent"], rounds=run["rounds"], gap=run["gap"],
                 size=run["size"], bytes=run["bytes"], state=state, heard=heard)]
    if not run["back"]:
        out.append(ctx.t("none_yet" if _due(run) < run["sent"] or not run["sent"] else "none"))
        return out
    due = _due(run)
    rows = sorted(run["back"].items())
    if ctx.is_mesh:
        rows = rows[:2]                          # (the mesh takes three lines)
    for stump, back in rows:
        times = sorted(back.values())
        lost = len([s for s in run["at"] if s not in back and _secs(run["at"][s]) >= LOST_S])
        out.append(ctx.t("row", stump=stump, got=len(back), of=max(due, len(back)), lost=lost,
                         lo=_fmt(times[0]), avg=_fmt(sum(times) // len(times)), hi=_fmt(times[-1])))
    return out


def _cmd(ctx, arg):
    parts = (arg or "").strip().lower().split()
    if not parts:
        return _status(ctx)
    if parts[0] == "stop":
        if _st["run"] is not None:
            _st["run"]["stopped"] = True
        return [ctx.t("stop")]
    if parts[0] != "start" or len(parts) > 4 or not all(p.isdigit() and len(p) < 5 for p in parts[1:]):
        return [ctx.t("usage")]
    if _running(_st["run"]):
        return [ctx.t("busy")]
    nums = [int(p) for p in parts[1:]]
    rounds = nums[0] if len(nums) > 0 else ROUNDS_DEFAULT
    size = nums[1] if len(nums) > 1 else 0
    gap = nums[2] if len(nums) > 2 else GAP_DEFAULT
    if not (1 <= rounds <= ROUNDS_MAX and 0 <= size <= len(PAD) and GAP_MIN <= gap <= GAP_MAX):
        return [ctx.t("limits", rounds=ROUNDS_MAX, sizes=len(PAD), lo=GAP_MIN, hi=GAP_MAX)]
    try:
        import rrc
        if not rrc.room_exists(_room()):
            return [ctx.t("err_room", room=_room())]
    except Exception:
        pass
    _start(rounds, size, gap)
    return [ctx.t("started", rounds=rounds, size=size, gap=gap, room=_room(), bytes=_st["run"]["bytes"])]
