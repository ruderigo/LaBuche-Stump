# Arborist

One HTML page for a **Freenove ESP32-S3-CAM** on a computer's USB, from
Chrome or Edge: it makes the board a Stump, updates one, changes its
settings and watches it start. Nothing to install, no certificate, no
terminal: save the page, open the saved file, plug the board in.

It's `Arborist-<release>.html` on each release (the version is in
the name). It holds everything: MicroPython 1.28 for the ESP32-S3 with
octal PSRAM (MIT licence), the Stump compiled for it, and esptool-js
(Apache-2.0), which writes the board. Nothing is downloaded or sent
anywhere while it works.

## Using it

1. Save the page on the computer and open the saved file in Chrome or
   Edge. (Opened from a web address it can't reach the board: browsers
   only allow USB from a secure page, and a saved file counts as one.)
2. Choose **A new Stump** and fill in its name, an admin password, and,
   only if you have one, the home Wi-Fi and the LoRa radio (a Heltec
   running RNode: set it up with the Provisioner).
3. **Make my Stump**, pick the board in the browser's list. About 5
   minutes: it erases the board, writes MicroPython, copies the Stump,
   writes its settings and restarts it.
4. Its Wi-Fi appears: join it from a phone.

**Update a Stump** copies the new release onto a Stump and keeps
everything else: its settings (`config.py`), its identity on the mesh,
what's on its card. A Stump on an older MicroPython is told to use
*A new Stump* instead (that erases the board, so it gets a new identity).

**Settings** reads a Stump's settings into the same form -- its name,
admin password, Wi-Fi, the radio, what it offers, its look, who may come
in, the bot, a chain -- and saves what you change. Its identity, chat and
files stay. Passwords aren't shown: left blank, they stay as they are.
*Keep a copy of its config.py* saves it on the computer; *Put a config.py
from this computer back* puts one back as it is (checked to be Python
first). The one replaced stays on the board as `config.py.bak`.

**Check** restarts the Stump and listens to it start for a minute: the
release it runs, whether its pages came up, what went wrong (a
traceback, the Wi-Fi it couldn't join). *Save what it said* keeps the
whole of it as a file, to send to whoever helps.

If the board isn't found: another USB cable (some only charge), another
port; or hold BOOT, press and release RESET, let go of BOOT, and try again.

## What it does on the board

The same as the Provisioner:

- erase, then MicroPython at 0x0 (esptool-js, 460800 baud)
- at its REPL (115200): a running Stump stopped, its watchdog given two
  hours (`QUIET_WATCHDOG`), a soft reset
- every file of the Stump, folders first: each in chunks to
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
  Provisioner's: a name of 20 characters at most, a chain's first Stump
  is number 4 and sets the channel, the others 5 to 254, a Heltec on the
  Stump's own Wi-Fi is 192.168.<number>.2. The fserv bot's operator
  password is the admin password.
- *Settings* checks that the `config.py` it saves over is the one it
  read (another board, or a change since: read again)
- `machine.reset()`

The **radio settings** are CAM-side (the RNode ships host-controlled): the
form writes them with the firmware's own `radio.save()` to `/radio.json`,
which the Stump applies to the bridge at boot and sends to the Heltec on
connect -- the same shape as how Settings writes `config.py`. They default
to the deployed mesh's values and must match every Stump on the mesh.

Not done here (the Provisioner does them): the SD card's Tools and packs,
the **Heltec's own provisioning** (EEPROM signing, `-w STATION` Wi-Fi,
Bluetooth -- `rnodeconf`, GPL-3), a Stump on a computer, checking a chain
over the network.

## Building it

    pip install mpy-cross==1.28.0.post2
    python3 scripts/build_arborist.py dist/ [--micropython FILE.bin]

Without `--micropython` it downloads the image the Provisioner uses
(`CAM_FIRMWARE_LATEST_URL`) and checks its SHA-256
(`MICROPYTHON_SHA256`). Built twice from the same commit, the page is
the same, byte for byte. Tests: `tests/arborist_test.py` (the uploader
against a pretend board running MicroPython's Unix port, and the page
in Chromium with Web Serial piped to it).
