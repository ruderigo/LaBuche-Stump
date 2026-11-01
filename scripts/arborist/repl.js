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
    var out = await repl.run("import sys\nv=getattr(sys.implementation,'_mpy',0)\n" +
      "print('INFO', '.'.join(str(x) for x in sys.implementation.version[:3]), '%d.%d' % (v & 0xff, (v >> 8) & 3))\n");
    var m = /INFO (\S+) (\S+)/.exec(out) || [];
    return { release: m[1], mpy: m[2] };
  }

  // files: [{path, data: Uint8Array, sha256: hex}]. progress(done, total, path) in bytes.
  async function upload(repl, files, progress, chunk) {
    chunk = chunk || 3072;
    progress = progress || function () {};
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
        await repl.run("import binascii\nf=open(" + py(tmp) + ",'" + (off ? "ab" : "wb") + "')\n" +
                       "f.write(binascii.a2b_base64('" + b64(f.data.subarray(off, off + chunk)) + "'))\nf.close()\n");
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
    files.forEach(function (f) {
      if (/\.mpy$/.test(f.path)) gone.push(f.path.slice(0, -4) + ".py");
      else if (/\.py$/.test(f.path) && !/(^|\/)(boot|main|config)\.py$/.test(f.path)) gone.push(f.path.slice(0, -3) + ".mpy");
    });
    if (gone.length)
      await repl.run("import os\nfor p in " + JSON.stringify(gone) + ":\n try:\n  os.remove(p)\n except OSError:\n  pass\n");
    progress(total, total, "");
  }

  // -- settings: the board's config.py -------------------------------------
  // Read: the values the form shows (passwords only as "set or not").
  // Written by the firmware's own settings.write_config -- what /admin
  // uses: each KEY = value in place of its line, the rest of the file as
  // it was, a temporary file then a rename -- plus the Heltec bridge's
  // three lines inside CONFIG (as the Provisioner edits them). The old
  // file is kept as config.py.bak; a result that isn't Python is undone.
  var KEYS = ["NODE_NAME", "BOT_NAME", "MESH_GREETING", "WIFI_SSID", "AUTH_MODE", "FEATURES", "THEME", "SSID_NAME",
              "CHAIN_ENABLED", "CHAIN_UPLINK_SSID", "CHAIN_UPLINK_PASS", "CHAIN_SUBNET", "CHAIN_CHANNEL", "CHAIN_SECRET"];
  var FEATURES = ["chat", "billboard", "files", "about"], THEMES = ["paper", "amber", "phosphor", "oled"],
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

  // values: {KEY: value} for config.py, and HELTEC: {enabled, host, port}.
  async function writeSettings(repl, values) {
    var top = {}, h = values.HELTEC || null;
    Object.keys(values).forEach(function (k) { if (k !== "HELTEC") top[k] = values[k]; });
    var code =
      "import json, os, settings\nch=json.loads(" + py(JSON.stringify(top)) + ")\nH=json.loads(" + py(JSON.stringify(h)) + ")\n" +
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
    if (a.opw) {
      if (a.opw.length < 4 || a.opw.length > 64) return no("The BarKeep operator password: 4 to 64 characters.");
      v.FSERVBOT_OP_PASSWORD = a.opw;
    }
    // A new Stump needs at least one way into /admin.
    if (a.requireAdmin && !v.AUTH_ADMIN_PASSWORD && !a.hasPw && !v.FSERVBOT_OP_PASSWORD && !a.hasOpPw)
      return no("Set an admin password (or a BarKeep operator password for /admin to reuse).");
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
    if (MODES.indexOf(a.mode || "open") < 0) return no("Unknown access mode.");
    v.AUTH_MODE = a.mode || "open";
    var hs = (a.hotspot || "").trim();
    if (hs.length > 32 || ctl(hs)) return no("The Wi-Fi name it shows: at most 32 characters.");
    v.SSID_NAME = hs || null; v.SSID_INCLUDE_IP = false;
    var subnet = 4;
    if (a.chain === "first" || a.chain === "member") {
      var secret = (a.secret || "").trim();
      if (secret.length > 64 || /['"\\]/.test(secret) || ctl(secret)) return no("The chain secret: at most 64 characters, no quotes or backslashes.");
      if (!secret) warn.push("No chain secret: anyone on a Stump's Wi-Fi could join the chain.");
      v.CHAIN_ENABLED = true; v.CHAIN_SECRET = secret;
      if (a.chain === "first") {
        var ch = parseInt(a.channel, 10);
        if (!(ch >= 1 && ch <= 13)) return no("The chain's Wi-Fi channel: 1 to 13.");
        v.CHAIN_UPLINK_SSID = ""; v.CHAIN_UPLINK_PASS = ""; v.CHAIN_SUBNET = 4; v.CHAIN_CHANNEL = ch;
      } else {
        var up = (a.upSsid || "").trim();
        if (!up || up.length > 32 || ctl(up)) return no("The Wi-Fi name of the Stump it joins: 1 to 32 characters.");
        subnet = parseInt(a.subnet, 10);
        if (!(subnet >= 5 && subnet <= 254) || String(subnet) !== String(a.subnet).trim()) return no("This Stump's number in the chain: 5 to 254, a different one on each Stump.");
        v.CHAIN_UPLINK_SSID = up; v.CHAIN_UPLINK_PASS = a.upPass || ""; v.CHAIN_SUBNET = subnet; v.CHAIN_CHANNEL = 0;
      }
    } else {
      v.CHAIN_ENABLED = false;
    }
    if (a.radio === "lan") {
      var host = (a.hhost || "").trim();
      if (!/^(\d{1,3})(\.\d{1,3}){3}$/.test(host) || host.split(".").some(function (x) { return +x > 255; }))
        return no("The Heltec's address looks like 192.168.1.50.");
      if (!ssid) return no("A Heltec on the home Wi-Fi needs the home Wi-Fi's name.");
      v.HELTEC = { enabled: true, host: host, port: 7633 };
    } else if (a.radio === "ap") {
      v.HELTEC = { enabled: true, host: "192.168." + subnet + ".2", port: 7633 };
    } else {
      v.HELTEC = { enabled: false, host: a.hhost && /^[\d.]+$/.test(a.hhost) ? a.hhost : "192.168." + subnet + ".2", port: 7633 };
    }
    return { values: v, warn: warn };
  }

  // What readSettings found -> the form's answers.
  function answersFrom(c) {
    var chain = !c.CHAIN_ENABLED ? "none" : c.CHAIN_UPLINK_SSID ? "member" : "first";
    var subnet = chain === "member" ? c.CHAIN_SUBNET : 4, h = c.HELTEC || {};
    var radio = !h.enabled ? "none" : h.host === "192.168." + subnet + ".2" ? "ap" : "lan";
    return {
      name: c.NODE_NAME || "", bot: c.BOT_NAME || "BarKeep", greeting: c.MESH_GREETING || "",
      ssid: c.WIFI_SSID || "", radio: radio, hhost: radio === "lan" ? h.host : "",
      features: Array.isArray(c.FEATURES) ? c.FEATURES : FEATURES.slice(0, 3), theme: c.THEME || "paper",
      mode: c.AUTH_MODE || "open", hotspot: c.SSID_NAME || "", chain: chain,
      channel: c.CHAIN_CHANNEL || 11, upSsid: c.CHAIN_UPLINK_SSID || "", upPass: c.CHAIN_UPLINK_PASS || "",
      subnet: c.CHAIN_SUBNET || 5, secret: c.CHAIN_SECRET || "",
      hasPw: !!c.HAS_ADMIN_PASSWORD, hasOpPw: !!c.HAS_OP_PASSWORD, hasWifiPass: !!c.HAS_WIFI_PASS
    };
  }

  // What a Stump says as it starts -> {release, micropython, serving, wifi, problems}.
  function bootSummary(text) {
    var lines = text.replace(/\r/g, "").split("\n"), m;
    var out = { release: null, micropython: null, serving: false, ap: null, sta: null, problems: [], notes: [] };
    if ((m = /Project Stump -- (.+?) \(([^)]+)\)/.exec(text))) out.release = m[2];
    if ((m = /MicroPython (v[\w.\-]+)/.exec(text))) out.micropython = m[1];
    out.serving = /\[web\] serving/.test(text);
    lines.forEach(function (l) {
      if (/Traceback|Error|FAILED|rescue start|couldn't|not armed/i.test(l) && out.problems.length < 12) out.problems.push(l.trim());
      else if (/WiFi unavailable|\[watchdog\] armed|\[chain\]|\[radio\]|\[sd\]/i.test(l) && out.notes.length < 12) out.notes.push(l.trim());
    });
    return out;
  }

  var api = { Repl: Repl, prepare: prepare, restart: restart, upload: upload, boardInfo: boardInfo,
              readSettings: readSettings, writeSettings: writeSettings, readConfigFile: readConfigFile, putConfigFile: putConfigFile,
              settingsFrom: settingsFrom, answersFrom: answersFrom, bootSummary: bootSummary, randomSecret: function () { return hex(8); },
              readRadio: readRadio, writeRadio: writeRadio, radioFrom: radioFrom, radioAnswers: radioAnswers,
              RADIO_BW: RADIO_BW, RADIO_DEFAULT: RADIO_DEFAULT,
              FEATURES: FEATURES, THEMES: THEMES, MODES: MODES, sleep: sleep, b64: b64 };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.StumpRepl = api;
})(typeof window !== "undefined" ? window : globalThis);
