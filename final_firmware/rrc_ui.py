# Project Stump -- RRC web client
# Place at: firmware/rrc_ui.py
#
# The mIRC-shaped client: room list down the side, message pane, nick,
# and one input line where /commands do the work.
#
# Kept separate from rrc.py so the chat engine has no HTML in it -- the
# same engine can later drive a LoRa/LXMF client without dragging a web
# UI along with it.
#
# Deliberately no external assets. Anyone on the Stump's own AP has no
# route to the wider internet, so a CDN font or script would simply
# never load for exactly the people this is built for. Everything below
# is inline and self-contained.
#
# The client polls for new messages by highest-id-seen rather than
# reloading the page. The old BarKeep chat box used a full page refresh,
# which wiped whatever you were mid-typing every few seconds -- with a
# real conversation happening that would be unusable.

import ujson as json
import rrc
import i18n

STYLE = """
*{box-sizing:border-box;}
body{
  background:#1b1512; color:#ecdfc8; margin:0;
  font-family:ui-monospace,'Cascadia Code','SF Mono','Courier New',monospace;
  font-size:14px; display:flex; flex-direction:column;
  /* 100vh is the WRONG height on mobile: it means the viewport with
     browser chrome hidden, so when Chrome Android puts its navigation
     bar at the bottom the page is taller than the visible area and the
     compose box sits underneath it. dvh tracks the CURRENTLY visible
     height, which is what we actually want. 100vh stays first as a
     fallback for engines without dvh -- they get today's behaviour
     rather than no height at all. */
  height:100vh;
  height:100dvh;
}
header{
  padding:8px 12px; border-bottom:1px solid #493c2e; background:#2a2119;
  display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
}
header b{color:#d97a3a;}
#topic{color:#9c8d76; font-size:12px; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
#me{color:#f0a050;}
.me{white-space:nowrap;}
/* Escape hatches. Touch-sized (44px min) because the kiosk is a
   wall-mounted panel operated with a finger, not a cursor. */
.nav{display:flex; gap:6px; margin-left:auto;}
.nav a{
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  min-width:44px; min-height:44px; gap:2px; padding:4px 8px;
  color:#d97a3a; text-decoration:none; border:1px solid #493c2e;
  border-radius:6px; background:#231c16;
}
.nav a:hover,.nav a:active,.nav a:focus{
  color:#f0a050; border-color:#d97a3a; outline:none;
}
.nav span{font-size:10px; letter-spacing:.02em;}
main{flex:1; display:flex; min-height:0;}
#rooms{
  width:132px; border-right:1px solid #493c2e; background:#221b15;
  overflow-y:auto; flex-shrink:0;
}
#rooms div{padding:7px 10px; cursor:pointer; border-bottom:1px solid #2f271e;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
#rooms div:hover{background:#2f271e;}
#rooms div.active{background:#d97a3a; color:#1b1512; font-weight:bold;}
/* DM section nests plain divs inside #rooms, so the base row styling
   above (#rooms div{...}) already applies -- descendant selectors
   match at any depth, not just direct children. Only the header label
   and the unread badge need their own rules, and they need !important
   specifically: #rooms div's id-based selector outranks a bare class
   selector on specificity alone, so .dm-header's overrides would
   silently lose without it. */
.dm-header{
  padding:10px 10px 4px !important; cursor:default !important;
  font-size:.72rem; text-transform:uppercase; letter-spacing:.06em;
  color:#7d715f; border-bottom:none !important;
}
#rooms div.dm-entry.unread{color:#f0a050; font-weight:bold;}
#log{flex:1; overflow-y:auto; padding:10px 12px; line-height:1.5;}
#log p{margin:0 0 3px; overflow-wrap:break-word; word-break:break-word;}
.nick{color:#d97a3a;}
.self .nick{color:#f0a050;}
.system{color:#7d715f; font-style:italic;}
.action{color:#c8b48f; font-style:italic;}
.local{color:#7d715f;}
/* Private messages, visually distinct from room traffic so nobody
   mistakes one for something the whole room can see. */
.dm{color:#c8a2c8;}
.dm .nick{color:#d8b4d8;}
.err{color:#e07a5a;}
footer{
  border-top:1px solid #493c2e; background:#2a2119;
  display:flex; gap:8px;
  /* Pad past the home-indicator / gesture area on devices that report
     one, so the input isn't flush against a bar the user can't move. */
  padding:8px;
  padding-bottom:calc(8px + env(safe-area-inset-bottom, 0px));
}
#in{
  flex:1; min-width:0; background:#1b1512; color:#ecdfc8;
  border:1px solid #493c2e; border-radius:4px; padding:9px 10px;
  font-family:inherit; font-size:14px;
}
#in:focus{outline:2px solid #d97a3a; outline-offset:1px;}
button{
  background:#d97a3a; color:#1b1512; border:none; border-radius:4px;
  padding:9px 16px; font-family:inherit; font-weight:bold; cursor:pointer;
}
button:hover{background:#f0a050;}
@media (max-width:520px){
  #rooms{width:96px;}
  header{font-size:13px;}
}
/* Language switcher, sitting just below the header. Small and out of
   the way -- used once per visit, not something that should compete
   with the actual conversation for attention. */
/* A real bar, not floating text: background + border-bottom matching
   header's own treatment, so this reads as a clearly separate band
   rather than blending into whatever comes next. Confirmed the bug
   this fixes directly: with no background/border and zero bottom
   padding, this used to sit with almost no visual clearance directly
   above #rooms -- itself a similarly dark, busy sidebar starting
   immediately below -- which is exactly what reads as the switcher
   "bleeding" into the room list on a narrow screen where everything
   stacks tightly. Padding is now symmetric top/bottom, not just top. */
.langbar{
  display:flex; gap:6px; padding:8px 12px; margin:0;
  background:#241d17; border-bottom:1px solid #493c2e;
}
.langbar a,.langbar span{
  min-width:36px; min-height:28px; display:flex; align-items:center;
  justify-content:center; padding:3px 9px; border-radius:6px;
  font-size:.72rem; font-weight:bold; text-decoration:none;
  border:1px solid #493c2e;
}
.langbar a{color:#9c8d76;}
.langbar a:hover,.langbar a:focus{color:#d97a3a; border-color:#d97a3a; outline:none;}
.langbar span.lang-active{background:#d97a3a; color:#1b1512; border-color:#d97a3a;}
"""

SCRIPT = """
var room=ROOM_INIT, lastId=0, nick=NICK_INIT, polling=false, pollAgain=false;
var log=document.getElementById('log');
// DM thread state -- purely client-side and session-scoped, since the
// server has no concept of "threads": it just delivers a flat inbox
// per recipient (rrc.py's _dms). Grouping by sender, tracking unread
// counts, and remembering which thread is currently open all happen
// here, not on the board.
var dmThreads={}, dmUnread={}, viewingDM=null;
var inp=document.getElementById('in');

// Escapes the five characters that matter in HTML. The previous
// version set textContent on a throwaway div and read back innerHTML,
// which neutralises < and > but leaves quotes alone. That is safe while
// every interpolation lands in text position, as they all currently do
// -- but the moment a value goes into an attribute (title="...", say)
// unescaped quotes become an injection point. Escaping here means that
// future change cannot silently open a hole.
function esc(s){
  return String(s).replace(/[&<>"']/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
  });
}

function line(html, cls){
  var p=document.createElement('p');
  if(cls) p.className=cls;
  p.innerHTML=html;
  log.appendChild(p);
  log.scrollTop=log.scrollHeight;
}

function render(m){
  if(m.kind==='dm'){
    var self=(m.nick===nick)?' self':'';
    line('&#8594; <span class="nick">'+esc(m.nick)+'</span> '+esc(m.body),'dm'+self);
    return;
  }
  if(m.kind==='system'){ line(esc(m.body),'system'); return; }
  if(m.kind==='action'){ line('* <span class="nick">'+esc(m.nick)+'</span> '+esc(m.body),'action'); return; }
  var self=(m.nick===nick)?' self':'';
  line('&lt;<span class="nick">'+esc(m.nick)+'</span>&gt; '+esc(m.body), 'msg'+self);
}

function setRooms(list, here){
  var box=document.getElementById('rooms');
  box.innerHTML='';
  list.forEach(function(r){
    var d=document.createElement('div');
    d.textContent='#'+r;
    if(!viewingDM && r===here) d.className='active';
    // Clicking a room always exits DM view, back to normal chat --
    // the two are mutually exclusive display modes, never both at once.
    d.onclick=function(){ viewingDM=null; send('/join '+r); };
    box.appendChild(d);
  });
  var dmBox=document.createElement('div');
  dmBox.id='dm-section';
  box.appendChild(dmBox);
  renderDMSidebar();
}

function renderDMSidebar(){
  // Its own nested container, rebuilt independently of the room list
  // above -- setRooms() only runs once per poll (when d.rooms is
  // present), but an unread count needs to update the instant a DM
  // arrives or a thread is opened, without waiting for or duplicating
  // the room list rebuild.
  var dmBox=document.getElementById('dm-section');
  if(!dmBox) return;
  dmBox.innerHTML='';
  var senders=Object.keys(dmThreads);
  if(senders.length===0) return;
  var hdr=document.createElement('div');
  hdr.className='dm-header';
  hdr.textContent='Direct Messages';
  dmBox.appendChild(hdr);
  senders.sort().forEach(function(s){
    var unread=dmUnread[s]||0;
    var d=document.createElement('div');
    d.className='dm-entry'+(viewingDM===s?' active':'')+(unread>0?' unread':'');
    d.textContent=s+(unread>0?' ('+unread+')':'');
    d.onclick=function(){ openDM(s); };
    dmBox.appendChild(d);
  });
}

function openDM(sender){
  viewingDM=sender;
  dmUnread[sender]=0;
  log.innerHTML='';
  (dmThreads[sender]||[]).forEach(function(m){ render(m); });
  renderDMSidebar();
}

function poll(){
  // Only one poll in flight at a time. send() kicks off a poll AND the
  // 2s interval keeps firing, so two requests could overlap -- and
  // because lastId is only updated once a response arrives, both would
  // ask for the same range and both would render the same messages.
  // That is what produced doubled lines in the room.
  // Queue rather than drop: a poll requested while one is in flight
  // (send() does exactly that) runs as soon as the current one lands,
  // so your own message still appears immediately instead of waiting
  // out the 2s interval.
  if(polling){ pollAgain=true; return; }
  polling=true;
  fetch('/rrc/poll?room='+encodeURIComponent(room)+'&since='+lastId)
   .then(function(r){return r.json();})
   .then(function(d){
     if(d.room && d.room!==room){
       room=d.room; lastId=0;
       if(!viewingDM){ log.innerHTML=''; }
     }
     // Room messages and DMs are no longer merged into one stream --
     // they render into two different places now (the room log vs. a
     // per-sender thread), so the ordering between them stopped
     // mattering the moment they stopped sharing a display. Each is
     // still processed in its own arrival order, id-guarded exactly
     // as before against a re-delivered or overlapping response.
     var maxId=lastId;
     (d.messages||[]).forEach(function(m){
       if(m.id>maxId) maxId=m.id;
       if(m.id<=lastId) return;
       if(!viewingDM) render(m);
     });
     (d.dms||[]).forEach(function(m){
       if(m.id>maxId) maxId=m.id;
       if(m.id<=lastId) return;
       var box=dmThreads[m.nick]=dmThreads[m.nick]||[];
       box.push(m);
       if(viewingDM===m.nick){ render(m); }
       else{ dmUnread[m.nick]=(dmUnread[m.nick]||0)+1; }
     });
     lastId=maxId;
     if(d.rooms) setRooms(d.rooms, room); else renderDMSidebar();
     if(d.topic!==undefined) document.getElementById('topic').textContent=d.topic?('— '+d.topic):'';
     if(d.nick && d.nick!==nick){ nick=d.nick; document.getElementById('me').textContent=nick; }
     onPollOk();
   })
   .catch(function(){ onPollFail(); })
   .then(function(){
     polling=false;
     if(pollAgain){ pollAgain=false; poll(); }
   });
}

var sending=false;
function setBusy(b){
  sending=b;
  inp.disabled=b;
  var go=document.getElementById('go');
  if(go) go.disabled=b;
  if(!b) inp.focus();
}

function send(text){
  if(!text) return;
  // Holding Enter repeats the keydown event, which without this fires a
  // POST per repeat -- hundreds a second, flooding the AP and, now that
  // rrc_mesh forwards room traffic, feeding the radio as well. The
  // control is released in the settle handler below whatever the
  // outcome, so a failed request cannot leave the box permanently dead.
  if(sending) return;
  setBusy(true);
  // While a DM thread is open, a plain line (no leading /) is sent as
  // a reply to that thread rather than posted to whatever room the
  // server still has you in -- typing and hitting Enter should just
  // work, the way replying in any chat app does, without retyping
  // "/msg <name>" every single line. A command (still starting with
  // /) is left alone and goes to the server exactly as typed.
  var isDMReply=(viewingDM && text.charAt(0)!=='/');
  var outgoing=isDMReply ? ('/msg '+viewingDM+' '+text) : text;
  fetch('/rrc/send',{method:'POST',body:outgoing})
   .then(function(r){return r.json();})
   .then(function(d){
     if(isDMReply){
       // The server only ever delivers a DM to its RECIPIENT's inbox
       // (rrc.py's _dms is keyed by recipient, never the sender) -- so
       // without echoing it here directly, the sender would never see
       // their own half of the conversation in the thread view at all,
       // confirmed by reading send_dm()'s actual storage target.
       var box=dmThreads[viewingDM]=dmThreads[viewingDM]||[];
       var mine={id:0, nick:nick, body:text, kind:'dm'};
       box.push(mine);
       render(mine);
     } else {
       (d.replies||[]).forEach(function(t){
         if(t==='__CLEAR__'){ log.innerHTML=''; return; }
         line(esc(t),'local');
       });
     }
     if(d.room && d.room!==room){
       room=d.room; lastId=0;
       if(!viewingDM){ log.innerHTML=''; line('now in #'+room,'local'); }
     }
     poll();
   })
   .catch(function(){ line('not sent — connection problem','err'); })
   .then(function(){ setBusy(false); });
}

inp.addEventListener('keydown',function(e){
  if(e.key==='Enter'){ var v=inp.value; inp.value=''; send(v); }
});
document.getElementById('go').onclick=function(){ var v=inp.value; inp.value=''; send(v); };

// Recursive timeout rather than a fixed setInterval.
//
// A fixed interval means that when the node reboots, every connected
// browser fails at once and then retries in lockstep forever after --
// all of them hitting the AP on the same tick, exactly when it is least
// able to cope. Backing off on failure spreads that out.
//
// The jitter matters as much as the backoff: without it, clients that
// failed together stay synchronised and simply collide on a slower
// beat. A random spread breaks that lockstep.
var POLL_BASE=2000, POLL_MAX=30000, pollDelay=POLL_BASE;

function onPollOk(){ pollDelay=POLL_BASE; }
function onPollFail(){
  pollDelay=Math.min(pollDelay*2, POLL_MAX);
}
function schedulePoll(){
  var jitter=Math.floor(Math.random()*(pollDelay*0.3));
  setTimeout(pollLoop, pollDelay+jitter);
}
function pollLoop(){ poll(); schedulePoll(); }

schedulePoll();
poll();
inp.focus();
"""


def _js(value):
    """Serialises a Python value for embedding inside a <script> block.

    json.dumps alone is NOT enough here. It produces correct JavaScript,
    but a string containing '</script>' terminates the <script> ELEMENT
    during HTML parsing -- which happens before any JavaScript is
    evaluated, so JS-level quoting never gets a say. Escaping the slash
    ('<\\/') is valid inside a JS string and stops the tag closing early.

    Reachable today only if clean_nick's allowlist ever admits '<' or
    '/', which it doesn't -- but this function shouldn't depend on a
    rule enforced in a different module to stay safe."""
    return json.dumps(value).replace("</", "<\\/")


# Small nav glyphs for the header. Inline SVG, like the tiles on the
# BarKeep page -- anyone on the Stump's own AP has no route to a CDN.
_NAV_HOME = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M3 10.5 12 3l9 7.5'/>"
    "<path d='M5.5 9.5V20h13V9.5'/></svg>"
)
_NAV_TOOLS = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M14.5 6.5a3.5 3.5 0 0 0 4.6 4.6l-7.2 7.2"
    "a2.3 2.3 0 0 1-3.2-3.2z'/><path d='M14.5 6.5 17 4l3 3-2.5 2.5'/></svg>"
)
_NAV_FILES = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><path d='M4 6.5A1.5 1.5 0 0 1 5.5 5h4L11 7h7.5"
    "A1.5 1.5 0 0 1 20 8.5v9A1.5 1.5 0 0 1 18.5 19h-13A1.5 1.5 0 0 1 4 17.5z'/>"
    "</svg>"
)
_NAV_BOARD = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><rect x='3' y='4' width='18' height='15' rx='1.5'/>"
    "<path d='M3 8h18M12 19v2M8 21h8'/></svg>"
)
_NAV_ABOUT = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><circle cx='12' cy='12' r='9'/>"
    "<path d='M12 11v5.5'/><circle cx='12' cy='7.7' r='.15' fill='currentColor' "
    "stroke-width='1.4'/></svg>"
)

# The way OUT of the chat.
#
# A kiosk browser has no back button, no address bar and no tabs, so a
# page with no outbound link is a dead end -- the unit has to be
# physically restarted to leave. These are the escape hatches, and they
# sit in the header where they stay reachable no matter how far the
# conversation has scrolled.
#
# Sized for a finger, not a mouse: 44px is the smallest reliable touch
# target, and this runs on a wall-mounted resistive panel.
def _nav_links(lang):
    """Was a module-level constant with hardcoded English labels, built
    once at import time -- converted to a function for the same reason
    barkeep.py's nav tiles were: labels now depend on who's asking, so
    this has to render fresh per request rather than once at boot."""
    return (
        "<nav class='nav'>"
        "<a href='/' title='Home' aria-label='Home'>" + _NAV_HOME +
        "<span>" + i18n.t("nav_home", lang) + "</span></a>"
        "<a href='/billboard' title='Billboard' aria-label='Billboard'>" + _NAV_BOARD +
        "<span>" + i18n.t("nav_board", lang) + "</span></a>"
        "<a href='/files' title='Files' aria-label='Files'>" + _NAV_FILES +
        "<span>" + i18n.t("nav_files", lang) + "</span></a>"
        "<a href='/tools' title='Tools' aria-label='Tools'>" + _NAV_TOOLS +
        "<span>" + i18n.t("nav_tools", lang) + "</span></a>"
        "<a href='/about' title='About' aria-label='About'>" + _NAV_ABOUT +
        "<span>" + i18n.t("nav_about", lang) + "</span></a>"
        "</nav>"
    )


def _esc(s):
    """HTML-escapes text going into markup.

    The nick below is already restricted by rrc.clean_nick's allowlist,
    which currently excludes every HTML-special character -- but that
    allowlist lives in a different module. Relying on it means a future
    edit there (allowing apostrophes, say, which IRC sometimes does)
    would silently open an injection here, in a file nobody thought to
    re-check. Escaping at the point of use removes that coupling."""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def render_page(room, nick, lang=None):
    """The whole client, one self-contained page.

    room/nick go through json.dumps, not hand-written quotes: clean_nick
    deliberately allows backslash (an IRC-traditional nick character),
    and a nick ending in one would escape the closing quote of the JS
    string literal and break the entire client script. That failure is
    unrecoverable from the user's side -- the nick lives server-side, so
    the only tool for changing it back is the page that just broke.
    JSON string syntax is a guaranteed-valid subset of JS syntax with
    correct escaping already handled.
    """
    if lang is None:
        lang = i18n.DEFAULT_LANG
    script = (SCRIPT
              .replace("ROOM_INIT", _js(room))
              .replace("NICK_INIT", _js(nick)))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>RRC — Stump</title><style>" + STYLE + "</style></head><body>"
        "<header><b>RRC</b><span id='topic'></span>"
        "<span class='me'>" + i18n.t("rrc_you_are", lang) + " <span id='me'>" + _esc(nick) + "</span></span>"
        + _nav_links(lang) + "</header>"
        + i18n.switcher_html(lang, "/rrc") +
        "<main><div id='rooms'></div><div id='log'></div></main>"
        "<footer>"
        "<input id='in' maxlength='" + str(rrc.MAX_MESSAGE_LEN) + "' "
        "autocomplete='off' autocapitalize='none' spellcheck='false' "
        "placeholder='" + i18n.t("rrc_input_placeholder", lang) + "'>"
        "<button id='go'>" + i18n.t("rrc_send", lang) + "</button>"
        "</footer>"
        "<script>" + script + "</script>"
        "</body></html>"
    )
