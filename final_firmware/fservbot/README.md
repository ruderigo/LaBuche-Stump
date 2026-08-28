# fservbot — mIRC-style fserv bot for RRC

Drop-in plugin for Project Stump Beta A. Unzips to
`final_firmware/fservbot/`. Edits no existing file.

---

## Install

1. Unzip this folder into `final_firmware/` so you have
   `final_firmware/fservbot/`.
2. Add two lines to `example_node.py` — after RRC exists, before the
   server starts serving:

```python
import fservbot.install
fservbot.install.activate()
```

3. Add the config block (or let the provisioner write it — see
   `plugin.json`):

```python
FSERVBOT_OP_PASSWORD = ""      # blank = dialogue editing off
FSERVBOT_TRIGGER_PREFIX = "!"
FSERVBOT_BROADCAST_MINS = 0
```

All three are optional. A `config.py` without them runs the bot on
defaults — config is an override here, not a dependency.

Uninstall is deleting the two lines and this folder.

### Boot log

`activate()` prints its own result, like every other component:

```
[fservbot] active: prefix '!', 7 trigger(s), notice off
[fservbot] no operator password set -- dialogue editing is OFF
```

The second line only appears when the password is blank. If neither
line appears, `activate()` was never reached.

---

## The one design decision worth reviewing

The bot needs to see what people type in the channel. That text
reaches `rrc.handle_input()` via barkeep's `POST /rrc/send`, so that
one function is the entire integration point.

`install.py` **wraps it at runtime** rather than asking you to edit
`barkeep.py`. That keeps `barkeep.py` and `rrc.py` byte-identical to
the files you already tested and hashed, so no existing manifest entry
changes and there is no merge to review.

The cost, stated plainly: monkey-patching is invisible in the source of
the file being patched. Someone reading `rrc.py` will not see that
`handle_input` has been wrapped. It's mitigated by the boot line, a
refusal to double-wrap, and `deactivate()` — but it's a real
trade-off.

**If you'd rather have it explicit**, the equivalent is ~8 lines in
barkeep's `/rrc/send` branch: dispatch `/fs*` names in `core.COMMANDS`
to `core.handle_command()`, and after a plain line that `rrc` accepted
cleanly, call `core.maybe_respond()` and post the result with
`rrc.post()`. Then delete `install.py`. Both paths are supported; the
wrapper is just the one that needs nothing from you.

---

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

Storage: `/sd/fservbot.json`, created on first edit. With no card,
edits last until reboot and every command that saves says so.

---

## Event loop

Binds nothing. Starts no task. Adds no `await`.

The periodic notice is driven by chat activity, not a timer — a
background coroutine in a cooperative single-threaded loop is exactly
what this project's "single most important structural fact" warns
about, and an empty room doesn't need advertising to. `maybe_broadcast`
is a timestamp comparison on a line someone already sent.

---

## Known limits

- **The bot's nick isn't reserved.** `/nick BarKeep` lets someone post
  under its name. Same class as IP-based credit identity — RRC has no
  accounts, and a reserved-name check would mean `rrc.py` importing the
  plugin, closing a cycle and undoing the drop-in property.
- **The operator password is plain chat over plain HTTP**, like every
  other credential here. It stops a curious walk-up visitor, nothing
  stronger.
- **Wrapping is invisible at the patch site.** See above.
- **A `/fs*` name added to `rrc.py` later would be shadowed** by this
  plugin, since the wrapper looks first. `core.COMMANDS` is the list to
  check against.

---

## Testing

Tested against real MicroPython (1.22, Unix port) with real sockets,
against an **unmodified** `barkeep.py` and `rrc.py`:

```bash
cd final_firmware
micropython -c "import fservbot.core"
micropython -c "import fservbot.install"
```

Then the server harness from the main README, with
`fservbot.install.activate()` after the imports, and drive it with
`curl` against `POST /rrc/send`.
