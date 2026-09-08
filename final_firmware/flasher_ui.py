# Project Stump -- kiosk flasher page
# Place at: firmware/flasher_ui.py
#
# Flashes an ESP board from the browser. The board plugs into the KIOSK
# (or laptop) running this page, not into the Stump -- the Stump only
# serves files. Espressif's esptool-js talks to the chip over WebSerial
# using the same ROM bootloader protocol as esptool.py, entirely inside
# the browser.
#
# WHY THIS WORKS AT ALL
# ---------------------
# MicroPython on the ESP32-S3 has no USB host, so the Stump cannot talk
# to a board plugged into itself. But the kiosk is a PC with real USB
# ports and a real browser, and WebSerial gives that browser direct
# access to the chip. The Stump's job reduces to serving a page and some
# binaries, which it is already good at.
#
# THE ONE HARD REQUIREMENT
# ------------------------
# WebSerial is restricted to SECURE CONTEXTS. Plain HTTP on a LAN
# address is not one, and navigator.serial simply will not exist. For a
# kiosk this is fine because we control the launch command:
#
#   chrome --kiosk --unsafely-treat-insecure-origin-as-secure="http://<node-ip>"
#
# The page detects the missing API and says exactly this, rather than
# failing with something cryptic -- being told "your browser blocked it,
# here is the flag" is the difference between a five minute fix and an
# afternoon.
#
# EXTENSION
# ---------
# Everything flashable comes from /sd/fw/catalog.json. New hardware,
# new images, alternative ROMs: add an entry, drop the .bin in /sd/fw/.
# No change to this file, to barkeep.py, or to the manifest.

STYLE = """
*{box-sizing:border-box;}
body{
  background:#1b1512; color:#ecdfc8; margin:0;
  font-family:Georgia,'Iowan Old Style',serif;
  max-width:760px; margin:0 auto; padding:22px 16px 40px; line-height:1.5;
}
h1{
  font-family:ui-monospace,'Cascadia Code','SF Mono',monospace;
  color:#d97a3a; font-size:1.3rem; margin:0 0 2px;
}
.sub{color:#9c8d76; margin-top:0; font-size:.92rem;}
.panel{
  background:#2a2119; border:1px solid #493c2e; border-radius:8px;
  padding:14px 16px; margin:14px 0;
}
.target{
  display:flex; align-items:flex-start; gap:12px; padding:14px 0;
  border-bottom:1px solid #493c2e;
}
.target:last-child{border-bottom:none;}
.target .body{flex:1; min-width:0;}
.target b{color:#ecdfc8; display:block;}
.target .board{color:#d97a3a; font-size:.85rem;}
.target p{margin:6px 0 0; color:#9c8d76; font-size:.88rem;}
.target .warn{color:#e0a05a;}
button{
  font-family:inherit; font-size:1rem; padding:12px 18px; min-height:44px;
  border-radius:6px; border:none; background:#d97a3a; color:#1b1512;
  font-weight:bold; cursor:pointer; flex-shrink:0;
}
button:hover:not(:disabled){background:#f0a050;}
button:disabled{background:#4a3f34; color:#9c8d76; cursor:not-allowed;}
#log{
  font-family:ui-monospace,monospace; font-size:.8rem; line-height:1.45;
  background:#161210; border:1px solid #493c2e; border-radius:6px;
  padding:10px; max-height:260px; overflow-y:auto; white-space:pre-wrap;
  word-break:break-word; display:none;
}
#log.on{display:block;}
.bar{height:8px; background:#161210; border-radius:4px; overflow:hidden;
  margin:10px 0; display:none;}
.bar.on{display:block;}
.bar div{height:100%; width:0; background:#d97a3a; transition:width .2s;}
.err{color:#e07a5a;}
.ok{color:#8fbf7a;}
.blocked{border-color:#e0a05a;}
.blocked h2{color:#e0a05a; font-size:1rem; margin:0 0 8px;}
code{
  background:#161210; padding:2px 6px; border-radius:4px;
  font-family:ui-monospace,monospace; font-size:.82rem;
  word-break:break-all; display:inline-block;
}
.tiles{display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin:16px 0;}
.tile{
  display:flex; flex-direction:column; align-items:center; gap:4px;
  background:#2a2119; border:1px solid #493c2e; border-radius:8px;
  padding:14px 8px; color:#d97a3a; text-decoration:none; min-height:44px;
}
.tile:hover{border-color:#d97a3a; color:#f0a050;}
.tile span{font-weight:bold; font-size:.9rem;}
"""

SCRIPT = """
var log=document.getElementById('log');
var bar=document.getElementById('bar');
var fill=document.getElementById('fill');
var busy=false;

function say(msg, cls){
  log.classList.add('on');
  var s=document.createElement('span');
  if(cls) s.className=cls;
  s.textContent=msg+'\\n';
  log.appendChild(s);
  log.scrollTop=log.scrollHeight;
}
function progress(pct){
  bar.classList.add('on');
  fill.style.width=Math.max(0,Math.min(100,pct))+'%';
}
function setBusy(b){
  busy=b;
  var bs=document.querySelectorAll('button[data-target]');
  for(var i=0;i<bs.length;i++){ bs[i].disabled=b; }
}

// Fetches a binary from the node. Images are large, so failures here
// are worth reporting precisely rather than as a generic error.
function fetchBinary(name){
  return fetch('/fw?f='+encodeURIComponent(name)).then(function(r){
    if(!r.ok) throw new Error('could not fetch '+name+' from the node (HTTP '+r.status+')');
    return r.arrayBuffer();
  }).then(function(buf){
    // esptool-js wants a binary STRING, not an ArrayBuffer.
    var bytes=new Uint8Array(buf), out='', CH=0x8000;
    for(var i=0;i<bytes.length;i+=CH){
      out+=String.fromCharCode.apply(null, bytes.subarray(i, i+CH));
    }
    return out;
  });
}

function flash(target){
  if(busy) return;
  setBusy(true);
  log.innerHTML=''; progress(0);
  say('Target: '+target.name+'  ('+target.chip+')');

  var esp=null, transport=null;

  import('/tool?f=esptool-bundle.js').then(function(mod){
    say('Select the board in the browser prompt...');
    return navigator.serial.requestPort().then(function(port){
      transport=new mod.Transport(port, true);
      esp=new mod.ESPLoader({
        transport: transport,
        baudrate: 921600,
        terminal: {
          clean:function(){},
          writeLine:function(d){ say(d); },
          write:function(d){}
        }
      });
      return esp.main();
    });
  }).then(function(chip){
    say('Connected: '+chip, 'ok');
    if(target.chip && String(chip).toLowerCase().indexOf(target.chip.replace('esp32',''))<0
       && String(chip).toLowerCase().indexOf(target.chip)<0){
      // Not fatal -- chip name strings vary -- but worth flagging loudly,
      // because flashing the wrong image is the expensive mistake here.
      say('WARNING: connected chip does not obviously match '+target.chip
          +'. Continue only if you are sure.', 'err');
    }
    say('Fetching '+target.parts.length+' image(s) from the node...');
    return Promise.all(target.parts.map(function(p){ return fetchBinary(p.file); }));
  }).then(function(datas){
    var fileArray=target.parts.map(function(p, i){
      return { data: datas[i], address: p.offset };
    });
    var total=datas.reduce(function(a,d){ return a+d.length; },0);
    say('Writing '+total+' bytes'+(target.erase?' (erasing first)':'')+'...');
    return esp.writeFlash({
      fileArray: fileArray,
      flashSize: 'keep',
      eraseAll: !!target.erase,
      compress: true,
      reportProgress: function(idx, written, size){
        progress(Math.round(written/size*100));
      }
    });
  }).then(function(){
    progress(100);
    say('Done.', 'ok');
    if(target.after) say(target.after);
    return esp.after();
  }).catch(function(e){
    say('FAILED: '+(e && e.message ? e.message : e), 'err');
    say('If the board was not found: hold BOOT, tap RESET, release BOOT, then try again.');
  }).then(function(){
    if(transport){ try{ transport.disconnect(); }catch(e){} }
    setBusy(false);
  });
}

function render(catalog){
  var box=document.getElementById('targets');
  box.innerHTML='';
  var notes=catalog.chip_notes||{};
  (catalog.targets||[]).forEach(function(t){
    var row=document.createElement('div');
    row.className='target';

    var body=document.createElement('div');
    body.className='body';
    var b=document.createElement('b'); b.textContent=t.name; body.appendChild(b);
    if(t.board){
      var brd=document.createElement('div'); brd.className='board';
      brd.textContent=t.board; body.appendChild(brd);
    }
    if(t.summary){
      var p=document.createElement('p'); p.textContent=t.summary; body.appendChild(p);
    }
    if(t.chip && notes[t.chip]){
      var n=document.createElement('p'); n.textContent=notes[t.chip]; body.appendChild(n);
    }
    if(!t.enabled){
      var w=document.createElement('p'); w.className='warn';
      w.textContent='Image not on this node. '+(t.missing_hint||'');
      body.appendChild(w);
    }
    row.appendChild(body);

    var btn=document.createElement('button');
    btn.textContent='Flash';
    btn.setAttribute('data-target','1');
    btn.disabled=!t.enabled;
    btn.onclick=function(){ flash(t); };
    row.appendChild(btn);

    box.appendChild(row);
  });
  if(!(catalog.targets||[]).length){
    box.textContent='Nothing in the catalog yet.';
  }
}

// Secure context is the gate. Say precisely what to do about it rather
// than letting the page fail on an undefined navigator.serial.
if(!('serial' in navigator)){
  document.getElementById('gate').style.display='block';
  document.getElementById('main').style.display='none';
} else {
  fetch('/fw?f=catalog.json')
    .then(function(r){
      if(!r.ok) throw new Error('no catalog on this node (HTTP '+r.status+')');
      return r.json();
    })
    .then(render)
    .catch(function(e){
      document.getElementById('targets').textContent =
        'Could not load the catalog: '+e.message;
    });
}
"""


def render_page(node_addr="this node"):
    """The flasher page. node_addr is only used to print the exact
    Chrome flag a technician needs, with the real address filled in."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Flash a board — Stump</title><style>" + STYLE + "</style></head><body>"

        "<h1>Flash a board</h1>"
        "<p class='sub'>Plug the board into <b>this computer</b>, not into the Stump. "
        "Everything runs in the browser; the Stump only supplies the images.</p>"

        # Shown only when WebSerial is unavailable.
        "<div id='gate' class='panel blocked' style='display:none'>"
        "<h2>This browser can't reach USB devices</h2>"
        "<p class='sub'>WebSerial only works in a <b>secure context</b>, and this page "
        "is served over plain HTTP on a local address. Two ways forward:</p>"
        "<p class='sub'><b>1. Kiosk / managed browser</b> — launch Chrome or Edge with:</p>"
        "<p><code>--unsafely-treat-insecure-origin-as-secure=\"http://" + node_addr + "\"</code></p>"
        "<p class='sub'><b>2. Any laptop</b> — download the Provisioner from the Files page "
        "and run it there instead. Same result, no browser flag.</p>"
        "<p class='sub'>Firefox and Safari do not implement WebSerial at all, and mobile "
        "browsers do not expose USB serial ports. Chrome or Edge on a desktop is required.</p>"
        "</div>"

        "<div id='main'>"
        "<div class='panel' id='targets'>Loading catalog…</div>"
        "<div class='bar' id='bar'><div id='fill'></div></div>"
        "<div id='log'></div>"
        "</div>"

        "<div class='tiles'>"
        "<a class='tile' href='/'><span>Home</span></a>"
        "<a class='tile' href='/files'><span>Files</span></a>"
        "<a class='tile' href='/rrc'><span>Chat</span></a>"
        "</div>"

        "<script type='module'>" + SCRIPT + "</script>"
        "</body></html>"
    )
