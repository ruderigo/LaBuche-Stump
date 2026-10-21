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
import plugins
import theme
import layout
import features
import radio
import settings as stump_settings
import profiles
import guard     # ("settings" is a local name in places here)
import conn      # the connections' deadlines and limits
import ubinascii
import keepalive  # a rescue start, the watchdog's last restart
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
ul.files a span{overflow-wrap:anywhere; min-width:0;}
ul.files a svg{flex:none;}
ul.files small{flex-shrink:0; padding-left:8px; text-align:right; max-width:40%;}
/* Phones: the name gets the whole width, its details go under it. */
@media (max-width:560px){
  ul.files li{flex-wrap:wrap; gap:0;}
  ul.files a{flex-basis:100%; padding-bottom:2px;}
  ul.files small{max-width:none; text-align:left; padding:0 4px 12px 38px;}
}
/* BarKeep the librarian, and the sort / filter bar (/files, /billboard) */
/* BarKeep the librarian: a firefly in the corner that opens into a chat */
body.has-ff{padding-bottom:96px;}
.ask{position:fixed; right:16px; bottom:16px; z-index:50; display:flex; flex-direction:column; align-items:flex-end; gap:10px;}
.ff-btn{width:66px; height:66px; border-radius:50%; padding:0; border:1px solid var(--border); cursor:pointer;
  background:radial-gradient(circle at 50% 50%, rgba(216,255,122,.22), transparent 62%), var(--panel);
  box-shadow:0 6px 22px rgba(0,0,0,.45); display:flex; align-items:center; justify-content:center;}
.ff-btn:hover{border-color:var(--ember);}
.ff-btn .bug{margin-top:10px;}
.bug{display:inline-block; white-space:pre; line-height:.95; font:16px/0.95 ui-monospace,'SF Mono','Courier New',monospace;
  color:var(--muted); text-align:center; animation:ffdrift 4.5s ease-in-out infinite;}
.bug .c{color:#d8ff7a; text-shadow:0 0 5px #d8ff7a, 0 0 12px #9be33c, 0 0 22px #6fbf1f; animation:ffglow 2.6s ease-in-out infinite;}
.bug .t{color:var(--dim); font-size:.8em; animation:fftrail 2.6s ease-in-out infinite;}
.ask.busy .bug .c{animation-duration:.7s;}
@keyframes ffglow{0%,100%{opacity:1}50%{opacity:.55}}
@keyframes fftrail{0%,100%{opacity:.2}50%{opacity:.8}}
@keyframes ffdrift{0%,100%{transform:translate(0,0)}25%{transform:translate(2px,-3px)}50%{transform:translate(0,-5px)}75%{transform:translate(-2px,-2px)}}
@media (prefers-reduced-motion:reduce){.bug,.bug *{animation:none!important;}}
.ff-panel{width:min(360px, calc(100vw - 32px)); max-height:min(560px, calc(100dvh - 120px)); display:flex; flex-direction:column;
  background:var(--panel); border:1px solid var(--border); border-radius:16px; box-shadow:0 10px 34px rgba(0,0,0,.5); overflow:hidden;}
.ff-panel[hidden]{display:none;}
.ff-head{display:flex; align-items:center; gap:10px; padding:10px 12px; border-bottom:1px solid var(--line); background:var(--panel-2);}
.ff-head .bug{font-size:12px;}
.ff-head div{display:flex; flex-direction:column; min-width:0; line-height:1.2;}
.ff-head small{color:var(--muted);}
.ff-x{margin-left:auto; background:none; color:var(--muted); border:none; font-size:1.4rem; padding:0 6px; cursor:pointer;}
.ff-log{flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:8px; min-height:120px;}
.ff-log .m{max-width:92%; padding:8px 11px; border-radius:14px; overflow-wrap:anywhere; word-break:break-word; font-size:.93rem; line-height:1.4;}
.ff-log .m p{margin:0 0 6px;} .ff-log .m p:last-child{margin:0;}
.ff-log .bot{background:var(--panel-2); border:1px solid var(--line); border-bottom-left-radius:4px; align-self:flex-start;}
.ff-log .you{background:var(--ember); color:var(--bg); border-bottom-right-radius:4px; align-self:flex-end;}
.ff-log ul{list-style:none; padding:0; margin:4px 0 6px;}
.ff-log li{padding:3px 0; border:none;}
.ff-log li a{word-break:break-all;}
.ff-log small{color:var(--muted); white-space:nowrap;}
.ff-chips{display:flex; flex-wrap:wrap; gap:6px; padding:0 12px 8px;}
.ff-chips button{padding:5px 10px; font-size:.82rem; font-weight:normal; background:var(--panel-2); color:var(--text);
  border:1px solid var(--border); border-radius:999px;}
.ff-in{display:flex; gap:6px; padding:10px 12px; border-top:1px solid var(--line);}
.ff-in input{flex:1; min-width:0;}
.ff-in button{padding:0 14px;}
.finder{display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:8px 0;}
.finder input{flex:1; min-width:140px;}
.finder select{font-family:inherit; font-size:1rem; padding:9px; border-radius:6px;
  border:1px solid var(--border); background:var(--panel); color:var(--text);}
/* Sort: one ⇅ in any language; the browser's own list (translated) opens on tap. */
.sortbox{position:relative; flex:none; width:48px; height:44px;}
.sortbox::before{content:'\\21C5'; position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  font-size:1.3rem; color:var(--text); pointer-events:none; border:1px solid var(--border); border-radius:6px; background:var(--panel);}
.sortbox:focus-within::before{border-color:var(--ember);}
.finder .sortbox select{position:absolute; inset:0; width:100%; height:100%; opacity:0; cursor:pointer; padding:0;}
.pills{display:flex; flex-wrap:wrap; gap:6px; width:100%;}
.pills button{padding:6px 12px; font-weight:normal; background:var(--panel); color:var(--text);
  border:1px solid var(--border);}
.pills button.on{background:var(--ember); color:var(--bg); border-color:var(--ember);}
/* /admin */
.admin-head{display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;}
.admin-wide{max-width:640px; margin:0 auto;}
.atiles{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:14px 0 6px;}
@media (max-width:560px){.atiles{grid-template-columns:repeat(2,minmax(0,1fr));}}
.atile{display:flex; flex-direction:column; align-items:flex-start; gap:2px; padding:12px 14px; background:var(--panel);
  border:1px solid var(--border); border-radius:12px; color:var(--text); text-decoration:none; min-width:0;}
a.atile:hover{border-color:var(--ember);}
.atile .ti{font-size:1.3rem; line-height:1.2; height:1.6rem; display:flex; align-items:center;}
.atile b{font-size:1.05rem; line-height:1.25; overflow-wrap:anywhere;}
.atile small{color:var(--muted); font-size:.8rem; overflow-wrap:anywhere;}
h2.grp{font-family:ui-monospace,'SF Mono','Courier New',monospace; font-size:.75rem; letter-spacing:.12em;
  text-transform:uppercase; color:var(--dim); margin:22px 0 8px; font-weight:normal;}
details.card{background:var(--panel); border:1px solid var(--border); border-radius:12px; margin:0 0 8px; overflow:hidden;}
details.card>summary{display:flex; align-items:center; gap:10px; padding:12px 14px; cursor:pointer; list-style:none;}
details.card>summary::-webkit-details-marker{display:none;}
details.card>summary::after{content:'\u25b8'; color:var(--muted); margin-left:auto; transition:transform .15s;}
details.card[open]>summary::after{transform:rotate(90deg);}
details.card[open]>summary{border-bottom:1px solid var(--line);}
details.card .ic{font-size:1.15rem; width:1.6rem; text-align:center; flex:none;}
details.card .tx{display:flex; flex-direction:column; min-width:0; line-height:1.3;}
details.card .ct{font-weight:bold; color:var(--text);}
details.card>summary::after{flex:none;}
details.card .pk{color:var(--muted); font-size:.82rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0;}
details.card .cb{padding:4px 14px 14px;}
details.card .cb h2.sub{font-size:.95rem; margin:16px 0 6px;}
details.inner{border:1px solid var(--line); border-radius:10px; margin:10px 0; background:var(--panel-2);}
details.inner>summary{padding:10px 12px; cursor:pointer; font-weight:bold;}
details.inner>summary .pk{font-weight:normal; color:var(--muted); font-size:.82rem;}
details.inner>*:not(summary){margin-left:12px; margin-right:12px;}
details.inner[open]{padding-bottom:10px;}
.btrow{display:flex; flex-wrap:wrap; gap:8px; margin:6px 0;}
.seg{display:inline-flex; border:1px solid var(--border); border-radius:999px; overflow:hidden; margin:2px 0 6px;}
.seg button{border:none; border-radius:0; background:var(--panel); color:var(--text); font-weight:normal; padding:8px 16px;}
.seg button.on{background:var(--ember); color:var(--bg); font-weight:bold;}
#btpin{margin:8px 0; padding:12px; border:1px dashed var(--ember); border-radius:10px; text-align:center;}
#btpin small{display:block; color:var(--muted);}
details.tips>summary{cursor:pointer; color:var(--muted); font-size:.88rem;}
#btpin b{font:bold 2rem ui-monospace,'SF Mono','Courier New',monospace; letter-spacing:.18em; color:var(--ember-bright);}
i.dot{display:inline-block; width:9px; height:9px; border-radius:50%; background:#f2efe8; margin-right:6px; vertical-align:middle;}
i.dot.big{width:16px; height:16px; margin:0;}
i.dot.online{background:#3fb950;} i.dot.connecting{background:#f0a030;} i.dot.error{background:#e5534b;}
.admin-head h1{margin:0;}
form.logout{margin:16px 0;}
.admin-head form.logout{margin:0;}
ul.plist li{display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 14px;
  align-items:center; padding:10px 0;}
ul.plist .pname{font-weight:bold; word-break:normal; overflow-wrap:normal;}
ul.plist .pstate{color:var(--muted); font-size:.9em;}
ul.plist .pmeta, ul.plist .pcmds{display:block; font-size:.9em; color:var(--muted); margin-top:2px;}
ul.plist .pcmds{color:var(--text); overflow-wrap:anywhere;}
ul.plist .perr{display:block; color:var(--err); font-size:.9em; margin-top:2px;}
ul.plist form{margin:0;}
ul.plist button{white-space:nowrap;}
ol.reorder{list-style:none; padding:0; margin:0;}
ol.reorder li{display:flex; align-items:center; gap:10px; padding:6px 4px;
  border-bottom:1px solid var(--border); cursor:grab; user-select:none; -webkit-user-select:none;}
ol.reorder li:last-child{border-bottom:none;}
ol.reorder li.dragging{opacity:.4;}
ol.reorder li.off .rname{text-decoration:line-through; color:var(--muted);}
ol.reorder li.off svg{opacity:.35;}
ol.reorder button.vis{min-width:5.5em;}
ol.reorder li.off button.vis{background:var(--panel-2); color:var(--text); border:1px solid var(--border);}
ol.reorder li.over{border-top:2px solid var(--ember);}
ol.reorder svg{width:22px; height:22px; flex-shrink:0;}
ol.reorder .rname{flex:1; min-width:0;}
ol.reorder .grip{color:var(--muted);}
ol.reorder button{padding:6px 10px; font-size:.9rem;}
@media (max-width:420px){ ul.plist li{grid-template-columns:1fr;} ul.plist form{justify-self:start;} }
details.readme{margin-top:8px;}
details.readme summary{cursor:pointer; color:var(--muted);}
details.readme pre{white-space:pre-wrap; overflow-wrap:anywhere; font-size:.85em; margin:8px 0 0;}

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
form.setup details.inner{padding:0 12px;}
form.setup details.inner[open]{padding-bottom:10px;}
form.setup details.inner>summary{margin:0 -12px;}
form.setup label:not(.opt){display:block; margin:10px 0;}
form.setup input:not([type=radio]):not([type=checkbox]), form.setup textarea{width:100%; max-width:30rem; box-sizing:border-box;}
form.setup .weights{display:grid; grid-template-columns:repeat(auto-fill,minmax(7rem,1fr)); gap:0 12px;}
form.setup .weights p{grid-column:1/-1;}
form.setup .weights label{margin:4px 0;}
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

_ICON_PLUGIN = (
    "<svg viewBox='0 0 24 24' width='30' height='30' fill='none' "
    "stroke='currentColor' stroke-width='1.6' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M10 4.5a2 2 0 1 1 4 0V6h4v4h-1.5a2 2 0 1 0 0 4H18v4h-4"
    "v-1.5a2 2 0 1 0-4 0V18H6v-4h1.5a2 2 0 1 0 0-4H6V6h4z'/></svg>"
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
    # it: five cards squeezed into a grid were too compact to read. In
    # the order an admin set in /admin (layout.py).
    cards = home_tile_items(lang)
    return ("<div class='tiles home'>"
            + "".join(c[2] for c in cards) + "</div>")


_HOME_TILES = (
    ("chat", "chat", "/rrc", _ICON_CHAT, "nav_chat", "tile_chat_sub"),
    ("board", "billboard", "/billboard", _ICON_BOARD, "nav_board", "tile_board_sub"),
    ("files", "files", "/files", _ICON_FILES, "nav_files", "tile_files_sub"),
    ("tools", None, "/tools", _ICON_TOOLS, "nav_tools", "tile_tools_sub"),
    ("about", "about", "/about", _ICON_ABOUT, "nav_about", "tile_about_sub"),
)


def home_tile_items(lang, all=False):
    """[(key, label, html)] for the home page's tiles, in layout order:
    the built-in ones this node offers, then plugin cards (key "p:" +
    their address). Tiles an admin hid only with all=True."""
    items = {}
    keys = []
    for key, feat, href, icon, label, sub in _HOME_TILES:
        if feat is not None and not features.enabled(feat):
            continue
        name = i18n.t(label, lang)
        items[key] = (key, name, "<a class='tile' href='" + href + "'>" + icon +
                      "<div class='tile-text'><span>" + name + "</span>"
                      "<small>" + i18n.t(sub, lang) + "</small></div></a>")
        keys.append(key)
    for c in plugins.home_cards(lang):
        # Cards plugins asked for (stump.page(..., nav="Polls")).
        key = "p:" + c["href"]
        items[key] = (key, c["label"], "<a class='tile' href='" + _attr_esc(c["href"]) + "'>" + _ICON_PLUGIN +
                      "<div class='tile-text'><span>" + billboard._esc(c["label"]) + "</span>"
                      "<small>" + billboard._esc(c["sub"]) + "</small></div></a>")
        keys.append(key)
    return [items[k] for k in layout.tiles(keys, all=all)]


def _page(body):
    return ("<!DOCTYPE html>" + theme.html_open() + "<head><meta name='viewport' "
             "content='width=device-width, initial-scale=1'>"
             "<style>" + STYLE + "</style>"
             "<script>" + theme.startup_script() + "</script>"
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


def _librarian(text, lang):
    """BarKeep as the librarian (librarian.py): (reply HTML, data) for a
    question. Every name and text from the node is escaped, every link
    percent-encoded as UTF-8."""
    import librarian
    files_on, board_on = features.enabled("files"), features.enabled("billboard")
    r = librarian.search(text, files_on, board_on)
    esc = billboard._esc
    out = []
    topic = esc(r["topic"])

    def files_list(rows):
        return "<ul>" + "".join(
            "<li>&bull; <a href='/download?f=" + _url_encode(f["name"]) + "'>" + esc(f["name"])
            + "</a> <small>(" + _human_size(f["size"]) + ")</small></li>" for f in rows) + "</ul>"

    cats = [c for c in librarian.CATS if c in r["intents"]]
    if not r["intents"] and not r["words"]:
        return i18n.t("lib_hello", lang), r
    if r["words"] or cats:                          # asked about files
        if not files_on:
            out.append("<p>" + i18n.t("lib_files_off", lang) + "</p>")
        elif not r["sd"]:
            out.append("<p>" + i18n.t("lib_no_card", lang) + "</p>")
        elif r["files"]:
            out.append("<p>" + i18n.t("lib_files_found", lang) + "</p>" + files_list(r["files"]))
        elif r["others"]:
            out.append("<p>" + i18n.t("lib_files_none_topic", lang, topic=topic) + " "
                       + i18n.t("lib_files_others", lang, kind=i18n.t("kind_" + cats[0], lang)) + "</p>"
                       + files_list(r["others"]))
        elif cats:
            have = ", ".join("%d %s" % (n, i18n.t("kind_" + c, lang)) for c, n in sorted(r["counts"].items()))
            out.append("<p>" + i18n.t("lib_files_none_kind", lang, kind=i18n.t("kind_" + cats[0], lang)) + " "
                       + (i18n.t("lib_shelf_has", lang, list=have) + " <a href='/files'>"
                          + i18n.t("lib_files_open", lang) + "</a>" if have else i18n.t("lib_shelf_empty", lang))
                       + "</p>")
        elif r["posts"]:
            out.append("<p>" + i18n.t("lib_files_none_topic", lang, topic=topic) + "</p>")
        else:
            out.append("<p>" + i18n.t("lib_nothing", lang, topic=topic) + "</p>")
    if r["posts"]:
        out.append("<p>" + i18n.t("lib_board_found", lang) + "</p><ul>" + "".join(
            "<li>&bull; <b>" + esc(p["title"]) + "</b>"
            + (" &mdash; <small>" + esc(p["snippet"]) + "</small>" if p["snippet"] else "") + "</li>"
            for p in r["posts"]) + "</ul><p><a href='/billboard'>" + i18n.t("lib_board_open", lang) + "</a></p>")
    elif "board" in r["intents"] and board_on:
        out.append("<p>" + i18n.t("lib_board_none", lang) + "</p>")
    if "apps" in r["intents"]:
        if r["apps"]:
            out.append("<p>" + i18n.t("lib_apps", lang) + "</p><ul>" + "".join(
                "<li>&bull; <a href='/tool?f=" + _url_encode(a["file"]) + "'>" + esc(a["name"]) + "</a> <small>("
                + _human_size(a["size"]) + ")</small></li>" for a in r["apps"]) + "</ul>")
        else:
            out.append("<p>" + i18n.t("lib_apps_none", lang) + " <a href='/tools'>/tools</a></p>")
    return "".join(out) or i18n.t("lib_hello", lang), r


_FIREFLY = ("<span class='bug' aria-hidden='true'><span class='c'>(\u25cf)</span>\n"
            "<span class='t'>\u02d9 \u00b7</span></span>")


def _ask_drawer(lang, open_=False):
    """BarKeep the librarian, as a firefly in the corner (like a help chat):
    tap it and it opens into a small chat that answers through POST /chat --
    what's on this node: files, notices, apps. On /files, /billboard and
    the home page -- unless /admin turned it off (Stump settings)."""
    if not stump_settings.librarian_on():
        return ""
    name = _esc_name(BOT_NAME)
    chips = "".join("<button type='button'>" + billboard._esc(c) + "</button>"
                    for c in i18n.t("ask_chips", lang).split("|") if c)
    return (
        "<div class='ask' id='ask'>"
        "<div class='ff-panel' id='ffp' role='dialog' aria-label='" + _attr_esc(i18n.t("ask_title", lang, bot=BOT_NAME)) + "'"
        + ("" if open_ else " hidden") + ">"
        "<div class='ff-head'>" + _FIREFLY + "<div><b>" + name + "</b><small>" + i18n.t("ask_role", lang)
        + "</small></div><button type='button' class='ff-x' id='ffx' aria-label='\u00d7'>\u00d7</button></div>"
        "<div class='ff-log' id='askout' aria-live='polite'><div class='m bot'>" + i18n.t("lib_hello", lang) + "</div></div>"
        "<div class='ff-chips' id='ffc'>" + chips + "</div>"
        "<div class='ff-in'><input id='askq' maxlength='200' autocomplete='off' placeholder='"
        + billboard._esc(i18n.t("ask_short", lang)) + "'>"
        "<button id='askgo' aria-label='" + _attr_esc(i18n.t("ask_button", lang)) + "'>\u2192</button></div></div>"
        "<button type='button' class='ff-btn' id='ffb' aria-expanded='" + ("true" if open_ else "false")
        + "' aria-controls='ffp' title='" + _attr_esc(i18n.t("ask_title", lang, bot=BOT_NAME)) + "'>" + _FIREFLY + "</button>"
        "</div><script>" + _ASK_SCRIPT + "</script>"
    )


_ASK_SCRIPT = """(function(){
var box=document.getElementById('ask'), p=document.getElementById('ffp'), b=document.getElementById('ffb'),
    q=document.getElementById('askq'), out=document.getElementById('askout');
document.body.classList.add('has-ff');
function show(on){ p.hidden=!on; b.setAttribute('aria-expanded',on?'true':'false'); if(on){ q.focus(); out.scrollTop=out.scrollHeight; } }
function say(cls,html,text){ var m=document.createElement('div'); m.className='m '+cls;
  if(text!==undefined) m.textContent=text; else m.innerHTML=html; out.appendChild(m); out.scrollTop=out.scrollHeight; return m; }
window.askBK=function(t){ t=(t||q.value||'').trim(); if(!t) return; show(true); q.value='';
  say('you',null,t); var a=say('bot',null,'\u2026'); box.classList.add('busy');
  fetch('/chat',{method:'POST',body:t}).then(function(r){return r.text();})
    .then(function(h){ a.innerHTML=h; }, function(){ a.textContent='\u2014'; })
    .then(function(){ box.classList.remove('busy'); out.scrollTop=out.scrollHeight; }); };
b.onclick=function(){ show(p.hidden); };
document.getElementById('ffx').onclick=function(){ show(false); b.focus(); };
document.getElementById('askgo').onclick=function(){ askBK(); };
q.addEventListener('keydown',function(e){ if(e.key==='Enter') askBK(); });
document.addEventListener('keydown',function(e){ if(e.key==='Escape'&&!p.hidden) show(false); });
document.querySelectorAll('#ffc button').forEach(function(c){ c.onclick=function(){ askBK(c.textContent); }; });
})();"""


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

    # Anything else is a question for the librarian (librarian.py).
    return _librarian(cmd, lang)[0]


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
        "<p class='sub'>" + i18n.t("barkeep_greeting", lang, bot_name=_esc_name(BOT_NAME)) + " "
        + i18n.t("ask_hint", lang, bot=_esc_name(BOT_NAME)) + "</p>"
        "<div class='panel' id='log'>"
        "<p><b>" + _esc_name(BOT_NAME) + ":</b> " + i18n.t("barkeep_evening", lang) + "</p>"
        "</div>"
        "<div class='row'>"
        "<input id='in' placeholder='" + billboard._esc(i18n.t("ask_placeholder", lang, bot=BOT_NAME)) + "' autofocus>"
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
# billboard, tools and about were each missing a destination). The order
# is the admin's, from /admin (layout.py); home first by default.
NAV_ORDER = layout.NAV_DEFAULT


def _nav_except(lang, current):
    return _nav(lang, *[k for k in layout.nav() if k != current])


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
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / (1024 * 1024))
    g = "%.1f" % (n / (1024 * 1024 * 1024))
    return (g[:-2] if g.endswith(".0") else g) + " GB"


def _check_admin_pw(pw, who=None):
    """True if pw unlocks admin actions -- unless `who` has tried too many
    wrong passwords (guard.py): then False without trying."""
    import guard
    who = who or "web"
    # The address, and the door itself across all addresses -- loosely
    # (ADMIN_LOCK_AFTER wrong ones from anywhere), so guessing from many
    # devices is slowed without one stranger easily locking the owner out.
    # The computer the Stump runs on isn't counted at the door.
    keys = [who] if conn._local(guard._k(who)) else [who, "door:admin"]
    ok, wait = guard.password_many(lambda: _check_admin_pw_now(pw), keys, {"door:admin": ADMIN_LOCK_AFTER})
    return ok


ADMIN_LOCK_AFTER = 20


async def _profile_pw(nick, pw, ip):
    """(ok, wait_s) for a profile's password: the hashing doesn't hold up
    the Stump; all checks together are budgeted (guard.pw_budget); wrong
    ones lock the address AND the profile -- guesses from many devices
    lock the account too."""
    if not guard.pw_budget():
        return False, 2
    return await guard.password_async(lambda: profiles.check_async(nick, pw),
                                      [ip, "nick:" + profiles.key(nick or "")])


def _check_admin_pw_now(pw):
    """True if pw unlocks admin actions on this node -- the one real
    check, reused by every admin-gated route rather than each one
    duplicating it. Asks the "identity" service (stumpid, through
    plugins.py: fail-closed, falls back to fservbot's operator password
    -- see check_admin_password's own docstring). Without it, every
    admin route still exists and refuses.""" 
    ident = plugins.service_lookup("identity")
    if ident is None:
        if keepalive.mode() == "rescue":
            # A rescue start loads no plugins: the same passwords, read
            # where stumpid and fservbot read them (config.py), fail-closed.
            try:
                import config
                want = getattr(config, "AUTH_ADMIN_PASSWORD", "") or getattr(config, "FSERVBOT_OP_PASSWORD", "")
            except ImportError:
                want = ""
            return bool(want) and pw == want
        return False
    try:
        return bool(ident.check_admin_password(pw))
    except Exception as e:
        print("[barkeep] admin password check failed, refusing:", e)
        return False


# Admin sessions. Logging in with the password gives the browser a
# random session token, in a cookie for /admin and in the page's forms
# (instead of the password, which never comes back in a page). The login
# answers with a redirect, so the browser's history holds a plain
# GET /admin, never the form with the password: going Back after Log out
# shows the password prompt, not the admin page, and can't resend the
# password. Sessions end on Log out, after SESSION_IDLE_S without use,
# and at restart (they're only in memory).
try:
    from config import BILLBOARD_POSTS_PER_MIN    # per visitor (guard.py)
except ImportError:
    BILLBOARD_POSTS_PER_MIN = 3
POST_MAX, POST_PER_S = BILLBOARD_POSTS_PER_MIN, 60
SESSION_IDLE_S = 1800
SESSION_MAX = 8
_sessions = {}          # token -> last used (time.time())
ADMIN_COOKIE = "stump_admin"


def _new_session():
    import os, time, ubinascii
    now = time.time()
    for t in [t for t, seen in _sessions.items() if now - seen > SESSION_IDLE_S]:
        del _sessions[t]
    while len(_sessions) >= SESSION_MAX:
        del _sessions[min(_sessions, key=lambda t: _sessions[t])]
    tok = ubinascii.hexlify(os.urandom(16)).decode()
    _sessions[tok] = now
    return tok


def _session_ok(tok):
    import time
    if not tok or tok not in _sessions:
        return False
    now = time.time()
    if now - _sessions[tok] > SESSION_IDLE_S:
        del _sessions[tok]
        return False
    _sessions[tok] = now
    return True


def _end_session(tok):
    _sessions.pop(tok, None)


def _admin_ok(pw, who=None):
    """A form from an admin page: a live session token (what pages carry
    now), or the password itself."""
    return _session_ok(pw) or _check_admin_pw(pw, who)


VISITOR_COOKIE = "stump_v"       # a browser's visitor ID (a year)
PROFILE_COOKIE = "stump_p"       # a logged-in profile's session
_seen_visitors = {}


def _new_visitor_id():
    return "v" + profiles._rand_hex(10)


def _valid_visitor_id(v):
    return len(v) == 21 and v[0] == "v" and all(c in "0123456789abcdef" for c in v[1:])


def _visitor(writer, headers, ip, method, path=""):
    """The ID a request is known by (see the request handler)."""
    tok = _cookie(headers, PROFILE_COOKIE)
    if tok:
        nick = profiles.session_nick(tok)
        if nick:
            vid = "p:" + profiles.key(nick)
            guard.net(vid, ip)
            u = rrc._users.get(vid)
            if u is None or u.get("nick") != nick:
                rrc.touch_user(vid, nick=nick)
            return vid
    v = _cookie(headers, VISITOR_COOKIE)
    if v and _valid_visitor_id(v):
        guard.net(v, ip)
        if v not in _seen_visitors:
            _seen_visitors[v] = 1
            if len(_seen_visitors) > 500:
                _seen_visitors.clear()
            _move_in(ip, v)
        return v
    if method == "GET" and not path.startswith(("/rrc/", "/hello", "/node.json", "/hw.json", "/stumps.json",
                                                 "/files.json", "/billboard.json", "/tools.json")):
        # Offered with the page; it counts from the browser's next request
        # (a client that doesn't keep it stays known by its address). Not
        # made for data requests -- an app polling every 2 s never keeps
        # it, and only pages carry it.
        writer._stump_set_cookie = (VISITOR_COOKIE + "=" + _new_visitor_id()
                                    + "; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax")
    return ip


# The greeting (APP_HELLO in config.py, on by default): an app -- a client
# that keeps no cookie, like FireFly -- must say who it is before it chats:
# /auth (prove its identity, FireFly's one button), then /nick <its name>.
# Until then it reads nothing, writes nothing, and isn't listed anywhere.
# Why: known only by its Wi-Fi address, the same phone was one person on
# LoRa, another on Wi-Fi, a third on the next Stump of the chain; proven,
# it's one person everywhere (rrc_mesh.same_person), under the name it
# chose. Browsers keep the Stump's cookie and are guests, as before.
_HELLO_OK = ("/auth", "/nick", "/help", "/whoami")


def _greeting(identifier, ip):
    """None (not asked: a browser, the gate off, no identity plugin),
    "done", or "required"."""
    if identifier != ip:
        return None
    try:
        import config
        if not getattr(config, "APP_HELLO", True):
            return None
    except ImportError:
        pass
    sid = plugins.service_lookup("identity")
    if sid is None:
        return None
    try:
        verified = sid.is_verified(identifier)
    except Exception:
        verified = False
    if verified and rrc.nick_chosen(identifier):
        rrc.unhide(identifier)
        return "done"
    rrc.hide(identifier)
    return "required"


def _move_in(ip, vid):
    """A browser's first request with its visitor ID: what this address
    had (its nick, language, credits) moves to it, once -- unless the
    address is a verified FireFly session (the app, which stays on it)."""
    try:
        sid = plugins.service_lookup("identity")
        if sid is not None and sid.verified_identity_for(ip):
            return
    except Exception:
        pass
    old = rrc._users.get(ip)
    if old is not None and vid not in rrc._users:
        rrc.touch_user(vid, nick=None if old["nick"] == rrc.default_nick(ip) else old["nick"], room=old.get("room"))
        rrc.drop_user(ip)
    if ip in i18n._prefs and vid not in i18n._prefs:
        i18n.set_lang(vid, i18n._prefs[ip])
    fserv.move_credits(ip, vid)


def _render_profile_page(lang, identifier, notice=""):
    """/profile: a registered profile for a visitor without a FireFly --
    log in, create one, or (logged in) its balance, a new password, log
    out. Kept on this Stump and the Stumps of its chain."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    esc = billboard._esc
    note = ("<p class='sub'><b>" + esc(notice) + "</b></p>") if notice else ""
    head = "<h1>" + T("profile_title") + "</h1>" + _lang_switcher(lang, "/profile") + note
    if identifier.startswith("p:"):
        rec = profiles.get(identifier[2:])
        nick = rec["nick"] if rec else identifier[2:]
        bal = ("<p class='sub'>" + T("profile_balance", n=fserv.credit_balance(identifier)) + "</p>"
               if fserv.CREDITS_ENABLED else "")
        body = ("<div class='panel'><p>" + T("profile_logged_in", nick="<b>" + esc(nick) + "</b>") + "</p>" + bal
                + "<p class='sub'>" + T("profile_kept") + "</p>"
                "<form method='POST' action='/profile/logout' class='post-form'>"
                "<button type='submit'>" + T("profile_logout") + "</button></form></div>"
                "<details class='inner'><summary>" + T("profile_change") + "</summary>"
                "<form method='POST' action='/profile/password' class='post-form setup' autocomplete='off'>"
                "<label>" + T("profile_current") + "<br><input type='password' name='old' required autocomplete='current-password'></label>"
                "<label>" + T("profile_new") + "<br><input type='password' name='pw' required minlength='"
                + str(profiles.PW_MIN) + "' autocomplete='new-password'></label>"
                "<label>" + T("profile_again") + "<br><input type='password' name='pw2' required autocomplete='new-password'></label>"
                "<button type='submit'>" + T("profile_change_go") + "</button></form></details>")
    else:
        cur = rrc.get_user(identifier)["nick"]
        body = ("<p class='sub'>" + T("profile_intro") + "</p>"
                "<div class='panel'><h2 class='sub'>" + T("profile_login") + "</h2>"
                "<form method='POST' action='/profile/login' class='post-form setup' autocomplete='on'>"
                "<label>" + T("rrc_profile_nick") + "<br><input name='nick' required maxlength='"
                + str(profiles.NICK_MAX) + "' autocomplete='username'></label>"
                "<label>" + T("profile_password") + "<br><input type='password' name='pw' required autocomplete='current-password'></label>"
                "<button type='submit'>" + T("profile_login_go") + "</button></form></div>"
                "<div class='panel'><h2 class='sub'>" + T("profile_create") + "</h2>"
                "<form method='POST' action='/profile/register' class='post-form setup' autocomplete='off'>"
                "<label>" + T("rrc_profile_nick") + "<br><input name='nick' required maxlength='"
                + str(profiles.NICK_MAX) + "' value='" + esc("" if cur.startswith("guest-") else cur)
                + "' autocomplete='username'></label>"
                "<label>" + T("profile_password") + " " + T("profile_pw_rule", n=profiles.PW_MIN)
                + "<br><input type='password' name='pw' required minlength='" + str(profiles.PW_MIN)
                + "' autocomplete='new-password'></label>"
                "<label>" + T("profile_again") + "<br><input type='password' name='pw2' required autocomplete='new-password'></label>"
                "<p class='sub'><small>" + T("profile_no_email") + "</small></p>"
                "<button type='submit'>" + T("profile_create_go") + "</button></form></div>"
                "<p class='sub'><small>" + T("profile_firefly") + "</small></p>")
    return head + body + _nav(lang, *layout.nav())


def _profile_cookie(writer, tok):
    writer._stump_set_cookie = (PROFILE_COOKIE + "=" + tok + "; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
                                if tok else PROFILE_COOKIE + "=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")


def _log_in(writer, identifier, nick):
    """This browser now is that profile: its chat nick, the room it was in."""
    tok = profiles.start_session(nick)
    _profile_cookie(writer, tok)
    old = rrc._users.get(identifier)
    pid = "p:" + profiles.key(nick)
    rrc.touch_user(pid, nick=profiles.get(nick)["nick"], room=old.get("room") if old else None)
    if old is not None and identifier != pid:
        rrc.drop_user(identifier)
    if identifier in i18n._prefs:
        i18n.set_lang(pid, i18n._prefs[identifier])


def _cookie(headers, name):
    for part in headers.get("cookie", "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def _admin_logout_form(lang, tok):
    return ("<form method='POST' action='/admin/logout' class='logout' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(tok) + "'>"
            "<button type='submit'>" + i18n.t("admin_logout", lang) + "</button></form>")


def _render_admin_login_page(lang, error=False, wait=0):
    """The ONLY way to reach file deletion now -- no link, no nav tile,
    no button anywhere in the visible UI points here. Reachable only by
    typing the address directly, which is the point: the drawer this
    replaced sat on /files where every visitor saw it exist, even
    collapsed, and a wrong password there had to be re-typed once per
    file. This page asks for the password exactly once regardless of
    how many files get selected next.
    """
    msg = i18n.t("admin_locked", lang, s=wait) if wait else i18n.t("admin_wrong_password", lang)
    err = "<p class='sub' style='color:var(--ember-bright);'>" + msg + "</p>" if error else ""
    return (
        "<h1>" + i18n.t("admin_header", lang) + "</h1>"
        + err +
        "<form method='POST' action='/admin' class='row'>"
        "<input type='password' name='admin_pass' placeholder='"
        + i18n.t("files_admin_password", lang) + "' autofocus>"
        "<button type='submit'>" + i18n.t("admin_login_button", lang) + "</button>"
        "</form>"
    )


def _hw_brief(can_edit=False):
    """The radio for the chat: {"state", "title", "settings", "edit"} --
    the status dot, a line per radio, the settings in use, and whether
    this visitor may change them (_radio_editor)."""
    try:
        h = radio.hw()
        out = {"state": h["state"], "title": "\n".join(radio.hw_line(r) for r in h["radios"])}
        cur = radio.current()
        if cur and radio.chat_edit():
            out["settings"] = cur
            out["edit"] = bool(can_edit)
        return out
    except Exception as e:
        return {"state": "error", "title": str(e)}


_LOCAL = ("127.0.0.1", "::1", "localhost")


def _radio_editor(identifier, headers):
    """Who may change the radio from the chat: the person on the computer
    the Stump runs on, or someone logged in to /admin (its session
    cookie)."""
    if not radio.chat_edit():
        return False
    return guard._k(identifier) in _LOCAL or _session_ok(_cookie(headers, ADMIN_COOKIE))


def _release_line():
    """"Stump Beta A · 1.0.0-beta.3": the release this node runs (set at
    boot from example_node.py), so anyone can check it from a phone.
    Empty before boot sets it (tests, a bare import)."""
    info = rrc.NODE_INFO or {}
    rel = info.get("release")
    if not rel:
        return ""
    ver = info.get("version")
    return ("<p class='sub release'>Stump " + billboard._esc((ver + " · ") if ver else "")
            + billboard._esc(rel) + "</p>")


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
    files_part = ("<h2 class='sub'>" + i18n.t("admin_files_header", lang) + "</h2>"
                  "<p class='sub'>" + i18n.t("admin_select_intro", lang) + "</p>" + file_section
                  if features.enabled("files") else "")
    board_part = _render_admin_billboard_section(lang, pw) if features.enabled("billboard") else ""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    radio_cur = radio.current()
    hw = radio.hw()
    import chain
    ch = chain.status()
    on_air = (
        _card("radio", "\U0001f4fb", _render_admin_radio_section(lang, pw),
              _radio_peek(hw, radio_cur, lang))
        + _card("chain", "\U0001f517", _render_admin_chain_section(lang), _chain_peek(ch, lang))
        + _card("pn", "\U0001f4ec", _render_admin_pn_section(lang, pw), ""))
    content = (
        _card("files", "\U0001f4c1", files_part, T("admin_peek_files", n=len(names)))
        + _card("board", "\U0001f4cc", board_part, "")
        + _card("home", "\U0001f3e0", _render_admin_home_section(lang, pw), "")
        + _card("tools", "\U0001f9f0", _render_admin_tools_section(lang, pw), ""))
    look = _card("look", "\U0001f3a8", _render_admin_site_theme_section(lang, pw)
                 + _render_admin_layout_section(lang, pw) + _render_admin_settings_section(lang),
                 _theme_label(theme.admin_choice(), lang) if theme.admin_choice() else "")
    system = (
        _card("setup", "\u2699\ufe0f", _render_admin_setup_section(lang, pw), _setup_peek(lang))
        + _card("people", "\U0001f465", _render_admin_profiles_section(lang, pw),
                T("admin_profiles_peek", n=len(profiles.all_profiles())))
        + _card("plugins", "\U0001f9e9", _render_admin_plugins_section(lang, pw), _plugins_peek(lang))
        + _card("https", "\U0001f512", _render_admin_tls_line(lang), "")
        + _card("power", "\u23fb", _render_admin_power_section(lang, pw), ""))
    return (
        "<div class='admin-wide'>"
        "<div class='admin-head'><h1>" + T("admin_header") + "</h1>"
        + _admin_logout_form(lang, pw) + "</div>"
        + _release_line()
        + _render_admin_health(lang, pw)
        + _admin_tiles(lang, hw, radio_cur, ch, len(names))
        + _group(T("admin_group_air"), on_air)
        + _group(T("admin_group_content"), content)
        + _group(T("admin_group_look"), look)
        + _group(T("admin_group_system"), system)
        + _admin_logout_form(lang, pw)
        + "</div><script>" + _ADMIN_CARDS_SCRIPT + "</script>"
    )


# ── /admin's layout: tiles at the top, then cards that open ──────────
# Each section (its own renderer, unchanged) goes in a card: its first
# heading becomes the card's title, a short line beside it says how it
# stands, and the rest opens on a tap. The tiles at the top show the
# Stump at a glance and open their card.

def _card(cid, icon, body, peek="", open_=False):
    if not body:
        return ""
    title = ""
    if body.startswith("<h2 class='sub'>"):
        end = body.find("</h2>")
        title, body = body[16:end], body[end + 5:]
    return ("<details class='card' id='c-" + cid + "'" + (" open" if open_ else "") + "><summary>"
            "<span class='ic' aria-hidden='true'>" + icon + "</span><span class='tx'><span class='ct'>" + title + "</span>"
            + ("<span class='pk'>" + peek + "</span>" if peek else "")
            + "</span></summary><div class='cb'>" + body + "</div></details>")


def _group(label, cards):
    return ("<h2 class='grp'>" + label + "</h2>" + cards) if cards else ""


def _radio_peek(hw, cur, lang):
    if hw["state"] == "none":
        return i18n.t("admin_peek_noradio", lang)
    txt = i18n.t("rrc_hw_" + hw["state"], lang)
    if cur:
        txt += " · " + radio.hz_to_mhz(cur["frequency"]) + " MHz"
    return "<i class='dot " + hw["state"] + "'></i>" + txt


def _chain_peek(ch, lang):
    if not ch.get("enabled"):
        return ""
    n = len(ch.get("below") or [])
    role = i18n.t("admin_peek_root" if ch["role"] == "root" else "admin_peek_member", lang)
    return role + " · " + i18n.t("admin_peek_below", lang, n=n)


def _plugins_peek(lang):
    try:
        items = plugins.status()
        on = sum(1 for p in items if p.get("state") == "on")
        return i18n.t("admin_peek_plugins", lang, n=on, total=len(items))
    except Exception:
        return ""


def _admin_tiles(lang, hw, cur, ch, nfiles):
    """The Stump at a glance: radio, chain, people in the chat, files --
    each opens its card below."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    tiles = []
    if hw["state"] != "none":
        tiles.append(("radio", "<i class='dot big " + hw["state"] + "'></i>", T("rrc_hw_" + hw["state"]),
                      (radio.hz_to_mhz(cur["frequency"]) + " MHz") if cur else T("admin_tile_radio")))
    else:
        tiles.append(("radio", "\U0001f4fb", T("admin_peek_noradio"), T("admin_tile_radio")))
    if ch.get("enabled"):
        tiles.append(("chain", "\U0001f517", T("admin_peek_root" if ch["role"] == "root" else "admin_peek_member"),
                      T("admin_peek_below", n=len(ch.get("below") or []))))
    try:
        rrc._prune_users()
        people = len(rrc._users)
    except Exception:
        people = 0
    tiles.append(("", "\U0001f465", str(people), T("admin_tile_people")))
    if features.enabled("files"):
        tiles.append(("files", "\U0001f4c1", str(nfiles), T("admin_tile_files")))
    out = []
    for cid, icon, big, small in tiles:
        inner = ("<span class='ti'>" + icon + "</span><b>" + big + "</b><small>" + small + "</small>")
        out.append(("<a class='atile' href='#c-" + cid + "'>" + inner + "</a>") if cid
                   else "<div class='atile'>" + inner + "</div>")
    return "<div class='atiles'>" + "".join(out) + "</div>"


# Opens the card a tile (or #c-...) points to, and remembers which cards
# were open on this device (try/catch: storage may be off).
_ADMIN_CARDS_SCRIPT = """(function(){
function op(h){var d=h&&document.getElementById(h.slice(1));if(d&&d.tagName==='DETAILS'){d.open=true;d.scrollIntoView({block:'start'});}}
var K='stumpAdminOpen',s=[];try{s=JSON.parse(localStorage.getItem(K)||'[]');}catch(e){}
document.querySelectorAll('details.card').forEach(function(d){
 if(s.indexOf(d.id)>=0)d.open=true;
 d.addEventListener('toggle',function(){var o=[];document.querySelectorAll('details.card[open]').forEach(function(x){o.push(x.id);});
  try{localStorage.setItem(K,JSON.stringify(o));}catch(e){}});});
document.querySelectorAll('a.atile').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();op(a.getAttribute('href'));});});
op(location.hash);})();"""


def _theme_label(name, lang):
    """A theme's name for people (plain text: escape it for HTML)."""
    if name in theme.EXTRA:
        return theme.label(name, lang)
    return i18n.t("admin_theme_" + name, lang)


def _render_admin_site_theme_section(lang, pw):
    """The theme everyone gets, chosen here by an admin (theme.py saves it
    on the board's own memory): every built-in and pack theme, and "as
    set up", which goes back to config.py's THEME."""
    current = theme.admin_choice()
    opts = ["<label class='opt'><input type='radio' name='theme' value=''"
            + (" checked" if current is None else "") + "> "
            + billboard._esc(i18n.t("admin_site_theme_setup", lang,
                                    name=_theme_label(theme.configured_theme(), lang)))
            + "</label>"]
    for n in theme.THEMES:
        opts.append("<label class='opt'><input type='radio' name='theme' value='" + _attr_esc(n) + "'"
                    + (" checked" if current == n else "") + "> " + billboard._esc(_theme_label(n, lang)) + "</label>")
    return (
        "<h2 class='sub'>" + i18n.t("admin_site_theme_header", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("admin_site_theme_intro", lang) + "</p>"
        "<form method='POST' action='/admin/theme' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'>" + "".join(opts) + "</div>"
        "<button type='submit'>" + i18n.t("admin_site_theme_save", lang) + "</button>"
        "</form>"
    )


def _nav_items(lang):
    """[(key, label, icon)] for the navigation row, in layout order:
    the destinations this node offers."""
    out = []
    for k in layout.nav(all=True):
        if k not in _DESTINATIONS:
            continue
        feat = _NAV_FEATURE.get(k)
        if feat is not None and not features.enabled(feat):
            continue
        href, icon, label = _DESTINATIONS[k]
        out.append((k, i18n.t(label, lang), icon))
    return out


def _render_admin_layout_section(lang, pw):
    """The order of the navigation buttons and of the home page's tiles:
    drag them (or use the arrows, on a phone), then save. layout.py."""
    hide, show = i18n.t("admin_layout_hide", lang), i18n.t("admin_layout_show", lang)

    def lst(field, items):
        off = layout.hidden(field)
        return ("<ol class='reorder' data-field='" + field + "'>"
                + "".join("<li draggable='true' data-key='" + _attr_esc(k) + "'"
                          + (" class='off'" if k in off else "") + ">"
                          "<span class='grip' aria-hidden='true'>⠿</span>" + icon
                          + "<span class='rname'>" + billboard._esc(name) + "</span>"
                          "<button type='button' class='vis'>" + (show if k in off else hide) + "</button>"
                          "<button type='button' class='up' aria-label='↑'>▲</button>"
                          "<button type='button' class='dn' aria-label='↓'>▼</button></li>"
                          for k, name, icon in items)
                + "</ol>")
    tiles = []
    icons = {k: icon for k, f, h, icon, l, sub in _HOME_TILES}
    for k, name, html in home_tile_items(lang, all=True):
        tiles.append((k, name, icons.get(k, _ICON_PLUGIN)))
    return (
        "<h2 class='sub'>" + i18n.t("admin_layout_header", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("admin_layout_intro", lang) + "</p>"
        "<form method='POST' action='/admin/layout' autocomplete='off' id='layout-form'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<input type='hidden' name='nav' value=''><input type='hidden' name='tiles' value=''>"
        "<input type='hidden' name='nav_hidden' value=''><input type='hidden' name='tiles_hidden' value=''>"
        "<div class='panel'><b>" + i18n.t("admin_layout_nav", lang) + "</b>" + lst("nav", _nav_items(lang)) + "</div>"
        "<div class='panel'><b>" + i18n.t("admin_layout_tiles", lang) + "</b>" + lst("tiles", tiles) + "</div>"
        "<div class='row'><button type='submit'>" + i18n.t("admin_layout_save", lang) + "</button>"
        "<button type='submit' name='reset' value='1'>" + i18n.t("admin_layout_reset", lang) + "</button></div>"
        "</form><script>" + _LAYOUT_SCRIPT.replace("I18N_HIDE", json.dumps(hide)).replace("I18N_SHOW", json.dumps(show))
        + "</script>"
    )


# Drag and drop for the lists above, with arrows for touch screens
# (HTML5 drag and drop doesn't work on most phones). On submit, each
# list's order goes into its hidden field, comma-separated.
_LAYOUT_SCRIPT = """
(function(){
  var f = document.getElementById('layout-form'); if (!f) return;
  var drag = null;
  f.querySelectorAll('ol.reorder').forEach(function(ol){
    ol.addEventListener('dragstart', function(e){
      drag = e.target.closest('li'); if (!drag) return;
      drag.classList.add('dragging');
      try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', drag.dataset.key); } catch (x) {}
    });
    ol.addEventListener('dragend', function(){
      if (drag) drag.classList.remove('dragging'); drag = null;
      ol.querySelectorAll('.over').forEach(function(x){ x.classList.remove('over'); });
    });
    ol.addEventListener('dragover', function(e){
      if (!drag || drag.parentNode !== ol) return;
      e.preventDefault();
      var li = e.target.closest('li');
      ol.querySelectorAll('.over').forEach(function(x){ x.classList.remove('over'); });
      if (li && li !== drag) li.classList.add('over');
    });
    ol.addEventListener('drop', function(e){
      if (!drag || drag.parentNode !== ol) return;
      e.preventDefault();
      var li = e.target.closest('li');
      if (li && li !== drag) {
        var r = li.getBoundingClientRect();
        ol.insertBefore(drag, (e.clientY > r.top + r.height / 2) ? li.nextSibling : li);
      }
    });
    ol.addEventListener('click', function(e){
      var b = e.target.closest('button'); if (!b) return;
      var li = b.closest('li');
      if (b.classList.contains('up') && li.previousElementSibling) ol.insertBefore(li, li.previousElementSibling);
      if (b.classList.contains('dn') && li.nextElementSibling) ol.insertBefore(li.nextElementSibling, li);
      if (b.classList.contains('vis')) {
        li.classList.toggle('off');
        b.textContent = li.classList.contains('off') ? I18N_SHOW : I18N_HIDE;
      }
    });
  });
  f.addEventListener('submit', function(){
    f.querySelectorAll('ol.reorder').forEach(function(ol){
      var keys = [], off = [];
      ol.querySelectorAll('li').forEach(function(li){
        keys.push(li.dataset.key);
        if (li.classList.contains('off')) off.push(li.dataset.key);
      });
      f.elements[ol.dataset.field].value = keys.join(',');
      f.elements[ol.dataset.field + '_hidden'].value = off.join(',');
    });
  });
})();
"""


def _render_admin_tools_section(lang, pw):
    """The Tools page's files, by section, each with a box to tick: a
    whole section, or single files. Deleted from the SD card."""
    if not fserv.sd_ok:
        return ""
    out = "<h2 class='sub'>" + i18n.t("admin_tools_header", lang) + "</h2>"
    secs = fserv.tool_sections(lang)
    if not secs:
        return out + "<p class='sub'>" + i18n.t("admin_tools_none", lang) + "</p>"
    rows = []
    for sec in secs:
        d = sec["dir"]
        prefix = d + "/" if d else ""
        if d:
            rows.append("<li><label><input type='checkbox' name='d' value='" + _attr_esc(d) + "'> <b>"
                        + billboard._esc(sec["title"]) + "</b> — "
                        + i18n.t("admin_tools_whole", lang, n=len(sec["files"])) + "</label></li>")
        else:
            rows.append("<li><b>" + i18n.t("tools_other_h2", lang) + "</b></li>")
        for n, size in sec["files"]:
            rows.append("<li><label>&nbsp;&nbsp;&nbsp;<input type='checkbox' name='f' value='"
                        + _attr_esc(prefix + n) + "'> " + billboard._esc(n)
                        + "</label><small>" + _human_size(size) + "</small></li>")
    return out + (
        "<p class='sub'>" + i18n.t("admin_tools_intro", lang) + "</p>"
        "<form method='POST' action='/admin/tools' autocomplete='off' onsubmit=\"return confirm("
        + _attr_esc(json.dumps(i18n.t("admin_tools_confirm", lang))) + ")\">"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'><ul class='files'>" + "".join(rows) + "</ul></div>"
        "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
        "</form>"
    )


def _render_admin_tls_line(lang):
    """HTTPS status: on/off and why, and how long the certificate has
    left -- every node falls back to plain HTTP the day it expires."""
    import time
    st = tls.status()
    head = "<h2 class='sub'>HTTPS</h2>"
    if not st["active"]:
        why = i18n.t("admin_tls_why_" + (st.get("code") or "failed"), lang, detail=st.get("detail") or st["reason"])
        return head + "<p class='sub'>" + i18n.t("admin_tls_off", lang, reason=billboard._esc(why)) + "</p>"
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


def _render_admin_chain_section(lang):
    """A chain of Stumps over Wi-Fi (chain.py): this Stump's place in it.
    Shown only on a Stump in a chain."""
    import chain
    st = chain.status()
    if not st["enabled"]:
        return ""
    e = billboard._esc
    lines = []
    if st["role"] == "root":
        lines.append(i18n.t("admin_chain_root", lang, ip=st["ap_ip"],
                            channel=(" · Wi-Fi %d" % st["channel"]) if st["channel"] else ""))
    else:
        up = st["uplink"]
        if up["ip"]:
            heard = (i18n.t("admin_chain_heard", lang, s=up["parent_heard_s"])
                     if up["parent_heard_s"] is not None else "")
            lines.append(i18n.t("admin_chain_member", lang, ssid=e(up["ssid"]), ip=up["ip"],
                                parent=up["parent"], heard=heard))
        else:
            lines.append(i18n.t("admin_chain_trying", lang, ssid=e(up["ssid"])))
    lines.append(i18n.t("admin_chain_below", lang, list=", ".join(st["below"])) if st["below"]
                 else i18n.t("admin_chain_none_below", lang))
    if not st["secret"]:
        lines.append("<b>" + i18n.t("admin_chain_nosecret", lang) + "</b>")
    return ("<h2 class='sub'>" + i18n.t("admin_chain_header", lang) + "</h2>"
            "<p class='sub'>" + "<br>".join(lines) + "</p>")


def _render_admin_plugins_section(lang, pw):
    """Plugins (plugins.py): what's installed, running or off, the RAM
    each took to start, the last error, and an on/off switch saved
    across restarts. Also says when the last boot went into safe mode."""
    items = plugins.status()
    out = "<h2 class='sub'>" + i18n.t("admin_plugins_header", lang) + "</h2>"
    safe = plugins.safe_mode()
    if safe["culprit"]:
        out += "<p class='sub'><b>" + i18n.t("admin_plugins_culprit", lang, name=billboard._esc(safe["culprit"])) + "</b></p>"
    elif safe["on"]:
        out += "<p class='sub'><b>" + i18n.t("admin_plugins_safe", lang) + "</b></p>"
    if not items:
        return out + "<p class='sub'>" + i18n.t("plugin_none", lang) + "</p>"
    hid = "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
    rows = []
    for p in items:
        on = p["state"] == "on"
        meta = []
        if p["version"]:
            meta.append("v" + billboard._esc(str(p["version"])))
        if p["mem"] is not None:
            meta.append(_human_size(p["mem"]) + " RAM")
        line = ("<div><span class='pname'>" + billboard._esc(p["name"]) + "</span> "
                "<span class='pstate'>— " + i18n.t("admin_plugins_state_" + p["state"].replace("-", "_"), lang)
                + "</span>"
                + ("<span class='pmeta'>" + " · ".join(meta) + "</span>" if meta else "")
                + ("<span class='pcmds'>" + " ".join("/" + c for c in p["commands"]) + "</span>"
                   if p["commands"] else "")
                + ("<span class='perr'>" + billboard._esc(p["error"]) + "</span>" if p["error"] else "")
                + "</div>")
        button = ""
        if not (on and p["name"] in plugins.PROTECTED):
            button = ("<form method='POST' action='/admin/plugins' autocomplete='off'>" + hid +
                      "<input type='hidden' name='name' value='" + _attr_esc(p["name"]) + "'>"
                      "<input type='hidden' name='action' value='" + ("off" if on else "on") + "'>"
                      "<button type='submit'>" + i18n.t("admin_plugins_off" if on else "admin_plugins_on", lang)
                      + "</button></form>")
        rows.append("<li>" + line + button + "</li>")
    return out + "<div class='panel'><ul class='plist'>" + "".join(rows) + "</ul></div>"


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


_HW_LIVE_SCRIPT = """<script>(function(){
var L=HW_LABELS, box=document.getElementById('hwlive');
function row(k,v){ return v===null||v===undefined||v===''?'':'<tr><th>'+k+'</th><td>'+v+'</td></tr>'; }
function esc(t){ return String(t).replace(/[&<>"']/g,function(c){return '&#'+c.charCodeAt(0)+';';}); }
function ago(t){ if(!t) return null; var s=Math.max(0,Math.round(Date.now()/1000-t)); return s+' s'; }
function draw(h){
  if(!h.radios.length){ box.innerHTML='<p class="sub">'+esc(L.none)+'</p>'; return; }
  box.innerHTML=h.radios.map(function(r){
    var set=r.settings||{}, echo=r.radio||{};
    var mhz=function(hz){ return hz? (hz/1e6).toFixed(3)+' MHz':null; };
    return '<table class="hwt"><caption><span class="hw-dot '+esc(r.state)+'"></span> '+esc(r.name)+' — '+esc(L[r.state]||r.state)+'</caption>'
      +row(L.where, esc(r.where||'')+(r.device?' · '+esc(r.device):''))
      +row('RNode', r.fw? esc(r.fw)+(r.platform?' · '+esc(r.platform):'') : null)
      +row(L.error, r.state!=='online'&&r.error? esc(r.error):null)
      +row(L.set, set.frequency? mhz(set.frequency)+' · '+(set.bandwidth/1000)+' kHz · SF'+set.sf+' · CR4/'+set.cr+' · '+set.txpower+' dBm':null)
      +row(L.echo, echo.frequency? mhz(echo.frequency)+(echo.on===1?' · '+L.on:''):null)
      +row('RSSI · SNR', r.rssi!==null? r.rssi+' dBm · '+r.snr+' dB':null)
      +row(L.noise, r.noise!==null? r.noise+' dBm':null)
      +row(L.load, r.channel_load!==null? r.channel_load+' % · '+L.airtime+' '+r.airtime+' %':null)
      +row(L.battery, r.battery!==null? r.battery+' %':null)
      +row(L.temp, r.temp!==null? r.temp+' °C':null)
      +row(L.counts, r.rx!==null||r.tx!==null? (r.rx||0)+' / '+(r.tx||0):null)
      +row(L.last, ago(r.last_rx))
      +row(L.since, ago(r.since))
      +'</table>';
  }).join('');
}
function tick(){
  fetch('/hw.json',{cache:'no-store'}).then(function(r){return r.json();}).then(draw)
    .catch(function(){}).then(function(){ setTimeout(tick, 2000); });
}
tick();
})();</script>"""


def _render_admin_radio_live(lang, heading=True):
    """The radio, live: whether the RNode answered, its firmware, the
    settings it echoed back, signal of the last packet, noise floor,
    channel load, battery -- refreshed every 2 s from /hw.json."""
    if not radio.bridges():
        return ""
    labels = {k: i18n.t("admin_hw_" + k, lang) for k in (
        "title", "none", "where", "error", "set", "echo", "on", "noise", "load", "airtime",
        "battery", "temp", "counts", "last", "since")}
    labels.update({k: i18n.t("rrc_hw_" + k, lang) for k in ("online", "connecting", "error")})
    return (("<h2 class='sub'>" + labels["title"] + "</h2>" if heading else "")
            + "<style>.hwt{border-collapse:collapse;margin:6px 0 14px;font-size:.85rem}"
            ".hwt caption{text-align:left;font-weight:bold;padding:4px 0}"
            ".hwt th{text-align:left;color:var(--muted);font-weight:normal;padding:2px 12px 2px 0;white-space:nowrap}"
            ".hwt td{padding:2px 0}.hw-dot{display:inline-block;width:10px;height:10px;border-radius:50%;"
            "background:#f2efe8;border:1px solid var(--border)}.hw-dot.online{background:#3fb950}"
            ".hw-dot.connecting{background:#f0a030}.hw-dot.error{background:#e5534b}</style>"
            "<div id='hwlive'><p class='sub'>…</p></div>"
            + _HW_LIVE_SCRIPT.replace("HW_LABELS", json.dumps(labels)))


def _host():
    """The computer this Stump runs on (pc_compat's stump_host), or None on
    a board."""
    try:
        import stump_host
        return stump_host if getattr(stump_host, "IS_PC", False) else None
    except ImportError:
        return None


def _render_admin_power_section(lang, pw):
    """On a computer: turn this Stump off (it runs in the background, from
    the login service). Started again from the Start file in its folder,
    or at the next login."""
    h = _host()
    restart = ("<p class='sub'>" + i18n.t("admin_restart_intro", lang) + "</p>" + _render_admin_restart_form(lang, pw))
    if h is None:
        return "<h2 class='sub'>" + i18n.t("admin_power_header_cam", lang) + "</h2>" + restart
    where = billboard._esc(getattr(h, "START_FILE", "Start Stump"))
    return ("<h2 class='sub'>" + i18n.t("admin_power_header", lang) + "</h2>" + restart +
            "<form method='POST' action='/admin/poweroff' class='post-form' onsubmit=\"return confirm("
            + billboard._esc(json.dumps(i18n.t("admin_power_confirm", lang))) + ")\">"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<p class='sub'>" + i18n.t("admin_power_intro", lang, start=where) + "</p>"
            "<button type='submit'>" + i18n.t("admin_power_off", lang) + "</button></form>")


def _setup_peek(lang):
    s = stump_settings.current()
    bits = [billboard._esc(s["NODE_NAME"]), i18n.t("admin_setup_" + stump_settings.economy(s), lang).split(" — ")[0]]
    if stump_settings.has_access_modes():
        bits.append(i18n.t("admin_setup_" + s["AUTH_MODE"], lang).split(" — ")[0])
    return " · ".join(bits)


_FEATURE_LABELS = {"chat": "nav_chat", "billboard": "nav_board", "files": "nav_files", "about": "nav_about"}


def _render_admin_setup_section(lang, pw, notice=""):
    """The Provisioner's answers, changed in place (settings.py): name and
    bot, the file-sharing economy, who may enter, the features. Folded
    groups, one Save; what waits for a restart says so, with the button."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    S = stump_settings
    s = S.current()
    esc = billboard._esc
    eco = S.economy(s)

    def radio_row(name, value, checked, label):
        return ("<label class='opt'><input type='radio' name='" + name + "' value='" + value + "'"
                + (" checked" if checked else "") + "> " + label + "</label>")

    names = ("<details class='inner'><summary>" + T("admin_setup_names") + " <span class='pk'>" + esc(s["NODE_NAME"])
             + " · " + esc(s["BOT_NAME"]) + "</span></summary>"
             "<label>" + T("admin_setup_node") + "<br><input name='node_name' maxlength='" + str(S.NAME_MAX)
             + "' required value='" + esc(s["NODE_NAME"]) + "'></label>"
             "<p class='sub'><small>" + T("admin_setup_node_note") + "</small></p>"
             "<label>" + T("admin_setup_bot") + "<br><input name='bot_name' maxlength='" + str(S.BOT_MAX)
             + "' required value='" + esc(s["BOT_NAME"]) + "'></label>"
             "<label>" + T("admin_setup_greeting", n=S.GREETING_MAX) + "<br><textarea name='greeting' rows='3' maxlength='"
             + str(S.GREETING_MAX) + "'>" + esc(s["MESH_GREETING"]) + "</textarea></label></details>")
    w = s["CREDIT_WEIGHTS"]
    files = ("<details class='inner'><summary>" + T("admin_setup_files") + " <span class='pk'>"
             + T("admin_setup_" + eco).split(" — ")[0] + "</span></summary>"
             + radio_row("economy", "free", eco == "free", T("admin_setup_free"))
             + radio_row("economy", "standard", eco == "standard", T("admin_setup_standard"))
             + radio_row("economy", "custom", eco == "custom", T("admin_setup_custom"))
             + "<div class='weights' id='setupw'><p class='sub'><small>" + T("admin_setup_weights") + "</small></p>"
             + "".join("<label>" + T("admin_setup_w_" + k) + "<br><input name='w_" + k + "' type='number' min='0' max='"
                       + str(S.WEIGHT_MAX) + "' value='" + str(w.get(k, 1)) + "'></label>" for k in S.WEIGHT_CLASSES)
             + "</div></details>")
    access = ""
    if S.has_access_modes():
        access = ("<details class='inner'><summary>" + T("admin_setup_access") + " <span class='pk'>"
                  + T("admin_setup_" + s["AUTH_MODE"]).split(" — ")[0] + "</span></summary>"
                  + "".join(radio_row("auth_mode", m, s["AUTH_MODE"] == m, T("admin_setup_" + m)) for m in S.MODES)
                  + "</details>")
    on = s["FEATURES"]
    feats = ("<details class='inner'><summary>" + T("admin_setup_features") + " <span class='pk'>"
             + (" · ".join(T(_FEATURE_LABELS[f]) for f in on) or T("admin_setup_f_none")) + "</span></summary>"
             + "".join("<label class='opt'><input type='checkbox' name='f_" + f + "' value='1'"
                       + (" checked" if f in on else "") + "> " + T(_FEATURE_LABELS[f]) + "</label>" for f in S.FEATURES)
             + "<label class='opt'><input type='checkbox' name='librarian' value='1'"
             + (" checked" if s["LIBRARIAN"] else "") + "> " + T("admin_setup_librarian", bot=esc(s["BOT_NAME"])) + "</label>"
             + "</details>")
    store = ""
    st = S.storage()
    if st is not None:
        # A computer's "card" (the sd/ folder): its size, what's in it, the disk.
        share = int(st["mb"] * fserv.MAX_SD_RATIO) * S.MB
        store = ("<details class='inner'><summary>" + T("admin_setup_storage") + " <span class='pk'>"
                 + _human_size(st["mb"] * S.MB) + "</span></summary>"
                 "<label>" + T("admin_setup_storage_mb") + "<br><input name='storage_mb' type='number' min='"
                 + str(S.STORAGE_MIN_MB) + "' max='" + str(st["max_mb"]) + "' step='256' value='" + str(st["mb"]) + "'></label>"
                 "<p class='sub'><small>" + T("admin_setup_storage_note", share=_human_size(share),
                                              used=_human_size(st["used"]), free=_human_size(st["disk_free"]),
                                              pct=int(fserv.MAX_SD_RATIO * 100)) + "</small></p>"
                 "</details>")
    return ("<h2 class='sub'>" + T("admin_setup_header") + "</h2>" + notice
            + "<p class='sub'>" + T("admin_setup_intro") + "</p>"
            "<form method='POST' action='/admin/setup' class='post-form setup' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + esc(pw) + "'>"
            + names + files + store + access + feats
            + "<button type='submit'>" + T("admin_setup_save") + "</button></form>"
            "<script>(function(){var w=document.getElementById('setupw');function f(){"
            "var c=document.querySelector(\"input[name=economy]:checked\");var on=c&&c.value==='custom';"
            "w.style.opacity=on?'':'.5';w.querySelectorAll('input').forEach(function(i){i.disabled=!on;});}"
            "document.querySelectorAll('input[name=economy]').forEach(function(r){r.onchange=f;});f();})();</script>")


def _render_admin_profiles_section(lang, pw):
    """The registered profiles (profiles.py), shared by the chain: each
    one's balance; a new password (there's no email to reset it by), or
    removing it (on every Stump of the chain)."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    esc = billboard._esc
    rows = []
    for r in profiles.all_profiles():
        n = r["nick"]
        rows.append("<details class='inner'><summary>" + esc(n) + " <span class='pk'>"
                    + T("profile_balance", n=profiles.balance(n)) + "</span></summary>"
                    "<form method='POST' action='/admin/profiles' class='post-form setup' autocomplete='off'>"
                    "<input type='hidden' name='admin_pass' value='" + esc(pw) + "'>"
                    "<input type='hidden' name='nick' value='" + esc(n) + "'>"
                    "<label>" + T("admin_profiles_newpw", n=profiles.PW_MIN) + "<br><input type='text' name='pw' minlength='"
                    + str(profiles.PW_MIN) + "' autocomplete='off'></label>"
                    "<button type='submit' name='action' value='reset'>" + T("admin_profiles_reset") + "</button> "
                    "<button type='submit' name='action' value='delete' onclick=\"return confirm("
                    + esc(json.dumps(T("admin_profiles_delete_confirm", nick=n))) + ")\">"
                    + T("admin_profiles_delete") + "</button></form></details>")
    return ("<h2 class='sub'>" + T("admin_profiles_header") + "</h2>"
            "<p class='sub'>" + T("admin_profiles_intro") + "</p>"
            + ("".join(rows) if rows else "<p class='sub'>" + T("admin_profiles_none") + "</p>"))


def _render_admin_restart_form(lang, pw):
    return ("<form method='POST' action='/admin/restart' class='post-form' onsubmit=\"return confirm("
            + billboard._esc(json.dumps(i18n.t("admin_setup_restart_confirm", lang))) + ")\">"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<button type='submit'>" + i18n.t("admin_setup_restart", lang) + "</button></form>")


async def _restart_soon():
    """After the answer has gone out: start the Stump again (from a rescue
    start: a normal start, which is what restarting it is asked for)."""
    import uasyncio as asyncio
    await asyncio.sleep(1)
    if keepalive.mode() == "rescue":
        keepalive.try_normal()          # (restarts it)
        return
    stump_settings.restart()


def _render_admin_health(lang, pw):
    """At the top of /admin: a rescue start (what failed, when a normal
    start is tried again, the button to try it now), or a note that the
    watchdog restarted the Stump. Nothing when all is well."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    st = keepalive.status()
    esc = billboard._esc
    if st["mode"] == "rescue":
        return ("<div class='panel rescue'><h2 class='sub'>" + T("admin_rescue_header") + "</h2>"
                "<p class='sub'>" + T("admin_rescue_text", n=st["failed_starts"],
                                      why=esc(str(st["last_failure"] or "?")),
                                      min=(st["retry_in_s"] or 0) // 60 + 1) + "</p>"
                + _render_admin_restart_form(lang, pw).replace(
                    T("admin_setup_restart") + "</button>", T("admin_rescue_try") + "</button>")
                + "</div>")
    if st["cause"] == "watchdog":
        return "<p class='sub'><b>" + T("admin_watchdog_note") + "</b></p>"
    return ""


async def _power_off_soon():
    """After the answer has gone out: turn the Stump off."""
    import uasyncio as asyncio
    await asyncio.sleep(1)
    _host().power_off()


def _render_admin_bt(lang, pw=""):
    """How the Heltec is reached, folded ("Transport: USB"): the USB /
    Bluetooth switch, "Reconnect the radio", and its Bluetooth from /admin,
    no terminal: on, off, and pairing mode with the PIN shown here (polled
    from /admin/bt.json, never in the public status)."""
    T = lambda k: i18n.t(k, lang)
    btn = lambda a, k: "<button type='button' data-bt='" + a + "'>" + T(k) + "</button>"
    now = radio.link()
    seg = ""
    if now:
        # A Heltec on this computer: USB or Bluetooth, switched live.
        seg = ("<div class='seg' role='group' aria-label='" + T("admin_link_title") + "'>"
               + "".join("<button type='button' data-link='" + k + "' aria-pressed='" + ("true" if now == k else "false")
                         + "'" + (" class='on'" if now == k else "") + ">" + lbl + "</button>"
                         for k, lbl in (("usb", "USB"), ("ble", "Bluetooth")))
               + "</div><p class='sub'>" + T("admin_link_" + now) + "</p>")
    how = {"usb": "USB", "ble": "Bluetooth"}.get(now) or "Wi-Fi"
    retry = ("<form method='POST' action='/admin/radioretry' class='post-form inline'>"
             "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
             "<p class='sub'>" + T("admin_radio_retry_intro") + "</p>"
             "<button type='submit'>" + T("admin_radio_retry") + "</button></form>")
    return ("<details class='inner' id='bt'" + (" open" if now == "ble" else "") + "><summary>" + T("admin_transport")
            + " <span class='pk'>" + how + "</span></summary>"
            + seg + retry +
            "<p class='sub'>" + T("admin_bt_intro") + "</p>"
            "<div class='btrow'>" + (btn("computer", "admin_bt_computer") if now == "usb" else "")
            + (btn("pair", "admin_bt_pair") if now != "ble" else "") + btn("on", "admin_bt_on") + btn("off", "admin_bt_off")
            + btn("unpair", "admin_bt_unpair")
            + "</div>" + ("<p class='sub'>" + T("admin_bt_ble_note") + "</p>" if now == "ble" else "")
            + "<p class='sub' id='btmsg' aria-live='polite'></p><div id='btpin' hidden></div>"
            "<p class='sub' id='btprobe'></p>"
            "<details class='tips'><summary>" + T("admin_bt_help") + "</summary><p class='sub'>" + T("admin_bt_tips")
            + "</p><p class='sub'><small>" + T("admin_bt_heard") + " <code id='btheard'>\u2014</code></small></p></details></details>"
            "<script>" + _BT_SCRIPT.replace("BT_LABELS", json.dumps({
                "pairing": T("admin_bt_pairing"), "pin": T("admin_bt_pin"), "gone": T("admin_bt_gone"),
                "switching": T("admin_link_switching"), "computer": T("admin_bt_computer_go"),
                "searching": T("admin_bt_probe_searching"), "connected": T("admin_bt_probe_connected"),
                "paired": T("admin_bt_probe_paired"), "left": T("admin_bt_left"),
                "unpair": T("admin_bt_unpair_confirm")})) + "</script>")


_BT_SCRIPT = """(function(){
var L=BT_LABELS, msg=document.getElementById('btmsg'), pin=document.getElementById('btpin'),
    probe=document.getElementById('btprobe'), heard=document.getElementById('btheard'), timer=null, switched=false;
function link(k){ msg.textContent=L.switching;
  return fetch('/admin/radiolink.json',{method:'POST',body:'link='+k}).then(function(r){return r.json();})
   .then(function(d){ msg.textContent=d.message||''; if(d.ok) setTimeout(function(){ location.reload(); },2500); })
   .catch(function(){ msg.textContent='\\u2014'; }); }
function poll(t0, base){ var left=Math.max(0, 35-Math.round((Date.now()-t0)/1000));
  fetch('/admin/bt.json').then(function(r){return r.json();}).then(function(d){
  if(d.pin){ pin.hidden=false; pin.innerHTML='<small></small><b></b>'; pin.firstChild.textContent=L.pin; pin.lastChild.textContent=d.pin; }
  var p = d.probe==='paired' ? L.paired : d.probe==='connected' ? L.connected : d.probe==='searching' ? L.searching : (d.probe||'');
  probe.textContent=p;
  if(d.heard&&d.heard.length) heard.textContent=d.heard.join(' ');
  if(d.probe==='paired'&&!switched){ switched=true; clearInterval(timer); link('ble'); return; }
  msg.textContent = left>0 ? base+' '+L.left.replace('{s}',left) : L.gone;
  if(Date.now()-t0>60000){ clearInterval(timer); }
 }).catch(function(){}); }
function watch(base){ var t0=Date.now(); clearInterval(timer); timer=setInterval(function(){poll(t0, base);},1000); }
document.querySelectorAll('#bt [data-link]').forEach(function(b){ b.onclick=function(){
  if(!b.classList.contains('on')) link(b.getAttribute('data-link')); }; });
document.querySelectorAll('#bt [data-bt]').forEach(function(b){ b.onclick=function(){
  var a=b.getAttribute('data-bt'); if(a==='unpair'&&!confirm(L.unpair)) return;
  msg.textContent='\\u2026'; pin.hidden=true; probe.textContent='';
  fetch('/admin/bluetooth.json',{method:'POST',body:'action='+a}).then(function(r){return r.json();}).then(function(d){
    msg.textContent=d.message||'';
    if((a==='pair'||a==='computer')&&d.ok){ watch(a==='computer'?L.computer:L.pairing); }
  }).catch(function(){ msg.textContent='\\u2014'; }); }; });
})();"""


def _admin_or_local(identifier, headers):
    """The person on the computer the Stump runs on, or an /admin session."""
    return guard._k(identifier) in _LOCAL or _session_ok(_cookie(headers, ADMIN_COOKIE))


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

    settings = (
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
        "</form>")
    return (
        head
        + _render_admin_radio_live(lang, heading=False) +
        "<details class='inner'><summary>" + i18n.t("admin_radio_settings", lang) + " <span class='pk'>"
        + radio.hz_to_mhz(cur["frequency"]) + " MHz · " + ("%g" % (cur["bandwidth"] / 1000)) + " kHz · SF"
        + str(cur["sf"]) + " · CR4/" + str(cur["cr"]) + " · " + str(cur["txpower"]) + " dBm</span></summary>"
        + settings + "</details>"
        + _render_admin_bt(lang, pw) +
        "<form method='POST' action='/admin/radiochat' class='post-form'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<input type='hidden' name='on' value='" + ("0" if radio.chat_edit() else "1") + "'>"
        "<p class='sub'>" + i18n.t("admin_radio_chat_on" if radio.chat_edit() else "admin_radio_chat_off", lang) + "</p>"
        "<button type='submit'>" + i18n.t("admin_radio_chat_turn_off" if radio.chat_edit() else "admin_radio_chat_turn_on", lang)
        + "</button></form>"
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
        "<p class='sub'>" + i18n.t("admin_theme_browser_intro", lang) + "</p>"
        "<div class='panel'>"
        "<div class='row'>"
        "<button type='button' onclick=\"stumpSetTheme('site')\">" + i18n.t("admin_theme_site", lang)
        + " (" + (billboard._esc(theme.label(theme.site_theme(), lang)) if theme.site_theme() in theme.EXTRA
                  else i18n.t("admin_theme_" + theme.site_theme(), lang)) + ")</button>"
        "<button type='button' onclick=\"stumpSetTheme('amber')\">" + i18n.t("admin_theme_amber", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('phosphor')\">" + i18n.t("admin_theme_phosphor", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('oled')\">" + i18n.t("admin_theme_oled", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('paper')\">" + i18n.t("admin_theme_paper", lang) + "</button>"
        + "".join("<button type='button' onclick=\"stumpSetTheme('" + n + "')\">"
                  + billboard._esc(theme.label(n, lang)) + "</button>" for n in sorted(theme.EXTRA)) +
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
        ).replace("STUMP_SITE_THEME", json.dumps(theme.site_theme())
        ).replace("STUMP_SITE_STAMP", json.dumps(theme.site_stamp())) + "</script>"
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
      localStorage.setItem('stump_theme_stamp', STUMP_SITE_STAMP);
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
    localStorage.setItem('stump_theme_stamp', STUMP_SITE_STAMP);
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
        shelf = fserv.query_files(None, None, "newest", 0)
        if not shelf:
            rows = "<p class='sub'>" + i18n.t("files_none_yet", lang) + "</p>"
        else:
            items = []
            for f in shelf:
                n = f["name"]
                cls = fserv.guess_class(n)
                size = _human_size(f["size"]) if f["size"] else ""
                meta = cls + (", " + size if size else "")
                if fserv.CREDITS_ENABLED:
                    meta += " &middot; %d %s" % (fserv.credit_cost(n), i18n.t("files_credit_suffix", lang))
                # data-*: the sort and filter bar works on these, in the browser.
                items.append(
                    "<li data-name='" + billboard._esc(n) + "' data-size='%d' data-mtime='%d' data-cat='%s'>"
                    % (f["size"], f["mtime"], f["cat"])
                    + "<a href='/download?f=" + _url_encode(n) + "'>"
                    + _ICON_DOWNLOAD + "<span>" + billboard._esc(n) + "</span>"
                    "</a><small>" + meta + "</small></li>"
                )
            rows = ("<ul class='files'>" + "".join(items) + "</ul>"
                    "<p id='fnone' class='sub' style='display:none'>" + i18n.t("filter_none", lang) + "</p>")

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

    finder = ""
    if fserv.sd_ok:
        sorts = "".join("<option value='%s'>%s</option>" % (k, i18n.t("sort_" + k, lang))
                        for k in ("newest", "oldest", "name", "small", "large"))
        pills = "".join("<button type='button' data-cat='%s'%s>%s</button>"
                        % (c, " class='on'" if not c else "", i18n.t("cat_" + (c or "all"), lang))
                        for c in ("", "video", "music", "document", "image", "other"))
        finder = (
            "<div class='finder'><input id='flt' type='search' placeholder='"
            + billboard._esc(i18n.t("filter_placeholder", lang, bot=BOT_NAME)) + "' aria-label='"
            + billboard._esc(i18n.t("filter_placeholder", lang, bot=BOT_NAME)) + "'>"
            "<span class='sortbox' title='" + billboard._esc(i18n.t("sort_label", lang)) + "'>"
            "<select id='srt' aria-label='" + billboard._esc(i18n.t("sort_label", lang)) + "'>" + sorts + "</select></span>"
            "<div class='pills'>" + pills + "</div></div>"
        )
    return (
        "<h1>" + i18n.t("nav_files", lang) + "</h1>"
        + _lang_switcher(lang, "/files") +
        (_ask_drawer(lang) if features.enabled("files") else "") +
        "<p class='sub'>" + i18n.t("files_tap_to_download", lang) + "</p>"
        "<p class='sub'><a href='/profile'>\U0001f464 " + i18n.t("profile_link", lang) + "</a></p>"
        + finder +
        "<div class='panel' id='file-list'>" + rows + "</div>"
        + (_FILES_FINDER_SCRIPT if finder else "")
        + upload
        + _nav_except(lang, "files")
    )


# The Files page's sort and filter bar: in the browser, on the rows'
# data-* attributes -- no request to the node. Enter in the filter box
# asks BarKeep instead (a whole question, not a filter).
_FILES_FINDER_SCRIPT = """<script>
(function(){
  var list=document.getElementById('file-list'), flt=document.getElementById('flt'),
      srt=document.getElementById('srt'), cat='';
  function norm(t){ t=String(t||'').toLowerCase(); return t.normalize ? t.normalize('NFD').replace(/[\u0300-\u036f]/g,'') : t; }
  function apply(){
    var ul=list.querySelector('ul.files'), none=document.getElementById('fnone');
    if(!ul) return;
    var q=norm(flt.value).trim(), how=srt.value, shown=0, items=[].slice.call(ul.children);
    items.sort(function(a,b){
      var A=a.dataset, B=b.dataset;
      if(how==='name') return norm(A.name).localeCompare(norm(B.name));
      if(how==='small') return A.size-B.size;
      if(how==='large') return B.size-A.size;
      if(how==='oldest') return A.mtime-B.mtime;
      return B.mtime-A.mtime;
    });
    items.forEach(function(li){
      ul.appendChild(li);
      var ok=(!cat || li.dataset.cat===cat) && (!q || norm(li.dataset.name).indexOf(q)>=0);
      li.style.display=ok ? '' : 'none';
      if(ok) shown++;
    });
    if(none) none.style.display=shown ? 'none' : '';
  }
  window.applyFinder=apply;
  flt.addEventListener('input', apply);
  flt.addEventListener('keydown', function(e){ if(e.key==='Enter' && flt.value.trim() && window.askBK) askBK(flt.value); });
  srt.onchange=apply;
  [].forEach.call(document.querySelectorAll('.pills button'), function(b){
    b.onclick=function(){
      cat=b.getAttribute('data-cat');
      [].forEach.call(document.querySelectorAll('.pills button'), function(o){ o.className = o===b ? 'on' : ''; });
      apply();
    };
  });
  apply();
})();
</script>"""


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
  // The name goes percent-encoded: a header carries only Latin-1, so a raw
  // "thé.jpg" reached the node as one undecodable byte, and a name outside
  // Latin-1 ("日本.jpg", emoji) made the browser refuse to send at all.
  fetch('/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name),
    'X-Filename-Encoding':'url','X-Hash':hash},body:file})
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
    if(window.applyFinder) applyFinder();
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
                + _ICON_DOWNLOAD + "<span>" + billboard._esc(a["name"]) + "</span></a>"
                "<small>" + _human_size(a["size"]) + " · " + i18n.t(title, lang) + "</small></li>"
            )
    # One section per folder of tools/ (fserv.tool_sections): its title
    # and summary from its README.md, its files, and the README itself
    # folded underneath. The FireFly apps are listed above instead.
    sections = []
    for sec in fserv.tool_sections(lang):
        files = [(n, size) for n, size in sec["files"] if not fserv.is_app_file(n)]
        if not files:
            continue
        prefix = sec["dir"] + "/" if sec["dir"] else ""
        items = "".join(
            "<li><a href='/tool?f=" + _url_encode(prefix + n) + "'>"
            + _ICON_DOWNLOAD + "<span>" + billboard._esc(n) + "</span>"
            "</a><small>" + _human_size(size) + "</small></li>"
            for n, size in files)
        head = billboard._esc(sec["title"]) if sec["dir"] else i18n.t("tools_other_h2", lang)
        readme = ""
        if sec["readme"]:
            readme = ("<details class='readme'><summary>" + i18n.t("tools_readme", lang)
                      + "</summary><pre>" + billboard._esc(sec["readme"]) + "</pre></details>")
        sections.append(
            "<h2>" + head + "</h2>"
            + ("<p class='sub'>" + billboard._esc(sec["summary"]) + "</p>" if sec["summary"] else "")
            + "<div class='panel'><ul class='files'>" + items + "</ul>" + readme + "</div>")
    if sections:
        listing = "".join(sections)
    else:
        listing = "<div class='panel'><p class='sub'>" + i18n.t("tools_none_installed", lang) + "</p></div>"

    apps_listing = (
        "<h2>" + i18n.t("tools_apps_h2", lang) + "</h2>"
        "<div class='panel'><ul class='files'>" + "".join(app_items) + "</ul></div>"
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

        "<h2>" + i18n.t("about_setups_h2", lang) + "</h2>"
        "<p>" + i18n.t("about_setups_intro", lang) + "</p>"
        "<div class='info-tiles'>"
        "<div class='info-tile'><span>ESP32-S3-CAM + Heltec V3</span>"
        "<small>" + i18n.t("about_setup1_desc", lang) + "</small></div>"
        "<div class='info-tile'><span>macOS / Windows / Linux + Heltec V3</span>"
        "<small>" + i18n.t("about_setup2_desc", lang) + "</small></div>"
        "<div class='info-tile'><span>macOS / Windows / Linux</span>"
        "<small>" + i18n.t("about_setup3_desc", lang) + "</small></div>"
        "</div>"
        "<div class='panel'><p style='margin-bottom:0'>" + i18n.t("about_setups_chain", lang) + "</p></div>"

        "<h2>" + i18n.t("about_clients_h2", lang) + "</h2>"
        "<div class='info-tiles'>"
        "<div class='info-tile'><span>Android</span>"
        "<small>" + i18n.t("about_client_android", lang) + "</small></div>"
        "<div class='info-tile'><span>" + i18n.t("about_client_handheld_title", lang) + "</span>"
        "<small>" + i18n.t("about_client_handheld", lang) + "</small></div>"
        "<div class='info-tile'><span>" + i18n.t("about_client_browser_title", lang) + "</span>"
        "<small>" + i18n.t("about_client_browser", lang) + "</small></div>"
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
        + _release_line()
        + script
    )


def _render_billboard_page(lang):
    sorts = "".join("<option value='%s'>%s</option>" % (k, i18n.t("sort_" + k, lang))
                    for k in ("newest", "oldest", "title"))
    tools = (
        _ask_drawer(lang) +
        "<div class='finder'><input id='bsearch' type='search' placeholder='"
        + billboard._esc(i18n.t("search_placeholder", lang)) + "' aria-label='"
        + billboard._esc(i18n.t("search_placeholder", lang)) + "'>"
        "<span class='sortbox' title='" + billboard._esc(i18n.t("sort_label", lang)) + "'>"
        "<select id='bsort' aria-label='" + billboard._esc(i18n.t("sort_label", lang)) + "'>" + sorts + "</select></span></div>"
    )
    return (billboard._render_page(lang, tools,
                                   "<p id='bnone' class='sub' style='display:none'>" + i18n.t("filter_none", lang) + "</p>")
            + _BOARD_FINDER_SCRIPT + _nav_except(lang, "board"))


# The billboard's search and sort, in the browser. A word found only in a
# collapsed body opens that post, so the match shows; clearing the search
# closes again what it opened.
_BOARD_FINDER_SCRIPT = """<script>
(function(){
  var ul=document.querySelector('ul.board'), q=document.getElementById('bsearch'), srt=document.getElementById('bsort');
  if(!ul) return;
  function norm(t){ t=String(t||'').toLowerCase(); return t.normalize ? t.normalize('NFD').replace(/[\u0300-\u036f]/g,'') : t; }
  function apply(){
    var w=norm(q.value).trim(), how=srt.value, shown=0, items=[].slice.call(ul.querySelectorAll('li[data-ts]'));
    items.sort(function(a,b){
      if(how==='title') return norm(a.dataset.title).localeCompare(norm(b.dataset.title));
      var d=parseFloat(a.dataset.ts||0)-parseFloat(b.dataset.ts||0);
      return how==='oldest' ? d : -d;
    });
    items.forEach(function(li){
      ul.appendChild(li);
      var det=li.querySelector('details'), body=li.querySelector('.post-body');
      var inTitle=!w || norm(li.dataset.title).indexOf(w)>=0,
          inBody=!!w && !!body && norm(body.textContent).indexOf(w)>=0;
      var ok=inTitle || inBody;
      li.style.display=ok ? '' : 'none';
      if(ok) shown++;
      if(det){
        if(w && inBody && !inTitle){ if(!det.open){ det.open=true; det.setAttribute('data-auto','1'); } }
        else if(det.getAttribute('data-auto')){ det.open=false; det.removeAttribute('data-auto'); }
      }
    });
    var none=document.getElementById('bnone'); if(none) none.style.display=shown||!items.length ? 'none' : '';
  }
  q.addEventListener('input', apply);
  q.addEventListener('keydown', function(e){ if(e.key==='Enter' && q.value.trim() && window.askBK) askBK(q.value); });
  srt.onchange=apply;
})();
</script>"""


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
    # Pages carry the theme (and live state): a browser must ask again
    # rather than show a copy from before the admin changed it.
    no_cache = content_type.startswith("text/html")
    # Admin pages: not kept at all, so Back can't show one from memory.
    no_store = getattr(writer, "_stump_no_store", False)
    # Connection: close matters here. The server closes after every
    # response, but HTTP/1.1 defaults to keep-alive -- so without this
    # the browser holds the socket open expecting to reuse it, then has
    # to discover it's dead and re-issue the request on a new one. That
    # shows up as pages that feel sluggish or hang on the first click.
    cookie = getattr(writer, "_stump_set_cookie", None) if no_cache else None
    resp = ("HTTP/1.1 " + status + "\r\nContent-Type: " + content_type +
             ("\r\nSet-Cookie: " + cookie if cookie else "") +
             "\r\nContent-Length: " + str(len(body)) +
             ("\r\nCache-Control: no-store" if no_store else
              "\r\nCache-Control: no-cache" if no_cache else "") +
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


def _may_read(identifier):
    """Mandatory mode: reading the chat takes a verified identity too
    (stumpid's can_read). Without the plugin, or in open/hybrid: yes."""
    sid = plugins.service_lookup("identity")
    try:
        return sid is None or sid.can_read(identifier)
    except Exception:
        return True


def _voice_gate(identifier, to):
    """The refusal a /msg to `to` would get from stumpid's access mode, or
    None -- a voice note obeys the same rule as the text it replaces."""
    sid = plugins.service_lookup("identity")
    if sid is None:
        return None
    try:
        if sid.requires_verification(identifier, "/msg " + (to or "x") + " x"):
            return i18n.t("auth_required_generic", i18n.get_lang(identifier))
    except Exception as e:
        print("[barkeep] voice access check failed, refusing:", e)
        return i18n.t("auth_required_generic", i18n.get_lang(identifier))
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
                "/files.json", "/plugins.json", "/chain.json", "/stumps.json", "/hw.json", "/admin/radio.json", "/admin/poweroff", "/admin/bluetooth.json", "/admin/bt.json", "/admin/radiolink.json", "/fw", "/tool?", "/lang", "/upload", "/post", "/chat")


def _own_ap_ip():
    """This Stump's address on its own Wi-Fi."""
    try:
        import chain
        return chain.ap_ip()
    except Exception:
        return "192.168.4.1"


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


def _header_text(b):
    """A request or header line as text: UTF-8, or Latin-1 when it isn't
    valid UTF-8. Browsers and Python's urllib send a header like
    X-Filename: thé.txt with é as ONE Latin-1 byte (0xE9); a plain
    .decode() raised on it and the connection closed with no reply at
    all (reported by the Android team, reproduced). Every byte maps to a
    character in Latin-1, so this can't fail."""
    try:
        return b.decode()
    except UnicodeError:
        return "".join(chr(c) for c in b)


def _wants_json(headers):
    """An app asking for JSON (Accept: application/json) gets the result of
    a write -- a post, an upload -- as data; a browser form keeps getting
    the page or sentence it always did."""
    return "application/json" in headers.get("accept", "")


async def _result(writer, as_json, status, code, message, extra=None):
    """One write's outcome: JSON for apps ({"ok", "error", "message", ...}),
    the plain sentence for everyone else. `code` is a stable token apps
    can act on; None on success."""
    if as_json:
        out = {"ok": status.startswith("2"), "message": message}
        if code:
            out["error"] = code
        if extra:
            out.update(extra)
        await _send(writer, status, json.dumps(out), "application/json")
    else:
        await _send(writer, status, message, "text/plain")


async def _handle_tls(reader, writer):
    await _handle(reader, writer, secure=True)


async def _handle(reader, writer, secure=False):
    """One connection: taken in or refused (conn.py: how many at once, per
    address), then served with deadlines on every read and write."""
    try:
        ip = billboard._extract_ip(writer.get_extra_info("peername"))
    except Exception:
        ip = "?"
    why = conn.admit(ip)
    if why:
        await conn.refuse(reader, writer, why)
        return
    task = conn._current()
    conn.watch(task)
    try:
        await _serve(conn.Reader(reader, task), conn.Writer(writer, task), secure)
    except asyncio.CancelledError:
        # The reaper: too slow or silent (conn.py). Let go, quietly.
        conn.note(conn.why_ended(task) or "idle")
        try:
            await writer.aclose()
        except BaseException:
            pass
    finally:
        conn.unwatch(task)
        conn.leave(ip)


async def _serve(reader, writer, secure=False):
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
        parts = _header_text(request_line).split()
        if len(parts) < 2:
            await _send(writer, "400 Bad Request", "Malformed request.", "text/plain")
            return
        method, path = parts[0], parts[1]
        if path.startswith("/admin"):
            try:
                writer._stump_no_store = True      # see _send
            except AttributeError:
                pass

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
            k, _, v = _header_text(line).partition(":")
            headers[k.strip().lower()] = v.strip()
        reader.start_body()

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
        ip = billboard._extract_ip(peer)
        # Who this is: a logged-in profile ("p:<nick>"), else the browser's
        # visitor cookie, else -- a client that keeps no cookies, like the
        # FireFly app (its one-button /auth included) -- its address, as
        # before. Limits (guard.py) still count per address.
        identifier = _visitor(writer, headers, ip, method, path)

        # HTTPS (stump_tls.py) is used for two things only: /admin, where a
        # password is typed, and the voice recorder (/rrc/rec), because
        # browsers only open the microphone on a secure page. Everything
        # else stays on plain HTTP: the chat polls every 2 s, and on HTTPS
        # each poll was a new secure handshake for the CAM -- a few people
        # chatting kept it busy doing nothing else (review H-01).
        # /admin over plain HTTP gets a tiny page that tries HTTPS first:
        # only if the BROWSER finds the certificate valid (by the phone's own
        # clock) does it move; otherwise it stays on HTTP, with no warning.
        if not secure and method == "GET" and "plain=1" not in path and path.startswith("/admin"):
            target = tls.https_redirect(ip, path)
            if target:
                await _send(writer, "200 OK", _https_upgrade_page(target, path))
                return
        if secure and method == "GET" and (path == "/rrc" or path.startswith("/rrc?")):
            # The chat itself, opened over HTTPS (an old bookmark, a link):
            # back to plain HTTP, where its polls cost nothing.
            host = headers.get("host", "").split(":")[0] or tls.status().get("host") or ""
            await writer.awrite("HTTP/1.1 302 Found\r\nLocation: http://" + host + "/rrc\r\n"
                                "Content-Length: 0\r\nConnection: close\r\n\r\n")
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
            lang = i18n.get_lang(identifier)
            if _wants_json(headers):
                # Apps: the librarian's findings as data, with the reply too.
                reply, found = _librarian(body.decode()[:200], lang)
                await _send(writer, "200 OK", json.dumps({"reply": reply, "found": found}), "application/json")
            else:
                reply = _process_command(body.decode()[:200], identifier, lang)
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
            if "application/json" in headers.get("content-type", ""):
                # An app may send {"title": ..., "body": ...} instead of a form.
                try:
                    data = json.loads(body.decode())
                    title = str(data.get("title", "") or "")
                    post_body = str(data.get("body", "") or "")
                except (ValueError, AttributeError):
                    pass
            else:
                for kv in body.decode().split("&"):
                    if kv.startswith("title="):
                        title = billboard._url_decode(kv[6:])
                    elif kv.startswith("body="):
                        post_body = billboard._url_decode(kv[5:])
                    elif kv.startswith("entry=") and not title:
                        title = billboard._url_decode(kv[6:])
            if not guard.window("post", identifier, POST_MAX, POST_PER_S):
                # A few posts a minute per visitor: each one rewrites the
                # billboard file on the card.
                lang = i18n.get_lang(identifier)
                if _wants_json(headers):
                    await _result(writer, True, "429 Too Many Requests", "rate_limited", i18n.t("post_rate", lang))
                else:
                    await _send(writer, "429 Too Many Requests", _page(
                        "<p>" + billboard._esc(rrc.tok_rate(i18n.t("post_rate", lang))) + "</p>"
                        "<p><a href='/billboard'>&larr; " + i18n.t("nav_board", lang) + "</a></p>"))
                return
            ok, info = billboard._append_entry(title, post_body, identifier)
            if _wants_json(headers):
                if ok:
                    await _result(writer, True, "201 Created", None, "posted", {"id": info})
                elif info == "title_required":
                    await _result(writer, True, "400 Bad Request", "title_required", "a title is required")
                else:
                    await _result(writer, True, "507 Insufficient Storage", "storage", "the post couldn't be saved")
            else:
                await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /billboard\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "POST" and path.startswith("/admin/radiolink.json"):
            # USB or Bluetooth for the Heltec on this computer, switched live.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            lang = i18n.get_lang(identifier)
            if not _admin_or_local(identifier, headers):
                await _send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t("admin_bt_denied", lang)}),
                            "application/json")
            else:
                kind = body.decode().split("link=", 1)[-1].split("&")[0] if b"link=" in body else ""
                r = radio.switch_link(kind)
                await _send(writer, "200 OK", json.dumps({"ok": r in ("done", "same"), "message": i18n.t(
                    "admin_link_r_" + r, lang)}), "application/json")

        elif method == "GET" and path.startswith("/admin/bt.json"):
            # The pairing PIN, while it's fresh: an /admin session only.
            if not _admin_or_local(identifier, headers):
                await _send(writer, "403 Forbidden", "{}", "application/json")
            else:
                await _send(writer, "200 OK", json.dumps({"pin": radio.bt_pin(), "probe": radio.probe_state(),
                                                          "heard": radio.bt_heard()}), "application/json")

        elif method == "GET" and path.startswith("/admin"):
            # No link anywhere points here on purpose -- see
            # _render_admin_login_page's own docstring. With a live
            # session (the cookie set at login), the admin page itself.
            lang = i18n.get_lang(identifier)
            tok = _cookie(headers, ADMIN_COOKIE)
            if _session_ok(tok):
                names = fserv._list_files() if fserv.sd_ok else []
                await _send(writer, "200 OK", _page(_render_admin_file_list_page(lang, names, tok)))
            else:
                await _send(writer, "200 OK", _page(_render_admin_login_page(lang)))

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
            if not _admin_ok(pw, identifier):
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
            if not _admin_ok(pw, identifier):
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

        elif method == "POST" and path.startswith("/admin/profiles"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            lang = i18n.get_lang(identifier)
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                n = fields.get("nick", "")
                if fields.get("action") == "delete":
                    msg = i18n.t("admin_profiles_deleted" if profiles.delete(n) else "admin_profiles_missing", lang, nick=n)
                elif profiles.valid_password(fields.get("pw", "")) and profiles.set_password(
                        n, fields.get("pw", ""), digested=await profiles.prepare(fields.get("pw", ""))):
                    msg = i18n.t("admin_profiles_was_reset", lang, nick=n)
                else:
                    msg = i18n.t("profile_err_password", lang, n=profiles.PW_MIN)
                names = fserv._list_files() if fserv.sd_ok else []
                await _send(writer, "200 OK", _page("<p class='sub'><b>" + billboard._esc(msg) + "</b></p>"
                                                    + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and (path.startswith("/admin/setup") or path.startswith("/admin/restart")):
            # The Provisioner's answers changed from /admin (settings.py),
            # and the restart that applies the rest.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            lang = i18n.get_lang(identifier)
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            elif path.startswith("/admin/restart"):
                await _send(writer, "200 OK", _page(
                    "<h1>" + i18n.t("admin_setup_restart", lang) + "</h1><p class='sub'>"
                    + i18n.t("admin_setup_restarting", lang) + "</p>"
                    "<script>setTimeout(function(){location.href='/admin';},20000);</script>"))
                import uasyncio as asyncio
                asyncio.create_task(_restart_soon())
            else:
                changes, bad = stump_settings.parse_form(fields)
                later = []
                if bad:
                    label = {"node_name": "admin_setup_node", "bot_name": "admin_setup_bot",
                             "greeting": "admin_setup_names", "economy": "admin_setup_files",
                             "auth_mode": "admin_setup_access",
                             "storage_mb": "admin_setup_storage"}.get(bad) or "admin_setup_w_" + bad[2:]
                    msg = i18n.t("admin_setup_invalid", lang, field=i18n.t(label, lang).split(" (")[0])
                else:
                    before = stump_settings.current()
                    saved, later = stump_settings.apply(changes)
                    if not saved:
                        msg = i18n.t("admin_setup_not_saved", lang)
                    elif all(before.get(k) == v for k, v in changes.items()):
                        msg = i18n.t("admin_setup_unchanged", lang)
                    elif later:
                        msg = None
                    else:
                        msg = i18n.t("admin_setup_saved", lang)
                labels = {"NODE_NAME": "admin_setup_node", "BOT_NAME": "admin_setup_bot",
                          "MESH_GREETING": "admin_setup_names", "FEATURES": "admin_setup_features",
                          "CREDIT_WEIGHTS": "admin_setup_files", "AUTH_MODE": "admin_setup_access",
                          "PC_STORAGE_MB": "admin_setup_storage"}
                what = ", ".join(i18n.t(labels.get(k, k), lang).split(" (")[0] for k in later)
                notice = "<p class='sub'><b>" + billboard._esc(msg or i18n.t("admin_setup_later", lang, what=what)) + "</b></p>"
                names = fserv._list_files() if fserv.sd_ok else []
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/poweroff"):
            # Turn this Stump off (a computer): /admin's button with the
            # password, or the Stop file on the computer itself (local).
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            h = _host()
            lang = i18n.get_lang(identifier)
            if h is None:
                await _send(writer, "404 Not Found", "Not a computer Stump", "text/plain")
            elif not (ip in _LOCAL or _admin_ok(fields.get("admin_pass", ""), identifier)):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                folder = billboard._esc(str(getattr(h, "ROOT", "") or ""))
                await _send(writer, "200 OK", _page(
                    "<h1>" + i18n.t("admin_power_bye_title", lang) + "</h1><p class='sub'>"
                    + i18n.t("admin_power_bye", lang, start=billboard._esc(h.START_FILE), folder=folder) + "</p>"))
                import uasyncio as asyncio
                asyncio.create_task(_power_off_soon())

        elif method == "POST" and path.startswith("/admin/radioretry"):
            # /admin's "Reconnect the radio": the handshake again, now.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                msg = i18n.t("admin_radio_retrying" if radio.retry() else "admin_radio_none", lang)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/radiochat"):
            # /admin's switch for the radio settings in the chat's pop-up.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                on = fields.get("on") == "1"
                ok = radio.set_chat_edit(on)
                msg = i18n.t(("admin_radio_chat_on" if on else "admin_radio_chat_off") if ok else "admin_radio_not_saved", lang)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/bluetooth.json"):
            # The Heltec's Bluetooth from /admin: on, off, pairing mode.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            lang = i18n.get_lang(identifier)
            if not _admin_or_local(identifier, headers):
                await _send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t("admin_bt_denied", lang)}),
                            "application/json")
            else:
                action = billboard._url_decode(body.decode().split("action=", 1)[-1].split("&")[0]) if b"action=" in body else ""
                if action == "computer":
                    r = radio.pair_computer()
                    ok, key = r == "done", {"done": "admin_bt_computer_go", "usb": "admin_bt_computer_usb",
                                            "bleak": "admin_link_r_bleak"}.get(r, "admin_bt_offline")
                else:
                    ok = action in ("on", "off", "pair", "unpair") and radio.bluetooth(action) > 0
                    key = {"on": "admin_bt_done_on", "off": "admin_bt_done_off", "pair": "admin_bt_pairing",
                           "unpair": "admin_bt_done_unpair"}.get(action)
                    key = key if ok else "admin_bt_offline"
                await _send(writer, "200 OK", json.dumps({"ok": ok, "message": i18n.t(key, lang)}), "application/json")

        elif method == "POST" and path.startswith("/admin/radio.json"):
            # The radio's settings from the chat's pop-up (rrc_ui): the
            # person on the computer, or an /admin session. JSON answer.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            lang = i18n.get_lang(identifier)
            if not _radio_editor(identifier, headers):
                await _send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t(
                    "rrc_radio_owner_only" if radio.chat_edit() else "admin_radio_chat_off", lang)}),
                            "application/json")
            else:
                fields = {}
                for kv in body.decode().split("&"):
                    parts = kv.split("=", 1)
                    if len(parts) == 2:
                        fields[parts[0]] = billboard._url_decode(parts[1])
                settings, bad = radio.parse_form(fields)
                if bad:
                    label = {"frequency": "radio_freq", "bandwidth": "radio_bw", "txpower": "radio_txp",
                             "sf": "radio_sf", "cr": "radio_cr"}[bad]
                    ok, msg = False, i18n.t("admin_radio_invalid", lang, field=i18n.t(label, lang))
                else:
                    result = radio.apply(settings)
                    ok = radio.save(settings)
                    msg = i18n.t("admin_radio_not_saved" if not ok else {"sent": "admin_radio_sent",
                                 "pending": "admin_radio_pending"}.get(result, "admin_radio_none"), lang)
                await _send(writer, "200 OK", json.dumps({"ok": ok, "message": msg}), "application/json")

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
            if not _admin_ok(pw, identifier):
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

        elif method == "POST" and path.startswith("/admin/layout"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                if fields.get("reset"):
                    ok = layout.save_many({k: None for k in layout.KINDS})
                else:
                    nav_ok = set(_DESTINATIONS)
                    tile_ok = set(k for k, n, h in home_tile_items(lang, all=True))

                    def keys(name, allowed):
                        return [k for k in fields.get(name, "").split(",") if k in allowed]
                    changes = {"nav_hidden": keys("nav_hidden", nav_ok),
                               "tiles_hidden": keys("tiles_hidden", tile_ok)}
                    if keys("nav", nav_ok):
                        changes["nav"] = keys("nav", nav_ok)
                    if keys("tiles", tile_ok):
                        changes["tiles"] = keys("tiles", tile_ok)
                    ok = layout.save_many(changes)
                msg = i18n.t("admin_layout_saved" if ok else "admin_layout_not_saved", lang)
                names = fserv._list_files() if fserv.sd_ok else []
                await _send(writer, "200 OK", _page("<p class='sub'>" + billboard._esc(msg) + "</p>"
                                                    + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and (path.startswith("/admin/theme") or path.startswith("/admin/tools")):
            # The theme for everyone, and deleting tools: like the other
            # /admin forms, the password comes back as a hidden field and
            # is checked again here.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields, ticked, sections = {}, [], []
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) != 2:
                    continue
                val = billboard._url_decode(parts[1])
                if parts[0] == "f":
                    ticked.append(val)
                elif parts[0] == "d":
                    sections.append(val)
                else:
                    fields[parts[0]] = val
            pw = fields.get("admin_pass", "")
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                extra = ""
                if path.startswith("/admin/theme"):
                    choice = fields.get("theme", "").strip().lower() or None
                    if choice is not None and choice not in theme.THEMES:
                        msg = i18n.t("admin_site_theme_not_saved", lang)
                    elif theme.set_site_theme(choice):
                        msg = i18n.t("admin_site_theme_saved", lang,
                                     name=_theme_label(theme.site_theme(), lang))
                        # This browser may have its own theme from the
                        # buttons below, which would hide the change:
                        # drop it, and show the new one now.
                        extra = ("<script>try{localStorage.removeItem('stump_theme');}catch(e){}"
                                 "document.documentElement.removeAttribute('style');"
                                 "document.documentElement.setAttribute('data-theme',"
                                 + json.dumps(theme.site_theme()) + ");</script>")
                    else:
                        msg = i18n.t("admin_site_theme_not_saved", lang)
                else:
                    gone = 0
                    for d in sections:
                        gone += fserv.delete_tool_section(d)
                    for rel in ticked:
                        if rel.split("/", 1)[0] in sections:
                            continue           # went with its section
                        if fserv.delete_tool(rel):
                            gone += 1
                    msg = i18n.t("admin_deleted_notice", lang, n=gone)
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>" + extra
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/plugins"):
            # Before the generic POST /admin (login) branch, like the others.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            fields = {}
            for kv in body.decode().split("&"):
                parts = kv.split("=", 1)
                if len(parts) == 2:
                    fields[parts[0]] = billboard._url_decode(parts[1])
            pw = fields.get("admin_pass", "")
            if not _admin_ok(pw, identifier):
                await _send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
            else:
                lang = i18n.get_lang(identifier)
                msg = plugins.admin_chat([fields.get("action", ""), fields.get("name", "")], lang)[0]
                names = fserv._list_files() if fserv.sd_ok else []
                notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
                await _send(writer, "200 OK", _page(notice + _render_admin_file_list_page(lang, names, pw)))

        elif method == "POST" and path.startswith("/admin/logout"):
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            for kv in body.decode().split("&"):
                if kv.startswith("admin_pass="):
                    _end_session(billboard._url_decode(kv[11:]))
            _end_session(_cookie(headers, ADMIN_COOKIE))
            await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /admin\r\n"
                                "Set-Cookie: " + ADMIN_COOKIE + "=; Path=/admin; Max-Age=0; HttpOnly; SameSite=Strict\r\n"
                                "Cache-Control: no-store\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

        elif method == "POST" and path.startswith("/admin") and not path.startswith("/admin/delete"):
            # The login. A right password starts a session and redirects
            # to GET /admin (see SESSION_IDLE_S above), so this POST --
            # with the password in it -- never sits in the history.
            length = int(headers.get("content-length", "0"))
            body = await _read_small_body(reader, length)
            pw = ""
            for kv in body.decode().split("&"):
                if kv.startswith("admin_pass="):
                    pw = billboard._url_decode(kv[11:])
            lang = i18n.get_lang(identifier)
            if not _check_admin_pw(pw, identifier):
                await _send(writer, "403 Forbidden", _page(_render_admin_login_page(
                    lang, error=True, wait=guard.locked(identifier))))
            else:
                tok = _new_session()
                await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /admin\r\n"
                                    "Set-Cookie: " + ADMIN_COOKIE + "=" + tok
                                    + "; Path=/admin; HttpOnly; SameSite=Strict\r\n"
                                    "Cache-Control: no-store\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

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
            if not _admin_ok(pw, identifier):
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
            if not _admin_ok(pw, identifier):
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
            hello = _greeting(identifier, ip)
            if hello == "required":
                u = rrc.touch_user(identifier)
                await _send(writer, "200 OK", json.dumps({
                    "room": rrc.DEFAULT_ROOM, "nick": u["nick"], "rooms": [], "messages": [], "dms": [],
                    "users": [], "stumps": [], "node": rrc.NODE, "registered": False, "renamed": {},
                    "hello": "required",
                    "topic": i18n.t("hello_required", i18n.get_lang(identifier))}), "application/json")
                return
            user = rrc.touch_user(identifier)
            # The server's idea of which room this client is in wins --
            # /join from another tab, or a room that has since vanished,
            # both need to be reflected back rather than silently
            # leaving the client polling a room it isn't in.
            actual = user["room"]
            if not rrc.room_exists(actual) or not rrc.room_allows(identifier, actual):
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
                "hw": _hw_brief(_radio_editor(identifier, headers)),
                # Which node this is -- a client reaching it over Wi-Fi
                # and LoRa matches the two by this LXMF address.
                "node": rrc.NODE,
                # Logged into a registered profile (profiles.py)?
                "registered": identifier.startswith("p:"),
                # People known by a new name lately (old -> new): a thread
                # under the old one belongs to the new one.
                "renamed": rrc.renames(),
            }
            if hello:
                payload["hello"] = hello
            if not _may_read(identifier):
                # Mandatory: nothing to read before /auth -- the room's
                # name and the rooms list stay, so the client still works,
                # and the topic says why it's empty.
                payload.update(messages=[], dms=[], users=[],
                               topic=i18n.t("rrc_auth_to_read", i18n.get_lang(identifier)))
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
            if not refused and _greeting(identifier, ip) == "required":
                refused = i18n.t("hello_required", i18n.get_lang(identifier))
            replies = [refused] if refused else rrc.send_voice(identifier, to, mode, audio)
            await _send(writer, "200 OK", json.dumps({"replies": replies, "room": None}), "application/json")

        elif method == "GET" and path.startswith("/rrc/voice"):
            # The audio of a voice note in this client's own inbox, as raw
            # Codec 2 frames; its mode is in the poll's "voice" object.
            try:
                m = rrc.find_dm(identifier, int(_query(path, "id") or "-1"))
            except ValueError:
                m = None
            if not _may_read(identifier) or _greeting(identifier, ip) == "required":
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
            text = body.decode()
            if _greeting(identifier, ip) == "required" and not any(
                    text.strip().lower() == c or text.strip().lower().startswith(c + " ") for c in _HELLO_OK):
                await _send(writer, "200 OK", json.dumps({
                    "replies": [i18n.t("hello_required", i18n.get_lang(identifier))], "room": None,
                    "error": "hello_required"}), "application/json")
                return
            user = rrc.touch_user(identifier)
            # The SERVER decides which room this client is in, not the
            # client. X-Room is deliberately ignored: the client only
            # ever learns its room from /rrc/poll in the first place, so
            # a header that disagrees is stale (a second tab left on an
            # old room) or forged. Honouring it used to move the user --
            # silently pulling them out of the room they were actually
            # in, and letting anyone post into a room they never joined.
            room = user["room"] if rrc.room_exists(user["room"]) else rrc.DEFAULT_ROOM
            replies, new_room = rrc.handle_input(identifier, room, text)
            out = {"replies": replies, "room": new_room}
            hello = _greeting(identifier, ip)
            if hello:
                out["hello"] = hello              # "done" once /auth and /nick are in
            await _send(writer, "200 OK", json.dumps(out), "application/json")

        elif method == "GET" and path.startswith("/rrc/rec"):
            # The voice recorder, opened by the chat in a small window over
            # HTTPS (the only way a browser opens the microphone). It
            # records and hands the audio back to the chat, which encodes
            # and sends it as before.
            origins = ["http://" + h for h in (tls.status().get("host"), _own_ap_ip(),
                                                headers.get("host", "").split(":")[0]) if h]
            await _send(writer, "200 OK", rrc_ui.render_recorder(i18n.get_lang(identifier), origins))

        elif method == "GET" and path.startswith("/rrc"):
            user = rrc.touch_user(identifier)
            rec = None if secure else tls.https_redirect(ip, "/rrc/rec")
            await _send(writer, "200 OK", rrc_ui.render_page(user["room"], user["nick"],
                                                             i18n.get_lang(identifier), rec_url=rec))

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

        elif method == "GET" and path.startswith("/tools.json"):
            # The tools page as data: the FireFly apps (newest of each, by
            # name -- fserv.find_apps) and the technician's files. Before
            # this, /tools.json matched "/tools" and returned the HTML page
            # with a 200, which an app would have taken for an answer.
            apps = fserv.find_apps()
            app_list = [{"app": k, "file": a["name"], "version": a["version"], "size": a["size"],
                         "url": "/tool?f=" + _url_encode(a["file"])}
                        for k, a in sorted(apps.items())]
            # "files": every other file, flat, as before ("section" says
            # which folder); "sections": the same grouped as on the page.
            files, sections = [], []
            for sec in fserv.tool_sections(i18n.get_lang(identifier)):
                prefix = sec["dir"] + "/" if sec["dir"] else ""
                sfiles = [{"name": n, "size": size, "section": sec["dir"],
                           "url": "/tool?f=" + _url_encode(prefix + n)}
                          for n, size in sec["files"] if not fserv.is_app_file(n)]
                files.extend(sfiles)
                if sec["dir"] and sfiles:
                    sections.append({"dir": sec["dir"], "title": sec["title"], "summary": sec["summary"],
                                     "readme": ("/tool?f=" + _url_encode(prefix + sec["readme_file"]))
                                     if sec["readme"] else None,
                                     "files": sfiles})
                elif sfiles:
                    sections.append({"dir": "", "title": None, "summary": "", "readme": None,
                                     "files": sfiles})
            await _send(writer, "200 OK", json.dumps({"sd": fserv.sd_ok, "apps": app_list, "files": files,
                                                      "sections": sections}),
                        "application/json")

        elif path == "/hello" or path.startswith("/hello?"):
            # The greeting as one exchange, made by the app inside its own
            # connect handshake -- invisible to the person (CLIENT_QUICKSTART,
            # "The greeting"). Both sides prove who they are:
            #   GET  -> this Stump's identity, a one-time code, and this
            #           Stump's signature over code + its LXMF address;
            #   POST {"pub", "sig" (over the code), "name", "prefer"}
            #        -> "hello": "done", one person from then on.
            sid = plugins.service_lookup("identity")
            if sid is None:
                await _send(writer, "503 Service Unavailable", json.dumps({"hello": "off"}), "application/json")
            elif not guard.allow("hello", ip, every=2, burst=6):
                # Each one is a signature (the Stump's, or checking the
                # phone's): a few at once, then one every 2 s per address,
                # so nobody keeps the CAM busy signing.
                await _send(writer, "429 Too Many Requests", json.dumps({"hello": "wait", "retry_after": 2}),
                            "application/json")
            elif method == "GET":
                nonce = sid.start_challenge(identifier)
                me = rrc.NODE_IDENTITY[0]
                lx = (rrc.NODE or {}).get("lxmf") or ""
                out = {"stump": (rrc.NODE or {}).get("name"), "lxmf": lx, "nonce": nonce,
                       "hello": _greeting(identifier, ip) or "done"}
                if me is not None:
                    out["pub"] = ubinascii.hexlify(me.get_public_key()).decode()
                    out["sig"] = ubinascii.hexlify(me.sign((nonce + lx).encode())).decode()
                await _send(writer, "200 OK", json.dumps(out), "application/json")
            else:
                body = await _read_small_body(reader, int(headers.get("content-length", "0")))
                try:
                    d = json.loads(body.decode())
                    pub, sig = str(d["pub"]), str(d["sig"])
                except (ValueError, KeyError, TypeError, AttributeError):
                    await _send(writer, "400 Bad Request", json.dumps({"hello": "required", "error": "bad_request"}),
                                "application/json")
                    return
                user = rrc.touch_user(identifier)
                room = user["room"] if rrc.room_exists(user["room"]) else rrc.DEFAULT_ROOM
                replies, _ = rrc.handle_input(identifier, room, "/auth " + pub + " " + sig)
                if not any(str(r).startswith("AUTH-OK") for r in replies):
                    await _send(writer, "403 Forbidden", json.dumps({
                        "hello": "required", "error": "auth", "message": " ".join(str(r) for r in replies)}),
                        "application/json")
                    return
                out = {}
                name = rrc.clean_nick(str(d.get("name") or ""))
                if name and name != rrc.get_user(identifier)["nick"]:
                    r2, _ = rrc.handle_input(identifier, room, "/nick " + name)
                    if r2:
                        out["error"], out["message"] = "name", " ".join(str(r) for r in r2)
                lx = rrc.lxmf_of(identifier)
                if not lx:
                    try:
                        import rrc_mesh
                        lx = rrc_mesh.lxmf_address(sid.verified_identity_for(identifier))
                    except Exception:
                        lx = None
                if d.get("prefer") in ("wifi", "lora") and lx:
                    rrc.set_prefer(lx, d["prefer"])
                out.update({"hello": _greeting(identifier, ip) or "done", "nick": rrc.get_user(identifier)["nick"],
                            "lxmf": lx, "prefer": rrc.prefer_of(lx) if lx else "wifi"})
                await _send(writer, "200 OK", json.dumps(out), "application/json")

        elif method == "GET" and path.startswith("/node.json"):
            # Who this node is, for discovery: answers without touching
            # the chat (polling /rrc/poll joins you to a room).
            import chain
            info = {"name": rrc.NODE.get("name"), "lxmf": rrc.NODE.get("lxmf")}
            info.update(rrc.NODE_INFO)
            info["ap_ip"] = chain.ap_ip()
            st = chain.status()
            info["chain"] = st if st.get("enabled") else None
            info["features"] = [f for f in features.ALL if features.enabled(f)]
            # "rescue": a rescue start after failed ones (keepalive.py) --
            # no radio, no chain, no plugins for now.
            info["mode"] = keepalive.mode()
            # The radio settings, when this Stump has a radio: every node of
            # a mesh needs the same ones (the Provisioner copies them).
            cur = radio.current()
            if cur:
                info["radio"] = {k: cur[k] for k in ("frequency", "bandwidth", "sf", "cr")}
            await _send(writer, "200 OK", json.dumps(info), "application/json")

        elif method == "GET" and path.startswith("/hw.json"):
            # The radio(s): connected, connecting, or why not; the RNode's
            # firmware and live statistics (radio.hw). For the chat's
            # status dot and /admin.
            await _send(writer, "200 OK", json.dumps(radio.hw()), "application/json")

        elif method == "GET" and path.startswith("/stumps.json"):
            # This Stump and the others it heard, each with its addresses
            # (discovery.py): reach one Stump, find them all.
            import discovery
            await _send(writer, "200 OK", json.dumps(discovery.directory()), "application/json")

        elif method == "GET" and path.startswith("/chain.json"):
            # This Stump's place in a chain over Wi-Fi (chain.py): role,
            # addresses, the Stump above it and who's linked below.
            import chain
            await _send(writer, "200 OK", json.dumps(chain.status()), "application/json")

        elif method == "GET" and path.startswith("/plugins.json"):
            # What the plugins add, for clients: their commands (to offer
            # as suggestions), the trigger prefix, and their pages.
            await _send(writer, "200 OK", json.dumps(plugins.public_info()), "application/json")

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
            # f= is a file in tools/ or one folder down ("rnfs/x.whl"):
            # each part goes through the same name guard, and
            # fserv.tool_path refuses anything deeper or with "..".
            raw_f = path.split("f=", 1)[-1] if "f=" in path else ""
            parts = billboard._url_decode(raw_f).split("/")
            tname = _clean_filename(parts[-1])
            rel = "/".join([_clean_filename(p) for p in parts[:-1]] + [tname])
            tpath = fserv.tool_path(rel) or ""
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
            # ?q= searches titles and bodies (accents and case ignored);
            # ?sort=oldest|title|newest.
            q = _query(path, "q") or ""
            sort = _query(path, "sort") or "newest"
            entries = [(sig, title, body, ts, pid) for sig, title, body, ts, pid in billboard._read_entries()
                       if billboard._live(ts)]
            if q:
                import librarian
                keep = set(p["id"] for p in billboard.search_entries(librarian.parse(q)[1] or [librarian.fold(q).strip()], 1000))
                entries = [e for e in entries if e[4] in keep]
            if sort == "title":
                entries.sort(key=lambda e: e[1].lower())
            elif sort != "oldest":
                entries.reverse()
            posts = [{"id": pid, "title": title, "body": body, "sig": sig}
                     for sig, title, body, ts, pid in entries]
            await _send(writer, "200 OK", json.dumps({"posts": posts}), "application/json")

        elif path == "/profile" or path.startswith("/profile?") or path.startswith("/profile/"):
            # Registered profiles (profiles.py).
            lang = i18n.get_lang(identifier)
            T = lambda k, **kw: i18n.t(k, lang, **kw)
            fields = {}
            if method == "POST":
                length = int(headers.get("content-length", "0"))
                body = await _read_small_body(reader, length)
                for kv in body.decode().split("&"):
                    parts = kv.split("=", 1)
                    if len(parts) == 2:
                        fields[parts[0]] = billboard._url_decode(parts[1])
            notice = ""
            nick = (fields.get("nick") or "").strip()
            if method != "POST":
                pass
            elif path.startswith("/profile/register"):
                if fields.get("pw") != fields.get("pw2"):
                    notice = T("profile_mismatch")
                elif rrc.nick_taken(nick, identifier) and not profiles.is_registered(nick):
                    notice = T("profile_taken", nick=nick)
                elif not guard.pw_budget():
                    notice = T("profile_wait", s=2)
                else:
                    pw_new = fields.get("pw", "")
                    rec, why = profiles.register(nick, pw_new, digested=(await profiles.prepare(pw_new)
                                                                         if profiles.valid_password(pw_new) else None))
                    if rec is None:
                        notice = T("profile_err_" + why, nick=nick, n=profiles.PW_MIN)
                    else:
                        moved = fserv.move_credits(identifier, "p:" + profiles.key(nick))
                        _log_in(writer, identifier, nick)
                        identifier = "p:" + profiles.key(nick)
                        notice = T("profile_created" if moved else "profile_created_empty", nick=rec["nick"])
            elif path.startswith("/profile/login"):
                ok, wait = await _profile_pw(nick, fields.get("pw", ""), ip)
                if wait:
                    notice = T("profile_wait", s=wait)
                elif not ok:
                    notice = T("profile_wrong")
                else:
                    _log_in(writer, identifier, nick)
                    identifier = "p:" + profiles.key(nick)
                    notice = T("profile_welcome", nick=profiles.get(nick)["nick"])
            elif path.startswith("/profile/logout"):
                profiles.end_session(_cookie(headers, PROFILE_COOKIE))
                _profile_cookie(writer, None)
                rrc.drop_user(identifier)
                identifier = ip
                notice = T("profile_bye")
            elif path.startswith("/profile/password") and identifier.startswith("p:"):
                pn = identifier[2:]
                ok, wait = await _profile_pw(pn, fields.get("old", ""), ip)
                if wait:
                    notice = T("profile_wait", s=wait)
                elif not ok:
                    notice = T("profile_wrong")
                elif fields.get("pw") != fields.get("pw2"):
                    notice = T("profile_mismatch")
                elif not profiles.valid_password(fields.get("pw", "")):
                    notice = T("profile_err_password", n=profiles.PW_MIN)
                elif not guard.pw_budget():
                    notice = T("profile_wait", s=2)
                elif not profiles.set_password(pn, fields.get("pw", ""),
                                               digested=await profiles.prepare(fields.get("pw", ""))):
                    notice = T("profile_err_password", n=profiles.PW_MIN)
                else:
                    # A new password ends every session, this one too: back in.
                    _profile_cookie(writer, profiles.start_session(pn))
                    notice = T("profile_changed")
            await _send(writer, "200 OK", _page(_render_profile_page(lang, identifier, notice)))

        elif method == "GET" and path.startswith("/files.json"):
            # The shelf as data. Download with /download?f=<name>, the
            # name percent-encoded as UTF-8.
            # ?q= words in the name, ?cat=video|music|document|image|other,
            # ?sort=newest|oldest|name|small|large (default: by name, as before).
            files = []
            if fserv.sd_ok:
                import librarian
                q = _query(path, "q") or ""
                words = (librarian.parse(q)[1] or [librarian.fold(q).strip()]) if q.strip() else None
                for f in fserv.query_files(words, _query(path, "cat") or None,
                                           _query(path, "sort") or "name", 0):
                    files.append({"name": f["name"], "size": f["size"], "class": fserv.guess_class(f["name"]),
                                  "cat": f["cat"], "mtime": f["mtime"],
                                  "cost": fserv.credit_cost(f["name"]) if fserv.CREDITS_ENABLED else 0})
            await _send(writer, "200 OK", json.dumps({"sd": fserv.sd_ok, "credits": fserv.CREDITS_ENABLED,
                                                      "files": files}), "application/json")

        elif method == "GET" and path.startswith("/files"):
            await _send(writer, "200 OK", _page(_render_files_page(i18n.get_lang(identifier))))

        elif method == "GET" and path.startswith("/billboard"):
            await _send(writer, "200 OK", _page(_render_billboard_page(i18n.get_lang(identifier))))

        elif method == "POST" and path.startswith("/upload"):
            raw_name = headers.get("x-filename", "upload.bin")
            if headers.get("x-filename-encoding", "").lower() == "url":
                # Percent-encoded UTF-8 (encodeURIComponent): plain ASCII on
                # the wire, so any name survives -- accents, other scripts,
                # emoji. What the Files page sends; a raw name still works.
                raw_name = billboard._url_decode(raw_name)
            fname = _clean_filename(raw_name)
            hash_hex = headers.get("x-hash", "").strip()
            length = int(headers.get("content-length", "0"))
            as_json = _wants_json(headers)

            # Validate and pick the destination BEFORE reading a single
            # byte of the body. The old order read the whole upload into
            # memory first and only then checked whether it could be
            # accepted -- so a rejected upload still had to fit in RAM,
            # which is the exact failure this restructure removes.
            # Rejections drain instead of buffering.
            if not fname:
                await fserv.drain(reader, length)
                await _result(writer, as_json, "400 Bad Request", "no_filename", "No filename given.")
            elif not fserv.sd_ok:
                await fserv.drain(reader, length)
                await _result(writer, as_json, "503 Service Unavailable", "no_card", "No card in the slot right now.")
            elif length <= 0:
                await _result(writer, as_json, "400 Bad Request", "empty", "Empty upload.")
            elif fserv.upload_gate(guard._k(identifier), length):
                # One visitor's share (fserv: one at a time, files an
                # hour, room a day): told why, nothing read.
                st, code, key, params = fserv.upload_gate(guard._k(identifier), length)
                await fserv.drain(reader, length)
                await _result(writer, as_json, st, code, i18n.t(key, i18n.get_lang(identifier), **params))
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
                await _result(writer, as_json, "507 Insufficient Storage", "full",
                              "Card capacity exceeded (75% threshold limit).")
            else:
                table = fserv._awaiting()
                is_slot = bool(hash_hex) and hash_hex in table and not table[hash_hex]["fulfilled"]
                if is_slot:
                    dest = fserv.SD_MOUNT + "/slot_" + hash_hex + "_" + fname
                else:
                    # A name already shared: "name (2).ext", never a
                    # replacement of someone else's file.
                    fname = fserv.free_name(fserv.SHARED_DIR, fname)
                    dest = fserv.SHARED_DIR + "/" + fname

                who = guard._k(identifier)
                fserv.upload_start(who)
                written = 0
                try:
                    ok, written = await fserv.stream_to_file(reader, dest, length)
                finally:
                    fserv.upload_end(who, written if written >= length else 0)

                if not ok:
                    await _result(writer, as_json, "500 Internal Server Error", "interrupted",
                                  "Upload interrupted (" + str(written) + " of " +
                                  str(length) + " bytes) — nothing was kept. Try again.",
                                  {"received": written, "expected": length})
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
                    extra = {"name": fname, "size": written, "slot": is_slot, "balance": None}
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
                                extra["balance"] = credited
                            else:
                                msg = "Uploaded. Thanks for bringing something."
                    except Exception as e:
                        print("[barkeep] upload bookkeeping failed (file already saved):", e)
                        msg = "Uploaded, but couldn't update your balance/slot record."
                        extra["warning"] = "bookkeeping"
                    await _result(writer, as_json, "200 OK", None, msg, extra)

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

        elif path.startswith(plugins.PAGE_PREFIX) and plugins.page_target(path) is not None:
            # A plugin's page. Only under /p/<plugin>/, so it can never
            # shadow a core address. HTML is framed like every other page
            # (theme, language, nav) unless the plugin asked for raw=True.
            lang = i18n.get_lang(identifier)
            body = b""
            if method == "POST":
                body = await _read_small_body(reader, int(headers.get("content-length", "0")),
                                              cap=plugins.PAGE_BODY_MAX)
            status, out, ctype, raw = await plugins.handle_page(method, path, headers, body, identifier, lang)
            if status.startswith("303"):
                loc = out if isinstance(out, str) and out.startswith("/") and "\r" not in out \
                    and "\n" not in out else "/"
                await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: " + loc +
                                    "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            elif raw:
                await _send(writer, status, out, ctype)
            else:
                await _send(writer, status, _page(_lang_switcher(lang, path.split("?", 1)[0]) + out
                                                  + _nav_except(lang, None)))

        else:
            lang = i18n.get_lang(identifier)
            await _send(writer, "200 OK", _page(_render_chat_page(lang) + (
                _ask_drawer(lang) if features.enabled("files") or features.enabled("billboard") else "")))

    except conn.Dropped as e:
        conn.note(e.args[0] if e.args else "head")   # too slow or silent: let go, quietly
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
        import stump_host                       # a Stump on a computer (docs/PC.md)
        port = int(stump_host.setting("PC_HTTP_PORT", port))
    except ImportError:
        stump_host = None
    try:
        await asyncio.start_server(_handle, "0.0.0.0", port)
    except Exception as e:
        bound = False
        if stump_host is not None and port != 8080:
            # Port 80 taken on the computer (another web server): 8080.
            print("[web] port %d unavailable (%s): using 8080" % (port, e))
            port = 8080
            try:
                await asyncio.start_server(_handle, "0.0.0.0", port)
                print("[web] visitors: http://%s:8080/" % stump_host.hotspot_ip())
                bound = True
            except Exception as e2:
                e = e2
        if not bound:
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
            tls.mark_failed("couldn't open port 443: %s" % e, "port", str(e))
            print("[web] HTTPS not started:", e)
    else:
        print("[web] HTTPS off:", tls.status()["reason"])
    print("[web] serving on port", port)
    try:
        import discovery
        discovery.WEB_PORT = port          # what "who's a Stump here?" and the beacon say
    except ImportError:
        pass
