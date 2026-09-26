# Building a Stump Client — Read This First

One page. Read it before writing any client code, especially before
you hit `AUTH-CHALLENGE` cold and start reverse-engineering source.

**Source and released builds**: https://github.com/ruderigo/LaBuche-Stump
— this is the reference point for what's actually current. If something
you're looking at (a forwarded zip, an older doc, a snippet someone
pasted into chat) disagrees with what's there, the repo wins.

---

## Transport priority: LoRa first, WiFi second

**Design for LoRa/Reticulum reachability as the default case.** It's
the whole reason this network exists — it works with no internet, no
cell service, no proximity to any specific hardware, over kilometers.
WiFi only works standing next to one specific Stump node's hotspot.

If your client only works well over WiFi and treats LoRa as an
afterthought, you've built it backwards. A user reachable *only* by
mesh should get the same identity, the same rooms, the same experience
as someone standing next to the node on WiFi — not a degraded one.

**The good news: the identity and room protocol below is identical over
both transports.** You write it once. `/auth`, room commands, tiers —
none of it cares whether the bytes arrived over LoRa/LXMF or WiFi/HTTP.
Get it right for one, you have it for both.

---

## Identity: what to do the moment you see `AUTH-CHALLENGE`

This is the thing that sends teams into "deep research" mode. It
shouldn't. Here's the complete recipe — verified end-to-end, both
directions, not just described:

### 1. Generate a real Reticulum-format identity

An identity here is **two keypairs concatenated**, not one:
- An **X25519** keypair (32-byte public key) — for encryption, unused
  in the auth handshake itself
- An **Ed25519** keypair (32-byte public key) — this is what actually
  signs and verifies

```
public_key  = X25519_public_bytes (32 bytes) + Ed25519_public_bytes (32 bytes)
            = 64 bytes total → 128 hex characters when sent on the wire

identity_hash = SHA-256(public_key)[first 16 bytes] → 32 hex characters
```

**This is the single most likely thing to get wrong.** If your
`pubkey_hex` is only 64 hex characters, you've sent an Ed25519 key
alone — the server expects both keys concatenated, and the identity
hash is computed over the *combined* 64 bytes, not just the signing
half.

**Strong recommendation: don't hand-roll this.** This is the standard
Reticulum Identity format, not a Stump invention. Use an existing
Reticulum-compatible identity library for your platform if one exists,
rather than reimplementing key generation and hashing by hand — a real
Kotlin Multiplatform port already exists covering Android and iOS
(`reticulum-mobile-app`), and it's worth checking before writing this
from scratch.

### 2. The handshake, three messages, over either transport

```
you:     /auth
server:  AUTH-CHALLENGE <32-char hex nonce>

you:     /auth <pubkey_hex> <sig_hex>
server:  AUTH-OK <32-char hex identity hash>
    or:  AUTH-FAIL <reason>
```

- `pubkey_hex` — your 128-char concatenated public key, from step 1
- `sig_hex` — sign **the ASCII bytes of the nonce's hex string** with
  your Ed25519 private key, then hex-encode the 64-byte signature (128
  hex chars)

**The second most likely thing to get wrong**: sign `nonce_hex.encode()`
— the literal text characters of the hex string the server sent you —
**not** the decoded bytes the hex represents. These produce different
signatures. If every `/auth` attempt fails with `AUTH-FAIL signature
does not match` and your key generation is otherwise correct, this is
almost certainly why.

- The nonce is single-use and expires in 60 seconds. Complete the
  handshake promptly; don't cache a challenge for later.
- `AUTH-CHALLENGE` / `AUTH-OK` / `AUTH-FAIL` are wire tokens, **never
  translated**, in any display language. Parse them by splitting on the
  first space, always.

### 3. What you get for it

Your identity hash becomes your durable name on the network — it
survives you reconnecting from a new session, a new IP, a new radio
path. (Without `/auth`, a WiFi/HTTP client is known only by its IP
address — see *Identity model* in the reference at the bottom.) Rooms can be tiered to require it (`minted`) or to admit anyone
with an invite from someone who has it (`hybrid`). None of that matters
until you've done the handshake above once.

---

## The two rooms that always exist

`#main` and `#lxmf` are both present the moment a node boots — nothing
to request, nothing to configure. If you're building a mesh-first
client, **`#lxmf` is where your users land by default**; expect it, and
don't be surprised it exists even on a freshly provisioned node you've
never touched. `#main` is the general room walk-up WiFi visitors share.

**`/part` depends on the transport.** Over WiFi/HTTP it always returns
you to `#main`. Over the mesh it returns you to wherever you actually
landed: `#lxmf` normally, or `#main` if a tiered `#lxmf` redirected you
there instead (see `COMMANDS.md` for the tier system). Don't hardcode
`#main` as the universal "go home" target in a mesh client — track the
room the peer actually landed in first, or read the server's reply.

---

## DMs: the one-sided delivery gotcha

`/msg <nick> <text>` sends a private message. Here's the detail that
will bite you if you're building a proper conversation-thread UI rather
than just firing messages blind:

**The server only ever delivers a DM to its recipient.** The sender
gets a plain text confirmation reply (not a structured message you can
render into a thread), and the sender's own outgoing text is *never*
handed back to them through polling — there is no "sent items" queue on
the server side.

If your client wants to show both sides of a conversation (which it
should — a DM view showing only what you *received* looks broken),
**echo your own sent message into your local thread view yourself, the
moment you send it**, rather than waiting for it to come back from the
server. It never will.

Group incoming DMs by sender locally, too — the server hands you a flat
list of new private messages on every poll (each tagged with who sent
it), not pre-organized threads. Building per-sender conversation views
is entirely a client-side concern.

**Mesh users can receive DMs before they've ever written to the node.**
Announcing is enough to be reachable by DM (you appear in web users'
"Message someone" list under your announced display name); room
traffic only starts once you send the node any message. Details under
*RRC over LXMF* in the reference below.

---

## Color and design palette

If your client is meant to feel visually connected to the rest of the
Stump ecosystem (the web console, RRC chat, the About page), this is
the actual palette in use — not illustrative, these are the live values
of the default theme, Amber. A node's technician can set Phosphor, OLED
or Paper site-wide instead (`THEME` in `config.py`); the full set of
palettes is in the firmware's `theme.py` if your client wants to match
a node exactly.

### Core colors

| Role | Hex | Use |
|---|---|---|
| Background | `#1b1512` | Page/app background — near-black, warm brown, never pure black |
| Panel | `#2a2119` | Cards, panels, input fields |
| Ember | `#d97a3a` | The one accent — buttons, active states, links, headings |
| Ember bright | `#f0a050` | Hover/focus state of the accent only — never used at rest |
| Text | `#ecdfc8` | Body text — warm off-white, never pure white |
| Muted | `#9c8d76` | Secondary text, subtitles, placeholders |
| Border | `#493c2e` | Every divider, every card outline |

### Extended shades (denser chat UI, and two deliberate breaks from the family)

| Hex | Use |
|---|---|
| `#221b15` | Sidebar / room-list background |
| `#2f271e` | Row hover, row dividers |
| `#7d715f` | Dim/system text — quieter than muted |
| `#c8b48f` | Action text (`/me`) |
| `#c8a2c8` / `#d8b4d8` | DM body / DM sender nick — a deliberate purple break from the ember family, so a private message is visually distinct from a room message at a glance |
| `#e07a5a` | Errors — the *only* red anywhere in the palette. If you introduce a second use for red, it stops meaning "something went wrong" |

### Typography

- **Serif body text**: `Georgia, 'Iowan Old Style', 'Palatino Linotype', serif`
- **Monospace everything else** (headings, the whole RRC interface, code):
  `ui-monospace, 'Cascadia Code', 'SF Mono', 'Courier New', monospace`
- System fonts only — nothing loaded over a network, since the whole
  point is this has to render with no internet behind it.

### Shape

- Border radius: `4px` (small controls), `6px` (buttons, tiles), `8px`
  (panels, cards) — pick based on element size, not arbitrarily
- One accent color used consistently for "this is interactive"; muted
  hues do everything else
- Background gets *darker* as UI density increases (page → panel →
  sidebar → row-hover) — the accent stays the one constant signal
  across all of it

---

## Minimum viable client checklist

1. Generate a Reticulum-format identity (or better, use an existing library for it)
2. Implement the three-message `/auth` handshake above
3. Support plain-text room posting and `/join <room>` — that's most of what a room actually is
4. Handle DMs as a client-side concern: group by sender, echo your own sent messages locally
5. Treat LoRa/LXMF and WiFi/HTTP as the same protocol over different pipes, not two different clients
6. Pick the node's LXMF address from its latest announce, not a saved value — a reflashed node can come back with a new one
7. Send text as UTF-8 (percent-encoded as UTF-8 bytes in form and URL fields) — that's what the server decodes

Once that's working, the full command reference (`COMMANDS.md`) covers
everything else — room tiers, invites, admin commands, the exact reply
text for every edge case. This page exists so you don't need it just to
get past the first handshake. The reference below covers the wire
formats: every endpoint, payload and limit a client touches.

---

# Reference for client builders

Everything the client teams have been given, in one place. Checked
against the current `barkeep.py`, `rrc.py`, `rrc_mesh.py`,
`billboard.py` and `fserv.py` source.

## Addresses

A node answers on two IPs with different guarantees:

- **`http://192.168.4.1/`** — its own WiFi hotspot. Always there, for
  any device that joins it directly. When in doubt, use this one.
- **Its LAN IP** — only if the node also joined a router. Reachable
  only from that router's network, and it can change whenever the node
  or the router reboots, unless the router reserves it.

Over LoRa there are no IPs: the node is its LXMF `lxmf.delivery`
address, taken from its announces.

## Features a node may not offer

A node's technician chooses which features it offers: chat, billboard,
file sharing, about. The addresses of one that's off answer
`404 Not Found` with a plain-text "not offered on this node", so treat
a 404 there as "this node doesn't do that", not as an error. With chat
off, the node also stops bridging chat over the mesh — LXMF messages to
it aren't answered — though its `stump.node` beacon still announces.

## Identity model (WiFi/HTTP)

There is no login, cookie or token. Without `/auth`, a WiFi client's
entire state — nickname, room, language, DM inbox, credit balance — is
keyed by **the source IP of the TCP connection**, read server-side on
every request. It's stable while your device keeps the same IP on the
node's network, and becomes a new, unrelated identity if the IP
changes. Build the WiFi client assuming "whoever is connecting from
this IP right now" is the whole session model; `/auth` (above) is what
gives a durable identity across IPs and transports.

## RRC over HTTP — chat, rooms, DMs

**`GET /rrc/poll?room=<room>&since=<id>`** — poll for updates. `room`
is where you think you are; the server's own idea wins and is echoed
back (if you were moved, you'll see it here, not as an error). `since`
is the highest message `id` you already have.

```json
{
 "room": "main",
 "nick": "guest-a1b2",
 "topic": "",
 "rooms": ["main", "lxmf"],
 "messages": [ {"id": 42, "ts": 1789700000.1, "nick": "alice", "body": "hey", "kind": "msg"} ],
 "dms": [ {"id": 43, "ts": 1789700001.2, "nick": "bob", "body": "psst", "kind": "dm"} ],
 "users": ["alice", "bob", "guest-a1b2"],
 "stumps": []
}
```

- `kind`: `msg`, `system` (join/part/nick notices; `nick` is `"*"`),
  `action` (`/me`), or `dm`.
- `messages` and `dms` share one id sequence — one `since` covers both.
- `users` is the people in your room **plus** mesh peers reachable by
  DM only (see RRC over LXMF) — i.e. everyone you can `/msg`.
- `stumps` lists which of those nicks are other Stump nodes (known from
  their `stump.node` beacons, below). The web chat shows them with a
  small "stump" tag; the nick itself is unchanged.
- Limits: 60 messages per room (oldest drop like scrollback), 16 rooms,
  400 characters per message.

**`POST /rrc/send`** — the body is the raw line, no field wrapping;
`Content-Length` required, 8 KB max (larger gets `413` with a JSON
explanation, never silent truncation). Text not starting with `/` posts
to your current room — the response body is empty, and you see your
line arrive on your next poll. A leading `/` is a command:

| Command | Effect |
|---|---|
| `/nick <name>` | Letters, digits and `` -_[]{}\^`| `` only; others become `-`; 16 chars max |
| `/join <room>` / `/j` | Creates the room if new (20 chars max, lowercased, non-alphanumerics become `-`) |
| `/part` | Back to `#main` over HTTP (to your landing room over the mesh) |
| `/rooms` | List rooms with occupancy |
| `/names` | Who's in your current room |
| `/topic [text]` | Show or set the room topic |
| `/msg <nick> <text>` / `/m` / `/w` | Direct message, delivered on the recipient's next poll, never posted to a room |
| `/me <text>` | Action message |
| `/help` | The server's help text, localized |

The response is always `{"replies": [...], "room": <new room or null>}`:
`replies` is feedback for the sender only (command output, errors);
`room` is non-null only when `/join` or `/part` actually moved you.

**Polling cadence** (match the reference client): 2 s base interval,
doubled on each failure up to 30 s, reset on the next success, plus up
to 30% random jitter on every interval so clients on the same WiFi
don't retry in lockstep.

## RRC over LXMF (mesh clients)

A mesh client talks to the same chat by LXMF messages to the node's
single `lxmf.delivery` address. Web users have no LXMF address; every
conversation goes through the node.

**Sending.** Message content uses the same line protocol as
`POST /rrc/send`: plain text posts to your room; `/msg <nick> <text>`
sends a DM; `/nick`, `/join`, `/part`, `/rooms`, `/names`, `/help` work
as above. Optionally prefix plain text with `#room ` to post into
another existing room without moving.

**Receiving.** The node sends LXMF messages back, one line per item:

```
<nick> text             room message
* nick text             /me action
* text                  system notice
[private] <nick> text   DM
```

Up to 10 room messages per 5-second cycle; your own lines are never
echoed back.

**Two presence levels.**

- **Announce only → reachable by DM.** Web users see you in "Message
  someone" under your announced display name, and DMs to you are
  delivered over LoRa. The first is preceded by a one-line hint on how
  to reply. No room traffic is sent to you.
- **Any message to the node → room participant.** You land in `#lxmf`
  (or `#main` if a tiered `#lxmf` redirects you), and room traffic is
  pushed to you as well.

Participation lasts `MESH_PEER_TIMEOUT` (default 300 s) after your last
message; DM reachability lasts `MESH_ANNOUNCE_TIMEOUT` (default 3600 s)
after your last announce, so announce more often than that. Moving
between the two never duplicates or drops a DM. Only LXMF delivery
announces count — node, propagation and other announces are ignored.

**Your nick** is your announced display name, cleaned to letters,
digits and `` -_[]{}\^`| ``, 16 chars max, with a numeric suffix if
it's taken. It stays the same from announce-only to participant.

**If the node is reflashed** it may come back with a new LXMF address.
Re-pick it from its latest announce rather than keeping a saved one.

## Telling Stumps from people

Every Stump (CAM) node announces a second destination on its own
identity, aspect **`stump.node`**, alongside its normal LXMF address.
Its announce data is a msgpack list:

```
["stump", <firmware version>, <node name>, <16-byte LXMF delivery hash>]
```

So any LXMF address that has a `stump.node` announce on the same
identity is a Stump; everything else is a person (or another app).
Stumps use this themselves: over HTTP, `GET /rrc/poll` already tells
you which chat nicks are Stumps (`stumps`), so a WiFi client doesn't
need to listen for beacons at all.
The LXMF announce itself is unchanged, so nothing else needs to change
in your client.

**Listening** (upstream Reticulum):

```python
import RNS, RNS.vendor.umsgpack as msgpack

class StumpBeacons:
    aspect_filter = "stump.node"
    def received_announce(self, destination_hash, announced_identity, app_data):
        kind, version, name, lxmf_hash = msgpack.unpackb(app_data)
        mark_as_stump(lxmf_hash, name, version)   # your code

RNS.Transport.register_announce_handler(StumpBeacons())
```

**Asking on demand**, for an LXMF peer you've just heard: compute the
beacon address from their identity and request a path. If an announce
comes back, it's a Stump.

```python
beacon = RNS.Destination.hash(identity, "stump", "node")
RNS.Transport.request_path(beacon)
```

Beacons go out at boot and every 30 minutes (`STUMP_ANNOUNCE_INTERVAL`),
so on first sight of a peer, asking is faster than waiting. Only CAM
nodes beacon: a standalone Heltec transport runs different firmware and
has no chat of its own.

## Billboard

**No JSON read endpoint yet** — a real gap. `GET /billboard` returns a
rendered HTML page. A post is a required **title** and an optional
**body**:

```html
<!-- with a body: collapsed, tap to expand -->
<li><details class='post'><summary>TITLE <small>&mdash; SIG</small></summary>
    <div class='post-body'>BODY</div></details></li>
<!-- without a body -->
<li>TITLE <small>&mdash; SIG</small></li>
```

Title, body and signature are HTML-escaped; the body keeps its line
breaks as literal newlines. Posts made before titles existed are
title-only rows. Scraping works as a stopgap; for real billboard data,
request a JSON endpoint rather than building on this markup.

**`POST /post`** — `application/x-www-form-urlencoded`, UTF-8. Fields:
`title=` (required, 80 chars, line breaks become spaces) and `body=`
(optional, 600 chars, line breaks kept). The original single `entry=`
field is still accepted as a title-only post; a post with only
`body=` takes the body's first line as its title. Always answers
`303 See Other` → `/billboard`, **even when an empty post was silently
rejected** — there's no error path on this endpoint. Posts expire 72
hours after posting (the clock only counts once the node's clock is
set); don't assume permanence.

## Files (fservbot)

**No JSON listing either** — `GET /files` is HTML only.

**`POST /upload`** — raw body, not multipart; filename and optional
slot hash in headers:

```
POST /upload
X-Filename: photo.jpg
X-Hash: <optional, see "regulated upload" below>
Content-Length: <n>

<raw file bytes>
```

Plain-text responses: `400` (no filename or empty body), `503` (no SD
card mounted), `507` (card at its 75% ceiling with nothing evictable),
`500` (connection dropped mid-upload; nothing kept), `200` with
`Uploaded. Your balance: N` (credits on) or `Uploaded. Thanks for
bringing something.` (credits off). Filenames are sanitized for the SD
card's FAT32/exFAT rules — `< > : " / \ | ? *` become `_`, quotes are
dropped — so the stored name can differ slightly from what you sent.

**`GET /download?f=<filename>`** — filename percent-encoded as UTF-8
bytes (`é` → `%C3%A9`, as standard URL encoders do). `404` if missing;
otherwise streams the file with `Content-Length` and a
`Content-Disposition` filename, debiting credit (if enabled) only once
the file is confirmed to exist.

**Credits** are on by default (`CREDITS_ENABLED` in `config.py`
disables them per node). Weight by extension: video (`mp4 mkv avi
mov`) 3, music (`mp3 flac wav ogg m4a`) 2, documents (`pdf txt doc
docx`) 1, anything else 1. Uploads credit that weight; downloads debit
it. All zero when credits are off.

**Regulated upload ("awaiting slot")**: a hash can be marked, locally
on the node, as awaiting one specific upload. Send that hash as
`X-Hash` and the file goes to its own one-shot slot instead of the
shared pool, marked fulfilled only once the bytes are on disk. Omit
`X-Hash` (or send it empty) for ordinary uploads.

## Android: RNode over a local TCP bridge

If your app bridges an RNode (USB or BLE) to Reticulum through a local
TCP socket, declare the interface with KISS framing:

```ini
[[LoRa_Interface]]
  type = TCPClientInterface
  target_host = 127.0.0.1
  target_port = 4243
  kiss_framing = True
```

Per Reticulum's manual, `TCPClientInterface` speaks Reticulum's own TCP
framing by default, not KISS; `kiss_framing = True` is the documented
option for a device or program exposing KISS on a TCP port. Without it
you get exactly this: the RX counter rises, nothing decodes, and
announce handlers never fire. Check `fixed_mtu` against your SF8/BW125
link budget while you're there.

Not covered here: what makes RNode firmware fall back to WiFi AP mode.
That lives in RNode_Firmware_CE's own source, a separate GPL-3.0
project.
