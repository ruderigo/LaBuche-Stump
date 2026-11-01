# esptool-js (build input for Arborist)

These two files — `esptool-bundle.js` (esptool-js, Apache-2.0) and
`esptool-js-LICENSE.txt` — are **not shipped to the board**. They are read
at build time by `scripts/build_arborist.py` to embed the flashing code
into `Arborist-<release>.html`.

The old on-device flasher (`/flash`, HF-001) was retired in
1.0.0-beta.41: it couldn't flash over the Stump's plain-HTTP Wi-Fi
(Web Serial needs a secure context), and Arborist — a page saved on the
computer and opened from `file://` — replaces it. See
`final_firmware/docs/ARBORIST.md`.
