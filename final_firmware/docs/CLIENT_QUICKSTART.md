# Building a Stump Client — Read This First

One page. Read it before writing any client code, especially before
you hit `AUTH-CHALLENGE` cold and start reverse-engineering source.

**Source and released builds**: https://github.com/ruderigo/SandBox_Stump
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
path. Rooms can be tiered to require it (`minted`) or to admit anyone
with an invite from someone who has it (`hybrid`). None of that matters
until you've done the handshake above once.

---

## The two rooms that always exist

`#main` and `#lxmf` are both present the moment a node boots — nothing
to request, nothing to configure. If you're building a mesh-first
client, **`#lxmf` is where your users land by default**; expect it, and
don't be surprised it exists even on a freshly provisioned node you've
never touched. `#main` is the general room walk-up WiFi visitors share.

**`/part` returns you to wherever you actually started, not always
`#main`.** A mesh peer's "home" is `#lxmf` under normal circumstances,
or `#main` specifically if a tiered `#lxmf` redirected them there
instead (see `COMMANDS.md` for the tier system). Don't hardcode `#main`
as the universal "go home" target in your own client logic — ask the
server, or just track whichever room the peer actually landed in first.

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

---

## Color and design palette

If your client is meant to feel visually connected to the rest of the
Stump ecosystem (the web console, RRC chat, the About page), this is
the actual palette in use — not illustrative, these are the live values.

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

Once that's working, the full command reference (`COMMANDS.md`) covers
everything else — room tiers, invites, admin commands, the exact reply
text for every edge case. This page exists so you don't need it just to
get past the first handshake.
