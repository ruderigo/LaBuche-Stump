# Tests

```
sh scripts/build_micropython.sh ~/micropython-f32       # once, ~1 minute
MICROPYTHON=~/micropython-f32/micropython python3 tests/run.py
```

`build_micropython.sh` builds the MicroPython Unix port at a pinned
commit with **single-precision floats**, like the ESP32-S3: code that
round-trips an exact value through a float passes with doubles and fails
on the board. Each firmware test runs in a fresh copy of
`final_firmware/`, so nothing is written to the source tree.

| Test | |
|---|---|
| `chat_golden`, `access_golden`, `mesh_golden` | Fixed scripts of chat lines (web, access modes and tiers with a real Ed25519 `/auth`, the LoRa bridge); the replies must match `expected/`. These were checked line by line against the build before the plugin API |
| `plugin_api`, `plugin_web` | Every hook, budget and failure rule, with the test plugins in `plugins/` |
| `safe_mode` | Boots after a crash while starting, and while running |
| `fservbot_storage` | Data moved to `/sd/plugins/`, the timer notice |
| `packs_chat`, `packs_web` | The tavern and rnfs packs, chat and pages |
| `relay_fastpath` | The relay fast path forwards exactly what the general path does: every packet type, context, direction and hop count |
| `chain` | Three Stumps chained on loopback addresses (root, middle, end) plus an intruder with the wrong chain secret: the end Stump sends a file to the root through the middle one, which relays it; the intruder is kept out |
| `chain_config` | Chain settings, interface configs, `/chain.json`, the `/admin` section |
| `chain_failover` | A CAM whose Stump above is gone, over a simulated Wi-Fi scan: joins the strongest Stump of its chain, never one below itself, another chain, or one cut off; the path that keeps circles out |
| `mesh_room`, `radio_status` | `#lora-mesh` as the radio's room; the radio's status (`/hw.json`, the status dot), `/admin`'s radio card, the chat's radio settings, Bluetooth and USB ⇄ Bluetooth permissions, turning a computer Stump off |
| `librarian` | BarKeep the librarian in three languages, the firefly chat, sort and filter |
| `pc_rnode_serial` | CPython, a simulated RNode on a pseudo-terminal: handshake, radio set-up, packets, unplug and replug (also under another name), a freeze, retry, Bluetooth commands and the pairing code, radio off on close |
| `pc_rnode_ble` | CPython, a stand-in for bleak behaving like the RNode firmware: writes with a reply, the link dropping and coming back, silent after pairing |
| `pc_radio_link` | The USB ⇄ Bluetooth switch live, *Pair this computer* (the code over USB), the choice kept for the next start |
| `pc_lora_radio` | `#lora-mesh` lines to another Stump go on the radio only (one packet, plain header), `#wifi-mesh` on the chain only; a Stump recognised by its room-titled batch; introductions over the radio |
| `pc_chain`, `pc_joins_cam`, `pc_two_stumps`, `pc_parent_moves` | Computer Stumps: a folder made by the Provisioner joined by a CAM; the chain checks; two computer Stumps talking in `#wifi-mesh` and `#lora-mesh`; following a parent whose address moves |
| `pc_radio_only` | Two computer Stumps with no Wi-Fi between them, their simulated Heltecs joined over a simulated air: they meet over the radio and share `#lora-mesh` |
| `pc_chain_radio` | The LaBuche + laptop setup: two computer Stumps in a chain, each with a simulated Heltec over a simulated air; the second writes in `#lora-mesh` first and it reaches the first, over the radio, both ways |
| `pc_power` | `/admin` → *Turn off this Stump* under the real supervisor: a clean exit that nothing restarts; the Start/Stop files |
| `json_api`, `latin1_names` | JSON answers; accented, Latin-1 and emoji file names |
| `config.py` | The fixed configuration every firmware test runs with (not the repository's own `final_firmware/config.py`, which is a real node's) |
| `provisioner` | CPython: the manifest is current, plugins and packs verify, a pack install and an upload against a simulated board |

Snapshot tests compare normalised output (random nonces and signatures,
clock values and log lines removed). After an intended change:
`python3 tests/run.py --update`, then read the diff of `tests/expected/`
before committing it.

**Changed a firmware file?** The Provisioner checks every file against
its manifest, so rebuild it (and re-hash a plugin you changed):

```
python3 provisioner.py --hash-plugin final_firmware/stumpid     # if you changed a plugin
python3 scripts/update_manifest.py
```
