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
"""

SCRIPT = """
var room=ROOM_INIT, lastId=0, nick=NICK_INIT, polling=false, pollAgain=false;
var log=document.getElementById('log');
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
    line('&#8594; <span class="nick">'+esc(m.nick)+'</span> '+esc(m.body),'dm');
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
    if(r===here) d.className='active';
    d.onclick=function(){ send('/join '+r); };
    box.appendChild(d);
  });
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
     if(d.room && d.room!==room){ room=d.room; lastId=0; log.innerHTML=''; }
     // Room messages and private ones share the id sequence, so merging
     // and sorting shows them in the order they actually happened rather
     // than in two separate clumps.
     var all=(d.messages||[]).concat(d.dms||[]);
     all.sort(function(a,b){return a.id-b.id;});
     all.forEach(function(m){
       // Second line of defence: never render an id already shown, so
       // even an overlapping response can't duplicate anything.
       if(m.id<=lastId) return;
       render(m);
       lastId=m.id;
     });
     if(d.rooms) setRooms(d.rooms, room);
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
  fetch('/rrc/send',{method:'POST',body:text})
   .then(function(r){return r.json();})
   .then(function(d){
     (d.replies||[]).forEach(function(t){
       if(t==='__CLEAR__'){ log.innerHTML=''; return; }
       line(esc(t),'local');
     });
     if(d.room && d.room!==room){
       room=d.room; lastId=0; log.innerHTML='';
       line('now in #'+room,'local');
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
_NAV_BOARD = (
    "<svg viewBox='0 0 24 24' width='20' height='20' fill='none' "
    "stroke='currentColor' stroke-width='1.8' stroke-linecap='round' "
    "stroke-linejoin='round'><rect x='3' y='4' width='18' height='15' rx='1.5'/>"
    "<path d='M3 8h18M12 19v2M8 21h8'/></svg>"
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
NAV_LINKS = (
    "<nav class='nav'>"
    "<a href='/' title='Home' aria-label='Home'>" + _NAV_HOME + "<span>Home</span></a>"
    "<a href='/billboard' title='Billboard' aria-label='Billboard'>" + _NAV_BOARD +
    "<span>Board</span></a>"
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


def render_page(room, nick):
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
    script = (SCRIPT
              .replace("ROOM_INIT", _js(room))
              .replace("NICK_INIT", _js(nick)))
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>RRC — Stump</title><style>" + STYLE + "</style></head><body>"
        "<header><b>RRC</b><span id='topic'></span>"
        "<span class='me'>you are <span id='me'>" + _esc(nick) + "</span></span>"
        + NAV_LINKS + "</header>"
        "<main><div id='rooms'></div><div id='log'></div></main>"
        "<footer>"
        "<input id='in' maxlength='" + str(rrc.MAX_MESSAGE_LEN) + "' "
        "autocomplete='off' autocapitalize='none' spellcheck='false' "
        "placeholder='message, or /help'>"
        "<button id='go'>Send</button>"
        "</footer>"
        "<script>" + script + "</script>"
        "</body></html>"
    )
