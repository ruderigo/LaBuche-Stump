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
import theme
import features
import i18n

STYLE = """
*{box-sizing:border-box;}
body{
  background:var(--bg); color:var(--text); margin:0;
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
  padding:8px 12px; border-bottom:1px solid var(--border); background:var(--panel);
  display:flex; align-items:baseline; gap:10px; flex-wrap:wrap;
}
header b{color:var(--ember);}
#topic{color:var(--muted); font-size:12px; flex:1; min-width:0;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
#me{color:var(--ember-bright);}
.me{white-space:nowrap;}
/* Escape hatches. Touch-sized (44px min) because the kiosk is a
   wall-mounted panel operated with a finger, not a cursor. */
.nav{display:flex; gap:6px; margin-left:auto;}
.nav a{
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  min-width:44px; min-height:44px; gap:2px; padding:4px 8px;
  color:var(--ember); text-decoration:none; border:1px solid var(--border);
  border-radius:6px; background:var(--panel-2);
}
.nav a:hover,.nav a:active,.nav a:focus{
  color:var(--ember-bright); border-color:var(--ember); outline:none;
}
main{flex:1; display:flex; min-height:0;}
#rooms{
  width:132px; border-right:1px solid var(--border); background:var(--panel-2);
  overflow-y:auto; flex-shrink:0;
}
#rooms div{padding:7px 10px; cursor:pointer; border-bottom:1px solid var(--line);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
#rooms div:hover{background:var(--line);}
#rooms div.active{background:var(--ember); color:var(--bg); font-weight:bold;}
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
  color:var(--dim); border-bottom:none !important;
}
#rooms div.dm-entry.unread{color:var(--ember-bright); font-weight:bold;}
.tag{font-size:.72em; color:var(--muted); border:1px solid var(--border); border-radius:4px;
  padding:0 4px; margin-left:6px; font-weight:normal; vertical-align:middle;}
#log{flex:1; overflow-y:auto; padding:10px 12px; line-height:1.5;}
#log p{margin:0 0 3px; overflow-wrap:break-word; word-break:break-word;}
.nick{color:var(--ember);}
.self .nick{color:var(--ember-bright);}
.system{color:var(--dim); font-style:italic;}
.action{color:var(--action); font-style:italic;}
.local{color:var(--dim);}
/* Private messages, visually distinct from room traffic so nobody
   mistakes one for something the whole room can see. */
.dm{color:var(--dm);}
.dm .nick{color:var(--dm-nick);}
.err{color:var(--err);}
footer{
  border-top:1px solid var(--border); background:var(--panel);
  display:flex; gap:8px;
  /* Pad past the home-indicator / gesture area on devices that report
     one, so the input isn't flush against a bar the user can't move. */
  padding:8px;
  padding-bottom:calc(8px + env(safe-area-inset-bottom, 0px));
}
#in{
  flex:1; min-width:0; background:var(--bg); color:var(--text);
  border:1px solid var(--border); border-radius:4px; padding:9px 10px;
  font-family:inherit; font-size:14px;
}
#in:focus{outline:2px solid var(--ember); outline-offset:1px;}
#voice.rec{background:var(--err); color:var(--bg);}
button.play{font-size:.85em; padding:0 8px; margin-left:4px; cursor:pointer;}
button{
  background:var(--ember); color:var(--bg); border:none; border-radius:4px;
  padding:9px 16px; font-family:inherit; font-weight:bold; cursor:pointer;
}
button:hover{background:var(--ember-bright);}
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
  background:var(--panel-2); border-bottom:1px solid var(--border);
}
.langbar a,.langbar span{
  min-width:36px; min-height:28px; display:flex; align-items:center;
  justify-content:center; padding:3px 9px; border-radius:6px;
  font-size:.72rem; font-weight:bold; text-decoration:none;
  border:1px solid var(--border);
}
.langbar a{color:var(--muted);}
.langbar a:hover,.langbar a:focus{color:var(--ember); border-color:var(--ember); outline:none;}
.langbar span.lang-active{background:var(--ember); color:var(--bg); border-color:var(--ember);}
"""

SCRIPT = """
var room=ROOM_INIT, lastId=0, nick=NICK_INIT, polling=false, pollAgain=false;
var log=document.getElementById('log');
// DM thread state -- purely client-side and session-scoped, since the
// server has no concept of "threads": it just delivers a flat inbox
// per recipient (rrc.py's _dms). Grouping by sender, tracking unread
// counts, and remembering which thread is currently open all happen
// here, not on the board.
var dmThreads={}, dmUnread={}, viewingDM=null, lastIdBeforeDM=null;
// Nicks the server knows are other Stump nodes (their stump.node beacons).
var stumps={};
// Ids of DMs already placed in a thread (see the poll handler).
var dmSeen={};
// A thread you started by typing a name ("/msg BOB") is keyed as typed;
// the server matches names case-insensitively, so the reply comes from
// "Bob". When it does, fold the typed-case thread into the sender's
// exact name, so one conversation stays one thread.
function adoptThread(exact){
  var low=exact.toLowerCase();
  for(var k in dmThreads){
    if(k!==exact && k.toLowerCase()===low){
      dmThreads[exact]=(dmThreads[exact]||[]).concat(dmThreads[k]);
      delete dmThreads[k];
      if(dmUnread[k]){ dmUnread[exact]=(dmUnread[exact]||0)+dmUnread[k]; delete dmUnread[k]; }
      if(viewingDM===k) viewingDM=exact;
    }
  }
}
function stumpTag(el, name){
  // A separate element, never part of the name: the name is what
  // /msg and openDM use, so it has to stay exactly the nick.
  if(!stumps[name]) return;
  var t=document.createElement('span');
  t.className='tag';
  t.textContent='stump';
  el.appendChild(t);
}
var inp=document.getElementById('in');
// The most recent user list from a poll, in each person's own
// server-registered case. find_client_by_nick() on the server is
// deliberately case-insensitive (so a DM to "BOB" still reaches
// "Bob"), but dmThreads here is a plain object keyed by whatever
// string was actually used -- typing "/msg BOB hello" would key the
// sent side "BOB" while Bob's own reply arrives with m.nick "Bob",
// splitting one conversation into two separate sidebar entries.
// Resolving a typed target against this list before using it as a key
// keeps both sides of a conversation under the one case Bob actually
// registered with. Confirmed directly: without this, typing a
// different case than someone's real nick reproduces exactly that
// split-thread symptom.
var knownUsers=[];

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

// ---- Voice notes ----
// Codec 2 1200 (LXMF mode 4), the same notes FireFly sends (its voice-note
// spec). Two ways to record, one pipeline after that:
//  - On a secure (HTTPS) page -- a node with a certificate, see
//    docs/HTTPS_SETUP.md -- the microphone is recorded live (getUserMedia
//    + MediaRecorder): tap ♪, tap ■ to send.
//  - On plain HTTP, or if microphone access is refused: HTML Media
//    Capture, <input type=file accept=audio/* capture>, which hands the
//    job to the device's own recorder and gives back a file. On iPhone,
//    Safari may offer a file picker instead of a recorder.
// Either way the audio is decoded, brought to 8 kHz, levelled and encoded
// here, then sent as raw Codec 2 frames to /rrc/voice: small enough for
// LoRa, and playable by FireFly.
// Notes are sent in Opus (LXMF mode 16, an Ogg Opus file), FireFly's
// default; Codec 2 notes (modes 3-9) from others still play.
var VOICE=I18N_VOICE, VOICE_MODE=16, VOICE_RATE=16000, VOICE_MAX_S=15, VOICE_MIN_S=0.6;
var voiceCtx=null, voiceBusy=false, voiceRec=null, voiceChunks=[], voiceTimer=null;
var vbtn=document.getElementById('voice'), vfile=document.getElementById('voicefile');
function updateVoiceButton(){ vbtn.style.display = viewingDM ? '' : 'none'; }
function liveMic(){
  return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
}
function voiceState(busy){
  voiceBusy=busy; vbtn.disabled=busy; vbtn.textContent = busy ? '\u22ef' : '\u266a';
}
function readFile(f){
  // File.arrayBuffer() is missing on older iPhones (Safari < 14).
  if(f.arrayBuffer) return f.arrayBuffer();
  return new Promise(function(res, rej){
    var r=new FileReader(); r.onload=function(){ res(r.result); }; r.onerror=rej; r.readAsArrayBuffer(f);
  });
}
function audioCtx(){
  if(!voiceCtx){ var C=window.AudioContext||window.webkitAudioContext; voiceCtx=new C(); }
  if(voiceCtx.state==='suspended') voiceCtx.resume();
  return voiceCtx;
}
function decodeAny(buf){
  // Callback form: older Safari has no promise-returning decodeAudioData.
  var ctx=audioCtx();
  return new Promise(function(res, rej){ ctx.decodeAudioData(buf, res, rej); });
}
// Any recording -> 8 kHz 16-bit mono, per FireFly's spec: windowed-sinc
// low-pass at 3.6 kHz while resampling (no folding of >4 kHz sound into
// the voice band), DC removed (~60 Hz high-pass), peak brought to ~70 %
// with at most 4x gain, capped at 15 s. null if shorter than 0.6 s.
function prepVoice(ab, rate){
  rate = rate || VOICE_RATE;
  var sr=ab.sampleRate, ch=ab.numberOfChannels, n=Math.min(ab.length, Math.floor(VOICE_MAX_S*sr));
  var x=new Float32Array(n), c, i;
  for(c=0;c<ch;c++){ var d=ab.getChannelData(c); for(i=0;i<n;i++) x[i]+=d[i]/ch; }
  var ratio=sr/rate, outN=Math.floor(n/ratio);
  if(outN < VOICE_MIN_S*rate) return null;
  // Low-pass at 0.45 x the target rate: 3.6 kHz for 8 kHz (Codec 2), 7.2 kHz
  // for 16 kHz (Opus) -- nothing above the new Nyquist folds back.
  var fc=Math.min(0.45*rate, sr*0.45)/sr, H=Math.ceil(4/fc), y=new Float32Array(outN), k, j;
  for(k=0;k<outN;k++){
    var t=k*ratio, j0=Math.floor(t), acc=0;
    for(j=j0-H+1;j<=j0+H;j++){
      // Past either end, the edge sample is repeated rather than the tap
      // dropped: dropped taps made the first and last samples ramp from
      // half level, a step the DC filter and levelling then mistook for
      // signal.
      var xj=x[j<0?0:(j>=n?n-1:j)];
      var u=t-j, v=u/H, w=0.42+0.5*Math.cos(Math.PI*v)+0.08*Math.cos(2*Math.PI*v);
      var s=(u===0)?1:Math.sin(2*Math.PI*fc*u)/(2*Math.PI*fc*u);
      acc+=xj*2*fc*s*w;
    }
    y[k]=acc;
  }
  // The high-pass starts from the first sample, not from zero: from zero,
  // a DC offset arrives as a step, its transient became the "peak", and
  // levelling then left the actual speech far too quiet.
  var a=Math.exp(-2*Math.PI*60/rate), prevX=outN?y[0]:0, prevY=0, peak=0;
  for(k=0;k<outN;k++){ var hp=a*(prevY+y[k]-prevX); prevX=y[k]; prevY=hp; y[k]=hp; peak=Math.max(peak, Math.abs(hp)); }
  var gain=peak>0 ? Math.min(4, 0.7/peak) : 1, out=new Int16Array(outN);
  for(k=0;k<outN;k++){ var z=Math.max(-1, Math.min(1, y[k]*gain)); out[k]=Math.round(z*32767); }
  return out;
}
function sendVoiceFrom(buf){
  var to=viewingDM;
  if(!to) return;
  voiceState(true);
  var decoded=decodeAny(buf).then(null, function(){ throw 'unreadable'; });
  Promise.all([decoded, Opus.load('/opus.wasm')]).then(function(r){
    var pcm=prepVoice(r[0], VOICE_RATE);
    if(!pcm){ line(esc(VOICE.length),'local'); voiceState(false); return; }
    var bits=Opus.encode(pcm);
    return fetch('/rrc/voice?to='+encodeURIComponent(to)+'&mode='+VOICE_MODE, {method:'POST', body:bits})
      .then(function(r){ return r.json(); })
      .then(function(d){
        var mineTag='[DM] <'+nick+'>: ', ok=null;
        (d.replies||[]).forEach(function(t){ if(ok===null && t.indexOf(mineTag)===0) ok=t; });
        if(ok!==null){
          var m={id:0, nick:nick, body:ok.slice(mineTag.length), kind:'dm', local:{mode:VOICE_MODE, bits:bits}};
          var box=dmThreads[to]=dmThreads[to]||[]; box.push(m);
          if(viewingDM===to) render(m);
        } else {
          (d.replies||[]).forEach(function(t){ labelled(line(esc(t),'local'), t.charAt(0)); });
        }
        voiceState(false);
      });
  }).catch(function(e){
    // A recording the browser can't decode is not a connection problem.
    line(esc(e==='unreadable' ? VOICE.unreadable : I18N_NOT_SENT),'err');
    voiceState(false);
  });
}
function voiceFailed(){ line(esc(VOICE.unreadable),'err'); voiceState(false); }
vbtn.onclick=function(){
  if(voiceRec){ voiceRec.stop(); return; }
  if(voiceBusy) return;
  if(!liveMic()){ vfile.click(); return; }
  navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
    voiceChunks=[]; voiceRec=new MediaRecorder(stream);
    voiceRec.ondataavailable=function(e){ if(e.data && e.data.size) voiceChunks.push(e.data); };
    voiceRec.onstop=function(){
      stream.getTracks().forEach(function(t){ t.stop(); });
      clearTimeout(voiceTimer); voiceRec=null;
      vbtn.className=''; vbtn.title=VOICE.record;
      readFile(new Blob(voiceChunks)).then(sendVoiceFrom, voiceFailed);
    };
    voiceRec.start(); vbtn.textContent='\u25a0'; vbtn.className='rec'; vbtn.title=VOICE.recording;
    voiceTimer=setTimeout(function(){ if(voiceRec) voiceRec.stop(); }, VOICE_MAX_S*1000);
  }, function(){ vfile.click(); });   // permission refused or no microphone: the recorder app instead
};
vfile.onchange=function(){
  var f=vfile.files[0]; vfile.value='';
  if(f) readFile(f).then(sendVoiceFrom, voiceFailed);
};
function addPlay(p, m){
  var mode=m.local ? m.local.mode : m.voice.mode;
  if([3,4,5,6,7,8,9,16].indexOf(mode)<0){
    var u=document.createElement('small'); u.textContent=' ('+VOICE.unplayable+')'; p.appendChild(u); return;
  }
  var b=document.createElement('button'); b.className='play'; b.textContent='\u25b6'; b.title=VOICE.record;
  b.onclick=function(){
    var ctx=audioCtx();   // created inside the tap, so playback is allowed
    var bits=m.local ? Promise.resolve(m.local.bits)
      : fetch('/rrc/voice?id='+m.id).then(function(r){ if(!r.ok) throw 0; return r.arrayBuffer(); })
          .then(function(a){ return new Uint8Array(a); });
    var opus=(mode===16), codec=opus ? Opus.load('/opus.wasm') : Codec2.load('/codec2.wasm');
    Promise.all([codec, bits]).then(function(r){
      // Opus decodes at 48 kHz (every browser takes that rate); Codec 2 at 8 kHz.
      var pcm=opus ? Opus.decode(r[1]) : Codec2.decode(mode, r[1]), rate=opus ? 48000 : 8000, buf, f, i;
      try { buf=ctx.createBuffer(1, pcm.length, rate); f=buf.getChannelData(0); for(i=0;i<pcm.length;i++) f[i]=pcm[i]/32768; }
      catch(e){
        // Browsers without 8 kHz buffers: upsample to the context's rate.
        var R=ctx.sampleRate, n=Math.floor(pcm.length*R/rate);
        buf=ctx.createBuffer(1, n, R); f=buf.getChannelData(0);
        for(i=0;i<n;i++){ var t=i*rate/R, j=Math.floor(t), a=t-j; f[i]=((pcm[j]||0)*(1-a)+(pcm[j+1]||0)*a)/32768; }
      }
      var src=ctx.createBufferSource(); src.buffer=buf; src.connect(ctx.destination); src.start(0);
    }).catch(function(){ line(esc(VOICE.unplayable),'err'); });
  };
  p.appendChild(document.createTextNode(' ')); p.appendChild(b);
}

function line(html, cls){
  var p=document.createElement('p');
  if(cls) p.className=cls;
  p.innerHTML=html;
  log.appendChild(p);
  log.scrollTop=log.scrollHeight;
  return p;
}
// Room activity and DMs are symbols (rrc.ev_*), the same for every
// reader. Each symbol line also gets a label in THIS reader's language,
// for the tooltip and for screen readers, which would otherwise say
// "check mark" or "circled minus".
var SYMBOLS=I18N_SYMBOLS;
function labelled(p, glyph){
  var t=SYMBOLS[glyph];
  if(t){ p.title=t; p.setAttribute('aria-label', t+': '+p.textContent.slice(glyph.length).trim()); }
  return p;
}

function render(m){
  if(m.kind==='dm'){
    // "[DM] <author>: text", always naming who wrote it -- the thread
    // reads as a conversation (rrc.dm_line builds the same line).
    var mine=(m.nick===nick);
    var p=labelled(line('[DM] <span class="nick">&lt;'+esc(m.nick)+'&gt;</span>: '+esc(m.body),'dm'+(mine?' self':'')), '[DM]');
    if(m.voice||m.local) addPlay(p, m);
    return;
  }
  if(m.kind==='system'){ labelled(line(esc(m.body),'system'), m.body.charAt(0)); return; }
  if(m.kind==='action'){ line('* <span class="nick">'+esc(m.nick)+'</span> '+esc(m.body),'action'); return; }
  var self=(m.nick===nick)?' self':'';
  line('&lt;<span class="nick">'+esc(m.nick)+'</span>&gt; '+esc(m.body), 'msg'+self);
}

function setRooms(list, here, users){
  knownUsers=users||[];
  var box=document.getElementById('rooms');
  box.innerHTML='';
  list.forEach(function(r){
    var d=document.createElement('div');
    d.textContent='#'+r;
    if(!viewingDM && r===here) d.className='active';
    // Clicking a room the server already has you in -- true for EVERY
    // room click while viewing a DM, since opening one never actually
    // changes your room server-side -- used to still send /join
    // unconditionally. The server correctly replied "you're already in
    // #room" (join_already in rrc.py), which is true but confusing
    // right after leaving a DM, and because the log-clearing below only
    // ever triggered on an ACTUAL room change (never true here), that
    // reply landed straight on top of whatever the DM thread had left
    // in the log -- confirmed as the real, reported cause of DM
    // content appearing to persist into #main. Exiting DM view back to
    // the SAME room is purely a local display change now: clear the
    // log directly, restore lastId to what it was before the DM opened
    // (advanced silently by messages that arrived and were correctly
    // skipped from rendering while viewingDM was set, but never
    // actually shown -- restoring it re-fetches them on the next poll
    // instead of leaving them permanently missed), and re-poll, with no
    // /join sent to the server at all. A genuine room change (r is
    // NOT the one the server already has you in) still sends /join
    // exactly as before.
    d.onclick=function(){
      var wasViewingDM=!!viewingDM;
      viewingDM=null;
      updateVoiceButton();
      if(r===room){
        if(wasViewingDM){
          log.innerHTML='';
          if(lastIdBeforeDM!==null){ lastId=lastIdBeforeDM; lastIdBeforeDM=null; }
          renderDMSidebar();
          poll();
        }
      } else {
        send('/join '+r);
      }
    };
    box.appendChild(d);
  });
  renderUserList(users||[]);
  var dmBox=document.createElement('div');
  dmBox.id='dm-section';
  box.appendChild(dmBox);
  renderDMSidebar();
}

function renderUserList(users){
  // "Select someone and DM them" -- clicking a name here calls the
  // SAME openDM() the "Direct Messages" section below already uses to
  // reopen an existing thread. That reuse is what makes the first
  // message to someone land in the same place as every message after
  // it: opening the thread FIRST (by clicking a name, here or there)
  // means viewingDM is already set by the time anything is typed, so
  // the plain-line-while-viewingDM path in send() handles it -- the
  // same path a second or third message already went through. The
  // separate fix in send() below covers the OTHER way to start a
  // thread (typing /msg directly without clicking anyone first), so
  // both roads into a conversation end up in the same place.
  var here=document.getElementById('user-section');
  if(here) here.remove();
  var box=document.getElementById('rooms');
  // Excludes both yourself AND anyone already in dmThreads -- confirmed
  // from a real screenshot that showing the same name in both "Message
  // someone" and "Direct Messages" at once reads as a duplicate, not
  // two different actions. Someone you already have a thread with is
  // reachable from that thread already; this list is specifically for
  // starting a NEW one.
  var others=(users||[]).filter(function(u){ return u!==nick && !dmThreads[u]; });
  if(others.length===0) return;
  var sec=document.createElement('div');
  sec.id='user-section';
  var hdr=document.createElement('div');
  hdr.className='dm-header';
  hdr.textContent=I18N_MESSAGE_SOMEONE;
  sec.appendChild(hdr);
  others.forEach(function(u){
    var d=document.createElement('div');
    d.className='dm-entry'+(viewingDM===u?' active':'');
    d.textContent=u;
    stumpTag(d, u);
    d.onclick=function(){ openDM(u); };
    sec.appendChild(d);
  });
  box.appendChild(sec);
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
  hdr.textContent=I18N_DIRECT_MESSAGES;
  dmBox.appendChild(hdr);
  senders.sort().forEach(function(s){
    var unread=dmUnread[s]||0;
    var d=document.createElement('div');
    d.className='dm-entry'+(viewingDM===s?' active':'')+(unread>0?' unread':'');
    d.textContent=s+(unread>0?' ('+unread+')':'');
    stumpTag(d, s);
    d.onclick=function(){ openDM(s); };
    dmBox.appendChild(d);
  });
}

function openDM(sender){
  // Only remember lastId the FIRST time DM view is entered (not on
  // every switch between two open threads) -- so #main round-trips
  // back to whatever lastId was before ANY DM was opened, not
  // whichever thread happened to be open most recently.
  if(!viewingDM){ lastIdBeforeDM=lastId; }
  viewingDM=sender;
  dmUnread[sender]=0;
  updateVoiceButton();
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
       // DMs are remembered by id, not by lastId alone: switching rooms
       // resets lastId to 0 to load the new room's history, and since
       // DMs share the same id sequence, the server rightly sends every
       // DM again -- each was being added to its thread (and counted as
       // unread) a second time. Reported and reproduced: DM received in
       // #lxmf, switch to #main, open the DM -> shown twice.
       if(m.id<=lastId || dmSeen[m.id]) return;
       dmSeen[m.id]=1;
       adoptThread(m.nick);
       var box=dmThreads[m.nick]=dmThreads[m.nick]||[];
       box.push(m);
       if(viewingDM===m.nick){ render(m); }
       else{ dmUnread[m.nick]=(dmUnread[m.nick]||0)+1; }
     });
     lastId=maxId;
     if(d.stumps){ stumps={}; d.stumps.forEach(function(n){ stumps[n]=1; }); }
     if(d.rooms) setRooms(d.rooms, room, d.users); else renderDMSidebar();
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
  // Typing /msg (or its /m, /w aliases) directly is ALSO how a
  // conversation starts, not just clicking a name in the sidebar first
  // -- recognized here so that path lands in the same place too. Match
  // mirrors rrc.py's own parsing exactly (arg.split(" ", 1) there,
  // same nick-then-rest-of-line shape here) so what this recognizes as
  // a DM is exactly what the server will actually treat as one.
  var directMsgMatch=(!isDMReply) && text.match(/^\/(?:msg|m|w)\s+(\S+)\s+([\s\S]+)/i);
  var outgoing=isDMReply ? ('/msg '+viewingDM+' '+text) : text;
  fetch('/rrc/send',{method:'POST',body:outgoing})
   .then(function(r){return r.json();})
   .then(function(d){
     // A successful /msg is answered with your own DM line, "[DM] <you>:
     // text", the same in every language (rrc.dm_line); anything else is
     // a failure to show as is ("⊖ name", usage...). Before, the reply to a line typed in
     // a thread was ignored outright, so a DM to someone who had left
     // looked sent; and a /msg drew your copy AND printed the reply.
     // The server only stores a DM for its RECIPIENT, so your own half
     // of a thread is drawn here, on success.
     var mineTag='[DM] <'+nick+'>: ';
     var sent=(d.replies||[]).some(function(t){ return t.indexOf(mineTag)===0; });
     if(isDMReply || directMsgMatch){
       var target=isDMReply ? viewingDM : directMsgMatch[1];
       var msgBody=isDMReply ? text : directMsgMatch[2].trim();
       // The server matches the name case-insensitively; resolving it
       // against the names this page knows keeps "/msg BOB" and Bob's
       // replies in one thread.
       if(!isDMReply){
         for(var k=0;k<knownUsers.length;k++){
           if(knownUsers[k].toLowerCase()===target.replace(/^~/,'').toLowerCase()){ target=knownUsers[k]; break; }
         }
       }
       if(sent){
         if(!isDMReply) openDM(target);
         var box=dmThreads[target]=dmThreads[target]||[];
         var mine={id:0, nick:nick, body:msgBody, kind:'dm'};
         box.push(mine);
         render(mine);
       } else {
         (d.replies||[]).forEach(function(t){
           if(t==='__CLEAR__'){ log.innerHTML=''; return; }
           labelled(line(esc(t),'local'), t.charAt(0));
         });
       }
     } else {
       (d.replies||[]).forEach(function(t){
         if(t==='__CLEAR__'){ log.innerHTML=''; return; }
         labelled(line(esc(t),'local'), t.charAt(0));
       });
     }
     if(d.room && d.room!==room){
       room=d.room; lastId=0;
       if(!viewingDM){ log.innerHTML=''; labelled(line('\u2192 #'+esc(room),'local'), '\u2192'); }
     }
     poll();
   })
   .catch(function(){ line(esc(I18N_NOT_SENT),'err'); })
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


def _attr(text):
    """Escapes text for a single-quoted HTML attribute (an apostrophe in
    a French label would otherwise end the attribute early)."""
    return (text.replace("&", "&amp;").replace("'", "&#39;")
                .replace("<", "&lt;").replace(">", "&gt;"))


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
    # Only features this node offers (features.py); home and tools always.
    # The same order as every other page's row (barkeep.NAV_ORDER: chat,
    # billboard, files, tools, about, home), minus chat itself.
    items = (
        ("billboard", "/billboard", "Billboard", _NAV_BOARD, "nav_board"),
        ("files", "/files", "Files", _NAV_FILES, "nav_files"),
        (None, "/tools", "Tools", _NAV_TOOLS, "nav_tools"),
        ("about", "/about", "About", _NAV_ABOUT, "nav_about"),
        (None, "/", "Home", _NAV_HOME, "nav_home"),
    )
    return (
        "<nav class='nav'>"
        + "".join(
            # Icons only, like every other navigation row; the name stays
            # as a tooltip and for screen readers.
            "<a href='" + href + "' title='" + _attr(i18n.t(key, lang)) + "' aria-label='" + _attr(i18n.t(key, lang)) + "'>" + icon
            + "</a>"
            for feat, href, title, icon, key in items
            if feat is None or features.enabled(feat)
        )
        + "</nav>"
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
              .replace("NICK_INIT", _js(nick))
              .replace("I18N_MESSAGE_SOMEONE", _js(i18n.t("rrc_message_someone", lang)))
              .replace("I18N_DIRECT_MESSAGES", _js(i18n.t("rrc_direct_messages", lang)))
              .replace("I18N_NOT_SENT", _js(i18n.t("rrc_not_sent", lang)))
              .replace("I18N_VOICE", _js({"record": i18n.t("voice_record", lang),
                  "unreadable": i18n.t("voice_unreadable", lang), "recording": i18n.t("voice_recording", lang),
                  "unplayable": i18n.t("voice_unplayable", lang), "length": i18n.t("voice_length", lang)}))
              .replace("I18N_SYMBOLS", _js({
                  "\u2713": i18n.t("sym_join", lang), "\u2717": i18n.t("sym_leave", lang),
                  "\u270e": i18n.t("sym_change", lang), "\u2192": i18n.t("sym_to", lang),
                  "\u2296": i18n.t("sym_unknown", lang), "[DM]": i18n.t("sym_dm", lang),
                  "\u29d7": i18n.t("sym_rate", lang),
                  "=": i18n.t("sym_already", lang), "?": i18n.t("sym_unknown_cmd", lang),
                  "\u2298": i18n.t("sym_refused", lang)})))
    return (
        "<!DOCTYPE html>" + theme.html_open() + "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>RRC — Stump</title><style>" + theme.CSS + STYLE + "</style>"
        "<script>" + theme.STARTUP_SCRIPT + "</script></head><body>"
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
        "<button id='voice' style='display:none' title='" + _attr(i18n.t("voice_record", lang))
        + "' aria-label='" + _attr(i18n.t("voice_record", lang)) + "'>\u266a</button>"
        "<input id='voicefile' type='file' accept='audio/*' capture hidden>"
        "</footer>"
        "<script src='/codec2.js'></script>"
        "<script src='/opus.js'></script>"
        "<script>" + script + "</script>"
        "</body></html>"
    )
