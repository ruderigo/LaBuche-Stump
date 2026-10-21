# Project Stump — Command Reference

Every command in the system, with exact request/response text pulled
directly from the running code — not paraphrased. Where a reply is
shown, that is what the field will actually see, verified against the
source on the date this document was written.

**Source and released builds**: https://github.com/ruderigo/SandBox_Stump_On (beta testing; stable: https://github.com/ruderigo/LaBuche-Stump)

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

**Kept across restarts.** A live mode change, and every room tier, is
saved (`access.json`, on the card, or the board's flash without one)
and restored at start-up: a brownout on a solar node no longer quietly
reopens rooms an admin had closed. `/admin <password> reset-access`
goes back to `config.py` (its mode, no room tiers) and forgets the
saved settings. (Room invites aren't kept: they're tied to a visitor's
address, which changes anyway.)

**Wrong passwords.** After 5 wrong passwords in a row from the same
visitor (here, on the `/admin` page, or with the fserv bot's commands),
that visitor waits 30 s, then twice as long after each further wrong
one, up to 15 minutes: `⧗ — too many wrong passwords — try again in
<n> s`. A right password clears it. Other visitors aren't affected.

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
- [ ] Re-tiering a room to something stricter moves everyone inside who no longer qualifies to `#main`: web visitors get the refusal (`⊘ #vip minted — …`) as a private message, mesh users get `→ #main` and the refusal; the admin's reply lists who was moved (`moved to #main: bob, ~meshy`)
- [ ] After a restart, `/rooms` still shows the tiers set before it; `/admin <pw> reset-access` clears them

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
#lora-mesh is always available, mesh peers land there by default -- currently <status>. Change the tier with /admin <password> room lora-mesh <open|minted|hybrid>.
```
where `<status>` is either `open to everyone (default)` or `tiered '<tier>'`.

`#lora-mesh` is a **fixed, always-present room** — seeded exactly like
`#main`, not something this command creates or names. This is a status
query only; there is nothing left to set here. See
[Part 3](#part-3--the-mesh-room) below for the full behavior.

---

### `/admin <password> reset-access` — back to config.py

**Request:** `/admin <password> reset-access`

**Reply:** `access back to config.py: mode <mode>, no room tiers`

Puts the mode back to `config.py`'s, clears every room tier, and deletes
the saved access settings, so a restart keeps it that way.

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

### Plugin commands

Plugins (`PLUGINS.md`) add their own commands, listed at the end of
`/help` (over the mesh, as one `extras: !roll !seen …` line). Each one
answers both `/roll` and `!roll`:

- `/roll 2d6` is a command: the line itself isn't posted.
- `!roll 2d6` is a room line, posted like any other, and then answered —
  the IRC-bot style, so everyone sees what was asked. A `!word` no
  plugin knows is just text. Commands that take a password (fservbot's
  `/fs*`, stumpid's `/admin`, `/auth`) are slash only.

A plugin can't take a core command's name (`/nick`, `/join`, `/msg`,
`/admin`, `/auth`, …). Over the mesh a plugin command answers in one
line by default (three at most, 200 characters each). Plugin commands
are limited to one per 1.5 s and 15 a minute per person; past that the
reply is `⧗ — one command at a time — wait a little`. If a plugin fails,
the reply is `<plugin> had a problem — not your fault, try again later`.

### `/admin <password> plugin` — plugin off switch

```
/admin <password> plugin                 list: name: state — commands
/admin <password> plugin off <name>      switch off, saved across restarts
/admin <password> plugin on <name>       switch back on
```

| Situation | Reply |
|---|---|
| Switched off | `<name> is off` |
| Switched off, but an older-style (`activate()`) plugin that can't stop while running | `<name> will be off from the next restart` |
| Switched on | `<name> is on` |
| Switched on, but it didn't start | `<name> is switched on but didn't start — see /admin` |
| `stumpid` | `stumpid runs this node's access control: it can't be switched off here` |
| Unknown name | `no plugin called <name>` |

The same switch is on the `/admin` page, under **Plugins**, with each
plugin's state, the RAM it took to start and its last error.

---

## Part 3 — The mesh room

(`#lxmf` before 1.0.0-beta.4: the old name still reaches it.)

`#lora-mesh` exists unconditionally from the moment the node boots — seeded
in `rrc.py` exactly like `#main`, not created by any command, not
dependent on `stumpid` being installed or active at all.

**A mesh/LoRa peer's first message lands them in `#lora-mesh` by default,**
and the node tells them so: its first reply is `→ #lora-mesh` (or `→ #main`
when redirected, below). The same happens after they go quiet past
`MESH_PEER_TIMEOUT` and write again.
A web visitor can `/join lora-mesh` and `/join main` freely, with zero setup
and zero auth friction under global `open` mode — this is core
chat-bridge behavior, not an identity-plugin feature. Auth is layered
on *top* of this, never a precondition for it.

If `stumpid` **is** active and an operator has tiered `#lora-mesh` (via the
same `/admin room lora-mesh <tier>` command from Part 1), an unqualified
mesh peer is redirected to `#main` instead, with a system message
explaining why — translated into their own language preference the
same way a rejected web visitor's message is.

**On a Stump in a chain**, a third room exists, `#wifi-mesh`: the other
Stumps of the chain land there (taken in as soon as they're heard over
the chain, no message needed), and so does anyone whose message came in
over the chain. Between Stumps only chat lines cross, shown as
`<nick@thatstump>`; joins, parts and renames stay on their own Stump.
People on other Stumps are DM contacts: `/msg rod@LaBuche text` is
private, and rod's answer arrives as a DM from `you@yourstump`; a
`/msg` to someone not there comes back as `⊖ name`. A DM to a Stump
itself (`/msg LaBuche text`) shows in that Stump's `#wifi-mesh` as
`<you@yourstump> @LaBuche text`. (`#chained` and `#mesh`, its names on
`main` for an afternoon, still reach it.)

**On a computer with its own Heltec**, `#lora-mesh` is the radio's room
(`/rooms` marks it `[LoRa]`): people on LoRa are always in; on the web,
the computer's owner (on the computer itself) and whom they invite:
`/lora invite <nick>`, `/lora uninvite <nick>`, `/lora` (who's invited).
Others get `⊘ #lora-mesh LoRa — …`; people on another Stump of the
chain can't join it here. `LORA_MESH_LOCKED = False` in config.py opens it.

**`#lora-mesh` is shared by the Stumps**: each sends its own people's
lines in it to the other Stumps it knows (on the chain, or heard over
LoRa), titled `#lora-mesh`, and they show there as `<nick@stump>`, once,
going no further. **Radio first:** a Stump with its radio online sends
them over the radio only, in single packets, even when Wi-Fi reaches the
other Stump too; without one, over the chain. A Stump keeps sending to
the others it knows over its radio even when the route to them is gone
(the radio blinked offline), and lines written meanwhile (the last 5
minutes) still go when they're back. `/names` there lists everyone in
it on every Stump: each Stump tells the others who's in its
`#lora-mesh` (a `[NAMES] nick nick` line in a `#lora-mesh` batch, when it
changes and every 10 minutes), shown as `nick@stump`. With no Wi-Fi between them
at all (a computer off Wi-Fi with its Heltec), Stumps meet over the
radio: a Stump announces itself as soon as its radio connects (at most
every 10 minutes), and answers a Stump it hears for the first time (at
most once a minute). `#wifi-mesh` is the
opposite: always over Wi-Fi (the chain), never on the radio. `#main`
stays on its Stump.

**Test matrix:**
- [ ] Fresh, unconfigured node: `/rooms` shows both `#main` and `#lora-mesh`, with zero admin action taken
- [ ] A mesh peer's first message lands them in `#lora-mesh`, and the first thing they receive is `→ #lora-mesh`
- [ ] A web user joins `#lora-mesh` and posts; the message survives and is visible via `/rooms`/polling
- [ ] `/admin <pw> room lora-mesh minted`, then a new (unverified) mesh peer arrives → redirected to `#main`, receives `→ #main`, and a system message explains why
- [ ] An unverified mesh peer sends `/join vip` (a `minted` room) → `⊘ #vip minted — …`, stays put
- [ ] The same peer sends `#vip hello` → refused the same way, nothing posted in `#vip`
- [ ] Re-tier `#lora-mesh` back to `open` → the *next* new mesh peer lands in `#lora-mesh` again (existing peers already placed aren't moved)
- [ ] Two chained Stumps, started minutes apart: each lists the other in `#wifi-mesh` (🪵, bottom of the column) within ~30 s of the link
- [ ] A line in `#wifi-mesh` on one shows once on the other as `<nick@stump>`; nothing appears in `#lora-mesh`
- [ ] A line in `#lora-mesh` on one shows once in the other's `#lora-mesh` as `<nick@stump>`; nothing in `#wifi-mesh`; with Heltecs, `/admin` → *Radio — live* counts it sent
- [ ] `/msg rod@LaBuche hi` reaches rod privately; rod's answer comes back

---

## Part 4 — BarKeep, the librarian (`/chat` — separate from RRC)

BarKeep answers questions about what's on the node: files on the shelf,
billboard notices, the apps on the card. Asked from the **firefly** in
the corner of `/files`, `/billboard` and `/`, from `/concierge`, with `POST /chat`
(raw text body; `Accept: application/json` gives `{"reply", "found"}`),
or in the chat with `!find <words>` (aliases `!search`, `!cherche`,
`!busca`; one short line, for LoRa). Its replies are in the asker's
language; `!find`'s in the node's default.

How it reads a question (`librarian.py`): lowercase, accents folded,
filler words dropped (*hey, do you have, est-ce que, tienes…*); words
naming a kind of file (*movie / film / película*, *book / livre / libro*,
*music / musique / música*, *photo / imagen*), the billboard (*notice,
annonce, aviso, news*) or the apps (*app, apk, firefly, outils*); the rest
is the topic, matched against file names and notices (*gardening* finds
*garden*, *vélo* finds *Vélo*).

| Input | Effect |
|---|---|
| `menu` / `help` / *(blank)* | Full command list |
| `chat` / `rrc` / `irc` / `talk` | Points to the RRC page |
| `files` | Lists what's on the shelf |
| `get <name>` | Download link for a specific file |
| `balance` | Credit balance (or "everything's free" if credits are disabled) |
| `upload` | How to share a file |
| *a kind* — `do you have a movie?` | Up to 5 of that kind, newest first, linked; none: what kinds there are |
| *a kind and a topic* — `as-tu un livre sur le jardinage ?` | The matching ones; none: the files of that kind |
| *a topic* — `anything about sourdough?` | Matching files and notices; none: says so, and points to the Files page |
| *the billboard* — `what's new on the billboard?` | The 3 newest notices (or matching ones) |
| *the apps* — `do you have the firefly app?` | The apps on the card, linked |
| *a greeting* — `hello` | What BarKeep can do |

File sharing or the billboard switched off (`FEATURES`): BarKeep says
file sharing isn't offered, and leaves notices out.

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
| POST | `/chat` | Ask BarKeep (raw text body); `Accept: application/json`: `{"reply", "found"}` |
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
| GET | `/billboard.json` | Bulletin board as JSON: `{"posts": [{"id","title","body","sig"}]}`; `?q=` search, `?sort=newest\|oldest\|title` |
| POST | `/post` | New notice: `title=` (required), `body=` (optional), or a JSON body; legacy `entry=` = title only. With `Accept: application/json`: `201` + the new post's `id`, or `400`/`507` with an `error` token |
| GET | `/files` | File listing and upload panel (upload shown only with an SD card) |
| GET | `/files.json` | Shelf as JSON: `{"sd","credits","files": [{"name","size","class","cat","mtime","cost"}]}`; `?q=`, `?cat=`, `?sort=newest\|oldest\|name\|small\|large` |
| POST | `/upload` | `X-Filename` header (percent-encoded UTF-8 with `X-Filename-Encoding: url`, or raw), raw body; `507` if the card is over capacity even after evicting the oldest shared files. With `Accept: application/json`: the result as data (`name`, `size`, `balance`, or an `error` token) |
| GET | `/download?f=` | Streamed file download (name percent-encoded as UTF-8) |
| GET | `/admin` | The admin page with a live session (the `stump_admin` cookie set at login), the password prompt otherwise; no link anywhere points here. Admin pages are sent `Cache-Control: no-store` |
| POST | `/admin` | The login: a right password starts a session (cookie, 30 min idle, ends at restart) and redirects to `GET /admin`, so the password never sits in the browser's history. The page has files, billboard moderation, the Tools page's files, radio, propagation node, plugins, the theme for everyone, the order of the buttons and this browser's own theme; its forms carry the session, not the password |
| POST | `/admin/logout` | Ends the session and clears the cookie; back to the prompt (Back can't show the admin page again) |
| POST | `/admin/delete` | Batch-deletes checked files, re-validates the password |
| POST | `/admin/delete_post` | Deletes checked billboard posts, re-validates the password |
| POST | `/admin/radio` | Heltec Bridge radio settings (TX power, frequency, bandwidth, SF, CR); saved, and sent to the Heltec at once if connected |
| POST | `/admin/home` | Home page section: `place` (top/bottom/left/right), `height` (80–1200), `show=1` to show it; saves `home/home.json` |
| POST | `/admin/propagation` | Propagation node: `action=on`/`off`, or `action=leave` with `to=` and `text=` to leave a message for an LXMF address |
| POST | `/admin/layout` | Order of the buttons: `nav=` and `tiles=`, comma-separated keys (`home chat board files tools about`, plugin cards `p:/p/<plugin>/…`); `nav_hidden=` and `tiles_hidden=`, the keys taken off the row (empty: none); or `reset=1` (default order, nothing hidden); saved on the board (`/layout.json`) |
| POST | `/admin/theme` | The theme for everyone: `theme=` a built-in or pack theme, or empty to go back to `config.py`'s `THEME`; applied at once, saved on the board (`/site_theme.json`) |
| POST | `/admin/tools` | Deletes from the card's `tools/`: `d=<section>` (a whole folder) and `f=<file>` or `f=<section>/<file>`, repeated; re-validates the password |
| POST | `/admin/plugins` | Plugin off switch: `action=on`/`off` and `name=`; saved across restarts (`stumpid` can't be switched off) |
| GET | `/about` | About page |
| GET | `/tools` | Apps, then a section per tool folder (title, summary and Read me from its `README.md`) |
| GET | `/tools.json` | The same as JSON: `{"sd", "apps": [{"app","file","version","size","url"}], "files": [{"name","size","section","url"}], "sections": [{"dir","title","summary","readme","files"}]}` |
| GET | `/tool?f=` | Download a tool file: `name`, or `folder/name` one folder down |
| GET | `/node.json` | Who this node is, without joining the chat: `{"name", "lxmf", "version", "release", "ap_ip", "chain", "features"}`; `chain` is `null` or the `/chain.json` object (see `CLIENT_QUICKSTART.md`) |
| GET | `/hw.json` | The radio(s): `{"state": "none"|"online"|"connecting"|"error", "radios": [{"name", "kind": "usb"|"ble"|"wifi", "where", "device", "fw", "platform", "error", "settings", "radio", "rssi", "snr", "noise", "channel_load", "airtime", "battery", "rx", "tx", "last_rx", "since"}]}` |
| POST | `/admin/radio.json` | The radio's settings from the chat: `freq` (MHz), `bw`, `sf`, `cr`, `txp`; the computer's owner or an `/admin` session, while `/admin` allows it; `{"ok", "message"}`, or 403 |
| POST | `/admin/poweroff` | A computer Stump only: turns it off (radio off, then a clean exit the login service doesn't restart). `admin_pass`, or none from the computer itself (the Stop file). Started again by the Start file in its folder, or at the next login |
| POST | `/admin/bluetooth.json` | The Heltec's Bluetooth: `action=on`/`off`/`pair` (pairing mode); the computer's owner or an `/admin` session; `{"ok", "message"}` |
| POST | `/admin/radiolink.json` | A computer's Heltec: `link=usb`/`ble`, switched live and kept (`radio_link.json`); the computer's owner or an `/admin` session; `{"ok", "message"}` |
| GET | `/admin/bt.json` | The pairing code the Heltec gave (`{"pin"}`, for 3 minutes); the computer's owner or an `/admin` session only, never in `/hw.json` |
| POST | `/admin/radioretry` | `/admin`'s *Reconnect the radio*: `admin_pass`; the Heltec (USB, Bluetooth or Wi-Fi) connects again now, handshake and radio set-up |
| POST | `/admin/radiochat` | `/admin`'s switch for the radio settings in the chat: `admin_pass`, `on=1`/`0`; saved (`radio_chat.json`) |
| GET | `/chain.json` | This Stump's place in a chain over Wi-Fi: `{"enabled", "role", "ap_ip", "lan_ip", "channel", "secret", "chain", "path", "fallback", "uplink", "below"}`; `chain`: an id made from the secret (the same on every Stump of the chain); `path`: the Stumps' addresses from the first one down to this one (`[]` while cut off); `fallback`: the Stump it joined after the one set up went away; `lan_ip`: the first Stump's address on the Wi-Fi it joined, where it also takes chain links (else `null`) (see `CHAIN.md`) |
| GET | `/plugins.json` | What the plugins add: `{"api", "trigger", "plugins": [{"name","version","commands","pages"}]}` (see `PLUGINS.md`) |
| GET, POST | `/p/<plugin>/…` | A plugin's own pages. Any address under `/p/` that no running plugin answers shows the home page, like any unknown address |
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
4. `/rooms` → both `#main` and `#lora-mesh` visible, no tags (untiered)
5. `/admin <pw> mode hybrid` → your own next plain message should **not** be blocked (you're already verified from step 2, and a verified session is fully exempt from the gate, regardless of mode) — if it *is* blocked, something regressed
6. `/admin <pw> mode open` → back to normal
