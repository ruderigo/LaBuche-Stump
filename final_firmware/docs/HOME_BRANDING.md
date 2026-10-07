# Your own home page section

Drop a few files on the node's SD card and the top of its home page —
the Stump logo and title — becomes your own: a café's name and logo, a
community's welcome, an event's banner. The language switcher and the
cards (Chat, Billboard, Files, Tools, About) stay.

## The files

Put them in a folder called **`home`** at the top level of the SD card,
next to `tools`:

| File | What it is |
|---|---|
| `home.html` | Your section: either a **piece of HTML** (up to 32 KB) or a **complete page** of its own (see below) |
| `home.fr.html`, `home.en.html`, `home.es.html` | Optional: a version per language. A visitor gets theirs if it exists, `home.html` otherwise |
| `home.json` | Optional: where your section goes (below) |
| `logo.png`, … | Any images or other files your section uses |

**Which file the node uses:** `home.<language>.html`, else `home.html`,
else — if the folder holds exactly **one** `.html` file — that one,
whatever it's called. With several `.html` files and no `home.html`, it
won't guess: name the one you want `home.html`. `/admin` shows, under
**Home page section**, exactly what the node sees — the file it's using,
or why it isn't using any.

**A piece of HTML or a complete page?**

- **A piece of HTML** (no `<!DOCTYPE>` or `<html>`) goes straight into
  the home page, sharing its styles.
- **A complete page** — its own `<!DOCTYPE html>`, `<head>`, styles and
  scripts, like a canvas animation — is shown **in a frame** at the top
  of the home page, so its styles stay inside it (a full-screen page's
  `body` styles would otherwise take over the whole Stump page). Set the
  frame's height in `home.json` (`"height": 360`, from 80 to 1200 pixels;
  280 by default); the width follows the page. A transparent page
  background shows the Stump's colours through it.

Refer to the files in the folder as `/home-file?f=` plus the name:

```html
<img src="/home-file?f=logo.png" alt="Café du Coin">
<h1>Café du Coin</h1>
<p>Free Wi-Fi with no internet: chat, notices and files, right here.</p>
```

## Images, styles and fonts

Everything your section uses goes in the **same `home` folder**, next to
`home.html`:

```
SD card
├── tools/
└── home/
    ├── home.html
    ├── home.json
    ├── logo.png
    ├── style.css
    └── brand.woff2
```

and is referred to as **`/home-file?f=` plus the file name**, wherever a
web address goes — an image, a background, a stylesheet, a font, a
script:

```html
<link rel="stylesheet" href="/home-file?f=style.css">
<img src="/home-file?f=logo.png" alt="Café du Coin">
<div class="banner" style="background-image:url('/home-file?f=banner.jpg')"></div>
```

```css
/* style.css -- addresses inside it use the same form */
@font-face {
  font-family: "Brand";
  src: url("/home-file?f=brand.woff2") format("woff2");
}
.brand-title { font-family: "Brand", sans-serif; color: var(--ember); }
```

- **Always the `/home-file?f=` form.** A plain `src="logo.png"` won't
  work: the home page lives at `/`, so the browser would ask the node for
  `/logo.png`, which it doesn't serve.
- **No subfolders.** `home/images/logo.png` won't load — only plain file
  names are accepted, so nothing can reach outside the folder.
- **Simple names are safest:** letters, digits, `-`, `_`, `.`. A space
  must be written `%20` in the address (`?f=my%20logo.png`); characters
  the card's file system forbids (like `:`) are stripped.
- **Types the node serves correctly:** images (PNG, JPG, GIF, WebP, SVG,
  AVIF, ICO), stylesheets (CSS), fonts (WOFF2, WOFF, TTF, OTF), scripts
  (JS), video (MP4, WebM) and audio (MP3, Ogg). Anything else is sent as
  generic data.
- **Any size works**, but files over 1 MB go on the card with a card
  reader rather than the provisioner.

## Where it goes

**The easy way: `/admin` → Home page section.** It shows what the node
sees in `home/`, and lets you choose:

- **Above the buttons**, **under the buttons**, or **beside them** on the
  left or right (side by side on a wide screen; on a phone, left goes
  above and right goes under);
- **the frame's height**, for a complete page;
- **whether to show it at all** — untick to bring back the Stump logo
  without deleting anything.

Save, then reload the home page. The panel writes `home.json` on the
card for you; editing that file by hand works too:

```json
{"place": "top", "height": 360, "hidden": false}
```

(`height` only matters for a complete page shown in a frame; `"hidden":
true` keeps your files but shows the Stump logo.)

| `place` | On a computer or tablet | On a phone |
|---|---|---|
| `top` (the default) | Above the cards | Above the cards |
| `bottom` | Below the cards | Below the cards |
| `left` | Beside the cards, on the left | Above the cards |
| `right` | Beside the cards, on the right | Below the cards |

Side by side needs a screen at least 760 px wide; the page widens to fit
both. Narrower screens stack them instead.

## Getting them onto the card

Either:

- **With a card reader:** copy the `home` folder to the top level of the
  card, or
- **With the provisioner:** put the `home` folder next to
  `provisioner.py`. `--upload-app` (or full provisioning) copies it to
  the card with the technician tools, skipping files already there.
  Files over 1 MB are left for the card reader — USB copies run at about
  1 KB/s.

Reload the home page to see the change: the node reads the files on each
visit, so there's nothing to restart. To go back to the Stump logo and
title, delete `home.html` (and any `home.<lang>.html`).

## Scripts

`home.html` goes into the page exactly as written, so the browser treats
it like any web page: inline `<script>…</script>` runs, and so does a
script file in the folder (`<script src="/home-file?f=app.js"></script>`).
Everything runs in the **visitor's browser**, never on the node.

**A script in a piece of HTML acts as the visitor.** The node knows
people by their device's address, not a login, so a script there can do
anything the visitor's own browser can on that node: post in chat under
their name, read their private messages, send voice notes as them. Only
put in code you wrote or fully trust — never a script copied from a
website.

**A complete page in its frame is sandboxed:** its scripts run (an
animation, a game, a clock), but in a separate origin, so they can't
read the node's pages as the visitor. That makes a complete page the
safer home for scripts.

**Python:** browsers don't run it, so `<script type="text/python">` is
ignored on its own. An in-browser Python runtime stored on the card can
run it: **Brython** is a single JavaScript file —

```html
<script src="/home-file?f=brython.min.js"></script>
<script type="text/python">
from browser import document
document["greeting"].text = "Bienvenue!"
</script>
<p id="greeting"></p>
<script>addEventListener("load", () => brython())</script>
```

— with Python's standard library as a separate, much larger file, needed
only if your code imports modules. (Brython hasn't been tried on a node
yet.) Pyodide and PyScript need many files in subfolders and tens of MB,
which this folder doesn't support.

## Good to know

- **Your HTML is served exactly as written.** Whatever is on the card
  appears on every visitor's screen, so treat it like your own website.
- **Keep everything on the card.** Visitors on the node's Wi-Fi have no
  internet, so images, fonts, stylesheets or scripts from other websites
  won't load.
- **Use the page's colours** to match the node's theme: `var(--text)`,
  `var(--muted)`, `var(--ember)`, `var(--panel)`, `var(--border)`.
- **A piece of HTML over 32 KB is ignored** and the default page shows;
  `/admin` and the node's log say why. Large images belong in their own
  files, not inside the HTML. (A complete page has no such limit: the
  frame loads it straight from the card.)
- **It didn't show up?** Look at **Home page section** in `/admin`: it
  names the file in use, or the reason — no SD card, no `home` folder,
  no `.html` file, several and none called `home.html`, too big, or
  hidden from that same panel. The
  folder must be `home` at the **top of the SD card** (on the node,
  `/sd/home`); a file copied with `mpremote` to `:/home/` lands in the
  board's own memory instead, where the node doesn't look.
