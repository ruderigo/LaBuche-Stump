# Arborist

One HTML page for a **Freenove ESP32-S3-CAM** or an **ESP32-WROVER** (since
1.0.0-beta.59) on a computer's USB, from Chrome or Edge: it makes the
board a Stump, updates one, changes its settings, sets up its Heltec V3
and watches it start. Nothing to install, no certificate, no
terminal: save the page, open the saved file, plug the board in.

It's `Arborist-<release>.html` on each release (the version is in
the name). It holds everything: the release's **Stump images** for both
boards (`stump-<release>-s3cam.bin`, `stump-<release>-wrover.bin`:
MicroPython 1.28, MIT licence, with the Stump built in), the Stump's
other files compiled, MicroPython for the ESP32-S3 with octal PSRAM, and
esptool-js (Apache-2.0), which writes the board. About 11 MB. Nothing is
downloaded or sent anywhere while it works.

What it carries is also on each release as a file of its own,
`Stump-<release>.bundle` (since 1.0.0-beta.48, in `SHA256SUMS`), for
tools that make Stumps the way Arborist does, such as the RK3326
Provisioner on a handheld. It's one gzip: 4 bytes (little-endian) the
header's length, a JSON header (`release`, `micropython`,
`micropython_sha256`, `mpy`, `config_template`, `default_ssid`, and
`files`: `[name, offset, length, sha256]` each), then the files' bytes in
order (`micropython.bin`, then `app/...`). `scripts/build_arborist.py`
makes both.

**From a Stump** (since 1.0.0-beta.49): the release zip has
`Arborist-<release>.html` next to the Provisioner, which puts it on the
Stump's Tools page (section *Arborist*), for a visitor's laptop to
download with no internet. A computer Stump gets it with `--pc`. On a
board it's too big for USB (about 11 MB with the images), so the
Provisioner lists it with the files to copy by card reader: into
`tools/arborist/` on the card, with the `README*.md` files from
`final_firmware/tools_payload/arborist/`. Only the Arborist of the same
release is offered.

## Using it

1. Save the page on the computer and open the saved file in Chrome or
   Edge. (Opened from a web address it can't reach the board: browsers
   only allow USB from a secure page, and a saved file counts as one.)
2. Choose **A new Stump** and fill in its name, an admin password
   (needed, typed twice: the page says as you type whether the two
   match; the fserv bot takes the same one unless you untick it, and its
   own is typed twice too), and,
   only if you have them, the home Wi-Fi, the LoRa radio (a Heltec V3
   running the RNode firmware: set up just after, see below) and a chain.
   The Stump's Wi-Fi name, whether its address shows in the name
   ("Stump 192.168.4.1": the page counts the 32 characters as you type),
   and its number (2 to 254: its Wi-Fi is 192.168.<number>.1) are under
   *More settings*, with *Its language* (French, English or Spanish: for
   visitors who haven't picked one and the bot's posts; also in `/admin`,
   since 1.0.0-beta.67). A chain is for linking Stumps; on its own, leave it at
   *Not in a chain* (it can be changed later in Settings).
3. **Make my Stump**, pick the board in the browser's list. A few
   minutes: it reads the chip, erases the board, writes the Stump image
   for it (the S3-CAM's or the WROVER's), copies the rest of the Stump,
   writes its settings and restarts it.
4. Its Wi-Fi appears: join it from a phone. The result says its name and
   the address to open on it.
5. **Set up a Heltec V3** (the third choice; since 1.0.0-beta.64): it's
   filled in from the Stump just made, read or saved on the page. Plug
   the Heltec itself in (not the Stump) and press **Set up the Heltec**.

After each new Stump the form goes back to its defaults (only the home
Wi-Fi's name is kept), so the next board doesn't take the last one's
chain, name or passwords.

**Set up a Heltec V3** writes which Wi-Fi the Heltec joins: a Stump's own
(its name as phones see it, open, at 192.168.<number>.200) or the home
Wi-Fi (its name, its password typed twice, the Heltec's address there).
What it will join is said as you type. It sends the RNode firmware its
documented serial commands (KISS frames), reads them back to check, then
restarts the Heltec. The page contains none of the RNode firmware (GPLv3)
or of `rnodeconf` (Reticulum License): the Heltec must already run that
firmware, flashed once with the Provisioner (*A Heltec for a CAM*). The
same button is in a Stump's radio settings, shown in Settings once
something the Heltec joins by has changed; do the Stump first, then the
Heltec.

**Update a Stump** puts the new release on a Stump and keeps everything
else: its settings (`config.py`), its identity on the mesh, what's on its
card. A Stump that runs a Stump image with this release's partition
layout gets the new image written in place (no erase), then the rest of
the files; old copies of modules the image has built in are removed. One
with an older layout is told to use *A new Stump* (an erase: a new
identity). One on plain MicroPython (an earlier Arborist) is updated file
by file as before, and told that *A new Stump* would give it the image.
A Stump on an older MicroPython is told to use *A new Stump*.
Since 1.0.0-beta.65, files already on the board and the same aren't
written again, and the room an update needs is worked out first: a
WROVER has about 1 MB of storage. Short of it, the safe leftovers go (a
write cut short, an old `.py` beside its `.mpy`); still short, it stops
before writing anything and says what takes the room.

**Look around with the board** (since 1.0.0-beta.60): the board, over
USB, lists the Wi-Fi networks it sees (it only sees 2.4 GHz), and with one
chosen joins it -- a wrong password is said there -- and asks "who's a
Stump here?" (the Stumps' discovery broadcast). The Stumps found are
listed; the first Stump of a chain is offered with **Join its chain**, its
address filled in, with a free chain number and a free Heltec address --
never chosen on its own (since 1.0.0-beta.64): a board left at *Not in a
chain* stays so, and Make asks once before writing a Stump that's in a
chain. A chain secret typed in is
checked against that first Stump (its chain id) before anything is
written; the secrets of the chains this browser made or read are
remembered (in its storage only) and filled in. A blank board gets the
Stump image first, to look with.

**After Make and Settings** it listens to the board start (up to 90 s):
its address on the home Wi-Fi or why it didn't join, the chain linked or
not answering, its Heltec reached or not; a first Stump ends with what the
next ones need (its address, the chain secret, reserve the address on the
router). A board without PSRAM is told it can't run a Stump; the SD card
section says a WROVER has no slot; a port another program holds is said.

**The chain**: *the first Stump* (it sets the channel; with the home
Wi-Fi and a chain secret, the others can join it over the router too),
*joins the first Stump over the home Wi-Fi* (the router, like the
Provisioner's: the first Stump's address there, this Stump's number, the
chain secret, needed), or *joins another Stump's Wi-Fi* (its hotspot, no
router).

**The Heltec**: on the Stump's own Wi-Fi it's at 192.168.<number>.200,
past the addresses the hotspot hands out (.2 to .101), as with the
Provisioner. On the home Wi-Fi, each Heltec needs its own address, one
the router doesn't hand out: the page suggests .222 on the home network
(from the first Stump's address when it knows it) -- it can't see which
are taken, so the next Heltec takes .223, and so on. The result says
which address to give the Heltec in its own setup (*Set up a Heltec V3*).

**Settings** reads a Stump's settings into the same form -- its name,
admin password, Wi-Fi, the radio, what it offers, its look, who may come
in, the bot, a chain -- and saves what you change. Its identity, chat and
files stay. Passwords aren't shown: left blank, they stay as they are.
*Keep a copy of its config.py* saves it on the computer; *Put a config.py
from this computer back* puts one back as it is (checked to be Python
first). The one replaced stays on the board as `config.py.bak`.
*Save its identity* (since 1.0.0-beta.76) downloads what makes it this
Stump on the mesh, as a `.stump-identity` file (the Provisioner's own
format); *Bring a saved identity back* puts one on a board made again,
or another board, so it's that Stump again. The identity it had is
downloaded first, to keep. Whoever has the file can be that Stump: keep
it safe.

**Check** restarts the Stump and listens to it start for a minute: the
release it runs, whether its pages came up, what went wrong (a
traceback, the Wi-Fi it couldn't join). *Save what it said* keeps the
whole of it as a file, to send to whoever helps.

**Packs** (since 1.0.0-beta.75) works on the Stump's packs alone;
nothing else about the Stump changes:

- *What it has*: the packs on the board, each with its version (a pack
  installed before beta.75, or copied in by hand, says "version not
  recorded"), and a *Remove* button. Removing asks first, then asks
  whether what the pack keeps goes too: its saved data
  (`/sd/plugins/<plugin>/`), its Tools page section, its home files and
  its theme. Say no and they stay, for a reinstall later.
- *Install or update a pack*: choose a pack's zip from a release
  (`tavern-pack-<v>.zip`, `rnfs-pack-<v>.zip`). Before anything is
  copied, every file is checked against the pack's own hashes, as the
  Provisioner checks them; a pack that would replace the Stump's own
  code or a built-in plugin is refused. Its plugin goes on the board (as
  source: Arborist can't compile in a browser, so the Stump compiles it
  as it starts), its tools and home files on the card, its theme into
  `themes.json`. It starts with the Stump. A file over 1 MB is left for
  a card reader, and the page lists it. tavern: about a minute; rnfs:
  about 17 minutes (its 1 MB of tools).
  The pack's setup questions (its `plugin.json`, as the Provisioner asks
  them) are shown once the zip is checked: the ones not marked optional
  (Bavard's "Talk from the start?"), the others under *More settings*
  with their working defaults. Each answer is checked by type (a choice
  from its options, a whole number in its range) and written into the
  board's `config.py` after the copy, the way *Save* writes (a backup
  first, undone if it isn't Python). A setting already on the board
  stays unless it's changed here, so a reinstall or an update keeps it.
  A pack only sets settings of its own (its name in capitals first,
  `BAVARD_...`): anything else it asks for (`CHAIN_SECRET`, `WIFI_PASS`,
  another pack's) is refused and said, not asked.
  Each install records the pack in its plugin folder (`.from_pack`:
  its name, version, Tools section, home files and theme), as the
  Provisioner does, so either can update or remove it later.

**SD card** (since 1.0.0-beta.49) works on the Stump's card through its
USB. Copies over USB run at about 1 KB a second, so each part says how
long it takes:

- *What's on it*: the card's size and free space, and each folder's
  (the shared files, the Tools page, the home page, the messages kept
  for others, the plugins' data). Seconds.
- *Fill the Tools page*: what visitors download with no internet: the
  Provisioner, its README and licence, the firmware zip, the FireFly
  handheld app, and each section's description, as the Provisioner puts
  them there. About 1.5 MB: 25 to 30 minutes. (The Android app, ~50 MB,
  and Arborist itself still go by card reader.)
- *Wipe the card*: the shared files only (what visitors uploaded), or
  everything on the card; type `WIPE` to confirm. The Stump's identity,
  settings and chat are on the board, never on the card: a wipe can't
  touch them.

If the board isn't found: another USB cable (some only charge), another
port; or hold BOOT, press and release RESET, let go of BOOT, and try again.

## What it does on the board

The same as the Provisioner:

- the chip read (esptool-js, 460800 baud), then erase and the Stump
  image for it: the S3-CAM's at 0x0, the WROVER's at 0x1000 (as the
  Provisioner writes them); plain MicroPython at 0x0 on a CAM when the
  page was built without images. An update in place writes the image
  without erasing.
- at its REPL (115200): a running Stump stopped, its watchdog given two
  hours (`QUIET_WATCHDOG`), a soft reset
- every file of the Stump the image hasn't built in (`stump_image.FROZEN`,
  read from the board), folders first: each in chunks to
  `<file>.part`, its SHA-256 checked on the board, then renamed over the
  old one, so a cable pulled out never leaves half a module; a module
  arriving compiled (`.mpy`) has its `.py` removed, and the other way
- `config.py`: `final_firmware/config.py` with every name, password and
  Wi-Fi taken out at build time (the build stops if one is left) and a
  fresh Stump's values, then the form's answers written into it as for
  *Settings*: by the firmware's own `settings.write_config` (what
  `/admin` uses: each `KEY = value` in place of its line, the rest of the
  file as it was, a temporary file then a rename), plus the Heltec
  bridge's three lines in `CONFIG` (as the Provisioner edits them). A
  result that isn't Python is undone. The rules are `/admin`'s and the
  Provisioner's: a name of 20 characters at most, an admin password on
  a new Stump, every Stump has a number from 2 to 254 (4 by default; its
  Wi-Fi is 192.168.<number>.1, in a chain or not, and a different one on
  each Stump of a chain), a chain's first Stump sets the channel, a
  member over the router has `CHAIN_PARENT_IP` and
  the home Wi-Fi as its uplink (its password copied on the board when it
  isn't typed again), a Heltec on the Stump's own Wi-Fi is
  192.168.<number>.200 (.2 read from an older one, saved as .200).
- *Settings* checks that the `config.py` it saves over is the one it
  read (another board, or a change since: read again)
- `machine.reset()`

The **radio settings** are CAM-side (the RNode ships host-controlled): the
form writes them with the firmware's own `radio.save()` to `/radio.json`,
which the Stump applies to the bridge at boot and sends to the Heltec on
connect -- the same shape as how Settings writes `config.py`. They default
to the deployed mesh's values and must match every Stump on the mesh.

Not done here (the Provisioner does them): the **Heltec's own provisioning** (EEPROM signing, `-w STATION` Wi-Fi,
Bluetooth -- `rnodeconf`, GPL-3), a Stump on a computer, checking a chain
over the network.

## Building it

    pip install mpy-cross==1.28.0.post2
    python3 scripts/build_arborist.py dist/ [--micropython FILE.bin] [--images DIR]

The Stump images are taken from `--images`, or `dist/` itself (where the
release workflow puts them first), or `build/images/` (`image/build.sh`);
without them the page makes a CAM with plain MicroPython. They go in a
block of their own, so `Stump-<release>.bundle` stays the page's other
block, byte for byte. Without `--micropython` it downloads the image the Provisioner uses
(`CAM_FIRMWARE_LATEST_URL`) and checks its SHA-256
(`MICROPYTHON_SHA256`). Built twice from the same commit, the page is
the same, byte for byte. Tests: `tests/arborist_test.py` (the uploader
against a pretend board running MicroPython's Unix port, and the page
in Chromium with Web Serial piped to it).
