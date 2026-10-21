# Project Stump -- plugins.py
#
# The plugin API (docs/PLUGINS.md). Every place a plugin can join in is
# registered here, and the core calls it from five visible call sites:
#
#   rrc.handle_input      -> before()   gates, then /commands
#                         -> after()    !triggers and listeners, once a
#                                       plain line has been posted
#   rrc.help_lines        -> help_lines()
#   barkeep._handle       -> handle_page()   /p/<plugin>/...
#   example_node          -> load_all(), scheduler_loop(), stable_loop()
#
# A plugin is a top-level folder with an install.py. If install.py has
# setup(stump), it gets an Api bound to its own name and registers hooks
# with it. If it only has activate() (fservbot, stumpid today), that is
# called exactly as the old loader did, so existing plugins run unchanged.
#
# MicroPython has no sandbox: a plugin is trusted code. What this module
# does is keep an honest but buggy one from taking the node down -- every
# callback is guarded, timed and rate-limited, a plugin can be switched
# off from /admin without a laptop, and a plugin that hangs or resets the
# board while starting is switched off automatically at the next boot.

import time

try:
    import ujson as json
except ImportError:
    import json

API_VERSION = 1

# In-house plugins: the only ones loaded in safe mode, and the only ones
# allowed a gate priority below 20 (the access-control band).
CORE_PLUGINS = ("stumpid", "fservbot")
RESERVED_BAND = 20            # gate priorities 0..19
# Can't be switched off from /admin: it IS the node's access control (and
# the admin password check). Remove it with the provisioner instead.
PROTECTED = ("stumpid",)
PROTECTION_BAND = 40          # 20..39: flood guards, filters
DEFAULT_PRIORITY = 50

# Top-level folders that are never plugins, even with an install.py.
NOT_PLUGINS = ("urns", "lib", "peripherals", "web", "docs", "third_party", "tools_payload")

# Commands rrc answers itself, and the ones stumpid owns: no other plugin
# can register these names.
CORE_COMMANDS = ("help", "nick", "join", "j", "part", "rooms", "names", "who", "topic",
                 "msg", "m", "w", "me", "clear", "admin", "auth", "whoami", "invite")
RESERVED_FOR = {"admin": "stumpid", "auth": "stumpid", "whoami": "stumpid", "invite": "stumpid"}

STATE_FILE = "plugins_state.json"    # on flash: works without an SD card
MARKER_FILE = "plugins_loading"      # present while a boot isn't yet known good
STABLE_AFTER_S = 60

# Budgets
CMD_GAP_MS = 1500             # one plugin command per client per 1.5 s
CMD_PER_MIN = 15              # and at most 15 a minute
POSTS_PER_MIN = 6             # room posts per plugin per minute
MESH_LINES_DEFAULT = 1        # reply lines a mesh user gets per command
MESH_LINES_MAX = 3
MESH_LINE_CHARS = 200
SLOW_MS = 100                 # a callback slower than this is logged
TIMER_MIN_S = 1
TIMER_MAX_FAILS = 3
PAGE_BODY_MAX = 4096          # POST bodies handed to plugin pages
PAGE_PREFIX = "/p/"

_plugins = {}     # name -> record (see _record)
_order = []       # names in load order
_gates = []       # [priority, seq, plugin, fn], sorted
_listeners = []   # [priority, seq, plugin, fn], sorted
_results = []     # [priority, seq, plugin, fn], sorted
_commands = {}    # name -> {"plugin", "fn", "help", "mesh_lines"}
_pages = {}       # plugin -> {sub: {"fn", "raw"}}
_cards = []       # [{"plugin", "href", "label", "sub"}]
_timers = []      # [{"plugin", "fn", "every", "next", "fails"}]
_services = {}    # name -> (plugin, obj)
_seq = [0]
_cmd_use = {}     # client_id -> [last_ms, window_start_ms, count]
_post_use = {}    # plugin -> [window_start_ms, count]
_safe = {"on": False, "reason": "", "culprit": None, "stable": False}


def _now_ms():
    return time.ticks_ms()


def _since(t0):
    return time.ticks_diff(time.ticks_ms(), t0)


def _log(plugin, msg):
    print("[plugin] %s: %s" % (plugin, msg))


def _record(name, kind):
    return {"name": name, "kind": kind, "state": "on", "error": "", "mem": None,
            "version": None, "errors": 0, "loaded": False, "module": None}


# ── state on flash ─────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.loads(f.read())
        if isinstance(st, dict):
            st.setdefault("off", [])
            st.setdefault("auto_off", {})
            return st
    except Exception:
        pass
    return {"off": [], "auto_off": {}}


def _save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(json.dumps(st))
        return True
    except Exception as e:
        print("[plugin] could not save", STATE_FILE, e)
        return False


def _write_marker(d):
    try:
        with open(MARKER_FILE, "w") as f:
            f.write(json.dumps(d))
    except Exception:
        pass


def _read_marker():
    try:
        with open(MARKER_FILE) as f:
            return json.loads(f.read() or "{}")
    except Exception:
        return None


def clear_marker():
    """The boot is known good (or was stopped on purpose: Ctrl+C from the
    provisioner), so the next one starts normally."""
    try:
        import os
        os.remove(MARKER_FILE)
    except Exception:
        pass


def is_off(name):
    return name in _load_state()["off"]


# ── the Api a plugin's setup() receives ───────────────────────────────

class Api:
    """What setup(stump) gets: registration bound to one plugin's name."""

    api_version = API_VERSION

    def __init__(self, name):
        self.name = name
        self.nick = name          # who ctx.post() speaks as; a plugin may change it
        self._strings = None

    # hooks
    def command(self, word, fn, help=None, mesh_lines=MESH_LINES_DEFAULT, trigger=True, limit=True):
        """fn(ctx, arg) answers /word (and !word unless trigger=False --
        use that for anything taking a password, since a !line is posted
        to the room). help: a string, {lang: string}, or fn(lang); only
        commands with help are listed in /help."""
        word = (word or "").lower().lstrip("/!")
        if not word or " " in word:
            raise ValueError("bad command name")
        if word in CORE_COMMANDS and RESERVED_FOR.get(word) != self.name:
            raise ValueError("/%s belongs to the core" % word)
        other = _commands.get(word)
        if other and other["plugin"] != self.name:
            raise ValueError("/%s is already %s's" % (word, other["plugin"]))
        core = self.name in CORE_PLUGINS
        mesh_lines = int(mesh_lines)
        if mesh_lines == 0 and core:
            pass                     # built-in plugins may answer in full
        else:
            mesh_lines = max(1, min(mesh_lines, MESH_LINES_MAX))
        _commands[word] = {"plugin": self.name, "fn": fn, "help": help,
                           "mesh_lines": mesh_lines, "trigger": bool(trigger),
                           "limit": bool(limit) or not core}

    def gate(self, fn, priority=DEFAULT_PRIORITY):
        priority = int(priority)
        if priority < RESERVED_BAND and self.name not in CORE_PLUGINS:
            raise ValueError("gate priorities below %d are reserved" % RESERVED_BAND)
        _insert(_gates, priority, self.name, fn)

    def on_message(self, fn, priority=DEFAULT_PRIORITY):
        _insert(_listeners, int(priority), self.name, fn)

    def on_result(self, fn, priority=DEFAULT_PRIORITY):
        """fn(ctx, replies, new_room) once a line has been fully handled
        (by the core or a plugin command; not when a gate stopped it).
        Return None to leave the result alone, or (replies, new_room)."""
        _insert(_results, int(priority), self.name, fn)

    def page(self, path, fn, nav=None, sub="", raw=False):
        """path is relative to /p/<plugin>/ ("" or "results"); the full
        form "/p/<plugin>/results" is accepted too. nav (and sub, its
        subtitle) adds a home-page card: a string, {lang: string} or
        fn(lang)."""
        base = PAGE_PREFIX + self.name
        path = path or ""
        if path.startswith(PAGE_PREFIX):
            if not (path == base or path.startswith(base + "/")):
                raise ValueError("pages live under " + base + "/")
            path = path[len(base):]
        path = path.strip("/")
        _pages.setdefault(self.name, {})[path] = {"fn": fn, "raw": bool(raw)}
        if nav:
            _cards.append({"plugin": self.name, "href": base + ("/" + path if path else "/"),
                           "label": nav, "sub": sub})

    def every(self, seconds, fn):
        seconds = max(TIMER_MIN_S, int(seconds))
        _timers.append({"plugin": self.name, "fn": fn, "every": seconds,
                        "next": time.ticks_add(_now_ms(), seconds * 1000), "fails": 0})

    def provide(self, service, obj):
        _services[service] = (self.name, obj)

    def service(self, service):
        return service_lookup(service)

    # namespaced things
    def setting(self, key, default=None):
        """config.<PLUGIN>_<KEY>, e.g. TAVERN_SIDES for setting("SIDES")."""
        prefix = self.name.upper().replace("-", "_") + "_"
        key = key if key.startswith(prefix) else prefix + key
        try:
            import config
            return getattr(config, key, default)
        except ImportError:
            return default

    def storage(self):
        """/sd/plugins/<name>/, created on first use; None without a card."""
        try:
            import fserv
            if not fserv.sd_ok:
                return None
            import os
            root = fserv.SD_MOUNT + "/plugins"
            path = root + "/" + self.name
            for p in (root, path):
                try:
                    os.mkdir(p)
                except OSError:
                    pass
            os.stat(path)
            return path
        except Exception:
            return None

    def storage_file(self, filename, legacy=None):
        """A file in storage(). A plugin that kept its data elsewhere
        before (legacy) has it moved here the first time; without a card,
        the legacy path is returned unchanged."""
        folder = self.storage()
        if folder is None:
            return legacy or filename
        path = folder + "/" + filename
        if legacy and legacy != path:
            import os
            try:
                os.stat(path)
            except OSError:
                try:
                    os.stat(legacy)
                    os.rename(legacy, path)
                    self.log("moved", legacy, "to", path)
                except OSError:
                    pass
        return path

    def t(self, key, lang=None, **kw):
        if self._strings is None:
            self._strings = {}
            try:
                mod = __import__(self.name + ".strings", None, None, ("STRINGS",))
                self._strings = getattr(mod, "STRINGS", {}) or {}
            except Exception:
                pass
        import i18n
        entry = self._strings.get(key)
        if entry is None:
            return i18n.t(key, lang, **kw)
        text = entry.get(lang) or entry.get(i18n.DEFAULT_LANG) or entry.get("en") or key
        try:
            return text.format(**kw)
        except Exception:
            return text

    @staticmethod
    def esc(text):
        """Text made safe for a page's HTML (and attribute values)."""
        return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))

    def log(self, *args):
        _log(self.name, " ".join(str(a) for a in args))


def _insert(lst, priority, plugin, fn):
    _seq[0] += 1
    lst.append([priority, _seq[0], plugin, fn])
    lst.sort(key=lambda e: (e[0], e[1]))


def service_lookup(service):
    """The object a plugin provided under this name, or None -- also None
    while that plugin is switched off."""
    entry = _services.get(service)
    if entry is None or not _active(entry[0]):
        return None
    return entry[1]


def _active(plugin):
    rec = _plugins.get(plugin)
    return rec is not None and rec["state"] == "on"


# ── the per-line context ───────────────────────────────────────────────

class Ctx:
    def __init__(self, client_id, room, nick, text, lang):
        self.client_id = client_id
        self.room = room
        self.nick = nick
        self.text = text
        self.lang = lang
        self.command = None   # "roll" for "/roll 2d6" or "!roll 2d6"
        self.arg = ""
        self.is_mesh = False
        self.via_propagation = False
        self.plugin = None
        self._api = None
        self._replies = []
        try:
            import rrc_mesh
            peer = rrc_mesh._peers.get(client_id)
            if peer is not None:
                self.is_mesh = True
                self.via_propagation = bool(peer.get("via_propagation"))
        except Exception:
            pass

    def reply(self, line):
        """A line for the sender only."""
        if line:
            self._replies.append(str(line))

    def post(self, text, room=None):
        """A line in the room, as the plugin's nick; False if the plugin
        is over its posting budget."""
        return _post(self.plugin, self._api.nick if self._api else self.plugin,
                     room or self.room, text)

    def t(self, key, **kw):
        if self._api is not None:
            return self._api.t(key, self.lang, **kw)
        import i18n
        return i18n.t(key, self.lang, **kw)


def _bind(ctx, plugin):
    ctx.plugin = plugin
    rec = _plugins.get(plugin)
    ctx._api = rec.get("api") if rec else None


def _post(plugin, nick, room, text):
    if not text:
        return False
    now = _now_ms()
    use = _post_use.get(plugin)
    if use is None or time.ticks_diff(now, use[0]) > 60000:
        use = _post_use[plugin] = [now, 0]
    if use[1] >= POSTS_PER_MIN:
        if use[1] == POSTS_PER_MIN:
            _log(plugin, "over its posting budget (%d a minute); dropping" % POSTS_PER_MIN)
        use[1] += 1
        return False
    use[1] += 1
    import rrc
    return rrc.post(room, nick, str(text)[:rrc.MAX_MESSAGE_LEN]) is not None


def _lines(result):
    if result is None:
        return []
    if isinstance(result, str):
        return [result]
    return [str(x) for x in result]


def _call(plugin, what, fn, *args):
    """Runs one plugin callback: (ok, result). Exceptions are logged with
    the plugin's name and never reach the chat; slow ones are logged."""
    t0 = _now_ms()
    try:
        res = fn(*args)
        ok = True
    except Exception as e:
        _log(plugin, "%s failed: %s: %s" % (what, type(e).__name__, e))
        rec = _plugins.get(plugin)
        if rec:
            rec["errors"] += 1
            rec["error"] = "%s: %s" % (what, e)
        res, ok = None, False
    dt = _since(t0)
    if dt > SLOW_MS:
        _log(plugin, "%s took %d ms (keep callbacks under %d ms)" % (what, dt, SLOW_MS))
    return ok, res


def _error_line(ctx, plugin):
    import i18n
    return i18n.t("plugin_error", ctx.lang, plugin=plugin)


def _rate_ok(client_id):
    now = _now_ms()
    use = _cmd_use.get(client_id)
    if use is None:
        if len(_cmd_use) > 200:
            _cmd_use.clear()
        _cmd_use[client_id] = [now, now, 1]
        return True
    if time.ticks_diff(now, use[0]) < CMD_GAP_MS:
        return False
    if time.ticks_diff(now, use[1]) > 60000:
        use[1], use[2] = now, 0
    if use[2] >= CMD_PER_MIN:
        return False
    use[0] = now
    use[2] += 1
    return True


def _run_command(ctx, entry):
    import i18n, rrc
    plugin = entry["plugin"]
    if entry["limit"] and not _rate_ok(ctx.client_id):
        return [rrc.tok_rate(i18n.t("plugin_rate", ctx.lang))]
    _bind(ctx, plugin)
    ok, res = _call(plugin, ("/" + ctx.command), entry["fn"], ctx, ctx.arg)
    lines = ctx._replies + _lines(res) if ok else [_error_line(ctx, plugin)]
    ctx._replies = []
    if ctx.is_mesh and entry["mesh_lines"]:
        lines = [l if len(l) <= MESH_LINE_CHARS else l[:MESH_LINE_CHARS - 1] + "…"
                 for l in lines[:entry["mesh_lines"]]]
    return lines


def _split_command(text, prefix):
    parts = text[len(prefix):].split(" ", 1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def trigger():
    try:
        import config
        t = getattr(config, "PLUGIN_TRIGGER", "!")
    except ImportError:
        t = "!"
    return t if t and t != "/" else "!"


def _nothing_registered():
    return not (_gates or _commands or _listeners or _results)


# ── call sites in rrc.handle_input ─────────────────────────────────────

def before(client_id, room, nick, text, lang):
    """Gates, then plugin /commands. Returns ("gate", result) when a gate
    stopped the line, ("command", result) when a plugin command answered
    it, or None for rrc to carry on. result is (replies, new_room)."""
    if _nothing_registered():
        return None
    ctx = Ctx(client_id, room, nick, text, lang)
    if text.startswith("/"):
        ctx.command, ctx.arg = _split_command(text, "/")
    else:
        t = trigger()
        if text.startswith(t):
            ctx.command, ctx.arg = _split_command(text, t)
    for priority, _, plugin, fn in list(_gates):
        if not _active(plugin):
            continue
        _bind(ctx, plugin)
        ok, res = _call(plugin, "gate", fn, ctx)
        if not ok:
            if priority < RESERVED_BAND:
                # An access-control gate that crashes fails closed.
                return "gate", ([_error_line(ctx, plugin)], None)
            continue          # anything else fails open
        if res is not None:
            # Lines (or an empty list, to drop the line silently) block it.
            return "gate", (_lines(res), None)
    if text.startswith("/") and ctx.command in _commands:
        entry = _commands[ctx.command]
        if _active(entry["plugin"]):
            return "command", (_run_command(ctx, entry), None)
    return None


def finish(client_id, room, nick, text, lang, result):
    """on_result hooks, in priority order, each seeing the last one's
    output."""
    if not _results:
        return result
    ctx = Ctx(client_id, room, nick, text, lang)
    if text.startswith("/"):
        ctx.command, ctx.arg = _split_command(text, "/")
    for _, _, plugin, fn in list(_results):
        if not _active(plugin):
            continue
        _bind(ctx, plugin)
        ok, res = _call(plugin, "on_result", fn, ctx, result[0], result[1])
        if ok and res is not None:
            result = (list(res[0]), res[1])
    return result


def after(client_id, room, nick, text, lang):
    """Once rrc has posted a plain room line: a !trigger command, then
    listeners. Returns the reply lines for the sender."""
    if _nothing_registered():
        return []
    ctx = Ctx(client_id, room, nick, text, lang)
    out = []
    t = trigger()
    if text.startswith(t):
        ctx.command, ctx.arg = _split_command(text, t)
        entry = _commands.get(ctx.command)
        if entry and entry["trigger"] and _active(entry["plugin"]):
            out = _run_command(ctx, entry)
    for _, _, plugin, fn in list(_listeners):
        if not _active(plugin):
            continue
        _bind(ctx, plugin)
        ok, _ = _call(plugin, "listener", fn, ctx)
        if ok and ctx._replies:
            out += ctx._replies[:MESH_LINES_DEFAULT] if ctx.is_mesh else ctx._replies
        ctx._replies = []
    return out


def help_lines(lang, mesh=False):
    """Plugin commands for /help: one line each on the web, one compact
    line for a mesh user."""
    words = sorted(w for w, e in _commands.items() if _active(e["plugin"]) and e["help"])
    if not words:
        return []
    if mesh:
        import i18n
        t = trigger()
        line = i18n.t("plugin_help_mesh", lang,
                      cmds=" ".join((t if _commands[w]["trigger"] else "/") + w for w in words))
        return [line if len(line) <= MESH_LINE_CHARS else line[:MESH_LINE_CHARS - 1] + "…"]
    out = []
    for w in words:
        h = _commands[w]["help"]
        try:
            if callable(h):
                h = h(lang)
            elif isinstance(h, dict):
                h = h.get(lang) or h.get("en") or ""
        except Exception as e:
            _log(_commands[w]["plugin"], "help failed: %s" % e)
            h = ""
        out.append(h if h else "/" + w)
    return out


# ── pages ─────────────────────────────────────────────────────────────

class Request:
    def __init__(self, method, path, headers, body, client_id, lang, plugin, sub):
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
        self.client_id = client_id
        self.lang = lang
        self.plugin = plugin
        self.sub = sub
        self.query = {}
        if "?" in path:
            self.query = _form(path.split("?", 1)[1])
        self._form = None

    @property
    def form(self):
        if self._form is None:
            try:
                self._form = _form(self.body.decode())
            except Exception:
                self._form = {}
        return self._form


def _form(s):
    import billboard
    out = {}
    for kv in s.split("&"):
        k, _, v = kv.partition("=")
        if k:
            out[billboard._url_decode(k)] = billboard._url_decode(v)
    return out


def page_target(path):
    """(plugin, sub, entry) for a /p/ address, or None."""
    if not path.startswith(PAGE_PREFIX):
        return None
    bare = path.split("?", 1)[0][len(PAGE_PREFIX):]
    plugin, _, sub = bare.partition("/")
    pages = _pages.get(plugin)
    if not pages or not _active(plugin):
        return None
    sub = sub.strip("/")
    entry = pages.get(sub)
    if entry is None:
        return None
    return plugin, sub, entry


async def handle_page(method, path, headers, body, client_id, lang):
    """(status, body, content_type, raw) for a plugin page, or None when
    no active plugin has that address."""
    hit = page_target(path)
    if hit is None:
        return None
    plugin, sub, entry = hit
    req = Request(method, path, headers, body, client_id, lang, plugin, sub)
    t0 = _now_ms()
    try:
        res = entry["fn"](req)
        if hasattr(res, "send"):      # an async def: run it
            res = await res
    except Exception as e:
        _log(plugin, "page /%s failed: %s: %s" % (sub, type(e).__name__, e))
        rec = _plugins.get(plugin)
        if rec:
            rec["errors"] += 1
            rec["error"] = "page: %s" % e
        import i18n
        return "500 Internal Server Error", i18n.t("plugin_error", lang, plugin=plugin), "text/plain", True
    dt = _since(t0)
    if dt > SLOW_MS:
        _log(plugin, "page /%s took %d ms" % (sub, dt))
    if isinstance(res, str):
        return "200 OK", res, "text/html", entry["raw"]
    status, out, ctype = (tuple(res) + ("text/html",))[:3]
    if isinstance(status, int):
        status = {200: "200 OK", 201: "201 Created", 303: "303 See Other", 400: "400 Bad Request",
                  403: "403 Forbidden", 404: "404 Not Found"}.get(status, "%d OK" % status)
    return status, out, ctype, entry["raw"] or not ctype.startswith("text/html")


def _text(v, lang):
    """A label given as a string, {lang: string} or fn(lang)."""
    try:
        if callable(v):
            v = v(lang)
        elif isinstance(v, dict):
            v = v.get(lang) or v.get("en") or next(iter(v.values()), "")
    except Exception:
        v = ""
    return str(v or "")


def home_cards(lang=None):
    """The home-page cards plugins asked for, labels in this language."""
    return [{"plugin": c["plugin"], "href": c["href"], "label": _text(c["label"], lang),
             "sub": _text(c["sub"], lang)} for c in _cards if _active(c["plugin"])]


# ── timers ────────────────────────────────────────────────────────────

async def _run_async(t, coro):
    try:
        await coro
        t["fails"] = 0
    except Exception as e:
        _timer_failed(t, e)


def _timer_failed(t, e):
    t["fails"] += 1
    _log(t["plugin"], "timer failed (%d of %d): %s" % (t["fails"], TIMER_MAX_FAILS, e))
    if t["fails"] >= TIMER_MAX_FAILS:
        _log(t["plugin"], "timer switched off after %d failures in a row" % TIMER_MAX_FAILS)
        rec = _plugins.get(t["plugin"])
        if rec:
            rec["error"] = "timer switched off: %s" % e


async def scheduler_loop():
    """One task for every plugin timer, checked once a second."""
    import uasyncio as asyncio
    while True:
        await asyncio.sleep(1)
        now = _now_ms()
        for t in list(_timers):
            if t["fails"] >= TIMER_MAX_FAILS or not _active(t["plugin"]):
                continue
            if time.ticks_diff(now, t["next"]) < 0:
                continue
            t["next"] = time.ticks_add(now, t["every"] * 1000)
            t0 = _now_ms()
            try:
                res = t["fn"]()
                if hasattr(res, "send"):
                    asyncio.create_task(_run_async(t, res))
                else:
                    t["fails"] = 0
            except Exception as e:
                _timer_failed(t, e)
            dt = _since(t0)
            if dt > SLOW_MS:
                _log(t["plugin"], "timer took %d ms (make it an async def that yields)" % dt)


async def stable_loop():
    """Clears the safe-mode marker once the node has run for a minute."""
    import uasyncio as asyncio
    await asyncio.sleep(STABLE_AFTER_S)
    _safe["stable"] = True
    clear_marker()


# ── loading ───────────────────────────────────────────────────────────

def discover():
    import os
    try:
        entries = os.listdir()
    except Exception:
        return []
    found = []
    for name in sorted(entries):
        if name.startswith(".") or name in NOT_PLUGINS:
            continue
        try:
            os.stat(name + "/install.py")
        except OSError:
            continue
        found.append(name)
    return found


def _mem_free():
    try:
        import gc
        if getattr(gc, "STUMP_NO_MEM_FREE", False):
            return None     # a computer: no free-RAM figure, so no "0 B RAM" per plugin
        gc.collect()
        return gc.mem_free()
    except Exception:
        return None


def load_one(name):
    """Imports and starts one plugin. True if it is running."""
    rec = _plugins.get(name) or _record(name, "?")
    _plugins[name] = rec
    if name not in _order:
        _order.append(name)
    before_mem = _mem_free()
    try:
        mod = __import__(name + ".install", None, None, ("activate", "setup"))
        rec["module"] = mod
        rec["version"] = getattr(mod, "VERSION", None)
        need = getattr(mod, "STUMP_API", 1)
        if need > API_VERSION:
            rec["state"] = "failed"
            rec["error"] = "needs plugin API %s; this firmware has %d" % (need, API_VERSION)
            _log(name, rec["error"])
            return False
        if hasattr(mod, "setup"):
            rec["kind"] = "setup"
            api = rec.get("api") or Api(name)
            rec["api"] = api
            mod.setup(api)
            ok = True
        else:
            rec["kind"] = "legacy"
            ok = bool(mod.activate())
        after_mem = _mem_free()
        if before_mem is not None and after_mem is not None:
            rec["mem"] = max(0, before_mem - after_mem)
        rec["loaded"] = ok
        rec["state"] = "on" if ok else "failed"
        if not ok and not rec["error"]:
            rec["error"] = "activate() returned False"
        return ok
    except Exception as e:
        rec["state"] = "failed"
        rec["error"] = "%s: %s" % (type(e).__name__, e)
        _log(name, "did not start: " + rec["error"])
        _unregister(name)
        return False


def _unregister(name):
    for lst in (_gates, _listeners, _results):
        lst[:] = [e for e in lst if e[2] != name]
    for w in [w for w, e in _commands.items() if e["plugin"] == name]:
        del _commands[w]
    _pages.pop(name, None)
    _cards[:] = [c for c in _cards if c["plugin"] != name]
    _timers[:] = [t for t in _timers if t["plugin"] != name]


def load_all():
    """The board-side loader: every plugin folder, in alphabetical order
    (which still decides how activate()-style plugins wrap each other),
    each one guarded. Returns the names that are running."""
    st = _load_state()
    marker = _read_marker()
    if marker is not None:
        _safe["on"] = True
        culprit = marker.get("plugin")
        if culprit:
            # It reset or hung the board while starting: off until someone
            # turns it back on in /admin.
            _safe["culprit"] = culprit
            _safe["reason"] = "starting"
            if culprit not in st["off"]:
                st["off"].append(culprit)
            st["auto_off"][culprit] = "the node restarted while it was starting"
            _save_state(st)
            print("[plugin] SAFE MODE: %s was starting when the node last stopped -- "
                  "switched off (turn it back on in /admin)" % culprit)
            _safe["on"] = False   # the culprit is known: the others load normally
        else:
            _safe["reason"] = "running"
            print("[plugin] SAFE MODE: the node stopped within %d s of starting last time -- "
                  "loading only %s this boot" % (STABLE_AFTER_S, ", ".join(CORE_PLUGINS)))
    running = []
    for name in discover():
        rec = _plugins.setdefault(name, _record(name, "?"))
        if name not in _order:
            _order.append(name)
        if name in st["off"]:
            rec["state"] = "off"
            continue
        if _safe["on"] and name not in CORE_PLUGINS:
            rec["state"] = "off"
            rec["error"] = "safe mode"
            continue
        _write_marker({"plugin": name})
        if load_one(name):
            running.append(name)
    _write_marker({"plugin": None})
    if running:
        print("[plugin] active:", ", ".join(running))
    return running


def set_enabled(name, on):
    """Switch a plugin on or off, saved across restarts. Returns
    (ok, now): now is True if it took effect immediately, False if it
    needs a restart."""
    rec = _plugins.get(name)
    if rec is None:
        return False, False
    if not on and name in PROTECTED:
        return None, False
    st = _load_state()
    if on:
        if name in st["off"]:
            st["off"].remove(name)
        st["auto_off"].pop(name, None)
        _save_state(st)
        if rec["loaded"]:
            if rec["kind"] == "legacy" and rec["state"] == "off-at-restart":
                rec["state"] = "on"     # never stopped: it's still running
                return True, True
            if rec["kind"] == "legacy":
                ok = _call(name, "activate", rec["module"].activate)[1]
                rec["state"] = "on" if ok else "failed"
                return True, bool(ok)
            rec["state"] = "on"
            return True, True
        _write_marker({"plugin": name})
        ok = load_one(name)
        if _safe["stable"]:
            clear_marker()
        else:
            _write_marker({"plugin": None})
        return True, ok
    if name not in st["off"]:
        st["off"].append(name)
    _save_state(st)
    if rec["kind"] == "legacy" and rec["loaded"]:
        deact = getattr(rec["module"], "deactivate", None)
        ok = bool(deact and _call(name, "deactivate", deact)[1])
        rec["state"] = "off" if ok else "off-at-restart"
        return True, ok
    rec["state"] = "off"
    return True, True


# ── what /admin, /plugins.json and chat show ──────────────────────────

def status():
    st = _load_state()
    out = []
    for name in _order:
        rec = _plugins[name]
        out.append({
            "name": name, "kind": rec["kind"], "state": rec["state"],
            "version": rec["version"], "mem": rec["mem"], "errors": rec["errors"],
            "error": st["auto_off"].get(name) or rec["error"],
            "commands": sorted(w for w, e in _commands.items() if e["plugin"] == name),
            "pages": [PAGE_PREFIX + name + ("/" + p if p else "/") for p in _pages.get(name, {})],
            "timers": len([t for t in _timers if t["plugin"] == name]),
        })
    return out


def safe_mode():
    return dict(_safe)


def public_info():
    """/plugins.json: what a client can offer its user."""
    return {
        "api": API_VERSION,
        "trigger": trigger(),
        "plugins": [{"name": p["name"], "version": p["version"], "commands": p["commands"],
                     "pages": p["pages"]}
                    for p in status() if p["state"] == "on"],
    }


def admin_chat(rest, lang=None):
    """/admin <password> plugin [on|off <name>] -- the password is already
    checked by the caller."""
    import i18n
    if not rest:
        items = status()
        if not items:
            return [i18n.t("plugin_none", lang)]
        return [i18n.t("plugin_list_line", lang, name=p["name"], state=p["state"],
                       cmds=" ".join("/" + c for c in p["commands"]) or "-") for p in items]
    if len(rest) < 2 or rest[0].lower() not in ("on", "off"):
        return [i18n.t("plugin_admin_usage", lang)]
    action, name = rest[0].lower(), rest[1]
    ok, now = set_enabled(name, action == "on")
    if ok is None:
        return [i18n.t("plugin_protected", lang, name=name)]
    if not ok:
        return [i18n.t("plugin_no_such", lang, name=name)]
    key = ("plugin_on_now" if now else "plugin_on_failed") if action == "on" else \
          ("plugin_off_now" if now else "plugin_off_restart")
    return [i18n.t(key, lang, name=name)]
