# Writing a Stump plugin

A plugin is a folder on the board with an `install.py`. Drop it in, and
at the next boot the node loads it: no core file to edit. This page is
the plugin API, version **1** (`plugins.py`).

```python
# tavern/install.py
import urandom

VERSION = "0.1"
STUMP_API = 1          # the API version this plugin needs

def setup(stump):
    stump.nick = "Tavernier"          # who the plugin speaks as in rooms
    stump.command("roll", roll, help="!roll 2d6 -- throw some dice")

def roll(ctx, arg):
    n, _, sides = (arg or "1d6").partition("d")
    n, sides = min(int(n or 1), 10), min(int(sides or 6), 100)
    total = sum(urandom.randint(1, sides) for _ in range(n))
    ctx.post("%s rolls %s: %d" % (ctx.nick, arg or "1d6", total))
```

`setup(stump)` is called once at boot with an object bound to your
plugin's name. fservbot and stumpid, which ship with the node, are
written this way too and are good examples. A plugin with only
`activate()` (the older style, which patched core functions itself) is
still loaded, but can't be switched off without a restart.

**A plugin is trusted code.** MicroPython has no sandbox: a plugin can
do anything on the board. The rules below keep an honest but buggy
plugin from taking a node down. They can't stop a malicious one, so
only install plugins you trust.

## What a plugin can register

| Call | What it does |
|---|---|
| `stump.command("roll", fn, help="…", mesh_lines=1, trigger=True)` | `fn(ctx, arg)` answers `/roll …` and, unless `trigger=False`, `!roll …`. Only commands with `help` are listed in `/help` |
| `stump.gate(fn, priority=50)` | `fn(ctx)` sees every line first; returns `None` to let it through, or reply lines to stop it (`[]` stops it silently) |
| `stump.on_message(fn, priority=50)` | `fn(ctx)` after a plain room line has been posted |
| `stump.on_result(fn, priority=50)` | `fn(ctx, replies, new_room)` once a line has been handled (not when a gate stopped it); return `None`, or a new `(replies, new_room)` |
| `stump.page("votes", fn, nav=None, sub="", raw=False)` | A page at `/p/<plugin>/votes`; `nav="Votes"` also adds a home-page card (`nav` and `sub` can be a string, `{lang: string}` or `fn(lang)`) |
| `stump.every(60, fn)` | `fn()` every 60 seconds (1 s at the shortest) |
| `stump.post(text, room="main", wait=True)` | **API 4.** A line in a room, as `stump.nick`, from anywhere (a timer, a poll closing, a bot): BarKeep paces it with `ctx.post()` (below, *Airtime*). `True` if posted or waiting its turn, `False` if refused. `wait=False`: refused at once when over the budget, never queued (for a plugin that counts its refusals) |
| `stump.provide("name", obj)` / `stump.service("name")` | Share an object with other plugins |
| `stump.mesh_out(fn, priority=50)` | **API 2.** `fn(dest_hex, text, title)` for each LXMF message the Stump sends to a person's app (never to another Stump); return `None`, or a new `(text, title)` |
| `stump.on_announce("nomadnetwork", ("node",), fn)` | **API 3.** `fn(dest_hex, app_data)` for each announce of that kind heard |
| `r = stump.connect(dest_hex, "rrc", ("hub",), identify=False)` | **API 3.** A link to another node's destination, made when first used: `r.request(path, data, fn)` (`fn(response or None)`), `r.send(bytes)`, `r.on_packet(fn)`, `r.on_close(fn)`, `r.is_open()`, `r.close()`. `identify`: prove the Stump's identity on it |
| `stump.admin_panel(title, render, handle)` | **API 3.** A section of the plugin's own in `/admin`, behind its login: `render(lang, hidden)` returns HTML whose forms post to `/admin/panel` and include `hidden`; `handle(fields, lang)` returns a message for the admin |
| `door = stump.node("nomadnetwork", "node")` | **API 2.** A Reticulum destination of the Stump's own identity. `door.handle("/page/index.mu", fn)`: `fn(path, data, remote_hex)` answers a link request (bytes or str; `None`: no answer; `remote_hex` is the identity the visitor proved, or `None`). `door.announce(app_data)`. The core's names (`lxmf`, `stump`, `rrc`, ...) are refused; requests over 4 KB too. It answers only while the plugin is on |
| `stump.setting("SIDES", 6)` | Reads `TAVERN_SIDES` from `config.py` |
| `stump.storage()` | Your own folder, `/sd/plugins/<plugin>/`; `None` without an SD card |
| `stump.storage_file("data.json", legacy=None)` | A file in that folder (moving `legacy` there the first time) |
| `stump.esc(text)` | Text made safe to put in a page's HTML |
| `stump.nick` | Who `ctx.post()` speaks as (the plugin's name unless you set it) |
| `stump.t("key", lang, **kw)` | Your own translations (below) |
| `stump.log(…)` | A `[plugin] tavern: …` line in the node's log |

### How a chat line flows

1. **Gates**, lowest priority number first. The first one that returns
   something stops the line there.
2. **`/commands`**: a plugin's, then the core's. A plugin can't take a
   core command's name (`/nick`, `/join`, `/msg`, `/admin`, `/auth`, …).
3. The core posts the line (a plain room line, not a `/command`).
4. **`!commands`**: a posted line starting with `!` that names a plugin
   command runs it, so everyone sees what was asked and the answer.
5. **Listeners**, by priority.

Gates see everything, including `/msg` (they need to, to gate it).
Listeners only see plain room lines: never DMs, never voice notes.

**Priorities:** 0–19 are reserved for access control (stumpid only);
20–39 for protection (flood guards, filters); 50 is the default.

### `ctx`

| | |
|---|---|
| `ctx.client_id`, `ctx.nick`, `ctx.room`, `ctx.lang` | Who sent it, where, in which language |
| `ctx.text` | The line as typed |
| `ctx.command`, `ctx.arg` | `"roll"`, `"2d6"` for `/roll 2d6` or `!roll 2d6`; `None` for plain text |
| `ctx.is_mesh` | A LoRa user: keep it short |
| `ctx.via_propagation` | The line came from a propagation node, not heard live |
| `ctx.reply(line)` | A line for the sender only |
| `ctx.post(text, room=None)` | A line in the room (this line's unless given), as `stump.nick`; paced by BarKeep like `stump.post()` |
| `ctx.t(key, **kw)` | Your string in the sender's language |

A command can also just `return` its reply lines (a string or a list).

**Passwords: `trigger=False`.** A `!word` line is posted to the room
before the command runs, so anything that takes a password (or anything
private) must be slash-only.

### Pages

```python
def setup(stump):
    stump.page("", index, nav="Votes", sub="What the tavern thinks")
    stump.page("api", results, raw=True)

def index(req):
    return "<h1>Votes</h1><p>…</p>"           # framed like every other page

async def results(req):                         # async is fine
    return 200, '{"yes": 3}', "application/json"
```

`req` has `method`, `path`, `query` (a dict), `form` (a POST form as a
dict), `body` (bytes, 4 KB at most), `headers`, `client_id` and `lang`.
Return HTML as a string, or `(status, body, content_type)`. HTML is
wrapped in the node's own page (theme, language switcher, navigation)
unless you pass `raw=True`; anything that isn't HTML is sent as is.
`(303, "/p/tavern/", "text/html")` redirects. Pages never go over LoRa.

### Timers

```python
def setup(stump):
    stump.every(300, close_old_votes)            # every 5 minutes
```

A timer must return quickly. Anything slow is an `async def` that
yields (`await asyncio.sleep(0)` between steps). A timer that fails 3
times in a row is switched off until the next boot.

### Strings

Ship a `strings.py` beside `install.py`, in the same shape as the
core's `i18n.py`, with French, English and Spanish:

```python
STRINGS = {
    "rolls": {"fr": "{who} lance {dice} : {total}",
              "en": "{who} rolls {dice}: {total}",
              "es": "{who} tira {dice}: {total}"},
}
```

`ctx.t("rolls", who=…, dice=…, total=…)` picks the sender's language,
then English. A key you don't have falls back to the core's strings.

### Settings

Config keys start with your plugin's name in capitals: `TAVERN_SIDES`,
`TAVERN_QUOTES_MAX`. Read them with `stump.setting("SIDES", 6)`.

## Ground rules

- **Errors stay local.** An exception in any callback is logged with
  your plugin's name; the person sees `<plugin> had a problem — not your
  fault, try again later`. The line they typed is never lost. A gate in
  the access-control band that crashes blocks the line (fails closed);
  any other gate that crashes lets it through.
- **No blocking.** No `time.sleep()`, no blocking sockets, nothing that
  holds the event loop. Any callback over 100 ms is logged.
- **Airtime.** Over the mesh a command answers in one line (ask for up
  to three with `mesh_lines=`), each cut at 200 characters. A plugin
  posts at most 6 room lines in any rolling minute — every room line
  also goes over LoRa to the mesh users in that room. BarKeep keeps the
  count for every plugin (`ctx.post()` and `stump.post()` alike): a line
  over it waits in a short queue (3 lines) and goes out as soon as the
  minute allows, so don't retry; with the queue full the line is refused
  (`False`). Post through these two only, never `rrc.post()` directly.
- **Flood control.** One plugin command per person per 1.5 s, 15 a
  minute; past that the reply is `⧗ — …`.
- **Storage** goes in `stump.storage()`, nowhere else. It's `None` on a
  node without an SD card: say so, don't crash.
- **Memory.** The node logs how much RAM each plugin took to start, and
  `/admin` shows it.

## Turning a plugin off

From `/admin` → **Plugins**, or in chat:

```
/admin <password> plugin                list them
/admin <password> plugin off tavern
/admin <password> plugin on tavern
```

Saved across restarts, on the board's own memory, so it works without
an SD card. `stumpid` can't be switched off: it is the node's access
control. An older-style plugin that can't stop while running (because
another plugin wrapped it later) is off from the next restart.

## Safe mode

If the node resets or hangs while a plugin is **starting**, that plugin
is switched off at the next boot and `/admin` says which one it was.
Turn it back on from there once it's fixed.

If the node stops within a minute of starting, but after every plugin
has started, the next boot loads only the built-in plugins (stumpid,
fservbot) and `/admin` says so. The boot after that is normal again.

Stopping the node with Ctrl+C (as the provisioner does) doesn't count,
even while a plugin is starting (since 1.0.0-beta.73).

**stumpid is never switched off by safe mode** (since 1.0.0-beta.73): it's
the access control, and it checks `/admin`'s password. A board gets reset
while it starts often enough on purpose -- Arborist and the Provisioner
reset it, or stop it for a prompt, when they connect -- and a stumpid
switched off that way left `/auth` "unknown" and `/admin` refusing every
password. One switched off by an older firmware is switched back on.

A built-in plugin (stumpid, fservbot) that **fails to start** is tried
again once, a minute after the boot. While stumpid isn't running,
`/admin`'s login checks the password in `config.py` itself (fail-closed,
as a rescue start does), and `/auth`, `/whoami`, `/admin`, `/invite` say
identity checks are unavailable, and why, instead of "unknown command".

## Getting a plugin onto a node

As part of a **pack** (`PACKS.md`): put the pack in `packs/` next to
`provisioner.py` and run the wizard, or
`python3 provisioner.py --install-pack PORT packs/my-pack`. To try a
single plugin folder while writing it, `mpremote cp -r tavern :` and
restart the node.

Before publishing, give it a `plugin.json` with its `files`, and run
`python3 provisioner.py --hash-pack my-pack` to record a hash of every
file; it also warns about calls that would hold the node
(`time.sleep()`, raw sockets, `input()`, …).

## The reference plugins

- `fservbot/` and `stumpid/` in the firmware: commands, a gate in the
  reserved band, `on_result`, a timer, a service, storage migration.
- `packs/tavern-pack/` and `packs/rnfs-pack/`: commands with `!`
  triggers, listeners, a flood-guard gate, timers, pages with home
  cards, storage, translations.


## Plugin API versions

- **1**: everything above but `mesh_out` and `node`.
- **2** (1.0.0-beta.53): `stump.mesh_out()` and `stump.node()` (with
  `door.on_link()` for incoming links).
- **3** (1.0.0-beta.55): `stump.on_announce()`, `stump.connect()`,
  `stump.admin_panel()`.
- **4** (1.0.0-beta.61): `stump.post()`, and posting paced by BarKeep
  (a rolling minute with a short queue; it was a fixed minute, the rest
  dropped).

A plugin that uses them says `STUMP_API = 2`, `3` or `4` (and `"stump_api"`
in its pack); older firmware refuses it instead of failing at start.
