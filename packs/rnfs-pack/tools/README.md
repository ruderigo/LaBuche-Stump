# rnfs — Reticulum Network File System

Files on the Reticulum network (LoRa included), fetched by content ID — written by Afi Hogan (afit21). Everything here installs it on any computer with Python 3.11 or newer, with no internet.

https://github.com/afit21/rnfs   (MIT licence, see rnfs-LICENSE.txt)

    rnfs add holiday.jpg          -> prints a CID
    rnfs get <cid> -o holiday.jpg

Run "rnfs daemon" first and leave it running; every other command talks
to it.

## Installing from this Stump

You need Python 3.11 or newer (python.org on Windows and macOS; most
Linux systems already have it). Download these files from this section
of the Tools page into ONE folder (your Downloads folder is fine):

    rnfs-0.1.1-py3-none-any.whl      rnfs itself
    lxmf-1.2.0-py3-none-any.whl      LXMF messaging
    rnspure-1.5.7-py3-none-any.whl   Reticulum, pure-Python edition
    rnfs-install.sh                  (macOS, Linux)   or
    rnfs-install.bat                 (Windows)

then run the installer from that folder:

    macOS / Linux:   sh rnfs-install.sh
    Windows:         double-click rnfs-install.bat

It installs into its own folder (~/.rnfs-venv) and touches nothing
else. All three are pure Python, so they work on any computer.

rnspure is Reticulum with its own built-in cryptography instead of the
"cryptography" package -- that is what makes an offline install
possible. It is slower ("slow: True" in "rnfs status"), which matters
little for file sharing. Later, with internet, "pip install rns" in the
same environment switches to the fast version.

By hand, without the script:

    python3 -m venv ~/.rnfs-venv
    ~/.rnfs-venv/bin/pip install --no-index --no-deps \
        rnspure-1.5.7-py3-none-any.whl lxmf-1.2.0-py3-none-any.whl rnfs-0.1.1-py3-none-any.whl

("pip check" then says rns isn't installed: expected, rnspure provides it.)

## Standalone installers

The project's own releases also have one-file installers, with the
fast Reticulum built in:

    Windows 64-bit         rnfs-<version>-windows-x64.exe  (rename to rnfs.exe)
    Debian 12+, Ubuntu     rnfs_<version>_amd64.deb / _arm64.deb
    24.04+, Raspberry Pi    sudo apt install ./rnfs_<version>_amd64.deb
    OS 64-bit

They're tens of MB: if this Stump has them, the technician copied them
onto its SD card (in tools/rnfs/) with a card reader, and they're
listed here too.

## On this Stump

In the chat: !shelf lists the files people here recommend, !shelf add
<cid> <title> adds one, and rnfs links shared in the chat are collected
on the page /p/rnfs/.

If this Stump runs a propagation node (store-and-forward for offline
messages), rnfs's own LXMF messages can use it: put its address in
~/.rnfs/rnfs.toml as  lxmf_propagation_node = "<32 hex digits>"

## Credits

- **rnfs** is written by **Afi Hogan** ([afit21](https://github.com/afit21)):
  https://github.com/afit21/rnfs — MIT licence, see rnfs-LICENSE.txt.
  The wheel here is built unchanged from its v0.1.1 source tag.
- **Reticulum** (here as rnspure, its pure-Python edition) and **LXMF**
  are by **Mark Qvist**: https://reticulum.network — Reticulum License,
  see reticulum-LICENSE.txt.

This Stump only hands them out, so that people can get them without
internet. Questions about rnfs itself, bug reports and thanks go to its
author, on the project's page.
