// Arborist: putting the Stump on a board that runs MicroPython, over
// its USB serial line -- the same steps as `mpremote` (what the Provisioner
// uses), in the browser. Three parts:
//
//   Repl      MicroPython's raw REPL over a byte stream: write(bytes) out,
//             feed(bytes) in. Raw-paste mode when the board has it (flow
//             control: the board says how much it can take), plain raw
//             mode otherwise.
//   upload    the app's files: folders first, each file in chunks to
//             <file>.part, its SHA-256 checked on the board, then renamed
//             over the old one -- a cut cable never leaves half a module.
//             A module that arrives compiled (.mpy) has its .py removed
//             (MicroPython would load the .py first), and the other way.
//   makeConfig  config.py from the template the build scrubbed, with the
//             answers of the setup form.
//
// No browser API here except TextEncoder/btoa/setTimeout, so the tests run
// it in Node against a simulated board (tests/arborist_test.py).
(function (root) {
  "use strict";
  var enc = new TextEncoder(), dec = new TextDecoder();
  var RAW_BANNER = "raw REPL; CTRL-B to exit\r\n>";

  function bytes(x) { return typeof x === "string" ? enc.encode(x) : x instanceof Uint8Array ? x : new Uint8Array(x); }
  function indexOf(hay, pat) {
    outer: for (var i = 0; i + pat.length <= hay.length; i++) {
      for (var j = 0; j < pat.length; j++) if (hay[i + j] !== pat[j]) continue outer;
      return i;
    }
    return -1;
  }
  function sleep(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }
  function b64(u8) {
    var s = "";
    for (var i = 0; i < u8.length; i += 4096) s += String.fromCharCode.apply(null, u8.subarray(i, i + 4096));
    return btoa(s);
  }
  function py(s) { return JSON.stringify(String(s)); }      // a JSON string is a Python string literal

  function Repl(write, opts) {
    opts = opts || {};
    this._write = write;
    this.buf = new Uint8Array(0);
    this.waiter = null;
    this.dead = null;
    this.rawPaste = opts.rawPaste === false ? false : undefined;
    this.log = opts.log || function () {};
  }
  Repl.prototype.feed = function (u8) {
    var b = new Uint8Array(this.buf.length + u8.length);
    b.set(this.buf); b.set(u8, this.buf.length);
    this.buf = b;
    if (this.waiter) this.waiter();
  };
  Repl.prototype.closed = function (err) {
    this.dead = err || new Error("the board went away");
    if (this.waiter) this.waiter();
  };
  Repl.prototype.write = function (x) { return this._write(bytes(x)); };
  Repl.prototype.drain = function () { var b = this.buf; this.buf = new Uint8Array(0); return b; };
  Repl.prototype._wait = function (ms) {
    var self = this;
    return new Promise(function (ok) {
      var t = setTimeout(function () { self.waiter = null; ok(false); }, Math.max(ms, 1));
      self.waiter = function () { clearTimeout(t); self.waiter = null; ok(true); };
    });
  };
  // Everything up to and including `pat`; what follows stays for the next read.
  Repl.prototype.readUntil = async function (pat, ms) {
    pat = bytes(pat);
    var until = Date.now() + (ms || 5000);
    for (;;) {
      var i = indexOf(this.buf, pat);
      if (i >= 0) {
        var out = this.buf.slice(0, i + pat.length);
        this.buf = this.buf.slice(i + pat.length);
        return out;
      }
      if (this.dead) throw this.dead;
      var left = until - Date.now();
      if (left <= 0) {
        var e = new Error("the board didn't answer (waited for " + JSON.stringify(dec.decode(pat)) + ")");
        e.tail = dec.decode(this.buf.slice(-300));
        throw e;
      }
      await this._wait(left);
    }
  };
  Repl.prototype.readN = async function (n, ms) {
    var until = Date.now() + (ms || 5000);
    while (this.buf.length < n) {
      if (this.dead) throw this.dead;
      var left = until - Date.now();
      if (left <= 0) throw new Error("the board didn't answer");
      await this._wait(left);
    }
    var out = this.buf.slice(0, n);
    this.buf = this.buf.slice(n);
    return out;
  };

  // Stop whatever runs (a Stump's main loop too) and enter the raw REPL.
  Repl.prototype.enterRaw = async function (ms) {
    for (var i = 0; i < 3; i++) { await this.write("\r\x03"); await sleep(150); }
    this.drain();
    await this.write("\r\x01");
    await this.readUntil(RAW_BANNER, ms || 5000);
  };
  // A soft reset from the raw REPL: a clean interpreter, main.py not run.
  Repl.prototype.softReset = async function (ms) {
    await this.write("\x04");
    await this.readUntil("soft reboot\r\n", ms || 10000);
    await this.readUntil(RAW_BANNER, ms || 10000);
  };
  Repl.prototype._paste = async function (data) {
    var w = await this.readN(2), win = w[0] | (w[1] << 8), remain = win, i = 0;
    while (i < data.length) {
      while (remain === 0 || this.buf.length) {
        var b = (await this.readN(1, 10000))[0];
        if (b === 1) remain += win;
        else if (b === 4) { await this.write("\x04"); throw new Error("the board stopped taking the code"); }
        else throw new Error("the board said something unexpected (" + b + ")");
      }
      var n = Math.min(remain, data.length - i);
      await this.write(data.subarray(i, i + n));
      remain -= n; i += n;
    }
    await this.write("\x04");
    await this.readUntil("\x04", 10000);
  };
  // Run code; {out, err} (err: the traceback, "" when it ran fine).
  Repl.prototype.exec = async function (code, ms) {
    var data = bytes(code), plain = this.rawPaste === false;
    if (!plain) {
      await this.write("\x05A\x01");
      var r = await this.readN(2, 3000);
      if (r[0] === 82 && r[1] === 1) { this.rawPaste = true; await this._paste(data); }
      else {
        this.rawPaste = false; plain = true;
        if (!(r[0] === 82 && r[1] === 0)) await this.readUntil("w REPL; CTRL-B to exit\r\n>", 3000);
      }
    }
    if (plain) {
      for (var i = 0; i < data.length; i += 256) { await this.write(data.subarray(i, i + 256)); await sleep(10); }
      await this.write("\x04");
      var ok = await this.readN(2, 5000);
      if (ok[0] !== 79 || ok[1] !== 75) throw new Error("the board didn't take the code");
    }
    var out = await this.readUntil("\x04", ms || 30000);
    var err = await this.readUntil("\x04", ms || 30000);
    await this.readUntil(">", 5000);
    return { out: dec.decode(out.slice(0, -1)), err: dec.decode(err.slice(0, -1)) };
  };
  // exec, and a traceback is an error.
  Repl.prototype.run = async function (code, ms) {
    var r = await this.exec(code, ms);
    if (r.err) { var e = new Error(r.err.trim().split("\n").pop()); e.traceback = r.err; throw e; }
    return r.out;
  };

  // Before anything: a running Stump stopped, its watchdog (keepalive.py)
  // given 2 hours -- it can't be switched off, and a reset mid-copy would
  // cut the upload -- and a clean interpreter (main.py not run). As the
  // Provisioner does (QUIET_WATCHDOG there).
  var QUIET_WATCHDOG = "try:\n import machine\n machine.WDT(timeout=7200000).feed()\nexcept Exception:\n pass\n";
  async function prepare(repl, ms) {
    await repl.enterRaw(ms);
    await repl.run(QUIET_WATCHDOG);
    await repl.softReset();
  }
  // Restart the board into the Stump (from the friendly REPL, so nothing waits for an answer).
  async function restart(repl) {
    await repl.write("\x02");
    await sleep(300);
    await repl.write("import machine; machine.reset()\r\n");
    await sleep(300);
  }

  // The board's MicroPython: {release: "1.28.0", mpy: "6.3"}.
  async function boardInfo(repl) {
    var out = await repl.run("import sys, os\nv=getattr(sys.implementation,'_mpy',0)\n" +
      "print('INFO', '.'.join(str(x) for x in sys.implementation.version[:3]), '%d.%d' % (v & 0xff, (v >> 8) & 3), (os.uname().machine if hasattr(os, 'uname') else sys.platform).replace(' ', '_'))\n");
    var m = /INFO (\S+) (\S+) ?(\S*)/.exec(out) || [];
    return { release: m[1], mpy: m[2], machine: (m[3] || "").replace(/_/g, " ") };
  }

  // What's on the board already, for these paths: {files: {path: [sha256, size]},
  // free: bytes free where they go}.
  async function haveOnBoard(repl, paths) {
    var root = paths.length && paths[0].indexOf(CARD.root + "/") === 0 ? CARD.root : "/";
    var out = await repl.run(
      "import os,hashlib,binascii,json\nr={}\nfor p in " + JSON.stringify(paths) + ":\n try:\n  n=os.stat(p)[6]\n" +
      "  h=hashlib.sha256()\n  f=open(p,'rb')\n  while 1:\n   b=f.read(2048)\n   if not b:\n    break\n   h.update(b)\n  f.close()\n" +
      "  r[p]=[binascii.hexlify(h.digest()).decode(),n]\n except OSError:\n  pass\n" +
      "s=os.statvfs(" + py(root) + ")\nprint('HAVE'+json.dumps({'files':r,'free':(s[1] or s[0])*s[3]}))\n", 180000);
    var m = /HAVE(\{.*\})/.exec(out);
    return m ? JSON.parse(m[1]) : { files: {}, free: null };
  }

  // Which files to write, and the room it takes (1.0.0-beta.65, kit finding
  // 15): a file already there and the same is skipped; each one written goes
  // next to the old one before the swap, so the largest needs its room on
  // top of what the update adds. have: haveOnBoard()'s.
  var SPACE_MARGIN = 16384;
  function uploadPlan(files, have) {
    var write = [], skip = [], grow = 0, biggest = 0;
    files.forEach(function (f) {
      var h = have.files[f.path];
      if (h && h[0] === f.sha256 && h[1] === f.data.length) { skip.push(f); return; }
      write.push(f);
      grow += f.data.length - (h ? h[1] : 0);
      biggest = Math.max(biggest, f.data.length);
    });
    var need = write.length ? Math.max(0, grow) + biggest + SPACE_MARGIN : 0;
    return { write: write, skip: skip, need: need, free: have.free, short: have.free !== null && need > have.free };
  }

  // Leftovers that are safe to remove when the room is short: a write cut
  // short before (*.part), an old .py beside its .mpy. Never config, the
  // identity, settings or data. Returns how many.
  async function clearLeftovers(repl) {
    var out = await repl.run(
      "import os\nn=0\ndef walk(d,k):\n global n\n try:\n  es=list(os.ilistdir(d))\n except OSError:\n  return\n" +
      " names=[e[0] for e in es]\n for e in es:\n  q=(d+'/' if d!='.' else '')+e[0]\n" +
      "  if e[1]==0x4000:\n   if k<3 and e[0] not in ('sd','rns'):\n    walk(q,k+1)\n   continue\n" +
      "  if e[0].endswith('.part') or (e[0].endswith('.py') and e[0][:-3]+'.mpy' in names and e[0] not in ('boot.py','main.py','config.py')):\n" +
      "   try:\n    os.remove(q)\n    n+=1\n   except OSError:\n    pass\nwalk('.',0)\nprint('GONE%d' % n)\n", 60000);
    var m = /GONE(\d+)/.exec(out);
    return m ? +m[1] : 0;
  }

  // What takes the room on the board: [[folder, bytes]], the biggest first.
  async function flashUse(repl) {
    var out = await repl.run(
      "import os,json\ndef size(p):\n t=0\n try:\n  es=list(os.ilistdir(p))\n except OSError:\n  return 0\n" +
      " for e in es:\n  q=p+'/'+e[0]\n  if e[1]==0x4000:\n   t+=size(q)\n  else:\n   try:\n    t+=os.stat(q)[6]\n   except OSError:\n    pass\n return t\n" +
      "d={}\nfor e in os.ilistdir('.'):\n if e[0]=='sd':\n  continue\n q=e[0]\n" +
      " d[e[0]+('/' if e[1]==0x4000 else '')]=size(q) if e[1]==0x4000 else os.stat(q)[6]\nprint('USE'+json.dumps(d))\n", 60000);
    var m = /USE(\{.*\})/.exec(out), d = m ? JSON.parse(m[1]) : {}, files = 0, list = [];
    Object.keys(d).forEach(function (k) { if (/\/$/.test(k)) list.push([k, d[k]]); else files += d[k]; });
    if (files) list.push(["(files at the top)", files]);
    return list.sort(function (a, b) { return b[1] - a[1]; });
  }
  function kb(n) { return Math.round(n / 1024) + " KB"; }

  // files: [{path, data: Uint8Array, sha256: hex}]. progress(done, total, path) in bytes.
  // Returns {written, skipped}: a file already on the board and the same
  // isn't written again; not enough room is said before anything is.
  async function upload(repl, files, progress, chunk) {
    chunk = chunk || 3072;
    progress = progress || function () {};
    var plan = uploadPlan(files, await haveOnBoard(repl, files.map(function (f) { return f.path; })));
    if (plan.short) {
      var gone = await clearLeftovers(repl);
      plan = uploadPlan(files, await haveOnBoard(repl, files.map(function (f) { return f.path; })));
      if (plan.short) {
        var use = files[0] && files[0].path.indexOf(CARD.root + "/") === 0 ? [] : await flashUse(repl);
        throw new Error("The board's storage is full: this needs about " + kb(plan.need) + " free, it has " + kb(plan.free) +
                        (gone ? " (after removing " + gone + " leftovers)" : "") + ". Nothing was written." +
                        (use.length ? " What takes the room: " + use.slice(0, 6).map(function (u) { return u[0] + " " + kb(u[1]); }).join(", ") + "." : "") +
                        " An installed pack can be removed to make room, or the Stump made again as new (it erases the board).");
      }
    }
    var skipped = plan.skip.length, all = files;
    files = plan.write;
    var dirs = {}, total = 0, done = 0;
    files.forEach(function (f) {
      total += f.data.length;
      var parts = f.path.split("/");
      for (var i = 1; i < parts.length; i++) dirs[parts.slice(0, i).join("/")] = 1;
    });
    var dl = Object.keys(dirs).sort(function (a, b) { return a.split("/").length - b.split("/").length || (a < b ? -1 : 1); });
    if (dl.length)
      await repl.run("import os\nfor d in " + JSON.stringify(dl) + ":\n try:\n  os.mkdir(d)\n except OSError:\n  pass\n");
    for (var k = 0; k < files.length; k++) {
      var f = files[k], tmp = f.path + ".part";
      progress(done, total, f.path);
      if (!f.data.length) await repl.run("open(" + py(tmp) + ",'wb').close()\n");
      for (var off = 0; off < f.data.length; off += chunk) {
        try {
          await repl.run("import binascii\nf=open(" + py(tmp) + ",'" + (off ? "ab" : "wb") + "')\n" +
                         "f.write(binascii.a2b_base64('" + b64(f.data.subarray(off, off + chunk)) + "'))\nf.close()\n");
        } catch (e) {
          if (!/OSError: 28\b|ENOSPC/.test(e.message)) throw e;
          try { await repl.run("import os\ntry:\n os.remove(" + py(tmp) + ")\nexcept OSError:\n pass\n"); } catch (e2) {}
          throw new Error("The board's storage filled up while writing " + f.path + " (" + kb(f.data.length) + "). " +
                          "The old one is still there. An installed pack can be removed to make room, or the Stump made again as new.");
        }
        done += Math.min(chunk, f.data.length - off);
        progress(done, total, f.path);
      }
      await repl.run(
        "import os,hashlib,binascii\nh=hashlib.sha256()\nf=open(" + py(tmp) + ",'rb')\nwhile 1:\n b=f.read(2048)\n" +
        " if not b:\n  break\n h.update(b)\nf.close()\nd=binascii.hexlify(h.digest()).decode()\n" +
        "if d!=" + py(f.sha256) + ":\n os.remove(" + py(tmp) + ")\n raise OSError(" + py(f.path + " arrived damaged") + ")\n" +
        "try:\n os.remove(" + py(f.path) + ")\nexcept OSError:\n pass\nos.rename(" + py(tmp) + "," + py(f.path) + ")\n", 60000);
    }
    var gone = [];
    all.forEach(function (f) {                       // (skipped ones too: an old .py beside a .mpy already there)
      if (/\.mpy$/.test(f.path)) gone.push(f.path.slice(0, -4) + ".py");
      else if (/\.py$/.test(f.path) && !/(^|\/)(boot|main|config)\.py$/.test(f.path)) gone.push(f.path.slice(0, -3) + ".mpy");
    });
    if (gone.length)
      await repl.run("import os\nfor p in " + JSON.stringify(gone) + ":\n try:\n  os.remove(p)\n except OSError:\n  pass\n");
    progress(total, total, "");
    return { written: files.length, skipped: skipped };
  }

  // -- settings: the board's config.py -------------------------------------
  // Read: the values the form shows (passwords only as "set or not").
  // Written by the firmware's own settings.write_config -- what /admin
  // uses: each KEY = value in place of its line, the rest of the file as
  // it was, a temporary file then a rename -- plus the Heltec bridge's
  // three lines inside CONFIG (as the Provisioner edits them). The old
  // file is kept as config.py.bak; a result that isn't Python is undone.
  var KEYS = ["NODE_NAME", "BOT_NAME", "MESH_GREETING", "WIFI_SSID", "AUTH_MODE", "FEATURES", "THEME", "DEFAULT_LANG", "SSID_NAME", "SSID_INCLUDE_IP",
              "CHAIN_ENABLED", "CHAIN_UPLINK_SSID", "CHAIN_UPLINK_PASS", "CHAIN_SUBNET", "CHAIN_CHANNEL", "CHAIN_SECRET",
              "CHAIN_PARENT_IP"];
  // A Heltec on the Stump's own hotspot: .200, past the addresses the
  // hotspot hands out itself (.2 to .101): at .2 a phone could take it
  // first (the Provisioner's HELTEC_ON_HOTSPOT, 1.0.0-beta.56). On the
  // router, each Heltec its own address from .222 (HELTEC_FIRST).
  var HELTEC_ON_HOTSPOT = 200, HELTEC_FIRST = 222;
  // CHAIN_UPLINK_PASS for a member over the router: the home Wi-Fi's
  // password, which the page never reads back -- copied on the board.
  var FROM_WIFI_PASS = "\u0000WIFI_PASS";
  var FEATURES = ["chat", "billboard", "files", "about"], THEMES = ["paper", "amber", "phosphor", "oled"], LANGS = ["fr", "en", "es"],
      MODES = ["open", "hybrid", "mandatory"];

  async function readSettings(repl) {
    var out = await repl.run(
      "import json\nimport config as c\nb=[i for i in c.CONFIG.get('interfaces',[]) if i.get('name')=='Heltec Bridge']\n" +
      "b=b[0] if b else {}\nd={}\nfor k in " + JSON.stringify(KEYS) + ":\n d[k]=getattr(c,k,None)\n" +
      "d['HAS_ADMIN_PASSWORD']=bool(getattr(c,'AUTH_ADMIN_PASSWORD',''))\nd['HAS_OP_PASSWORD']=bool(getattr(c,'FSERVBOT_OP_PASSWORD',''))\n" +
      "d['HAS_WIFI_PASS']=bool(getattr(c,'WIFI_PASS',''))\n" +
      "d['HELTEC']={'there':bool(b),'enabled':bool(b.get('enabled')),'host':b.get('target_host',''),'port':b.get('target_port',7633)}\n" +
      "print('CFG'+json.dumps(d))\n", 30000);
    var m = /CFG(\{.*\})/.exec(out);
    if (!m) throw new Error("couldn't read its settings");
    return JSON.parse(m[1]);
  }

  // -- the radio (the Heltec, host-controlled by the CAM) -----------------
  // Saved in /radio.json (radio.SETTINGS_FILE), written into the bridge at
  // boot and sent to the Heltec on connect -- so Arborist sets it with the
  // firmware's own radio.save(), the same way Settings uses settings.py.
  // The live radio.current() needs a running bridge, which there isn't at
  // the REPL, so a read goes straight to the saved file.
  var RADIO_BW = [7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000];
  // LaBuche's mesh, the defaults a new Stump starts from (must match the mesh).
  var RADIO_DEFAULT = { frequency: 868800000, bandwidth: 125000, sf: 8, cr: 5, txpower: 22 };

  async function readRadio(repl) {
    var out = await repl.run("import json, store, radio\nprint('RAD'+json.dumps(store.load_json(radio.SETTINGS_FILE, None)))\n");
    var m = /RAD(null|\{.*\})/.exec(out);
    return m && m[1] !== "null" ? JSON.parse(m[1]) : null;
  }
  // Returns true if the firmware's radio.save wrote the file (it fails safe,
  // returning False rather than throwing, e.g. a full disk).
  async function writeRadio(repl, settings) {
    var out = await repl.run("import json, radio\nprint('RSAVE' + ('1' if radio.save(json.loads(" + py(JSON.stringify(settings)) + ")) else '0'))\n");
    return /RSAVE1/.test(out);
  }
  // The form's radio answers -> {values:{frequency,bandwidth,sf,cr,txpower}} or {error}.
  // Bounds are radio.py's (FREQ 150-960 MHz, the SX126x bandwidths, SF 5-12, CR 4/5..4/8, TXP 0-22).
  function radioFrom(a) {
    function no(e) { return { error: e }; }
    var mhz = parseFloat(a.freqMhz);
    if (!isFinite(mhz)) return no("The frequency: a number in MHz, like 868.8.");
    var hz = Math.round(mhz * 1e6);
    if (hz < 150000000 || hz > 960000000) return no("The frequency: 150 to 960 MHz.");
    var bw = parseInt(a.bandwidth, 10);
    if (RADIO_BW.indexOf(bw) < 0) return no("The bandwidth isn't one the radio supports.");
    var sf = parseInt(a.sf, 10);
    if (!(sf >= 5 && sf <= 12)) return no("The spreading factor: 5 to 12.");
    var cr = parseInt(a.cr, 10);
    if (!(cr >= 5 && cr <= 8)) return no("The coding rate: 4/5 to 4/8.");
    var txp = parseInt(a.txpower, 10);
    if (!(txp >= 0 && txp <= 22) || String(txp) !== String(a.txpower).trim()) return no("The TX power: 0 to 22 dBm.");
    return { values: { frequency: hz, bandwidth: bw, sf: sf, cr: cr, txpower: txp } };
  }
  // Saved radio (or the defaults) -> the form's radio answers.
  function radioAnswers(r) {
    r = r || RADIO_DEFAULT;
    return { freqMhz: ("" + (r.frequency / 1e6)).replace(/\.0+$/, ""), bandwidth: r.bandwidth,
             sf: r.sf, cr: r.cr, txpower: r.txpower };
  }

  async function readConfigFile(repl) {
    var out = await repl.run("import binascii\nprint('F'+binascii.b2a_base64(open('config.py','rb').read()).decode().replace('\\n',''))\n");
    var m = /F([A-Za-z0-9+\/=]*)/.exec(out);
    var s = atob(m ? m[1] : ""), u = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) u[i] = s.charCodeAt(i);
    return u;
  }

  // -- the identity keepsake (1.0.0-beta.76), as the Provisioner writes it --
  var IDENTITY_FILES = ["rns/identity", "pn_mailbox_identity"];
  function b64ToU8(t) { var s = atob(t || ""), u = new Uint8Array(s.length); for (var i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u; }
  function u8ToB64(u) { var s = ""; for (var i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return btoa(s); }
  // The board's identity, or null when it has none yet. The board sends
  // each file with its size and SHA-256, checked here; a read that doesn't
  // match is tried again, then thrown -- never taken as none, which would
  // let a new identity go over it unsaved (1.0.0-beta.84; on a CAM a
  // garbled read was).
  async function readIdentity(repl, tries) {
    var why = "the board didn't answer whole";
    for (var t = 0; t < (tries || 2); t++) {
      var out = await repl.run("import os,binascii,json,hashlib\nd={}\nfor p in " + JSON.stringify(IDENTITY_FILES) + ":\n try:\n" +
                               "  b=open(p,'rb').read()\n  d[p]=[binascii.b2a_base64(b).decode().strip(),len(b),binascii.hexlify(hashlib.sha256(b).digest()).decode()]\n" +
                               " except OSError:\n  try:\n   os.stat(p)\n   d[p]='unreadable'\n  except OSError:\n   pass\nprint('ID'+json.dumps(d))\n");
      var m = /ID(\{.*\})/.exec(out || ""), d = null;
      try { d = m ? JSON.parse(m[1]) : null; } catch (e) { d = null; }
      if (!d || typeof d !== "object") { why = "the board didn't answer whole"; continue; }
      if (!d["rns/identity"]) return null;
      var files = {}; why = null;
      for (var i = 0; i < IDENTITY_FILES.length && !why; i++) {
        var k = IDENTITY_FILES[i], v = d[k];
        if (v === undefined) continue;
        if (!Array.isArray(v) || v.length !== 3) { why = k + " won't read on the board"; break; }
        var b = null;
        try { b = b64ToU8(v[0]); } catch (e) { b = null; }
        if (!b || b.length !== v[1] || (await shaHex(b)) !== v[2]) { why = k + " came across garbled"; break; }
        files[k] = b;
      }
      if (!why) {
        if (files["rns/identity"].length === 64) return files;
        why = "rns/identity isn't whole on the board (" + files["rns/identity"].length + " bytes)";
      }
    }
    var err = new Error("Its identity won't read whole (" + why + "): nothing changed. Try again (a CAM: unplug it and plug it back in).");
    err.unreadable = true;
    throw err;
  }
  // Two identities the same: every file, the mailbox key too (1.0.0-beta.84).
  async function sameIdentity(a, b) {
    for (var i = 0; i < IDENTITY_FILES.length; i++) {
      var k = IDENTITY_FILES[i];
      if (!a[k] !== !b[k]) return false;
      if (a[k] && (await shaHex(a[k])) !== (await shaHex(b[k]))) return false;
    }
    return true;
  }
  async function identityFingerprint(files) {
    var h = (await shaHex(files["rns/identity"])).slice(0, 16);
    return h.match(/.{4}/g).join(" ");
  }
  async function keepsake(files, name, release) {
    var f = {};
    Object.keys(files).sort().forEach(function (k) { f[k] = u8ToB64(files[k]); });
    var now = new Date(), day = now.getFullYear() + "-" + ("0" + (now.getMonth() + 1)).slice(-2) + "-" + ("0" + now.getDate()).slice(-2);
    return JSON.stringify({ stump_identity: 1, name: name || "", saved: day, release: release || "",
                            fingerprint: await identityFingerprint(files),
                            note: "A Stump's identity: whoever has this file can be that Stump on the mesh. Keep it safe, and don't share it.",
                            files: f }, null, 1) + "\n";
  }
  function keepsakeName(name) { return ((name || "Stump").replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/^[ .]+|[ .]+$/g, "") || "Stump") + ".stump-identity"; }
  // A keepsake's text -> {files, name} or {error}.
  function readKeepsake(text) {
    var d;
    try { d = JSON.parse(text); } catch (e) { return { error: "That file isn't a saved Stump identity." }; }
    if (!d || d.stump_identity !== 1 || typeof d.files !== "object") return { error: "That file isn't a saved Stump identity." };
    var files = {};
    try { IDENTITY_FILES.forEach(function (k) { if (d.files[k]) files[k] = b64ToU8(d.files[k]); }); }
    catch (e) { return { error: "That file is damaged: its identity won't read." }; }
    if (!files["rns/identity"] || files["rns/identity"].length !== 64) return { error: "That file is damaged: its identity isn't whole." };
    return { files: files, name: d.name || "" };
  }
  async function putIdentity(repl, files) {
    await repl.run("import os\ntry:\n os.mkdir('rns')\nexcept OSError:\n pass\n");
    var up = [];
    for (var k in files) up.push({ path: k, data: files[k], sha256: await shaHex(files[k]) });
    await upload(repl, up);
    if (!files["pn_mailbox_identity"]) await repl.run("import os\ntry:\n os.remove('pn_mailbox_identity')\nexcept OSError:\n pass\n");
  }

  // values: {KEY: value} for config.py, and HELTEC: {enabled, host, port}.
  async function writeSettings(repl, values) {
    var top = {}, h = values.HELTEC || null;
    Object.keys(values).forEach(function (k) { if (k !== "HELTEC") top[k] = values[k]; });
    var code =
      "import json, os, settings\nch=json.loads(" + py(JSON.stringify(top)) + ")\nH=json.loads(" + py(JSON.stringify(h)) + ")\n" +
      "if ch.get('CHAIN_UPLINK_PASS')==" + py(FROM_WIFI_PASS) + ":\n import config as _c\n ch['CHAIN_UPLINK_PASS']=ch.get('WIFI_PASS',getattr(_c,'WIFI_PASS',''))\n" +
      "src=open('config.py').read()\nf=open('config.py.bak','w')\nf.write(src)\nf.close()\n" +
      "if ch and not settings.write_config(ch,'config.py'):\n raise OSError('config.py not written')\n" +
      "if H:\n L=open('config.py').read().split('\\n')\n blk=False\n done=0\n for i in range(len(L)):\n" +
      "  l=L[i]\n  s=l.lstrip()\n  ind=l[:len(l)-len(s)]\n  if 'Heltec Bridge' in l:\n   blk=True\n  if not blk:\n   continue\n" +
      "  if s.startswith('\"enabled\"'):\n   L[i]=ind+'\"enabled\": %r,' % bool(H['enabled'])\n   done+=1\n" +
      "  elif s.startswith('\"target_host\"'):\n   L[i]=ind+'\"target_host\": %r,' % H['host']\n   done+=1\n" +
      "  elif s.startswith('\"target_port\"'):\n   L[i]=ind+'\"target_port\": %d,' % int(H['port'])\n   done+=1\n   blk=False\n" +
      " if done!=3:\n  raise OSError('no Heltec Bridge in config.py to set')\n f=open('config.py.tmp','w')\n f.write('\\n'.join(L))\n f.close()\n" +
      " os.rename('config.py.tmp','config.py')\n" +
      "try:\n compile(open('config.py').read(),'config.py','exec')\nexcept SyntaxError as e:\n" +
      " f=open('config.py','w')\n f.write(src)\n f.close()\n raise OSError('the new config.py was not Python: put back as it was')\n" +
      "print('SAVED')\n";
    var out = await repl.run(code, 30000);
    if (!/SAVED/.test(out)) throw new Error("its settings weren't saved");
  }

  // A config.py from this computer, put back as it is (checked to be Python first).
  async function putConfigFile(repl, data, sha) {
    await upload(repl, [{ path: "config.py.new", data: data, sha256: sha }]);
    await repl.run("import os\ntry:\n compile(open('config.py.new').read(),'config.py','exec')\nexcept SyntaxError as e:\n" +
                   " os.remove('config.py.new')\n raise OSError(\"that file isn't a config.py (not Python)\")\n" +
                   "try:\n os.rename('config.py','config.py.bak')\nexcept OSError:\n pass\nos.rename('config.py.new','config.py')\n");
  }

  function ctl(s) { return /[\x00-\x1f\x7f]/.test(s); }
  function isIp(s) { return /^(\d{1,3})(\.\d{1,3}){3}$/.test(s || "") && s.split(".").every(function (x) { return +x <= 255; }); }
  function hex(n) { var u = new Uint8Array(n); crypto.getRandomValues(u); return Array.from(u).map(function (b) { return ("0" + b.toString(16)).slice(-2); }).join(""); }

  // The form's answers -> {values} for writeSettings, or {error}; {warn} too.
  // The rules are /admin's (settings.py) and the Provisioner's. A blank
  // password or Wi-Fi password means "leave it"; requireAdmin (a new
  // Stump) means at least one of the two passwords must end up set.
  function settingsFrom(a) {
    var v = {}, warn = [];
    function no(e) { return { error: e }; }
    var name = (a.name || "").trim();
    if (!name || name.length > 20 || ctl(name)) return no("The name: 1 to 20 characters.");
    v.NODE_NAME = name;
    var bot = (a.bot || "BarKeep").trim();
    if (!bot || bot.length > 24 || ctl(bot)) return no("The bot's name: 1 to 24 characters.");
    v.BOT_NAME = bot;
    var greet = (a.greeting || "").replace(/[\r\n]+/g, " ").trim();
    if (greet.length > 200) return no("The greeting: at most 200 characters.");
    v.MESH_GREETING = greet;
    // Two independent passwords, as the Provisioner sets them. Each blank
    // means "leave it" (the template's blank on a new Stump, or the
    // current value on one being changed -- there's no way to blank one
    // that's set, same as Wi-Fi). /admin uses AUTH_ADMIN_PASSWORD, and
    // falls back to FSERVBOT_OP_PASSWORD when it's blank (barkeep.py).
    if (a.pw || a.pw2) {
      if (a.pw.length < 4 || a.pw.length > 64) return no("The admin password: 4 to 64 characters.");
      if (a.pw !== a.pw2) return no("The two admin passwords aren't the same.");
      v.AUTH_ADMIN_PASSWORD = a.pw;
    }
    if (a.samePw && v.AUTH_ADMIN_PASSWORD) {
      v.FSERVBOT_OP_PASSWORD = v.AUTH_ADMIN_PASSWORD;      // the fserv bot's, the same (the Provisioner's default)
    } else if ((a.opw || a.opw2) && !a.samePw) {
      if (a.opw2 !== undefined && a.opw !== a.opw2) return no("The two fserv bot's passwords aren't the same.");
      if (a.opw.length < 4 || a.opw.length > 64) return no("The fserv bot's password: 4 to 64 characters.");
      v.FSERVBOT_OP_PASSWORD = a.opw;
    }
    // A new Stump needs its admin password, as with the Provisioner
    // (1.0.0-beta.58: the bot's alone used to do).
    if (a.requireAdmin && !v.AUTH_ADMIN_PASSWORD && !a.hasPw)
      return no("Set an admin password (for /admin).");
    var ssid = (a.ssid || "").trim();
    if (ssid.length > 32 || ctl(ssid)) return no("The home Wi-Fi's name: at most 32 characters.");
    v.WIFI_SSID = ssid;
    if (!(a.keepWifiPass && !a.wpass)) {
      var wp = a.wpass || "";
      if (wp && !ssid) return no("A Wi-Fi password, but no Wi-Fi name.");
      if (wp && (wp.length < 8 || wp.length > 63)) return no("A Wi-Fi password is 8 to 63 characters.");
      v.WIFI_PASS = wp;
    }
    var feats = FEATURES.filter(function (f) { return (a.features || []).indexOf(f) >= 0; });
    if (!feats.length) warn.push("No features chosen: its pages offer nothing but /admin.");
    v.FEATURES = feats;
    if (THEMES.indexOf(a.theme || "paper") < 0) return no("Unknown theme.");
    v.THEME = a.theme || "paper";
    if (LANGS.indexOf(a.lang || "fr") < 0) return no("Unknown language.");
    v.DEFAULT_LANG = a.lang || "fr";                 // the Stump's language (1.0.0-beta.67)
    if (MODES.indexOf(a.mode || "open") < 0) return no("Unknown access mode.");
    v.AUTH_MODE = a.mode || "open";
    var hs = (a.hotspot || "").trim();
    if (hs.length > 32 || ctl(hs)) return no("The Wi-Fi name it shows: at most 32 characters.");
    v.SSID_NAME = hs || null; v.SSID_INCLUDE_IP = !!a.hotspotIp;
    // Its number, in a chain or not (1.0.0-beta.64): its Wi-Fi is 192.168.<number>.1.
    var subnet = a.subnet === undefined || String(a.subnet).trim() === "" ? 4 : parseInt(a.subnet, 10);
    if (!(subnet >= 2 && subnet <= 254) || (a.subnet !== undefined && String(a.subnet).trim() !== "" && String(subnet) !== String(a.subnet).trim()))
      return no("Its number: 2 to 254 (its Wi-Fi is 192.168.<number>.1).");
    v.CHAIN_SUBNET = subnet;
    if (a.chain === "first" || a.chain === "member") {
      var secret = (a.secret || "").trim();
      if (secret.length > 64 || /['"\\]/.test(secret) || ctl(secret)) return no("The chain secret: at most 64 characters, no quotes or backslashes.");
      if (!secret) warn.push("No chain secret: anyone on a Stump's Wi-Fi could join the chain.");
      v.CHAIN_ENABLED = true; v.CHAIN_SECRET = secret; v.CHAIN_PARENT_IP = "";
      if (a.chain === "first") {
        var ch = parseInt(a.channel, 10);
        if (!(ch >= 1 && ch <= 13)) return no("The chain's Wi-Fi channel: 1 to 13.");
        v.CHAIN_UPLINK_SSID = ""; v.CHAIN_UPLINK_PASS = ""; v.CHAIN_CHANNEL = ch;
      } else {
        var up = (a.upSsid || "").trim();
        if (!up || up.length > 32 || ctl(up)) return no("The Wi-Fi name of the Stump it joins: 1 to 32 characters.");
        v.CHAIN_UPLINK_SSID = up; v.CHAIN_UPLINK_PASS = a.upPass || ""; v.CHAIN_CHANNEL = 0;
      }
    } else if (a.chain === "lan") {
      // A member over the router (chain.py's CHAIN_PARENT_IP, 1.0.0-beta.57):
      // it stays on the home Wi-Fi and links to the first Stump there.
      var sec2 = (a.secret || "").trim();
      if (!sec2) return no("Over the home Wi-Fi the first Stump only takes Stumps with the chain secret: type it in.");
      if (sec2.length > 64 || /['"\\]/.test(sec2) || ctl(sec2)) return no("The chain secret: at most 64 characters, no quotes or backslashes.");
      if (!ssid) return no("Joining over the home Wi-Fi needs the home Wi-Fi's name (above).");
      var pip = (a.parentIp || "").trim();
      if (!isIp(pip)) return no("The first Stump's address on the home Wi-Fi looks like 192.168.0.173.");
      v.CHAIN_ENABLED = true; v.CHAIN_SECRET = sec2; v.CHAIN_PARENT_IP = pip;
      v.CHAIN_UPLINK_SSID = ssid; v.CHAIN_UPLINK_PASS = a.wpass || FROM_WIFI_PASS; v.CHAIN_CHANNEL = 0;
    } else {
      v.CHAIN_ENABLED = false;
    }
    // The name with its address: 32 characters at most, or the Stump cuts it.
    var shown = hotspotName(hs || a.defaultSsid || "", v.SSID_INCLUDE_IP, subnet);
    if (shown.over > 0)
      return no("The Wi-Fi's name with its address is " + shown.over + " character" + (shown.over === 1 ? "" : "s") +
                " too long (32 at most): shorten the name, or untick showing the address.");
    var onAp = "192.168." + subnet + "." + HELTEC_ON_HOTSPOT;
    if (a.radio === "lan") {
      var host = (a.hhost || "").trim();
      if (!isIp(host)) return no("The Heltec's address looks like 192.168.0.222.");
      if (!ssid) return no("A Heltec on the home Wi-Fi needs the home Wi-Fi's name.");
      if (+host.split(".")[3] < 2 || +host.split(".")[3] > 254) return no("The Heltec's address: its last number from 2 to 254 (.222 is a good one).");
      v.HELTEC = { enabled: true, host: host, port: 7633 };
    } else if (a.radio === "ap") {
      v.HELTEC = { enabled: true, host: onAp, port: 7633 };
    } else {
      v.HELTEC = { enabled: false, host: a.hhost && isIp(a.hhost) ? a.hhost : onAp, port: 7633 };
    }
    return { values: v, warn: warn };
  }

  // What readSettings found -> the form's answers.
  function answersFrom(c) {
    var chain = !c.CHAIN_ENABLED ? "none" : c.CHAIN_PARENT_IP ? "lan" : c.CHAIN_UPLINK_SSID ? "member" : "first";
    var subnet = c.CHAIN_SUBNET || 4, h = c.HELTEC || {};
    // On its hotspot: .200, or .2 as before 1.0.0-beta.58 (saved again as .200).
    var radio = !h.enabled ? "none" : (h.host === "192.168." + subnet + "." + HELTEC_ON_HOTSPOT
                                       || h.host === "192.168." + subnet + ".2") ? "ap" : "lan";
    return {
      name: c.NODE_NAME || "", bot: c.BOT_NAME || "BarKeep", greeting: c.MESH_GREETING || "",
      ssid: c.WIFI_SSID || "", radio: radio, hhost: radio === "lan" ? h.host : "",
      features: Array.isArray(c.FEATURES) ? c.FEATURES : FEATURES.slice(0, 3), theme: c.THEME || "paper",
      lang: LANGS.indexOf(c.DEFAULT_LANG) >= 0 ? c.DEFAULT_LANG : "fr",
      mode: c.AUTH_MODE || "open", hotspot: c.SSID_NAME || "", hotspotIp: !!c.SSID_INCLUDE_IP, chain: chain,
      channel: c.CHAIN_CHANNEL || 11, upSsid: c.CHAIN_UPLINK_SSID || "", upPass: c.CHAIN_UPLINK_PASS || "",
      subnet: c.CHAIN_SUBNET || 4, secret: c.CHAIN_SECRET || "", parentIp: c.CHAIN_PARENT_IP || "",
      hasPw: !!c.HAS_ADMIN_PASSWORD, hasOpPw: !!c.HAS_OP_PASSWORD, hasWifiPass: !!c.HAS_WIFI_PASS
    };
  }

  // What a Stump says as it starts -> {release, micropython, serving, wifi, problems}.
  function bootSummary(text) {
    var lines = text.replace(/\r/g, "").split("\n"), m;
    var out = { release: null, micropython: null, serving: false, ap: null, sta: null, problems: [], notes: [],
                home: null, homeWhy: null, heapKB: null,
                chain: { root: false, linked: null, noAnswer: null, below: [], refused: [] },
                heltec: { ok: null, fail: null } };
    if ((m = /Project Stump -- (.+?) \(([^)]+)\)/.exec(text))) out.release = m[2];
    if ((m = /MicroPython (v[\w.\-]+)/.exec(text))) out.micropython = m[1];
    out.serving = /\[web\] serving/.test(text);
    // What a setup wants to know (1.0.0-beta.60): its address on the home
    // Wi-Fi or why it isn't on it, its place in the chain, its Heltec.
    var IP = "(\\d{1,3}(?:\\.\\d{1,3}){3})";
    if ((m = new RegExp("Connected! IP: " + IP).exec(text)) || (m = new RegExp("over the home Wi-Fi: " + IP).exec(text))
        || (m = new RegExp("and on " + IP + " \\(the Wi-Fi it joined\\)").exec(text))) out.home = m[1];
    if (!out.home) {
      if ((m = /WiFi connection timed out: ([^)\n]+)/.exec(text))) out.homeWhy = m[1].trim();
      else if (/WiFi connection timed out|WiFi unavailable|isn't joined yet/.test(text)) out.homeWhy = "it didn't get on within its time";
    }
    if ((m = /\[mem\] at boot: heap (\d+) KB/.exec(text))) out.heapKB = +m[1];
    out.chain.root = /\[chain\] root on/.test(text);
    if ((m = new RegExp("Chain: linked to the Stump above at " + IP).exec(text))) out.chain.linked = m[1];
    if ((m = new RegExp("Chain: no answer from the Stump above at " + IP).exec(text))) out.chain.noAnswer = m[1];
    var re = new RegExp("Chain: " + IP + " joined below this one", "g");
    while ((m = re.exec(text))) if (out.chain.below.indexOf(m[1]) < 0) out.chain.below.push(m[1]);
    re = new RegExp("Chain hello from " + IP + " refused", "g");
    while ((m = re.exec(text))) if (out.chain.refused.indexOf(m[1]) < 0) out.chain.refused.push(m[1]);
    if ((m = new RegExp("WiFi Serial connected to " + IP).exec(text))) out.heltec.ok = m[1];
    else if ((m = /WiFi Serial (?:initial )?connect failed: ([^\n]+)/.exec(text))) out.heltec.fail = m[1].trim();
    lines.forEach(function (l) {
      if (/Traceback|Error|FAILED|rescue start|couldn't|not armed/i.test(l) && out.problems.length < 12) out.problems.push(l.trim());
      else if (/WiFi unavailable|\[watchdog\] armed|\[chain\]|\[radio\]|\[sd\]/i.test(l) && out.notes.length < 12) out.notes.push(l.trim());
    });
    return out;
  }

  // -- the SD card ------------------------------------------------------------
  // At the REPL main.py hasn't run, so the card isn't mounted: the
  // firmware's own fserv.mount_sd() does it (the pins live there), as the
  // Provisioner does. Copies to the card run at about 1 KB/s: anything over
  // USB_MAX goes by card reader instead (the Provisioner's APP_USB_MAX).
  var CARD = { root: "/sd", mount: "import fserv\nif not fserv.sd_ok:\n fserv.mount_sd()\n" };
  var USB_MAX = 1000000;
  var RMTREE = "import os\ndef rm(p):\n n=0\n try:\n  es=list(os.ilistdir(p))\n except OSError:\n  return 0\n" +
               " for e in es:\n  q=p+'/'+e[0]\n  if e[1]==0x4000:\n   n+=rm(q)\n   try:\n    os.rmdir(q)\n   except OSError:\n    pass\n" +
               "  else:\n   try:\n    os.remove(q)\n    n+=1\n   except OSError:\n    pass\n return n\n";

  // {present, total, free, dirs: {name: [bytes, files]}} (top-level files under "").
  async function cardStatus(repl) {
    var out = await repl.run(CARD.mount +
      "import os, json\nR=" + py(CARD.root) + "\ndef size(p):\n t=n=0\n try:\n  es=list(os.ilistdir(p))\n except OSError:\n  return 0,0\n" +
      " for e in es:\n  q=p+'/'+e[0]\n  if e[1]==0x4000:\n   a,b=size(q)\n   t+=a\n   n+=b\n  else:\n   try:\n    t+=os.stat(q)[6]\n   except OSError:\n    pass\n   n+=1\n return t,n\n" +
      "try:\n os.stat(R)\n ok=True\nexcept OSError:\n ok=False\nd={'present':ok,'dirs':{}}\nif ok:\n s=os.statvfs(R)\n f=s[1] or s[0]\n" +
      " d['total']=f*s[2]\n d['free']=f*s[3]\n top=[0,0]\n for e in list(os.ilistdir(R)):\n  q=R+'/'+e[0]\n  if e[1]==0x4000:\n" +
      "   d['dirs'][e[0]]=list(size(q))\n  else:\n   try:\n    top[0]+=os.stat(q)[6]\n   except OSError:\n    pass\n   top[1]+=1\n" +
      " if top[1]:\n  d['dirs']['']=top\nprint('CARD'+json.dumps(d))\n", 120000);
    var m = /CARD(\{.*\})/.exec(out);
    if (!m) throw new Error("couldn't read the card");
    return JSON.parse(m[1]);
  }

  // scope "shared": the visitors' shared files only (the folder stays);
  // "all": everything on the card (the Stump makes its folders again when
  // it starts). Its flash -- identity, settings, chat -- is never touched.
  // Returns how many files went.
  async function wipeCard(repl, scope) {
    if (scope !== "shared" && scope !== "all") throw new Error("wipe what?");
    var target = scope === "shared" ? CARD.root + "/shared" : CARD.root;
    var out = await repl.run(CARD.mount + RMTREE + "import os\ntry:\n os.stat(" + py(CARD.root) + ")\nexcept OSError:\n print('NOCARD')\nelse:\n" +
                             " print('WIPED%d' % rm(" + py(target) + "))\n", 600000);
    if (/NOCARD/.test(out)) throw new Error("No SD card in it (or the card can't be read): nothing wiped.");
    var m = /WIPED(\d+)/.exec(out);
    if (!m) throw new Error("the card wasn't wiped");
    return +m[1];
  }

  // Files to the card: [{path (under the card), data, sha256}], each the
  // way upload() does it (chunks, SHA-256 checked, then renamed).
  async function toCard(repl, files, progress) {
    var out = await repl.run(CARD.mount + "import os\ntry:\n os.stat(" + py(CARD.root) + ")\n print('HASCARD')\nexcept OSError:\n pass\n");
    if (!/HASCARD/.test(out)) throw new Error("No SD card in it (or the card can't be read): nothing copied.");
    await upload(repl, files.map(function (f) { return { path: CARD.root + "/" + f.path, data: f.data, sha256: f.sha256 }; }), progress);
  }

  // -- zips (a pack from its release) ------------------------------------------
  // The central directory, stored and deflated entries; a path that climbs
  // out (absolute, "..") refuses the whole zip, as the Provisioner does.
  async function inflateRaw(u8) {
    return new Uint8Array(await new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream("deflate-raw"))).arrayBuffer());
  }
  async function readZip(u8) {
    var dv = new DataView(u8.buffer, u8.byteOffset, u8.byteLength), e = -1;
    for (var i = u8.length - 22; i >= Math.max(0, u8.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { e = i; break; }
    if (e < 0) throw new Error("that isn't a zip file");
    var n = dv.getUint16(e + 10, true), p = dv.getUint32(e + 16, true), out = {};
    for (var k = 0; k < n; k++) {
      if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("a damaged zip file");
      var method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), size = dv.getUint32(p + 24, true);
      var nl = dv.getUint16(p + 28, true), xl = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true), off = dv.getUint32(p + 42, true);
      var name = dec.decode(u8.subarray(p + 46, p + 46 + nl));
      p += 46 + nl + xl + cl;
      if (/\/$/.test(name)) continue;
      if (/^\//.test(name) || /(^|\/)\.\.(\/|$)/.test(name) || /\\|:/.test(name)) throw new Error("a path that climbs out in the zip: " + name);
      var data = off + 30 + dv.getUint16(off + 26, true) + dv.getUint16(off + 28, true), raw = u8.subarray(data, data + csize);
      var body = method === 0 ? raw.slice() : method === 8 ? await inflateRaw(raw) : null;
      if (!body) throw new Error(name + ": compressed a way this page can't read");
      if (body.length !== size) throw new Error(name + ": damaged in the zip");
      out[name] = body;
    }
    return out;
  }
  async function shaHex(u8) {
    return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", u8))).map(function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }

  // A pack zip -> {plan} or {error}. The Provisioner's checks: pack.json
  // with name/version/author/license; each plugin's name not the Stump's own
  // code nor a built-in plugin (known: {core: [names], builtin: [names]});
  // every file against its hash (plugin.json, and pack.json for plugin.json
  // itself and the tools/home files) -- one that doesn't match, is missing
  // or has no hash refuses the pack. plan: {name, version, plugins: [files
  // for the board], card: [files for the card], byReader: [card paths too
  // big for USB], theme}.
  var RESERVED = ["urns", "lib", "peripherals", "web", "docs", "third_party", "tools_payload", "sd", "rns", "tls",
                  "plugins_state", "boot", "main", "config"];
  // A plugin's setup questions (plugin.json "wizard", as the Provisioner
  // asks them, 1.0.0-beta.64): only settings of its own -- its name in
  // capitals first (BAVARD_...), into config.py, never one of the Stump's
  // (passwords, Wi-Fi, chain); the others are refused, said, not asked.
  var NEVER_KEYS = /^(AUTH_|WIFI_|CHAIN_|FSERVBOT_OP|NODE_NAME$|ADMIN|STUMP_)/;
  function packQuestions(pn, ps) {
    var w = (ps && ps.wizard) || {}, own = pn.toUpperCase() + "_", out = { plugin: pn, title: ps.title || pn, note: w.note || "", prompts: [], refused: [] };
    (Array.isArray(w.prompts) ? w.prompts : []).forEach(function (q) {
      if (!q || typeof q.key !== "string") return;
      var k = q.key;
      if (!/^[A-Z][A-Z0-9_]{1,47}$/.test(k) || k.indexOf(own) !== 0 || NEVER_KEYS.test(k) || (q.writes_to || "config.py") !== "config.py") {
        out.refused.push(k); return;
      }
      var type = q.type === "choice" || q.type === "int" ? q.type : "string";
      var options = type === "choice" ? (Array.isArray(q.options) ? q.options.map(String) : []).concat([]) : null;
      if (type === "choice" && !options.length) options = [String(q["default"] === undefined ? "" : q["default"])];
      out.prompts.push({ key: k, prompt: String(q.prompt || k), type: type, options: options,
                         "default": q["default"] === undefined ? (type === "int" ? 0 : "") : q["default"],
                         min: typeof q.min === "number" ? q.min : null, max: typeof q.max === "number" ? q.max : null,
                         advanced: !!q.advanced });
    });
    return out;
  }

  // The values to write for a pack's questions. answers: {KEY: {value,
  // touched}}; current: what the board's config.py has. A setting already
  // on the board stays unless it was changed here; a question not marked
  // optional, missing on the board, gets its answer (or default). Checked
  // by type: a choice from its options, a whole number in its range.
  function packValues(questions, answers, current) {
    var v = {}, cur = current || {};
    for (var i = 0; i < questions.length; i++) {
      for (var j = 0; j < questions[i].prompts.length; j++) {
        var q = questions[i].prompts[j], a = (answers || {})[q.key] || {}, has = Object.prototype.hasOwnProperty.call(cur, q.key);
        if (!a.touched && (has || q.advanced)) continue;
        var raw = a.touched ? a.value : q["default"], val;
        if (q.type === "choice") {
          val = String(raw);
          if (q.options.indexOf(val) < 0) return { error: q.key + ": one of " + q.options.join(", ") + "." };
        } else if (q.type === "int") {
          if (!/^-?\d+$/.test(String(raw).trim())) return { error: q.key + ": a whole number." };
          val = parseInt(String(raw).trim(), 10);
          if ((q.min !== null && val < q.min) || (q.max !== null && val > q.max))
            return { error: q.key + ": " + (q.min !== null ? q.min : "…") + " to " + (q.max !== null ? q.max : "…") + "." };
        } else {
          val = String(raw);
          if (val.length > 200 || /[\n\r]/.test(val)) return { error: q.key + ": one line, 200 characters at most." };
        }
        if (has && cur[q.key] === val) continue;
        v[q.key] = val;
      }
    }
    return { values: v };
  }

  // What the board's config.py has for these keys (as Python values).
  async function packCurrent(repl, keys) {
    if (!keys.length) return {};
    var out = await repl.run("import json\ntry:\n import config\n c={k:getattr(config,k) for k in json.loads(" + py(JSON.stringify(keys)) +
                             ") if hasattr(config,k)}\nexcept Exception:\n c={}\nprint('J'+json.dumps(c))\n", 15000);
    var m = /J(\{.*\})/.exec(out);
    try { return m ? JSON.parse(m[1]) : {}; } catch (e) { return {}; }
  }

  // The Stump's own Wi-Fi: its address (192.168.4.1, or 192.168.<number>.1
  // in a chain), and its name as phones will see it -- with the address
  // when it's shown -- and how far over 32 characters that is.
  function apAddress(subnet) { return "192.168." + (subnet || 4) + ".1"; }
  function hotspotName(name, withIp, subnet) {
    var full = (name || "").trim() + (withIp ? " " + apAddress(subnet) : "");
    return { name: full, length: full.length, over: full.length - 32 };
  }

  // A Heltec on the Stump's own Wi-Fi was set up (rnodeconf -w STATION)
  // to join that Wi-Fi by its name, at 192.168.<number>.200. Changing the
  // name (the address shown in it too) or the number leaves it behind: the
  // radio stops until the Heltec is set up again. What would change, said
  // before saving (1.0.0-beta.64).
  function heltecPairBreaks(before, a, defaultSsid) {
    if (!before || before.radio !== "ap" || (a.radio || "none") !== "ap") return [];
    var out = [], n0 = (before.hotspot || "").trim() || defaultSsid || "", n1 = (a.hotspot || "").trim() || defaultSsid || "";
    if (n0 !== n1) out.push("its Wi-Fi's name");
    if (!!before.hotspotIp !== !!a.hotspotIp) out.push("whether its address shows in the Wi-Fi's name");
    if (String(before.subnet || 4) !== String(a.subnet || 4).trim()) out.push("its number (the Heltec stays at 192.168." + (before.subnet || 4) + ".200)");
    return out;
  }

  // -- the Heltec's Wi-Fi (1.0.0-beta.64) ---------------------------------------
  // The Heltec runs the RNode firmware (GPLv3, Mark Qvist / unsigned.io):
  // this page contains none of it, and none of rnodeconf (Reticulum
  // License). It only sends the firmware its documented serial commands,
  // in KISS frames, as rnodeconf -w STATION --ssid --psk --ip --nm does:
  // join this Wi-Fi by its name, with this password (none: an open one),
  // at this fixed address. Then it reads the settings back to check them,
  // and restarts the Heltec so they take effect.
  var KISS = { FEND: 0xC0, FESC: 0xDB, TFEND: 0xDC, TFESC: 0xDD };
  var RN = { DETECT: 0x08, DETECT_REQ: 0x73, DETECT_RESP: 0x46, FW_VERSION: 0x50, ROM_READ: 0x51, RESET: 0x55, RESET_HARD: 0xF8,
             WIFI_MODE: 0x6A, WIFI_SSID: 0x6B, WIFI_PSK: 0x6C, CFG_READ: 0x6D, WIFI_IP: 0x84, WIFI_NM: 0x85,
             ROM_WIFI: 0xBA, CFG_SSID: 0x00, CFG_PSK: 0x21, CFG_IP: 0x42, CFG_NM: 0x46 };
  function kissFrame(cmd, data) {
    var out = [KISS.FEND, cmd];
    (data || []).forEach(function (b) {
      if (b === KISS.FEND) out.push(KISS.FESC, KISS.TFEND);
      else if (b === KISS.FESC) out.push(KISS.FESC, KISS.TFESC);
      else out.push(b);
    });
    out.push(KISS.FEND);
    return new Uint8Array(out);
  }
  // Frames out of the bytes as they come: {cmd, data}; next(cmd, ms) waits for one.
  function KissReader() {
    this.frames = []; this.cur = null; this.esc = false; this.waiting = [];
  }
  KissReader.prototype.feed = function (u8) {
    for (var i = 0; i < u8.length; i++) {
      var b = u8[i];
      if (b === KISS.FEND) {
        if (this.cur && this.cur.length) this._got({ cmd: this.cur[0], data: this.cur.slice(1) });
        this.cur = []; this.esc = false;
      } else if (this.cur) {
        if (this.esc) { this.cur.push(b === KISS.TFEND ? KISS.FEND : b === KISS.TFESC ? KISS.FESC : b); this.esc = false; }
        else if (b === KISS.FESC) this.esc = true;
        else this.cur.push(b);
      }
    }
  };
  KissReader.prototype._got = function (f) {
    for (var i = 0; i < this.waiting.length; i++) {
      if (this.waiting[i].cmd === f.cmd) { var w = this.waiting.splice(i, 1)[0]; clearTimeout(w.t); w.ok(f); return; }
    }
    this.frames.push(f);
  };
  KissReader.prototype.next = function (cmd, ms) {
    var self = this;
    for (var i = 0; i < this.frames.length; i++) if (this.frames[i].cmd === cmd) return Promise.resolve(this.frames.splice(i, 1)[0]);
    return new Promise(function (ok) {
      var w = { cmd: cmd, ok: ok };
      w.t = setTimeout(function () { var j = self.waiting.indexOf(w); if (j >= 0) self.waiting.splice(j, 1); ok(null); }, ms || 1500);
      self.waiting.push(w);
    });
  };
  function utf8(s) { return Array.prototype.slice.call(new TextEncoder().encode(s)); }
  function ipBytes(ip) { return ip.split(".").map(function (x) { return parseInt(x, 10); }); }
  // What to send for: {ssid, psk ("" = open), ip, nm}. Checked first: a name
  // of 1 to 32 bytes, a password of 8 to 32 or none, addresses as 4 numbers.
  function heltecWifiPlan(o) {
    var ssid = o.ssid || "", psk = o.psk || "", ip = (o.ip || "").trim(), nm = (o.nm || "255.255.255.0").trim();
    var sb = utf8(ssid), pb = utf8(psk);
    if (!sb.length || sb.length > 32) return { error: "The Wi-Fi's name for the Heltec: 1 to 32 characters." };
    if (pb.length && (pb.length < 8 || pb.length > 32)) return { error: "The Wi-Fi's password for the Heltec: 8 to 32 characters, or none for an open Wi-Fi." };
    if (!isIp(ip)) return { error: "The Heltec's address looks like 192.168.4.200." };
    if (!isIp(nm)) return { error: "The network mask looks like 255.255.255.0." };
    return { ssid: ssid, open: !pb.length, ip: ip, nm: nm, frames: [
      kissFrame(RN.WIFI_SSID, sb.concat([0])),
      kissFrame(RN.WIFI_PSK, pb.length ? pb.concat([0]) : [0]),
      kissFrame(RN.WIFI_IP, ipBytes(ip)),
      kissFrame(RN.WIFI_NM, ipBytes(nm)),
      kissFrame(RN.WIFI_MODE, [0x01])                 // station: it joins that Wi-Fi
    ] };
  }
  // The Wi-Fi settings out of the RNode's EEPROM and config sector.
  function rnodeWifi(eeprom, cfg) {
    function str(at) {
      var b = [];
      for (var i = 0; i < 32 && cfg && at + i < cfg.length; i++) { var c = cfg[at + i]; if (c === 0 || c === 0xFF) break; b.push(c); }
      try { return new TextDecoder().decode(new Uint8Array(b)); } catch (e) { return ""; }
    }
    function quad(at) {
      if (!cfg || cfg.length < at + 4) return null;
      var q = [cfg[at], cfg[at + 1], cfg[at + 2], cfg[at + 3]].join(".");
      return q === "255.255.255.255" || q === "0.0.0.0" ? null : q;
    }
    var m = eeprom && eeprom.length > RN.ROM_WIFI ? eeprom[RN.ROM_WIFI] : null;
    return { mode: m === 1 ? "station" : m === 2 ? "access point" : "off", ssid: cfg ? str(RN.CFG_SSID) : null,
             hasPsk: cfg ? !!str(RN.CFG_PSK) : null, ip: quad(RN.CFG_IP), nm: quad(RN.CFG_NM) };
  }
  function concatU8(list) {
    var n = list.reduce(function (a, u) { return a + u.length; }, 0), out = new Uint8Array(n), o = 0;
    list.forEach(function (u) { out.set(u, o); o += u.length; });
    return out;
  }
  // io: {write(u8) -> Promise, reader: KissReader, sleep(ms)}. Returns
  // {fw, before, after, ok} or throws with what to check.
  async function heltecSetWifi(io, plan, say) {
    say = say || function () {};
    var hello = false, fw = null;
    for (var t = 0; t < 4 && !hello; t++) {
      await io.write(concatU8([kissFrame(RN.DETECT, [RN.DETECT_REQ]), kissFrame(RN.FW_VERSION, [0])]));
      var d = await io.reader.next(RN.DETECT, 1500);
      hello = !!(d && d.data[0] === RN.DETECT_RESP);
      if (!hello) await io.sleep(1000);              // (it may be starting: opening the port restarts some boards)
    }
    if (!hello) throw new Error("No answer from an RNode on that port: is it the Heltec (not the Stump), on the RNode firmware, and not held by another program?");
    var v = await io.reader.next(RN.FW_VERSION, 800);
    if (v && v.data.length >= 2) fw = v.data[0] + "." + (v.data[1] < 10 ? "0" : "") + v.data[1];
    say("an RNode" + (fw ? ", firmware " + fw : ""));
    async function read() {
      await io.write(kissFrame(RN.ROM_READ, [0]));
      var e = await io.reader.next(RN.ROM_READ, 2000);
      await io.write(kissFrame(RN.CFG_READ, [0]));
      var c = await io.reader.next(RN.CFG_READ, 2000);
      return { eeprom: e && e.data, cfg: c && c.data };
    }
    var r0 = await read();
    if (!r0.cfg) throw new Error("This Heltec's RNode firmware" + (fw ? " (" + fw + ")" : "") + " doesn't take Wi-Fi settings: update it with the Provisioner (A Heltec for a CAM) first.");
    var before = rnodeWifi(r0.eeprom, r0.cfg);
    for (var i = 0; i < plan.frames.length; i++) { await io.write(plan.frames[i]); await io.sleep(250); }
    var r1 = await read(), after = rnodeWifi(r1.eeprom, r1.cfg);
    var ok = after.ssid === plan.ssid && after.ip === plan.ip && after.hasPsk === !plan.open && (r1.eeprom ? after.mode === "station" : true);
    if (ok) await io.write(kissFrame(RN.RESET, [RN.RESET_HARD]));     // restarts with them
    return { fw: fw, before: before, after: after, ok: ok };
  }

  async function packFrom(zip, known) {
    function no(e) { return { error: e }; }
    var names = Object.keys(zip), base = null;
    names.forEach(function (n) { var m = /^(?:([^/]+)\/)?pack\.json$/.exec(n); if (m && (base === null || !m[1])) base = m[1] ? m[1] + "/" : ""; });
    if (base === null) return no("No pack.json in that zip: it isn't a Stump pack.");
    var spec;
    try { spec = JSON.parse(dec.decode(zip[base + "pack.json"])); } catch (e) { return no("Its pack.json won't read."); }
    var miss = ["name", "version", "author", "license"].filter(function (k) { return !spec[k]; });
    if (miss.length) return no("Its pack.json has no " + miss.join(", ") + ".");
    if (!/^[a-z][a-z0-9_-]{0,31}$/.test(spec.name)) return no("The pack's name isn't a plain name.");
    known = known || {};
    var plugins = spec.plugins || [];
    var plan = { name: spec.name, version: spec.version, plugins: [], card: [], byReader: [], theme: spec.theme || null,
                 questions: [], refused: [], names: plugins.slice() };
    for (var i = 0; i < plugins.length; i++) {
      var pn = plugins[i];
      if (typeof pn !== "string" || !/^[a-z][a-z0-9_]{1,23}$/.test(pn)) return no("A plugin's name is 2-24 lowercase letters, digits or _.");
      if (RESERVED.indexOf(pn) >= 0 || (known.core || []).indexOf(pn) >= 0) return no(pn + " is a part of the Stump's own code: a pack can't replace it.");
      if ((known.builtin || []).indexOf(pn) >= 0) return no(pn + " is a built-in plugin: a pack can't replace it.");
      var pj = zip[base + pn + "/plugin.json"];
      if (!pj) return no(pn + ": no plugin.json.");
      var want = (spec.plugin_hashes || {})[pn];
      if (!want || (await shaHex(pj)).slice(0, 16) !== want) return no(pn + "/plugin.json doesn't match the pack's hash.");
      var ps;
      try { ps = JSON.parse(dec.decode(pj)); } catch (e) { return no(pn + "/plugin.json won't read."); }
      var qs = packQuestions(pn, ps);
      if (qs.prompts.length) plan.questions.push(qs);
      plan.refused = plan.refused.concat(qs.refused);
      var hashes = ps.hashes || {}, files;
      if (Array.isArray(ps.files) && ps.files.length)
        files = ps.files.filter(function (f) { return typeof f === "string" && f.indexOf(pn + "/") === 0 && !/(^|\/)\.\.?(\/|$)/.test(f) && zip[base + f]; });
      else
        files = names.filter(function (n) { return n.indexOf(base + pn + "/") === 0 && !/__pycache__|\.(pyc|bak|orig|swp)$|\.DS_Store$/.test(n); })
                     .map(function (n) { return n.slice(base.length); });
      for (var j = 0; j < files.length; j++) {
        var f = files[j];
        if (f === pn + "/plugin.json") continue;
        if (!hashes[f]) return no(f + " has no hash in its plugin.json.");
        if ((await shaHex(zip[base + f])).slice(0, 16) !== hashes[f]) return no(f + " changed since it was hashed: not installed.");
      }
      var listed = Object.keys(hashes);
      for (var q = 0; q < listed.length; q++) if (!zip[base + listed[q]]) return no(listed[q] + " is missing from the pack.");
      for (j = 0; j < files.length; j++) {
        f = files[j];
        if (/(^|\/)plugin\.json$/.test(f) || /\.md$/.test(f)) continue;       // not for the board (BOARD_EXCLUDE_*)
        var d = zip[base + f];
        plan.plugins.push({ path: f, data: d, sha256: await shaHex(d) });
      }
    }
    var packHashes = spec.hashes || {};
    var subs = ["tools", "home"];
    for (var s = 0; s < subs.length; s++) {
      if (spec[subs[s]] === false) continue;
      var pre = base + subs[s] + "/";
      var here = names.filter(function (n) { return n.indexOf(pre) === 0 && n.slice(pre.length).indexOf("/") < 0 && !/^\./.test(n.slice(pre.length)); }).sort();
      for (var t = 0; t < here.length; t++) {
        var rel = here[t].slice(base.length), fname = here[t].slice(pre.length), data = zip[here[t]];
        if (!packHashes[rel]) return no(rel + " has no hash in the pack.json.");
        if ((await shaHex(data)).slice(0, 16) !== packHashes[rel]) return no(rel + " changed since the pack was hashed: not installed.");
        var dest = subs[s] === "tools" ? "tools/" + spec.name + "/" + fname : "home/" + fname;
        if (data.length > USB_MAX) plan.byReader.push(dest);
        else plan.card.push({ path: dest, data: data, sha256: await shaHex(data) });
      }
    }
    return { plan: plan };
  }

  // A checked pack onto the board: its plugins on the flash (as source;
  // a compiled copy left from before goes), its tools and home files on the
  // card, its theme into themes.json. It starts at the next restart.
  // What a pack installs is recorded in each of its plugin folders
  // (.from_pack, as the Provisioner writes it, 1.0.0-beta.75): which pack,
  // which version, its Tools section, home files and theme, so it can be
  // updated and removed whole.
  var PACK_MARK = ".from_pack";
  function packRecord(plan) {
    var tools = {}, home = [];
    plan.card.concat(plan.byReader.map(function (p) { return { path: p }; })).forEach(function (f) {
      var m = /^tools\/([^/]+)\//.exec(f.path);
      if (m) tools["tools/" + m[1]] = 1;
      else if (/^home\//.test(f.path)) home.push(f.path.slice(5));
    });
    return { pack: plan.name, version: String(plan.version), plugins: plan.names || [],
             theme: plan.theme && plan.theme.name || null, tools: Object.keys(tools).sort(), home: home.sort() };
  }
  async function installPack(repl, plan, progress) {
    if (plan.plugins.length) await upload(repl, plan.plugins, progress);
    if (plan.card.length) await toCard(repl, plan.card, progress);
    var rec = enc.encode(JSON.stringify(packRecord(plan)));
    for (var i = 0; i < (plan.names || []).length; i++)
      await upload(repl, [{ path: plan.names[i] + "/" + PACK_MARK, data: rec, sha256: await shaHex(rec) }]);
    if (plan.theme && plan.theme.name) {
      var out = await repl.run("import json\ntry:\n d=json.load(open('themes.json'))\nexcept Exception:\n d={}\nprint('TH'+json.dumps(d))\n");
      var m = /TH(\{.*\})/.exec(out), cur = m ? JSON.parse(m[1]) : {};
      cur[plan.theme.name] = { label: plan.theme.label || plan.theme.name, colors: plan.theme.colors || {} };
      var data = enc.encode(JSON.stringify(cur));
      await upload(repl, [{ path: "themes.json", data: data, sha256: await shaHex(data) }]);
    }
  }

  // The Tools page from what the page carries (bundle entries "sd/tools/...":
  // the Provisioner, its README and licence, the firmware zip, the FireFly
  // apps and each section's README). Big ones are left for a card reader;
  // an older firmware zip beside the new one goes, as the Provisioner does.
  async function fillTools(repl, files, progress) {
    var go = [], byReader = [];
    files.forEach(function (f) { (f.data.length > USB_MAX * 2 ? byReader : go).push(f); });
    await toCard(repl, go, progress);
    var zip = go.filter(function (f) { return /^tools\/provisioner\/Stump-.*\.zip$/.test(f.path); })[0];
    if (zip) {
      var keep = zip.path.split("/").pop();
      await repl.run("import os\nD=" + py(CARD.root + "/tools/provisioner") + "\nfor n in os.listdir(D):\n" +
                     " if n.startswith('Stump-') and n.endswith('.zip') and n!=" + py(keep) + ":\n  os.remove(D+'/'+n)\n");
    }
    return { byReader: byReader.map(function (f) { return f.path; }) };
  }

  // -- the Stump image (MicroPython with the Stump frozen in, 1.0.0-beta.57)
  // Which image for the chip esptool-js names, and where it goes: the
  // S3-CAM's at 0x0, the original ESP32's (a WROVER) at 0x1000 -- as the
  // Provisioner writes them (FIRMWARE_FOR_CHIP).
  var IMAGE_FOR = { s3cam: { offset: 0x0, board: "the Freenove ESP32-S3-CAM" },
                    wrover: { offset: 0x1000, board: "an ESP32-WROVER (the original ESP32, with PSRAM)" } };
  function imageForChip(chip) {
    var c = String(chip || "").toUpperCase();
    if (/ESP32-S3/.test(c)) return "s3cam";
    if (/ESP32-(S2|C\d|H\d|P\d)/.test(c)) return null;     // other chips: no Stump for them
    if (/ESP32/.test(c)) return "wrover";
    return null;
  }
  // What a board running a Stump image says of it: {release, partitions,
  // frozen} (stump_image.py, frozen in by image/stage.py), or null.
  async function imageInfo(repl) {
    var out = await repl.run("import json\ntry:\n import stump_image as s\n print('IMG'+json.dumps({'release':s.RELEASE,'partitions':s.PARTITIONS,'frozen':s.FROZEN}))\n" +
                             "except ImportError:\n print('IMGnone')\n");
    var m = /IMG(\{.*\})/.exec(out);
    return m ? JSON.parse(m[1]) : null;
  }
  // The board's files left to copy beside an image: those it hasn't frozen
  // in (a frozen module's copy on the filesystem would be loaded instead:
  // the filesystem comes first).
  function notFrozen(files, frozen) {
    var f = {};
    (frozen || []).forEach(function (n) { f[n.replace(/\.(py|mpy)$/, "")] = 1; });
    return files.filter(function (x) { return !/\.(py|mpy)$/.test(x.path) || /(^|\/)(boot|main|config)\.py$/.test(x.path)
                                                || !f[x.path.replace(/\.(py|mpy)$/, "")]; });
  }
  // Stale copies of frozen modules removed from the filesystem (an older
  // install's), then the packages' folders left empty.
  async function removeFrozenCopies(repl, frozen) {
    var names = Array.from(new Set((frozen || []).map(function (n) { return n.replace(/\.(py|mpy)$/, ""); }))).sort();
    var dirs = Array.from(new Set((frozen || []).filter(function (n) { return n.indexOf("/") > 0; })
                                  .map(function (n) { return n.slice(0, n.lastIndexOf("/")); })))
                    .sort(function (a, b) { return b.split("/").length - a.split("/").length; });
    var out = await repl.run("import os\nn=0\nfor m in " + JSON.stringify(names) + ":\n for p in (m+'.py',m+'.mpy'):\n  try:\n   os.remove(p)\n   n+=1\n  except OSError:\n   pass\n" +
                             "for d in " + JSON.stringify(dirs) + ":\n try:\n  if not os.listdir(d):\n   os.rmdir(d)\n except OSError:\n  pass\nprint('GONE%d' % n)\n", 60000);
    var m = /GONE(\d+)/.exec(out);
    return m ? +m[1] : 0;
  }

  // The packs on a board: its plugins that aren't built in, each with its
  // record (null for one installed before 1.0.0-beta.75, or by hand),
  // grouped by pack: [{name, plugins, record}].
  async function listPacks(repl, builtins) {
    var skip = (builtins || []).concat(["rns", "sd", "__pycache__", "lib", "urns", "web", "peripherals"]);
    var out = await repl.run(
      "import os,json\nout={}\nfor n in os.listdir():\n if n in " + JSON.stringify(skip) + ":\n  continue\n ok=False\n" +
      " for f in ('/install.py','/install.mpy'):\n  try:\n   os.stat(n+f)\n   ok=True\n  except OSError:\n   pass\n" +
      " if not ok:\n  continue\n r=None\n try:\n  with open(n+'/" + PACK_MARK + "') as f:\n   r=json.loads(f.read())\n" +
      " except Exception:\n  pass\n out[n]=r\nprint('PACKS'+json.dumps(out))\n", 30000);
    var m = /PACKS(\{.*\})/.exec(out), found = m ? JSON.parse(m[1]) : {}, groups = [], by = {};
    Object.keys(found).sort().forEach(function (pn) {
      var r = found[pn];
      if (r && r.pack) {
        if (!by[r.pack]) { by[r.pack] = { name: r.pack, plugins: [], record: r }; groups.push(by[r.pack]); }
        by[r.pack].plugins.push(pn);
      } else groups.push({ name: pn, plugins: [pn], record: null });
    });
    return groups;
  }
  // A pack off the board: its plugin folders; with also, what it keeps too
  // (its saved data in plugins/ on the card, its Tools sections, its home
  // files, its theme). Returns how many paths went.
  // The record is read from the board, so it can be tampered with
  // (1.0.0-beta.84): only the pack's own places are taken from it, as the
  // Provisioner does (_pack_leftovers) -- plugins/<its plugin>,
  // tools/<a section the Stump doesn't ship>, home/<one file, deleted as a
  // file> -- and anything else in it is ignored.
  var CORE_TOOL_SECTIONS = ["apps", "arborist", "flasher", "images", "provisioner", "firefly", "fw"];
  function safeName(s) { return typeof s === "string" && /^[A-Za-z0-9][A-Za-z0-9 _.+-]{0,63}$/.test(s) && s.indexOf("..") < 0; }
  function plainPlugin(p) { return typeof p === "string" && /^[a-z][a-z0-9_]{1,23}$/.test(p) &&
                                   ["urns", "lib", "peripherals", "web", "docs", "third_party", "tools_payload", "sd", "rns", "tls",
                                    "plugins_state", "boot", "main", "config"].indexOf(p) < 0; }
  function packLeftovers(pack) {
    var rec = pack.record && typeof pack.record === "object" ? pack.record : {};
    return {
      dirs: pack.plugins.filter(plainPlugin).map(function (p) { return "plugins/" + p; })
                .concat((Array.isArray(rec.tools) ? rec.tools : []).filter(function (t) {
                  return typeof t === "string" && t.indexOf("tools/") === 0 && safeName(t.slice(6)) && CORE_TOOL_SECTIONS.indexOf(t.slice(6)) < 0; })),
      files: (Array.isArray(rec.home) ? rec.home : []).filter(safeName).map(function (h) { return "home/" + h; }),
      theme: typeof rec.theme === "string" ? rec.theme : null };
  }
  async function removePack(repl, pack, also) {
    var left = also ? packLeftovers(pack) : { dirs: [], files: [], theme: null }, card = left.dirs.length || left.files.length;
    var out = await repl.run(
      "import os\nn=0\ndef rm(p):\n global n\n try:\n  for e in os.listdir(p):\n   rm(p+'/'+e)\n  os.rmdir(p)\n  n+=1\n" +
      " except OSError:\n  try:\n   os.remove(p)\n   n+=1\n  except OSError:\n   pass\n" +
      "for p in " + JSON.stringify(pack.plugins.filter(plainPlugin)) + ":\n rm(p)\n" +
      (card ? "try:\n import fserv\n fserv.mount_sd()\nexcept Exception:\n pass\nfor p in " + JSON.stringify(left.dirs) + ":\n rm(" + py(CARD.root) + "+'/'+p)\n" +
              "for p in " + JSON.stringify(left.files) + ":\n try:\n  os.remove(" + py(CARD.root) + "+'/'+p)\n  n+=1\n except OSError:\n  pass\n" : "") +
      (left.theme ? "import json\ntry:\n d=json.load(open('themes.json'))\n d.pop(" + py(left.theme) + ",None)\n f=open('themes.json','w')\n f.write(json.dumps(d))\n f.close()\nexcept Exception:\n pass\n" : "") +
      "print('GONE%d' % n)\n", 120000);
    var m = /GONE(\d+)/.exec(out);
    return m ? +m[1] : 0;
  }

  // The files an earlier release put on the board and this one no longer
  // has (1.0.0-beta.74), left behind by every update before. Not inside a
  // folder that is a plugin the Stump doesn't ship (a pack's). Returns the
  // paths removed.
  async function removeRetired(repl, paths, builtins) {
    if (!paths || !paths.length) return [];
    var out = await repl.run(
      "import os\ndef has(t):\n for f in ('/install.py','/install.mpy'):\n  try:\n   os.stat(t+f)\n   return True\n  except OSError:\n   pass\n return False\n" +
      "gone=[]\nfor p in " + JSON.stringify(paths) + ":\n t=p.split('/')[0] if '/' in p else None\n" +
      " if t and t not in " + JSON.stringify(builtins || []) + " and has(t):\n  continue\n" +
      " try:\n  os.remove(p)\n  gone.append(p)\n except OSError:\n  pass\nprint('RETIRED:'+','.join(gone))\n", 60000);
    var m = /RETIRED:(.*)/.exec(out);
    return m ? m[1].trim().split(",").filter(function (g) { return g; }) : [];
  }

  // Free memory at the prompt (bytes), or null: a WROVER's or a CAM's
  // PSRAM gives megabytes; a board without it (an ESP32-WROOM) a few
  // hundred KB, too little for a Stump.
  async function memory(repl) {
    var m = /MEM(\d+)/.exec(await repl.run("import gc\ngc.collect()\nprint('MEM%d' % (gc.mem_free() + gc.mem_alloc()))\n"));
    return m ? +m[1] : null;
  }

  // -- looking around with the board (1.0.0-beta.60) ----------------------------
  // The browser can't see Wi-Fi networks or the local network; the board
  // can, at its prompt: it scans, joins the home Wi-Fi (the password
  // checked there and then), and asks "who's a Stump here?" (discovery's
  // broadcast, port 4244), then each Stump's /chain.json and /hw.json.
  var LOOK_PY = "import network, time, json, socket\nw=network.WLAN(network.STA_IF)\nw.active(True)\n" +
    "def _s(b):\n try:\n  return b.decode()\n except Exception:\n  return ''\n" +
    "def scan():\n o={}\n for n in w.scan():\n  s=_s(n[0])\n  if s and (s not in o or n[3]>o[s][0]):\n   o[s]=[n[3],n[2],n[4]==0]\n print('SCAN'+json.dumps(o))\n" +
    "def get(ip,pt,p):\n c=None\n try:\n  c=socket.socket()\n  c.settimeout(2)\n  c.connect(socket.getaddrinfo(ip,pt)[0][-1])\n" +
    "  c.send(('GET %s HTTP/1.0\\r\\nHost: %s\\r\\n\\r\\n'%(p,ip)).encode())\n  d=b''\n  while len(d)<24000:\n   x=c.recv(1024)\n   if not x:\n    break\n   d+=x\n" +
    "  return json.loads(d.split(b'\\r\\n\\r\\n',1)[1])\n except Exception:\n  return None\n finally:\n  if c:\n   c.close()\n" +
    "def why():\n st=w.status()\n for k,v in (('STAT_WRONG_PASSWORD','wrong'),('STAT_NO_AP_FOUND','notfound'),('STAT_ASSOC_FAIL','refused'),('STAT_HANDSHAKE_TIMEOUT','wrong')):\n  if st==getattr(network,k,None):\n   return v\n return 'timeout'\n" +
    "def look(ssid,pw):\n try:\n  w.disconnect()\n except Exception:\n  pass\n w.connect(ssid,pw)\n t=time.time()\n" +
    " while not w.isconnected() and time.time()-t<20:\n  time.sleep(0.3)\n if not w.isconnected():\n  print('LOOK'+json.dumps({'joined':False,'why':why()}))\n  return\n" +
    " ip=w.ifconfig()[0]\n u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n try:\n  u.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)\n except Exception:\n  pass\n" +
    " for d in ('255.255.255.255',ip.rsplit('.',1)[0]+'.255'):\n  try:\n   u.sendto(b'STUMP?',socket.getaddrinfo(d,4244)[0][-1])\n  except Exception:\n   pass\n" +
    " u.settimeout(0.3)\n cards={}\n t=time.time()\n while time.time()-t<3:\n  try:\n   c=json.loads(u.recv(1024))\n  except Exception:\n   continue\n" +
    "  h=(c.get('here') or '').split('//')[-1].split('/')[0]\n  a=h.split(':')[0]\n  if c.get('stump') and a and a!=ip:\n   cards[a]=(c,int(h.split(':')[1]) if ':' in h else 80)\n u.close()\n out=[]\n" +
    " for a,(c,pt) in cards.items():\n  ch=get(a,pt,'/chain.json') or {}\n  hw=get(a,pt,'/hw.json') or {}\n" +
    "  out.append({'name':c.get('name'),'lxmf':c.get('lxmf'),'release':c.get('release'),'ip':a,'role':ch.get('role'),'chain':ch.get('chain'),\n" +
    "   'ap_ip':ch.get('ap_ip'),'lan_ip':ch.get('lan_ip'),'secret':ch.get('secret'),'stumps':[[x.get('name'),x.get('http')] for x in ch.get('stumps') or []],\n" +
    "   'heltecs':[r.get('where') for r in hw.get('radios') or [] if r.get('where')]})\n" +
    " print('LOOK'+json.dumps({'joined':True,'ip':ip,'stumps':out}))\n";
  // The home Wi-Fi's password as the board has it (Settings, when it isn't
  // typed again): used on the board by Look, never shown.
  async function wifiPass(repl) {
    var m = /WPW(".*")/.exec(await repl.run("import json\ntry:\n import config as c\n print('WPW'+json.dumps(getattr(c,'WIFI_PASS','')))\nexcept Exception:\n print('WPW\"\"')\n"));
    return m ? JSON.parse(m[1]) : "";
  }
  async function scanWifi(repl) {
    var m = /SCAN(\{.*\})/.exec(await repl.run(LOOK_PY + "scan()\n", 30000));
    if (!m) throw new Error("the board couldn't scan the Wi-Fi");
    var o = JSON.parse(m[1]);
    return Object.keys(o).map(function (k) { return { ssid: k, rssi: o[k][0], channel: o[k][1], open: o[k][2] }; })
                 .sort(function (a, b) { return b.rssi - a.rssi; });
  }
  async function lookAround(repl, ssid, pw) {
    var m = /LOOK(\{.*\})/.exec(await repl.run(LOOK_PY + "look(" + py(ssid) + "," + py(pw || "") + ")\n", 90000));
    if (!m) throw new Error("the board couldn't look around the Wi-Fi");
    var r = JSON.parse(m[1]);
    r.why = { wrong: "the password is wrong", notfound: "the network wasn't found (this board only sees 2.4 GHz Wi-Fi)",
              refused: "the router refused it", timeout: "it didn't get on (is the password right?)" }[r.why] || r.why;
    return r;
  }
  // What the page offers from what the board found: the first Stumps on
  // this Wi-Fi (taking links here), the chain numbers taken (each Stump's
  // own Wi-Fi is 192.168.<number>.1) and the Heltec addresses taken.
  function offers(found, homeIp) {
    var st = found || [], numbers = {}, heltecs = {}, roots = [];
    function num(u) { var m = /192\.168\.(\d+)\.1\b/.exec(u || ""); if (m) numbers[+m[1]] = 1; }
    st.forEach(function (s) {
      num(s.ap_ip);
      (s.stumps || []).forEach(function (x) { (x[1] || []).forEach(num); });
      (s.heltecs || []).forEach(function (h) { if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) heltecs[h] = 1; });
      if (s.role === "root" && s.lan_ip === s.ip) roots.push(s);
    });
    var next = 5;
    while (numbers[next]) next++;
    var prefix = (homeIp || (roots[0] && roots[0].ip) || "192.168.0.1").split(".").slice(0, 3).join("."), h = HELTEC_FIRST;
    while (heltecs[prefix + "." + h] && h < 254) h++;
    return { roots: roots, numbers: Object.keys(numbers).map(Number).sort(function (a, b) { return a - b; }), number: next,
             heltecs: Object.keys(heltecs).sort(), heltec: prefix + "." + h };
  }
  // The chain's id from its secret, as chain.py makes it (sha256 of
  // "stump-chain:" + secret, the first 6 bytes in hex): a typed secret
  // checked against the first Stump's /chain.json, which never shows it.
  async function chainIdOf(secret) {
    var data = new TextEncoder().encode("stump-chain:" + secret), d;
    if (root.crypto && root.crypto.subtle) d = new Uint8Array(await root.crypto.subtle.digest("SHA-256", data));
    else d = new Uint8Array(require("crypto").createHash("sha256").update(Buffer.from(data)).digest());
    return Array.from(d.subarray(0, 6)).map(function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
  }

  var api = { Repl: Repl, prepare: prepare, restart: restart, upload: upload, uploadPlan: uploadPlan, haveOnBoard: haveOnBoard,
              clearLeftovers: clearLeftovers, flashUse: flashUse, boardInfo: boardInfo, memory: memory,
              scanWifi: scanWifi, lookAround: lookAround, offers: offers, chainIdOf: chainIdOf, wifiPass: wifiPass,
              IMAGE_FOR: IMAGE_FOR, imageForChip: imageForChip, imageInfo: imageInfo, notFrozen: notFrozen,
              removeFrozenCopies: removeFrozenCopies, removeRetired: removeRetired, listPacks: listPacks, removePack: removePack, packLeftovers: packLeftovers, PACK_MARK: PACK_MARK, readIdentity: readIdentity, sameIdentity: sameIdentity, putIdentity: putIdentity, keepsake: keepsake, readKeepsake: readKeepsake, keepsakeName: keepsakeName, identityFingerprint: identityFingerprint, HELTEC_ON_HOTSPOT: HELTEC_ON_HOTSPOT, HELTEC_FIRST: HELTEC_FIRST,
              CARD: CARD, USB_MAX: USB_MAX, cardStatus: cardStatus, wipeCard: wipeCard, toCard: toCard,
              readZip: readZip, packFrom: packFrom, installPack: installPack, fillTools: fillTools, shaHex: shaHex,
              readSettings: readSettings, writeSettings: writeSettings,
              packQuestions: packQuestions, packValues: packValues, packCurrent: packCurrent,
              apAddress: apAddress, hotspotName: hotspotName, heltecPairBreaks: heltecPairBreaks,
              kissFrame: kissFrame, KissReader: KissReader, heltecWifiPlan: heltecWifiPlan, rnodeWifi: rnodeWifi,
              heltecSetWifi: heltecSetWifi, RN: RN, readConfigFile: readConfigFile, putConfigFile: putConfigFile,
              settingsFrom: settingsFrom, answersFrom: answersFrom, bootSummary: bootSummary, randomSecret: function () { return hex(8); },
              readRadio: readRadio, writeRadio: writeRadio, radioFrom: radioFrom, radioAnswers: radioAnswers,
              RADIO_BW: RADIO_BW, RADIO_DEFAULT: RADIO_DEFAULT,
              FEATURES: FEATURES, THEMES: THEMES, LANGS: LANGS, MODES: MODES, sleep: sleep, b64: b64 };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.StumpRepl = api;
})(typeof window !== "undefined" ? window : globalThis);
