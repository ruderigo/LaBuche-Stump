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
import theme
import features
import radio
import propagation
import stump_tls as tls  # not "tls": MicroPython has a built-in module by that name
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

STYLE = theme.CSS + """
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
input,textarea{background:var(--panel); color:var(--text);}
textarea{font-family:inherit; font-size:1rem; padding:10px 12px;
  border-radius:6px; border:1px solid var(--border); resize:vertical;}
.post-form{display:flex; flex-direction:column; gap:8px; margin:10px 0;}
.post-form button{align-self:flex-start;}
details.post summary{cursor:pointer; padding:2px 0;}
details.post[open] summary{color:var(--ember-bright);}
.post-body{display:block; white-space:pre-wrap; color:var(--muted);
  margin-top:6px; padding-left:14px;}
input:focus,textarea:focus,button:focus,summary:focus{outline:2px solid var(--ember); outline-offset:1px;}
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
/* Home page branding from the card (home/home.html). */
.home-brand{margin:0 0 18px;}
.home-brand img, .home-brand video, .home-brand svg{max-width:100%; height:auto;}
.home-frame{display:block; width:100%; border:0; background:transparent;}
fieldset.opts{border:1px solid var(--border); border-radius:6px; padding:8px 12px; margin:0;
  display:flex; flex-direction:column; gap:6px;}
fieldset.opts legend{color:var(--muted); padding:0 4px;}
label.opt{display:flex; align-items:center; gap:8px; cursor:pointer;}
.home-layout.side .home-brand{margin:0;}
@media (min-width:760px){
  .home-layout.side{display:grid; grid-template-columns:1fr 1fr; gap:24px; align-items:start;}
}
@media (max-width:759px){ .home-layout.side > * + *{margin-top:18px;} }
/* Home page: one card per row, the icon beside its name and subtitle. */
.tiles.home{grid-template-columns:1fr;}
.tiles.home .tile{flex-direction:row; align-items:center; justify-content:flex-start; gap:16px; padding:14px 18px;}
.tiles.home .tile svg{flex-shrink:0;}
.tiles.home .tile-text{display:flex; flex-direction:column; gap:2px;}
.tiles.home .tile small{text-align:left;}
/* Navigation rows: icons only, so every destination fits on one row,
   even five on a narrow phone. */
.tiles.nav{grid-template-columns:repeat(auto-fit, minmax(48px, 1fr));}
.tiles.nav .tile{padding:12px 6px; justify-content:center;}
@media (max-width:380px){ .tiles:not(.nav){grid-template-columns:1fr;} }

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
  background:var(--panel-2); border:1px solid var(--border); border-radius:6px;
  padding:12px 14px; transition:border-color .2s, background .2s;
}
.link-tile:hover{border-color:var(--ember); background:var(--line);}
.link-tile .title{
  font-family:ui-monospace,monospace; font-size:.9rem; font-weight:bold;
  color:var(--ember); margin-bottom:2px;
}
.link-tile .desc{
  font-size:.8rem; color:var(--muted); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;
}
.device-card{
  background:var(--panel); border:1px solid var(--border); border-radius:8px;
  padding:18px; margin-bottom:16px; display:flex; flex-direction:column; gap:12px;
}
.device-header{display:flex; align-items:center; gap:12px;}
.device-icon{
  width:28px; height:28px; min-width:28px; stroke:var(--ember); fill:none;
  stroke-width:2; stroke-linecap:round; stroke-linejoin:round;
}
.device-title{font-family:ui-monospace,monospace; font-weight:bold; font-size:1.05rem; color:var(--ember-bright);}
.device-desc{font-size:.95rem; color:var(--text); margin:0; line-height:1.5;}
.device-note{font-size:.8rem; color:var(--muted); margin:0; line-height:1.4;}
.device-btn{
  align-self:flex-start; display:inline-flex; align-items:center; gap:8px;
  background:var(--panel-2); border:1px solid var(--border); color:var(--ember);
  text-decoration:none; font-family:ui-monospace,monospace; font-size:.85rem;
  font-weight:bold; padding:6px 12px; border-radius:6px; transition:border-color .2s, background .2s;
}
.device-btn:hover{border-color:var(--ember); background:var(--line); color:var(--ember-bright);}
.device-btn small{font-weight:normal; color:var(--muted);}
.device-btn.secondary{font-weight:normal; color:var(--muted);}

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
    # One card per row, icon on the left and the name and subtitle beside
    # it: five cards squeezed into a grid were too compact to read.
    return (
        "<div class='tiles home'>"
        + "".join(
            "<a class='tile' href='" + href + "'>" + icon +
            "<div class='tile-text'><span>" + i18n.t(label, lang) + "</span>"
            "<small>" + i18n.t(sub, lang) + "</small></div></a>"
            for feat, href, icon, label, sub in (
                ("chat", "/rrc", _ICON_CHAT, "nav_chat", "tile_chat_sub"),
                ("billboard", "/billboard", _ICON_BOARD, "nav_board", "tile_board_sub"),
                ("files", "/files", _ICON_FILES, "nav_files", "tile_files_sub"),
                (None, "/tools", _ICON_TOOLS, "nav_tools", "tile_tools_sub"),
                ("about", "/about", _ICON_ABOUT, "nav_about", "tile_about_sub"),
            )
            if feat is None or features.enabled(feat)
        ) +
        "</div>"
    )


def _page(body):
    return ("<!DOCTYPE html>" + theme.html_open() + "<head><meta name='viewport' "
             "content='width=device-width, initial-scale=1'>"
             "<style>" + STYLE + "</style>"
             "<script>" + theme.STARTUP_SCRIPT + "</script>"
             "</head><body>" + body + "</body></html>")


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
            # Each UTF-8 byte, not ord(ch): _url_decode now decodes
            # UTF-8 (as browsers send it), and the two must stay exact
            # inverses or links to accented filenames break. ord() also
            # produced "%2019" for characters past U+00FF, which no
            # decoder reads back correctly.
            for b in ch.encode("utf-8"):
                out += "%%%02X" % b
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
    """The home page: logo, title, language switcher, and a tile for
    each feature this node offers (see features.py). The Concierge chat
    box that used to sit here is a hidden feature now -- HF-003,
    _render_concierge_page() below."""
    # Branding from the card (fserv.home_branding): replaces the logo and
    # title, placed above, below, or beside the cards. The language
    # switcher always stays first.
    brand = fserv.home_branding(lang)
    if brand is None:
        return (
            "<div id='site-logo'><pre>" + BARKEEP_ART + "</pre></div>"
            "<h1>Stump</h1>"
            + _lang_switcher(lang, "/")
            + _home_tiles(lang)
        )
    place = brand["place"]
    if brand["kind"] == "page":
        # A complete page (its own <html>, styles, scripts) goes in a frame:
        # inline, its body styles would take over the whole Stump page.
        # Sandboxed: its scripts run, but in an origin of their own, so they
        # can't read the node's pages as the visitor.
        section = ("<section class='home-brand'><iframe class='home-frame' sandbox='allow-scripts' "
                   "src='/home-file?f=" + _url_encode(brand["name"]) + "' title='" + _attr_esc(brand["name"]) + "' "
                   "style='height:" + str(brand["height"]) + "px'></iframe></section>")
    else:
        section = "<section class='home-brand'>" + brand["html"] + "</section>"
    tiles = _home_tiles(lang)
    if place == "bottom":
        body = tiles + section
    elif place in ("left", "right"):
        # Side by side where the screen allows (760 px and up), the page
        # widened for it; on a phone they stack, "left" above the cards
        # and "right" below -- the order they're written in.
        body = ("<style>@media (min-width:760px){body{max-width:960px;}}</style>"
                "<div class='home-layout side'>"
                + (section + tiles if place == "left" else tiles + section) + "</div>")
    else:
        body = section + tiles
    return _lang_switcher(lang, "/") + body


def _render_concierge_page(lang):
    """HF-003 -- the Concierge (BarKeep) chat box, unlinked. Moved off
    the home page by request; still fully working at /concierge, talking
    to the same POST /chat endpoint. See docs/HIDDEN_FEATURES.md."""
    you_label = billboard._esc(i18n.t("home_you_label", lang))
    return (
        "<h1>" + _esc_name(BOT_NAME) + "</h1>"
        + _lang_switcher(lang, "/concierge") +
        "<p class='sub'>" + i18n.t("barkeep_greeting", lang, bot_name=_esc_name(BOT_NAME)) + "</p>"
        "<div class='panel' id='log'>"
        "<p><b>" + _esc_name(BOT_NAME) + ":</b> " + i18n.t("barkeep_evening", lang) + "</p>"
        "</div>"
        "<div class='row'>"
        "<input id='in' placeholder='" + i18n.t("home_say_something", lang) + "' autofocus>"
        "<button onclick='sendMsg()'>" + i18n.t("rrc_send", lang) + "</button>"
        "</div>"
        + _nav(lang, "home") +
        "<script>"
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


# Every page's navigation row is this list minus the page itself -- one
# rule, one order, so no page can drift (four of them had: home, the
# billboard, tools and about were each missing a destination).
NAV_ORDER = ("chat", "board", "files", "tools", "about", "home")


def _nav_except(lang, current):
    return _nav(lang, *[k for k in NAV_ORDER if k != current])


# Nav keys that belong to a switchable feature (see features.py).
_NAV_FEATURE = {"chat": "chat", "board": "billboard", "files": "files", "about": "about"}


def _attr_esc(t):
    """Text for a single-quoted HTML attribute."""
    return (t.replace("&", "&amp;").replace("'", "&#39;").replace('"', "&quot;")
             .replace("<", "&lt;").replace(">", "&gt;"))


def _nav(lang, *keys):
    """Builds a tile row in the requesting client's language. The grid
    is auto-fit, so whatever number of tiles a page asks for spreads
    evenly across the row instead of leaving an orphan on a second
    line.

    Was a module-level constant, built once at import time with
    hardcoded English labels -- that stopped being possible the moment
    labels depend on who's asking, so this is now called fresh inside
    each page renderer instead."""
    # Icons only: with a word under each, five tiles didn't fit a phone's
    # width and the last one dropped to a row of its own. The name stays
    # as a tooltip and for screen readers (title, aria-label).
    out = ["<div class='tiles nav'>"]
    for k in keys:
        feat = _NAV_FEATURE.get(k)
        if feat is not None and not features.enabled(feat):
            continue
        href, icon, label_key = _DESTINATIONS[k]
        name = _attr_esc(i18n.t(label_key, lang))
        out.append("<a class='tile' href='" + href + "' title='" + name + "' aria-label='" + name + "'>" + icon + "</a>")
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
            "<form method='POST' action='/admin/delete' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<div class='panel'><ul class='files'>" + items + "</ul></div>"
            "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
            "</form>"
        )

    # Sections for features this node doesn't offer are left out (their
    # delete routes answer 404 anyway); theme and logo settings always show.
    files_part = ("<p class='sub'>" + i18n.t("admin_select_intro", lang) + "</p>" + file_section
                  if features.enabled("files") else "")
    board_part = _render_admin_billboard_section(lang, pw) if features.enabled("billboard") else ""
    return (
        "<h1>" + i18n.t("admin_header", lang) + "</h1>"
        + files_part
        + board_part
        + _render_admin_tls_line(lang)
        + _render_admin_home_section(lang, pw)
        + _render_admin_radio_section(lang, pw)
        + _render_admin_pn_section(lang, pw)
        + _render_admin_settings_section(lang)
    )


def _render_admin_tls_line(lang):
    """HTTPS status: on/off and why, and how long the certificate has
    left -- every node falls back to plain HTTP the day it expires."""
    import time
    st = tls.status()
    head = "<h2 class='sub'>HTTPS</h2>"
    if not st["active"]:
        return head + "<p class='sub'>" + i18n.t("admin_tls_off", lang, reason=billboard._esc(st["reason"])) + "</p>"
    try:
        import propagation
        now = propagation.unix_now()
    except Exception:
        import time as _time
        now = int(_time.time())
    # The node's clock may be unset (off-grid, no NTP): then "days left"
    # would be nonsense, so show the expiry date and skip the count.
    clock_ok = now >= 1735689600
    days = tls.days_left(now) if clock_ok else None
    try:
        import propagation as _pn
        y, mo, d = time.gmtime(int(st["not_after"]) - _pn.EPOCH_OFFSET)[:3]
        until = "%04d-%02d-%02d" % (y, mo, d)
    except Exception:
        until = "?"
    line = i18n.t("admin_tls_on", lang, host=billboard._esc(st["host"] or "?"), until=until,
                  days=days if days is not None else "?")
    if days is not None and days < 21:
        line = "<b>" + line + " — " + i18n.t("admin_tls_renew", lang) + "</b>"
    return head + "<p class='sub'>" + line + "</p>"


def _render_admin_home_section(lang, pw):
    """The home page's own section (docs/HOME_BRANDING.md): what the node
    sees in the card's home/ folder, and where to put it -- above, under,
    or beside the buttons -- its frame height, and whether to show it.
    Saved to home/home.json, so the panel and the file always agree."""
    cfg = fserv.home_config()
    out = ("<h2 class='sub'>" + i18n.t("admin_home_header", lang) + "</h2>"
           "<p class='sub'>" + billboard._esc(fserv.home_status(lang)) + "</p>")
    if not fserv.sd_ok:
        return out
    radios = "".join(
        "<label class='opt'><input type='radio' name='place' value='" + v + "'"
        + (" checked" if cfg["place"] == v else "") + "> " + i18n.t(k, lang) + "</label>"
        for v, k in (("top", "admin_home_top"), ("bottom", "admin_home_bottom"),
                     ("left", "admin_home_left"), ("right", "admin_home_right")))
    return out + (
        "<form method='POST' action='/admin/home' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<fieldset class='opts'><legend>" + i18n.t("admin_home_place", lang) + "</legend>" + radios + "</fieldset>"
        "<label>" + i18n.t("admin_home_height", lang) + "<br>"
        "<input name='height' type='number' min='80' max='1200' value='" + str(cfg["height"]) + "'></label>"
        "<small>" + i18n.t("admin_home_height_note", lang) + "</small>"
        "<label class='opt'><input type='checkbox' name='show' value='1'" + ("" if cfg["hidden"] else " checked") + "> "
        + i18n.t("admin_home_show", lang) + "</label>"
        "<button type='submit'>" + i18n.t("admin_home_save", lang) + "</button></form>"
    )


def _render_admin_pn_section(lang, pw):
    """LXMF propagation node (propagation.py): switch, status, and the two
    single-phone test tools."""
    st = propagation.status()
    hid = "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
    head = ("<h2 class='sub'>" + i18n.t("admin_pn_header", lang) + "</h2>"
            "<p class='sub'>" + i18n.t("admin_pn_intro", lang) + "</p>")
    toggle = ("<form method='POST' action='/admin/propagation' autocomplete='off'>" + hid +
              "<input type='hidden' name='action' value='" + ("off" if st["enabled"] else "on") + "'>"
              "<button type='submit'>" + i18n.t("admin_pn_disable" if st["enabled"] else "admin_pn_enable", lang)
              + "</button></form>")
    if not st["enabled"]:
        return head + "<p class='sub'>" + i18n.t("admin_pn_off_state", lang) + "</p>" + toggle
    s = st["stats"]
    body = ("<p class='sub'>" + i18n.t("admin_pn_on_state", lang) + " <code>" + (st["address"] or "") + "</code><br>"
            + i18n.t("admin_pn_counts", lang, stored=st["stored"], checking=st["checking"],
                     valid=s["valid"], invalid=s["invalid"], served=s["served"]) + "</p>")
    inbox = propagation.mailbox_inbox()
    mailbox = ("<p class='sub'>" + i18n.t("admin_pn_mailbox", lang) + " <code>" + (st["mailbox"] or "") + "</code></p>"
               + ("<ul>" + "".join("<li>" + billboard._esc(m["text"]) + " <small>&mdash; " + m["from"][:8]
                                   + "…</small></li>" for m in reversed(inbox)) + "</ul>"
                  if inbox else "<p class='sub'><small>" + i18n.t("admin_pn_mailbox_none", lang) + "</small></p>"))
    leave = ("<form method='POST' action='/admin/propagation' class='post-form' autocomplete='off'>" + hid +
             "<input type='hidden' name='action' value='leave'>"
             "<label>" + i18n.t("admin_pn_leave_to", lang) + "<br><input name='to' maxlength='32' "
             "placeholder='53d91d2f…'></label>"
             "<label>" + i18n.t("admin_pn_leave_text", lang) + "<br><input name='text' maxlength='200'></label>"
             "<button type='submit'>" + i18n.t("admin_pn_leave_button", lang) + "</button></form>")
    return head + body + toggle + mailbox + leave


def _render_admin_radio_section(lang, pw):
    """The Heltec Bridge's radio settings (radio.py). Shows what the
    bridge is using right now; applying sends them to the Heltec at once
    if it's connected, and saves them so they survive a reboot."""
    cur = radio.current()
    head = "<h2 class='sub'>" + i18n.t("admin_radio_header", lang) + "</h2>"
    if cur is None:
        return head + "<p class='sub'>" + i18n.t("admin_radio_none", lang) + "</p>"

    def opts(values, selected, label=str):
        return "".join("<option value='%d'%s>%s</option>" % (v, " selected" if v == selected else "", label(v))
                       for v in values)

    def field(label_key, control):
        return "<label>" + i18n.t(label_key, lang) + "<br>" + control + "</label>"

    return (
        head +
        "<p class='sub'>" + i18n.t("admin_radio_intro", lang) + "</p>"
        "<form method='POST' action='/admin/radio' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        + field("radio_txp", "<input name='txp' type='number' min='%d' max='%d' value='%d'>"
                % (radio.TXP_MIN, radio.TXP_MAX, cur["txpower"]))
        + field("radio_freq", "<input name='freq' inputmode='decimal' value='" + radio.hz_to_mhz(cur["frequency"]) + "'>")
        + field("radio_bw", "<select name='bw'>" + opts(radio.BANDWIDTHS, cur["bandwidth"],
                lambda v: ("%g" % (v / 1000)) + " kHz") + "</select>")
        + field("radio_sf", "<select name='sf'>" + opts(range(radio.SF_MIN, radio.SF_MAX + 1), cur["sf"]) + "</select>")
        + field("radio_cr", "<select name='cr'>" + opts(range(radio.CR_MIN, radio.CR_MAX + 1), cur["cr"],
                lambda v: "4/%d" % v) + "</select>")
        + "<button type='submit'>" + i18n.t("admin_radio_save", lang) + "</button>"
        "</form>"
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
        "<li><label><input type='checkbox' name='ts' value='" + billboard._esc(pid) + "'> "
        + "<span>" + billboard._esc(title) + " <small>&mdash; " + billboard._esc(sig) + "</small>"
        # Full body, not collapsed: a moderator needs to see exactly what
        # they're about to remove, which is often in the body, not the title.
        + ("<span class='post-body'>" + billboard._esc(body) + "</span>" if body else "")
        + "</span></label></li>"
        for sig, title, body, ts, pid in reversed(entries)
    )
    return (
        "<h2 class='sub'>" + i18n.t("admin_billboard_header", lang) + "</h2>"
        "<form method='POST' action='/admin/delete_post' autocomplete='off'>"
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
        "<button type='button' onclick=\"stumpSetTheme('site')\">" + i18n.t("admin_theme_site", lang)
        + " (" + i18n.t("admin_theme_" + theme.site_theme(), lang) + ")</button>"
        "<button type='button' onclick=\"stumpSetTheme('amber')\">" + i18n.t("admin_theme_amber", lang) + "</button>"
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
        ).replace("STUMP_SITE_THEME", json.dumps(theme.site_theme())) + "</script>"
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
    if (t === 'site') {
      // Follow the node's own theme again (set by the technician).
      localStorage.removeItem('stump_theme');
      document.documentElement.setAttribute('data-theme', STUMP_SITE_THEME);
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
    // The in-between shades (tiles, sidebars, dividers, secondary text)
    // derived from the five picked colours, so a light custom palette
    // doesn't keep the dark amber versions of them.
    function mix(a, b){
      var r = '#';
      for (var i = 1; i < 7; i += 2) {
        var v = Math.round((parseInt(a.substr(i, 2), 16) + parseInt(b.substr(i, 2), 16)) / 2);
        r += ('0' + v.toString(16)).slice(-2);
      }
      return r;
    }
    colors['--panel-2'] = mix(colors['--bg'], colors['--panel']);
    colors['--line'] = mix(colors['--panel'], colors['--border']);
    colors['--muted'] = mix(colors['--text'], colors['--bg']);
    colors['--dim'] = colors['--muted'];
    colors['--ember-bright'] = colors['--ember'];
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
    document.documentElement.setAttribute('data-theme', STUMP_SITE_THEME);
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

    # Upload lives here, next to the shelf it adds to (it used to sit on
    # the home page). Shown only with a card mounted -- without one,
    # /upload can only answer "no card", so offering it would be a trap.
    upload = ""
    if fserv.sd_ok:
        upload = (
            "<div class='panel'>"
            "<p class='sub' style='margin-top:0;'>" + i18n.t("home_bring_something", lang) + "</p>"
            "<input type='file' id='upfile'>"
            "<div class='row'>"
            "<input id='uphash' style='display:none' value=''>"
            "<button id='upbtn' onclick='doUpload()'>" + i18n.t("home_upload_button", lang) + "</button>"
            "</div>"
            "<p id='upstatus'><small></small></p>"
            "</div>"
            + _UPLOAD_SCRIPT
                .replace("I18N_SENDING", json.dumps(i18n.t("home_sending", lang)).replace("</", "<\\/"))
                .replace("I18N_UPLOAD_ERROR", json.dumps(i18n.t("home_upload_error", lang)).replace("</", "<\\/"))
        )

    return (
        "<h1>" + i18n.t("nav_files", lang) + "</h1>"
        + _lang_switcher(lang, "/files") +
        "<p class='sub'>" + i18n.t("files_tap_to_download", lang) + "</p>"
        "<div class='panel' id='file-list'>" + rows + "</div>"
        + upload
        + _nav_except(lang, "files")
    )


# The upload button's script. Three real, reported gaps fixed earlier
# stay fixed: a .catch() so a failed request shows an error instead of
# "Sending..." forever; the button disabled while a request is in flight
# (no overlapping uploads from impatient clicks); the file input cleared
# once any response arrives. New with the move to /files: on success the
# file list refreshes in place, so the new file appears while the
# confirmation (and any credit balance) stays on screen. Translated text
# goes in via json.dumps, never raw into a quoted JS string -- the
# apostrophe in the French error message broke this whole script once.
_UPLOAD_SCRIPT = """<script>
function doUpload(){
  var file=document.getElementById('upfile').files[0];
  if(!file){return;}
  var hash=document.getElementById('uphash').value;
  var status=document.getElementById('upstatus');
  var btn=document.getElementById('upbtn');
  function say(t){ status.innerHTML='<small></small>'; status.firstChild.textContent=t; }
  btn.disabled=true;
  say(I18N_SENDING);
  var ok=false;
  fetch('/upload',{method:'POST',headers:{'X-Filename':file.name,'X-Hash':hash},body:file})
    .then(function(r){ok=r.ok; return r.text();})
    .then(function(t){
      say(t);
      document.getElementById('upfile').value='';
      btn.disabled=false;
      if(ok) refreshList();
    })
    .catch(function(){
      say(I18N_UPLOAD_ERROR);
      btn.disabled=false;
    });
}
function refreshList(){
  fetch('/files').then(function(r){return r.text();}).then(function(h){
    var fresh=new DOMParser().parseFromString(h,'text/html').getElementById('file-list');
    if(fresh) document.getElementById('file-list').innerHTML=fresh.innerHTML;
  }).catch(function(){});
}
</script>"""


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
    apps = fserv.find_apps()
    app_items = []
    for key, h2, title, desc, note, gh_label, gh_url, icon in _APP_INFO:
        a = apps.get(key)
        if a:
            app_items.append(
                "<li><a href='/tool?f=" + _url_encode(a["file"]) + "'>"
                + _ICON_DOWNLOAD + "<span>" + billboard._esc(a["file"]) + "</span></a>"
                "<small>" + _human_size(a["size"]) + " · " + i18n.t(title, lang) + "</small></li>"
            )
    tools = [t for t in fserv.list_tools() if not fserv.is_app_file(t)]
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

    apps_listing = (
        "<h2>" + i18n.t("tools_apps_h2", lang) + "</h2>"
        "<div class='panel'><ul class='files'>" + "".join(app_items) + "</ul></div>"
        "<h2>" + i18n.t("tools_tech_h2", lang) + "</h2>"
    ) if app_items else ""
    return (
        "<h1>" + i18n.t("tools_header", lang) + "</h1>"
        + _lang_switcher(lang, "/tools") +
        "<p class='sub'>" + i18n.t("tools_intro", lang) + "</p>"
        + apps_listing + listing
        + _nav_except(lang, "tools")
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


def _current_ap_ssid():
    """The name this node's Wi-Fi actually broadcasts, read live -- the
    connect steps name it instead of guessing a prefix (a technician can
    choose any name)."""
    try:
        import network
        name = network.WLAN(network.AP_IF).config("essid")
        if name:
            return name
    except Exception:
        pass
    # Not readable live: the same choice the node made at boot --
    # config.SSID_NAME, or the default hosted-page name when it's None.
    try:
        from config import SSID_NAME
        if SSID_NAME:
            return SSID_NAME
    except Exception:
        pass
    try:
        import captive_portal
        return captive_portal.DEFAULT_SSID
    except Exception:
        return "LaBuche-Stump.web.app"


_ICON_PHONE = ("<rect x='5' y='2' width='14' height='20' rx='2' ry='2'></rect>"
               "<line x1='12' y1='18' x2='12.01' y2='18'></line>")
_ICON_HANDHELD = ("<rect x='2' y='6' width='20' height='12' rx='2'></rect><line x1='6' y1='12' x2='10' y2='12'></line>"
                  "<line x1='8' y1='10' x2='8' y2='14'></line><line x1='15' y1='13' x2='15.01' y2='13'></line>"
                  "<line x1='18' y1='11' x2='18.01' y2='11'></line>")
_APP_INFO = (   # key, heading, title, description, install note, GitHub label, GitHub URL, icon
    ("android", "about_app1_h2", "about_app1_title", "about_app1_desc", "about_app1_install", "about_app1_github",
     "https://github.com/ruderigo/FireFly-Android", _ICON_PHONE),
    ("rk3326", "about_app2_h2", "about_app2_title", "about_app2_desc", "about_app2_install", "about_app2_github",
     "https://github.com/ruderigo/FireFly_RK3326_R36MAX_R36S", _ICON_HANDHELD),
)


def _render_app_cards(lang):
    """The apps pane: the site's cards, plus what matters on the Stump's own
    Wi-Fi, where there's no internet -- a download from this node (when the
    file is on its card), with GitHub kept as the second, online-only link."""
    apps = fserv.find_apps()
    out = "<p class='sub'>" + i18n.t("about_apps_tagline", lang) + "</p>"
    for key, h2, title, desc, note, gh_label, gh_url, icon in _APP_INFO:
        a = apps.get(key)
        if a:
            local = ("<a class='device-btn' href='/tool?f=" + _url_encode(a["file"]) + "'>"
                     "<span>" + i18n.t("about_app_download", lang) + "</span> "
                     "<small>v" + billboard._esc(a["version"]) + " · " + _human_size(a["size"]) + "</small></a>"
                     "<p class='device-note'>" + i18n.t(note, lang) + "</p>")
        else:
            local = "<p class='device-note'>" + i18n.t("about_app_missing", lang) + "</p>"
        out += (
            "<h2>" + i18n.t(h2, lang) + "</h2>"
            "<div class='device-card'>"
            "<div class='device-header'><svg class='device-icon' viewBox='0 0 24 24'>" + icon + "</svg>"
            "<div class='device-title'>" + i18n.t(title, lang) + "</div></div>"
            "<p class='device-desc'>" + i18n.t(desc, lang) + "</p>"
            + local +
            "<a class='device-btn secondary' href='" + gh_url + "' target='_blank' rel='noopener'>"
            "<span>" + i18n.t(gh_label, lang) + "</span> &rarr; <small>" + i18n.t("about_app_github_note", lang) + "</small></a>"
            "</div>"
        )
    return out


def _render_about_page(lang):
    """The About page, mirroring the public site (labuche-stump.web.app):
    three views switched client-side -- the project, how to connect, and
    the FireFly apps -- in the same server-side language as every other
    page. The site's copy, with what only the node can do better: the
    connect steps name the network it actually broadcasts and its live
    address, and the apps view offers each FireFly as a download from
    this node (found on its SD card by name, see fserv.find_apps), since
    on its own Wi-Fi there is no internet to reach GitHub.
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
        "<div class='title'>LaBuche-Stump</div><div class='desc'>" + i18n.t("about_link_stump_desc", lang) + "</div></a>"
        "<a class='link-tile' href='https://github.com/ruderigo/FireFly-Android' target='_blank' rel='noopener'>"
        "<div class='title'>FireFly-Android</div><div class='desc'>" + i18n.t("about_link_android_desc", lang) + "</div></a>"
        "<a class='link-tile' href='https://github.com/ruderigo/FireFly_RK3326_R36MAX_R36S' target='_blank' rel='noopener'>"
        "<div class='title'>FireFly_RK3326</div><div class='desc'>" + i18n.t("about_link_rk_desc", lang) + "</div></a>"
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
               "about_connect_step1_title", "about_connect_step1_text",
               ssid="<strong>" + billboard._esc(_current_ap_ssid()) + "</strong>")
        + step("<rect x='3' y='11' width='18' height='11' rx='2' ry='2'/><path d='M7 11V7a5 5 0 0 1 9.9-1'/>",
               "about_connect_step2_title", "about_connect_step2_text")
        + step("<circle cx='12' cy='12' r='10'/><polygon points='12 8 8 12 12 16 12 8'/><line x1='16' y1='12' x2='8' y2='12'/>",
               "about_connect_step3_title", "about_connect_step3_text", ip=ip_html)
        + "</div>"
    )

    apps_pane = _render_app_cards(lang)
    tabs = (
        "<div class='tab-bar'>"
        "<button class='view-btn active' id='tab-about' onclick=\"switchAboutView('about')\">"
        + i18n.t("about_tab_about", lang) + "</button>"
        "<button class='view-btn' id='tab-connect' onclick=\"switchAboutView('connect')\">"
        + i18n.t("about_tab_connect", lang) + "</button>"
        "<button class='view-btn' id='tab-apps' onclick=\"switchAboutView('apps')\">"
        + i18n.t("about_tab_apps", lang) + "</button>"
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
        "<div class='view-pane' id='pane-apps'>" + apps_pane + "</div>"
        + _nav_except(lang, "about")
        + script
    )


def _render_billboard_page(lang):
    return billboard._render_page(lang) + _nav_except(lang, "board")


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
    "wasm": "application/wasm",
    "js": "application/javascript",
    "apk": "application/vnd.android.package-archive",
    # For a home page's own section (docs/HOME_BRANDING.md): a browser refuses a
    # stylesheet sent as generic data, and fonts need their own types.
    "css": "text/css",
    "woff2": "font/woff2",
    "woff": "font/woff",
    "ttf": "font/ttf",
    "otf": "font/otf",
    "ico": "image/x-icon",
    "avif": "image/avif",
    # A complete page dropped in home/ is shown in a frame: it has to be
    # served as a page, or the browser downloads it instead.
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
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
    default) keep forcing Save-As; inline is for files a page loads itself
    -- the voice codecs (codec2.wasm, opus.wasm and their scripts).

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


def _voice_gate(identifier, to):
    """The refusal a /msg to `to` would get from stumpid's access mode, or
    None -- a voice note obeys the same rule as the text it replaces."""
    try:
        import stumpid.install as sid_install
        if sid_install._original is None:
            return None
        from stumpid import core as sid
        if sid.requires_verification(identifier, "/msg " + (to or "x") + " x"):
            return i18n.t("auth_required_generic", i18n.get_lang(identifier))
    except ImportError:
        pass
    return None


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


async def _read_small_body(reader, length, chunk=1024, cap=None):
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
    cap = MAX_SMALL_BODY if cap is None else cap
    if length > cap:
        length = cap
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


# Requests that are part of an already-open page (polls, uploads, audio,
# downloads, data). Only whole pages are redirected to HTTPS: redirecting
# these would break a page still open over HTTP, since the browser
# refuses a cross-origin redirect for them.
_NO_REDIRECT = ("/home-file", "/tls-ok", "/rrc/poll", "/rrc/voice", "/rrc/send", "/download", "/codec2", "/opus.", "/billboard.json",
                "/files.json", "/fw", "/tool?", "/lang", "/upload", "/post", "/chat")


def _https_upgrade_page(target, path):
    """A tiny page that tries HTTPS first. If the browser can fetch
    https://<host>/tls-ok -- certificate valid, by the phone's own clock --
    it moves to the HTTPS address; otherwise (expired, wrong clock, no
    answer within 3 s) it reloads the same page over HTTP with plain=1,
    which skips this check. The flow never shows a certificate warning."""
    host = target.split("/")[2]
    back = path + ("&" if "?" in path else "?") + "plain=1"
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Stump</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'></head><body><script>"
            "var go=window.__stumpGo||function(u){location.replace(u);},done=false;"
            "function to(u){if(!done){done=true;go(u);}}"
            "fetch('https://" + host + "/tls-ok',{mode:'no-cors',cache:'no-store'})"
            ".then(function(){to(" + json.dumps(target) + ");},function(){to(" + json.dumps(back) + ");});"
            "setTimeout(function(){to(" + json.dumps(back) + ");},3000);"
            "</script><noscript><meta http-equiv='refresh' content=\"0;url=" + billboard._esc(back) + "\"></noscript>"
            "</body></html>")


async def _handle_tls(reader, writer):
    await _handle(reader, writer, secure=True)


async def _handle(reader, writer, secure=False):
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

        # With HTTPS set up (stump_tls.py), a page opened over plain HTTP on
        # the Stump's own Wi-Fi moves to https://<hostname>/ -- a secure
        # page, which is what lets browsers open the microphone -- but only
        # if the BROWSER finds the certificate valid. A server-side redirect
        # used to send visitors into a full-page warning once a certificate
        # lapsed (or on a phone with a wrong clock). Now the phone checks,
        # against its own clock, and stays on HTTP silently if anything is
        # off. No redirect also leaves captive-portal detection as it was.
        if not secure and method == "GET" and "plain=1" not in path \
                and not any(path.startswith(p) for p in _NO_REDIRECT):
            target = tls.https_redirect(identifier, path)
            if target:
                await _send(writer, "200 OK", _https_upgrade_page(target, path))
                return

        if method == "GET" and path.startswith("/tls-ok"):
            # What the upgrade page fetches over HTTPS: reaching it at all
            # means the browser accepted the certificate.
            await _send(writer, "200 OK", "ok", "text/plain")
            return

        # A feature the technician turned off (features.py) is gone,
        # not just unlinked: every one of its addresses answers 404.
        if features.path_blocked(path):
            await _send(writer, "404 Not Found",
                        i18n.t("feature_off", i18n.get_lang(identifier)), "text/plain")
            return

        if method == "GET" and path.startswith("/concierge"):
            # HF-003: the Concierge, unlinked (see docs/HIDDEN_FEATURES.md).
            await _send(writer, "200 OK", _page(_render_concierge_page(i18n.get_lang(identifier))))
            return

        if method == "POST" and path.startswith("/chat"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            reply = _process_command(body.decode(), identifier, i18n.get_lang(identifier))
            await _send(writer, "200 OK", reply, "text/plain")

        elif method == "POST" and path.startswith("/post"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            # title= and body= from the current form. entry= is the
            # original single-field form, still accepted as a title-only
            # post so existing clients (the Android integration's tech
            # sheet documents it) keep working unchanged.
            title = ""
            post_body = ""
            for kv in body.decode().split("&"):
                if kv.startswith("title="):
                    title = billboard._url_decode(kv[6:])
                elif kv.startswith("body="):
                    post_body = billboard._url_decode(kv[5:])
                elif kv.startswith("entry=") and not title:
                    title = billboard._url_decode(kv[6:])
            billboard._append_entry(title, post_body, identifier)
            await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /billboard\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "GET" and path.startswith("/admin"):
            # No link anywhere points here on purpose -- see
            # _render_admin_login_page's own docstring.
            await _send(writer, "200 OK", _page(_render_admin_login_page(i18n.get_lang(identifier))))

        elif method == "POST" and path.startswith("/admin/home"):
            # Before the generic POST /admin (login) branch, like the others.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                try:
                    height = int(fields.get("height", "280"))
                except ValueError:
                    height = -1
                # An unticked checkbox sends nothing: no "show" means hide.
                why = fserv.save_home_config(fields.get("place", ""), height, fields.get("show") != "1")
                msg = i18n.t("admin_home_saved", lang) if why is None else i18n.t("admin_home_failed", lang, why=why)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/propagation"):
            # Before the generic POST /admin (login) branch, like /admin/radio.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                action = fields.get("action", "")
                if action == "on":
                    propagation.save_setting(True)
                    if propagation.enable():
                        propagation.announce()
                        msg = i18n.t("admin_pn_enabled", lang)
                    else:
                        msg = i18n.t("admin_pn_no_sd", lang)
                elif action == "off":
                    propagation.save_setting(False)
                    propagation.disable()
                    msg = i18n.t("admin_pn_disabled", lang)
                elif action == "leave":
                    err = propagation.leave_message(fields.get("to", ""), fields.get("text", "").strip() or "test")
                    msg = i18n.t({None: "admin_pn_left", "off": "admin_pn_off_state", "address": "admin_pn_bad_address",
                                  "unknown": "admin_pn_unknown"}.get(err, "admin_pn_unknown"), lang)
                else:
                    msg = ""
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>" if msg else ""
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/radio"):
            # Must come before the generic POST /admin (login) branch
            # below, which catches every other /admin POST.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _check_admin_pw(pw):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                settings, bad = radio.parse_form(fields)
                if bad:
                    label = {"frequency": "radio_freq", "bandwidth": "radio_bw", "txpower": "radio_txp",
                             "sf": "radio_sf", "cr": "radio_cr"}[bad]
                    msg = i18n.t("admin_radio_invalid", lang, field=i18n.t(label, lang))
                else:
                    result = radio.apply(settings)
                    saved = radio.save(settings)
                    if not saved:
                        msg = i18n.t("admin_radio_not_saved", lang)
                    elif result == "sent":
                        msg = i18n.t("admin_radio_sent", lang)
                    elif result == "pending":
                        msg = i18n.t("admin_radio_pending", lang)
                    else:
                        msg = i18n.t("admin_radio_none", lang)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

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
                deleted_titles = []
                for ts_str in timestamps:
                    removed = billboard.delete_entry(ts_str) if ts_str else False
                    if removed:
                        deleted_titles.append(removed if removed is not True else "?")
                # Name exactly what was removed, not just how many -- so a
                # moderator can see at a glance that it matches what they
                # ticked.
                lang = i18n.get_lang(identifier)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + i18n.t("admin_post_deleted_notice", lang, n=len(deleted_titles))
                if deleted_titles:
                    notice += " " + ", ".join("« " + billboard._esc(t) + " »" for t in deleted_titles)
                notice += "</p>"
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
                "topic": rrc.topic(actual, i18n.get_lang(identifier)),
                "rooms": rrc.room_names(),
                "messages": rrc.since(actual, since_id),
                # Private messages ride the same poll rather than a
                # second endpoint: one request per cycle instead of two
                # on a board where each connection costs real work, and
                # they share the message-id sequence so the client's
                # existing since/lastId bookkeeping covers both.
                # Voice notes are described here; their audio is fetched
                # separately from /rrc/voice (it isn't JSON).
                "dms": [rrc.dm_public(m) for m in rrc.dms_since(identifier, since_id)],
                # Who's actually in this room right now -- lets the
                # client offer "select someone to DM" instead of
                # requiring the exact nick typed blind into /msg. Sent
                # every poll (not just when it changes): cheap, already
                # pruned server-side by users_in_room's own
                # _prune_users call, and simpler than a second
                # changed-since check for a list this short (MAX_USERS
                # is 40).
                # People in this room, plus mesh peers reachable only by
                # DM (heard by announce, never messaged) -- the web UI
                # lists these under "Message someone". /names and room
                # counts stay room-only.
                "users": sorted(set(rrc.users_in_room(actual)) | set(rrc.reachable_nicks())),
                # Which of those are other Stump nodes (from their
                # stump.node beacons), so the UI can label them.
                "stumps": rrc.stump_nicks(),
                # Which node this is -- a client reaching it over Wi-Fi
                # and LoRa matches the two by this LXMF address.
                "node": rrc.NODE,
            }
            await _send(writer, "200 OK", json.dumps(payload), "application/json")

        elif method == "POST" and path.startswith("/rrc/voice"):
            # A voice note: raw Codec 2 frames as the body, ?to=<nick>&mode=<n>.
            # Same access rule as the /msg it stands in for.
            # Its own, larger limit: a 15 s Opus note (FireFly's default) is
            # ~13.4 KB, past the 8 KB every other small body gets.
            length = int(headers.get("content-length", "0"))
            if length > rrc.VOICE_MAX_BYTES:
                await _send(writer, "413 Payload Too Large", json.dumps({"replies": ["too large"], "room": None}),
                            "application/json")
                return
            audio = await _read_small_body(reader, length, cap=rrc.VOICE_MAX_BYTES)
            to = billboard._url_decode(_query(path, "to") or "")
            try:
                mode = int(_query(path, "mode") or "4")
            except ValueError:
                mode = 0
            refused = _voice_gate(identifier, to)
            replies = [refused] if refused else rrc.send_voice(identifier, to, mode, audio)
            await _send(writer, "200 OK", json.dumps({"replies": replies, "room": None}), "application/json")

        elif method == "GET" and path.startswith("/rrc/voice"):
            # The audio of a voice note in this client's own inbox, as raw
            # Codec 2 frames; its mode is in the poll's "voice" object.
            try:
                m = rrc.find_dm(identifier, int(_query(path, "id") or "-1"))
            except ValueError:
                m = None
            if m is None or "audio" not in m:
                await _send(writer, "404 Not Found", "no such voice note", "text/plain")
            else:
                await _send(writer, "200 OK", m["audio"][1],
                            "audio/ogg" if m["audio"][0] == rrc.OPUS_OGG else "application/octet-stream")

        elif method == "GET" and path.split("?")[0] in ("/codec2.wasm", "/codec2.js", "/opus.wasm", "/opus.js"):
            # The voice codecs for the browser -- Codec 2 (LGPL-2.1) and Opus
            # (BSD) -- served from the board so voice notes work offline.
            name = path.split("?")[0][1:]
            # Streamed in chunks: opus.wasm is 333 KB, and reading it whole
            # needed one contiguous 333 KB allocation -- a MemoryError on a
            # fragmented heap, right when someone taps play.
            try:
                size = os.stat("web/" + name)[6]
                await _send_file(writer, "web/" + name, size, name, inline=True)
            except OSError:
                await _send(writer, "404 Not Found", "missing " + name, "text/plain")

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

        elif method == "GET" and path.startswith("/about"):
            await _send(writer, "200 OK", _page(_render_about_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/tools"):
            await _send(writer, "200 OK", _page(_render_tools_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/home-file"):
            # Files the home page's branding uses (images, a font...), from
            # the card's home/ folder only: the same name guard as /tool.
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            hname = _clean_filename(billboard._url_decode(raw_f))
            try:
                size = os.stat(fserv.HOME_DIR + "/" + hname)[6]
                await _send_file(writer, fserv.HOME_DIR + "/" + hname, size, hname, inline=True)
            except OSError:
                await _send(writer, "404 Not Found", "no such file", "text/plain")

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

        elif method == "GET" and path.startswith("/billboard.json"):
            # The billboard as data: newest first, as the page shows it.
            # "id" is the post id (null for a post written before ids
            # existed, until the next rewrite gives it one).
            posts = [{"id": pid, "title": title, "body": body, "sig": sig}
                     for sig, title, body, ts, pid in reversed(billboard._read_entries())]
            await _send(writer, "200 OK", json.dumps({"posts": posts}), "application/json")

        elif method == "GET" and path.startswith("/files.json"):
            # The shelf as data. Download with /download?f=<name>, the
            # name percent-encoded as UTF-8.
            files = []
            if fserv.sd_ok:
                for n in fserv._list_files():
                    try:
                        size = os.stat(fserv.SHARED_DIR + "/" + n)[6]
                    except OSError:
                        size = None
                    files.append({"name": n, "size": size, "class": fserv.guess_class(n),
                                  "cost": fserv.credit_cost(n) if fserv.CREDITS_ENABLED else 0})
            await _send(writer, "200 OK", json.dumps({"sd": fserv.sd_ok, "credits": fserv.CREDITS_ENABLED,
                                                      "files": files}), "application/json")

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
    # HTTPS on 443, when a certificate is installed (tls.py). Failing here
    # never takes plain HTTP down with it.
    ctx = tls.context()
    if ctx is not None:
        try:
            await asyncio.start_server(_handle_tls, "0.0.0.0", 443, ssl=ctx)
            print("[web] HTTPS on 443 for", tls.status()["host"])
        except Exception as e:
            tls.mark_failed("couldn't open port 443: %s" % e)
            print("[web] HTTPS not started:", e)
    else:
        print("[web] HTTPS off:", tls.status()["reason"])
    print("[web] serving on port", port)
