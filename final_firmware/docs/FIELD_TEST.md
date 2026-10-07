# Field test — Beta A, home branding build

What's new since the last field test, in the order you'd check it on
site, and what to send back. Items marked **(hardware)** are things the
lab tests could not settle — they need the real board, radio and phones.

## Before you go

- [ ] `python3 provisioner.py --upload-app PORT --skip-tools` — the app
      is ~1.6 MB with the two voice codecs: about 11 minutes over USB.
- [ ] Once, with time to spare: `--upload-app PORT` **without**
      `--skip-tools`, to fill in the technician tools (~1 MB, 15–20
      minutes the first time; later runs skip what's unchanged).
- [ ] With a card reader, onto the SD card:
  - `tools/FireFly-Android-0.2.11.apk` (rename `app-debug.apk`)
  - `home/` — your branding, if testing it (see `HOME_BRANDING.md`)
- [ ] `/admin` → Radio: set TX power (e.g. 17 dBm). The page says whether
      it reached the Heltec now or will at its next connection.
- [ ] `/admin` → Propagation node: **Activer** (needs the SD card).

## 1. Boot

- [ ] Boot log shows `[web] serving on port 80` and
      `[web] HTTPS off: no certificate installed` (expected for now).
- [ ] With the propagation node on: `[pn] propagation node on: …`.
- [ ] If radio settings were saved: `[radio] saved settings in use: …`.

## 2. Home page and navigation

- [ ] Home: one card per row — Chat, Billboard, Files, Tools, About —
      icon on the left, name and subtitle beside it.
- [ ] Every other page: a row of five icons (all but the page you're on),
      on **one line, on a phone**. On a laptop, hovering an icon shows
      its name (phones don't show these tooltips; screen readers read
      them).
- [ ] Switch the billboard off in the provisioner: it disappears from
      every row.

## 3. Home branding (if a `home/` folder is on the card)

- [ ] `home.html` replaces the Stump logo and title; the language
      switcher stays on top.
- [ ] `/admin` → **Home page section** names the file in use (or why
      none is used).
- [ ] A complete page (e.g. `the_hearth_kinetic_ascii.html`, alone in
      `home/` under its own name) shows in a frame at the top and
      animates; touching it stokes the fire. **(hardware)** Try a
      `"height"` in `home.json` and say what looks right on a phone.
- [ ] Images, a stylesheet and a font from `/home-file?f=…` all load.
- [ ] In `/admin` → Home page section, try each placement — above, under,
      beside left, beside right — on a **phone** (stacks) and a
      **laptop** (side by side from 760 px), plus a frame height and
      "show it" off and on. Each change shows on reload, no restart.
- [ ] `home.fr.html` / `home.en.html`: switch language, the section
      follows.

## 4. About and Tools

- [ ] About: three tabs — Project (v1), How to Connect, Apps & Handhelds.
- [ ] How to Connect names the **real** Wi-Fi name and shows the node's
      address.
- [ ] Apps: "Download from this Stump" shows version and size.
      **(hardware)** On an Android phone: download the APK over the
      node's Wi-Fi (~50 MB — note how long it takes) and install it.
- [ ] `/tools`: apps first, then the technician files.

## 5. Chat over Wi-Fi and LoRa

- [ ] Room movements read `✓ name` / `✗ name`; DMs read
      `[DM] <author>: text`.
- [ ] FireFly's first message lands it in `#lxmf`, and it receives
      `→ #lxmf` first.
- [ ] Make a room `minted` (in chat: `/admin <password> room vip minted`),
      then from an unverified FireFly: `/join vip` and `#vip hello` are
      both refused with `⊘ #vip minted — …`.

## 6. Voice notes

- [ ] Web chat, in a DM: ♪ opens the phone's recorder; the note arrives
      as `[DM] <you>: ♪ N.N s` with ▶.
      **(hardware)** **iPhone:** what does ♪ offer — a recorder, or only
      a file picker? (This decides what iPhone users need.)
- [ ] ▶ plays notes from FireFly (Opus) and any Codec 2 note.
- [ ] FireFly → web user and web user → FireFly, a short note and a full
      15 s note. **(hardware)** Note how long a 15 s Opus note takes over
      LoRa, and whether chat stalls meanwhile (~1 minute of airtime per
      hop, twice when relayed through the Stump).
- [ ] Two notes within 5 s from one sender: the second gets `⧗ — …`.

## 7. Propagation node

- [ ] FireFly lists the Stump as a propagation node; select it.
- [ ] `/admin` test mailbox: send it a message through the node; it shows
      in `/admin` about 30 s later (the stamp check).
- [ ] `/admin` "leave a message" for FireFly's address; close FireFly,
      reopen, sync: it arrives.
- [ ] A 15 s Opus note left for an offline phone is collected intact.
- [ ] **(hardware)** Note the stamp-check time per message on the CAM,
      and whether the web pages stay responsive while it runs.
- [ ] Chat left at the propagation node for the Stump itself, arriving
      more than 30 minutes late, gets "arrived N min late…" instead of
      being acted on.

## 8. Radio

- [ ] **(hardware)** Signal strength of the Stump's announces in FireFly
      before and after the TX power change (7 → 17 dBm should be ~10 dB).

## What to send back

- The boot log (from power-on to `Announced as`).
- Screenshots of `/admin` (radio, propagation status) and the home page
  with branding, phone and laptop.
- The iPhone ♪ answer, the APK download time, the 15 s Opus note time
  over LoRa, and the stamp-check time.
- Anything that looked stuck: the last line on screen, or the last lines
  of the boot log.
