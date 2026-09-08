# stumpid

Optional Ed25519 identity verification for RRC. Answers a question
`rrc.py` deliberately leaves open: is this client provably the same
client as last time?

`rrc.py`'s `client_id` is a connection identifier (peer IP on WiFi, an
LXMF hash on the mesh via `rrc_mesh`) — honest about what it is, but not
proof of anything. Nothing stops a reconnect from getting a new IP, and
nothing stops one IP from being reused by someone else. stumpid adds a
second, optional layer: a real RNS `Identity` a client can *prove* it
holds via a signed challenge, giving it a hash that survives a changed
IP or a new LoRa session in a way `client_id` never could.

## How it works

Wraps `rrc.handle_input` at runtime, exactly like `fservbot` does —
never edits a core file, so hashed firmware stays byte-identical whether
or not this plugin is installed.

```
/auth                          -- request a challenge (step 1)
/auth <pubkey_hex> <sig_hex>   -- answer it (step 2)
/whoami                        -- what this session is verified as
/admin <password> mode <m>     -- change AUTH_MODE live
/admin <password> revoke <h>   -- forget a saved identity
```

The hash a client ends up verified as is never something they assert;
it's computed server-side from the public key that step 2 just proved
possession of the matching private key for. An earlier version of this
protocol had the client also state a hash up front, checked against the
key — confirmed that's provably redundant (identical computation, one
input either way), so it isn't part of the wire format.

**The signed message is the ASCII bytes of the nonce's hex STRING** (the
literal text after `AUTH-CHALLENGE`), not the decoded nonce bytes.
Verified directly that these are not interchangeable — a signature over
one fails `validate()` against the other. Client implementers: sign
`nonce_hex.encode()`, not `bytes.fromhex(nonce_hex)`.

Every challenge is single-use (consumed on the first response attempt,
valid or not) and expires after 60 seconds — a captured
`(nonce, signature)` pair can't be replayed later or against a different
request.

## Modes

- `open` — today's behaviour, no proof required. Default.
- `hybrid` — reading is open; posting, joining a room, DMing, etc.
  require a verified identity. A bare `/topic` (reading the current
  topic) stays open; `/topic <text>` (setting it) is gated — same
  command, opposite classification, so this is checked by argument, not
  just the verb.
- `mandatory` — everything except `/auth`, `/whoami`, `/help` requires
  one.

Set via the Provisioner wizard, or live with `/admin <password> mode
<m>`. A live change is runtime-only — it does not rewrite `config.py`,
so it reverts to the provisioned value on next boot. That's deliberate:
a permanent security-posture change belongs in `config.py` via the
Provisioner, with a technician physically present, not in a chat line
anyone who ever learned the password could type.

## What does NOT persist across a reconnect

A verified *identity* is durable (saved to `/sd/stumpid.json` if a card
is fitted). The *binding* of that identity to a `client_id` is not, and
is not meant to be — it lives only in memory for the session. Reconnect
with a new IP and you're unverified again until you `/auth` a second
time. Persisting that binding would mean trusting whoever holds an IP
next, which defeats the point.

## Nicks follow the identity, not the session

Once verified, setting a nick with `/nick` syncs it into the saved
identity record — `/whoami` and `/admin list` show it on a later visit,
even from a different IP. Syncing happens by reading back what `rrc`
actually settled on *after* delegating, rather than re-parsing `/nick`
here, so it can never disagree with `rrc`'s own collision handling: a
requested nick that's already taken is rejected by `rrc`, and nothing
here overrides that.

Two things worth knowing if you're touching this code:

- rrc's own auto-generated placeholder (`guest-XXXX`) is deliberately
  never synced, checked by exact comparison against
  `rrc.default_nick(client_id)`. A prefix check isn't enough — a
  **rejected** `/nick` attempt still reaches the sync point with the
  placeholder still in effect, and would otherwise silently persist
  `"guest-c0fe"` as if it were a real chosen name, defeating the
  `None`-means-nothing-chosen-yet signal `/whoami` relies on.
- The sync only writes to SD when the nick actually changed. Without
  that check, every single chat message from a verified user would
  trigger an SD write for no reason.

## Admin password

Checked via `fservbot.core.check_op_password()` when `fservbot` is
installed and has no password of its own set on this node — read LIVE
from fservbot's module, not a second independent `from config import
FSERVBOT_OP_PASSWORD` under stumpid's own name. That distinction is not
cosmetic: an earlier version did the second-import version, and it
silently stopped reflecting fservbot's actual password the moment
anything changed it, which is exactly the kind of drift "one password
story" is supposed to prevent.

No hard dependency between the two plugins — either can be removed
without breaking the other's admin path. If neither `AUTH_ADMIN_PASSWORD`
nor `fservbot` is configured, `/admin` is refused outright.

A wrong password and "no password configured anywhere" produce the
*same* denial message, matching `fservbot`'s own convention. Splitting
them would tell an anonymous prompter whether this node has any admin
protection at all, for free.

## Wrapping order, and what happens if you unwrap it wrong

Discovered and activated alphabetically by `load_plugins()`, so
`stumpid` activates after `fservbot` and becomes the *outer* wrapper.
That's required here — gating a write means intercepting it before
anything else (like a fservbot trigger) acts on it — but it currently
depends on alphabetical ordering rather than an enforced priority. A
future plugin sorting ahead of `stumpid` that also wants to gate writes
would need this reconsidered.

`deactivate()` on either plugin now refuses to unwind if it isn't still
the outermost layer — confirmed this matters: deactivating two stacked
plugins in *activation* order (rather than the reverse) used to corrupt
`rrc.handle_input` into a self-referential chain and blow MicroPython's
recursion limit. The correct order is last-activated-first-deactivated;
getting it backwards is now a logged refusal, not a crash.

## For Firefly / client implementers

The wire format is deliberately just chat text, so it works identically
over WiFi (direct HTTP to `barkeep.py`) and over LoRa (via `rrc_mesh`,
which routes mesh-peer commands through the same `rrc.handle_input`
this plugin wraps) — one implementation, two transports. A mesh peer
using `/auth` gets the exact same handshake a web client does.

Minting an identity is `Identity()` from `urns.identity` (or the
equivalent in any RNS-compatible client) — nothing Stump-specific.
`Identity.get_public_key()` is 64 bytes (128 hex chars);
`Identity.sign()` output is 64 bytes (128 hex chars).

This deliberately does **not** give LoRa peers a free pass just because
LXMF delivery already requires a real RNS identity to address them —
gating is identical across both transports on purpose, so there's no
footnote where LoRa users are silently "more trusted" than WiFi ones for
reasons unrelated to who they actually are.

## Verification status

Tested against real MicroPython with real Ed25519 crypto (not mocked),
through the actual plugin dispatch, not a mock of it:

- Full challenge/response cycle, wrong signer rejected, a consumed nonce
  rejected on replay.
- All three modes (`open`/`hybrid`/`mandatory`), including the
  bare-vs-argued `/topic` read/write distinction, and that verification
  correctly does not leak across a changed `client_id`.
- Composed with `fservbot` loaded together in real activation order: a
  write is genuinely blocked *before* reaching `fservbot`'s triggers
  under `mandatory` (confirmed the room doesn't grow), and the same
  trigger fires normally once verified.
- `deactivate()` ordering: an out-of-order unwind is refused cleanly
  (handler still callable afterward); correct order fully unwinds.
- `/admin`: wrong password, no password configured, correct password,
  live mode changes actually reflected in gating immediately, `revoke`
  removing both the saved record and any live session, and the
  fservbot-password fallback tracking a live password rotation on
  fservbot's side.
- Persistence round-trip across a simulated reboot (identity known,
  session verification correctly *not* restored), and eight malformed
  `/sd/stumpid.json` shapes (non-JSON, wrong types, wrong-length hash or
  key, a non-dict value under a real-looking key) — none corrupt the
  in-memory table or crash the boot; a genuinely valid entry survives
  sitting next to a garbage one in the same file.
- Nick sync: a set nick lands in the identity record and survives to
  `/whoami`; the auto-generated placeholder is never persisted, even via
  a rejected `/nick` attempt; an unrelated chat message triggers no SD
  write.
- Full headless boot of `example_node.py` with `fservbot` and `stumpid`
  both present: both discovered and activated by the real
  `load_plugins()`, web server and captive-portal DNS both come up, no
  crash, no boot loop.

Not yet tested: against a real Firefly client, against `rrc_mesh` with a
live LXMF peer, or the storage path on real SD hardware (only the
flash-fallback-free path is exercised here, same limitation as every
other SD-backed module in this project).
