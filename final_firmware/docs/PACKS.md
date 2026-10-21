# Packs

A **plugin** is what runs on the node: one folder with an `install.py`
(`PLUGINS.md`). A **pack** is what people download and share, like an
mIRC script: one or more plugins, and optionally a look, a bot persona
and files for the SD card, installed in one step.

```text
tavern-pack/
  pack.json        name, version, author, licence, what's inside, hashes
  README.md        for people
  tavern/          a plugin: install.py, strings.py, plugin.json, ...
  tools/           optional: the pack's section of the Tools page (SD card),
                   with a README.md
  home/            optional: a home-page section (SD card, HOME_BRANDING.md)
```

Two reference packs ship in `packs/`: **tavern** (`!roll`, `!8ball`,
`!slap`, `!seen`, `!quote`, `!vote`, a flood guard, a theme and a
persona) and **rnfs** (the Reticulum file system on the Tools page, a
link spotter and `!shelf`).

## Installing a pack (technicians)

Put the pack, as a folder or a `.zip`, in **`packs/` next to
`provisioner.py`**. Then:

- **The wizard** (`python3 provisioner.py`) asks once which packs to
  install, then asks each plugin's own questions with the others. A
  pack's persona is offered as the bot's name and mesh welcome; its
  theme is added to the theme choices.
- **`--upload-app PORT`** offers the packs it finds after the upload
  (their settings keep their defaults).
- **`--install-pack PORT packs/tavern-pack`** adds one to a node that's
  already set up.

Then restart the node.

Before installing, the Provisioner shows each pack's name, author,
version, licence, rough RAM need, and every file with its hash, and
checks them:

- **Refused:** a pack that needs a newer plugin API than this firmware
  has, or whose plugin folder has no `install.py` or takes a reserved
  name (`urns`, `lib`, …).
- **Noted, your call:** a file that changed since the author hashed it,
  files with no hash at all, and calls that would hold the node
  (`time.sleep()`, raw sockets, `input()`, …).

**A pack is code with full access to the node.** The checks catch
honest mistakes and damaged downloads; they can't make an untrusted
pack safe. Install packs from people you trust.

Other commands:

| | |
|---|---|
| `python3 provisioner.py --list-packs` | What's in `packs/`, with hashes and notes |
| `python3 provisioner.py --remove-plugin PORT tavern` | Takes a plugin off the node (its data in `/sd/plugins/tavern/` stays) |
| `/admin <password> plugin off tavern` | Switches it off without a laptop (`/admin` → **Plugins** too) |

**A pack's tools section** gets its title and summary from a
`README.md` in `tools/`: its first `# ` line, and the paragraph under
it. The whole README folds out on the page under the files. Add
`README.fr.md` and `README.es.md` beside it for French and Spanish
readers (each falls back to `README.md`). Without one, the section is
titled with the pack's name.

Files in `tools/` or `home/` over 1 MB aren't copied over USB (about
1 KB/s): the Provisioner says which, for a card reader.

## Writing a pack

### `pack.json`

```json
{
  "name": "tavern",
  "version": "1.0",
  "author": "Project Stump",
  "license": "MIT",
  "stump_api": 1,
  "description": "Dice, fortunes, quotes, polls...",
  "ram_kb": 30,
  "plugins": ["tavern"],
  "theme": {"name": "tavern", "label": {"fr": "Taverne", "en": "Tavern", "es": "Taberna"},
            "colors": {"--bg": "#1a120b", "--panel": "#2b1d12", "--ember": "#e0a040", "--text": "#f0e2c8"}},
  "persona": {"bot_name": "Tavernier", "mesh_greeting": "Bienvenue à la taverne !"},
  "tools": true,
  "home": true
}
```

| Field | |
|---|---|
| `name`, `version`, `author`, `license` | Required |
| `stump_api` | The plugin API version it needs (1). Firmware with an older one refuses the pack, and the node itself won't start a plugin that asks for more than it has |
| `description` | Shown before installing |
| `credits` | Who wrote what the pack hands out (third-party software, art...), with its licence and address; shown before installing. Credit them on the pack's own page too |
| `ram_kb` | A rough figure, so a technician can plan; `/admin` shows what each plugin really took |
| `plugins` | The plugin folders inside |
| `theme` | A palette offered beside amber, phosphor, oled and paper. `name`: lowercase letters, digits, `-`. `colors`: any of `--bg --panel --panel-2 --line --text --muted --dim --border --ember --ember-bright --action --dm --dm-nick --err`, as `#rgb` or `#rrggbb`; any you leave out stay amber's. Anything else is ignored |
| `persona` | `bot_name`, `mesh_greeting`: offered as defaults in the wizard |
| `tools`, `home` | `false` to leave out a `tools/` or `home/` folder that's there. Otherwise `tools/` becomes the pack's own section of the Tools page, `tools/<name>/` on the SD card (a folder inside `tools/` becomes a section of that name instead), and `home/` goes to the card's `home/` |

### Each plugin's `plugin.json`

```json
{
  "plugin": "tavern",
  "title": "Tavern (dice, fortunes, quotes, !seen, polls, flood guard)",
  "version": "1.0",
  "summary": "...",
  "wizard": {"prompts": [
    {"key": "TAVERN_VOTE_MINUTES", "prompt": "Minutes a poll stays open",
     "type": "int", "default": 30, "advanced": true, "writes_to": "config.py"}
  ]},
  "files": ["tavern/__init__.py", "tavern/install.py", "tavern/strings.py", "tavern/plugin.json"]
}
```

`files` is what gets uploaded (`plugin.json` and `.md` files stay on the
laptop). Prompts are asked by the wizard and written to `config.py`;
`type` is `string`, `int` or `choice` (with `options`), and `advanced`
ones are only asked on request. Keys start with the plugin's name.

### Hash it, then publish

```
python3 provisioner.py --hash-pack packs/tavern-pack
```

writes a hash of every file into each `plugin.json` and `pack.json`,
and lists any calls that would hold the node. Run it after every
change. To publish, zip the folder (the folder itself at the top of the
zip) as `tavern-pack-1.0.zip`.

For now packs live in `packs/` in the LaBuche-Stump repository; the
Stump team reviews what's listed there. Signed packs can come later —
the node already has Ed25519 through Reticulum, so a pack could be
signed with its author's Reticulum identity.
