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

## HF-001 — Browser-based WebSerial flasher

**Hidden from**: the `/tools` page (previously a prominent "Open the
flasher →" button; later replaced with plain CLI provisioning steps
shown directly on the page, and since then removed from `/tools`
entirely — those steps now live only in `README.md`, which
`install_tools_on_node()` pushes to the node's own `/sd/tools/`
alongside the other technician tools).

**Still fully functional at**: `GET /flash` (renders `flasher_ui.py`'s
page) and `GET /fw?f=<name>` (serves staged firmware images/catalog from
`tools_payload/` for it). Neither route was removed or disabled --
anyone who already has the URL, or types it directly, still gets the
working flasher.

**What it is**: an in-browser ESP32 flashing tool using the WebSerial
API (Chrome/Edge desktop only), so a technician could flash a board
without installing `esptool`/`provisioner.py` locally.

**Why hidden**: the CLI flow (`provisioner.py`) is the actual,
supported, tested provisioning path for every board role this project
has (CAM, Heltec RNode bridge, standalone Heltec transport). The
in-browser flasher duplicates part of that without the same testing
history. The provisioning steps themselves used to be duplicated too
— once shown directly on `/tools`, separately from the README's own
copy — which was confusing on its own; that duplication is gone now
that `/tools` shows downloads only and the README is one of the things
downloaded, but the flasher itself remains a second, untested path to
the same goal regardless.

**Decision an epic would need to make**: either (a) invest in it —
re-link it from `/tools`, confirm its firmware catalog
(`tools_payload/flasher/catalog.json`) is kept current alongside
`provisioner.py`'s own firmware URLs, and give it equal testing
priority, or (b) remove it formally — delete `flasher_ui.py`,
`tools_payload/` in full, the `/flash` and `/fw` route handlers, and
the `_DESTINATIONS["flash"]` nav entry and `_ICON_FLASH` (the
`tools_open_flasher`/`tools_flash_header`/`tools_flash_intro` i18n
keys this entry originally flagged for cleanup here are already gone
— removed along with the CLI-instructions section when `/tools` was
later cut down to downloads only), and stop `provisioner.py`'s
`install_tools_on_node()` from pushing `esptool-bundle.js` /
`catalog.json` / firmware images to `/sd/tools` and `/sd/fw`.

**Code locations**:
- `barkeep.py` — `import flasher_ui`, `_DESTINATIONS["flash"]`,
  `_ICON_FLASH`, the `/flash` and `/fw` `elif` branches in the request
  router
- `flasher_ui.py` — the whole file
- `tools_payload/` — the whole folder (`flasher/esptool-bundle.js`,
  `flasher/catalog.json`, `flasher/esptool-js-LICENSE.txt`,
  `images/README.txt`). That last file now also carries a real GPL-3.0
  §6 warning about what staging a firmware binary there would require
  (see the "File storage" / `/sd/tools` section of the main README) --
  worth preserving or relocating that specific warning even if the
  rest of this folder goes away under option (b), since the licensing
  concern it describes doesn't disappear just because this feature
  does.
- `fserv.py` — `FW_DIR`, `list_fw()` (used only by the `/fw` route)
- `provisioner.py` — `install_tools_on_node()`'s `tools_payload`-pushing
  block, and the `":/sd/fw"` mkdir in the same function
- `i18n.py` — `nav_flash` (defined in `_DESTINATIONS` but never
  actually passed to `_nav()` anywhere, so it was already dead before
  this; `tools_open_flasher`/`tools_flash_header`/`tools_flash_intro`
  are gone already, not just unreferenced -- see the note above)
- `README.md` / `docs/COMMANDS.md` — HTTP API tables still list
  `/flash` and `/fw?f=` as real, working routes, which remains accurate
  either way this resolves

---

## HF-002 — Awaiting-slot upload hash field

> **Update**: a real, working admin file-deletion feature (password-gated,
> reusing `stumpid.core.check_admin_password()`, with FIFO storage
> eviction at 75% capacity) shipped since this entry was first written
> — originally as a drawer directly on `/files`, later moved to its own
> unlinked `/admin` page (batch delete, one password covering multiple
> files) once that drawer turned out to overflow its own container and
> demand a password re-entry per file. That's a separate capability
> from the one described below — deletion doesn't touch the
> awaiting-slot mechanism at all, and this hash field is still exactly
> as hidden and exactly as incomplete as when this entry was written.
> Noted here only so nobody assumes "file management got worked on"
> means this specific field did too.

**Hidden from**: the home page's file-upload box (previously a visible
text input labelled "Awaiting-slot hash (optional)" next to the
Upload button).

**Still fully functional underneath**: the input element is still in
the page (`id='uphash'`, `style='display:none'`), so the existing
upload JS (`doUpload()`) still finds it and still sends an `X-Hash`
header on every upload exactly as before. With the field hidden,
nobody can type into it, so that header is now always empty — which
the server already treats as "not an awaiting-slot upload," i.e.
today's behavior is identical to a normal upload for everyone.

**What it is**: `fserv.py`'s own header comment describes the intended
design directly: in "regulated mode," someone with permission (the
comment says "a practitioner") marks a slot as "awaiting hash X" while
physically at the Stump. The matching person later types that same
hash into this field when uploading, which routes their file into that
specific pre-arranged slot (`fserv.py`'s `is_slot` branch in the
`/upload` handler) rather than the general shared folder.

**Why hidden, and the actual root cause found while investigating**:
`fserv.mark_awaiting(hash_hex, note)` — the function that would create
an awaiting slot in the first place — has **zero callers anywhere in
this codebase**. There is no chat command, no admin action, nothing
reachable by a normal operator that invokes it. The visible field was
only ever the consumer half of a two-sided mechanism; the producer half
was never wired into anything a person could actually trigger. That's
a materially different, and more specific, problem than "the label is
unclear" — the feature is genuinely unusable as shipped, not just
under-explained.

**Decision an epic would need to make**: either (a) complete it — add
a real way to call `mark_awaiting()` (an `/admin` subcommand, a
BarKeep command, whatever fits the access-control model in
`README.md`'s "Access control" section) and then re-expose the field
with instructions that actually make sense once the producer side
exists, or (b) remove it — delete `mark_awaiting()`, `AWAITING_FILE`,
`_awaiting()`, the `is_slot` branch in `/upload`'s handler (falling
back to always saving into the normal shared folder), the `uphash`
element and its JS wiring, and the now-unreferenced
`home_awaiting_hash` i18n key.

**Code locations**:
- `barkeep.py` — the `uphash` input (now hidden), `doUpload()`'s
  `X-Hash` header, the `/upload` handler's `is_slot` branch
- `fserv.py` — `mark_awaiting()`, `AWAITING_FILE`, `_awaiting()`, and
  the "Regulated mode" design comment near the top of the file
- `i18n.py` — `home_awaiting_hash` (now unreferenced)

---

## Adding a new entry

Same shape each time: what it is, where it's hidden from, what still
actually works underneath, why it was hidden (cite the specific code
finding, not just an impression), the decision a real epic would need
to make, and every code location touched by that decision either way.
Next ID is **HF-003**.
