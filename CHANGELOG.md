# Changelog

## 1.0.0-beta.47
- **A DM to a Stump stays a DM.** Writing to another Stump itself (its entry under *Stumps* in the chat's side column, or `/msg Laptop text`) used to land in that Stump's `#wifi-mesh` as `<you@yourstump> @Laptop text`, for everyone there to read, and any answer came in the room. Now it never goes into a room: that Stump's BarKeep answers it privately -- the same triggers as in a room (`!help`, `!fserv`, `!list`...), or, at most every 10 minutes per sender, how to reach the people there (`name@Laptop`) -- and the answer comes back into the sender's DM thread with that Stump (from `Laptop`, not `Laptop@Laptop`; a "no one here by that name" answer too). Without BarKeep, the Stump says nobody reads its own DMs. BarKeep offers this as the plugin service `bot`. It's the receiving Stump that keeps it private, so the CAMs need it too: an older Stump still puts it in its `#wifi-mesh`.
- Docs: `CLIENT_QUICKSTART.md` (a client sees the answer as a DM whose `nick` is the Stump's name), `COMMANDS.md`, README.
- Test: `web_fuzz` keeps at most 128 KB of each answer (`harness.KEEP`): `/codec2.wasm` (~300 KB) fetched whole ran the test's heap out now and then (`MemoryError`; it stopped LaBuche-Stump's beta.46 release build twice). Nothing changed in the Stump.
- Test: `mesh_room` -- a DM to a Stump: answered privately by BarKeep, every line; the how-to-reach line once in a while; nothing in a room; without BarKeep; the other Stump's answer in the thread with that Stump.

## 1.0.0-beta.46
- **A computer Stump restarts on an update by itself.** Found on the bench right after beta.45: `/admin` was a blank page on a laptop Stump, the log full of `request error: module 'settings' has no attribute 'awake'`. The Provisioner (`--pc`) had copied the new files into the Stump folder, but the Stump already running wasn't restarted (it is only when start-at-login is set up again, and only the one launchd/systemd runs). It ran on with the old modules in memory, and `/admin` -- loaded on first use -- came from the new files: a mix. Now the Provisioner touches `.updated` in the folder once every file is in place, and the running Stump, at its next heartbeat (5 s), restarts on the new files: under its supervisor as `/admin`'s restart does; run by hand (`python3 stump_pc.py`), it starts itself again in the same process. The log says `this folder was updated (the Provisioner): restarting on the new files`.
- `/admin`'s restart, on a Stump run by hand, starts it again too (it used to just stop: no supervisor to start it).
- `docs/PC.md`: updating a running Stump.
- Test: `pc_power_test` -- updated while running, supervised and by hand: it restarts on its own, and `/admin` opens after.

## 1.0.0-beta.45
- **A computer Stump survives the computer sleeping.** Found on the bench: a laptop Stump ran fine, then the laptop slept overnight; on every wake-up the supervisor (`stump_pc.py`) saw a heartbeat as old as the sleep (15 minutes) and restarted the Stump as "stopped responding". Each restart counted as a failed start, so the night ended in rescue starts and the 10-minute rest. The supervisor's watch loop comes round every 2 s; when it finds itself much later than that (`SLEPT_S`, 10 s), the whole computer was asleep, so the Stump gets a full `WATCHDOG_S` from waking to beat again (a real hang is still caught after that). The log says `the computer was asleep (N s): not a hang`.
- **A stop during the rescue rest no longer counts as one more failed start.** After six failed starts in a row, `keepalive.begin()` rested 600 s *before* saving the reset count, so stopping the Stump during that rest (the Stop file, the Provisioner) left the old count on disk: every following start rested again, and a Stump restarted within 10 minutes never came back (`localhost` blank). The state is now saved as not running before the rest.
- **Keep the computer awake, from `/admin`** (a computer Stump only): Stump settings → *This computer* → "Keep the computer awake while the Stump runs" (`PC_KEEP_AWAKE` in `config.py`, off by default, applied at once). macOS holds it with `caffeinate -i -s -w <pid>`, Linux with `systemd-inhibit` (idle and sleep), Windows with `SetThreadExecutionState`; the hold ends with the Stump's process, so a stopped Stump lets the computer sleep. The group's summary says whether it's held (`kept awake`, `may sleep`, or `awake asked for, not held`), and says so where nothing on the computer can hold it. The screen may still turn off; a laptop closed on battery still sleeps.
- `docs/PC.md`: `WATCHDOG_S` and sleep; `PC_KEEP_AWAKE`.
- Tests: `watchdog_test` (the computer sleeps 20 s with a 6 s watchdog: the same Stump runs on), `keepalive_boot` (stopped during the rest: the next start is normal, no second rest), `settings_test` (the toggle: shown, on and off at once, kept in `config.py`, the hold started and ended, a hold killed from outside reported, a computer that can't hold it).

## 1.0.0-beta.44
- **A restarted Stump's name shows at once** (no lingering `mesh-xxxx`). The beacon already carries the Stump's name and, since beta.43, arrives within seconds of a restart -- but `_known_name` resolved the name only from the LXMF *announce* (`router.peers`), a separate crypto-heavy broadcast on its own ~2-minute cycle, so a restarted peer kept showing as `nick@mesh-xxxx` until then. Now the name falls back to the beacon (`discovery._known`), so it resolves as soon as the beacon lands. (The LXMF announce is still what carries DM-delivery paths; this only changes how the *name* is shown.) Follows beta.43's re-introduction change. `CLIENT_QUICKSTART.md` unchanged (behavioural).
- Test: `chain_proto` -- a Stump's name comes from its beacon, not only its LXMF announce.

## 1.0.0-beta.43
- **A restarted Stump is re-introduced to the chain at once** (asked by the R36MAX team; chain hardening). A Stump that restarts while the others keep running forgot them all, but they still knew it, so nobody answered its beacon -- it named them `mesh-xxxx` for up to 2 minutes and listed them (`/stumps.json`, `/chain.json`) only at their half-hourly beacon. Now: the beacon's `caps` carry this boot (`"b"`, random so a clock-less CAM has one too); a Stump answers another's first beacon since it started, or the first of a new boot (`discovery.restarted`), not only one `rrc.is_stump` didn't know (the chat set that too, so a Stump known from `#wifi-mesh` traffic but never from a beacon was never answered); and an answer the once-a-minute limit holds back is owed, not dropped (`chain.take_owed`, from `example_node`'s beacon loop). Older Stumps ignore `"b"`; still at most one introduction a minute, so LoRa airtime is unchanged. It only helps when the Stumps that stayed up run it, so it belongs on every Stump, the CAMs included.
- Tests: `pc_three_stumps` step 7b (an end Stump restarts: it lists and names the others within 45 s), `lora_radio_test` (the owed answer comes once the minute is up, once).

## 1.0.0-beta.42
- **Arborist sets the radio** (the Heltec's host-controlled LoRa parameters): frequency, bandwidth, spreading factor, coding rate and TX power, in the Make and Settings forms when a Heltec is enabled. Written with the firmware's own `radio.save()` to `/radio.json` (applied to the bridge at boot, sent to the Heltec on connect) -- the same approach Settings uses for `config.py`. CAM-side only: the Heltec's own provisioning (EEPROM, `-w STATION`, Bluetooth) stays with the Provisioner (`rnodeconf`, GPL-3). The values default to the deployed mesh's (868.8 MHz / 125 kHz / SF8 / CR4/5 / 22 dBm) with a reminder they must match every Stump on the mesh. `final_firmware/docs/ARBORIST.md`.
- Tests: `arborist` -- radio written and read back on a real MicroPython board (via `radio.save`/`store`), the bounds enforced, and the form's show/hide and drop-downs in the page.

## 1.0.0-beta.41
- **The on-device browser flasher (HF-001) is retired**, replaced by Arborist. It served `/flash` over the Stump's plain-HTTP Wi-Fi, where Web Serial can't run (it needs a secure context), so it could never actually flash a board there; Arborist -- a page saved on the computer and opened from `file://` -- does. Removed: `flasher_ui.py`, the `/flash` and `/fw` routes and the Flash nav tile, and the flasher payload the Provisioner pushed to each card (`catalog.json`, `serial-check.html`, the board copy of `esptool-bundle.js` and its licence, the section READMEs) -- about 256 KB off every SD card. `esptool-bundle.js` stays in the repo as a build input for Arborist (`scripts/build_arborist.py`). `final_firmware/docs/ARBORIST.md`.
- Tests updated: `tools_sections`, `provisioner`, `pc_chain` no longer expect a flasher section.

## 1.0.0-beta.40
- **Arborist sets both passwords, independently** (parity with the Provisioner): the **admin** password (`AUTH_ADMIN_PASSWORD`, unlocks `/admin`) and the **BarKeep operator** password (`FSERVBOT_OP_PASSWORD`, lets an operator edit BarKeep's replies from chat). Before, Arborist had one field and copied it into both. Each is its own field now; blank means "leave it", and `/admin` falls back to BarKeep's password when the admin one is blank, exactly as the firmware does (`barkeep.py`). A new Stump must have at least one of the two set. `final_firmware/docs/ARBORIST.md`.
- Tests: `arborist` -- both passwords set apart, read back, and changed on their own, on a real MicroPython board and through the page.

## 1.0.0-beta.39
- **Arborist**: `Arborist-1.0.0-beta.39.html` on the release, one page saved on a computer and opened in Chrome or Edge (from the saved file, Web Serial needs no certificate). For a Freenove ESP32-S3-CAM on USB. Nothing to install. `final_firmware/docs/ARBORIST.md`.
  - **Make or update** (it began as a flasher): a new Stump (erases the board, MicroPython 1.28, the Stump compiled, its settings from a form) or an update that keeps its settings, its identity and its card. Each file's SHA-256 is checked on the board; a pulled cable never leaves half a module.
  - **Settings**: a Stump's settings read into the same form (name, admin password, Wi-Fi, the Heltec, features, look, access, the bot, a chain) and saved by the firmware's own `settings.py`, as `/admin` does; passwords are never shown (blank keeps them); `config.py.bak` kept; a copy saved on the computer, or one put back.
  - **Check**: restarts the Stump and listens to it start: its release, whether its pages came up, what went wrong; saved as a file to send.
  - Built by `scripts/build_arborist.py` (MicroPython's image pinned by its SHA-256), by CI on every change and attached to each release. CAM only: MicroPython and esptool-js keep the MIT/Apache licences; the Heltec's RNode (GPL-3) stays with the Provisioner.
- `docs/THREAT_MODEL.md`: holding the board is game over, by design (no eFuses, flash encryption or boot passphrase). `docs/ROADMAP.md`: step 5, pentest preparation.
- Tests: `arborist` (the uploader against a pretend board running MicroPython's Unix port; the page in Chromium, Web Serial piped to it).

## 1.0.0-beta.38
- **The billboard over LoRa** (asked by the FireFly team, for a LoRa Board tab): `/board [words]` (the newest notices, or those about the words, at most 5: `#<id> <title>`), `/board <id>` (the notice), `/post <title> | <body>` (`✓ #<id>` or why not), answered over LXMF like `/rooms`, and in the web chat too. The same rules as posting over Wi-Fi (title 80, body 600, the posting limit, the billboard switch); the short id is 6 digits of the notice's id. `CLIENT_QUICKSTART.md`, *Billboard*.
- **When LoRa is lost** -- this Stump's Heltec by any link, or the radio of a Stump along the chain -- its reconnect gets 2 minutes (`LORA_GRACE_S`) with nothing said. Past that, whoever writes to LoRa (`#lora-mesh`, a DM to someone on LoRa) is told, once a minute, that the radio has been down N minutes and their message will go out when it's back; it waits (nobody on LoRa is dropped as silent meanwhile), up to an hour (`LORA_KEEP_S`). `/hw.json` → `lora`: `up`, `connecting`, `down` with `down_s`, or `none`. A Stump whose radio goes announces it at once (its beacon: `rad`, and `rh`, it has one), so the chain knows.
- Tests: `board_lora`, `lora_down`.

## 1.0.0-beta.37
- **Pentest hardening** (step 3 of `docs/ROADMAP.md`); the picture for the pentesters and the client teams: `docs/THREAT_MODEL.md`.
  - **A forged Stump beacon is ignored.** The LXMF address in a `stump.node` beacon must be the beacon's own identity's: anyone could otherwise announce a beacon naming someone else's address and make a Stump take that person for a Stump.
  - **Profiles cross the chain only with the chain secret's mark** (an HMAC): coming in over a chain link wasn't proof, since UDP can look like it comes from the Stump above. Open chains: as before. With a secret, update every Stump of the chain together: older ones don't share profiles with this one.
  - **The Stump above proves the chain secret in its answer to a hello**; a plain answer (an older Stump) is taken, and `/admin` → Stump chain says to update it; one with another secret is ignored.
  - **A request body that isn't UTF-8 no longer ends a request with no answer** (found by the new web fuzz test: the chat, the billboard, profiles, every `/admin` form, plugin pages).
  - **msgpack refuses nesting deeper than 32** (the stack ran out first), and **LXMF refuses a payload of the wrong shape** as a parsing error (found by the new Reticulum fuzz test).
  - Passwords (admin, operator) and the chain's tags are compared in constant time.
  - **A pack can't replace the Stump's own code or a built-in plugin**, nor list files outside its folder (`tavern/../barkeep.py` was copied over the core).
- Tests: `web_fuzz` (every route, every method, paths that climb out, odd bodies and headers, a canary that must never be served), `urns_fuzz` (mutated and random packets, LXMF, msgpack bombs, an announce flood), more in `chain_proto` and `provisioner`.

## 1.0.0-beta.36
- **The chain, hardened** (step 2 of `docs/ROADMAP.md`).
  - **Only a good hello keeps a Stump in.** A Stump below another stayed in as long as it sent packets, whatever its hellos said: one whose chain secret changed, or a device that took over its address after it left, wasn't let go. Now packets are taken only while the hellos are good, and a hello with another secret puts that address out at once.
  - **Who was refused**, shown: `/chain.json` → `refused` and `/admin` → Stump chain say who tried to join with another secret, how often and when.
  - **Everyone in `#wifi-mesh` is listed on every Stump**, someone quiet included, as `nick@thatstump` and a DM contact: each Stump sends the others who's in its `#wifi-mesh`. Only who had spoken used to be. A names list for one room no longer drops the people the other room's list has.
  - **What each Stump is**, in its beacon (a sixth item, short: it goes over LoRa too): the chain protocol (2), its release, access mode, radio, storage and plugins. `/chain.json` → `stumps`, `/node.json` → `proto`; `/admin` says when another Stump is older ("update it") or newer. The groundwork for sharing storage along the chain.
  - **No "TX failed" warning on every announce** at the end of a chain: a link with nobody on it isn't a failure (seen on LaBuche).
  - **A bot's lines stay on its Stump** (seen on the bench: `BarKeep@LaBuche` was a DM contact on the Mac, and a DM to it got `⊖ BarKeep`). BarKeep's and the plugins' lines (the Tavern 1.1 too) are marked as a bot's and not sent to the other Stumps; in `/rrc/poll` they carry `"bot": true`.
  - `/names` showed people on a Stump met before its name was known as `nick@mesh-xxxx` until their next list: it follows the name now.
  - A Stump on a computer with two networks can sit in the middle of a chain: `PC_STA_IP` (docs/PC.md).
  - A computer Stump stopped (SIGTERM) now and then printed a traceback from asyncio cancelling its tasks: once a stop is asked for, that's the stop, not a crash.
- Tests: `pc_three_stumps` (three Stumps in a line: rooms and DMs across one and two hops, a DM to a Stump, `#main` staying home, names once everywhere, nothing echoed or doubled; the middle Stump restarting; the wrong secret then the right one), `chain_proto`.

## 1.0.0-beta.35
- **Cleanup review** (step 1 of `docs/ROADMAP.md`): no change in what a Stump does, less of it to carry.
  - **The CAM gets its modules compiled.** With mpy-cross 1.28 on the computer (`pip3 install mpy-cross==1.28.0.post2`), the Provisioner compiles each module (`.mpy`) before copying it, once it has checked the board's MicroPython loads that version: 1,429 KB of source goes as 589 KB, and the board compiles nothing at boot (on the Unix port: imports about 10x faster, about 300 KB less memory needed to start). `main.py` and `config.py` stay source; plugins and packs go compiled too. Without mpy-cross, or with an older MicroPython on the board, source, as before, and it says so.
  - **/admin loads when it's opened.** The router was one 1,350-line chain of 52 routes: each is its own handler now, matched in the same order. Everything under `/admin` (the pages and their forms, ~1,500 lines) is in `admin_web.py`, loaded the first time someone opens `/admin`: a Stump nobody administers doesn't hold it.
  - **Every settings file is saved whole or not at all** (`store.py`). About a dozen saves wrote their file in place -- the identity table, the layout, the radio and propagation settings, the theme, the plugins' states: a reset in the middle left a truncated file, read back as nothing saved, so they quietly went back to their defaults.
  - **One copy of the text helpers** (`webtext.py`: HTML escaping, URL encoding). There were seven, and two had drifted into bugs: one escaper left apostrophes, and the language switcher wrote accented addresses wrong.
  - Removed: the awaiting-slot upload (HF-002: nothing could open a slot; `/upload` still answers `"slot": false`), functions and imports nothing used, 12 translation keys, 2 config keys. The browser flasher (HF-001) is loaded only when `/flash` is opened; bringing it back is on the roadmap.
  - The root `CLIENT_QUICKSTART.md`, `HIDDEN_FEATURES.md` and `REF.md` point to `final_firmware/docs/` (REF.md had drifted from COMMANDS.md).
- Tests: `store_atomic`, `admin_lazy`, `provisioner_mpy`; `MPY_CROSS=... tests/run.py` runs the tests on the compiled firmware, and CI does for a set of them.

## 1.0.0-beta.34
- **Password checks can't hold up the CAM** (the fourth and last step of hardening for the pentest).
  - A profile's password is hashed 2,000 times (on purpose: guessing is slow): on the CAM, a long moment in which the Stump served nobody. It now gives everyone else a turn every 250 rounds -- the same hash, the same strength, nothing frozen. Registering, logging in, changing a password and the admin's reset all hash that way.
  - All password checks together, from everyone, are budgeted: 10 every 10 s. Past that, "try again in a moment", and no hashing is done for it -- several devices, each within its own limit, can't keep the CAM hashing.
  - Wrong passwords lock the account as well as the address: five wrong ones for a profile, from any number of devices, lock that profile for a while (30 s, doubling, at most 15 min). Before, each device had its own five.
  - `/admin`: wrong passwords from all addresses together lock the door too, loosely (20), so guessing from many devices slows down without one stranger easily locking the owner out; the computer the Stump runs on is never counted.
- Test: `password_cost` (the board's MicroPython: the same answers, turns given while hashing, the budget, the account and door locks, register and log in on the web).

## 1.0.0-beta.33
- **Lighter on the ESP32** (reported: LaBuche getting a tad slow). Measured on the board's MicroPython, then cut:
  - the connections' deadlines (beta.24) made a timer task on every read and write -- about 14x the read itself, and garbage for the collector, hundreds of times per upload. Now each connection has one deadline, moved forward as bytes move, and one reaper checks them once a second; a read is a plain await again (14 µs → under 8 µs per read here, the timer task gone). The same limits as before, the same tests;
  - deciding whether an address belongs to a feature that's off (asked on every request) re-parsed FEATURES each time: worked out once now (55 µs → 1.3 µs);
  - a visitor ID was made (random bytes and a cookie string) on every request from a client without cookies -- an app polling every 2 s -- and thrown away: only pages carry it now;
  - 97 debug log lines in Reticulum's code (announces, links, transport) built their text even with debug logging off: they're skipped unless it's on.
- `/hello` is limited per address (a few, then one every 2 s): each one is a signature, so nobody keeps the CAM busy signing.

## 1.0.0-beta.32
- **`/hello`: the greeting as one exchange, inside the app's own connect handshake** -- nothing for the person to press. Both sides prove who they are: `GET /hello` gives this Stump's identity, a one-time code and the Stump's signature over it (the app checks it's the real Stump); `POST /hello` gives the app's public key, its signature over the code, the person's name and their route. A wrong or replayed signature is refused.
- **The person's route**: `"prefer": "wifi"` or `"lora"`, a toggle in the app like the Stump's USB / Bluetooth switch. With `"lora"`, DMs go over LoRa even while the phone is on the Stump's Wi-Fi too; listed once either way.
- `identity_matrix` covers it: the Stump's signature checked, a bad and a replayed signature refused, the greeting done in one exchange, DMs following the chosen route and nowhere else.

## 1.0.0-beta.31
- **One person, one name** (reported: the same FireFly was `guest-f678`, `guest-f678@macOS1`, `RodCell` and `RodCell@macOS1`, and only `guest-f678` reached it). The Stump had added its own layer over Reticulum -- Wi-Fi guests known by their address, and names passed between Stumps -- with no identity in it.
  - **The greeting.** An app (a client keeping no cookie, like FireFly) says who it is before it chats: `/auth`, then `/nick <its name>` (or `/auth` alone when its LoRa announce already gave the name). Until then it reads nothing, writes nothing, and isn't listed anywhere; `/rrc/poll` and `/rrc/send` say `"hello": "required"` / `"done"`. Browsers keep the cookie and stay guests. `APP_HELLO = False` in config.py switches it off.
  - **A chosen name beats a guest's**, in both directions: proven over Wi-Fi, an app whose LoRa announce says `RodCell` is `RodCell`, not `guest-f678`; an app that `/nick`s while its LoRa side is unnamed names it. Renamed in the app (a new announce): both sides follow at once. Neither side chose yet: neither name spreads.
  - **The chain lists identities.** `[NAMES] RodCell~3f2a91bc ...`: someone here isn't listed again as `RodCell@macOS1`; someone on two other Stumps is listed once, where heard last; someone gone from another Stump's list is gone here at once (not an hour later). Older Stumps' plain lists still work.
- Test: `identity_matrix` -- browsers, an app, the same phone on LoRa, another LoRa user and two other Stumps, through the web server: the greeting, the names, DMs in every direction and rooms, each checked to arrive where it should and nowhere else.

## 1.0.0-beta.30
- An update (`--upload-app`, or Check or fix → Re-upload) keeps a CAM's settings more safely. Reported: after updates LaBuche had no `#wifi-mesh` and no About page -- what the firmware folder's own config.py sets (chain off, About off). An update kept the board's config.py unless one check said it had none; that check, during resets, was all that stood between a node's settings and the folder's. Now:
  - before every update the board's config.py is copied to this computer (`~/.provisioner/config_backups`, the newest 30);
  - the check looks for `/config.py` by its full path, and tells "a fresh board" from "a board a Stump ran on whose config.py is gone" (its identity, its start record...);
  - for the second, the folder's config.py is never copied without a yes: the last saved copy is offered first, and what each one sets is shown (name, chain, features).
- The Provisioner can erase a board's whole flash, a CAM's or a Heltec's, to start it over (corrupted firmware, a board going to another use): Check or fix → **Erase a board's flash**, or `--erase-flash PORT`. It asks which board and for a yes, says what goes and how to set it up again, copies a CAM's config.py to this computer first, and walks through download mode if the board won't answer. A CAM's SD card isn't touched.

## 1.0.0-beta.29
- Fixed: copying the Tools page's files to a CAM's card (up to half an hour) could be cut by the board's watchdog ("unexpected read during raw paste: b'E'"). The Provisioner now gives the board's watchdog 2 hours before every USB command, so no maintenance session depends on the Stump having disarmed its own. Left at the console afterwards, the board starts the Stump by itself when the 2 hours are up. The Stump's own disarm uses the same 2 hours (a huge value could come out short in the chip's settings).

## 1.0.0-beta.28
- Fixed (beta.26's rescue start): uploading to a CAM could leave it in a rescue start -- no chain (so no `#wifi-mesh`), no radio, no plugins -- and pressing RST didn't bring it back. An upload's own stops (a Ctrl+C while the Stump was still starting) and a reset during it counted as failed starts. Now a stop at the console (Ctrl+C, the Provisioner) clears the count: the next start is always a normal one. A Stump already in a rescue start: /admin → **Start normally**.

## 1.0.0-beta.27
- Fixed (beta.26's watchdog): a CAM could reset in the middle of `--upload-app`. mpremote sends Ctrl+C twice; the second could land while the Stump was stopping and skip disarming the watchdog, which then reset the board a minute later. The watchdog is now disarmed first, with Ctrl+C held off while it is, and again by boot_common if a Ctrl+C gets past the node. Every start's log says why the board last reset (`[watchdog] last reset: ...`).
- **Uploads, fair to everyone** (the third step of hardening for the pentest). One visitor (one address) can't hog a Stump:
  - one upload at a time (`429`, `one_at_a_time`);
  - 30 files an hour (`too_many`; `UPLOAD_FILES_PER_HOUR`);
  - a quarter of the card's room a day (`quota`; `UPLOAD_DAY_SHARE`) -- on a free Stump nothing else stopped someone pushing everyone's files out;
  - on the CAM, all uploads together at most 300 KB/s, split evenly between those going on (`UPLOAD_MAX_BPS`; no cap on a computer): one video can't take all the Wi-Fi's airtime;
  - a file shared in the last hour is never pushed out to make room for someone else's (`FRESH_S`) -- a clock restarted at 2000 after a power cut doesn't make every file "fresh";
  - a name already shared isn't replaced any more: the new file is kept as `name (2).ext`. Before, anyone could swap someone's shared file for another.
- Test: `upload_fair` (the board's MicroPython: each limit, the eviction rule, the speed cap and its split).
- The supervisor's stop on a computer: a stop landing in a weak-reference clean-up is retried too (it was swallowed, now and then, on macOS).

## 1.0.0-beta.26
- **A Stump that stops responding restarts by itself** (keepalive.py; the second step of hardening for the pentest). On the CAM, the chip's watchdog: if the Stump's loop stops for 60 s, the board resets. On a computer, the Stump writes a heartbeat file every 5 s and its supervisor (the Start file, start at login) restarts it when the heartbeat stops for 60 s. `WATCHDOG_S` in config.py (0: off). A stop at the console (Ctrl+C, the Provisioner) switches the CAM's watchdog off, so an upload isn't cut short.
- **A Stump that keeps failing to start comes back in a rescue start** instead of sitting idle: after 3 failed starts in a row (a crash, a hang, a power cut before it was up for 2 minutes; a planned restart doesn't count), it starts without its radio, the chain or plugins -- its Wi-Fi, the pages and /admin. /admin says what failed, with **Start normally**; it tries a normal start by itself after 30 minutes, and if even the rescue start keeps failing, it rests 10 minutes and starts from scratch. It never gives up -- except on a missing module (an incomplete upload), where it stays idle at the console with the message, as before. In a rescue start, /admin takes the password from config.py (the identity plugin isn't loaded).
- /admin notes it when the watchdog restarted the Stump. `/node.json` has `"mode": "normal"` or `"rescue"`.
- The longest the Stump's loop was kept from serving anyone is measured; past 2 s it's logged.
- New tests: `keepalive_boot` (the board's MicroPython: failed starts, rescue, its retry, the rest after failing rescue starts, /admin's notice) and `pc_watchdog` (the real supervisor: a frozen Stump restarted, three failed starts giving a rescue start, /admin logged in without plugins, "Start normally").

## 1.0.0-beta.25
- **One phone on Wi-Fi and LoRa is one person.** Reported: a FireFly known on LoRa as `rods-cell` (its radio then off) came back over Wi-Fi as `RodRod`; the Stump listed both, the chat page kept two threads, and `rods-cell` stayed for an hour. Now, once the phone proves its identity over Wi-Fi (`/auth`, FireFly's one button) and the Stump knows that identity on LoRa: the LoRa entry takes the Wi-Fi nick and is listed once; DMs still waiting to go out over LoRa are delivered over Wi-Fi; DMs to that nick go over Wi-Fi while the phone is there, over LoRa again once it's gone. Only `/auth` links them: a claim without the identity's signature could take someone else's messages.
- A LoRa contact announcing a new name (a new nickname in its app) is renamed. Before, the Stump kept the first name it heard until the contact went quiet for an hour.
- The chat page folds a renamed person's thread into their new name, instead of keeping a second conversation. `/rrc/poll` carries `"renamed": {"old": "new"}` (the last 30 minutes) for clients that keep conversations by nick.
- For FireFly: `/auth` over Wi-Fi as soon as you connect to a Stump, so you're one person there (CLIENT_QUICKSTART, Identity model).

## 1.0.0-beta.24
- **Nobody can hold a Stump by keeping connections open** (conn.py; the first step of hardening for the pentest). Before, the web server waited forever on every read and write, and the CAM has about ten sockets: a few phones opening connections and sending a byte a minute locked every visitor out. Now:
  - at most 10 connections at once on the CAM (128 on a computer), 6 from one address (16 on a computer; the computer itself isn't limited); past that, a quick `503` with `Retry-After: 2` instead of a socket held;
  - the request line and headers must arrive within 10 s -- 3 s once the Stump is three quarters full, so held connections go first -- each line at most 4 KB, 16 KB in all (a line that never ends can't fill the CAM's memory);
  - a body where nothing arrives for 15 s ends (a half upload is discarded, as before), and past 30 s a body must average 512 bytes/s;
  - an answer the client doesn't take within 15 s (a download nobody reads) lets the connection go;
  - a refused upload is read for at most 2 MB before the answer goes out and the connection closes (it used to be read to its end: minutes of Wi-Fi airtime for a claimed 2 GB).
  The log sums up what was refused or let go once a minute at most, never a line per connection. Each limit can be changed in config.py (`CONN_MAX`, `CONN_PER_CLIENT`, `CONN_HEAD_S`, `CONN_HEAD_BUSY_S`, `CONN_IDLE_S`, `CONN_MIN_BPS`, `CONN_RATE_GRACE_S`). New tests: `conn_limits` (the board's MicroPython) and `pc_conn` (real sockets: a Stump filled with silent connections answers again within seconds; a dribbled request, an endless header line and a stalled upload are each let go, nothing kept).
- For clients: a `503` with `Retry-After` means "full right now": wait that long and try again. Keep at most a few connections open to one Stump at a time.

## 1.0.0-beta.23
- A computer Stump's storage (`PC_STORAGE_MB`, the size of its sd/ folder "card") is now a setting you see: the Provisioner asks it for a new Stump (showing the disk's free space; a smaller default on a nearly full disk), `/admin` → Stump settings → Storage changes it at once (with what's in it and what the disk has free), and an update (`--pc` on an existing Stump) asks "Change something?": `s` changes only the storage, `a` all the questions, Enter nothing -- the rest of config.py, the identity, history and files are kept either way.
- Sizes of 1 GB and more read in GB in `/admin` and the Files page.

## 1.0.0-beta.22
- Fixed: a computer Stump asked to stop (Stop file, launchctl, systemctl) could, now and then, carry on: the stop landed while Python was tidying up an object and was swallowed. It now tries again a moment later.
- `/admin` → Plugins on a computer no longer shows "0 B RAM" for each plugin: a computer has no free-RAM figure to measure it with, so it's left out.
- Fixed: a computer Stump (macOS, Windows, Linux) refused every upload with 507 ("Card capacity exceeded"), whatever its size. Its free-space check ended up calling itself, and the "card" read as missing -- full, on purpose. Free disk space is now read straight from the system (and if it can't be, the `PC_STORAGE_MB` quota is used and the log says so). New test: `pc_storage` (an upload kept, one past 75% of `PC_STORAGE_MB` refused, the oldest shared file making room).

## 1.0.0-beta.21
- Access modes, as meant: **open** — everyone reads and writes; **hybrid** — everyone reads every room (joins it, sees its lines and who's there) and writes in `#main`, while writing anywhere else (other rooms, private messages) takes a verified identity (`/auth`, one button in FireFly; a FireFly over LoRa counts, its messages are signed); **mandatory** (closed) — reading takes it too: before `/auth` a web client's `/rrc/poll` comes back with no messages, private messages or names, and the topic says why. (beta.19's hybrid kept people out of rooms other than `#main`/`#wifi-mesh` instead.)
- `#lora-mesh` on a computer with its own Heltec is no longer locked to LoRa users and invitees by default -- that refused a computer's own browser on an `open` Stump. It follows the access mode like every room; `LORA_MESH_LOCKED = True` in config.py brings the lock back.
- Fixed (from beta.19's visitor IDs): the person on the computer a Stump
  runs on was no longer recognized as "this computer" once their browser
  had a visitor ID, so `#lora-mesh` refused them ("il faut une radio LoRa,
  ou une invitation") and `/lora invite` said owner only. Both now map the
  visitor ID back to its address.
- **Finding a Stump on a Wi-Fi without knowing its address.** The router
  can change a Stump's address (a laptop went .172 → .175 → .176), and an
  app that kept the old one talked to nothing. Now every Stump, the CAM
  and a computer alike:
  - answers `STUMP?` on UDP port 4244 (a broadcast finds them all) with
    its name, identity and current web addresses;
  - puts those addresses in its `stump.node` beacon (a fifth item; older
    readers take the first four), and announces again soon after its
    address changes;
  - lists the other Stumps it heard, with their addresses, at
    `/stumps.json`.
  Apps keep a Stump by its identity and find its address again when it
  moves -- no router settings, nothing to type. (CLIENT_QUICKSTART:
  *Finding a Stump on a Wi-Fi*.)

## 1.0.0-beta.20
- A message arriving twice (the sender didn't get our delivery proof and
  tried again) is proved again, so the sender stops resending, and still
  delivered once. A "too fast" refusal is written in the log (who, which
  line). Stopping the Stump while Bluetooth disconnects no longer prints
  a traceback.

## 1.0.0-beta.19
- **Registered profiles** for people without a FireFly (`/profile`, from
  the chat's pop-up and the Files page): a nick and a password; logged
  in from any browser, the nick (reserved), the credits and the private
  messages follow, on every Stump of the chain (synced over the chain
  link only; credits counted per Stump and merged). `/admin` → System →
  *Registered profiles*: reset a password (there's no email), delete.
- **No longer known by IP in a browser:** a visitor ID cookie keeps a
  browser's nick, room, messages and credits when its address changes
  (what the address had moves to it once). Clients that keep no cookies
  -- the FireFly app -- stay known by their address, so its one-button
  `/auth` works as before. Limits and lockouts still count per address.
- **Hybrid access, as meant:** anyone is in, and writes in, `#main` and
  `#wifi-mesh`; every other room (`#lora-mesh`, rooms people make) and
  private messages take a verified identity -- the `/auth` challenge only
  a FireFly answers. A FireFly over LoRa counts as verified (its messages
  are signed). Before, hybrid gated every post, `#main` included.
  Switching to hybrid sends the unverified back to `#main`.
- **/admin → System → Stump settings: the Provisioner's answers, changed
  in place** -- the name, the bot's name and auto-reply, file sharing
  (free, standard economy, custom weights), who may enter (open, hybrid,
  mandatory), the features, and the librarian's firefly bubble (on or
  off). Written into config.py (an update keeps them); the economy, the
  access mode, the features and the firefly apply at once, the rest with
  *Restart the Stump* -- at the bottom of /admin, beside *Turn off* on a
  computer (the board resets; a computer Stump starts again by itself). Nothing is reinstalled or minted again: the
  identity, history, files, credits and radio stay as they are. On the
  CAM and on a computer alike.

## 1.0.0-beta.18
- **Fixed: Bluetooth needed the USB → Bluetooth switch after every start
  or update.** The Heltec's name typed in the Provisioner ("EA69", as its
  screen shows) was matched exactly against what it advertises ("RNode
  EA69"), so a Stump starting on Bluetooth never found it; the switch
  looks for any RNode, which is why it "fixed" it. Now "EA69", "RNode
  EA69", "rnode-ea69" or its address all find it, and the Provisioner
  keeps a typed "EA69" as "RNode EA69".
- **/admin → Radio: one folded *Transport* section** ("Transport: USB",
  "Bluetooth" or "Wi-Fi") holds the USB ⇄ Bluetooth switch, *Reconnect
  the radio* (moved from the top of the card) and the Bluetooth buttons.
- **Fixed: after an update the Heltec only came back over Bluetooth once
  it was rebooted by hand.** A Heltec that still counts a Bluetooth device
  as connected (the Stump that just stopped, or the Mac itself) stops
  advertising and ignores everything on its USB -- the reset command
  beta.17 sent over the cable included. Now:
  - on the cable, it's restarted through the USB chip's reset line, as
    flashing tools do: the reset button, by wire, whatever the firmware
    is doing (at start-up, during a Bluetooth fresh start, and when it
    isn't seen for a few scans);
  - on USB too: a Heltec that's there but answers nothing twice in a row
    is restarted the same way;
  - on macOS, cable or not: a Heltec the Mac is still connected to (so
    no scan can see it) is found in the Mac's list of connected devices
    and used through that link;
  - never a stream of restarts: at most one every 5 minutes (start-up
    aside).
- **"Peer removed pairing information"** (the Heltec forgot this
  computer -- its pairings cleared or its flash erased -- while the Mac
  kept its own, so macOS refused to connect or pair again): the radio
  line says what happened and what to do, and *Pair this computer* now
  removes the Mac's old pairing itself, then pairs afresh. (Before, it
  had to be forgotten in System Settings by hand.)

## 1.0.0-beta.17
- **Fixed: an already paired Heltec stayed silent over Bluetooth after an
  update, until /admin's switch went USB → Bluetooth** -- with or without
  the cable. beta.16 copied only the switch's USB half (a Heltec
  restart, which needs the cable). Now, cable or not: the link is
  encrypted with the saved pairing before the Stump listens on it (what
  left answers unheard); still silent twice in a row, this computer's
  Bluetooth side is started over from nothing, as the switch did;
  stopping or updating the Stump disconnects properly, so the Heltec is
  free for the next start. With the cable, the Heltec is also restarted
  (the RNode's reset command, more reliable than opening the port), and
  when it hasn't been seen over Bluetooth for a few scans.

## 1.0.0-beta.16
- **Bluetooth settles on its own after a restart.** After an update or a
  restart it could stay flaky until /admin's switch went USB → Bluetooth
  -- which restarts the Heltec through its USB port. The Stump now does
  that by itself when the Heltec is also on USB (charging): at start-up,
  and when Bluetooth stays silent twice in a row; never during a working
  Bluetooth session. Without the cable, reconnecting and backing off as
  before.

## 1.0.0-beta.15
- **Fixed: `#lora-mesh` from a second Stump waited for the first to
  speak.** A Stump only pushed to the Stumps it held as peers, and lost
  one when the route to it was gone -- every route through the radio is
  dropped when the radio blinks offline. A message from the other Stump
  brought it back, so writing on the first Stump "unblocked" it. Now a
  Stump with its radio online keeps every Stump whose keys it knows as a
  `#lora-mesh` destination, route or not (a radio send needs none), all
  Stumps get `#lora-mesh` the radio-first way, and how far each has been
  sent is kept: lines written while one was away (the last 5 minutes) go
  when it's back, never twice.
- **`/names` in `#lora-mesh` shows everyone in it, on every Stump**: each
  Stump tells the others who's in its `#lora-mesh` (a `[NAMES]` line,
  when it changes and every 10 minutes); the other Stumps' people show as
  `nick@stump` (also in `/rooms`' counts) and are DM contacts.
- Fixed: a chained Stump first heard over the radio, before its
  introduction, stayed out of `#wifi-mesh` and kept a `mesh-xxxx` name;
  it now moves to `#wifi-mesh`, and takes its name once it's known.

## 1.0.0-beta.14
- The same firmware as 1.0.0-beta.13, with the docs brought up to it: the README (the three rooms, the
  chain as a tree, the computer's Heltec on USB or Bluetooth, on/off,
  the /admin layout, tested so far), the client guide (what changed for
  clients since beta.4, and not to title messages `#lora-mesh` or
  `#wifi-mesh`), PC.md (Bluetooth from /admin, Start/Stop), the field
  checklist, RELEASE.md, tests/README.md, and engineering notes on the
  Heltec, its Bluetooth and the rooms between Stumps.

## 1.0.0-beta.13
- **Bluetooth pairing that works with the RNode firmware as it is** (read
  from its source):
  - The Stump no longer occupies the Heltec: a Bluetooth connection that
    goes nowhere made it count as "connected", so it ignored pairing mode
    and hid from other devices (the on/off dance). Failed attempts now
    back off, up to 30 s.
  - Writes go WITH a reply, as the RNode's channel requires: that is what
    makes the computer encrypt the link (or ask the code the first time).
    Without it the Heltec never answered after pairing -- the failed
    handshake.
  - Pair this computer: the code comes over USB when pairing starts, a
    35 s countdown matches the Heltec's window, and once it answers over
    Bluetooth the Stump switches by itself.
  - **Forget paired devices** (`CMD_BT_UNPAIR`), for a Heltec paired with
    something else before.
  - On Bluetooth, *Pair a device* gives way to a note: this computer
    holds the Heltec's one connection; switch to USB to pair a phone.

## 1.0.0-beta.12
- **Fixed: lines from another Stump showed as `<Stump> <nick> text`**
  instead of `nick@Stump` when that Stump's beacon hadn't been heard
  (over the radio, no chain). A batch titled `#lora-mesh` or
  `#wifi-mesh`, or a DM line, now marks its sender as a Stump wherever it
  comes from -- and this Stump introduces itself back.
- **Pair this computer** (`/admin` → Radio → Bluetooth, on USB): pairing
  mode over USB, a Bluetooth attempt from the computer, and the code the
  Heltec gives shown in /admin -- no boot button to hold. Then switch to
  Bluetooth. The USB "still there?" check pauses meanwhile (reopening the
  port would restart the Heltec mid-pairing).
- **Bluetooth handshake after pairing:** a short wait after connecting,
  a longer handshake (asked twice), and when the link is up but silent
  (macOS drops a subscription made before the bond) it's started over,
  as reference RNS does.
- `/admin` shows the Heltec's last answers under *Not working?*, to find
  out what it does while pairing.

## 1.0.0-beta.11
- **USB ⇄ Bluetooth switch in /admin** (Radio → Bluetooth), for the
  Heltec on a computer: live, no restart -- the old link is closed
  properly (radio off), the new one comes up with the same radio
  settings, and the choice is kept (the USB port too). Bluetooth support
  for Python is installed on the spot if it's missing.
- **Pairing fixed:** *Pair a device* turns Bluetooth on first (pairing
  mode did nothing while it was off), and says the truth: the code shows
  when the phone or computer asks for it (and on the Heltec's screen).
  To pair the computer itself: USB, *Pair a device*, then switch to
  Bluetooth.
- **The Provisioner no longer explains pairing**: it points to /admin,
  and installs Bluetooth support whenever there's a Heltec.

## 1.0.0-beta.10
- **Pair the Heltec over Bluetooth from /admin, no terminal.** `/admin`
  → Radio → Bluetooth: *Pair a device* puts the Heltec in pairing mode
  and shows the code it gives, big, to type on the phone or computer;
  *Turn on* / *Turn off*. Over USB on a computer, or over Wi-Fi to a
  CAM's Heltec. The code is shown to the admin only (`/admin/bt.json`),
  never in the public status.
- **A Bluetooth Heltec plugged in to charge stays on Bluetooth**: an
  update no longer offers to use it over USB (opening its port restarts
  it and drops its Bluetooth link). `/admin` explains the one-connection
  rule and the charging case under *Not working?*.

## 1.0.0-beta.9
- **#lora-mesh works with no Wi-Fi at all.** A computer Stump off Wi-Fi
  (its Heltec on USB) only learned the other Stumps were Stumps from
  their half-hourly beacon, so `#lora-mesh` had nobody to send to for up
  to 30 minutes. Now a Stump announces itself as soon as its radio
  connects, and answers a Stump it hears for the first time with its own
  beacon -- they meet within seconds, over the radio alone. Tested with
  two computer Stumps whose simulated Heltecs talk to each other.

## 1.0.0-beta.8
- **A CAM finds another Stump when its own goes away.** Its Stump above
  gone for a minute, a CAM looks for another Stump of the same chain in
  range and joins the strongest -- never one below itself (a circle),
  another chain, or one cut off from the first Stump. Nothing fits: it
  tries the one it was set up with again, and looks again every 3
  minutes. `/chain.json` gains `chain` (an id from the secret), `path`
  and `fallback`. A new Stump can join any Stump in range: a tree.
- **/admin, redone:** tiles at the top (radio, chain, people in the
  chat, files) that open their section; the sections in groups (On the
  air, Content, Look, System), each a card that folds open with a short
  status by its title; the radio card leads with the live readout and
  keeps the settings folded. Open cards stay open on that device.
- **The librarian is a firefly** in the corner of the Files, Billboard
  and home pages (like a help chat): an ASCII firefly that glows
  and drifts, pulsing faster while it thinks (still with reduced motion), opening into a chat
  with BarKeep and suggestions to tap. Long names wrap inside their
  bubble. On phones the Files list gives names the whole width.
- **Sort is one ⇅** on Files and Billboard, the same in every language;
  the choices open translated.

## 1.0.0-beta.7
- **#lora-mesh goes over the radio between Stumps.** A Stump with its
  Heltec online sends its `#lora-mesh` lines to the other Stumps over
  the radio only -- one packet each, cut to fit -- even when Wi-Fi
  reaches them too. Before, an open link over the chain carried them,
  so the Heltec never transmitted. Without a radio, they still go over
  the chain. `#wifi-mesh` now stays on Wi-Fi (a short batch used to go
  out on the radio as well); `#main` stays on its Stump.
- **Turn a computer Stump off from /admin.** It runs in the background,
  so stopping it took a terminal command. Now `/admin` → *This Stump on
  the computer* → **Turn off this Stump** (it asks first): the radio off,
  then a clean exit that the login service doesn't restart. `--pc` puts
  **Start Stump** and **Stop Stump** in the Stump folder to double-click
  (`.command` on macOS, `.bat` on Windows, `.sh` on Linux); Start opens
  the page once it's up. (`POST /admin/poweroff`; from the computer
  itself, no password.)
- **The Heltec on USB, lost and found by itself.** Unplugged, the radio
  stayed "online" (macOS reads just go quiet) until the Provisioner was
  run again. Now the port is looked at every second and a quiet Heltec
  is asked "are you an RNode?" every 10 s: gone, or no answer, and the
  dot turns red with why. Plugged back in -- even under another port
  name -- it reconnects by itself (handshake, radio set-up).
- **/admin → Radio → Reconnect the radio**: the handshake again, now
  (`POST /admin/radioretry`).
- **The radio off when the Stump stops**: the Heltec's radio is switched
  off and told the computer left (as reference RNS does), so it doesn't
  carry on alone. Stopping the login service (launchctl, systemd) now
  reaches the Stump itself, which does this first.
- **A Heltec over Bluetooth on a computer** (`RNodeBLEInterface`): the
  RNode firmware's Bluetooth serial link, no cable. The Provisioner
  offers it (and installs `bleak`), with the pairing steps
  (`rnodeconf --bluetooth-on`, `--bluetooth-pair`); an update offers it
  when the USB Heltec isn't plugged in. Lost and found by itself like
  USB. Tested against a simulated RNode, not yet on hardware.

## 1.0.0-beta.6
- **#lora-mesh crosses between Stumps.** A line in `#lora-mesh` stayed
  on the Stump it was written on: only LoRa users who had messaged that
  Stump got it, so with none, nothing reached the Heltec. Now each Stump
  sends its own people's `#lora-mesh` lines to the other Stumps it
  knows -- on the chain, or heard over LoRa -- and they show in their
  `#lora-mesh` as `<nick@stump>`, once. A short batch to a Stump one hop
  away goes out on every interface, the Heltec included. `#wifi-mesh`
  is unchanged.

## 1.0.0-beta.5
- **Is the radio really there?** A Heltec on USB is now asked "are you an
  RNode?" (the RNode handshake) and its firmware before the Stump calls
  it online; one that doesn't answer is an error that says what to
  check (the RNode firmware, another program on the port). Its reports
  are read: RSSI and SNR of the last packet, noise floor, channel load,
  airtime, battery, packets in and out, the settings it confirmed.
  `GET /hw.json` gives all of it; `/admin` shows it live (every 2 s).
- **Your profile, at the foot of the chat's column**: the radio's status
  dot and your nickname. Click it for the details in a pop-up: your
  nickname (change it there), this Stump (name, LXMF address) and its
  radio. The dot: green online, white no radio, glowing orange
  connecting, red trouble; the line under it gives the port and device
  name (to tell boards apart on a hub), the RNode firmware, or what's
  wrong. The column runs to the bottom of the window; the input line
  sits under the chat only. The navigation bar no longer shows who you
  are or the dot.
- **Change the radio from the chat.** Click Radio in the pop-up and it
  opens on the settings: frequency (any value, e.g. 917.112; 915, 868,
  869.525 and 433 suggested), bandwidth, spreading factor, coding rate,
  TX power. Apply sends them to the Heltec at once and saves them. Only
  the person on the computer, or someone logged in to `/admin`, can
  apply (`POST /admin/radio.json`); others see them greyed out.
  `/admin` has a switch to take the settings out of the chat (then the
  pop-up shows the radio's state only, and `/admin` is where they
  change).
- **#lora-mesh is the radio's room on a computer with its own Heltec**:
  people on LoRa are always in; on the web, the computer's owner and
  whom they invite (`/lora invite <nick>`, `/lora uninvite <nick>`,
  `/lora`); it never reaches other Stumps over the chain.
  `LORA_MESH_LOCKED = False` in config.py opens it.
- **Adding a USB Heltec copies the mesh's radio settings** from the
  Stump this computer joins (`/node.json` now has `radio`), instead of
  the defaults, which may not match the mesh.
- Fixed: without pyserial in the Python running the Provisioner, a
  Heltec plugged in by USB wasn't recognised ("none is plugged in").
- **A computer Stump follows the Stump it joins when its address
  moves.** On a shared Wi-Fi a CAM's address comes from the router and
  can change when it restarts, which cut the chain until the
  Provisioner was run again. Now the computer Stump remembers who the
  Stump above is (its LXMF address); when it goes quiet for 90 s, it
  looks for it on the network, rejoins it at its new address, and keeps
  that address (`chain_parent.json`). Tested end to end: two computer
  Stumps, the first restarted at another address.
- **A Stump that moved on the home Wi-Fi is found.** A CAM gets its
  address there from the router, and it can change when it restarts;
  the Provisioner kept trying the old one. Now, when the Stump a
  computer joins doesn't answer on a shared Wi-Fi, it looks for Stumps
  on that network (a few seconds), shows them, and offers to join the
  one it finds at its new address (saved in the computer Stump's
  `config.py`). The wizard's "On the Wi-Fi it's on now" also lists the
  Stumps it finds instead of asking you to type an address. "No route to
  host" on macOS now says it's most often that, before Local Network.
- **macOS: Wi-Fi names and the local network.** macOS hides the names
  of the networks around from the Terminal unless it has Location
  access, and never asks for it: the Wi-Fi picker now lists the networks
  this Mac has joined before instead of nothing. And when macOS stops
  the Terminal reaching the local network ("No route to host", though a
  browser works), the chain check says so and where to allow it
  (Privacy & Security > Local Network: Python). A Stump that doesn't answer on a
  shared Wi-Fi gets "its address may have changed" and `--get-ip`, not
  advice about its own Wi-Fi.
- **Updating a computer Stump notices a new Heltec.** `--pc` on an
  existing Stump used to keep `config.py` without a word and go straight
  to the chain: plugging in a Heltec by USB changed nothing. It now shows
  the settings (name, Heltec, chain), offers a Heltec found on USB
  (keeping everything else), and asks whether to change the rest.
- **The About page says what a Stump is**, in French, English and
  Spanish, as the README now does: its three setups (ESP32-S3-CAM +
  Heltec V3; a macOS/Windows/Linux computer + Heltec V3; a computer
  with no radio, reaching LoRa through a chained Stump), how Stumps
  chain (`#wifi-mesh`, `#lora-mesh`), and who connects (FireFly on
  Android or the R36S/R36MAX handheld, each with its own Heltec V3, and
  any browser).

## 1.0.0-beta.4
- **Chat column you can widen.** Drag the bar between the room list
  and the chat to make room for long names (kept on that device;
  double-click puts it back); a cut-off name shows whole on hover.
  The other Stumps of the chain are grouped at the bottom of the column
  under *Stumps* (*Buches* in French), each with a 🪵.
- **Scroll bars out of sight until used**, on every page: the bar shows
  while something scrolls and fades back right after.
- **#wifi-mesh works when the Stumps start at different times.** A Stump
  knew another one as a Stump only from its beacon, sent at boot and
  then every half hour: the one started second never heard the first's,
  so nothing crossed in #wifi-mesh and a DM to the other Stump landed in
  #lora-mesh. Now a new chain link makes both ends announce themselves right
  away, a Stump is also recognised by what it sends, and anyone reached
  over the chain lands in #wifi-mesh, never #lora-mesh.
- **DMs between people on different Stumps.** People seen in #wifi-mesh
  on another Stump are DM contacts as `rod@LaBuche`; `/msg rod@LaBuche
  ...` reaches rod privately, who answers the same way. A DM to a Stump
  itself shows in that Stump's #wifi-mesh, addressed to it (`@LaBuche ...`).
- **`#wifi-mesh`: the Stumps of a chain talk among themselves, `#lora-mesh`
  stays for LoRa.** (Room names that read the same in every language and name the
  transport, so others can follow.) On a Stump in a chain, the other Stumps are taken
  into `#wifi-mesh` as soon as their beacon is heard over the chain --
  no one has to message them first -- and anyone on any Stump can
  `/join wifi-mesh`. Only chat lines cross between Stumps: each Stump
  sends its own people's lines, shown as `<nick@thatstump>` on the
  others, never relayed twice, without the joins, parts and renames
  that used to arrive glued together on one line (and the doubled
  `<macOS> <macOS> hello`). Mesh users (phones, handhelds) still land
  in the mesh room. Fixed on the way: announces heard in a Stump's
  first seconds were missed by the chat bridge until the next
  re-announce.
- **`#lxmf` is now `#lora-mesh`.** The old name still works (`/join lxmf`,
  `#lxmf text`, `/admin <pw> room lxmf hybrid`, `?room=lxmf`, a saved
  tier); replies use the new one. (Room names are lowercase.)
- **A computer joins the chain without leaving its Wi-Fi.** The first
  Stump of a chain, with a chain secret, also takes chain links on the
  Wi-Fi it joined (`WIFI_SSID`): a computer on that same network (home,
  office) joins at the Stump's address there and keeps its internet.
  The Provisioner asks which way ("On the Wi-Fi it's on now" or "By
  joining the Stump's own Wi-Fi"), a CAM's summary says where computers
  join, and the chain check says when a Stump doesn't take links on that
  network and why. `CHAIN_LAN = False` turns it off; `/chain.json` gains
  `lan_ip`.
- **A computer joining a CAM: the right address.** The wizard says the
  Stump's address is the *Router* in the computer's Wi-Fi details, and
  refuses the computer's own address; the chain check, given its own
  address, says it was checking itself instead of reporting a secret
  mismatch.
- **Joining a CAM from a computer: "Connection refused" explained.**
  The Provisioner now reaches a Stump directly, never through a proxy
  from the system settings (a VPN or filter app can't reach the
  Stump's Wi-Fi). When the Stump's Wi-Fi is up but its web server
  refuses, it waits up to a minute (a CAM still starting), then says
  so: its program has most likely stopped, power-cycle it, and where
  its boot log says why.
- **Which version, at a glance.** The Provisioner's first box says which
  release this folder installs and whether GitHub has newer commits
  ("quit, run git pull, start again"); a firmware folder remembered from
  an earlier run that isn't this download's is pointed out. A CAM's
  *Verify* reads the release back from the board and fails on a
  mismatch ("re-upload the Stump files"), and so does `--diag`.
  `python3 provisioner.py --version [PORT]` prints all of it. On the
  node, the About page and `/admin` show `Stump Beta A · <release>`
  (also in `GET /node.json` and the boot log's first line).

## 1.0.0-beta.3
- **The Provisioner no longer edits your checkout.** A CAM's generated
  `config.py` used to be written into `final_firmware/config.py`, which
  made every later `git pull` refuse ("Your local changes ... would be
  overwritten") and left the old Provisioner running. It's now saved as
  `~/.provisioner/profiles/<node>.config.py`. At start, the Provisioner
  warns if a changed `final_firmware/config.py` would block a pull.
- **The Tools page copy is a question.** In a CAM's section 3: copy the
  Tools page's files (firmware bundle, Provisioner) to the card, ~20-25
  minutes the first time and after each update, since they change with
  it; no keeps the node working the same. The card says so.
- **A CAM's setup asks everything first.** Section 3, *Questions*, asks
  them all (flash or not, the wizard, diagnostics at the end); sections
  4-7 then run by themselves, about 25 minutes, and the Provisioner keeps
  the computer awake meanwhile (macOS, Windows). The route card marks
  each section *you* or *auto*, totals both, and says why it's slow: the
  board takes files at about 1 KB/s on its SD card, and the Tools page's
  ~1 MB is most of it, the first time only.
- **BarKeep is back, as the node's librarian (HF-003).** Visitors ask it
  what's on the node in French, English or Spanish ("do you have a
  movie?", "as-tu un livre sur le jardinage ?") from an *Ask BarKeep*
  drawer on the Files and Billboard pages and `/concierge`, and get the
  matching files (linked), notices and apps; `!find <words>` asks it in
  the chat, on one short line. `librarian.py`: accents folded, filler
  dropped, kinds of file / billboard / apps spotted, the rest matched.
  The Files page sorts and filters (kind pills, as-you-type, Enter asks
  BarKeep); the Billboard searches titles and bodies and sorts; both in
  the browser. `/files.json` and `/billboard.json` take `?q=`, `?cat=`,
  `?sort=`; `/files.json` rows gain `cat` and `mtime`; `POST /chat`
  answers JSON when asked. Notices past 72 hours are no longer shown
  before the next post prunes them.
- **A shorter README (H-09).** From about 2,400 lines to about 1,100:
  each feature is a short reference now, and the bug stories and test
  write-ups it had collected moved, word for word, to
  `docs/ENGINEERING_NOTES.md`. Out-of-date passages fixed on the way
  (the mesh landing room, the LXMF signature warning).
- **HTTPS only where it's needed (H-01).** With a certificate, the chat
  and the other pages now stay on plain HTTP: the chat polls every 2 s,
  and over HTTPS each poll was a new secure handshake for the CAM. Voice
  notes record in a small HTTPS window (`/rrc/rec`) that hands the
  recording back to the chat; `/admin` still moves to HTTPS (a password
  is typed there). The chat opened over HTTPS goes back to HTTP.
  `bench/tls_probe.py`, which `docs/HTTPS_SETUP.md` step 0 runs, is in
  the repo now.
- **Hardening (October 2026 review, H-02 to H-08).**
  - **Forged LXMF messages are dropped.** A message whose signature
    doesn't match its sender never reaches the chat or the propagation
    node's mailbox. Without the native Ed25519 module, signatures used to
    go unchecked *and* the message was marked verified (which could also
    set the clock); now they're always checked. A sender not yet heard
    from (no key to check with) is still taken on an open node, and
    refused in hybrid or mandatory mode.
  - **Speed limits** (`guard.py`): chat lines that reach others, on the
    web and over the mesh, 3 at once then one every 2 s (`CHAT_BURST`,
    `CHAT_EVERY`); 3 billboard posts a minute per visitor
    (`BILLBOARD_POSTS_PER_MIN`, 429 / `rate_limited`); after 5 wrong
    passwords (the `/admin` page, `/admin` in the chat, the fserv bot)
    that visitor waits 30 s, doubling each time, up to 15 min.
  - **Access settings survive a restart:** `/admin … mode` and room
    tiers are saved (`access.json`) and restored; `/admin <password>
    reset-access` goes back to `config.py`.
  - **A room made stricter moves out** whoever no longer qualifies, to
    #main, told why (web: a private message; mesh: `→ #main` and the
    refusal).
  - **Saves are whole or not at all:** the credit ledger is written to a
    temporary file and renamed (a power cut mid-write used to leave an
    unreadable ledger, read as empty: everyone's credits gone); the
    unfinished saves a power cut leaves are removed at start-up.
  - **The Heltec link** is primed before every mesh send, not once per
    cycle.
- **The Provisioner, laid out as a route.** It starts by asking what
  you're setting up (CAM, Heltec for a CAM, Heltec relay, Stump on this
  computer, check or fix), shows the route on a card (numbered sections,
  what each does, what you'll need), then runs one section at a time:
  a header with where you are, everything inside it indented under it,
  the setup questions grouped by topic, and a done / skipped / didn't
  work line at the end of each. It ends on a summary of each section and
  what to keep (addresses, chain secret, next command). "Check or fix
  something" offers the one-off tools without flags. Packs list their
  files only with `--list-packs`; the wizard says whether they match the
  pack's hashes. Fixed: a computer with its Heltec on USB was asked the
  CAM's Heltec-over-Wi-Fi questions.
- **The Provisioner times itself.** Each section says how long it took,
  the summary adds them up, and the route card shows the times measured
  on this computer (the median of its last five runs) instead of a guess.
- **Is the chain linked?** When a computer Stump joins another Stump, the
  `--pc` wizard checks each step before and after it starts: on that
  Stump's network, a Stump answers there, its chain is on, the secrets
  match (a real hello), and once running, the Stump above answers it and
  lists it. Each failure says what to fix. Any time:
  `python3 provisioner.py --check-chain` (this computer's Stump) or
  `--check-chain 192.168.5.1` (a CAM's, from a computer on its Wi-Fi).
- **Wi-Fi names picked from a list.** Wherever the Provisioner wizard
  asks for a Wi-Fi name (the network to join, the Stump a chain member
  joins, the Heltec's network), it lists the networks this computer
  hears, strongest first, with a lock on those that need a password, and
  skips the password for open ones. Typing a name still works at the same
  prompt; with no list (no Wi-Fi, or the system hiding names), it asks as
  before.
- **Stump on a computer** (`docs/PC.md`): Windows, macOS or Linux, the
  same firmware on regular Python 3 -- pages, chat, plugins, packs,
  `/admin`, LXMF, propagation node -- with a Heltec on USB (new
  `RNodeSerialInterface`, sharing the Wi-Fi bridge's RNode code) or over
  Wi-Fi, and chaining with CAM Stumps. `python3 provisioner.py --pc` makes
  its folder (firmware, packs, Tools page, its own venv), runs the wizard
  adapted to a computer, and can start it at login (macOS LaunchAgent,
  Windows scheduled task, Linux user service); running it again updates
  and keeps the settings. `pc/` holds the launcher and the stand-ins for
  MicroPython's modules (crypto from `cryptography`, checked against the
  board's own code). Tested in CI on Windows, macOS and Linux, and on Linux
  with a MicroPython CAM joining it over the chain.
- **`GET /node.json`**: name, LXMF address, version and release,
  hotspot address, chain role and features, without joining the chat
  (asked for by the Android team, for discovering nodes).
- **Tools page in sections:** each tool in its own folder on the SD
  card (`tools/provisioner/`, `flasher/`, `firefly/`, one per pack) with
  its own `README.md`, which gives the section its title and summary and
  folds out on the page. Cards set up before are tidied at boot (files
  moved, not copied). `/tools.json` adds `sections` and each file's
  `section`; `/tool?f=` takes `folder/name`. The rnfs pack's
  `rnfs-INSTALL.txt` is now its section's `README.md`.
- **Theme for everyone from `/admin`:** an admin picks the theme every
  visitor gets (built-in or pack themes), applied at once, saved on the
  board; *As set up* goes back to `config.py`'s. The old theme buttons
  there are now labelled for what they always were: this browser only.
  Applying a theme for everyone drops any browser's own older pick
  (which used to beat the site's theme forever), and pages are sent
  with `Cache-Control: no-cache`, so no browser shows an old copy.
- **Delete tools from `/admin`:** a whole section of the Tools page, or
  single files, off the SD card.
- **`/admin` sessions and Log out:** the login gives a session (cookie)
  and redirects, so Back after Log out shows the password prompt instead
  of the admin page, and nothing in the history can resend the password.
  Admin pages are sent `no-store`. Sessions end after 30 min unused.
- **Order of the buttons from `/admin`:** drag the navigation row and the
  home page tiles into order (arrows on a phone), and hide or show each
  one (a hidden page still answers at its address). Home now comes
  first in the navigation row by default.
- `/admin` → Plugins: one tidy row per plugin (name, state, version and
  RAM, commands), the button beside it, or under it on a phone.
- **Tools page in the reader's language:** each section can have
  `README.fr.md` and `README.es.md` beside its `README.md`; the built-in
  sections (Provisioner, flasher, FireFly apps) and the rnfs pack now
  have all three. `/admin`'s home-section status and HTTPS reason are
  translated too (they were English in every language).
- `--skip-tools` still brings the Tools page's READMEs up to date (a few
  KB, every language, for the sections already on the card, packs'
  included): a translation no longer waits for the slow tools copy.
- **rnfs pack 1.1 — credits:** rnfs is by Afi Hogan (afit21), MIT;
  Reticulum and LXMF, shipped with it, by Mark Qvist. Credited on the
  `/p/rnfs/` page, in the `!rnfs` reply, in its Tools page section
  (summary and a Credits section) and the pack's README, in all three
  languages; `pack.json` has a new `credits` field the Provisioner shows
  before installing. Its shelf list no longer squeezes titles on a phone.
- `/help`'s argument names are translated (`/nick <nom>`, `/join <sala>`).
- Fixed: **`--upload-app` replaced the node's `config.py`** with the one
  in the firmware folder, so an update could change a node's name,
  Wi-Fi, passwords or theme (the theme was what showed). It now keeps
  the node's own; `--replace-config` copies the folder's anyway, and a
  freshly flashed board still gets one.
- Fixed: the Provisioner printed "no tools_payload folder found" on
  every run without a `home/` folder.

## 1.0.0-beta.2
- **Chained Stumps over Wi-Fi** (`docs/CHAIN.md`): each Stump serves its
  own Wi-Fi on its own range (`192.168.<n>.1`) and joins the one before
  it; they relay Reticulum for each other, so files cross the chain over
  Wi-Fi. A chain secret keeps visitors out (HMAC hellos). PCs join with
  a plain UDP interface (`scripts/chain_hello.py` with a secret). The
  Provisioner asks about the chain, and whether the Stump has a Heltec
  at all. `/admin` → Stump chain, `GET /chain.json`.
- **Big transfers stay off LoRa:** a Stump relays at most 48 KB of file
  data per link every 10 minutes onto a radio interface.
- **UDP interfaces drain their socket** in batches instead of one packet
  per 10 ms with a full garbage collection each (which capped them at
  well under 100 packets a second).
- Tests: a three-Stump chain on loopback (with an intruder and two PCs),
  the radio cap, chain settings.
- **Faster relaying**, measured on a CAM first (`bench/`): file and
  channel packets inside a link the Stump carries are forwarded from
  their raw bytes, without building a Packet or hashing it (the general
  path took 6.8 ms a packet); a packet's hash is computed once, not
  twice; per-packet debug strings are only built when they'll be
  printed; HMAC pads are XORed in C instead of byte by byte (~1.3 ms
  saved per HMAC: link encryption, HKDF, propagation stamps).
  `tests/mp/relay_fastpath.py` checks the fast path forwards exactly
  what the general path does.
- `bench/`: relay, profiling and Wi-Fi benchmarks for chaining Stumps.
- The release zip also carries `docs/RELEASE.md`, the public signing
  key and `scripts/sign_release.py`, to check a release offline.
- **Client docs** (`CLIENT_QUICKSTART.md`): what changed for clients
  since beta.1, chained Stumps' addresses (use the gateway, not a fixed
  `192.168.4.1`), the `stump.node` beacon's forward compatibility.
- Docs: the field-test checklist and the README's quick start start
  from the release download; the README lists the pack commands.

## 1.0.0-beta.1
First beta-test release from this repository: Stump **Beta A** with
everything since the `beta-a` tag on LaBuche-Stump (Oct 9).

- **Plugin API** (`plugins.py`, `docs/PLUGINS.md`). Plugins register
  commands (`/roll` and `!roll`), gates with an explicit priority (0–19
  reserved for access control), listeners, `on_result` hooks, pages under
  `/p/<plugin>/` with home-page cards, timers, services, their own
  settings, storage folder (`/sd/plugins/<name>/`) and translations —
  instead of patching `rrc.handle_input`. Every callback is guarded,
  timed and rate-limited; mesh replies are capped to keep airtime down.
  `GET /plugins.json` lists what's running.
- **Off switch and safe mode.** `/admin <password> plugin off <name>`
  or `/admin` → Plugins (state, RAM, last error). A plugin that resets
  or hangs the node while starting is switched off at the next boot.
- **stumpid and fservbot moved onto the API.** Every reply is unchanged
  (checked line by line against the previous build), except `/help`,
  which now ends with the symbol key. stumpid's gate runs at priority 10
  and the core asks it about access through the `identity` service
  instead of importing it. Password-taking commands are slash-only.
- **Packs** (`docs/PACKS.md`): plugins plus a theme, a bot persona and
  SD-card files in one download. The Provisioner finds them in `packs/`,
  shows author, licence and every file's hash, refuses incompatible ones,
  warns about calls that would hold the node, and installs them
  (`--install-pack`, `--list-packs`, `--remove-plugin`, `--hash-pack`).
  The core manifest now covers the core only; plugins carry their own
  hashes.
- **Two packs**: *tavern* (`!roll`, `!8ball`, `!slap`, `!seen`, `!quote`,
  `!vote` with a results page, a flood guard, a Tavern theme) and *rnfs*
  (the Reticulum file system installable with no internet from the Tools
  page, a link spotter, `!shelf`).
- **Voice notes** on the web chat and over LoRa: Opus (FireFly's default)
  and Codec 2, recorded in the browser (or the phone's recorder) and
  played back; 15 s, 16 KB at most.
- **LXMF propagation node** (store-and-forward for offline phones),
  switched on from `/admin`, with a test mailbox and "leave a message".
- **HTTPS** with your own certificate (`docs/HTTPS_SETUP.md`), checked by
  the browser before it moves a page there.
- **`/admin` → Radio**: TX power, frequency, bandwidth, SF, CR, sent to
  the Heltec.
- **Home-page branding** from a `home/` folder on the SD card, placed
  above, under or beside the buttons (`docs/HOME_BRANDING.md`).
- **JSON** answers for billboard posts and uploads, `/tools.json`,
  and UTF-8 / Latin-1 file names that survive the trip.
- **Navigation**: icon rows on every page, one card per row on the home
  page, About and Tools mirror the public site, FireFly apps on the node.
- **Fixed**: fservbot's saved dialogues were never reloaded after a
  reboot (plugins started before the SD card was mounted).
- **Tests and releases**: `tests/run.py` (firmware on single-precision
  MicroPython, plugins, packs, Provisioner) on every push; tagged
  releases are tested, built reproducibly, hashed and signed
  (`docs/RELEASE.md`).
