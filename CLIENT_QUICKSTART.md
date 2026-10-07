# Building a Stump Client — Read This First

One page. Read it before writing any client code, especially before
you hit `AUTH-CHALLENGE` cold and start reverse-engineering source.

**Source and released builds**: https://github.com/ruderigo/LaBuche-Stump
— this is the reference point for what's actually current. If something
you're looking at (a forwarded zip, an older doc, a snippet someone
pasted into chat) disagrees with what's there, the repo wins.

---

## Radio settings

The provisioner's defaults, which LaBuche and its Heltec run:

| Frequency | Bandwidth | Spreading factor | Coding rate | TX power |
|---|---|---|---|---|
| 915.0 MHz | 125 kHz | 8 | 4/5 | 7 dBm |

The technician can change them when provisioning each node, and a
node's admin can change its Heltec Bridge's settings from `/admin`
(TX power included) — and every node on a mesh has to match — a mismatched radio doesn't error, it just
never hears anything. Don't hardcode them; make them a setting with
these as the default.

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

### Over LXMF: what's automatic, what survives, how tight it is

**Same three messages**, sent as LXMF message contents; the node answers
the same way. Verified end to end over the mesh bridge with a real
signature.

**Nothing is automatic.** A mesh sender is known to the node only by its
LXMF address, and the node does not turn that into a verified identity
by itself. It couldn't safely today either: whether it checks the
signature on an incoming LXMF message depends on the board having
native Ed25519 (`verify_signatures = ed25519.have_native()` in the
node's `urns/lxmf.py`), and without it the check is skipped — and
unchecked messages are delivered all the same. So `/auth` is the proof.

**Do you need it?** Only if the node's access mode is `hybrid` or
`mandatory`, or you want to enter a `minted` or `hybrid` room. The
default mode is `open`, where nothing requires it. It's still what
carries your nick and credits with you as a person rather than as a
connection.

**What survives a node reboot:** your identity record (identity hash,
nick, credits) is saved on the SD card and survives. The link between
your LXMF address and that identity does not — it lives for the
session only, by design. After the node reboots, send `/auth` again.
(On the web, the same applies whenever your IP changes.)

**The 60-second window**, from the node sending `AUTH-CHALLENGE` to
receiving your answer, is fixed. At SF8 / 125 kHz one hop typically
takes about 10 s in total — ~4 s of airtime for the whole exchange,
plus up to 5 s for the node's send cycle each way. What can eat the
rest: a lost packet costs 10 s before LXMF retries
(`DELIVERY_RETRY_WAIT`), an unknown path ~7 s (`PATH_REQUEST_WAIT`),
and each extra hop adds its own airtime. So:

- send `/auth` directly, never through a propagation node, and answer
  the challenge immediately;
- keep the answer to one packet — `/auth <pubkey> <sig>` is 263 bytes
  of the 295 allowed; leave the LXMF title empty and add no fields, or
  it's sent over a link, with extra round trips;
- if it fails as expired, just start over: every challenge is
  single-use, so a new `/auth` gets a fresh one.

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

**The server stores a DM only for its recipient.** Polling
(`GET /rrc/poll`) never returns a DM you sent — there is no "sent items"
queue. What comes back instead is the **reply to the send itself**: the
response to `POST /rrc/send` (over LXMF, a message back) is your own DM
line, `[DM] <your nick>: text`, when the DM reached an existing
recipient — or `⊖ <name>` if no one has that name, or a sentence for
the rarer failures.

So show your own messages from that reply, not from polling. Either
draw a message the moment you send it as *pending* and mark it
*delivered* or *failed* when the reply arrives, or draw it only on the
confirming reply — the web chat does the latter. Both work; the one
thing that never happens is your message turning up in the poll.

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

## HTTPS

A node with a certificate installed (see `HTTPS_SETUP.md`) also serves
HTTPS on 443 under its name, e.g. `https://stump.labuche.app`, reachable
**on the node's own Wi-Fi** (its DNS answers every name with the node's
address). Same API, same paths. A page opened over plain HTTP on the node's Wi-Fi first asks the
browser to fetch `https://<name>/tls-ok` and moves to HTTPS only if that
works; API requests are never redirected.

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
 "stumps": [],
 "node": {"name": "LaBuche", "lxmf": "53d91d2f…48e4"}
}
```

- `kind`: `msg`, `system` (room activity, written as symbols — see
  *Room activity and DMs are symbols* below; `nick` is `"*"`),
  `action` (`/me`), or `dm`.
- `messages` and `dms` share one id sequence — one `since` covers both.
- `users` is the people in your room **plus** mesh peers reachable by
  DM only (see RRC over LXMF) — i.e. everyone you can `/msg`.
- `node` is the node you're talking to: its name and its LXMF address.
  A client reaching the same node over Wi-Fi and LoRa matches the two
  by this address.
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

## Room activity and DMs are symbols

Room activity and DM lines are written with symbols, not sentences, so
every reader sees the same line whatever their language — over HTTP
(`system` messages in `/rrc/poll`) and over LXMF alike. Parse them by
their first character:

| Line | Meaning |
|---|---|
| `✓ rod` | rod joined the room |
| `✗ rod` | rod left the room (or, for a mesh user, went quiet) |
| `✎ rod → bob` | rod is now bob |
| `✎ #main text` | the room's topic is now *text* |
| `[DM] <bob>: text` | a private message, written by bob — see below |
| `→ #main` | you are now in #main (reply to `/join` or `/part`) |
| `⊖ ghost` | no one here is called ghost (reply to `/msg`) |
| `#main: a, b` | who's in #main (reply to `/names`) |
| `#main ·3 text` | a room, how many are in it, its topic (reply to `/rooms`) |

**Replies you act on start with a token**, the same in every language,
then ` — ` and a sentence for people:

| Token | Meaning |
|---|---|
| `= #lxmf — …` | you're already in #lxmf (reply to `/join`, `/part`) |
| `? /frob — …` | no such command |
| `⊘ #vip minted — …` | that room's tier (`minted` or `hybrid`) refused you |
| `⧗ — …` | slow down: too many voice notes too fast |

Inside a DM, `♪ 5.0 s` is a voice note and its length: `[DM] <bob>: ♪ 5.0 s`.

Match on the token; ignore the sentence. (`AUTH-CHALLENGE`, `AUTH-OK`
and `AUTH-FAIL` work the same way.)

`~` in front of a name means that person is on the mesh: `✓ ~rod`.
It's never part of the nick itself (nicks can't contain it), and
`/msg ~rod` still reaches rod. `/me` stays `* rod waves`. The symbols
are U+2713 ✓, U+2717 ✗, U+270E ✎, U+2192 →, U+2296 ⊖. A room
move shows as `✗ rod` in the room left and `✓ rod` in the room joined,
with nothing after the name.

What's still a sentence: `/help` (which ends with a key to these
symbols), and the rarer replies — messaging yourself, a malformed
command, a taken nick, a room you can't enter, and the one-line hint
before the first DM to an announce-only mesh peer. Those follow the
reader's language on the web; mesh clients get the node's default,
French. The two built-in rooms' default topics also follow the reader
until someone sets a real topic.

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
✓ ~rod                  room activity (symbols, above)
[DM] <nick>: text       DM -- always the author's nick
```

Up to 10 room messages per 5-second cycle; your own room lines are
never echoed back.

**Each pushed batch names its room in the LXMF message title** — `#lxmf`,
`#main` — so lines queued just before a `/join` can't be filed under the
wrong room. File room lines by the title, not by the room you think
you're in. A batch with only DMs has no title (each `[DM]` line names
its author).

**You're told which room you landed in.** Whenever the node puts you in
a room you didn't ask for — your first message, your first message
after going quiet past `MESH_PEER_TIMEOUT`, or a tiered `#lxmf`
redirecting you to `#main` — it sends `→ #room` before anything else.

**Room tiers apply over the mesh.** `/join` and the `#room text`
shortcut into a `minted` or `hybrid` room are refused with
`⊘ #room <tier> — …` unless you qualify, exactly as on the web.
(Before this, the mesh bridge didn't check tiers at all.) `/rooms`
shows each gated room's tier in brackets.

### DMs: `[DM] <author>: text` — and your own come back to you

Every DM line, on the web and over LXMF, is `[DM] <author>: text`, and
the name is always **who wrote it**. A conversation reads like this:

```
[DM] <meshuser>: hello
[DM] <mynickname>: bonjour!
[DM] <meshuser>: I want to burrow your pen
[DM] <mynickname>: of course!
```

When a mesh client sends `/msg <nick> <text>` (or `/m`, `/w`), the node
answers with that DM as your own line — `[DM] <your nick>: text`. **This
echo is deliberate and stays:** it means the node accepted the DM *and
found a recipient by that name* (the LXMF delivery proof only means the
node received your message). If no one has that name you get
`⊖ <name>` instead; the rarer failures (messaging yourself, no text)
are a sentence.

So over LXMF, a `[DM]` line is either someone writing to you, or the
confirmation of what you sent. **Tell them apart by the author:** your
own nick is a confirmation — match it to the message you just sent by
its text; any other nick is an incoming DM. Your nick is your
announced display name (cleaned: letters, digits and `` -_[]{}\^`| ``,
16 characters, a numeric suffix if taken); the first confirmation also
shows it to you exactly. The confirmation no longer names the
recipient — your app knows whom it sent to.

Parsing (a nick never contains `>`):

```kotlin
val dm = Regex("""^\[DM\] <([^>]+)>: (.*)$""")    // author, text
val unknown = Regex("""^\u2296 (\S+)$""")          // ⊖ name: no one called that
// dm.author == myNick -> confirmation of your DM; otherwise a DM to you
```

The web chat uses the same rule: your message only appears in the
thread once your own `[DM]` line comes back, and a `⊖` reply is shown
instead — so a DM to someone who has left is never mistaken for sent.

**What it costs on air** (SF8 / 125 kHz / CR 4/5): every LXMF message
across the radio is two transmissions — the message and its delivery
proof — so the echo doubles a DM's airtime.

| Per DM (29 characters) | Airtime |
|---|---|
| DM + its proof | ~0.9 s |
| with the echo + its proof | ~1.8 s |

That's ~3% vs ~6% of the channel at 2 DMs a minute, ~15% vs ~30% at 10.
Shortening the echo wouldn't help much: ~198 of a message's 227 bytes
are fixed encryption and LXMF overhead.

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

## Voice notes

Voice notes work in Stump chat as DMs, in the format FireFly and Sideband
share: LXMF `FIELD_AUDIO` (7) = `[mode, audio]` (see FireFly's client
quickstart, 0.2.10). The node accepts both formats and relays the field
**byte for byte unchanged**:

| Mode | Audio bytes | |
|---|---|---|
| 16 (`AM_OPUS_OGG`) | A complete Ogg Opus file (RFC 7845), 1 or 2 channels, mapping family 0 | FireFly's default, and what this node's web chat sends (16 kHz mono, 8 kbit/s constrained VBR, 60 ms frames) |
| 3–9 (`AM_CODEC2_*`) | Raw Codec 2 1.2.0 frames, back to back | Plays everywhere |

**Length, label and limits** come from the bytes, in whole milliseconds,
exactly as FireFly's spec defines them — never from a timer:

- Opus: `floor((last granule position − pre-skip) / 48)`, from the Ogg
  pages of the first logical stream (pages with granule −1 don't count).
- Codec 2: `floor(bytes / bytes_per_frame) × frame_ms`; trailing bytes
  that don't make a whole frame count for nothing.
- Label: `♪ 5.0 s` — tenths rounded half up.
- Accepted: 600 to 15,000 ms for Codec 2, 600 to **15,100** ms for Opus
  (60 ms frames don't land exactly on 15 s); at most 16 KB of audio. A
  15 s Opus note at 8 kbit/s is about 13.4–14.2 KB.

**Over LXMF:** send an LXMF message to the node with the text
`/msg <nick>` and the audio field, **directly** — not through a
propagation node (see below). The node relays it as a voice DM and
answers `[DM] <you>: ♪ 5.0 s` (or `⊖ name`, or `⧗ — …` if you're sending
too fast). A voice note *to* you arrives as its own LXMF message with
the audio field and the text `[DM] <sender>: ♪ 5.0 s`. Audio without
`/msg <nick>` gets a one-line hint instead.

**Over HTTP:**

| Request | What it does |
|---|---|
| `POST /rrc/voice?to=<nick>&mode=<n>` | Body: the note — an Ogg Opus file (mode 16) or Codec 2 frames; up to 16 KB, `413` above. Reply: `{"replies": [...]}`, the same lines as `/msg` |
| `GET /rrc/poll` | A voice DM has `"voice": {"mode", "bytes", "ms", "secs"}` and the text `♪ 5.0 s`; no audio in the JSON. `ms` is exact; `secs` is the same length as a number |
| `GET /rrc/voice?id=<dm id>` | The note's bytes, if it's in *your* inbox (`404` otherwise); `audio/ogg` for Opus |
| `GET /opus.wasm`, `/opus.js` | libopus 1.5.2 for the browser (BSD-style licence) |
| `GET /codec2.wasm`, `/codec2.js` | Codec 2 1.2.0 for the browser (LGPL-2.1) |

**Matching a confirmation to the note you sent.** `[DM] <you>: ♪ 5.0 s`
names the length but not the recipient. Compute the label from your own
bytes when you send and keep the note pending; when a `♪` confirmation
arrives, take the pending note with exactly that label, or failing that
the one whose length is closest. **Never match by order:** over LoRa a
one-packet note can reach the node before a longer one sent just before
it, which needed a link. A `⧗` names no note — it stopped your most
recent one.

**Limits:** one note per 5 s and 30 an hour per sender (`⧗ — …`); the 10
most recent voice notes per inbox, and about 2 MB of voice in total, are
kept. Same access rules as `/msg`.

**DMs only.** Rooms are text: a note sent to a room (or with no
`/msg <nick>`) gets the hint below, not a relay.

**Your own notes don't come back in the poll** — the same one-sided
delivery as text DMs (see "DMs: the one-sided delivery gotcha"). Keep
the audio you sent and show it from your side once the confirming
`[DM] <you>: ♪ …` reply arrives.

**Replies that aren't tokens** (sentences, in English here; over LXMF
in the node's default language):

| Situation | Reply |
|---|---|
| Under 0.6 s, or over the length limit | `voice notes must be 0.6 to 15 seconds` |
| Unsupported mode, or bytes that aren't a valid note of that mode | `unreadable voice note (Opus or Codec 2 expected)` |
| Audio with no `/msg <nick>` | `to send a voice note through this node: /msg <who> with the note attached` |

A node not yet updated still says `(Codec 2 expected)` and refuses Opus.

**Edge cases:** over LXMF, a malformed audio field (not `[int, bytes]`,
empty, or over 64 KiB) is ignored, and the message is handled as plain
text.

**Chat sent through a propagation node can arrive late.** Anything left
at a propagation node for the Stump itself — a `/msg`, a `/nick`, a
voice note — reaches its chat only when it's delivered, possibly hours
later. If it's more than 30 minutes old by the node's clock, the node
doesn't act on it and replies `your message arrived N min late (left at
a propagation node) — nothing was done; send it again directly`. Send
Stump chat directly.

## Propagation node (offline messages)

A node can run as an **LXMF 1.2 propagation node** (its technician
switches it on). It then announces `lxmf.propagation` on its own
identity, with standard propagation-node announce data — upstream's
`LXMF.pn_announce_data_is_valid()` accepts it, and the name is the
node's name. Its address comes from the same identity as the node's LXMF address, so
a client that knows the Stump can compute it and ask for a path right
away, without waiting for its (rare) announce:
`RNS.Destination.hash(stump_identity, "lxmf", "propagation")`. Use it exactly like any upstream propagation node: set it
as the outbound propagation node, send with `PROPAGATED`, and sync with
`request_messages_from_propagation_node()`. It holds messages for
recipients who are offline; it can't read them.

What's different from an upstream `lxmd`, and what to plan for:

- **Receipt is confirmed before the stamp is checked.** Checking a
  stamp takes this board ~30 s, so it happens in the background after
  your upload is confirmed. A message with a bad stamp is dropped
  afterwards, without telling you — compute stamps properly (upstream
  does) and this never matters.
- **15 KB per message** (`data[3]` in the announce). Every voice format
  fits, including a 15 s Opus note (~13.4–14.2 KB); a big attachment
  won't. Anything the node accepts can always be collected.
- **Stamp cost 16, flexibility 3** — the node accepts cost 13 and up.
- **A sync reply is at most ~16 KB** — always room for one full-size
  message. If more is waiting, sync again.
- **A message isn't collectable until its stamp has been checked,**
  usually within a minute or two of the upload; longer if several
  arrived together.
- **No peering yet.** `/offer` answers `ERROR_NO_ACCESS` (`0xf1`), so an
  upstream node that tries to peer backs off. Peering is planned.

Messages addressed to the node itself are delivered to its chat, as
upstream delivers locally.

## Billboard

**`GET /billboard.json`** — the billboard as data, newest first:

```json
{"posts": [{"id": "1790978231.612803836", "title": "Marché samedi",
            "body": "L'été à 10h", "sig": "0078"}]}
```

`id` is the post's id (it can be `null` for a post written before ids
existed, until the node next rewrites the board). `body` is `""` for a
title-only post.

`GET /billboard` is the rendered HTML page. A post is a required **title** and an optional
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
title-only rows. Use `/billboard.json` above rather than parsing this
markup.

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

**`GET /files.json`** — the shelf as data:

```json
{"sd": true, "credits": true,
 "files": [{"name": "photo.jpg", "size": 48213, "class": "other", "cost": 1}]}
```

`size` is in bytes (`null` if it couldn't be read), `class` is one of
`video`, `music`, `document`, `other`, and `cost` is the credit weight
(0 when credits are off). Both JSON endpoints answer `404` when their
feature is off on the node. `GET /files` is the rendered HTML page.

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
