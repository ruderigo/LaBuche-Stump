#!/usr/bin/env python3
"""Stump OS in Raspberry Pi Imager's list (pi/README.md, 1.0.0-beta.87):
writes the catalogue entry (os_list.json) for one image.

    python3 pi/os_list.py dist/stump-os-1.0.0-beta.87.img.xz 1.0.0-beta.87 > dist/os_list.json

Imager reads it from a fixed address -- the "stump-os" release's
os_list.json, replaced at every release -- added once in its settings
(App options -> Content repository -> Use custom URL).
"""
import datetime
import hashlib
import json
import lzma
import sys

REPO = "ruderigo/SandBox_Stump_On"


def sha_and_size(stream):
    h, n = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(1 << 20), b""):
        h.update(chunk)
        n += len(chunk)
    return h.hexdigest(), n


def entry(path, version, today=None):
    with open(path, "rb") as f:
        dl_sha, dl_size = sha_and_size(f)
    with lzma.open(path, "rb") as f:
        ex_sha, ex_size = sha_and_size(f)
    return {
        "name": "Stump OS %s" % version,
        "description": "A Stump on this Pi: open Wi-Fi hotspot, the Stump's pages and rooms, LoRa with a Heltec on USB. "
                       "Nothing to type: join Stump-XXXX, the page opens, /admin sets it up.",
        "url": "https://github.com/%s/releases/download/v%s/stump-os-%s.img.xz" % (REPO, version, version),
        "icon": "https://raw.githubusercontent.com/%s/main/pi/icon.png" % REPO,
        "website": "https://github.com/%s/blob/main/final_firmware/docs/PI.md" % REPO,
        "release_date": (today or datetime.date.today()).isoformat(),
        "extract_size": ex_size,
        "extract_sha256": ex_sha,
        "image_download_size": dl_size,
        "image_download_sha256": dl_sha,
        "devices": ["pi5-64bit", "pi4-64bit", "pi3-64bit"],
    }


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__)
    print(json.dumps({"os_list": [entry(argv[1], argv[2])]}, indent=2))


if __name__ == "__main__":
    main(sys.argv)
