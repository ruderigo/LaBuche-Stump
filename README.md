# LaBuche-Stump
# Project Stump — Beta A

<img width="270" height="585" alt="1000017845" src="https://github.com/user-attachments/assets/bfe1289f-72d3-4bce-94d0-8e5f64d7cddb" />
<img width="270" height="585" alt="1000017847" src="https://github.com/user-attachments/assets/53f4e19f-04e2-49f0-9c16-b7617cbf7c31" />
<img width="270" height="585" alt="1000017846" src="https://github.com/user-attachments/assets/2dc16a46-869c-4479-9e52-7b11f4fac354" />


An off-grid community node. A long-range encrypted mesh radio and a
local high-bandwidth server, deliberately kept on separate hardware.

```
[ Mesh ] <--( LoRa )--> [ Heltec V3 ] <--( WiFi/TCP :7633 )--> [ ESP32-S3-CAM ] <--( WiFi )--> [ Local room ]
                        CONTROL PLANE                          DATA PLANE
                        RNS identity, LXMF,                    SD storage, chat, files,
                        routing. Low power.                    billboard, captive portal.
```

Walk up with a phone, join the WiFi, and you get a chat room, a bulletin
board, and a file library. Meanwhile the node holds a cryptographic
identity on the Reticulum mesh, and people kilometres away over LoRa can
talk in the same rooms as the people standing in front of it.

---

## Contents

- [Why two boards](#why-two-boards)
- [INSTALL](#INSTALL)
- [Reading the boot log](#reading-the-boot-log)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [Architecture](#architecture)
- [HTTP API](#http-api)
- [Module reference](#module-reference)
- [Plugins](#plugins)
- [Configuration](#configuration)
- [Conventions for contributors](#conventions-for-contributors)
- [Testing](#testing)
- [Verification status](#verification-status)
- [Credits](#credits)

---

## Why two boards

The split is the point, not an accident of hardware availability.

**Fault isolation.** Everything memory-hungry and unpredictable — file
uploads, web traffic, SD writes — lives on the CAM. If it crashes, OOMs,
or its card corrupts, the Heltec keeps running and the node stays
reachable on the mesh with its identity intact.

**Reach containment.** Heavy media stays in the room, bounded by WiFi
range. Only small authenticated messages cross the LoRa mesh.

**Air-gapped administration.** No setting is changeable over WiFi or
LoRa. The web interface only ever touches *content* — posts, messages,
files. Configuration requires a physical serial connection.

**No wire between the boards.** The Heltec runs stock RNode firmware in
WiFi Station mode, so the CAM reaches it over TCP.

---

## INSTALL

```bash
python3 provisioner.py
```

Handles both boards: flashing, upload, guided configuration, plugin
setup, and diagnostics.

```bash
python3 provisioner.py --check-tools      # verify esptool/mpremote/rnodeconf
python3 provisioner.py --scan             # list connected boards
python3 provisioner.py --upload-app PORT  # (re-)upload firmware only
python3 provisioner.py --get-ip PORT      # query LAN + hotspot addresses
python3 provisioner.py --diag PORT        # serial, SD, radio, bridge tests
python3 provisioner.py --wipe-sd PORT     # reformat a problem SD card
```

The Heltec needs its WiFi mode set once (the wizard does this too):

```bash
rnodeconf <port> --autoinstall
rnodeconf <port> -w STATION --ssid "..." --psk "..." \
          --ip 192.168.0.222 --nm 255.255.255.0
```

Then join the node's WiFi — `LaBuche-192.168.4.1` — and open any plain
`http://` address.

### Telling the two boards apart

| Board | USB | Port name | USB ID |
|---|---|---|---|
| Freenove ESP32-S3-CAM | native | `usbmodem…` / `ttyACM…` | `303a:1001` |
| Heltec V3 | CP2102 bridge | `usbserial…` / `ttyUSB…` | `10c4:ea60` |

Both IDs are generic parts shared with other hardware, so the Provisioner
treats identification as a hint and always asks you to confirm.

**The Freenove has two USB-C sockets** and only one is wired to the
chip's data lines. The other gives power and a lit LED but enumerates
nothing — indistinguishable from a dead board until you try the other
socket.

---

## Reading the boot log

Connect with `mpremote connect <port>` and reset. A healthy boot prints,
in order:

```
Project Stump -- Beta A
[ap] 'LaBuche-192.168.4.1' up on 192.168.4.1 (active=True)
[fserv] SD mounted at /sd
[fservbot] active: prefix '!', 7 trigger(s), notice off
[plugin] active: fservbot
[web] serving on port 80
[dns] captive portal answering on port 53 -> 192.168.4.1
LXMF address: <hash>
Announced as: <NODE_NAME>
```

Every line is printed **after** the thing it describes succeeded. If a
line is missing, that component failed — and the failure prints its own
reason. An earlier version announced "DNS running" before even attempting
to bind, so the log confirmed a feature that had never once worked.

**A persistent startup failure now stops rather than loops.** After three
attempts `main.py` gives up and leaves the error on screen. A board
showing `no module named 'rrc'` is fixable in seconds; a board silently
rebooting every ten seconds is not.

---

## Troubleshooting

**Board won't flash — "No serial data received".**
Try the other USB-C socket first (see above). If that isn't it, enter the
ROM bootloader by hand: hold BOOT, tap RESET, release BOOT. Either way
the board may come back on a **different port name** — normal on native
USB; the Provisioner re-scans for it.

**"3 files don't match the current known-good version".**
Wrong build folder, not corrupted files. The Provisioner refuses
wrong-version folders outright and names the version it found. **Never
continue past this** — a mixed install produced exactly the boot loop
described below.

**Site can't be reached / connection refused.**
Nothing is listening on port 80. Usually a boot loop from a mixed
install: Beta A's `barkeep.py` imports `rrc`, so an older folder's file
set (no `rrc.py`) fails at import, cascades, and resets. Check the serial
console — the failure now names itself.

**AP doesn't appear, or has no sign-in prompt.**
Check for `[ap]` and `[dns]`. `[dns] FAILED` means no captive-portal
prompt but the node is otherwise fine — browse to the AP address
directly.

**Node is extremely slow or unresponsive.**
Almost always something blocking the event loop. The usual cause is an
unreachable Heltec: `wifi_serial.py` does a blocking connect. Confirm the
Heltec's IP matches `target_host` in `config.py` and that it's powered
and in STATION mode (`rnodeconf <port> --info`).

**Chat messages appear twice.**
Fixed in Beta A — a client-side race between the send-triggered poll and
the interval poll. If you see it again, the guard is in `rrc_ui.py`.

---

## Repository layout

```
final_firmware/              59 files hashed, 57 uploaded (~880 KB)
├── main.py                  boot entry; retries transient failures, stops on persistent ones
├── example_node.py          the real boot sequence; wires everything together
├── config.py                ALL configuration lives here
├── barkeep.py               HTTP server (the only one) + chat console + router
├── rrc.py                   RRC chat engine — rooms, nicks, history, DMs
├── rrc_ui.py                RRC web client (mIRC-style)
├── rrc_mesh.py              RRC ↔ LXMF bridge; mesh peers join the same rooms
├── billboard.py             bulletin board storage + rendering
├── fserv.py                 file storage, streaming I/O, credit economy
├── captive_portal.py        AP bring-up + DNS redirect
├── lora_boards.py           LoRa pinout presets
├── fservbot/                PLUGIN — channel bot (see Plugins)
├── lib/                     native crypto accelerators (~150x faster signing)
├── peripherals/             ADC / battery reading
└── urns/                    µReticulum: identity, LXMF, crypto, interfaces
    └── interfaces/
        └── wifi_serial.py   the Heltec bridge (project-specific)

provisioner.py               technician deployment tool (runs on a laptop)
```

`README.md` and `plugin.json` files are hashed for integrity but not
uploaded — they're host-side documentation with no business on a
constrained board.

---

## Architecture

### Boot sequence (`main.py` → `example_node.py`)

1. Join upstream WiFi — **optional**; failure is non-fatal
2. Bring up the node's own AP
3. Mount SD; migrate any flash-stored billboard onto it
4. Activate plugins (`load_plugins()`)
5. Start Reticulum + LXMF, register delivery identity
6. Start the HTTP server (port 80) and captive-portal DNS (port 53)
7. Start the mesh bridge; announce, then re-announce every 120s

Individual failures are isolated: no upstream WiFi, no SD card, no
reachable Heltec, a failed DNS bind, or a broken plugin each degrade one
feature rather than taking down the node.

### The single most important structural fact

**This is a cooperative, single-threaded event loop.** Any blocking call
anywhere freezes *everything* — web server, DNS, and Reticulum together.
A 5-second blocking `connect()` to an absent Heltec once made the node
appear permanently hung. When adding code that touches the network,
sockets, or sleeps, the question is always "does this yield?"

### One server, many modules

**`barkeep.py` owns port 80. Nothing else binds a TCP port.**

`billboard.py`, `fserv.py`, `rrc.py` are pure logic — storage, rendering,
state — called directly by barkeep's router. Earlier versions had three
near-duplicate HTTP handlers, only one reachable, and they caused three
separate divergence bugs before being removed. If you find yourself
adding `asyncio.start_server` to a module, stop.

### Room traffic is radio traffic

Since `rrc_mesh` forwards room messages to subscribed mesh peers,
**anything posted to a room costs LoRa airtime**. This changes the
calculus for anything that posts automatically — bots, notices,
auto-replies. Private messages (`/msg`) deliberately bypass rooms and go
only to their recipient.

### Import graph (no cycles)

```
example_node → captive_portal, barkeep, fserv, billboard, rrc_mesh, config
barkeep      → billboard, fserv, rrc, rrc_ui
rrc_mesh     → rrc
fserv        → billboard          (for _extract_ip)
rrc_ui       → rrc
billboard    → (nothing)
```

`billboard.py` is the leaf, which is why shared helpers like
`_extract_ip` live there. It must **not** import `fserv` — that would
close a cycle, and is why `billboard._sd_available()` stats the
mountpoint directly.

---

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | BarKeep console (also the captive-portal landing page) |
| POST | `/chat` | Send a BarKeep command; body is raw text |
| GET | `/billboard` | Bulletin board page |
| POST | `/post` | Add a notice; body `entry=<urlencoded>` |
| GET | `/rrc` | RRC chat client |
| GET | `/rrc/poll?room=&since=` | New room messages **and** private messages (JSON) |
| POST | `/rrc/send` | Send a chat line or `/command`; body is raw text |
| POST | `/upload` | Upload a file; `X-Filename` header, raw body |
| GET | `/download?f=` | Download a file (streamed, with real filename) |

Any unmatched path returns the BarKeep page with `200` — captive-portal
detection depends on probe requests getting a real HTTP response.
Malformed request lines, bad `Content-Length`, and header floods get a
real `400`; oversized chat bodies get `413` rather than silent
truncation.

### Limits

| Limit | Value | Where |
|---|---|---|
| Max headers | 40 | `barkeep.MAX_HEADERS` |
| Max small body (chat/post) | 8 KB | `barkeep.MAX_SMALL_BODY` |
| Upload write batch | 16 KB | `fserv.stream_to_file` |
| Download chunk | 16 KB | `barkeep._send_file` |
| File size | SD space | streamed, never buffered whole |

---

## Module reference

### `rrc.py` — chat engine

Memory-only. Nothing written to SD or flash: chat is the
highest-write-rate content on the node, and a conversation that
disappears on reboot is easier to reason about privacy-wise than one
silently accumulating on a card someone might walk off with.

| Bound | Value |
|---|---|
| `MAX_ROOMS` | 16 |
| `MAX_MESSAGES_PER_ROOM` | 60 |
| `MAX_MESSAGE_LEN` | 400 |
| `MAX_NICK_LEN` | 16 |
| `MAX_USERS` | 40 |
| `MAX_DMS_PER_USER` | 30 |
| `USER_TIMEOUT` | 300s |

Every bound drops oldest data rather than rejecting new input — a room
that stops accepting messages when full is broken; one that forgets old
lines is just scrollback.

Commands: `/nick /join /part /rooms /names /topic /msg /me /clear /help`

**Private messages** (`/msg <who> <text>`) are kept per recipient, never
posted to a room, and delivered on the same poll. They share the message
id sequence so the client renders them chronologically alongside room
traffic. Nicks resolve across transports — a web user can `/msg` someone
on the mesh and it goes out over LXMF as `[private] <nick> …` to that
peer only.

Client identity is the peer IP (or LXMF hash for mesh users). Fine for
per-device chat state; **not** a security boundary. The server's record
of which room a client is in is authoritative — client-supplied room
headers are ignored.

### `rrc_mesh.py` — RRC ↔ LXMF bridge

Mesh peers appear in rooms as named users alongside the walk-up crowd.
Inbound LXMF is parsed for an optional `#room` prefix; slash commands
are handled in the bridge because they need the LXMF reply path.

Outbound is opt-in per peer, capped at `POLL_LIMIT = 10` per cycle so a
busy room can't flood a LoRa link. Peer state bounded at
`MAX_MESH_PEERS = 20`.

`MESH_GREETING` in `config.py` is sent **once per peer on first contact**
— not per message. Every LXMF send costs airtime and roughly seven
seconds of crypto on the S3.

### `fserv.py` — files and credits

Storage under `/sd/shared/`, ledger at `/sd/fserv_ledger.json`.

- `stream_to_file()` — socket → disk in chunks, batched writes. Deletes
  partial files if the connection drops, so a truncated upload can't sit
  in the library looking legitimate.
- `credit_cost(filename)` — returns 0 when the economy is off. Use this,
  never `CLASS_WEIGHTS` directly.

Free mode (`CREDITS_ENABLED = False`) removes the economy entirely: no
cost labels, no `balance` command, no ledger writes.

### `billboard.py` — notices

Prefers the SD card (`/sd/billboard.txt`), falls back to internal flash
with no card, and migrates flash → SD on first boot with one. SD is
preferred because flash has a limited erase/write budget and the
billboard is the most frequently rewritten file on the node.

Entries: 200 chars, newest 50 shown, each tagged with a short
deterministic per-poster signature — enough to tell a repeat poster from
a stranger without publishing anyone's address.

### `captive_portal.py` — sign-in prompt

`setup_ap()` brings up the AP; `run_dns_server()` answers every DNS query
with the node's own IP. **Both are required.**

SSID is `<node-name>-192.168.4.1`. The LAN address is deliberately not
included — it only helps people already on the upstream network, who can
be told it directly, and carrying it consumed nearly the whole
32-character field.

---

## Plugins

A plugin is a folder containing `install.py` (with `activate()`) and
`plugin.json`. Installing one is:

1. Drop the folder into `final_firmware/`
2. Re-run `python3 provisioner.py`

No edit to any Stump file, for the second plugin or the twentieth.

**Firmware side:** `load_plugins()` in `example_node.py` discovers and
activates every plugin folder present. A plugin that fails to import or
activate is logged and skipped — an add-on must never be able to stop
the node booting.

**Provisioner side:** `discover_plugins()` reads every `plugin.json`,
shows what it found along with the plugin's own declared claims
(`MODIFIES CORE FILES`, `binds a port`, `starts a background task`,
version mismatch), then runs the prompts it declares. Settings marked
`"advanced": true` are listed with their defaults and asked only on
request, so installing one thing isn't six questions.

Plugin settings are written into `config.py`, updated in place on re-run
rather than duplicated.

### Writing one

`plugin.json` declares files, config keys, wizard prompts, commands, and
a `config_block` of paste-ready `config.py` lines. In your code, read
settings the way the rest of the tree does — as an override, not a hard
dependency:

```python
try:
    from config import MYPLUGIN_SETTING
except ImportError:
    MYPLUGIN_SETTING = "default"
```

A node whose `config.py` predates your plugin then still boots.

### Bundled: `fservbot`

Channel bot for RRC — answers set phrases (`!rules`, `!files`), with
dialogues editable live from chat by an operator holding
`FSERVBOT_OP_PASSWORD`. Reuses `BOT_NAME`, so it answers to whatever the
greeter is called. Wraps `rrc.handle_input` at runtime rather than
requiring an edit to `barkeep.py` — which keeps hashed core files
byte-identical, at the cost of the wrap being invisible to someone
reading `rrc.py`.

---

## Configuration

Everything is in `config.py`. The Provisioner writes it; to edit by hand:

```python
WIFI_SSID = "network to join"     # for LAN/internet reachability
WIFI_PASS = "password"
NODE_NAME = "display name"         # mesh identity AND the AP name
BOT_NAME  = "BarKeep"              # the local greeter, cosmetic

MESH_GREETING = ""                 # sent once per mesh peer, blank = off
MESH_GREETING_MAX = 200

CREDITS_ENABLED = True             # False = free mode, credit UI disappears
CREDIT_WEIGHTS = {"video": 3, "music": 2, "document": 1, "other": 1}
```

And the bridge target:

```python
{
    "type": "WiFiSerialInterface",
    "name": "Heltec Bridge",
    "enabled": True,
    "target_host": "192.168.0.222",   # static IP set via rnodeconf
    "target_port": 7633,               # fixed by Reticulum's protocol
},
```

**Do not edit the disabled `"TCP Client"` block above it** — an unrelated
placeholder that happens to share key names. Tooling that rewrites config
is context-aware for exactly this reason.

---

## Conventions for contributors

Every rule here comes from a bug that actually shipped.

**Test against real MicroPython, not CPython.** `apt-get install
micropython`. Confirmed absent on this build: `os.path`,
`str.isalnum()`, dict unpacking in literals (`{**a, **b}`), the `stat`
and `types` modules.

**Sockets need `getaddrinfo` — for `bind()` as well as `connect()`.**
A raw `(host, port)` tuple raises `TypeError: object with buffer protocol
required`. This one silently disabled the captive portal for the entire
life of the project.

```python
addr = socket.getaddrinfo(host, port)[0][-1]
s.bind(addr)      # or s.connect(addr)
```

**Nothing may block the event loop.** Use `asyncio.sleep`, never
`time.sleep`, in anything reachable from a task. Blocking socket ops need
short timeouts and backoff.

**Report status from where the work happens.** `create_task()` only
schedules — a `try/except` around it catches nothing, and any success
message printed there is a claim about something that hasn't happened.

**`get_extra_info("peername")` returns a raw `sockaddr_in` bytearray**,
not a tuple. Use `billboard._extract_ip()`.

**Never concatenate `str + bytes`.** Hard `TypeError`.

**Never buffer a whole file.** Stream in chunks, both directions.

**Escape at the point of use**, not by relying on an allowlist enforced
in another module. Inside `<script>` blocks JSON escaping alone is
insufficient — `</script>` closes the element during HTML parsing before
JS is evaluated. Use `rrc_ui._js()`.

**Don't add a second HTTP server.**

**Remember room posts cost airtime.** Anything that posts automatically
needs a rate limit.

**Update the manifest when adding files**, or they will never reach the
board. Use `manifest_candidates()` rather than a bare `rglob` — a blind
glob once swept in `boot_fail_count`, a file the node *writes at
runtime*, and listed it as canonical firmware:

```bash
python3 -c "
import hashlib, sys; sys.path.insert(0,'.')
from pathlib import Path
import provisioner
fw = Path('final_firmware')
for f in provisioner.manifest_candidates(fw):
    print('    %r: %r,' % (f, hashlib.sha256((fw/f).read_bytes()).hexdigest()[:16]))"
```

`config.py` is exempt from the staleness check — it's supposed to differ
per node.

---

## Testing

Syntax and import chain, per module:

```bash
cd final_firmware
micropython -c "import barkeep"
```

Run the server locally with real sockets:

```python
import sys; sys.path.insert(0, 'final_firmware')
import fserv, barkeep, uasyncio as asyncio
fserv.sd_ok = True                        # no real SD in a sandbox
async def main():
    await barkeep.run_barkeep_server(port=8080)
    while True: await asyncio.sleep(1)
asyncio.run(main())
```

Then exercise it with `curl`. The Unix port has real `usocket`,
`_thread`, and `uasyncio`, so HTTP behaviour is genuinely tested. It has
no `machine.ADC`, `machine.SDCard`, `machine.reset`, or `network` — those
need hardware.

**Simulate a headless boot** by stubbing `network` and `machine`. This is
how the crash in `serial_input_loop` was found: it polls `sys.stdin`,
which is invalid on a deployed node with no terminal attached.

**Multi-party behaviour can't be tested over loopback** — every client
gets the same IP and so is the same RRC user. Test at the engine level
with distinct client ids instead.

---

## Verification status

Being precise, because "it compiles" and "it works" are different claims.

**Hardware-proven.** The LoRa bridge, both directions. A separate device
running its own reference RNS stack received a live LXMF announce from
this node; an inbound announce passed full Ed25519 signature validation,
which fails on any single corrupted bit anywhere in the chain.

**Source-verified.** `wifi_serial.py`'s KISS layer — all 13 constants,
the escape function, and frame construction checked byte-for-byte
against upstream `RNS.Interfaces.RNodeInterface`.

**Tested against real MicroPython** (real sockets, real threading, real
HTTP): RRC multi-user chat, room isolation, nick collisions, private
message delivery and privacy, every command path, all bounds under
flood; the mesh bridge in both directions with stubbed LXMF objects;
billboard posting and SD migration; multi-megabyte file round-trips at
zero net memory delta; credit accounting in both modes; path-traversal
rejection; malformed request handling; captive-portal DNS answering a
real `captive.apple.com` probe; plugin discovery, activation, and
isolation of broken plugins.

**Needs hardware.** Reticulum/LXMF in live operation, real SD card
mounting, the bridge under sustained traffic, and fservbot's effect on
memory with every subsystem live.

**Known limits.**
- Credit identity is IP-based; phones randomize MACs. Soft reputation
  signal, deliberately not a security boundary.
- Captive-portal popup isn't guaranteed on every device.
- Chat does not survive reboot. By design.
- `fservbot` has **no per-trigger rate limit**. Since room posts now
  reach the radio, a trigger-happy channel costs airtime. Outstanding.
- `wifi_serial.py` wraps `sendall` in a 2s blocking timeout — a
  hardware-validated workaround for an ESP32-S3 lwIP bug. Sends return
  immediately on a healthy socket, but a stalled peer can still block
  the loop. Left as-is deliberately: it is the most-verified code in the
  project.

---

## Credits

Built on [µReticulum](https://github.com/varna9000/micropython-reticulum)
(MIT), a MicroPython port of
[Reticulum](https://github.com/markqvist/Reticulum). The Heltec runs
[RNode Firmware CE](https://github.com/liberatedsystems/RNode_Firmware_CE).

