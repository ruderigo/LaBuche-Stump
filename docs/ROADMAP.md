# Roadmap

What comes next, in order. Tick a box when it lands on main (with the
release it came in). Sizes compare to one of the four hardening
milestones of October (conn.py, keepalive.py, fair uploads, password
checks): **L** is about one of them, **M** about half.

Smaller things noticed along the way go in [TODO.md](TODO.md).

## 1. Cleanup review (M)

Clear out what's stale before building on it. **No change in
behaviour**: the full suite stays green and the golden outputs stay
identical throughout.

- [x] Dead code: functions nothing calls, unused imports, config keys
      nothing reads; HF-002 (the awaiting-slot upload) removed
- [x] Duplicates: escaping and URL encoding in one place (webtext.py,
      two drifted copies were bugs); every JSON save whole or not at all
      (store.py: a dozen wrote in place)
- [x] barkeep.py split: one handler per route; /admin in admin_web.py,
      loaded on first use
- [x] Provisioner: reviewed (no dead menus); the firmware zip is named
      with its release (`Stump-1.0.0-beta.35.zip`)
- [x] i18n: keys nothing uses (12 removed)
- [x] Docs: the root copies (CLIENT_QUICKSTART, HIDDEN_FEATURES, REF)
      point to final_firmware/docs
- [x] Docs: changelog, README (mpy-cross), engineering notes (the code's
      layout)
- [x] Tests: reviewed, none checking the same thing twice
- [x] The board gets its modules compiled (.mpy): 1,429 KB of source
      sent as 589 KB, nothing compiled at boot
- [x] On LaBuche (1.0.0-beta.35, compiled): ~450 KB of modules on the
      board (was ~1.4 MB of source), 7,467,280 bytes free after start,
      ~10 s from the crypto line to serving (the baseline from here)

Done in 1.0.0-beta.35.

## 2. Chain hardening and interoperability (L)

Make the chain a solid base for everything built on it (chain storage,
NomadNet over the chain).

- [x] A chain protocol version (2) in the beacon, `/node.json` and
      `/chain.json`; older and newer Stumps keep working, `/admin` says
      which to update
- [x] Capabilities in the beacon: release, access mode, radio, storage,
      plugins (the groundwork for chain storage)
- [x] No `TX failed` warning for a chain link with nobody on it
- [x] Only a good hello keeps a Stump in (a packet used to); refusals
      shown in `/chain.json` and `/admin`
- [x] Everyone in `#wifi-mesh` listed on every Stump, quiet people too
- [x] Recovery when a Stump in the middle restarts or the chain secret
      changes (pc_three_stumps); its Wi-Fi lost: chain_failover,
      pc_parent_moves
- [x] Nothing lost and nothing doubled across hops: messages, DMs,
      names (pc_three_stumps)
- [x] Mixed chains: a computer first and a CAM below (pc_chain), a CAM
      first and a computer below (pc_joins_cam), a Stump in the middle
      (pc_three_stumps, a computer with two networks)
- [x] Recovery when an end Stump restarts: it lists and names the others
      at once (beta.43; R36MAX team's change -- caps "b" per boot, answer a
      first/restart beacon, owed answers queued). Bench pending on a CAM.
- [ ] On the bench: a CAM in the middle of a real chain
- [x] The path matrix across 1, 2 and 3 Stumps: DM to DM, room to room,
      client to Stump, visitors, and the paths that must not exist
      (identity_matrix, pc_two_stumps, pc_three_stumps)

## 3. Pentest hardening (L)

From the attacker's side, after the cleanup (less code to review).

- [x] Every route's input: sizes, encodings, paths (`..`, odd names),
      JSON depth (web_fuzz; bodies that aren't UTF-8 fixed)
- [x] Auth and sessions: cookies, `/hello` replay, constant-time
      compares; lockouts are per Stump (THREAT_MODEL.md)
- [x] The chain as a way in: forged beacons ignored, profiles marked
      with the secret, the parent's answer signed (chain_proto)
- [x] LoRa: floods, malformed packets, a fuzzer on urns' parsers
      (urns_fuzz; msgpack depth and LXMF shape fixed)
- [x] Plugins and packs: hash checks, what an update can overwrite (a
      pack can't replace the core or a built-in plugin)
- [x] A short threat model: docs/THREAT_MODEL.md

Done in 1.0.0-beta.37.

## 4. Enhancements (after 1-3)

- [ ] Storage layer (M-L): one place for SD, the board's flash, none,
      and the chain; storage profiles in features.py; files stored on a
      chain Stump that has room (needs 2)
- [ ] NomadNet pack (L): shelved for now
- [x] Browser flasher back, as Arborist: one page saved on the computer,
      opened from file:// (Web Serial, no certificate). CAM only
      (MicroPython is MIT; RNode is GPL-3, the Heltec stays with the
      Provisioner). The old on-device /flash flasher (HF-001) retired in
      beta.41. final_firmware/docs/ARBORIST.md
  - [x] Arborist-<release>.html: MicroPython 1.28, the Stump
        compiled, a setup form (name, admin password, home Wi-Fi, the
        Heltec), or an update that keeps the settings
        (scripts/build_arborist.py; tests/arborist_test.py)
  - [x] Built by CI (an artifact) and attached to each release
  - [x] One page for the Provisioner's board jobs too: Settings (read,
        change, save -- by the firmware's settings.py, as /admin; a
        copy kept, one put back) and Check (restart, listen, sum up)
  - [ ] The SD card: Tools, packs, wipe (slow over USB: say so)
  - [x] On the bench: a Stump updated with it (1.0.0-beta.38, 2026-10-25)
  - [x] On the bench: Settings read from a Stump (2026-10-25)
  - [ ] On the bench: a new Stump made with it; Settings changed and
        saved; Check
  - [ ] Windows: serial-check.html, then Arborist
  - [x] MicroPython's SHA-256 pinned in build_arborist.py (as micropython.org served it to CI)
  - [x] Radio settings (freq/bw/SF/CR/TX power) via the firmware's
        radio.save, when a Heltec is enabled (beta.42); the Heltec's own
        rnodeconf provisioning stays with the Provisioner (GPL-3)
  - [ ] On the bench: Arborist sets the radio on a CAM with a Heltec
  - [ ] Offered from the Stump: the Tools page (/sd/tools/arborist/),
        for a visitor's laptop to download and open
- [ ] More boards (M each): one Provisioner profile per board (flash,
      PSRAM, radio, SD pins)

## 5. Pentest preparation

From the checklist made with Gemini (2026-10-25, for 1.0.0-beta.38),
sections I to IV, each against the code. Its section V (how the
auditors are graded) stays out of the repository.

**I. Boundaries** -- a proposal that breaks one fails before any code

- [x] 1. Holding the board is game over: no eFuses, flash encryption or
      boot passphrase; USB stays plug-and-play (THREAT_MODEL.md, known limits)
- [x] 2. Storage postures: room lines, DMs and voice notes in memory
      only; the billboard (72 h), `/sd/shared/` (fserv.MAX_SD_RATIO) and
      the propagation store on the card, never encrypted again. The
      checklist's numbers are all enforced in code, not just the TTLs:
      rooms and DMs 60 each (`rrc.MAX_MESSAGES_PER_ROOM` rrc.py:816,
      `MAX_DMS_PER_USER` rrc.py:924), voice 10/inbox and ~2 MB total
      (`VOICE_PER_RECIPIENT`, `VOICE_TOTAL_BYTES` rrc.py:912-917), 150
      notices (`billboard.MAX_ENTRIES_STORED` billboard.py:353). README
      matches. TODO (future): a cap-flood test covering all five.
- [x] 3. Port 80 stateless (the 2 s /rrc/poll), HTTPS only for `/admin`
      and `/rrc/rec`, behind the `/tls-ok` check (barkeep.py, stump_tls.py)

**II. One thread, never stalled** (over ~7 s the Heltec bridge drops)

- [x] 4a. `prime_all_bridges()` before the long synchronous steps
      (announces, LXMF signing); ed25519_iram.mpy loaded
- [ ] 4b. Handshakes on 443 one at a time (three phones on `/admin` at
      once): no lock today -- measure on the bench, then decide
- [ ] 5. The propagation node's stamp checks yield between steps
      (propagation.py): measure the longest step on a CAM
- [x] 6. Slow and half-open connections: header and body deadlines,
      per-address limits (conn.py; tests conn_limits, pc_conn)
- [ ] 7. (the checklist's item 7 didn't come through: to add)

**III. Protocol, identity, the chain**

- [x] 8. Speed limits by network address (guard.py): CHAT_BURST 3,
      CHAT_EVERY 2 s, 3 posts a minute, announces 6 per 60 s; wrong
      passwords lock 30 s doubling to 15 min. An app header (e.g. an
      `X-Stump-Session` for FireFly) must never be a rate-limit key
- [x] 9. Room tiers (open, minted, hybrid) apply whatever AUTH_MODE is;
      an unverified LoRa person in a tiered #lora-mesh goes to #main,
      told why; `/auth` signatures over the challenge's hex
      (tests access_golden, identity_matrix, mesh_room)
- [x] 10a. Profiles only over a signed chain link, marked with the chain
      secret, never over LoRa (rrc_mesh._take_profiles; chain_proto)
- [ ] 10b. A profiles batch replayed by someone on a chain Stump's
      Wi-Fi: check that an older batch can't roll back a newer
      profile (newest wins), or keep recent batch ids
- [x] 10c. A Stump looking for a new parent never joins one of its own
      (chain._good_parent: "one of this Stump's own, below it")

**IV. Tools, supply chain**

- [x] 11. Arborist: every file's SHA-256 checked on the board;
      an update keeps config.py, the identity and the card (bench,
      beta.38); a config.py put back leaves config.py.bak; no GPL
      (RNode) code in it or in /sd/tools/flasher/
- [ ] 12. The HTTPS bench (bench/tls_probe.py, with the Android suite):
      handshake time and free memory on a CAM, a wrong clock, mismatched
      keys, /rrc/rec voice over HTTPS
