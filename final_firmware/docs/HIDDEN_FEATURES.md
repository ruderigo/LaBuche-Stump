# Hidden Features

Features that are **fully present and working in the code**, but have been
deliberately unlinked from the visible UI because they're confusing,
incomplete, or not the currently-supported path -- without deleting the
underlying code, since removing it outright would be a bigger decision than
"stop showing it for now."

Each entry has a stable ID for referencing directly when these get turned
into real JIRA epics. Nothing here has been fixed or removed at the code
level -- this is a map of what's there and why it's hidden, not a changelog
of work done.

---

## HF-001 — Browser-based WebSerial flasher — **retired in 1.0.0-beta.41**

Replaced by **Arborist** (`final_firmware/docs/ARBORIST.md`), a single HTML
page saved on a computer and opened from `file://`. The on-device `/flash`
page served over the Stump's plain-HTTP Wi-Fi, where Web Serial can't run
(it needs a secure context), so it never actually flashed a board there.

Removed: `flasher_ui.py`, the `/flash` and `/fw` routes, the Flash nav
tile and `_ICON_FLASH`, the `nav_flash` string, and the flasher payload
the Provisioner pushed to the card (`catalog.json`, `serial-check.html`,
the board copy of `esptool-bundle.js` + licence, the section READMEs).
`tools_payload/flasher/esptool-bundle.js` and its licence stay in the repo
as build inputs for Arborist.


## HF-002 — Awaiting-slot upload hash field — **removed (1.0.0-beta.35)**

Removed in the cleanup of October 2026: nothing could ever open a slot
(`fserv.mark_awaiting()` had no caller), so the hidden field, the
`X-Hash` header, the slot branch of `/upload`, `AWAITING_FILE` and the
`home_awaiting_hash` key went. `/upload`'s JSON answer still carries
`"slot": false`, so older clients reading it don't break. If regulated
uploads come back, they start from a design with the producer side
first.

---

## HF-003 — The Concierge (BarKeep) chat box — **brought back (October 2026)**

No longer hidden: BarKeep is the node's **librarian** now. Visitors ask
it what's on the node in French, English or Spanish, from the firefly
in the corner of the Files, Billboard and home pages (and `/concierge`,
still there); `POST /chat` answers, and `!find` asks it from the chat.
It was hidden because visitors took the box for the people's chat room;
it now lives where people look for things, labelled with what it does,
and not on the home page. See *BarKeep, the librarian* in the README
and Part 4 of `COMMANDS.md`.

---

## Adding a new entry

Same shape each time: what it is, where it's hidden from, what still
actually works underneath, why it was hidden (cite the specific code
finding, not just an impression), the decision a real epic would need
to make, and every code location touched by that decision either way.
Next ID is **HF-004**.
