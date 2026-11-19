# Stump OS: a Stump on a Raspberry Pi

**Stump OS** turns a Raspberry Pi into a Stump: you write it to a micro-SD
card, put the card in the Pi and plug it in. No terminal, no commands.

The Pi does nothing else: its Wi-Fi becomes the Stump's own open hotspot,
where visitors join and chat. Later, a Heltec plugged in by USB adds LoRa.

**Works on:** Raspberry Pi 5, 4, 3B+ and Zero 2 W (64-bit).
**You need:** the Pi, its power supply (for a Pi 5, the official 5 V / 5 A
one), a micro-SD card of 8 GB or more, and a computer to write the card.

## 1. Write the card

1. Install **Raspberry Pi Imager** (free, Windows, macOS, Linux) from
   raspberrypi.com/software.
2. Download `stump-os-<version>.img.xz` from the latest release on the
   project's GitHub page.
3. In Imager: **Choose OS** → scroll down → **Use custom** → pick the file
   you downloaded. **Choose Storage** → your card. **Next**.
4. If Imager offers to apply settings (user name, Wi-Fi), say **No**:
   Stump OS doesn't need any, and it takes the Wi-Fi for its hotspot anyway.
5. **Write**, and wait until Imager says it's done.

**Even easier, once:** in Imager's settings (*App options*), set a custom
content repository to Stump OS's list:
`https://github.com/ruderigo/SandBox_Stump_On/releases/download/stump-os/os_list.json`.
Stump OS then shows up in **Choose OS** by name, always the latest one.

## 2. Start it

1. Put the card in the Pi.
2. Optional: plug in an **Ethernet cable** to your router. The Stump works
   without it; it's only needed for updates (step 4).
3. Plug in the power. **The first start takes a few minutes**: the Pi sets
   itself up once (its own identity, its own name). Later starts take
   about a minute.

## 3. Join it and set it up

1. On a phone or laptop, open the Wi-Fi list: join **Stump-XXXX**, an open
   network (XXXX is particular to your Pi).
2. The Stump's page opens by itself. If it doesn't, open
   **http://10.42.0.1/** in the browser.
3. Open **http://10.42.0.1/admin**. The first time, it asks you to
   **choose the admin password** (at least 6 characters). Write it down:
   whoever knows it can change the Stump. You're logged in straight away.
4. Everything else is in `/admin`: the Stump's name, rooms, look, packs,
   later the radio.

**Forgot the admin password?** Turn the Pi off (step 5), put the card in
your computer, and on the partition called **bootfs** create an empty file
named `stump-reset-admin.txt`. Put the card back and start the Pi: `/admin`
asks for a new password again. Nothing else changes.

## 4. Update it

In `/admin` → **Stump OS (Raspberry Pi)** → **Check for an update and install
it**. The Pi needs the internet for this: an Ethernet cable to your router.

The Stump downloads the newest release, checks its signature, installs it
and restarts by itself, in a minute or two. **Its settings, identity, rooms
and files are kept.** Come back to `/admin`: the card says how it went. If
anything doesn't check, nothing changes.

## 5. Turn it off

Before unplugging the Pi: `/admin` → **Stump OS (Raspberry Pi)** → **Shut
down the Pi**. Wait about twenty seconds, until its green light stays off,
then unplug it. (Pulling the plug while it writes can damage the card.)

To turn it back on, plug it back in.

## Adding LoRa later: a Heltec on USB

1. A Heltec V3 for **your country's band** (915 MHz in the Americas, 868 MHz
   in Europe; check the box: a 433–510 MHz board can't join these meshes),
   with **RNode firmware** on it and its **antenna attached** (never power a
   LoRa board without its antenna).
2. Plug it into the Pi's USB. The Stump finds it by itself.
3. Set the radio in `/admin` → **Radio**: the same frequency, bandwidth,
   spreading factor and coding rate as the other Stumps and apps around you.

## Questions

**Can the Pi also be on my home Wi-Fi?** No: its Wi-Fi is the hotspot. Use
an Ethernet cable for the internet.

**Is the hotspot private?** It's open, like a board Stump's: anyone nearby can
join and use the Stump's pages. Only `/admin` has a password.

**What does Stump OS change on the Pi?** It's Raspberry Pi OS Lite (64-bit)
with the Stump installed. There's no login and no SSH: the Pi is the Stump.

**Something's wrong.** A screen on the Pi's HDMI port shows the hotspot's name
and address. For anything else, writing the card again gives a fresh start:
a new Stump, with a new identity (apps will see it as a different Stump).
