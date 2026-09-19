# Project Stump -- BarKeep (chat interface + site router)
# Place at: firmware/barkeep.py
#
# Same routing/logic as before -- this pass only changes presentation.
# All command processing, billboard/fserv reuse, and download handling
# are unchanged from the tested version.
#
# DESIGN NOTE: no external fonts or CDN assets anywhere in this file.
# Anyone connected to Stump's own AP has no route to the wider internet
# at all -- a Google Fonts link, for instance, would just silently fail
# for exactly the people this is built for. Every style below is a
# system font stack or plain CSS, nothing fetched.

import os
import ujson as json
import uasyncio as asyncio
import billboard
import i18n
import fserv
import rrc
import rrc_ui
import flasher_ui

# Per-node greeter name. Falls back so this module still imports on its
# own (tests, or a config.py from a build that predates the setting).
try:
    from config import BOT_NAME
except ImportError:
    BOT_NAME = "BarKeep"

BARKEEP_ART = (
    "    )  (\n"
    "   (    )\n"
    " .--------.\n"
    "/  o  o  o \\\n"
    "|          |\n"
    " \\________/\n"
    "  |  |  |  |\n"
)

STYLE = """
:root{
  --bg:#1b1512; --panel:#2a2119; --ember:#d97a3a; --ember-bright:#f0a050;
  --text:#ecdfc8; --muted:#9c8d76; --border:#493c2e;
}
/* Theme presets -- [data-theme] on <html>, set by the startup script in
   _page() from localStorage. Absent entirely (the default amber look
   above, :root's own values) unless a visitor or the admin has chosen
   something else. --muted isn't mentioned in the original spec's four
   preset definitions, so each one below picks a --muted that actually
   reads against its own --bg/--panel rather than leaving warm amber
   tan sitting on, say, pure black -- the .sub class uses --muted for
   secondary text on every page, so leaving it untouched would be a
   visible, real inconsistency, not a harmless gap. */
[data-theme='phosphor']{
  --bg:#0d140e; --panel:#142217; --ember:#33ff66; --ember-bright:#5cff85;
  --text:#d0f0d6; --muted:#5c8f68; --border:#1f3b25;
}
[data-theme='oled']{
  --bg:#000000; --panel:#121212; --ember:#4da6ff; --ember-bright:#80bfff;
  --text:#f0f0f0; --muted:#8a8a8a; --border:#2a2a2a;
}
[data-theme='paper']{
  --bg:#f5f2eb; --panel:#e8e3d5; --ember:#a84814; --ember-bright:#c25b1f;
  --text:#2c2825; --muted:#7a7264; --border:#d0c8b6;
}
*{box-sizing:border-box;}
body{
  background:var(--bg); color:var(--text);
  font-family:Georgia,'Iowan Old Style','Palatino Linotype',serif;
  max-width:600px; margin:0 auto; padding:28px 18px 40px; line-height:1.55;
}
h1{
  font-family:ui-monospace,'Cascadia Code','SF Mono','Courier New',monospace;
  color:var(--ember); font-size:1.35rem; letter-spacing:.02em; margin:0 0 4px;
  animation:glow 5s ease-in-out infinite;
}
@keyframes glow{
  0%,100%{text-shadow:0 0 6px rgba(217,122,58,.30);}
  50%{text-shadow:0 0 15px rgba(217,122,58,.65);}
}
@media (prefers-reduced-motion:reduce){ h1{animation:none;} }
.sub{color:var(--muted); margin-top:0;}
pre{
  color:var(--ember); font-family:ui-monospace,monospace;
  font-size:.82rem; line-height:1.15; margin:0 0 10px;
}
.panel{
  background:var(--panel); border:1px solid var(--border);
  border-radius:8px; padding:12px 14px; margin:12px 0;
  overflow-wrap:break-word; word-break:break-word;
}
li{overflow-wrap:break-word; word-break:break-word;}
#log{max-height:220px; overflow-y:auto;}
#log p{margin:0 0 8px;}
a{color:var(--ember-bright);}
.row{display:flex; gap:8px; flex-wrap:wrap; margin:10px 0;}
.row input{flex:1; min-width:0;}
input,button{
  font-family:inherit; font-size:1rem; padding:10px 12px;
  border-radius:6px; border:1px solid var(--border);
}
input{background:var(--panel); color:var(--text);}
input:focus,button:focus{outline:2px solid var(--ember); outline-offset:1px;}
button{
  background:var(--ember); color:var(--bg); font-weight:bold;
  border:none; cursor:pointer;
}
button:hover{background:var(--ember-bright);}
ul{list-style:none; padding:0; margin:0;}
li{padding:7px 0; border-bottom:1px solid var(--border);}
li:last-child{border-bottom:none;}
small{color:var(--muted);}

/* File rows. Each is a full-width touch target -- on the kiosk these
   are tapped with a finger, so the whole row is the link, not just the
   filename text. */
ul.files{list-style:none; padding:0; margin:0;}
ul.files li{
  display:flex; align-items:center; justify-content:space-between;
  gap:10px; padding:0; border-bottom:1px solid var(--border);
}
ul.files li:last-child{border-bottom:none;}
ul.files a{
  display:flex; align-items:center; gap:10px; flex:1; min-width:0;
  padding:14px 4px; text-decoration:none; min-height:44px;
}
ul.files a span{overflow-wrap:break-word; word-break:break-word;}
ul.files small{flex-shrink:0; padding-left:8px; text-align:right;}

/* Two destination tiles. Grid so they stay equal width and stack
   sensibly on a narrow phone, which is the common case here. */
/* auto-fit, not a fixed column count: a row of 3 or 4 tiles then fills
   the width evenly instead of wrapping one orphan onto its own line,
   which is what the old two-column grid did. */
.tiles{
  display:grid; gap:10px; margin:14px 0;
  grid-template-columns:repeat(auto-fit, minmax(92px, 1fr));
}
.tile{
  display:flex; flex-direction:column; align-items:center; gap:4px;
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:16px 10px; color:var(--ember); text-decoration:none;
  transition:border-color .15s, color .15s;
}
.tile:hover,.tile:focus{border-color:var(--ember); color:var(--ember-bright); outline:none;}
.tile span{font-weight:bold; font-size:.95rem;}
.tile small{color:var(--muted); text-align:center; line-height:1.25;}
@media (max-width:380px){ .tiles{grid-template-columns:1fr;} }

/* Language switcher. Small and out of the way -- it's used once per
   visit, not something that should compete for attention with the
   actual content on every single page. Touch-sized regardless (44px
   minimum), since this shows on the same kiosk touchscreen as
   everything else here. A border-bottom gives it a clear edge of its
   own rather than floating text with nothing to visually separate it
   from whatever comes next -- the same underlying gap that caused the
   switcher to look like it bled into RRC's room list, fixed there with
   a full bar treatment; here a lighter border is enough since these
   pages are single-column, not sitting flush against a busy sidebar. */
.langbar{
  display:flex; gap:6px; margin:10px 0 14px; padding-bottom:12px;
  border-bottom:1px solid var(--border);
}
.langbar a,.langbar span{
  min-width:38px; min-height:32px; display:flex; align-items:center;
  justify-content:center; padding:4px 10px; border-radius:6px;
  font-size:.8rem; font-weight:bold; text-decoration:none;
  border:1px solid var(--border);
}
.langbar a{color:var(--muted);}
.langbar a:hover,.langbar a:focus{color:var(--ember); border-color:var(--ember); outline:none;}
.langbar span.lang-active{background:var(--ember); color:var(--panel); border-color:var(--ember);}

/* About page. h2 is new -- nothing on any other page currently uses a
   second-level heading, so this is purely additive, not an override of
   existing behaviour. .tiles/.tile are already spoken for by the nav
   system (icon + label, centered) -- the About page's own tiles are a
   different shape entirely (left-aligned info cards, no icon), so they
   get their own names rather than silently colliding with the nav
   tiles' styling the moment both appear in the same stylesheet. */
h2{
  font-family:ui-monospace,'Cascadia Code','SF Mono','Courier New',monospace;
  color:var(--ember-bright); font-size:1.15rem; letter-spacing:.02em;
  border-bottom:1px solid var(--border); padding-bottom:6px; margin:28px 0 12px;
}
.badge-open{
  display:inline-block; font-family:ui-monospace,monospace;
  background:rgba(217,122,58,.15); color:var(--ember-bright);
  border:1px solid var(--ember); border-radius:4px; padding:2px 8px;
  font-size:.8rem; font-weight:bold; margin-bottom:10px;
}
.info-tiles{
  display:grid; gap:10px; margin:14px 0 20px;
  grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));
}
.info-tile{
  display:flex; flex-direction:column; align-items:flex-start; gap:4px;
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:14px 12px; color:var(--ember);
}
.info-tile span{font-weight:bold; font-size:.95rem;}
.info-tile small{color:var(--muted); line-height:1.25; font-size:.8rem;}
.links-grid{
  display:grid; gap:10px; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));
  margin-top:14px;
}
.link-tile{
  display:flex; flex-direction:column; text-decoration:none;
  background:#221b15; border:1px solid var(--border); border-radius:6px;
  padding:12px 14px; transition:border-color .2s, background .2s;
}
.link-tile:hover{border-color:var(--ember); background:#271f18;}
.link-tile .title{
  font-family:ui-monospace,monospace; font-size:.9rem; font-weight:bold;
  color:var(--ember); margin-bottom:2px;
}
.link-tile .desc{
  font-size:.8rem; color:var(--muted); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;
}

/* About page: view tabs (About / Connect / Hardware). A real UI
   interaction switched client-side on purpose -- these are three
   facets of one page, not three destinations, so a server round-trip
   for something this cheap would just be latency with no benefit. */
.tab-bar{display:flex; gap:8px; flex-wrap:wrap; margin:0 0 20px;}
.view-btn{
  min-height:38px; padding:6px 14px; border-radius:6px;
  font-family:ui-monospace,monospace; font-size:.85rem; font-weight:bold;
  background:transparent; border:1px solid var(--border); color:var(--muted);
  cursor:pointer;
}
.view-btn:hover{border-color:var(--ember); color:var(--text);}
.view-btn.active{background:var(--ember); color:var(--panel); border-color:var(--ember); cursor:default;}
.view-pane{display:none;}
.view-pane.active{display:block;}

/* Connect pane: numbered step cards. */
.steps-container{display:flex; flex-direction:column; gap:12px; margin:16px 0 20px;}
.step-item{
  display:flex; gap:14px; background:var(--panel); border:1px solid var(--border);
  border-radius:8px; padding:14px 16px; align-items:flex-start;
}
.step-icon{
  width:24px; height:24px; min-width:24px; stroke:var(--ember); fill:none;
  stroke-width:2; stroke-linecap:round; stroke-linejoin:round; margin-top:2px;
}
.step-body{flex:1;}
.step-title{
  font-family:ui-monospace,monospace; font-weight:bold; font-size:.95rem;
  color:var(--ember-bright); margin-bottom:4px;
}
.step-text{font-size:.95rem; color:var(--text); margin:0; line-height:1.45;}
code.ip-tag{
  font-family:ui-monospace,monospace; background:var(--bg);
  border:1px solid var(--border); color:var(--ember-bright);
  padding:2px 8px; border-radius:4px; font-size:.9rem; display:inline-block;
}

/* Hardware pane: photo gallery. Two columns on anything wide enough
   to show them side by side, one column on a narrow kiosk/phone. */
.gallery-grid{display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));}
.gallery-card{
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  overflow:hidden;
}
.gallery-card img{width:100%; height:auto; display:block; background:#161210;}
.gallery-card .caption{
  padding:8px 10px; display:flex; flex-direction:column; gap:2px;
}
.gallery-card .caption span:first-child{font-weight:bold; font-size:.85rem; color:var(--ember);}
.gallery-card .caption span:last-child{font-size:.75rem; color:var(--muted);}
"""


def _esc_name(s):
    """Escapes the operator-set greeter name.

    It lands in HTML and, in the chat script, inside a JS string literal
    -- so an apostrophe (entirely plausible in a name like O'Malley)
    would otherwise break the page. Escaping the quote characters covers
    both cases, since the surrounding JS uses single quotes."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


# Two destinations, as tiles rather than the text links they used to be.
#
# Inline SVG on purpose. Anyone connected to the Stump's own AP has no
# route to the wider internet, so an icon font or a CDN sprite sheet
# would silently fail to load for precisely the people this is built
# for -- the same reasoning the stylesheet already follows. These are a
# few hundred bytes and always work.
_ICON_FILES = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M4 6.5A1.5 1.5 0 0 1 5.5 5h4L11 7h7.5"
    "A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z'/>"
    "</svg>"
)

_ICON_DOWNLOAD = (
    "<svg viewBox='0 0 24 24' width='18' height='18' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M12 4v10M8 11l4 3 4-3'/>"
    "<path d='M5 18.5h14'/></svg>"
)

_ICON_TOOLS = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M14.5 6.5a3.5 3.5 0 0 0 4.6 4.6l-7.2 7.2"
    "a2.3 2.3 0 0 1-3.2-3.2z'/><path d='M14.5 6.5 17 4l3 3-2.5 2.5'/></svg>"
)

_ICON_FLASH = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M13 2 4.5 13.5H11l-.5 8.5L19 10.5h-6.5z'/>"
    "</svg>"
)

_ICON_ABOUT = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><circle cx='12' cy='12' r='9'/>"
    "<path d='M12 11v5.5'/><circle cx='12' cy='7.7' r='.15' fill='currentColor' "
    "stroke-width='1.4'/></svg>"
)

_ICON_HOME = (
    "<svg viewBox='0 0 24 24' width='24' height='24' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M3 10.5 12 3l9 7.5'/>"
    "<path d='M5.5 9.5V20h13V9.5'/></svg>"
)

_ICON_CHAT = (
    "<svg viewBox='0 0 24 24' width='30' height='30' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'>"
    "<path d='M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-4.2-.9L3 21l1.9-4.4"
    "A8.4 8.4 0 0 1 12 3.1a8.4 8.4 0 0 1 9 8.4z'/>"
    "<path d='M8.5 10.5h7M8.5 14h4.5'/></svg>"
)
_ICON_BOARD = (
    "<svg viewBox='0 0 24 24' width='30' height='30' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'>"
    "<rect x='3' y='4' width='18' height='15' rx='1.5'/>"
    "<path d='M3 8h18M12 19v2M8 21h8'/>"
    "<path d='M6.5 11.5h5M6.5 14.5h8'/></svg>"
)

def _home_tiles(lang):
    """The three big tiles on the home page. Kept separate from _nav()
    (which builds the smaller nav-bar tiles used on every OTHER page)
    because these carry a subtitle line the compact nav tiles don't --
    was a static module-level constant with hardcoded English text;
    now built fresh per request in the requesting client's language.
    Uses the same nav_board/tile_board_sub keys FILES_NAV etc. use for
    the billboard tile, unifying what were two independently-written
    English labels ("Billboard" here, "Board" elsewhere) that meant the
    same thing -- no reason to translate one concept two different ways
    just because the original English text happened to say it twice."""
    return (
        "<div class='tiles'>"
        "<a class='tile' href='/rrc'>" + _ICON_CHAT +
        "<span>" + i18n.t("nav_chat", lang) + "</span>"
        "<small>" + i18n.t("tile_chat_sub", lang) + "</small></a>"
        "<a class='tile' href='/billboard'>" + _ICON_BOARD +
        "<span>" + i18n.t("nav_board", lang) + "</span>"
        "<small>" + i18n.t("tile_board_sub", lang) + "</small></a>"
        "<a class='tile' href='/files'>" + _ICON_FILES +
        "<span>" + i18n.t("nav_files", lang) + "</span>"
        "<small>" + i18n.t("tile_files_sub", lang) + "</small></a>"
        "<a class='tile' href='/about'>" + _ICON_ABOUT +
        "<span>" + i18n.t("nav_about", lang) + "</span>"
        "<small>" + i18n.t("tile_about_sub", lang) + "</small></a>"
        "</div>"
    )


def _page(body):
    return ("<!DOCTYPE html><html><head><meta name='viewport' "
             "content='width=device-width, initial-scale=1'>"
             "<style>" + STYLE + "</style>"
             "<script>" + _THEME_STARTUP_SCRIPT + "</script>"
             "</head><body>" + body + "</body></html>")


# Runs before body content renders, so a returning visitor's chosen
# theme/logo apply immediately rather than flashing default styling
# first. Wrapped in try/catch per localStorage access -- private
# browsing mode can make localStorage throw outright rather than just
# return null on some browsers, and a malformed stump_custom_colors
# value (hand-edited devtools, an old format from a future version)
# would otherwise take the whole page down over a cosmetic preference.
# Logo swapping/hiding waits for DOMContentLoaded since #site-logo
# doesn't exist yet at this point in <head>; the theme variables don't
# need that wait, since CSS custom properties apply to <html> whether
# or not <body> has rendered.
_THEME_STARTUP_SCRIPT = """
(function(){
  try {
    var t = localStorage.getItem('stump_theme');
    if (t === 'custom') {
      var c = JSON.parse(localStorage.getItem('stump_custom_colors') || '{}');
      for (var k in c) document.documentElement.style.setProperty(k, c[k]);
    } else if (t) {
      document.documentElement.setAttribute('data-theme', t);
    }
  } catch (e) {}
  try {
    var customSvg = localStorage.getItem('stump_custom_svg');
    var hidden = localStorage.getItem('stump_logo_hidden') === '1';
    if (customSvg || hidden) {
      window.addEventListener('DOMContentLoaded', function(){
        var el = document.getElementById('site-logo');
        if (!el) return;
        if (customSvg) el.innerHTML = customSvg;
        if (hidden) el.style.display = 'none';
      });
    }
  } catch (e) {}
})();
"""


def _url_encode(s):
    """Minimal percent-encoding for filenames used in href query strings.
    MicroPython has no urllib.parse.quote built in. Without this, a
    filename containing a space, '&', or '#' breaks the resulting link
    outright -- a real bug, not a style nit, and one that upload (below)
    would have exposed immediately once reachable."""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = ""
    for ch in s:
        if ch in safe:
            out += ch
        else:
            out += "%%%02X" % ord(ch)
    return out


def _clean_filename(name):
    """No path traversal, no quote-breaking, and -- a real, reported
    bug -- no FAT32/exFAT-reserved characters either.

    The SD card these files land on is FAT32/exFAT. Confirmed directly
    against a real FAT32 filesystem image, not assumed: a filename
    containing a colon (a real screenshot's own default name --
    "screenshot 2026.12:18pm EST.png") fails to write at the
    filesystem level outright, while the identical name with the colon
    removed writes successfully. stream_to_file()'s open(dest, "wb")
    would raise on a name like that, and its own except already turns
    that into a real 500 response -- but from the browser's side, a
    request that fails this early and this completely is
    indistinguishable from the connection itself never having worked
    at all: no partial response, nothing in the console, nothing in
    the network tab, just a silent, unexplained failure. Given real
    filenames come from whatever device and OS a visitor's browser or
    phone happened to generate them on -- none of which know or care
    that this specific board's storage is FAT32/exFAT -- sanitizing
    every character that filesystem actually reserves, not just the
    two this originally handled, is what makes upload robust to a name
    like that instead of asking every visitor to rename their file
    first. `<>:\\|?*` are the same reserved set FAT32/exFAT (and,
    since they share it, Windows) has always disallowed; control
    characters are invalid there regardless of what produced them.
    """
    name = name.replace("/", "_").replace("..", "_").replace("'", "").replace('"', "")
    for ch in "<>:\\|?*":
        name = name.replace(ch, "_")
    name = "".join(c for c in name if ord(c) >= 32)
    return name.strip()


def _process_command(text, identifier, lang):
    cmd = text.strip()
    lower = cmd.lower()

    if lower in ("menu", "help", ""):
        # 'balance' is only listed when there's actually a tab to check.
        balance_line = i18n.t("bot_menu_balance", lang) if fserv.CREDITS_ENABLED else ""
        return (
            i18n.t("bot_menu_header", lang)
            + i18n.t("bot_menu_menu", lang)
            + i18n.t("bot_menu_chat", lang)
            + i18n.t("bot_menu_billboard", lang)
            + i18n.t("bot_menu_files", lang)
            + i18n.t("bot_menu_get", lang)
            + balance_line
            + i18n.t("bot_menu_upload", lang)
        )

    if lower in ("upload", "how to upload", "how do i upload", "share", "share a file"):
        # Don't promise credit that isn't being given.
        if fserv.CREDITS_ENABLED:
            return i18n.t("bot_upload_credit", lang)
        return i18n.t("bot_upload_free", lang)

    if lower in ("chat", "rrc", "irc", "talk"):
        return i18n.t("bot_chat_pointer", lang)

    if lower == "billboard":
        return i18n.t("bot_billboard_pointer", lang)

    if lower == "files":
        if not fserv.sd_ok:
            return i18n.t("bot_files_no_card", lang)
        names = fserv._list_files()
        if not names:
            return i18n.t("bot_files_empty", lang)
        lines = []
        for n in names:
            cls = fserv.guess_class(n)
            safe_name = billboard._esc(n)
            # Class is always useful; the cost label only means something
            # when credits are actually being charged. Showing "1 credit"
            # in free mode would be a straightforwardly false statement.
            if fserv.CREDITS_ENABLED:
                tag = " <small>(" + cls + ", " + str(fserv.credit_cost(n)) + " credit)</small>"
            else:
                tag = " <small>(" + cls + ")</small>"
            lines.append(
                "&nbsp;&nbsp;<a href='/download?f=" + _url_encode(n) + "'>" + safe_name +
                "</a>" + tag
            )
        return i18n.t("bot_files_header", lang) + "<br>".join(lines)

    if lower.startswith("get "):
        fname = _clean_filename(cmd[4:])
        if not fname:
            return i18n.t("bot_get_what", lang)
        return i18n.t("bot_get_here", lang, href=_url_encode(fname), name=billboard._esc(fname))

    if lower == "balance":
        if not fserv.CREDITS_ENABLED:
            return i18n.t("bot_balance_free", lang)
        bal = fserv.credit_balance(identifier)
        unit = i18n.t("bot_credit_singular", lang) if bal == 1 else i18n.t("bot_credit_plural", lang)
        return i18n.t("bot_balance_have", lang, n=str(bal), unit=unit)

    return i18n.t("bot_unknown", lang)


def _render_chat_page(lang):
    # Escaped, not just embedded raw, even though today's three
    # translations ("Toi"/"You"/"Tú") happen to contain nothing that
    # would break the surrounding single-quoted JS string below --
    # that's exactly the assumption that just failed for a different
    # string in this same script block (see the real, confirmed bug
    # this line sits next to), so this stays escaped on the same
    # principle even though it isn't broken today.
    you_label = billboard._esc(i18n.t("home_you_label", lang))
    return (
        # Restored to the original ASCII art by request after real
        # testing -- the concentric-ring SVG this held before read as
        # a target/bullseye rather than the tree-stump cross-section it
        # was meant to be, and the person who's actually looked at it
        # in a browser wanted the original back. The #site-logo wrapper
        # stays: an admin who DOES want a custom SVG can still set one
        # from /admin, which replaces this element's content the exact
        # same way regardless of what's inside it by default. No
        # forced inline style here either -- the existing pre{} rule
        # already colors and sizes this correctly (color:var(--ember),
        # monospace, proper line-height); the SVG-specific fill/
        # max-width/display rules that were here don't mean anything
        # applied to text and would have squeezed the art oddly if
        # they'd stuck around.
        "<div id='site-logo'><pre>" + BARKEEP_ART + "</pre></div>"
        "<h1>Stump</h1>"
        + _lang_switcher(lang, "/") +
        "<p class='sub'>" + i18n.t("barkeep_greeting", lang, bot_name=_esc_name(BOT_NAME)) + "</p>"
        "<div class='panel' id='log'>"
        "<p><b>" + _esc_name(BOT_NAME) + ":</b> " + i18n.t("barkeep_evening", lang) + "</p>"
        "</div>"
        "<div class='row'>"
        "<input id='in' placeholder='" + i18n.t("home_say_something", lang) + "' autofocus>"
        "<button onclick='sendMsg()'>" + i18n.t("rrc_send", lang) + "</button>"
        "</div>"
        + _home_tiles(lang) +
        "<div class='panel'>"
        "<p class='sub' style='margin-top:0;'>" + i18n.t("home_bring_something", lang) + "</p>"
        "<input type='file' id='upfile'>"
        "<div class='row'>"
        "<input id='uphash' style='display:none' value=''>"
        "<button id='upbtn' onclick='doUpload()'>" + i18n.t("home_upload_button", lang) + "</button>"
        "</div>"
        "<p id='upstatus'><small></small></p>"
        "</div>"
        # doUpload() below: three real, reported gaps fixed together --
        # no .catch() on the fetch() chain meant any failure on the
        # server's side of this request (the actual exception source is
        # fixed in fserv.py/barkeep.py, but this is the client's own
        # last line of defense against any OTHER cause too, network
        # failure included) left the promise rejecting with nothing to
        # handle it: the status line stuck on "Sending..." forever, no
        # success shown and no error either, indistinguishable from the
        # upload having silently failed even when the file itself made
        # it onto the card intact. Disabling #upbtn for the duration
        # also stops a second, overlapping upload from a repeated or
        # impatient click, since nothing here queues or cancels one
        # already in flight. Clearing the file input once a response
        # actually arrives (success or a server-side error message,
        # either counts) is itself a visible sign something happened,
        # independent of whatever text the response carries.
        "<script>"
        "function doUpload(){"
        "  var file=document.getElementById('upfile').files[0];"
        "  if(!file){return;}"
        "  var hash=document.getElementById('uphash').value;"
        "  var status=document.getElementById('upstatus');"
        "  var btn=document.getElementById('upbtn');"
        "  btn.disabled=true;"
        "  status.innerHTML='<small>" + billboard._esc(i18n.t("home_sending", lang)) + "</small>';"
        "  fetch('/upload',{method:'POST',headers:{'X-Filename':file.name,'X-Hash':hash},body:file})"
        "    .then(function(r){return r.text();})"
        "    .then(function(t){"
        "      status.innerHTML='<small>'+t+'</small>';"
        "      document.getElementById('upfile').value='';"
        "      btn.disabled=false;"
        "    })"
        "    .catch(function(){"
        "      status.innerHTML='<small>" + billboard._esc(i18n.t("home_upload_error", lang)) + "</small>';"
        "      btn.disabled=false;"
        "    });"
        "}"
        "function sendMsg(){"
        "  var input=document.getElementById('in');"
        "  var text=input.value;"
        "  if(!text)return;"
        "  var log=document.getElementById('log');"
        "  log.innerHTML+='<p><b>" + you_label + ":</b> '+text.replace(/</g,'&lt;')+'</p>';"
        "  input.value='';"
        "  fetch('/chat',{method:'POST',body:text})"
        "    .then(function(r){return r.text();})"
        "    .then(function(t){"
        "      log.innerHTML+='<p><b>"+_esc_name(BOT_NAME)+":</b> '+t+'</p>';"
        "      log.scrollTop=log.scrollHeight;"
        "      input.focus();"
        "    });"
        "}"
        "document.getElementById('in').addEventListener('keydown',function(e){"
        "  if(e.key==='Enter'){sendMsg();}"
        "});"
        "</script>"
    )


# Same escape hatches as the chat page. On a kiosk every page needs to
# reach every other in one touch -- going Billboard -> Home -> Chat is
# two deliberate taps on a wall panel, and the old page couldn't reach
# chat at all.
_CHAT_SMALL = _ICON_CHAT.replace("width='30' height='30'", "width='24' height='24'")
_BOARD_SMALL = _ICON_BOARD.replace("width='30' height='30'", "width='24' height='24'")

# Every destination in one table, so a page picks a set rather than
# hand-assembling markup. Three near-identical nav blocks had already
# drifted apart on tile count, which is what left the grid with holes.
# Labels are i18n KEYS, not literal text -- looked up per request
# against the requesting client's language, since these render fresh
# on every page load rather than once at import time now.
_DESTINATIONS = {
    "home":  ("/", _ICON_HOME, "nav_home"),
    "chat":  ("/rrc", _CHAT_SMALL, "nav_chat"),
    "board": ("/billboard", _BOARD_SMALL, "nav_board"),
    "files": ("/files", _ICON_FILES, "nav_files"),
    "tools": ("/tools", _ICON_TOOLS, "nav_tools"),
    "flash": ("/flash", _ICON_FLASH, "nav_flash"),
    "about": ("/about", _ICON_ABOUT, "nav_about"),
}


def _nav(lang, *keys):
    """Builds a tile row in the requesting client's language. The grid
    is auto-fit, so whatever number of tiles a page asks for spreads
    evenly across the row instead of leaving an orphan on a second
    line.

    Was a module-level constant, built once at import time with
    hardcoded English labels -- that stopped being possible the moment
    labels depend on who's asking, so this is now called fresh inside
    each page renderer instead."""
    out = ["<div class='tiles'>"]
    for k in keys:
        href, icon, label_key = _DESTINATIONS[k]
        out.append("<a class='tile' href='" + href + "'>" + icon +
                    "<span>" + i18n.t(label_key, lang) + "</span></a>")
    out.append("</div>")
    return "".join(out)


def _lang_switcher(lang, current_path):
    """Thin wrapper over the shared builder in i18n.py -- one
    implementation used by both this server-rendered page and
    rrc_ui.py's client page, rather than two that could drift apart."""
    return i18n.switcher_html(lang, current_path)



def _human_size(n):
    """Bytes as something a person can judge at a glance -- 'is this
    worth waiting for on a slow link' is the actual question here."""
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.0f KB" % (n / 1024)
    return "%.1f MB" % (n / (1024 * 1024))


def _check_admin_pw(pw):
    """True if pw unlocks admin actions on this node -- the one real
    check, reused by every admin-gated route rather than each one
    duplicating the same soft-import. Delegates entirely to
    stumpid.core.check_admin_password (fail-closed, falls back to
    fservbot's operator password -- see that function's own docstring).
    Soft-imported the same way rrc_mesh.py already does for stumpid:
    an admin route must still exist and correctly refuse on a node
    where the plugin isn't installed at all, not crash with an
    ImportError."""
    try:
        import stumpid.core as _stumpid
        return _stumpid.check_admin_password(pw)
    except ImportError:
        return False


def _render_admin_login_page(lang, error=False):
    """The ONLY way to reach file deletion now -- no link, no nav tile,
    no button anywhere in the visible UI points here. Reachable only by
    typing the address directly, which is the point: the drawer this
    replaced sat on /files where every visitor saw it exist, even
    collapsed, and a wrong password there had to be re-typed once per
    file. This page asks for the password exactly once regardless of
    how many files get selected next.
    """
    err = "<p class='sub' style='color:var(--ember-bright);'>" + i18n.t("admin_wrong_password", lang) + "</p>" if error else ""
    return (
        "<h1>" + i18n.t("admin_header", lang) + "</h1>"
        + err +
        "<form method='POST' action='/admin' class='row'>"
        "<input type='password' name='admin_pass' placeholder='"
        + i18n.t("files_admin_password", lang) + "' autofocus>"
        "<button type='submit'>" + i18n.t("admin_login_button", lang) + "</button>"
        "</form>"
    )


def _render_admin_file_list_page(lang, names, pw):
    """Shown only after a correct password. The password travels
    forward as a hidden field on the SAME simple, stateless pattern
    this whole project already uses everywhere else (no sessions, no
    cookies) -- re-validated for real by /admin/delete when the form
    comes back, never trusted just because it arrived in a hidden
    field. One password entry covers selecting as many files as
    wanted; the batch submits together as a single delete.

    Also carries theme and logo customization -- unrelated to file
    deletion, but gated behind the same login rather than a second
    password prompt, since both are "things only an admin should
    change" and asking twice would be the exact kind of friction the
    /admin redesign was built to remove. Shown regardless of whether
    there are any files to delete, unlike the checkbox list below.
    """
    if not names:
        file_section = "<p class='sub'>" + i18n.t("files_none_yet", lang) + "</p>"
    else:
        items = "".join(
            "<li><label><input type='checkbox' name='f' value='" + billboard._esc(n) + "'> "
            + billboard._esc(n) + "</label></li>"
            for n in names
        )
        file_section = (
            "<form method='POST' action='/admin/delete'>"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<div class='panel'><ul class='files'>" + items + "</ul></div>"
            "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
            "</form>"
        )

    return (
        "<h1>" + i18n.t("admin_header", lang) + "</h1>"
        "<p class='sub'>" + i18n.t("admin_select_intro", lang) + "</p>"
        + file_section
        + _render_admin_billboard_section(lang, pw)
        + _render_admin_settings_section(lang)
    )


def _render_admin_billboard_section(lang, pw):
    """Moderation for the walk-up bulletin board -- added on request,
    specifically for removing something inappropriate rather than
    waiting up to 72 hours for BILLBOARD_TTL_SECONDS to age it out on
    its own. Same password-gated pattern as file deletion above: one
    hidden field carries the already-validated password forward,
    checked again for real by /admin/delete_post rather than trusted
    just because it arrived with the form.

    Each post is identified by its own timestamp (see
    billboard.delete_entry's own docstring for why that's a reasonable
    identifier here without adding a separate ID field to the storage
    format), carried as the checkbox's value -- HTML-escaped for safe
    embedding, not URL-encoded, the same fix that corrected the file
    checkboxes after a real double-encoding bug there.

    Calls billboard._prune_and_write() first, with no new entry, purely
    to guarantee every entry has a REAL, stored timestamp before any
    checkbox is built. Without this, a post that predates the
    auto-purge feature and hasn't been through a prune pass yet (the
    board's very first admin visit after upgrading, before anyone has
    posted since) reads back from _read_entries() with ts=None --
    which rendered as a checkbox value='None', and deleting it always
    silently failed: delete_entry() can only ever match a stored line
    with a real, three-field timestamp, and an old, never-pruned entry
    is still stored as its original two-field line with no timestamp
    at all. Confirmed directly: that exact sequence reproduced "0
    deleted" with the entry still present, matching a real report.
    Pruning here first means _read_entries() right after always sees
    the same freshly-stamped timestamp that gets written to disk, so
    the two can never disagree.
    """
    billboard._prune_and_write()
    entries = billboard._read_entries()
    if not entries:
        return (
            "<h2 class='sub'>" + i18n.t("admin_billboard_header", lang) + "</h2>"
            "<p class='sub'>" + i18n.t("billboard_nothing_yet", lang) + "</p>"
        )
    items = "".join(
        "<li><label><input type='checkbox' name='ts' value='" + billboard._esc(str(ts)) + "'> "
        + billboard._esc(text) + " <small>&mdash; " + billboard._esc(sig) + "</small></label></li>"
        for sig, text, ts in reversed(entries)
    )
    return (
        "<h2 class='sub'>" + i18n.t("admin_billboard_header", lang) + "</h2>"
        "<form method='POST' action='/admin/delete_post'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'><ul class='files'>" + items + "</ul></div>"
        "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
        "</form>"
    )


def _render_admin_settings_section(lang):
    """Theme presets, a custom color palette, and SVG logo controls --
    all of it client-side, persisted in the ADMIN'S OWN BROWSER via
    localStorage, not written to the server or the SD card anywhere.
    Worth being direct about since it's easy to assume otherwise for a
    'branding' feature: setting a theme or logo here changes what this
    one browser sees on its own next visit. It does not change what
    any other visitor sees, and there is currently no way to make a
    theme or logo choice apply site-wide to everyone -- that would be
    server-side storage, which this deliberately isn't.
    """
    return (
        "<h2 class='sub'>" + i18n.t("admin_theme_header", lang) + "</h2>"
        "<div class='panel'>"
        "<div class='row'>"
        "<button type='button' onclick=\"stumpSetTheme('default')\">" + i18n.t("admin_theme_default", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('phosphor')\">" + i18n.t("admin_theme_phosphor", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('oled')\">" + i18n.t("admin_theme_oled", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('paper')\">" + i18n.t("admin_theme_paper", lang) + "</button>"
        "</div>"
        "<p class='sub'>" + i18n.t("admin_theme_custom_intro", lang) + "</p>"
        "<div class='row'>"
        "<label>" + i18n.t("admin_color_bg", lang) + " <input type='color' id='stump-c-bg' value='#1b1512'></label>"
        "<label>" + i18n.t("admin_color_panel", lang) + " <input type='color' id='stump-c-panel' value='#2a2119'></label>"
        "<label>" + i18n.t("admin_color_text", lang) + " <input type='color' id='stump-c-text' value='#ecdfc8'></label>"
        "<label>" + i18n.t("admin_color_ember", lang) + " <input type='color' id='stump-c-ember' value='#d97a3a'></label>"
        "<label>" + i18n.t("admin_color_border", lang) + " <input type='color' id='stump-c-border' value='#493c2e'></label>"
        "</div>"
        "<div class='row'>"
        "<button type='button' onclick='stumpSaveCustomPalette()'>" + i18n.t("admin_save_palette", lang) + "</button>"
        "<button type='button' onclick='stumpResetPalette()'>" + i18n.t("admin_reset_palette", lang) + "</button>"
        "</div>"
        "</div>"

        "<h2 class='sub'>" + i18n.t("admin_logo_header", lang) + "</h2>"
        "<div class='panel'>"
        "<textarea id='stump-svg-input' rows='6' placeholder='<svg ...>...</svg>' "
        "style='width:100%;font-family:ui-monospace,monospace;font-size:.8rem;'></textarea>"
        "<div class='row'>"
        "<button type='button' onclick='stumpSaveSvgLogo()'>" + i18n.t("admin_save_logo", lang) + "</button>"
        "<button type='button' onclick='stumpResetLogo()'>" + i18n.t("admin_reset_logo", lang) + "</button>"
        "<button type='button' onclick='stumpHideLogo()'>" + i18n.t("admin_hide_logo", lang) + "</button>"
        "</div>"
        "</div>"

        "<script>" + _ADMIN_SETTINGS_SCRIPT.replace(
            "I18N_LOGO_RESET_NOTICE",
            json.dumps(i18n.t("admin_logo_reset_notice", lang)).replace("</", "<\\/")
        ) + "</script>"
    )


# All four stumpSet*/stumpSave*/stumpReset* functions are plain globals,
# not wrapped in an IIFE -- they're referenced from onclick= attributes
# on this same page, which need them reachable on window.
_ADMIN_SETTINGS_SCRIPT = """
(function(){
  try {
    var saved = JSON.parse(localStorage.getItem('stump_custom_colors') || '{}');
    var map = {'--bg':'stump-c-bg','--panel':'stump-c-panel','--text':'stump-c-text','--ember':'stump-c-ember','--border':'stump-c-border'};
    for (var k in map) {
      if (saved[k]) { var el = document.getElementById(map[k]); if (el) el.value = saved[k]; }
    }
    var svg = localStorage.getItem('stump_custom_svg');
    if (svg) { var ta = document.getElementById('stump-svg-input'); if (ta) ta.value = svg; }
  } catch (e) {}
})();

function stumpSetTheme(t){
  try {
    localStorage.removeItem('stump_custom_colors');
    document.documentElement.removeAttribute('style');
    if (t === 'default') {
      localStorage.removeItem('stump_theme');
      document.documentElement.removeAttribute('data-theme');
    } else {
      localStorage.setItem('stump_theme', t);
      document.documentElement.setAttribute('data-theme', t);
    }
  } catch (e) {}
}

function stumpSaveCustomPalette(){
  try {
    var colors = {
      '--bg': document.getElementById('stump-c-bg').value,
      '--panel': document.getElementById('stump-c-panel').value,
      '--text': document.getElementById('stump-c-text').value,
      '--ember': document.getElementById('stump-c-ember').value,
      '--border': document.getElementById('stump-c-border').value
    };
    localStorage.setItem('stump_custom_colors', JSON.stringify(colors));
    localStorage.setItem('stump_theme', 'custom');
    document.documentElement.removeAttribute('data-theme');
    for (var k in colors) document.documentElement.style.setProperty(k, colors[k]);
  } catch (e) {}
}

function stumpResetPalette(){
  try {
    localStorage.removeItem('stump_custom_colors');
    localStorage.removeItem('stump_theme');
    document.documentElement.removeAttribute('data-theme');
    document.documentElement.removeAttribute('style');
  } catch (e) {}
}

function stumpSaveSvgLogo(){
  try {
    var svg = document.getElementById('stump-svg-input').value;
    if (!svg.trim()) return;
    localStorage.setItem('stump_custom_svg', svg);
    localStorage.removeItem('stump_logo_hidden');
    var el = document.getElementById('site-logo');
    if (el) { el.innerHTML = svg; el.style.display = ''; }
  } catch (e) {}
}

function stumpResetLogo(){
  try {
    localStorage.removeItem('stump_custom_svg');
    localStorage.removeItem('stump_logo_hidden');
    var ta = document.getElementById('stump-svg-input');
    if (ta) ta.value = '';
  } catch (e) {}
  // A page reload, not a DOM patch here -- the default mark is server-
  // rendered HTML in _render_chat_page, not something this page (the
  // admin settings page, a different page entirely) has a copy of to
  // restore from client-side.
  alert(I18N_LOGO_RESET_NOTICE);
}

function stumpHideLogo(){
  try {
    localStorage.setItem('stump_logo_hidden', '1');
    var el = document.getElementById('site-logo');
    if (el) el.style.display = 'none';
  } catch (e) {}
}
"""


def _render_files_page(lang):
    """A real page for the shelf, not just a chat reply.

    The file list previously existed only as a BarKeep command, which
    meant finding a download required knowing to type 'files' first.
    On a kiosk with no keyboard in reach that is close to unusable.

    No admin/delete UI on this page at all -- that lives entirely at
    /admin now, a page with no link pointing to it anywhere, reachable
    only by typing the address directly. See _render_admin_page()'s own
    docstring for why.
    """
    if not fserv.sd_ok:
        rows = "<p class='sub'>" + i18n.t("files_no_card", lang) + "</p>"
    else:
        names = fserv._list_files()
        if not names:
            rows = "<p class='sub'>" + i18n.t("files_none_yet", lang) + "</p>"
        else:
            items = []
            for n in names:
                cls = fserv.guess_class(n)
                try:
                    size = _human_size(os.stat(fserv.SHARED_DIR + "/" + n)[6])
                except Exception:
                    size = ""
                meta = cls + (", " + size if size else "")
                if fserv.CREDITS_ENABLED:
                    meta += " &middot; %d %s" % (fserv.credit_cost(n), i18n.t("files_credit_suffix", lang))
                items.append(
                    "<li><a href='/download?f=" + _url_encode(n) + "'>"
                    + _ICON_DOWNLOAD + "<span>" + billboard._esc(n) + "</span>"
                    "</a><small>" + meta + "</small></li>"
                )
            rows = "<ul class='files'>" + "".join(items) + "</ul>"

    return (
        "<h1>" + i18n.t("nav_files", lang) + "</h1>"
        + _lang_switcher(lang, "/files") +
        "<p class='sub'>" + i18n.t("files_tap_to_download", lang) + "</p>"
        "<div class='panel'>" + rows + "</div>"
        + _nav(lang, "home", "chat", "board", "tools", "about")
    )


def _render_tools_page(lang):
    """Technician tools, on their own page.

    Split out of the file list deliberately. Visitors browsing for
    something to read or listen to have no use for a flashing tool, and
    mixing the two made the file page longer for everyone to serve a
    case that only comes up when a technician is standing there.

    The point: someone can walk up to a Stump with nothing but a laptop
    and pull down the tool that configures it. No USB stick to forget,
    no wondering whether the copy on your desktop is the one that
    matches this build -- the node hands you the version it was
    provisioned with.

    Downloaded here and run on the laptop, exactly as before. The
    Provisioner needs esptool, mpremote and rnodeconf, which are CPython
    programs; none of that changes, and none of it runs on the board.
    """
    tools = fserv.list_tools()
    items = []
    for t in tools:
        try:
            size = _human_size(os.stat(fserv.TOOLS_DIR + "/" + t)[6])
        except Exception:
            size = ""
        items.append(
            "<li><a href='/tool?f=" + _url_encode(t) + "'>"
            + _ICON_DOWNLOAD + "<span>" + billboard._esc(t) + "</span>"
            "</a><small>" + size + "</small></li>"
        )
    if items:
        listing = "<div class='panel'><ul class='files'>" + "".join(items) + "</ul></div>"
    else:
        listing = "<div class='panel'><p class='sub'>" + i18n.t("tools_none_installed", lang) + "</p></div>"

    return (
        "<h1>" + i18n.t("tools_header", lang) + "</h1>"
        + _lang_switcher(lang, "/tools") +
        "<p class='sub'>" + i18n.t("tools_intro", lang) + "</p>"
        + listing
        + _nav(lang, "home", "files", "chat", "about")
    )


def _current_ap_ip():
    """The AP's actual current IP, queried live rather than assumed --
    same technique captive_portal.py itself uses at boot, so the About
    page's connection instructions always describe the real address
    rather than a hardcoded example that could silently go stale.
    Falls back to the standard default if the interface can't be read
    for any reason, matching captive_portal.py's own defensive fallback."""
    try:
        import network
        return network.WLAN(network.AP_IF).ifconfig()[0]
    except Exception:
        return "192.168.4.1"


def _render_about_page(lang):
    """The About page: what Stump/Fireflies are, how to connect, and a
    hardware gallery -- three views in one page, switched client-side
    (a real UI interaction, not a navigation -- no reason to round-trip
    the server for it), with the SAME server-side language handling
    every other page uses, replacing the standalone client-side
    language toggle the source page shipped with. One language
    mechanism for the whole site, not two that could drift out of sync.

    Content is the PR/marketing team's own copy, ported not rewritten,
    with two corrections made deliberately rather than silently: the
    connect instructions no longer assume the SSID "starts with
    LaBuche" (stale against this build's actual default, which is
    either the full hosted-page domain or a technician-chosen custom
    name), and the example IP is the AP's real, live address instead of
    a hardcoded placeholder from wherever the page was originally drafted.

    Gallery images are served from the SD card (see /about/img),
    not baked into the firmware -- a technician copies the two photos
    onto the card; nothing here assumes they're already present, and a
    missing photo just renders as a broken image, the same as any other
    web page missing an asset.
    """
    ip_html = "<code class='ip-tag'>http://" + _current_ap_ip() + "</code>"

    about_pane = (
        "<div class='badge-open'>" + i18n.t("about_badge", lang) + "</div>"
        "<div class='panel'>"
        "<p style='margin-bottom:0'>" + i18n.t("about_badge_text", lang) + "</p>"
        "</div>"

        "<h2>" + i18n.t("about_what_h2", lang) + "</h2>"
        "<div class='panel'>"
        "<p>" + i18n.t("about_what_p1", lang) + "</p>"
        "<p style='margin-bottom:0'>" + i18n.t("about_what_p2", lang) + "</p>"
        "</div>"

        "<h2>" + i18n.t("about_fireflies_h2", lang) + "</h2>"
        "<p>" + i18n.t("about_fireflies_intro", lang) + "</p>"
        "<div class='info-tiles'>"
        "<div class='info-tile'><span>" + i18n.t("about_tile1_title", lang) + "</span>"
        "<small>" + i18n.t("about_tile1_desc", lang) + "</small></div>"
        "<div class='info-tile'><span>" + i18n.t("about_tile2_title", lang) + "</span>"
        "<small>" + i18n.t("about_tile2_desc", lang) + "</small></div>"
        "<div class='info-tile'><span>" + i18n.t("about_tile3_title", lang) + "</span>"
        "<small>" + i18n.t("about_tile3_desc", lang) + "</small></div>"
        "</div>"
        "<div class='panel'>"
        "<p style='margin-bottom:0'>" + i18n.t("about_fireflies_closing", lang) + "</p>"
        "</div>"

        "<h2>" + i18n.t("about_visions_h2", lang) + "</h2>"
        "<div class='panel'><p style='margin-bottom:0'>" + i18n.t("about_visions_p", lang) + "</p></div>"

        "<h2>" + i18n.t("about_creator_h2", lang) + "</h2>"
        "<div class='panel'>"
        "<p>" + i18n.t("about_creator_p", lang) + "</p>"
        "<div class='links-grid'>"
        "<a class='link-tile' href='https://github.com/ruderigo/LaBuche-Stump' target='_blank' rel='noopener'>"
        "<div class='title'>GitHub</div><div class='desc'>" + i18n.t("about_link_github_desc", lang) + "</div></a>"
        "<a class='link-tile' href='https://www.linkedin.com/in/rodrigo-gl' target='_blank' rel='noopener'>"
        "<div class='title'>LinkedIn</div><div class='desc'>rodrigo-gl</div></a>"
        "<a class='link-tile' href='mailto:Rodrigoandresgl@gmail.com'>"
        "<div class='title'>" + i18n.t("about_link_email_title", lang) + "</div>"
        "<div class='desc'>Rodrigoandresgl@gmail.com</div></a>"
        "</div>"
        "</div>"
    )

    def step(icon_path, title_key, text_key, **fmt):
        return (
            "<div class='step-item'>"
            "<svg class='step-icon' viewBox='0 0 24 24'>" + icon_path + "</svg>"
            "<div class='step-body'>"
            "<div class='step-title'>" + i18n.t(title_key, lang) + "</div>"
            "<p class='step-text'>" + i18n.t(text_key, lang, **fmt) + "</p>"
            "</div></div>"
        )

    connect_pane = (
        "<p class='sub'>" + i18n.t("about_connect_tagline", lang) + "</p>"
        "<h2>" + i18n.t("about_connect_h2", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("about_connect_intro", lang) + "</p>"
        "<div class='steps-container'>"
        + step("<path d='M5 12.55a11 11 0 0 1 14.08 0'/><path d='M1.42 9a16 16 0 0 1 21.16 0'/>"
               "<path d='M8.53 16.11a6 6 0 0 1 6.95 0'/><line x1='12' y1='20' x2='12.01' y2='20'/>",
               "about_connect_step1_title", "about_connect_step1_text")
        + step("<rect x='3' y='11' width='18' height='11' rx='2' ry='2'/><path d='M7 11V7a5 5 0 0 1 9.9-1'/>",
               "about_connect_step2_title", "about_connect_step2_text")
        + step("<circle cx='12' cy='12' r='10'/><polygon points='12 8 8 12 12 16 12 8'/><line x1='16' y1='12' x2='8' y2='12'/>",
               "about_connect_step3_title", "about_connect_step3_text", ip=ip_html)
        + "</div>"
    )

    def gallery_card(fname, alt_key, title_key, sub_key):
        return (
            "<div class='gallery-card'>"
            "<img src='/about/img?f=" + _url_encode(fname) + "' alt='" + i18n.t(alt_key, lang) + "'>"
            "<div class='caption'><span>" + i18n.t(title_key, lang) + "</span>"
            "<span>" + i18n.t(sub_key, lang) + "</span></div>"
            "</div>"
        )

    hardware_pane = (
        "<p class='sub'>" + i18n.t("about_hardware_tagline", lang) + "</p>"
        "<h2>" + i18n.t("about_hardware_h2", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("about_hardware_intro", lang) + "</p>"
        "<div class='gallery-grid'>"
        + gallery_card("examplenode.jpg", "about_hardware_img1_alt", "about_hardware_img1_title", "about_hardware_img1_sub")
        + gallery_card("examplenode2.jpg", "about_hardware_img2_alt", "about_hardware_img2_title", "about_hardware_img2_sub")
        + "</div>"
    )

    tabs = (
        "<div class='tab-bar'>"
        "<button class='view-btn active' id='tab-about' onclick=\"switchAboutView('about')\">"
        + i18n.t("about_tab_about", lang) + "</button>"
        "<button class='view-btn' id='tab-connect' onclick=\"switchAboutView('connect')\">"
        + i18n.t("about_tab_connect", lang) + "</button>"
        "<button class='view-btn' id='tab-hardware' onclick=\"switchAboutView('hardware')\">"
        + i18n.t("about_tab_hardware", lang) + "</button>"
        "</div>"
    )

    script = (
        "<script>function switchAboutView(view){"
        "document.querySelectorAll('.view-pane').forEach(function(el){el.classList.remove('active');});"
        "document.getElementById('pane-'+view).classList.add('active');"
        "document.querySelectorAll('.view-btn').forEach(function(el){el.classList.remove('active');});"
        "document.getElementById('tab-'+view).classList.add('active');"
        "}</script>"
    )

    return (
        "<pre class='art'>" + BARKEEP_ART + "</pre>"
        "<h1 class='doctitle'>Projet Stump &amp; Fireflies</h1>"
        + i18n.switcher_html(lang, "/about") +
        "<p class='tagline'>" + i18n.t("about_tagline", lang) + "</p>"
        + tabs +
        "<div class='view-pane active' id='pane-about'>" + about_pane + "</div>"
        "<div class='view-pane' id='pane-connect'>" + connect_pane + "</div>"
        "<div class='view-pane' id='pane-hardware'>" + hardware_pane + "</div>"
        + _nav(lang, "home", "chat", "board", "files")
        + script
    )


def _render_billboard_page(lang):
    return billboard._render_page(lang) + _nav(lang, "home", "chat", "files", "about")


async def _send(writer, status, body, content_type="text/html"):
    """Headers and body are written separately, and str bodies encoded,
    rather than concatenated -- 'str' + bytes is a hard TypeError in
    MicroPython (verified against the real interpreter), so the old
    concatenating version failed outright on every binary response.
    fserv.py's own _send already did it this way; this had diverged.

    Body is encoded to bytes BEFORE Content-Length is computed, not
    after -- confirmed directly that these differ for any non-ASCII
    text: "Tirez-vous une bûche" is 20 Python characters but 21 UTF-8
    bytes (û alone is 2 bytes). Every actual caller of this function
    passes text/html, text/plain, or application/json -- nothing binary
    ever reaches it (file downloads go through a separate byte-accurate
    path reading a real file size from disk) -- so charset=utf-8 is
    always correct to declare, unconditionally.

    Both bugs were real and both were shipping: no charset declaration
    meant a browser guessing the wrong single-byte encoding for
    anything with accents (the exact "Ã©"-for-"é" pattern this project
    hit in the field), and the uncorrected character-count
    Content-Length meant every response containing an accented
    character was already lying about its own size before that.
    """
    if isinstance(body, str):
        body = body.encode()
    if "charset" not in content_type:
        content_type = content_type + "; charset=utf-8"
    # Connection: close matters here. The server closes after every
    # response, but HTTP/1.1 defaults to keep-alive -- so without this
    # the browser holds the socket open expecting to reuse it, then has
    # to discover it's dead and re-issue the request on a new one. That
    # shows up as pages that feel sluggish or hang on the first click.
    resp = ("HTTP/1.1 " + status + "\r\nContent-Type: " + content_type +
             "\r\nContent-Length: " + str(len(body)) +
             "\r\nConnection: close\r\n\r\n")
    await writer.awrite(resp)
    await writer.awrite(body)


MIME_TYPES = {
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav",
    "flac": "audio/flac", "ogg": "audio/ogg",
    "mp4": "video/mp4", "mkv": "video/x-matroska", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "webm": "video/webm",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "pdf": "application/pdf", "txt": "text/plain", "md": "text/plain",
    "zip": "application/zip", "epub": "application/epub+zip",
}


def _mime_for(filename):
    # A handful of real, well-known filenames genuinely have no
    # extension at all -- LICENSE specifically, the standard,
    # tool-recognized name GitHub/package managers/license scanners all
    # look for. Without this, it fell through to
    # application/octet-stream and downloaded as an anonymous binary
    # blob instead of the plain text it actually is.
    if filename.upper() in ("LICENSE", "LICENCE"):
        return "text/plain"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return MIME_TYPES.get(ext, "application/octet-stream")


def _disposition_name(filename):
    """Makes a filename safe to sit inside a quoted HTTP header value.

    A quote or backslash would terminate or escape the quoted string and
    corrupt the header; CR/LF would split it into forged extra headers.
    Anything problematic becomes '_' rather than being dropped, so the
    name still resembles the original."""
    out = []
    for ch in filename:
        out.append("_" if (ch in '"\\' or ord(ch) < 32 or ord(ch) == 127) else ch)
    return "".join(out)


async def _send_file(writer, fpath, size, filename, chunk=16384, inline=False):
    """Streams a file from disk in chunks instead of reading it into RAM.

    Content-Disposition is what actually gives the saved file its real
    name. Without it the browser names the download after the last URL
    path segment -- "download" -- with no extension, so every file
    arrived unreadable until the user renamed it by hand.

    inline=True omits the "attachment" disposition entirely rather than
    setting it to "inline" -- browsers already render an <img> tag's
    response body regardless of what Content-Disposition says as long
    as it isn't "attachment", so simply not sending it is enough, and
    it avoids asserting a disposition value this function has never
    tested against every browser's handling of it. Downloads (the
    default) keep forcing Save-As, unchanged from before -- this exists
    for the about-page gallery, which needs the opposite behaviour: a
    photo that renders on the page, not one that pops a save dialog.

    16KB chunks, not 2KB: a 10MB transfer at 2KB is over 5000
    read/write/await cycles, and every await hands control to the DNS
    poller and the bridge loop before coming back. The per-cycle
    overhead, not the bytes, dominated transfer time. 16KB is still tiny
    against available PSRAM and nowhere near large enough to fragment
    the heap, but cuts the cycle count eightfold.
    """
    disposition = "" if inline else (
        "Content-Disposition: attachment; filename=\"" + _disposition_name(filename) + "\"\r\n"
    )
    await writer.awrite(
        "HTTP/1.1 200 OK\r\nContent-Type: " + _mime_for(filename) + "\r\n"
        + disposition +
        "Content-Length: " + str(size) + "\r\nConnection: close\r\n\r\n"
    )
    with open(fpath, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            await writer.awrite(buf)


def _query(path, key):
    """Pulls one value out of a query string. MicroPython has no
    urllib.parse, and the existing ad-hoc split('f=') pattern elsewhere
    in this file only works when there is exactly one parameter -- RRC
    polls with two (room and since), so it needs to actually parse."""
    if "?" not in path:
        return None
    qs = path.split("?", 1)[1]
    for pair in qs.split("&"):
        k, _, v = pair.partition("=")
        if k == key:
            return billboard._url_decode(v)
    return None


MAX_HEADERS = 40
# Chat commands and billboard entries are tiny (a line of text, and
# billboard caps entries at 200 chars anyway). Anything claiming to be
# bigger is a broken or hostile client, not a real post.
MAX_SMALL_BODY = 8192


async def _read_small_body(reader, length, chunk=1024):
    """Reads a small request body, bounded and EOF-safe.

    Not readexactly(): that blocks indefinitely if a client sends a
    Content-Length larger than what it actually delivers, leaking the
    connection until something else times it out. This returns whatever
    genuinely arrives and stops at EOF, and refuses to buffer more than
    MAX_SMALL_BODY regardless of what the header claims -- the same
    shape as fserv.drain/stream_to_file, which already handle a lying
    length correctly."""
    if length <= 0:
        return b""
    if length > MAX_SMALL_BODY:
        length = MAX_SMALL_BODY
    out = bytearray()
    while len(out) < length:
        want = length - len(out)
        if want > chunk:
            want = chunk
        buf = await reader.read(want)
        if not buf:
            break
        out.extend(buf)
    return bytes(out)


async def _handle(reader, writer):
    try:
        request_line = await reader.readline()
        # Parse defensively: a phone's captive-portal probe, a port
        # scanner, or a half-open connection can all produce a request
        # line that doesn't split into three parts. The old unpack threw
        # and the socket closed with NOTHING sent back -- confirmed by
        # probing with a bare newline and with "GET /" (no version).
        # A silent close is the wrong answer here specifically because
        # captive-portal detection depends on the phone getting a real
        # HTTP response to its probe.
        parts = request_line.decode().split()
        if len(parts) < 2:
            await _send(writer, "400 Bad Request", "Malformed request.", "text/plain")
            return
        method, path = parts[0], parts[1]

        headers = {}
        header_count = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b""):
                break
            header_count += 1
            if header_count > MAX_HEADERS:
                # Bounded so a broken or hostile client can't grow this
                # dict indefinitely on a memory-constrained board.
                await _send(writer, "400 Bad Request", "Too many headers.", "text/plain")
                return
            k, _, v = line.decode().partition(":")
            headers[k.strip().lower()] = v.strip()

        # Content-Length has to survive garbage too -- "abc" used to
        # throw here and close the socket silently, so an upload with a
        # broken header got no error message at all.
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            await _send(writer, "400 Bad Request", "Bad Content-Length.", "text/plain")
            return
        if content_length < 0:
            content_length = 0
        headers["content-length"] = str(content_length)

        peer = writer.get_extra_info("peername")
        identifier = billboard._extract_ip(peer)

        if method == "POST" and path.startswith("/chat"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            reply = _process_command(body.decode(), identifier, i18n.get_lang(identifier))
            await _send(writer, "200 OK", reply, "text/plain")

        elif method == "POST" and path.startswith("/post"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            entry = ""
            for kv in body.decode().split("&"):
                if kv.startswith("entry="):
                    entry = billboard._url_decode(kv[6:])
            billboard._append_entry(entry, identifier)
            await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /billboard\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "GET" and path.startswith("/admin"):
            # No link anywhere points here on purpose -- see
            # _render_admin_login_page's own docstring.
            await _send(writer, "200 OK", _page(_render_admin_login_page(i18n.get_lang(identifier))))

        elif method == "POST" and path.startswith("/admin") and not path.startswith("/admin/delete"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            pw = ""
            for kv in body.decode().split("&"):
                if kv.startswith("admin_pass="):
                    pw = billboard._url_decode(kv[11:])
            lang = i18n.get_lang(identifier)
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", _page(_render_admin_login_page(lang, error=True)))
            else:
                names = fserv._list_files() if fserv.sd_ok else []
                await _send(writer, "200 OK", _page(_render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/delete_post"):
            # Must come BEFORE the /admin/delete branch below --
            # "/admin/delete_post".startswith("/admin/delete") is True,
            # so without this ordering the broader check would catch
            # these requests first and try to delete files named after
            # timestamps that don't exist, silently doing nothing.
            # Confirmed directly before writing this rather than
            # assumed: a startswith-based router means more specific
            # paths always have to come first.
            #
            # Same re-validation discipline as file deletion: the
            # password arrived as a hidden field, convenient but not
            # trustworthy on its own, so it's checked here exactly as
            # strictly as everywhere else that accepts one. Timestamps
            # arrive the same repeated-field way filenames do for a
            # batch file delete -- one ts=... occurrence per checked
            # post, collected into a list rather than only keeping the
            # last match.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            pw = ""
            timestamps = []
            for kv in body.decode().split("&"):
                if kv.startswith("ts="):
                    timestamps.append(billboard._url_decode(kv[3:]))
                elif kv.startswith("admin_pass="):
                    pw = billboard._url_decode(kv[11:])
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                deleted_count = 0
                for ts_str in timestamps:
                    if ts_str and billboard.delete_entry(ts_str):
                        deleted_count += 1
                # Same reasoning as the file-delete route: re-render
                # the updated admin page directly rather than redirect
                # to the bare /admin login, so a successful delete is
                # immediately visible instead of looking like nothing
                # happened.
                lang = i18n.get_lang(identifier)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + i18n.t("admin_post_deleted_notice", lang, n=deleted_count) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/delete"):
            # Re-validates the password for real -- it arrived as a
            # hidden field on the page above, which is convenient, not
            # trustworthy: anyone could POST here directly with a
            # forged or empty one, so this checks it exactly as
            # strictly as the login step did, not just checks it was
            # present. Every checked box arrives as a repeated f=...
            # field with the SAME name, one occurrence per file --
            # collected into a list here rather than the earlier
            # single-file parsing pattern that only ever kept the last
            # match, since a batch is the whole point of this route.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            pw = ""
            fnames = []
            for kv in body.decode().split("&"):
                if kv.startswith("f="):
                    fnames.append(_clean_filename(billboard._url_decode(kv[2:])))
                elif kv.startswith("admin_pass="):
                    pw = billboard._url_decode(kv[11:])
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                deleted_count = 0
                for fname in fnames:
                    if fname and fserv.delete_file(fname):
                        deleted_count += 1
                # Re-renders the file list directly instead of
                # redirecting to /admin -- a 303 there lands on the
                # bare login form (that's what GET /admin always shows),
                # giving zero visible confirmation that anything
                # happened. A real deletion succeeding and then bouncing
                # back to an empty password prompt reads exactly like
                # "the delete doesn't work", confirmed directly against
                # a real test report. The password is already validated
                # at this point in the handler, so there's no reason to
                # make the admin type it a second time just to see the
                # file is actually gone.
                lang = i18n.get_lang(identifier)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + i18n.t("admin_deleted_notice", lang, n=deleted_count) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "GET" and path.startswith("/rrc/poll"):
            # Poll for new messages. The client sends the highest id it
            # already has, so only genuinely new lines come back rather
            # than the whole room every two seconds.
            q_room = _query(path, "room") or rrc.DEFAULT_ROOM
            try:
                since_id = int(_query(path, "since") or "0")
            except ValueError:
                since_id = 0
            user = rrc.touch_user(identifier)
            # The server's idea of which room this client is in wins --
            # /join from another tab, or a room that has since vanished,
            # both need to be reflected back rather than silently
            # leaving the client polling a room it isn't in.
            actual = user["room"]
            if not rrc.room_exists(actual):
                actual = rrc.DEFAULT_ROOM
                rrc.touch_user(identifier, room=actual)
            if actual != q_room:
                since_id = 0
            payload = {
                "room": actual,
                "nick": user["nick"],
                "topic": rrc.topic(actual),
                "rooms": rrc.room_names(),
                "messages": rrc.since(actual, since_id),
                # Private messages ride the same poll rather than a
                # second endpoint: one request per cycle instead of two
                # on a board where each connection costs real work, and
                # they share the message-id sequence so the client's
                # existing since/lastId bookkeeping covers both.
                "dms": rrc.dms_since(identifier, since_id),
                # Who's actually in this room right now -- lets the
                # client offer "select someone to DM" instead of
                # requiring the exact nick typed blind into /msg. Sent
                # every poll (not just when it changes): cheap, already
                # pruned server-side by users_in_room's own
                # _prune_users call, and simpler than a second
                # changed-since check for a list this short (MAX_USERS
                # is 40).
                "users": rrc.users_in_room(actual),
            }
            await _send(writer, "200 OK", json.dumps(payload), "application/json")

        elif method == "POST" and path.startswith("/rrc/send"):
            length = int(headers.get("content-length", "0"))
            # The read below is already bounded, so an oversized paste
            # can't exhaust memory -- but silently truncating it means
            # the sender watches a message go out that nobody receives
            # in full. Refuse it and say so instead.
            if length > MAX_SMALL_BODY:
                await _send(writer, "413 Payload Too Large",
                             json.dumps({"replies": [
                                 "message too long (%d bytes, limit %d) -- nothing was sent"
                                 % (length, MAX_SMALL_BODY)], "room": None}),
                             "application/json")
                return
            body = await _read_small_body(reader, length)
            user = rrc.touch_user(identifier)
            # The SERVER decides which room this client is in, not the
            # client. X-Room is deliberately ignored: the client only
            # ever learns its room from /rrc/poll in the first place, so
            # a header that disagrees is stale (a second tab left on an
            # old room) or forged. Honouring it used to move the user --
            # silently pulling them out of the room they were actually
            # in, and letting anyone post into a room they never joined.
            room = user["room"] if rrc.room_exists(user["room"]) else rrc.DEFAULT_ROOM
            replies, new_room = rrc.handle_input(identifier, room, body.decode())
            await _send(writer, "200 OK",
                         json.dumps({"replies": replies, "room": new_room}),
                         "application/json")

        elif method == "GET" and path.startswith("/rrc"):
            user = rrc.touch_user(identifier)
            await _send(writer, "200 OK", rrc_ui.render_page(user["room"], user["nick"], i18n.get_lang(identifier)))

        elif method == "GET" and path.startswith("/flash"):
            # Pass the address the client actually used, so the Chrome
            # flag printed on the page is copy-pasteable rather than a
            # placeholder someone has to work out.
            host = headers.get("host", "").split(":")[0] or "this-node"
            await _send(writer, "200 OK", flasher_ui.render_page(host))

        elif method == "GET" and path.startswith("/fw"):
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            fname = _clean_filename(billboard._url_decode(raw_f))
            fpath = fserv.FW_DIR + "/" + fname
            try:
                size = os.stat(fpath)[6]
                # Firmware images are megabytes; _send_file streams them
                # in chunks so a 2MB image doesn't have to exist in RAM.
                await _send_file(writer, fpath, size, fname)
            except OSError:
                await _send(writer, "404 Not Found", "no such image", "text/plain")

        elif method == "GET" and path.startswith("/lang"):
            # Sets the requesting client's language preference, then
            # redirects back to wherever they were -- no cookies, no
            # JS, just a link and a 303, matching the existing pattern
            # already used for billboard posts. next= is decoded and
            # falls back to home if missing or empty, so a stripped or
            # malformed query string can't leave someone stranded.
            new_lang = _query(path, "set") or i18n.DEFAULT_LANG
            next_path = billboard._url_decode(_query(path, "next") or "/") or "/"
            i18n.set_lang(identifier, new_lang)
            await writer.awrite(
                "HTTP/1.1 303 See Other\r\nLocation: " + next_path +
                "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "GET" and path.startswith("/about/img"):
            # Checked before the general /about route below -- /about/img
            # would otherwise be swallowed by a startswith("/about")
            # match, exactly the /tools-before/tool ordering already
            # established elsewhere in this file: the more specific
            # path always has to be checked first.
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            iname = _clean_filename(billboard._url_decode(raw_f))
            ipath = fserv.ABOUT_DIR + "/" + iname
            try:
                size = os.stat(ipath)[6]
                await _send_file(writer, ipath, size, iname, inline=True)
            except OSError:
                await _send(writer, "404 Not Found", "not found", "text/plain")

        elif method == "GET" and path.startswith("/about"):
            await _send(writer, "200 OK", _page(_render_about_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/tools"):
            await _send(writer, "200 OK", _page(_render_tools_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/tool"):
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            tname = _clean_filename(billboard._url_decode(raw_f))
            tpath = fserv.TOOLS_DIR + "/" + tname
            try:
                # Deliberately NOT the /download route: tools carry no
                # credit cost and must not be charged for, and keeping
                # the paths apart means a user file can never be served
                # as a tool or the reverse.
                size = os.stat(tpath)[6]
                await _send_file(writer, tpath, size, tname)
            except OSError:
                await _send(writer, "404 Not Found", "no such tool", "text/plain")

        elif method == "GET" and path.startswith("/files"):
            await _send(writer, "200 OK", _page(_render_files_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/billboard"):
            await _send(writer, "200 OK", _page(_render_billboard_page(i18n.get_lang(identifier))))

        elif method == "POST" and path.startswith("/upload"):
            fname = _clean_filename(headers.get("x-filename", "upload.bin"))
            hash_hex = headers.get("x-hash", "").strip()
            length = int(headers.get("content-length", "0"))

            # Validate and pick the destination BEFORE reading a single
            # byte of the body. The old order read the whole upload into
            # memory first and only then checked whether it could be
            # accepted -- so a rejected upload still had to fit in RAM,
            # which is the exact failure this restructure removes.
            # Rejections drain instead of buffering.
            if not fname:
                await fserv.drain(reader, length)
                await _send(writer, "400 Bad Request", "No filename given.", "text/plain")
            elif not fserv.sd_ok:
                await fserv.drain(reader, length)
                await _send(writer, "503 Service Unavailable", "No card in the slot right now.", "text/plain")
            elif length <= 0:
                await _send(writer, "400 Bad Request", "Empty upload.", "text/plain")
            elif not fserv.make_room_fifo(length):
                # Checked (and, if needed, evicted the oldest shared
                # files to make room) BEFORE reading a single byte of
                # the body -- same reasoning as the checks above it:
                # rejecting after the fact would still mean the upload
                # had to arrive first. TOOLS_DIR/FW_DIR/ABOUT_DIR are
                # never eligible for eviction (see make_room_fifo's own
                # docstring), so this can still legitimately fail on a
                # card that's genuinely full even after clearing every
                # shared file -- that's a real "no room" answer, not a
                # bug.
                await fserv.drain(reader, length)
                await _send(writer, "507 Insufficient Storage",
                            "Card capacity exceeded (75% threshold limit).", "text/plain")
            else:
                table = fserv._awaiting()
                is_slot = bool(hash_hex) and hash_hex in table and not table[hash_hex]["fulfilled"]
                if is_slot:
                    dest = fserv.SD_MOUNT + "/slot_" + hash_hex + "_" + fname
                else:
                    dest = fserv.SHARED_DIR + "/" + fname

                ok, written = await fserv.stream_to_file(reader, dest, length)

                if not ok:
                    await _send(writer, "500 Internal Server Error",
                                 "Upload interrupted (" + str(written) + " of " +
                                 str(length) + " bytes) — nothing was kept. Try again.",
                                 "text/plain")
                else:
                    # The file itself is already safely on disk at this
                    # point -- stream_to_file succeeded. Everything from
                    # here on is bookkeeping (marking a slot fulfilled,
                    # updating the credit ledger), and _save_json's own
                    # fix already stops a write failure there from
                    # raising -- but this try/except is a second,
                    # independent layer specifically so that ANY future
                    # exception in this bookkeeping, not just the one
                    # already fixed, still can't turn a real, completed
                    # upload into a response the client never receives.
                    # Confirmed as a real, reported bug without this:
                    # barkeep.py's own outer exception handler logs an
                    # uncaught error server-side but was never built to
                    # send a response for one this deep, so the file
                    # could be genuinely, successfully written while the
                    # browser's fetch() just hangs -- no success, no
                    # error, nothing, indistinguishable from the upload
                    # having failed outright.
                    try:
                        if is_slot:
                            # Only mark the slot fulfilled once the bytes
                            # are actually on disk -- marking it earlier
                            # would burn a one-shot slot on an upload
                            # that never completed.
                            table[hash_hex]["fulfilled"] = True
                            fserv._save_json(fserv.AWAITING_FILE, table)
                            msg = "Delivered to your awaiting slot."
                        else:
                            credited = fserv.credit_add(identifier, fserv.credit_cost(fname))
                            if fserv.CREDITS_ENABLED:
                                msg = "Uploaded. Your balance: " + str(credited)
                            else:
                                msg = "Uploaded. Thanks for bringing something."
                    except Exception as e:
                        print("[barkeep] upload bookkeeping failed (file already saved):", e)
                        msg = "Uploaded, but couldn't update your balance/slot record."
                    await _send(writer, "200 OK", msg, "text/plain")

        elif method == "GET" and path.startswith("/download"):
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            fname = _clean_filename(billboard._url_decode(raw_f))
            fpath = fserv.SHARED_DIR + "/" + fname
            try:
                # stat first: confirms the file exists and gives the size
                # needed for Content-Length, without reading it into RAM.
                # Charging only after this means a missing/unreadable file
                # can't debit someone for something they never received.
                size = os.stat(fpath)[6]
                fserv.credit_add(identifier, -fserv.credit_cost(fname))
                await _send_file(writer, fpath, size, fname)
            except OSError:
                await _send(writer, "404 Not Found", "not found", "text/plain")

        else:
            await _send(writer, "200 OK", _page(_render_chat_page(i18n.get_lang(identifier))))

    except Exception as e:
        print("[barkeep] request error:", e)
    finally:
        await writer.aclose()


async def run_barkeep_server(port=80):
    """Starts the one and only HTTP server.

    Reports its own bind result. If port 80 can't be bound the node
    would otherwise come up looking entirely healthy -- WiFi joined,
    mesh announced, AP broadcasting -- while serving nothing at all, and
    nothing in the boot log would say why."""
    try:
        await asyncio.start_server(_handle, "0.0.0.0", port)
    except Exception as e:
        print("[web] FAILED to bind port %d: %s" % (port, e))
        print("[web] the billboard, chat and file pages are NOT available.")
        raise
    print("[web] serving on port", port)
