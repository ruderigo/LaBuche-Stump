# Threat model

What a Stump protects, from whom, how, and what it doesn't. For the
pentesters, the client teams (FireFly for Android, the handhelds) and
whoever runs a Stump. Written for 1.0.0-beta.37; the tests named here are
in `tests/`, and run on every change (CI).

## What a Stump is

A small server in a place: its own Wi-Fi (a captive portal) with chat,
a billboard and shared files, and a LoRa radio (a Heltec running RNode)
on the Reticulum mesh. Stumps chain to each other over Wi-Fi. It runs on
an ESP32-S3 CAM (MicroPython) or on a computer (the same code on Python).

## What's worth protecting

| Asset | Where | Who should reach it |
|---|---|---|
| The admin password, the operator password | `config.py` on the Stump | the person running the Stump |
| The chain secret | `config.py` on each Stump of a chain | the Stumps of that chain |
| The Stump's Reticulum identity (private key) | `/rns/` on the Stump | the Stump only |
| Profiles: nick, salted password hash (2,000 rounds), credits | `profiles.json`, shared along the chain | each person; the hashes, nobody |
| Who's who: verified identities, nick ↔ LXMF address | memory and `/sd/stumpid` | the Stump |
| DMs, room lines | memory (and the propagation node's store) | the people in them |
| Files people shared | the SD card (`/sd/shared`) | everyone on the Wi-Fi (they're shared) |
| The Stump staying up | -- | everyone |

## Who might attack, and from where

1. **A visitor on the Stump's Wi-Fi** (the captive portal is open by design): the web routes, the chat, uploads, `/admin`'s login, the chain port (UDP 4242), discovery (UDP 4244).
2. **Someone with a radio** in LoRa range: any Reticulum packet, announce or LXMF message they like, as many as the air takes.
3. **A device on the chain's Wi-Fi without the chain secret**: hellos, spoofed UDP from another Stump's address.
4. **A Stump of the chain** (knows the secret): an insider.
5. **Someone holding the board** (USB): everything; out of scope beyond what's noted.

## What stops what

**The web side** (attacker 1)
- Connections can't hold the Stump: at most 10 at once on a CAM (6 from one address), deadlines on headers (10 s, 3 s when busy), on silence (15 s) and on slow bodies (512 B/s past 30 s); headers 16 KB, a line 4 KB. Test: `conn_limits`, `pc_conn`.
- Every route takes junk without falling over: paths climbing out (`..`, encoded, doubled), bodies that aren't UTF-8, deep JSON, huge or negative lengths, every method. Nothing outside the card's folders is ever served or written. Test: `web_fuzz` (a canary file beside the firmware must never come back).
- Uploads: one at a time per address, 30 files an hour, a quarter of the card a day, 300 KB/s on a CAM; names cleaned (no paths, no reserved characters); a taken name gets "(2)". Test: `upload_fair`.
- Passwords: guessed slowly. 5 wrong for an address or a profile lock it (30 s, doubling to 15 min); `/admin` locks for everyone after 20 wrong (never for the computer the Stump runs on); all checks together are budgeted (10 every 10 s); profile hashes give everyone else a turn while they're computed; passwords and tags are compared in constant time. Test: `password_cost`, `hardening`.
- Sessions: `/admin` and profile sessions are random 128-bit tokens in `HttpOnly; SameSite` cookies; a visitor ID is 80 random bits and must look like one; made-up ones get nothing. `/admin`'s forms carry the session token, so another site can't post them. Test: `admin_session_layout`, `web_fuzz`.
- Apps prove who they are (`/hello`: the Stump signs, the app signs, a nonce once): no replay, no taking another's name. Test: `identity_matrix`.
- `/admin` and the voice recorder over HTTPS when a certificate is installed (`docs/HTTPS_SETUP.md`); the chat stays on HTTP.

**The radio side** (attacker 2)
- Every LXMF message's signature is checked; a forged or unverifiable one is never "verified", and with an access mode other than open, refused. Test: `hardening`.
- The parsers take junk: mutated and random packets through Transport, mutated LXMF messages and msgpack (bombs included: 4-billion-element arrays, nesting refused past 32), without an exception escaping, a slow call, or memory growing past the tables' caps. Test: `urns_fuzz`.
- A Stump's beacon must name its own LXMF address; one naming someone else's is ignored (it would have made a person look like a Stump). Test: `chain_proto`.
- Limits per sender on chat lines, voice notes and commands; at most 48 KB of relayed file data per link every 10 minutes onto LoRa.

**The chain** (attackers 3 and 4)
- A Stump joins with a hello carrying an HMAC of the chain secret; only good hellos keep it in (a packet never does); a hello with another secret puts that address out at once; refusals are shown in `/admin` and `/chain.json`. The Stump above answers with an HMAC too. Test: `chain_proto`, `pc_three_stumps`.
- Profiles (password hashes) cross the chain only with an HMAC of the chain secret: arriving on a chain link isn't enough, since UDP can be made to look like it comes from the Stump above. Test: `chain_proto`.
- Bots' lines stay on their Stump.

**Code that runs** (plugins, packs, updates)
- The Provisioner checks every firmware file against its manifest before copying, and every pack's files against their hashes (a changed file is a warning that must be answered twice). A pack can't replace the core or a built-in plugin, nor list files outside its folder. Test: `provisioner`.

## What it doesn't do (known limits)

- **HTTP is readable on the Wi-Fi.** Without HTTPS, anyone on the Stump's Wi-Fi can read chat polls and cookies, and take over a visitor's or a profile's session with a copied cookie. `/admin` asks for HTTPS when a certificate is installed; the rest stays on HTTP by design (the CAM's handshakes).
- **Clients known by their address.** With `APP_HELLO = False`, an app without cookies is known by its IP address, which another device on the Wi-Fi can take.
- **Chain packets aren't signed one by one.** Membership is (the hellos); each Reticulum packet carries its own end-to-end protection (signatures, encryption), but a device spoofing a chain Stump's address can still inject ordinary Reticulum traffic -- what any radio user can send anyway. Reticulum's per-packet access codes (IFAC) would close it at about a hundredfold slowdown on a CAM.
- **An insider is trusted.** A Stump with the chain secret can push profiles (a nick, a password hash) to the others.
- **Plugins are trusted code.** MicroPython has no sandbox; install only packs you trust.
- **Holding the board is game over, by design.** No flash encryption, secure boot or eFuses, and no passphrase at start: they're irreversible on an ESP32-S3, they'd lock out the Provisioner and Arborist (the way a broken Stump is repaired), and a Stump has to start by itself when it's plugged in. Hardening that needs any of these is out of scope.
- **What the Stump keeps, it keeps in the clear.** Room lines and DMs are in its memory, profiles and the identity table on its storage; the propagation node stores messages as they arrived (encrypted for their recipients). Holding the board is holding all of it, the admin password included (`config.py`).
- **Denial of service by radio.** Nobody can stop someone filling the air; the Stump keeps its tables capped and its own airtime limited.
- **Mixed releases.** A chain with Stumps before 1.0.0-beta.37 doesn't share profiles with newer ones when it has a secret, and their answers to hellos aren't signed (`/admin` says so).

## For the pentesters

Please try, and tell us what you find:
- the web routes (`docs/COMMANDS.md`, `CLIENT_QUICKSTART.md`) from a phone or laptop on the Stump's Wi-Fi, with and without HTTPS;
- taking another person's name or session; getting into `/admin`;
- getting a file outside the shared folders, or writing one;
- holding the Stump (connections, uploads, password checks) so others can't use it;
- joining a chain without its secret, or making a Stump believe something about the chain;
- from a radio: malformed packets, announce floods, forged beacons and LXMF messages;
- Arborist (`Arborist-<release>.html`, `final_firmware/docs/ARBORIST.md`): what it writes, and whether a page or a board can make it write something else.

Reports: to the person running the project, with the release (`/node.json` → `release`), what you sent and what came back.
