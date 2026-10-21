# To do

Things noted for later, newest first. Each with what was seen and what
we think the fix is.

## Provisioner: uploads to a CAM restart the board now and then

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
