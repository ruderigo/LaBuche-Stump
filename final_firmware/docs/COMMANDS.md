# Project Stump — Command Reference

Every command in the system, with exact request/response text pulled
directly from the running code — not paraphrased. Where a reply is
shown, that is what the field will actually see, verified against the
source on the date this document was written.

**Source and released builds**: https://github.com/ruderigo/LaBuche-Stump

**Structure**: identity and access control first, since that's what
needs the most thorough testing right now — the rest of the chat
surface follows after.

---

## Part 1 — Identity & Access Control (test this first, test it hardest)

Two independent systems. Read this before testing either:

- **Global `AUTH_MODE`** (`open` / `hybrid` / `mandatory`) — a node-wide
  setting.
- **Per-room tiers** (`open` / `minted` / `hybrid`) — a property of one
  specific room, independent of global mode, enforced *unconditionally*
  regardless of which global mode is active.

**The naming trap**: "hybrid" means two different things above. A room
tiered `hybrid` and a node in global `hybrid` mode are unrelated
settings that happen to share a word. Test them as separate variables,
not as one switch.

---

### `/auth` — request a challenge

**Request:**
```
/auth
```
**Response:**
```
AUTH-CHALLENGE <32-char hex nonce>
```

Single-use, expires in 60 seconds. A second `/auth` before completing
the first silently replaces the pending challenge — the old nonce
becomes invalid.

---

### `/auth <pubkey_hex> <sig_hex>` — answer the challenge

**Request:**
```
/auth <pubkey_hex> <sig_hex>
```
- `pubkey_hex` — 128 hex characters (the identity's public key)
- `sig_hex` — 128 hex characters (signature over the nonce)

**⚠ The exact thing to get right when testing a client against this:**
the signed message is the **ASCII bytes of the nonce's hex string** —
literally `nonce_hex.encode()` — **not** the decoded nonce bytes. These
two are not interchangeable; a signature over one fails validation
against the other, confirmed directly. If a client's `/auth` always
fails with `signature does not match`, this is the first thing to check.

**Responses:**

| Condition | Reply |
|---|---|
| Success | `AUTH-OK <32-char hex identity hash>` |
| No `/auth` was sent first | `AUTH-FAIL no challenge pending -- start with /auth` |
| Took longer than 60s to answer | `AUTH-FAIL challenge expired -- start again with /auth` |
| Malformed hex in either argument | `AUTH-FAIL bad public key or signature encoding` |
| Public key doesn't decode to a valid key | `AUTH-FAIL bad public key: <reason>` |
| Signature doesn't match the nonce | `AUTH-FAIL signature does not match` |
| Wrong number of arguments | `usage: /auth   (then)   /auth <pubkey_hex> <signature_hex>` |

**Never translated, ever, regardless of the sender's language setting**:
`AUTH-CHALLENGE` / `AUTH-OK` / `AUTH-FAIL` are wire tokens a client
parses by splitting on the first space. Test this specifically if
verifying i18n — these three lines must come back in English-token form
even for a French- or Spanish-set session.

**Test matrix for this pair:**
- [ ] Fresh `/auth` → `/auth <pub> <sig>` with a correct signature → `AUTH-OK`
- [ ] `/auth <pub> <sig>` with **no** preceding `/auth` → correct `AUTH-FAIL`
- [ ] `/auth`, wait 61+ seconds, then answer → expired `AUTH-FAIL`
- [ ] `/auth`, answer with a signature from a **different** identity than the pubkey claims → `signature does not match`
- [ ] `/auth <pub> <sig>` reusing an **already-consumed** nonce → `AUTH-FAIL no challenge pending` (nonces are single-use)
- [ ] Malformed hex (odd length, non-hex characters) in either field → `bad public key or signature encoding`

---

### `/whoami` — check current verification status

**Request:**
```
/whoami
```
**Responses:**

| Condition | Reply |
|---|---|
| Not verified this session | `not verified this session -- /auth to prove an identity` |
| Verified | `verified as <hash>  nick=<nick or "(no nick set)">` |

Always allowed, in every `AUTH_MODE`, with no exceptions — this is one
of the three bootstrap commands (with `/auth` and `/help`) that never
gets gated, since you need it to even begin proving who you are.

**Note for testing nick sync**: the nick shown here updates automatically
the first time a verified session runs `/nick`, but *only* to a real
chosen name — the auto-generated `guest-XXXX` placeholder is
deliberately never persisted here, even after a **rejected** `/nick`
attempt (taken name). Worth testing that specific edge case: verify,
attempt `/nick` with an already-taken name, then `/whoami` — the nick
field should still say "(no nick set)", not show the placeholder.

---

### `/admin <password> mode <open|hybrid|mandatory>` — global access mode

**Request:**
```
/admin <password> mode
/admin <password> mode <open|hybrid|mandatory>
```
**Responses:**

| Condition | Reply |
|---|---|
| No password configured anywhere on the node | `not authorised -- or no admin password is configured yet, in which case /admin is off until a technician sets one.` |
| Wrong password | *(identical message to above — deliberately ambiguous, so an anonymous prompter can't learn whether the node has any admin protection at all)* |
| Correct password, no subcommand argument | `current mode: <open\|hybrid\|mandatory>` |
| Correct password, valid mode | `mode set to <mode>` |
| Correct password, invalid mode name | `unknown mode -- use open, hybrid, or mandatory` |

**Runtime-only.** A live mode change reverts to whatever `config.py`
specifies on next reboot — it is never written back to disk. This is
deliberate: a permanent security-posture change should require a
technician physically present with the Provisioner, not just anyone who
learned the password.

**What each mode actually gates**, precisely:

| Mode | Reads (`/rooms`, `/names`, bare `/topic`) | Writes (posting, `/join`, `/msg`, `/me`, `/topic <text>`) |
|---|---|---|
| `open` | open | open |
| `hybrid` | open | **requires `/auth`** |
| `mandatory` | **requires `/auth`** | **requires `/auth`** |

A plain message with no leading `/` is *always* classified as a write,
unconditionally — this is the one thing worth testing explicitly under
`hybrid`: an unverified user's very first plain chat line should be
blocked, even though `/rooms` works fine for them.

**Blocked-write reply** (any mode, any gated action): `auth required
for that -- send /auth to verify your identity`

**Test matrix:**
- [ ] `open`: unverified user can post, join, DM — nothing blocked
- [ ] `hybrid`: unverified user's `/rooms` works; their next plain message is blocked with the exact reply above
- [ ] `hybrid`: unverified user's bare `/topic` (no argument) is **not** blocked; `/topic <text>` **is**
- [ ] `mandatory`: unverified user's `/rooms` is **also** blocked
- [ ] In every mode: `/auth`, `/whoami`, `/help` always work, even for a totally fresh, unverified connection

---

### `/admin <password> room <name> <open|minted|hybrid>` — per-room tiers

**Request:**
```
/admin <password> room <name> <open|minted|hybrid>
```
**Responses:**

| Condition | Reply |
|---|---|
| Fewer than 2 arguments after the password | `usage: /admin <password> room <name> <open|minted|hybrid>` |
| Room name has no letters or numbers | `room names need at least one letter or number` |
| Tier isn't one of the three valid values | `tier must be open, minted, or hybrid` |
| Success | `#<name> is now <tier>` |

Setting a room to `minted` or `hybrid` **creates it immediately** if it
doesn't exist yet — a technician doesn't need to wait for someone to
`/join` it first before it's visible in `/rooms`.

**What each tier means, precisely:**

| Tier | Who can `/join` |
|---|---|
| `open` (default for any untouched room) | Anyone |
| `minted` | Only a verified identity. No exceptions, no invites. |
| `hybrid` | A verified identity, freely. Anyone else needs `/invite` from someone verified who is **already inside**. |

**A tiered room's gate runs regardless of global `AUTH_MODE`** —
including under global `open`, where nothing else is gated at all. This
is the one behavior most worth confirming directly: create a `minted`
room while the node is in global `open` mode, and confirm an unverified
user still can't get in.

**Rejection replies** (from an unverified user attempting `/join`, on
the web or over the mesh — mesh `/join` and the `#room text` shortcut
are gated exactly like the web's `/join`). Each starts with the
`⊘ #room <tier>` token, then the sentence:

| Tier | Reply |
|---|---|
| `minted` | `⊘ #vip minted — that room requires a verified identity -- send /auth first` |
| `hybrid` | `⊘ #lounge hybrid — that room is invite-only for unverified visitors -- ask someone already inside to /invite you` |

`/rooms` shows the tier of every non-open room in brackets — on the web
and, since this was added, over LXMF too (the mesh bridge answers
`/rooms` itself and used to show no tiers at all). Each line is
`#room ·<how many> <topic>`:
```
#main ·3  General. Be decent.
#lounge ·2  [hybrid]
```

**Test matrix:**
- [ ] `/admin <pw> room vip minted`, global mode `open` → unverified `/join vip` still blocked
- [ ] `/rooms` shows `[minted]` next to it
- [ ] A verified identity `/join vip` succeeds
- [ ] `/admin <pw> room lounge hybrid` → unverified `/join lounge` blocked with the invite-only message, not the verified-identity message
- [ ] Re-tiering an existing room to `open` removes the restriction immediately
- [ ] Re-tiering a room to something stricter does **not** eject anyone already inside (known, intentional limitation — worth confirming it behaves this way rather than assuming)

---

### `/invite <nick>` — bring an unverified person into a hybrid room

**Request:**
```
/invite <nick>
/invite <nick> <room>          (room defaults to the one you're currently in)
```
**Responses:**

| Condition | Reply |
|---|---|
| No arguments | `usage: /invite <nick> [room]  (defaults to the room you're in)` |
| Sender isn't verified | `only a verified identity can invite someone -- /auth first` |
| Sender is trying to invite into a room they're not currently in | `you can only invite people into a room you're currently in` |
| Target room isn't tiered `hybrid` | `that room isn't invite-gated` |
| Named nick doesn't exist in that room | `no one here called '<nick>'` |
| Success | `invited <nick> into #<room>` |

**Not password-gated** — gated by being verified *and physically
present in the room*, a deliberately different axis from `/admin`.
Membership control requires presence.

**Order matters for testing**: the "not invite-gated" check runs
*before* resolving the nick, so inviting into an already-open room
correctly says "not needed here" rather than a confusing "no one by
that name" — worth confirming both orderings produce the message you'd
actually expect.

**Test matrix:**
- [ ] Verified user, present in a `hybrid` room, invites a real (unverified) nick present in the same room → success, and that person's next `/join` on that room succeeds
- [ ] Same invite attempted from *outside* the room → wrong-room message
- [ ] Unverified user attempts `/invite` at all → verification-required message
- [ ] `/invite` into an `open` or `minted` room → "not invite-gated"

---

### `/admin <password> meshroom` — mesh room status

**Request:**
```
/admin <password> meshroom
```
**Response** (always, regardless of argument — this command takes none):
```
#lxmf is always available, mesh peers land there by default -- currently <status>. Change the tier with /admin <password> room lxmf <open|minted|hybrid>.
```
where `<status>` is either `open to everyone (default)` or `tiered '<tier>'`.

`#lxmf` is a **fixed, always-present room** — seeded exactly like
`#main`, not something this command creates or names. This is a status
query only; there is nothing left to set here. See
[Part 3](#part-3--the-mesh-room) below for the full behavior.

---

### `/admin <password> revoke <hexhash>` — forget a verified identity

**Request:**
```
/admin <password> revoke <hexhash>
```
**Responses:**

| Condition | Reply |
|---|---|
| No hash argument | `usage: /admin <password> revoke <hexhash>` |
| Hash found and removed | `revoked` |
| Hash not found | `no such identity` |

Removes the identity's saved record **and** any currently-live verified
session tied to it — a revoked identity is unverified again
immediately, not just on their next reconnect.

---

## Part 2 — General chat commands (`rrc.py`)

Command *words* stay in English as a fixed vocabulary regardless of the
sender's language. Room activity and DMs are **symbols**, the same for
everyone — `✓ rod` joined, `✗ rod` left, `✎ rod → bob` renamed,
`✎ #main text` topic, `[DM] <author>: text`, `→ #main` you moved,
`⊖ name` no one by that name, `~` before a mesh user's name (full table
in `CLIENT_QUICKSTART.md`). Replies a client acts on start with a token, then ` — ` and the
sentence: `= #room` already there, `? /cmd` no such command,
`⊘ #room <tier>` refused by the room's tier. Tiers now apply over the
mesh too — the bridge's own `/join` and its `#room text` shortcut are
checked exactly like the web's `/join`. The remaining sentence replies — `/help`,
errors, the tier rejections above — follow the sender's language on the
web; **over LXMF they arrive in the node's default language, French**,
since a mesh client has no language setting. The reply texts in this
document are the English versions. All of these are subject to the
global `AUTH_MODE` gate described in Part 1.

| Command | Effect |
|---|---|
| `/nick <name>` | Change your display name |
| `/join <room>` | Join or create a room |
| `/part` | Leave: back to `#main` on the web; over the mesh, back to the room you landed in |
| `/rooms` | List all rooms, with tier annotations |
| `/names` | Who's in the current room |
| `/topic` | Show the current room's topic (a *read*) |
| `/topic <text>` | Set the room's topic (a *write*) |
| `/msg <who> <text>` | Private message — never posted to a room. Reaches web users on their next poll and mesh users over LoRa (including mesh users who have only announced). The sender gets `[DM] <own nick>: text` back as confirmation, or `⊖ <name>` |
| `/me <action>` | Third-person action |
| `/clear` | Clear your own local view (client-side only) |
| `/help` | This list, translated, ending with a key to the symbols |

An unrecognized command replies: `unknown command: /<cmd>  (try /help)`

---

## Part 3 — The mesh room

`#lxmf` exists unconditionally from the moment the node boots — seeded
in `rrc.py` exactly like `#main`, not created by any command, not
dependent on `stumpid` being installed or active at all.

**A mesh/LoRa peer's first message lands them in `#lxmf` by default,**
and the node tells them so: its first reply is `→ #lxmf` (or `→ #main`
when redirected, below). The same happens after they go quiet past
`MESH_PEER_TIMEOUT` and write again.
A web visitor can `/join lxmf` and `/join main` freely, with zero setup
and zero auth friction under global `open` mode — this is core
chat-bridge behavior, not an identity-plugin feature. Auth is layered
on *top* of this, never a precondition for it.

If `stumpid` **is** active and an operator has tiered `#lxmf` (via the
same `/admin room lxmf <tier>` command from Part 1), an unqualified
mesh peer is redirected to `#main` instead, with a system message
explaining why — translated into their own language preference the
same way a rejected web visitor's message is.

**Test matrix:**
- [ ] Fresh, unconfigured node: `/rooms` shows both `#main` and `#lxmf`, with zero admin action taken
- [ ] A mesh peer's first message lands them in `#lxmf`, and the first thing they receive is `→ #lxmf`
- [ ] A web user joins `#lxmf` and posts; the message survives and is visible via `/rooms`/polling
- [ ] `/admin <pw> room lxmf minted`, then a new (unverified) mesh peer arrives → redirected to `#main`, receives `→ #main`, and a system message explains why
- [ ] An unverified mesh peer sends `/join vip` (a `minted` room) → `⊘ #vip minted — …`, stays put
- [ ] The same peer sends `#vip hello` → refused the same way, nothing posted in `#vip`
- [ ] Re-tier `#lxmf` back to `open` → the *next* new mesh peer lands in `#lxmf` again (existing peers already placed aren't moved)

---

## Part 4 — BarKeep console (`/chat` — separate from RRC)

The single-box command console. No longer on the home page: it's
hidden feature HF-003, still working at `/concierge` (and `POST /chat`)
for anyone who types the address — see `HIDDEN_FEATURES.md`. Command
words stay fixed; only descriptions translate.

| Input | Effect |
|---|---|
| `menu` / `help` / *(blank)* | Full command list |
| `chat` / `rrc` / `irc` / `talk` | Points to the RRC page |
| `billboard` | Points to the bulletin board |
| `files` | Lists what's on the shelf |
| `get <name>` | Download link for a specific file |
| `balance` | Credit balance (or "everything's free" if credits are disabled) |
| `upload` | How to share a file |
| *(anything else)* | `Don't know that one. Try menu.` |

Not subject to `AUTH_MODE` at all — this console is intentionally
outside the RRC/stumpid gating system.

---

## Part 5 — HTTP API

A feature the technician turned off (chat, billboard, files, about)
answers `404` with a "not offered" message on all of its addresses.

With a certificate installed (`HTTPS_SETUP.md`), everything below is
also served over HTTPS on port 443 under the node's name. A page opened
over plain HTTP on the node's own Wi-Fi moves there only if the browser
accepts the certificate (it fetches `/tls-ok` first).

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Home page: a tile per enabled feature |
| GET | `/concierge` | BarKeep console — hidden feature HF-003, not linked anywhere |
| POST | `/chat` | BarKeep command, raw text body |
| GET | `/rrc` | RRC chat client |
| GET | `/rrc/poll?room=&since=` | JSON: new messages, DMs, `users`, `stumps`, and `node` (name + LXMF address) |
| POST | `/rrc/send` | RRC chat line or `/command`, raw text body |
| POST | `/rrc/voice?to=&mode=` | Voice note DM: an Ogg Opus file (mode 16) or Codec 2 frames (3–9) as the body, up to 16 KB; replies like `/msg` |
| GET | `/rrc/voice?id=` | A voice note in your own inbox, byte for byte (`audio/ogg` for Opus) |
| GET | `/codec2.wasm` | Codec 2 1.2.0 for the browser (LGPL-2.1) |
| GET | `/codec2.js` | Its JavaScript glue |
| GET | `/opus.wasm` | libopus 1.5.2 for the browser (BSD-style licence) |
| GET | `/opus.js` | Its JavaScript glue: Ogg Opus writer and reader |
| GET | `/billboard` | Bulletin board page |
| GET | `/billboard.json` | Bulletin board as JSON: `{"posts": [{"id","title","body","sig"}]}` |
| POST | `/post` | New notice: `title=` (required), `body=` (optional); legacy `entry=` = title only |
| GET | `/files` | File listing and upload panel (upload shown only with an SD card) |
| GET | `/files.json` | Shelf as JSON: `{"sd","credits","files": [{"name","size","class","cost"}]}` |
| POST | `/upload` | `X-Filename` header, raw body; `507` if the card is over capacity even after evicting the oldest shared files |
| GET | `/download?f=` | Streamed file download (name percent-encoded as UTF-8) |
| GET | `/admin` | Password-only login; no link anywhere points here |
| POST | `/admin` | Validates the password; shows files, billboard moderation, radio, propagation node, and theme settings |
| POST | `/admin/delete` | Batch-deletes checked files, re-validates the password |
| POST | `/admin/delete_post` | Deletes checked billboard posts, re-validates the password |
| POST | `/admin/radio` | Heltec Bridge radio settings (TX power, frequency, bandwidth, SF, CR); saved, and sent to the Heltec at once if connected |
| POST | `/admin/home` | Home page section: `place` (top/bottom/left/right), `height` (80–1200), `show=1` to show it; saves `home/home.json` |
| POST | `/admin/propagation` | Propagation node: `action=on`/`off`, or `action=leave` with `to=` and `text=` to leave a message for an LXMF address |
| GET | `/about` | About page |
| GET | `/tools` | Technician tools — CLI provisioning steps |
| GET | `/tool?f=` | Download a tool file |
| GET | `/home-file?f=` | A file from the card's `home/` folder, for the home page's own section (see `HOME_BRANDING.md`) |
| GET | `/flash` | Browser-based flasher — hidden feature HF-001, not linked from `/tools` |
| GET | `/fw?f=` | Firmware image / catalog for the flasher |
| GET | `/lang?set=&next=` | Set language, redirect back |
| GET | `/tls-ok` | Answers `ok`: what a plain-HTTP page fetches over HTTPS to check the browser accepts the certificate (see `HTTPS_SETUP.md`) |

The propagation node itself (offline LXMF messages) has no HTTP or chat
commands of its own: phones talk to it over LXMF, as to any upstream
propagation node — see `CLIENT_QUICKSTART.md`.

---

## Appendix — quick sanity checklist before a field session

1. `/whoami` on a fresh connection → not verified
2. `/auth` → `/auth <pub> <sig>` → `AUTH-OK`
3. `/whoami` again → now shows verified, correct hash
4. `/rooms` → both `#main` and `#lxmf` visible, no tags (untiered)
5. `/admin <pw> mode hybrid` → your own next plain message should **not** be blocked (you're already verified from step 2, and a verified session is fully exempt from the gate, regardless of mode) — if it *is* blocked, something regressed
6. `/admin <pw> mode open` → back to normal
