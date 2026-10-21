# rnfs -- speaks the link format of rnfs, the Reticulum file system
# (https://github.com/afit21/rnfs, MIT): files fetched by content ID.
# rnfs itself is a CPython daemon and can't run on the ESP32; this plugin
# hands it out (the pack's tools/ go on the Tools page), spots rnfs links
# in chat, and keeps the place's own shelf of them.

import time

try:
    import ujson as json
except ImportError:
    import json

VERSION = "1.1"
STUMP_API = 1

MAX_SHELF = 200
MAX_SPOTTED = 50
MAX_TITLE = 80
HINT_EVERY_S = 600          # at most one hint per room every 10 minutes
B32 = "abcdefghijklmnopqrstuvwxyz234567"

_api = []
_state = {"shelf": [], "spotted": []}
_file = [None]
_dirty = [False]
_last_hint = {}             # room -> ticks_ms


def setup(stump):
    _api.append(stump)
    _file[0] = stump.storage_file("shelf.json")
    _load()
    stump.command("shelf", shelf, help=lambda lang: stump.t("help_shelf", lang))
    stump.command("rnfs", about, help=lambda lang: stump.t("help_rnfs", lang))
    stump.on_message(_spot, priority=60)
    stump.every(60, _tick)
    stump.page("", _page, nav=lambda lang: stump.t("card", lang), sub=lambda lang: stump.t("card_sub", lang))
    stump.page("shelf.json", _shelf_json, raw=True)


def is_cid(s):
    """An rnfs CID: lowercase base32 starting with "b" (bafk..., baga...)."""
    return 50 <= len(s) <= 100 and s[0] == "b" and all(c in B32 for c in s)


def find_links(text):
    """rnfs links in a line: "rnfs:<cid>[/path]", "rnfs:name/<32 hex>/...",
    or a bare CID. Returns [(target, cid_or_None)]."""
    out = []
    for word in text.replace("<", " ").replace(">", " ").replace("(", " ").replace(")", " ").split():
        w = word.strip(".,;:!?\"'")
        if w.startswith("rnfs://"):
            w = "rnfs:" + w[7:]
        if w.startswith("rnfs:"):
            target = w[5:]
            head = target.split("/", 1)[0]
            if is_cid(head):
                out.append((target, head))
            elif head == "name" and len(target.split("/")) >= 2:
                n = target.split("/")[1]
                if len(n) == 32 and all(c in "0123456789abcdef" for c in n):
                    out.append((target, None))
        elif is_cid(w.split("/", 1)[0]):
            out.append((w, w.split("/", 1)[0]))
    return out


def _load():
    if not _file[0]:
        return
    try:
        with open(_file[0]) as f:
            data = json.loads(f.read())
        for k in ("shelf", "spotted"):
            if isinstance(data.get(k), list):
                _state[k] = data[k]
    except Exception:
        pass


def _save():
    _dirty[0] = False
    if not _file[0] or _api[0].storage() is None:
        return
    try:
        with open(_file[0], "w") as f:
            f.write(json.dumps(_state))
    except Exception as e:
        _api[0].log("save failed:", e)


def _tick():
    if _dirty[0]:
        _save()


def _cmd(target):
    """The command for a link: "rnfs get <cid>[/path]"; a name link is
    shown as the link itself."""
    return ("rnfs get " + target) if is_cid(target.split("/", 1)[0]) else ("rnfs:" + target)


def _short(cid):
    return cid[:10] + "…" + cid[-4:] if len(cid) > 16 else cid


# ── the link spotter ──────────────────────────────────────────────────

def _spot(ctx):
    links = find_links(ctx.text)
    if not links:
        return
    known = set(e[0] for e in _state["spotted"])
    for target, cid in links:
        if target not in known:
            _state["spotted"].append([target, ctx.nick, ctx.room, int(time.time())])
            known.add(target)
    del _state["spotted"][:-MAX_SPOTTED]
    _dirty[0] = True
    if str(_api[0].setting("HINTS", True)).lower() in ("false", "0", "no", "off"):
        return
    now = time.ticks_ms()
    last = _last_hint.get(ctx.room)
    if last is not None and time.ticks_diff(now, last) < HINT_EVERY_S * 1000:
        return
    cids = [t for t, cid in links if cid]
    if not cids:
        return       # a name link: collected, but no command to suggest for it
    _last_hint[ctx.room] = now
    ctx.post(ctx.t("hint", cmd="rnfs get " + cids[0]))


# ── !shelf ────────────────────────────────────────────────────────────

def shelf(ctx, arg):
    """!shelf, !shelf add <cid> <title>, !shelf rm <n>, !shelf find <word>, !shelf <n>"""
    arg = (arg or "").strip()
    items = _state["shelf"]
    word, _, rest = arg.partition(" ")
    w = word.lower()
    if w == "add":
        target, _, title = rest.strip().partition(" ")
        if target.startswith("rnfs:"):
            target = target[5:]
        head = target.split("/", 1)[0]
        if not is_cid(head):
            return ctx.t("shelf_bad_cid")
        title = (title.strip() or _short(head))[:MAX_TITLE]
        for i, e in enumerate(items):
            if e[0] == target:
                return ctx.t("shelf_already", n=i + 1, title=e[1])
        if len(items) >= MAX_SHELF:
            return ctx.t("shelf_full", n=MAX_SHELF)
        items.append([target, title, ctx.nick, ctx.client_id, int(time.time())])
        _dirty[0] = True
        ctx.post(ctx.t("shelf_added", who=ctx.nick, n=len(items), title=title))
        return None
    if w in ("rm", "del"):
        try:
            i = int(rest.strip().lstrip("#")) - 1
        except ValueError:
            return ctx.t("shelf_usage")
        if not 0 <= i < len(items):
            return ctx.t("shelf_no_such", n=len(items))
        if items[i][3] != ctx.client_id:
            return ctx.t("shelf_not_yours", who=items[i][2])
        e = items.pop(i)
        _dirty[0] = True
        return ctx.t("shelf_removed", title=e[1])
    if w == "find":
        needle = rest.strip().lower()
        hits = [(i, e) for i, e in enumerate(items) if needle and needle in e[1].lower()]
        if not hits:
            return ctx.t("shelf_none_found", word=needle)
        return [ctx.t("shelf_line", n=i + 1, title=e[1], cmd="rnfs get " + e[0]) for i, e in hits[-3:]]
    if w:
        try:
            i = int(w.lstrip("#")) - 1
        except ValueError:
            return ctx.t("shelf_usage")
        if not 0 <= i < len(items):
            return ctx.t("shelf_no_such", n=len(items))
        return ctx.t("shelf_line", n=i + 1, title=items[i][1], cmd="rnfs get " + items[i][0])
    if not items:
        return ctx.t("shelf_empty")
    head = ctx.t("shelf_count", n=len(items))
    if ctx.is_mesh:
        return head
    return [head] + [ctx.t("shelf_line", n=i + 1, title=e[1], cmd="rnfs get " + e[0])
                     for i, e in list(enumerate(items))[-5:]]


def about(ctx, arg):
    return ctx.t("about")


# ── pages ─────────────────────────────────────────────────────────────

def _page(req):
    s = _api[0]
    e, t = s.esc, (lambda k, **kw: s.t(k, req.lang, **kw))
    out = ["<h1>" + e(t("page_title")) + "</h1><p class='sub'>" + e(t("page_intro")) + "</p>",
           "<p class='sub'><a href='/tools'>" + e(t("page_get")) + "</a></p>",
           "<h2>" + e(t("page_shelf")) + "</h2>"]
    items = _state["shelf"]
    if not items:
        out.append("<p class='sub'>" + e(t("shelf_empty")) + "</p>")
    else:
        out.append("<div class='panel'><ul class='files'>" + "".join(
            "<li><div style='flex:1;min-width:0'><b>#%d %s</b> <small>— %s</small><br>"
            "<code style='overflow-wrap:anywhere'>rnfs get %s</code></div></li>"
            % (i + 1, e(it[1]), e(it[2]), e(it[0])) for i, it in reversed(list(enumerate(items)))) + "</ul></div>")
    if _state["spotted"]:
        out.append("<h2>" + e(t("page_spotted")) + "</h2><div class='panel'><ul class='files'>" + "".join(
            "<li><div style='flex:1;min-width:0'><code style='overflow-wrap:anywhere'>%s</code><br>"
            "<small>— %s, #%s</small></div></li>" % (e(_cmd(sp[0])), e(sp[1]), e(sp[2]))
            for sp in reversed(_state["spotted"][-20:])) + "</ul></div>")
    # Credit where it's due: rnfs and the Reticulum stack it ships with.
    credit = e(t("page_credit", rnfs="\x00R", rns="\x00N"))
    credit = (credit.replace("\x00R", "<a href='https://github.com/afit21/rnfs'>github.com/afit21/rnfs</a>")
              .replace("\x00N", "<a href='https://reticulum.network'>reticulum.network</a>"))
    out.append("<p class='sub'><small>" + credit + "</small></p>")
    return "".join(out)


def _shelf_json(req):
    return 200, json.dumps({
        "shelf": [{"n": i + 1, "target": it[0], "title": it[1], "by": it[2], "added": it[4]}
                  for i, it in enumerate(_state["shelf"])],
        "spotted": [{"target": sp[0], "by": sp[1], "room": sp[2], "at": sp[3]} for sp in _state["spotted"]],
    }), "application/json"
