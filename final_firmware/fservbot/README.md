# fservbot — mIRC-style fserv bot for RRC

Drop-in plugin for Project Stump Beta A. Unzips to
`final_firmware/fservbot/`. Edits no existing file.

---

## Install

Nothing to do: it ships in `final_firmware/fservbot/`, and the node's
plugin loader (`plugins.py`, see `docs/PLUGINS.md`) finds it at boot.
The config block is optional (the provisioner writes it — see
`plugin.json`):

```python
FSERVBOT_OP_PASSWORD = ""      # blank = dialogue editing off
FSERVBOT_TRIGGER_PREFIX = "!"
FSERVBOT_BROADCAST_MINS = 0
```

A `config.py` without them runs the bot on defaults.

Switch it off without a laptop: `/admin <password> plugin off
fservbot`, or `/admin` → **Plugins**.

### Boot log

```
[fservbot] active: prefix '!', 7 trigger(s), notice off
[fservbot] no operator password set -- dialogue editing is OFF
```

The second line only appears when the password is blank.

---

## How it hooks in (plugin API 1)

`install.py` has `setup(stump)`, which registers:

- the `/fs*` commands — **slash only** (`trigger=False`): several carry
  the operator password, and a `!` line is posted to the room for all to
  read;
- a listener for the channel triggers (`!fserv`, `!list`, …), answering
  as the bot's own name;
- a 30-second timer for the periodic notice.

Before the plugin API it wrapped `rrc.handle_input`; that wrapper, its
re-entry guard and `deactivate()` are gone.

## Two setup surfaces, one backend

**Technician, at provision time.** The provisioner writes
`FSERVBOT_OP_PASSWORD` into `config.py`. That is the only prompt that
matters; prefix and broadcast are advanced and have working defaults.

**A user in RRC, after boot.** Anyone who knows the password
configures the bot live from the channel — no serial, no reflash:

```
/fshelp                               explains the rest (no password)
/fslist                               active triggers (no password)
/fsshow <trigger>                     preview a reply (no password)
/fsadd <pw> <trigger> :: <response>   add or replace
/fsdel <pw> <trigger>                 remove (base or custom)
/fsset <pw> prefix <char>             change trigger character
/fsset <pw> broadcast <mins|off>      periodic notice
```

Both surfaces read and write the same state, so neither can produce a
node the other can't understand.

**The password itself is only settable in `config.py`** — that is, by a
technician with the provisioner or a serial connection, never from
chat. A password rotatable by whoever currently holds it is a door
that locks the operator out of their own node, and `README.md`'s
"Air-gapped administration" section puts settings behind a physical
connection. Dialogues, prefix, and broadcast interval are content, so
they're editable from the channel and persist to SD next to the
billboard.

---

## What it ships answering

`!fserv` · `!about` · `!help` · `!list` · `!files` · `!info` · `!rules`

Rewrite the node's voice by editing `templates.py` — one file, plain
functions returning plain strings, no engine internals.

Base templates are **switched off**, not deleted, by `/fsdel`, and come
back with `/fsadd` or by deleting `/sd/fservbot.json`. A custom
dialogue on the same trigger overrides the base one.

Responses are **plain text, not HTML** — unlike
`barkeep._process_command`, which returns markup. These land in an RRC
message that `rrc_ui.render()` escapes with `esc()` like any other chat
line, so a `<a href>` would display as literal tags. That escaping is
deliberate: an operator-supplied string replayed to every visitor
should go through the same path as a stranger's chat message. Don't add
an unescaped path for bot lines.

---

## Bounds

Same philosophy as `rrc.py` — nothing an operator can append to grows
without a ceiling.

| Bound | Value |
|---|---|
| `MAX_DIALOGUES` | 40 |
| `MAX_TRIGGER_LEN` | 24 |
| `MAX_RESPONSE_LEN` | 300 |
| `MIN_BROADCAST_MINS` | 5 |

Storage: `/sd/plugins/fservbot/fservbot.json`, created on first edit
(an older `/sd/fservbot.json` is moved there on the first boot). With
no card, edits last until reboot and every command that saves says so.

---

## Event loop

Binds nothing. The periodic notice runs on the plugin API's shared
timer (every 30 s, a timestamp comparison), and only in a room where
someone has spoken since the last notice — an empty room, or a LoRa
mesh, is never advertised to.

---

## Known limits

- **The bot's nick isn't reserved.** `/nick BarKeep` lets someone post
  under its name. Same class as IP-based credit identity — RRC has no
  accounts, and a reserved-name check would mean `rrc.py` importing the
  plugin, closing a cycle and undoing the drop-in property.
- **The operator password is plain chat over plain HTTP**, like every
  other credential here. It stops a curious walk-up visitor, nothing
  stronger.
- **A `/fs*` name added to `rrc.py` later would be shadowed** by this
  plugin, since plugin commands are looked up first. `core.COMMANDS` is
  the list to check against.

---

## Testing

Against real MicroPython (Unix port), through `plugins.load_all()` and
`rrc.handle_input()`; the replies are compared line by line with the
build before the plugin API.
