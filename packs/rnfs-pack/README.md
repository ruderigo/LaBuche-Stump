# rnfs pack

[rnfs](https://github.com/afit21/rnfs) shares files over Reticulum —
LoRa included — by content ID: `rnfs add photo.jpg` gives a CID, and
`rnfs get <cid>` fetches it from any node that has it. It's a CPython
daemon, so it runs on visitors' computers, not on the ESP32. This pack
hands it out and speaks its link format.

| | |
|---|---|
| **Tools page** | Everything for an **offline** install on any computer with Python 3.11+: `rnfs-0.1.1` (built from the `v0.1.1` source tag, commit `24ec47b`), `lxmf-1.2.0` and `rnspure-1.5.7` (Reticulum with its own built-in cryptography, so nothing needs compiling or downloading), the installers `rnfs-install.sh` (macOS, Linux) and `rnfs-install.bat` (Windows), the licences, and a `README.md` the Tools page shows as the rnfs section |
| **Link spotter** | Recognises `rnfs:<cid>`, `rnfs:name/<32 hex>/…` and bare CIDs in room lines, from the web and from LoRa. Each is collected on `/p/rnfs/`, and for a CID the room gets a short "fetch with `rnfs get …`" hint — at most once every 10 minutes per room (`RNFS_HINTS = False` turns the hint off) |
| **`!shelf`** | The place's own index: `!shelf` (the count and the last 5), `!shelf 3`, `!shelf add <cid> <title>`, `!shelf find <word>`, `!shelf rm <n>` (whoever added it) |
| **`!rnfs`** | What rnfs is, and where to get it |
| **Page** | `/p/rnfs/` (a card on the home page): the shelf and the links seen in chat, each with its `rnfs get` command; `/p/rnfs/shelf.json` as data |

The visitor downloads the three wheels and the installer for their
system into one folder and runs it: it makes its own environment
(`~/.rnfs-venv`) and installs with `--no-index --no-deps`, so nothing
comes from the internet. Tested here: install, `rnfs daemon`,
`rnfs status`, `rnfs add`. rnspure is slower than Reticulum with the
`cryptography` package (`rnfs status` says `slow: True`); a later
`pip install rns` in the same environment switches over.

The tools are about 1 MB together, so the provisioner's USB copy takes
roughly 17 minutes the first time (unchanged files are skipped after).
The project's one-file Windows `.exe` and Debian `.deb`
([releases](https://github.com/afit21/rnfs/releases), tens of MB) can
go on the SD card's `tools/` folder with a card reader.

Licences: rnfs is MIT (Afi Hogan); Reticulum (rnspure) and LXMF are
under the Reticulum License (Mark Qvist), which allows redistribution
with its notice — both are in `tools/`.

Data: `/sd/plugins/rnfs/shelf.json`.

Later: a LAN "seeder" (a Raspberry Pi running `rnfs daemon`) that
fetches a shelved CID and puts the file on the Stump's file shelf.

## Install

```
python3 provisioner.py --install-pack PORT packs/rnfs-pack
```

## Credits

**rnfs** is written by **Afi Hogan** ([afit21](https://github.com/afit21)),
MIT licence: https://github.com/afit21/rnfs. The wheel in `tools/` is
built unchanged from its `v0.1.1` source tag. **Reticulum** (as rnspure)
and **LXMF** are by **Mark Qvist** (Reticulum License,
https://reticulum.network). This pack only hands them out and speaks
rnfs's link format; the `/p/rnfs/` page, the `!rnfs` reply and the
Tools page section all credit them, in French, English and Spanish.
