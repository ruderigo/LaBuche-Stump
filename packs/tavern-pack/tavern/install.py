# Tavern -- dice, fortunes, the trout, quotes, !seen, quick polls and a
# flood guard. The reference plugin for the Stump plugin API
# (docs/PLUGINS.md): every hook it uses is there for a reason, and
# everything it says is text, so mesh users get all of it too.

import time

try:
    import urandom as random
except ImportError:
    import random

try:
    import ujson as json
except ImportError:
    import json

VERSION = "1.0"
STUMP_API = 1

MAX_QUOTES = 300
MAX_QUOTE_LEN = 300
MAX_SEEN = 200
MAX_POLL_OPTS = 6
MAX_HISTORY = 10

_api = []
_state = {"seen": {}, "quotes": [], "polls": {}, "history": [], "next_poll": 1}
_dirty = [False]
_file = [None]
_flood = {}       # client_id -> [window_start, count, blocked_until]


def setup(stump):
    _api.append(stump)
    stump.nick = stump.setting("NICK", "Tavernier")
    _file[0] = stump.storage_file("tavern.json")
    _load()
    h = lambda key: (lambda lang: stump.t(key, lang))
    stump.command("roll", roll, help=h("help_roll"))
    stump.command("8ball", eightball, help=h("help_8ball"))
    stump.command("slap", slap, help=h("help_slap"))
    stump.command("seen", seen, help=h("help_seen"))
    stump.command("quote", quote, help=h("help_quote"))
    stump.command("vote", vote, help=h("help_vote"))
    stump.on_message(_remember)
    stump.gate(_flood_guard, priority=30)
    stump.every(30, _tick)
    stump.page("", _page, nav=lambda lang: stump.t("page_title", lang),
               sub=lambda lang: stump.t("card_sub", lang))
    stump.page("votes.json", _votes_json, raw=True)


def _t(ctx, key, **kw):
    return ctx.t(key, **kw)


def _now():
    return int(time.time())


def _ago(ctx, ts):
    d = _now() - int(ts or 0)
    if d < 0 or not ts:
        return _t(ctx, "ago_while")
    if d < 90:
        return _t(ctx, "ago_now")
    if d < 5400:
        return _t(ctx, "ago_min", n=d // 60)
    if d < 129600:
        return _t(ctx, "ago_h", n=d // 3600)
    return _t(ctx, "ago_d", n=d // 86400)


# ── storage ───────────────────────────────────────────────────────────

def _load():
    if not _file[0]:
        return
    try:
        with open(_file[0]) as f:
            data = json.loads(f.read())
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for k in ("seen", "polls"):
        if isinstance(data.get(k), dict):
            _state[k] = data[k]
    for k in ("quotes", "history"):
        if isinstance(data.get(k), list):
            _state[k] = data[k]
    if isinstance(data.get("next_poll"), int):
        _state["next_poll"] = data["next_poll"]


def _save():
    _dirty[0] = False
    if not _file[0] or _api[0].storage() is None:
        return
    try:
        with open(_file[0], "w") as f:
            f.write(json.dumps(_state))
    except Exception as e:
        _api[0].log("save failed:", e)


# ── listener and gate ─────────────────────────────────────────────────

def _remember(ctx):
    seen = _state["seen"]
    seen[ctx.nick.lower()] = [ctx.nick, ctx.room, _now()]
    if len(seen) > MAX_SEEN:
        oldest = min(seen, key=lambda k: seen[k][2])
        del seen[oldest]
    _dirty[0] = True


def _flood_guard(ctx):
    """More than FLOOD_LINES lines in 10 s: told to slow down, and lines
    are dropped for 20 s. Commands with "/" go to the core, which has its
    own limits; this only counts room lines and !commands."""
    if ctx.text.startswith("/"):
        return None
    limit = int(_api[0].setting("FLOOD_LINES", 6) or 0)
    if limit <= 0:
        return None
    now = time.ticks_ms()
    st = _flood.get(ctx.client_id)
    if st is None:
        if len(_flood) > 200:
            _flood.clear()
        st = _flood[ctx.client_id] = [now, 0, None]
    if st[2] is not None:
        if time.ticks_diff(st[2], now) > 0:
            return ["⧗ — " + _t(ctx, "flood")]
        st[2] = None
        st[0], st[1] = now, 0
    if time.ticks_diff(now, st[0]) > 10000:
        st[0], st[1] = now, 0
    st[1] += 1
    if st[1] > limit:
        st[2] = time.ticks_add(now, 20000)
        return ["⧗ — " + _t(ctx, "flood")]
    return None


# ── fun ───────────────────────────────────────────────────────────────

def roll(ctx, arg):
    spec = (arg or "1d6").strip().lower()
    n, d, sides = spec.partition("d")
    try:
        n = int(n or 1)
        sides = int(sides or 6) if d else int(spec)
        if not d:
            n = 1
    except ValueError:
        return _t(ctx, "roll_usage")
    if not (1 <= n <= 10 and 2 <= sides <= 1000):
        return _t(ctx, "roll_usage")
    rolls = [random.randint(1, sides) for _ in range(n)]
    detail = "" if n == 1 else " (" + " + ".join(str(r) for r in rolls) + ")"
    ctx.post(_t(ctx, "roll_result", who=ctx.nick, dice="%dd%d" % (n, sides),
                total=sum(rolls), detail=detail))


def eightball(ctx, arg):
    answers = _t(ctx, "8ball_answers").split("|")
    a = answers[random.randint(0, len(answers) - 1)]
    if arg:
        ctx.post("🎱 %s: « %s » — %s" % (ctx.nick, arg[:120], a))
    else:
        ctx.post("🎱 %s — %s" % (ctx.nick, a))


def slap(ctx, arg):
    target = (arg or "").strip().split(" ")[0][:16]
    if not target:
        return _t(ctx, "slap_usage")
    ctx.post(_t(ctx, "slap", who=ctx.nick, target=target))


def seen(ctx, arg):
    name = (arg or "").strip().split(" ")[0].lstrip("~")
    if not name:
        return _t(ctx, "seen_usage")
    if name.lower() == ctx.nick.lstrip("~").lower():
        return _t(ctx, "seen_self")
    entry = _state["seen"].get(name.lower()) or _state["seen"].get("~" + name.lower())
    if not entry:
        return _t(ctx, "seen_never", nick=name)
    return _t(ctx, "seen_at", nick=entry[0], room=entry[1], ago=_ago(ctx, entry[2]))


# ── quotes ────────────────────────────────────────────────────────────

def quote(ctx, arg):
    arg = (arg or "").strip()
    quotes = _state["quotes"]
    word, _, rest = arg.partition(" ")
    if word.lower() == "add":
        text = rest.strip()
        if not text:
            return _t(ctx, "quote_usage")
        if len(quotes) >= MAX_QUOTES:
            return _t(ctx, "quote_full", n=MAX_QUOTES)
        quotes.append([text[:MAX_QUOTE_LEN], ctx.nick, _now()])
        _dirty[0] = True
        return _t(ctx, "quote_saved", n=len(quotes))
    if word.lower() == "find":
        needle = rest.strip().lower()
        if not needle:
            return _t(ctx, "quote_usage")
        hits = [i for i, q in enumerate(quotes) if needle in q[0].lower()]
        if not hits:
            return _t(ctx, "quote_none_found", word=needle)
        i = hits[-1]
        return _t(ctx, "quote_line", n=i + 1, text=quotes[i][0], by=quotes[i][1]) + \
            ("" if len(hits) == 1 else "  " + _t(ctx, "quote_more", n=len(hits) - 1))
    if not quotes:
        return _t(ctx, "quote_empty")
    if word:
        try:
            i = int(word.lstrip("#")) - 1
        except ValueError:
            return _t(ctx, "quote_usage")
        if not 0 <= i < len(quotes):
            return _t(ctx, "quote_no_such", n=len(quotes))
    else:
        i = random.randint(0, len(quotes) - 1)
    ctx.post(_t(ctx, "quote_line", n=i + 1, text=quotes[i][0], by=quotes[i][1]))


# ── polls ─────────────────────────────────────────────────────────────

def _poll_line(ctx, p, final=False):
    counts = [0] * len(p["opts"])
    for v in p["votes"].values():
        if 0 <= v < len(counts):
            counts[v] += 1
    opts = " · ".join("%d) %s %d" % (i + 1, o, counts[i]) for i, o in enumerate(p["opts"]))
    if final:
        return _t(ctx, "vote_final", q=p["q"], opts=opts, n=len(p["votes"]))
    left = max(0, p["ends"] - _now()) // 60
    return _t(ctx, "vote_status", q=p["q"], opts=opts, mins=left)


def _close(room, p, ctx=None):
    _state["polls"].pop(room, None)
    p["closed"] = _now()
    p["room"] = room
    _state["history"].append(p)
    del _state["history"][:-MAX_HISTORY]
    _dirty[0] = True
    line = _poll_line(ctx or _Quiet(), p, final=True)
    import rrc
    rrc.post(room, _api[0].nick, line)
    return line


class _Quiet:
    """Enough of a ctx for text when a timer closes a poll."""
    lang = None
    def t(self, key, **kw):
        return _api[0].t(key, None, **kw)


def vote(ctx, arg):
    arg = (arg or "").strip()
    polls = _state["polls"]
    p = polls.get(ctx.room)
    if "|" in arg:
        if p:
            return _t(ctx, "vote_already", q=p["q"])
        bits = [b.strip() for b in arg.split("|")]
        q, opts = bits[0][:120], [o[:40] for o in bits[1:] if o][:MAX_POLL_OPTS]
        if not q or len(opts) < 2:
            return _t(ctx, "vote_usage")
        mins = int(_api[0].setting("VOTE_MINUTES", 30) or 30)
        p = {"id": _state["next_poll"], "q": q, "opts": opts, "votes": {}, "by": ctx.client_id,
             "nick": ctx.nick, "ends": _now() + mins * 60}
        _state["next_poll"] += 1
        polls[ctx.room] = p
        _dirty[0] = True
        ctx.post(_t(ctx, "vote_open", who=ctx.nick, q=q, mins=mins,
                    opts=" · ".join("%d) %s" % (i + 1, o) for i, o in enumerate(opts))))
        return None
    if not p:
        return _t(ctx, "vote_none")
    if not arg:
        return _poll_line(ctx, p)
    if arg.lower() in ("close", "fin", "cerrar"):
        if p["by"] != ctx.client_id:
            return _t(ctx, "vote_not_yours", who=p["nick"])
        _close(ctx.room, p, ctx)
        return None
    choice = None
    try:
        choice = int(arg) - 1
    except ValueError:
        low = arg.lower()
        for i, o in enumerate(p["opts"]):
            if o.lower() == low:
                choice = i
    if choice is None or not 0 <= choice < len(p["opts"]):
        return _t(ctx, "vote_bad_choice", n=len(p["opts"]))
    changed = ctx.client_id in p["votes"]
    p["votes"][ctx.client_id] = choice
    _dirty[0] = True
    return _t(ctx, "vote_changed" if changed else "vote_counted", opt=p["opts"][choice])


def _tick():
    now = _now()
    for room, p in list(_state["polls"].items()):
        if now >= p["ends"]:
            _close(room, p)
    if _dirty[0]:
        _save()


# ── pages ─────────────────────────────────────────────────────────────

def _page(req):
    s = _api[0]
    e = s.esc
    t = lambda k, **kw: s.t(k, req.lang, **kw)
    out = ["<h1>" + e(t("page_title")) + "</h1><p class='sub'>" + e(t("page_intro")) + "</p>"]
    out.append("<h2>" + e(t("page_polls")) + "</h2>")
    polls = list(_state["polls"].items())
    if not polls:
        out.append("<p class='sub'>" + e(t("page_no_polls")) + "</p>")
    for room, p in polls:
        out.append(_poll_html(req, room, p, False))
    if _state["history"]:
        out.append("<h2>" + e(t("page_results")) + "</h2>")
        for p in reversed(_state["history"][-5:]):
            out.append(_poll_html(req, p.get("room", ""), p, True))
    out.append("<h2>" + e(t("page_quotes")) + "</h2>")
    quotes = _state["quotes"]
    if not quotes:
        out.append("<p class='sub'>" + e(t("quote_empty")) + "</p>")
    else:
        out.append("<div class='panel'><ul class='files'>" + "".join(
            "<li><b>#%d</b> %s <small>— %s</small></li>" % (i + 1, e(q[0]), e(q[1]))
            for i, q in reversed(list(enumerate(quotes))[-30:])) + "</ul></div>")
    return "".join(out)


def _poll_html(req, room, p, final):
    e = _api[0].esc
    counts = [0] * len(p["opts"])
    for v in p["votes"].values():
        if 0 <= v < len(counts):
            counts[v] += 1
    total = max(1, sum(counts))
    rows = "".join(
        "<li>%s <b>%d</b><div style='height:6px;background:var(--ember);width:%d%%;border-radius:3px'></div></li>"
        % (e(o), counts[i], counts[i] * 100 // total) for i, o in enumerate(p["opts"]))
    head = "<b>%s</b> <small>#%s · %s</small>" % (e(p["q"]), e(room), e(p.get("nick", "")))
    return "<div class='panel'>" + head + "<ul class='files'>" + rows + "</ul></div>"


def _votes_json(req):
    def pub(room, p):
        counts = [0] * len(p["opts"])
        for v in p["votes"].values():
            if 0 <= v < len(counts):
                counts[v] += 1
        return {"id": p["id"], "room": room, "question": p["q"], "options": p["opts"],
                "counts": counts, "by": p.get("nick"), "ends": p.get("ends"), "closed": p.get("closed")}
    data = {"open": [pub(r, p) for r, p in _state["polls"].items()],
            "closed": [pub(p.get("room", ""), p) for p in _state["history"]]}
    return 200, json.dumps(data), "application/json"
