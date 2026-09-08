# Project Stump — Beta A (Release)

# https://labuche-stump.web.app/

An off-grid community node. A long-range encrypted mesh radio and a
local high-bandwidth server, deliberately kept on separate hardware.

```
[ Mesh ] <--( LoRa )--> [ Heltec V3 ] <--( WiFi/TCP :7633 )--> [ ESP32-S3-CAM ] <--( WiFi )--> [ Local room ]
                        CONTROL PLANE                          DATA PLANE
                        RNS identity, LXMF,                    SD storage, chat, files,
                        routing. Low power.                    billboard, about page, captive portal.
```

Walk up with a phone, join the WiFi, and get a chat room, a bulletin
board, a file library, and an About page explaining the project — in
French, English, or Spanish. Meanwhile the node holds a cryptographic
identity on the Reticulum mesh, so people reachable only over LoRa can
share rooms with the people standing in front of it.

---

## Before anything else: access modes are in test

This build ships **three** node-wide access profiles — `open`,
`hybrid`, `mandatory` — plus a separate, newer per-room access system
(minted/invite-only rooms) and a mesh-landing-room feature layered on
top of both. All of this is real, implemented, and passes its own test
suite. **None of it has been field-validated at the scale or duration
this release is going out to.**

**Recommendation for this release: leave `AUTH_MODE` at `open`.**
Everything else in this README about `hybrid`, `mandatory`, room tiers,
and the mesh landing room is accurate and instructions are provided —
use them if you're specifically testing that part of the system. But if
you just want a Stump that works the way every walk-up visitor expects
(anyone can read, anyone can chat, nothing asks for a password), `open`
is the tested, unsurprising choice, and it's the default.

One naming trap worth knowing before you touch any of this: **"hybrid"
means two unrelated things** — the global `AUTH_MODE` (a node-wide
security posture) and a per-room *tier* (a property one specific room
can have, regardless of global mode). A real field test already
confused these two. See [Access control](#access-control) below before
assuming you know what "turn on hybrid" does.

---

## Contents

- [Quick start](#quick-start)
- [Reading the boot log](#reading-the-boot-log)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [Architecture](#architecture)
- [HTTP API](#http-api)
- [Access control](#access-control)
- [Internationalization](#internationalization)
- [The About page](#the-about-page)
- [Plugins](#plugins)
- [Configuration](#configuration)
- [Conventions for contributors](#conventions-for-contributors)
- [Testing](#testing)
- [Verification status](#verification-status)
- [Credits](#credits)

---

## Quick start

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

Then join the node's WiFi. By default that's the literal network name
`LaBuche-Stump.web.app` — a real, publicly hosted page explaining what
the network is, so anyone can read it off their phone's WiFi list and
look it up on their own data before ever joining. The Provisioner
wizard also offers a custom name instead, with or without the AP's own
IP appended (see [Configuration](#configuration)).

---

## Reading the boot log

Connect with `mpremote connect <port>` and reset. A healthy boot prints,
in order:

```
Project Stump -- Beta A
[ap] 'LaBuche-Stump.web.app' up on 192.168.4.1 (active=True)
[fserv] SD mounted at /sd
Ed25519/X25519: fast IRAM crypto active
[fservbot] active: prefix '!', 7 trigger(s), notice off
[stumpid] active: mode=open
[plugin] active: fservbot, stumpid
[web] serving on port 80
[dns] captive portal answering on port 53 -> 192.168.4.1
LXMF address: <hash>
Announced as: <NODE_NAME>
```

Every line prints **after** the thing it describes succeeded. If a
line is missing, that component failed — and the failure prints its own
reason.

**The crypto line matters more than it looks.** Three possible outcomes:

- `Ed25519/X25519: fast IRAM crypto active` — healthy, ~17.6ms per
  signature verification.
- `SLOW crypto path active (...)` — a native module loaded, but not the
  fast one. Signing and verifying run roughly **80x slower**. Check that
  `lib/ed25519_iram.mpy` is present and matches this board's firmware
  build.
- `NO native crypto module loaded -- running pure Python` — slower
  again. Something is wrong with the upload or the board.

---

## Troubleshooting

**Board won't flash — "No serial data received".**
Try the other USB-C socket on the Freenove first — only one is wired to
the chip's data lines. If that isn't it, enter the ROM bootloader by
hand: hold BOOT, tap RESET, release BOOT.

**"Files don't match the current known-good version".**
Wrong build folder, not corrupted files. The Provisioner refuses
wrong-version folders outright.

**Site can't be reached / connection refused.**
Nothing is listening on port 80. A persistent startup failure now stops
and reports itself after three attempts rather than looping silently —
check the serial console.

**The LoRa bridge periodically drops and re-sends its full radio
config, and effective range shrinks.**
Diagnosed against a real field report. The node's own mesh
announcement (`REANNOUNCE_INTERVAL`, 120s by default) and any message
forwarded to a mesh peer are both fully synchronous — zero yield points
anywhere in that call chain — so each one freezes the *entire* event
loop, including the bridge's own keepalive, for however long the
signing takes. If that freeze outlasts the Heltec's connection
tolerance (observed empirically around 7 seconds of silence), the
bridge disconnects and reconnects, re-sending the complete radio setup.
Mitigated (not eliminated) by priming the bridge's keepalive immediately
before both known-expensive calls — see `prime_all_bridges()` in
`urns/interfaces/wifi_serial.py`.

**A room I tiered isn't gating anyone.**
`/rooms` annotates every non-open room with its tier in brackets. If a
room shows no tag, it's open.

**Mesh peers aren't landing where I told them to expect.**
Check `/admin <password> meshroom` with no argument — it reports the
current setting. If a mesh peer isn't verified and the landing room is
tiered `minted` or `hybrid`, they're redirected to `#main` instead, with
a system message explaining why. See [Access control](#access-control).

**Images on the About page's hardware gallery are broken.**
They're served from `/sd/about/`, not baked into the firmware — a
technician has to copy the actual photos there. See `/about/img` in
[HTTP API](#http-api).

---

## Repository layout

```
final_firmware/              71 files hashed, 63 uploaded (~1.3 MB)
├── main.py                  boot entry; retries transient failures, stops on persistent ones
├── example_node.py          the real boot sequence; wires everything together
├── config.py                ALL configuration lives here
├── node_common.py           shared identity/router bring-up, safe for other firmware to import
├── i18n.py                  trilingual (FR/EN/ES) string table + per-visitor language state
├── barkeep.py                HTTP server (the only one), chat console, page router
├── rrc.py                    RRC chat engine — rooms, nicks, history, DMs
├── rrc_ui.py                  RRC web client (mIRC-style)
├── rrc_mesh.py                 RRC ↔ LXMF bridge; mesh peers join rooms, land per stumpid's setting
├── billboard.py                 bulletin board storage + rendering
├── fserv.py                      file storage, streaming I/O, credit economy
├── captive_portal.py              AP bring-up + DNS redirect
├── flasher_ui.py                   browser-based board flasher (WebSerial)
├── lora_boards.py                   LoRa pinout presets
├── fservbot/                         PLUGIN — channel bot
├── stumpid/                           PLUGIN — identity verification + room + mesh access
├── tools_payload/                      host-side flasher assets (not uploaded to the board)
├── lib/                                 native crypto accelerators
├── peripherals/                          ADC / battery reading
└── urns/                                  µReticulum: identity, LXMF, crypto, interfaces

provisioner.py               technician deployment tool (runs on a laptop)
```

---

## Architecture

### Boot sequence

1. Join upstream WiFi — optional; failure is non-fatal
2. Bring up the node's own AP (`LaBuche-Stump.web.app` by default)
3. Mount SD; migrate any flash-stored billboard onto it
4. Activate plugins (`load_plugins()`)
5. Start Reticulum + LXMF, register delivery identity
6. Start the HTTP server (port 80) and captive-portal DNS (port 53)
7. Start the mesh bridge; announce, then re-announce every `REANNOUNCE_INTERVAL`

### The single most important structural fact

**This is a cooperative, single-threaded event loop.** Any blocking call
anywhere freezes *everything*. LXMF signing has zero yield points
anywhere in its call chain — confirmed directly — so any send or
announce blocks the whole node for however long that crypto takes. See
[Troubleshooting](#troubleshooting) for the field-diagnosed consequence.

### One server, many modules

**`barkeep.py` owns port 80. Nothing else binds a TCP port.**
`billboard.py`, `fserv.py`, `rrc.py` are pure logic, called directly by
barkeep's router.

### Room traffic is radio traffic

Since `rrc_mesh` forwards room messages to subscribed mesh peers,
**anything posted to a room costs LoRa airtime**. Both the per-room
tier system and the mesh landing room exist partly to keep an anonymous
walk-up visitor's chatter from consuming a mesh peer's airtime budget
by default.

### Import graph (no cycles)

```
example_node → captive_portal, barkeep, fserv, billboard, rrc_mesh, node_common, config
barkeep      → billboard, fserv, rrc, rrc_ui, flasher_ui, i18n
rrc_mesh     → rrc, stumpid.core (soft -- see below)
stumpid.core → rrc, i18n
rrc_ui       → rrc, i18n
billboard    → i18n            (for _extract_ip; must NOT import fserv -- would close a cycle)
```

`rrc_mesh`'s import of `stumpid.core` is wrapped in `try/except
ImportError` — the mesh bridge has no hard dependency on the identity
plugin being installed at all. If `stumpid` isn't present, mesh peers
land in `#main`, exactly as if no landing-room setting had ever been
configured.

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
| GET | `/files` | Browsable file listing |
| GET | `/about` | About page — what Stump/Fireflies are, how to connect, hardware gallery |
| GET | `/about/img?f=` | Serves a gallery image from `/sd/about/`, inline (no download prompt) |
| GET | `/tools` | Technician tools page |
| GET | `/tool?f=` | Download a tool file |
| GET | `/flash` | Browser-based board flasher (WebSerial) |
| GET | `/fw?f=` | Firmware image / catalog for the flasher |
| GET | `/lang?set=&next=` | Sets the requesting visitor's language, redirects back |

Any unmatched path returns the BarKeep page with `200` — captive-portal
detection depends on probe requests getting a real HTTP response.

---

## Access control

Two genuinely separate systems. Read this section before touching
either, since conflating them is exactly what confused a real field
test.

### 1. Global `AUTH_MODE` — a node-wide security posture

Set at provisioning time, or live via `/admin <password> mode <m>`:

- **`open`** (default, recommended for this release) — nothing is
  gated. Anyone can read, post, join, DM.
- **`hybrid`** — reading stays open; writing anything (posting,
  joining, DMing, `/topic` with an argument) requires a verified
  identity (`/auth`). A **plain message is always classified as a
  write**, unconditionally — a mesh peer's very first message will be
  challenged before a room's own tier ever gets consulted.
- **`mandatory`** — everything except `/auth`, `/whoami`, `/help`
  requires verification.

**Status: implemented, unit-tested, not field-hardened.** Use `hybrid`
or `mandatory` if you're specifically testing identity verification;
otherwise stay on `open`.

### 2. Per-room tiers — a property of one specific room

Independent of global mode, and it stays in effect **regardless of
which global mode is active** — a tiered room's own gate runs
unconditionally, even under global `open`, on purpose. Tiers:

- **open** (the default for any room that's never been touched)
- **minted** — only a verified identity may join. No exceptions.
- **hybrid** *(the other "hybrid" — see the warning at the top of this
  README)* — a verified identity joins freely; anyone else needs an
  invite from someone verified who is **already in that room**.

```
/admin <password> room <name> <open|minted|hybrid>   -- create or re-tier a room
/invite <nick>                                        -- invite someone into the room you're in
```

`/rooms` shows each non-open room's tier in brackets:
```
#main  (3 here)  -- General. Be decent.
#lounge  (2 here)  [hybrid]
```

**Status: implemented, tested against every combination with the
global mode above, not field-hardened.**

### 3. The mesh landing room

Where a mesh peer lands **automatically**, on first contact — not
something they request, since they never typed `/join` at all.

```
/admin <password> meshroom <name>   -- mesh peers now default here instead of #main
/admin <password> meshroom off      -- back to today's behaviour (#main)
/admin <password> meshroom          -- shows the current setting
```

Unset by default — nothing changes for a deployment that never touches
this. Runtime-only, like a live `mode` change: it reverts on reboot,
not written to `config.py`.

**Automatic placement is gated exactly like an explicit `/join` would
be**, on purpose. If the landing room is tiered and an unverified peer's
first message arrives, they're redirected to `#main` instead, with a
system message explaining why, translated into whatever language
they're set to (see [Internationalization](#internationalization)) —
mesh peers get the same courtesy a rejected web visitor already gets.
This holds identically under all three global modes: the redirect
doesn't get stricter or looser depending on `AUTH_MODE`, because the
room-tier gate this relies on was already mode-independent before the
mesh landing room existed.

**Status: implemented and tested across all three global modes in the
same test run, not yet exercised against a real mesh peer on real
hardware.**

---

## Internationalization

French (default), English, Spanish. A visitor's language is a
per-client preference (same identifier used for nick and verified
identity elsewhere), set via a toggle present on every page, and
persists across pages for that visitor's session — not written to disk,
resets on reboot, matching everything else session-scoped in this
project.

149 translation keys, covering the walk-up interface (BarKeep, RRC
chat and its commands, Billboard, Files, the About page) and stumpid's
identity/room-access messages. Command *words* (`/nick`, `menu`) stay
in English as a fixed vocabulary in every language — only the responses
translate.

**Never translated, deliberately:** stumpid's `AUTH-CHALLENGE` /
`AUTH-OK` / `AUTH-FAIL` lines are wire-protocol tokens a client parses
by splitting on the first space. Translating them would break that
integration.

---

## The About page

`/about` — three views in one page (About / How to Connect / Hardware
Gallery), switched client-side since they're facets of one page, not
separate destinations. Content is the PR/marketing team's own copy,
ported not rewritten, with corrected instructions matching this
build's actual default SSID and the AP's real address rather than a
placeholder.

The hardware gallery's two photos are **not** part of the firmware
upload — they live on the SD card at `/sd/about/`, copied there
separately (e.g. via `mpremote fs cp`). A missing photo renders as a
broken image, the same as any web page missing an asset; nothing
crashes.

---

## Plugins

A plugin is a folder containing `install.py` (with `activate()`) and
`plugin.json`. Installing one is: drop the folder in, re-run
`python3 provisioner.py`.

**`fservbot`** — channel bot for RRC, answering set phrases with
dialogues editable live by an operator.

**`stumpid`** — Ed25519 identity verification, per-room access control,
and the mesh landing room (above). Wraps `rrc.handle_input` at runtime,
same as `fservbot` — activates second (alphabetically), becoming the
outermost wrapper, required for it to gate a write before anything else
acts on it. `deactivate()` on either plugin refuses to unwind if it
isn't still the outermost layer, rather than corrupting the handler
chain.

`/auth` protocol: `/auth` requests a challenge, `/auth <pubkey_hex>
<sig_hex>` answers it. The signed message is the ASCII bytes of the
nonce's hex **string**, not the decoded bytes.

Admin password falls back to `fservbot`'s if `stumpid` has none of its
own configured, read live from `fservbot.core` — no hard dependency
between the two plugins in either direction.

---

## Configuration

Everything is in `config.py`. The Provisioner writes it.

```python
WIFI_SSID = "network to join"
WIFI_PASS = "password"
NODE_NAME = "ESP32s3"               # mesh identity display name

SSID_NAME = None                    # None = "LaBuche-Stump.web.app"; or a custom string
SSID_INCLUDE_IP = False             # only meaningful with a custom SSID_NAME -- see below

BOT_NAME  = "BarKeep"
MESH_GREETING = ""                  # sent once per mesh peer, blank = off

REANNOUNCE_INTERVAL = 120           # seconds between this node's own mesh announces
ANNOUNCE_RATE_MAX = 6               # max rebroadcasts/source/window when relaying for others
ANNOUNCE_RATE_WINDOW = 60

CREDITS_ENABLED = True
CREDIT_WEIGHTS = {"video": 3, "music": 2, "document": 1, "other": 1}
```

**`SSID_NAME` / `SSID_INCLUDE_IP` interaction is a hard constraint, not
a style choice.** WiFi SSIDs have a 32-byte protocol maximum.
`"LaBuche-Stump.web.app"` alone is 21 characters and fits with room to
spare; with the IP suffix it's 33 — one byte over, and truncating a
real web address by even one character breaks it as something a
browser can resolve. `SSID_INCLUDE_IP` defaults to `False` specifically
because `SSID_NAME` defaults to `None` — the two defaults have to stay
consistent with each other out of the box. The Provisioner wizard
enforces this by construction (the IP question is only ever asked in
the custom-name branch); this default is what protects an unprovisioned
board booted straight from the template.

And the bridge target:

```python
{
    "type": "WiFiSerialInterface",
    "name": "Heltec Bridge",
    "enabled": True,
    "target_host": "192.168.0.222",
    "target_port": 7633,
},
```

---

## Conventions for contributors

Every rule here comes from a bug that actually shipped.

**Test against real MicroPython, not CPython.** `apt-get install
micropython`. Confirmed absent: `os.path`, `str.isalnum()`, dict
unpacking in literals, `sendall` on this Unix port's socket (present on
real ESP32 hardware).

**Sockets need `getaddrinfo` — for `bind()` as well as `connect()`.**

**Nothing may block the event loop — and this includes crypto.**
A caller about to trigger a blocking LXMF send that could stall
something else's timing should prime that thing first — see
`prime_all_bridges()`.

**A room-tier or access check must run unconditionally**, not
conditioned on the node's global mode. Confirmed directly: skipping it
under global `open` left a "restricted" room with zero actual
restriction.

**Never assume a bare module-level name is already imported just
because a sibling file in the same package imports it.** `stumpid/
install.py` imports `rrc`; `stumpid/core.py` didn't, and two new
functions added late in this project referenced `rrc.X` anyway —
compiled fine, failed at the first real call. Caught by exercising the
function, not by reading it.

**Update the manifest when adding files**, or they won't reach the
board. Use `manifest_candidates()`, not a bare `rglob`.

`config.py` is exempt from the staleness check.

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
fserv.sd_ok = True
async def main():
    await barkeep.run_barkeep_server(port=8080)
    while True: await asyncio.sleep(1)
asyncio.run(main())
```

**Simulate a headless boot** by stubbing `network` and `machine`.

**Multi-party behaviour can't be tested over loopback** — every client
gets the same IP. Test at the engine level with distinct client ids.

---

## Verification status

**Hardware-proven.** The LoRa bridge, both directions, including a live
LXMF announce passing full Ed25519 signature validation on a separate
reference device.

**Tested against real MicroPython** (real sockets, real crypto, through
actual dispatch, not mocks): the full `/auth` cycle; every room-tier
scenario against every global `AUTH_MODE`, including the mesh landing
room redirect confirmed identical across all three modes in the same
test run; i18n table completeness and placeholder consistency across
all three languages; the browser flasher's catalog-driven design.

**Needs hardware.** Reticulum/LXMF in live operation with a real mesh
peer exercising the landing-room redirect; real SD card mounting for
the About page's gallery; which crypto backend (`iram`/`xip`/pure
Python) a given deployed board actually loads.

**Known limits.**
- All three `AUTH_MODE` values and both room-tier systems are
  implemented and tested but not field-hardened at release scale — see
  the notice at the top of this document.
- Re-tiering a room stricter doesn't eject existing members.
- Credit identity is IP-based; not a security boundary.
- Chat, room-tier policy, and the mesh landing room all reset on
  reboot, by design, for the identical reason rrc.py's own rooms do.
- `wifi_serial.py` wraps `sendall` in a 2s blocking timeout — a
  hardware-validated ESP32-S3 lwIP workaround, left untouched
  deliberately.

---

## Credits

Built on [µReticulum](https://github.com/varna9000/micropython-reticulum)
(MIT), a MicroPython port of
[Reticulum](https://github.com/markqvist/Reticulum). The Heltec runs
[RNode Firmware CE](https://github.com/liberatedsystems/RNode_Firmware_CE).
