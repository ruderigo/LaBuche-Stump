# To do

The plan, in order: [ROADMAP.md](ROADMAP.md). Things noted for later, newest first. Each with what was seen and what
we think the fix is.

## From the review of November 2026

Seen while reading the code for the review (the fixes that came with it:
CHANGELOG, *code review, Nov 2026*). These change what goes on the air or
how a Stump behaves, so they are noted, not done.

### A `#lora-mesh` radio broadcast from an earlier boot can be played again

**Seen** (`rrc_mesh._lb_in`): a broadcast is refused as played again only
when its boot id is the one last seen from that Stump and its number
isn't higher (`last[0] == boot and seq <= last[1]`). Any packet recorded
under another boot id passes, and becomes "the last seen": after the
Stump's next real line, the same recording passes again. Someone with a
radio and two recordings from two boots of a Stump can show its people's
old lines in `#lora-mesh` on every Stump in range, as often as they like
(the 2 minutes of `_seen_line` aside). Old public lines only: nothing is
read that wasn't public, nothing new can be written (the signature
holds). A Stump that restarts forgets every number it had seen.

**Fix to consider**: tie the broadcast's boot id to the beacon's
(`caps["b"]`, an announce: signed, and ordered by Reticulum), and take a
broadcast only under the boot id of the latest beacon heard from that
Stump. A recording from an earlier boot then dies with the next beacon.
(Keeping the retired boot ids instead would let one old recording shut
the real Stump out.)

### Nothing limits how fast new addresses are taken in

**Seen**: every announce heard, of any kind, reaches
`LXMRouter.handle_announce` (and `rrc_mesh.on_announce`); Reticulum's own
tables are capped (urns/const.py) and the router's now are too, so memory
holds. What isn't limited is the work: each new address is a signature
check (about 2 s without the native module) and, for a person, a contact
in `_reach`. `Transport` has an ingress queue (`MAX_ANNOUNCE_INGRESS`);
worth a bench test of a board under a steady stream of new identities
over Wi-Fi (AutoInterface, the chain's port), where airtime doesn't
slow the sender down.

### RADIO_CHUNK is 200 of the 295 bytes a packet holds

`#lora-mesh` pieces between Stumps stop at 200 bytes (170 for the
broadcast, which as a plain packet could hold about 360). Kept as it is:
the kit was tested with it. Each packet spends about 205 bytes before its
first letter, so fuller packets are cheaper per line; with stamps on, a
ticket takes 18 bytes and a proof of work 34, which leaves 251. A bench
measure of loss by packet length at SF8 would say how far to go.

## Provisioner: uploads to a CAM restart the board now and then

*Still open for the Provisioner (Nov 2026). Arborist doesn't hit it: it keeps
one port open for the whole job, and with the Stump images most of the
Stump is in the image, not copied file by file.*

**Seen** (LaBuche, 20-21 Oct, beta.30-34): during `--upload-app`, a file
fails with `could not complete raw paste: b"ESP-ROM:esp32s3..."` -- the
board restarted mid-copy. It happens most during the slow Tools copy to
the SD card (`provisioner.py (296 KB) ... FAILED`), sometimes during the
app files (the verify step then resends them). The board's own log says
`[watchdog] last reset: power on / USB (code 1)`: not the watchdog, not a
crash.

**Likely cause**: the CAM's USB-serial chip ("USB Single Serial") wires its
control lines to the ESP32's reset. The Provisioner opens and closes the
port once per file (one `mpremote` run each), and now and then, on macOS,
opening it pulses those lines and restarts the board. A cable or USB port
dropping power briefly looks the same.

**Workaround for now**: copy the Tools files straight onto the SD card
(card reader), or re-run `--upload-app` (files already there are skipped).

**Fix to make**:
- send many files per `mpremote` run (its `+` chaining), so the port opens
  a handful of times instead of once per file -- fewer resets, and faster;
- after a failed file, wait for the board to finish starting, then resend
  it (today only the app files are resent, by the verify step; the Tools
  copy just reports FAILED);
- consider opening the port with the reset lines held steady (pyserial
  `dtr`/`rts` set before `open()`), if mpremote can be made to.

## /admin: who's who, with identities

A page for an admin, `/admin/people.json`: everyone a Stump lists, with
where it knows them from (browser, app, LoRa, another Stump), their
identity, and whether a Wi-Fi visitor and a LoRa identity are linked. To
check a duplicate name without reading code.
