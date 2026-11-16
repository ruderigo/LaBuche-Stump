// Arborist's board side (scripts/arborist/repl.js) against a pretend board
// (board_sim.py: the REPL protocol, the code run by MicroPython's Unix port).
//   node repl_test.js <micropython> <scratch dir>
"use strict";
const { spawn } = require("child_process");
const fs = require("fs"), path = require("path"), crypto = require("crypto");
const R = require(path.join(__dirname, "../../scripts/arborist/repl.js"));
const MP = process.argv[2], SCRATCH = process.argv[3], TEMPLATE = process.argv[4], FW = process.argv[5];
const FAIL = [];
function check(name, ok, info) {
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  " + String(info === undefined ? "" : info).slice(0, 400)));
  if (!ok) FAIL.push(name);
}
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");

function board(root, extra) {
  fs.mkdirSync(root, { recursive: true });
  const p = spawn("python3", [path.join(__dirname, "board_sim.py"), "--root", root, "--micropython", MP].concat(extra || []),
                  { stdio: ["pipe", "pipe", "inherit"] });
  const repl = new R.Repl((u8) => new Promise((ok) => p.stdin.write(Buffer.from(u8), ok)));
  p.stdout.on("data", (d) => repl.feed(new Uint8Array(d)));
  p.on("exit", () => repl.closed(new Error("the board went away")));
  repl.stop = () => p.kill();
  return repl;
}

function someFiles() {
  const big = crypto.randomBytes(20000);                       // several chunks, and bytes of every value
  const files = [
    { path: "main.py", data: Buffer.from("print('hello from main')\n") },
    { path: "rrc.mpy", data: crypto.randomBytes(5000) },
    { path: "urns/crypto/pure25519/basic.mpy", data: big },
    { path: "urns/__init__.py", data: Buffer.from("") },
    { path: "web/opus.wasm", data: crypto.randomBytes(7001) },
  ];
  return files.map((f) => ({ path: f.path, data: new Uint8Array(f.data), sha256: sha(f.data) }));
}

async function scenario(name, extra, chunk) {
  const root = path.join(SCRATCH, name);
  fs.rmSync(root, { recursive: true, force: true });
  fs.mkdirSync(root, { recursive: true });
  fs.writeFileSync(path.join(root, "rrc.py"), "old source\n");             // shadowed by rrc.mpy: must go
  fs.mkdirSync(path.join(root, "urns"));
  fs.writeFileSync(path.join(root, "urns/__init__.mpy"), "stale");          // shadows the new __init__.py: must go
  fs.writeFileSync(path.join(root, "main.py"), "old main\n");
  const repl = board(root, extra);
  try {
    await R.prepare(repl, 8000);
    check(name + ": raw REPL entered" + (extra && extra.includes("--running") ? " (a running Stump stopped first)" : ""), true);
    const info = await R.boardInfo(repl);
    check(name + ": the board's MicroPython and .mpy version", /^\d+\.\d+\.\d+$/.test(info.release) && info.mpy === "6.3", JSON.stringify(info));
    const files = someFiles(), seen = [];
    await R.upload(repl, files, (d, t, p) => seen.push([d, t]), chunk);
    check(name + ": raw-paste " + (extra && extra.includes("--no-paste") ? "refused, plain raw used" : "used"),
          repl.rawPaste === !(extra && extra.includes("--no-paste")), repl.rawPaste);
    const same = files.every((f) => {
      const p = path.join(root, f.path);
      return fs.existsSync(p) && sha(fs.readFileSync(p)) === f.sha256;
    });
    check(name + ": every file arrived, byte for byte (folders made)", same);
    const left = [];
    (function walk(d) { for (const n of fs.readdirSync(d)) { const p = path.join(d, n); fs.statSync(p).isDirectory() ? walk(p) : left.push(path.relative(root, p)); } })(root);
    check(name + ": no .part left", !left.some((p) => p.endsWith(".part")), left);
    // Kit finding 15: the same files again -- none written (a WROVER's 1 MB
    // couldn't take opus.wasm twice); one changed -- only that one.
    const mt = fs.statSync(path.join(root, files[0].path)).mtimeMs;
    const again = await R.upload(repl, files, null, chunk);
    check(name + ": the same files again: none written, all skipped (and left as they were)",
          again.written === 0 && again.skipped === files.length && fs.statSync(path.join(root, files[0].path)).mtimeMs === mt, JSON.stringify(again));
    const changed = files.map((f, i) => i ? f : Object.assign({}, f, { data: new Uint8Array(Buffer.from("changed\n")), sha256: sha(Buffer.from("changed\n")) }));
    const one = await R.upload(repl, changed, null, chunk);
    check(name + ": one changed: only that one written", one.written === 1 && one.skipped === files.length - 1
          && fs.readFileSync(path.join(root, files[0].path), "utf8") === "changed\n", JSON.stringify(one));
    await R.upload(repl, files, null, chunk);
    fs.writeFileSync(path.join(root, "half.mpy.part"), "a write cut short");
    fs.writeFileSync(path.join(root, "pair.py"), "old"); fs.writeFileSync(path.join(root, "pair.mpy"), "new");
    const cleared = await R.clearLeftovers(repl);
    check(name + ": leftovers cleared when the room is short: a .part, a .py beside its .mpy; config.py and main.py kept",
          cleared >= 2 && !fs.existsSync(path.join(root, "half.mpy.part")) && !fs.existsSync(path.join(root, "pair.py"))
          && fs.existsSync(path.join(root, "pair.mpy")) && fs.existsSync(path.join(root, "main.py")), cleared);
    check(name + ": the .py a .mpy replaces is gone, and the .mpy a .py replaces",
          !fs.existsSync(path.join(root, "rrc.py")) && !fs.existsSync(path.join(root, "urns/__init__.mpy")), left);
    const total = files.reduce((a, f) => a + f.data.length, 0);
    check(name + ": progress goes up to the total", seen.length > 5 && seen[seen.length - 1][0] === total
          && seen.every((s, i) => !i || s[0] >= seen[i - 1][0]), JSON.stringify(seen.slice(-3)));
    // A file damaged on the way: refused, and the old one stays.
    fs.writeFileSync(path.join(root, "keep.py"), "the old one\n");
    let err = null;
    try { await R.upload(repl, [{ path: "keep.py", data: new Uint8Array(Buffer.from("new\n")), sha256: "0".repeat(64) }]); }
    catch (e) { err = e; }
    check(name + ": a file that arrives damaged: an error, the old file kept, no .part",
          err && /arrived damaged/.test(err.message) && fs.readFileSync(path.join(root, "keep.py"), "utf8") === "the old one\n"
          && !fs.existsSync(path.join(root, "keep.py.part")), err && err.message);
    const out = await repl.exec("print(1+1)\nraise ValueError('nope')\n");
    check(name + ": output and traceback come back apart", out.out.trim() === "2" && /ValueError: nope/.test(out.err), JSON.stringify(out));
    check(name + ": ... and the REPL still works after it", (await repl.run("print('again')")).trim() === "again");
  } catch (e) {
    check(name + ": ran through", false, e.stack + (e.tail ? "\n tail: " + JSON.stringify(e.tail) : ""));
  } finally { repl.stop(); }
}

(async () => {
  await scenario("paste", []);
  await scenario("paste_small_window", ["--window", "32"], 1024);
  await scenario("plain", ["--no-paste"]);
  await scenario("running", ["--running"]);

  // The board going away (cable out) mid-way: an error, not a hang.
  const root = path.join(SCRATCH, "gone");
  const repl = board(root);
  await repl.enterRaw(8000);
  let err = null, t0 = 0;
  try { await R.upload(repl, someFiles(), (d) => { if (d && !t0) { t0 = Date.now(); repl.stop(); } }); } catch (e) { err = e; }
  check("cable pulled mid-upload: says so at once", err && /went away/.test(err.message) && Date.now() - t0 < 5000, err && err.message);

  // -- the form's answers -> settings (the rules of /admin and the Provisioner) --
  const base = { name: "Café du Coin", bot: "BarKeep", greeting: "", pw: "s3cret", pw2: "s3cret", opw: "", requireAdmin: true,
                 ssid: "", wpass: "", radio: "none", features: ["chat", "files"], theme: "paper", mode: "open", hotspot: "", chain: "none" };
  const S = (o) => R.settingsFrom(Object.assign({}, base, o));
  let r = S({});
  check("settings: a plain new Stump (admin password set, BarKeep's left off)", !r.error && r.values.NODE_NAME === "Café du Coin"
        && r.values.AUTH_ADMIN_PASSWORD === "s3cret" && !("FSERVBOT_OP_PASSWORD" in r.values) && r.values.CHAIN_ENABLED === false
        && r.values.HELTEC.enabled === false && r.values.SSID_NAME === null, JSON.stringify(r));
  check("settings: the address not in the Wi-Fi's name by default", r.values.SSID_INCLUDE_IP === false);
  check("settings: the Stump's language, French unless chosen (1.0.0-beta.67)", r.values.DEFAULT_LANG === "fr"
        && S({ lang: "en" }).values.DEFAULT_LANG === "en" && /Unknown language/.test(S({ lang: "de" }).error || ""));
  check("... read back from config.py (an older one without it: French)", R.answersFrom({ DEFAULT_LANG: "es" }).lang === "es"
        && R.answersFrom({}).lang === "fr" && R.answersFrom({ DEFAULT_LANG: "xx" }).lang === "fr");
  r = S({ hotspot: "Café du Coin", hotspotIp: true, defaultSsid: "Stump" });
  check("settings: the address in the Wi-Fi's name when ticked (it fits)", !r.error && r.values.SSID_INCLUDE_IP === true
        && R.hotspotName("Café du Coin", true, 4).name === "Café du Coin 192.168.4.1", JSON.stringify(r.error || r.values.SSID_INCLUDE_IP));
  r = S({ hotspot: "A very long café name here!", hotspotIp: true });
  check("settings: a name too long with its address: refused, how much too long", r.error && /7 characters too long/.test(r.error), r.error);
  r = S({ hotspot: "A very long café name here!", hotspotIp: false });
  check("... the same name without the address: fine", !r.error, r.error);
  const B0 = { radio: "ap", hotspot: "Café", hotspotIp: false, subnet: 4 };
  check("a Heltec on its Wi-Fi, nothing that pairs it changed: no warning",
        R.heltecPairBreaks(B0, { radio: "ap", hotspot: "Café", hotspotIp: false, subnet: "4", ssid: "New home" }, "Stump").length === 0);
  const brk = R.heltecPairBreaks(B0, { radio: "ap", hotspot: "Café 2", hotspotIp: true, subnet: "9" }, "Stump");
  check("... its Wi-Fi's name, the address in it, its number changed: each said", brk.length === 3 && /192\.168\.4\.200/.test(brk[2]), JSON.stringify(brk));
  check("... a Heltec on the router, or none: no warning (it doesn't join this Wi-Fi)",
        R.heltecPairBreaks({ radio: "lan", hotspot: "Café", subnet: 4 }, { radio: "lan", hotspot: "Other", subnet: "9" }, "Stump").length === 0
        && R.heltecPairBreaks({ radio: "none", hotspot: "Café", subnet: 4 }, { radio: "none", hotspot: "Other", subnet: "9" }, "Stump").length === 0);
  r = S({ subnet: "7" });
  check("its number on a Stump on its own (1.0.0-beta.64): written, the address follows it", !r.error && r.values.CHAIN_SUBNET === 7
        && r.values.CHAIN_ENABLED === false, JSON.stringify(r.error || r.values.CHAIN_SUBNET));
  check("... no number given: 4, as before", S({}).values.CHAIN_SUBNET === 4);
  check("the address follows the number (192.168.<number>.1)", R.apAddress(9) === "192.168.9.1" && R.hotspotName("X", true, 9).name === "X 192.168.9.1");
  r = S({ opw: "barkeep99" });
  check("settings: both passwords, independent (as the Provisioner)", !r.error && r.values.AUTH_ADMIN_PASSWORD === "s3cret"
        && r.values.FSERVBOT_OP_PASSWORD === "barkeep99", JSON.stringify(r));
  r = S({ pw: "", pw2: "", opw: "barkeep99" });
  check("settings: the fserv bot's password alone: refused, a new Stump needs its admin one (1.0.0-beta.58, as the Provisioner)",
        /admin password/.test(r.error || ""), JSON.stringify(r));
  r = S({ samePw: true, opw: "ignored1" });
  check("settings: the same password for the fserv bot", !r.error && r.values.FSERVBOT_OP_PASSWORD === "s3cret", JSON.stringify(r));
  r = S({ pw: "", pw2: "", opw: "" });
  check("settings: a new Stump with neither password: refused", /admin password|BarKeep/.test(r.error || ""), JSON.stringify(r));
  r = S({ opw: "barkeep99", opw2: "barkeep98" });
  check("the fserv bot's password typed twice differently: refused", r.error && /aren't the same/.test(r.error), r.error);
  r = S({ opw: "ab" });
  check("settings: too-short fserv bot password: refused", /fserv bot's password/.test(r.error || ""), JSON.stringify(r));
  const errs = [
    [{ name: "" }, /name/], [{ name: "x".repeat(21) }, /name/], [{ pw: "abc", pw2: "abc" }, /password/], [{ pw2: "other" }, /aren't the same/],
    [{ wpass: "longenough" }, /no Wi-Fi name/], [{ ssid: "Home", wpass: "short" }, /8 to 63/],
    [{ radio: "lan", hhost: "300.1.1.1", ssid: "Home" }, /address/], [{ radio: "lan", hhost: "192.168.1.50" }, /home Wi-Fi/],
    [{ chain: "first", channel: "14", secret: "s" }, /channel/], [{ chain: "member", upSsid: "", subnet: "5" }, /joins/],
    [{ chain: "member", upSsid: "LaBuche-Stump.web.app", subnet: "1" }, /2 to 254/], [{ subnet: "255" }, /2 to 254/], [{ chain: "first", channel: "6", secret: "a'b" }, /quotes/],
    [{ greeting: "x".repeat(201) }, /greeting/], [{ theme: "neon" }, /theme/],
  ];
  const bad = errs.filter(([o, re]) => !re.test(S(o).error || ""));
  check("settings: each wrong answer refused, with why (" + errs.length + ")", !bad.length, JSON.stringify(bad.map(([o]) => [o, S(o)])));
  r = S({ requireAdmin: false, hasPw: true, hasOpPw: true, pw: "", pw2: "", opw: "" });
  check("settings: blank passwords on a Stump that has them: both kept (not written)", !r.error
        && !("AUTH_ADMIN_PASSWORD" in r.values) && !("FSERVBOT_OP_PASSWORD" in r.values), JSON.stringify(r));
  r = S({ requireAdmin: false, hasPw: true, hasOpPw: true, pw: "", pw2: "", opw: "newbar99" });
  check("settings: change only the BarKeep password, admin kept", !r.error && !("AUTH_ADMIN_PASSWORD" in r.values)
        && r.values.FSERVBOT_OP_PASSWORD === "newbar99", JSON.stringify(r));
  r = S({ chain: "member", upSsid: "LaBuche-Stump.web.app", upPass: "", subnet: "7", secret: "abc", radio: "ap" });
  check("settings: a chain member, its Heltec on its own Wi-Fi at 192.168.<its number>.200 (past what the hotspot hands out)",
        !r.error && r.values.CHAIN_SUBNET === 7 && r.values.CHAIN_CHANNEL === 0 && r.values.HELTEC.host === "192.168.7.200"
        && r.values.HELTEC.enabled && r.values.CHAIN_PARENT_IP === "", JSON.stringify(r));
  r = S({ chain: "lan", ssid: "Home", wpass: "homepass1", parentIp: "192.168.0.173", subnet: "8", secret: "sec" });
  check("settings: a member over the router (CHAIN_PARENT_IP): the home Wi-Fi as its uplink", !r.error && r.values.CHAIN_PARENT_IP === "192.168.0.173"
        && r.values.CHAIN_UPLINK_SSID === "Home" && r.values.CHAIN_UPLINK_PASS === "homepass1" && r.values.CHAIN_SUBNET === 8
        && r.values.CHAIN_CHANNEL === 0, JSON.stringify(r));
  r = S({ requireAdmin: false, hasPw: true, keepWifiPass: true, chain: "lan", ssid: "Home", wpass: "", parentIp: "192.168.0.173", subnet: "8", secret: "sec" });
  check("... its Wi-Fi password kept (not typed again): copied on the board", !r.error && r.values.CHAIN_UPLINK_PASS === "\u0000WIFI_PASS", JSON.stringify(r));
  const lanErrs = [[{ chain: "lan", ssid: "Home", parentIp: "192.168.0.173", subnet: "8", secret: "" }, /secret/],
                   [{ chain: "lan", ssid: "", parentIp: "192.168.0.173", subnet: "8", secret: "s" }, /home Wi-Fi/],
                   [{ chain: "lan", ssid: "Home", parentIp: "nope", subnet: "8", secret: "s" }, /address/],
                   [{ chain: "lan", ssid: "Home", parentIp: "192.168.0.173", subnet: "1", secret: "s" }, /2 to 254/]];
  const lanBad = lanErrs.filter(([o, re]) => !re.test(S(o).error || ""));
  check("settings: a router member's wrong answers refused (no secret, no home Wi-Fi, an address, the number)", !lanBad.length,
        JSON.stringify(lanBad.map(([o]) => [o, S(o)])));
  check("answers from a router member's config.py: read back as one", R.answersFrom({ CHAIN_ENABLED: true, CHAIN_PARENT_IP: "192.168.0.173",
        CHAIN_UPLINK_SSID: "Home", CHAIN_SUBNET: 8, HELTEC: { enabled: true, host: "192.168.8.2" } }).chain === "lan"
        && R.answersFrom({ CHAIN_ENABLED: true, CHAIN_UPLINK_SSID: "X", CHAIN_SUBNET: 8, HELTEC: { enabled: true, host: "192.168.8.2" } }).radio === "ap");
  r = S({ chain: "first", channel: "6", secret: "" });
  check("settings: the first Stump, no secret: allowed, with a warning", !r.error && r.values.CHAIN_SUBNET === 4 && r.values.CHAIN_CHANNEL === 6
        && r.warn.some((w) => /secret/.test(w)), JSON.stringify(r));

  // -- on a board: written by the firmware's settings.py, read back -----------
  {
    const root = path.join(SCRATCH, "settings");
    fs.rmSync(root, { recursive: true, force: true });
    fs.mkdirSync(root, { recursive: true });
    fs.copyFileSync(TEMPLATE, path.join(root, "config.py"));
    fs.copyFileSync(path.join(FW, "settings.py"), path.join(root, "settings.py"));
    fs.copyFileSync(path.join(FW, "lora_boards.py"), path.join(root, "lora_boards.py"));
    fs.copyFileSync(path.join(FW, "store.py"), path.join(root, "store.py"));
    // radio.py as-is, but its SETTINGS_FILE made board-root-relative so the Unix
    // port writes it in the sim's cwd (on a real board "/radio.json" is littlefs root).
    fs.writeFileSync(path.join(root, "radio.py"),
      fs.readFileSync(path.join(FW, "radio.py"), "utf8").replace('SETTINGS_FILE = "/radio.json"', 'SETTINGS_FILE = "radio.json"'));
    const tplText = fs.readFileSync(TEMPLATE, "utf8");
    const repl = board(root, ["--running"]);
    try {
      await R.prepare(repl, 8000);
      const want = S({ name: "L'Atelier \"2\"", opw: "barkeep99", ssid: "Home Net", wpass: "pa'ss\\word", radio: "lan", hhost: "192.168.1.50",
                       features: ["chat", "about"], theme: "oled", mode: "hybrid", hotspot: "Atelier", bot: "Bob",
                       greeting: "Hi, I'm Bob", chain: "member", upSsid: "First-Stump", upPass: "upPass12", subnet: "9", secret: "s3cr3t" });
      await R.writeSettings(repl, want.values);
      const cfg = fs.readFileSync(path.join(root, "config.py"), "utf8");
      const kept = tplText.split("\n").filter((l) => l.startsWith("#") && l.length > 20);
      check("written: the file's comments and other lines kept", kept.length > 20 && kept.every((l) => cfg.includes(l)), kept.filter((l) => !cfg.includes(l)).slice(0, 3));
      check("written: the old one kept as config.py.bak", fs.readFileSync(path.join(root, "config.py.bak"), "utf8") === tplText);
      const got = await R.readSettings(repl);
      const a = R.answersFrom(got);
      check("read back: what was written", a.name === "L'Atelier \"2\"" && a.ssid === "Home Net" && a.radio === "lan" && a.hhost === "192.168.1.50"
            && a.features.join() === "chat,about" && a.theme === "oled" && a.mode === "hybrid" && a.hotspot === "Atelier" && a.bot === "Bob"
            && a.greeting === "Hi, I'm Bob" && a.chain === "member" && a.upSsid === "First-Stump" && a.upPass === "upPass12" && a.subnet === 9
            && a.secret === "s3cr3t" && a.hasPw && a.hasOpPw && a.hasWifiPass, JSON.stringify(a));
      check("read: the passwords not sent to the page, only whether they're set", !("AUTH_ADMIN_PASSWORD" in got)
            && !("FSERVBOT_OP_PASSWORD" in got) && !("WIFI_PASS" in got)
            && !JSON.stringify(got).includes("pa'ss") && !JSON.stringify(got).includes("barkeep99"), JSON.stringify(got));
      const py = (await repl.run("import config as c\nprint(repr((c.WIFI_PASS, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.CHAIN_CHANNEL, c.SSID_INCLUDE_IP)))\n")).trim();
      check("... the two passwords are independent, as MicroPython reads them", py === "(\"pa'ss\\\\word\", 's3cret', 'barkeep99', 0, False)", py);
      // Changed again: blank passwords stay, the BarKeep one changes, a chain left.
      const again = R.settingsFrom(Object.assign({}, a, { requireAdmin: false, keepWifiPass: true, pw: "", pw2: "", opw: "newbar",
                                                          wpass: "", chain: "none", radio: "ap" }));
      await R.writeSettings(repl, again.values);
      const py2 = (await repl.run("import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
                                  "print(repr((c.WIFI_PASS, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.CHAIN_ENABLED, b['target_host'], b['enabled'])))\n")).trim();
      check("changed again: admin + Wi-Fi kept, BarKeep changed, out of the chain (its number kept: its Wi-Fi stays 192.168.9.1), Heltec on its Wi-Fi",
            py2 === "(\"pa'ss\\\\word\", 's3cret', 'newbar', False, '192.168.9.200', True)", py2);
      // A member over the router, its Wi-Fi password not typed again: the
      // home Wi-Fi's copied into CHAIN_UPLINK_PASS on the board.
      const lan = R.settingsFrom(Object.assign({}, a, { requireAdmin: false, keepWifiPass: true, pw: "", pw2: "", opw: "",
                                                        wpass: "", chain: "lan", parentIp: "192.168.0.173", subnet: "8", secret: "sec" }));
      await R.writeSettings(repl, lan.values);
      const py3 = (await repl.run("import sys\nsys.modules.pop('config', None)\nimport config as c\n" +
                                  "print(repr((c.CHAIN_PARENT_IP, c.CHAIN_UPLINK_SSID, c.CHAIN_UPLINK_PASS, c.CHAIN_SUBNET)))\n")).trim();
      check("a router member, its Wi-Fi password kept: copied on the board into CHAIN_UPLINK_PASS",
            py3 === "('192.168.0.173', 'Home Net', \"pa'ss\\\\word\", 8)", py3);
      // What a setup reads from the board starting (1.0.0-beta.60).
  {
    const ok = R.bootSummary("[mem] at boot: heap 4077 KB, 93 KB in use\nConnected! IP: 192.168.0.181 (esp32)\n" +
                             "[1][NOTE] Chain: linked to the Stump above at 192.168.0.173\n[2][NOTE] WiFi Serial connected to 192.168.0.223:7633\n[web] serving on port 80\n");
    check("boot: its home address, the chain linked, its Heltec reached, its memory", ok.home === "192.168.0.181" && ok.chain.linked === "192.168.0.173"
          && ok.heltec.ok === "192.168.0.223" && ok.heapKB === 4077 && ok.serving, JSON.stringify(ok));
    const bad = R.bootSummary("WiFi unavailable (WiFi connection timed out: the password is wrong)\n" +
                              "[3][WARN] Chain: no answer from the Stump above at 192.168.0.170 yet: is that its address\n" +
                              "[4][ERR] WiFi Serial initial connect failed: [Errno 113] EHOSTUNREACH\n");
    check("boot: why not: the Wi-Fi's password, the Stump above silent, the Heltec unreachable", !bad.home && bad.homeWhy === "the password is wrong"
          && bad.chain.noAnswer === "192.168.0.170" && bad.heltec.fail === "[Errno 113] EHOSTUNREACH" && !bad.heltec.ok, JSON.stringify(bad));
    const root = R.bootSummary("[chain] root on 192.168.4.1, channel 11 and on 192.168.0.173 (the Wi-Fi it joined)\n[5][NOTE] Chain: 192.168.0.179 joined below this one\n" +
                               "[6][WARN] Chain hello from 192.168.0.190 refused (wrong chain secret)\n");
    check("boot: a first Stump: its home address, who linked below, who was refused", root.home === "192.168.0.173" && root.chain.root
          && root.chain.below.join() === "192.168.0.179" && root.chain.refused.join() === "192.168.0.190", JSON.stringify(root));
    const o = R.offers([{ name: "LaBuche", ip: "192.168.0.173", role: "root", lan_ip: "192.168.0.173", ap_ip: "192.168.4.1",
                          stumps: [["LaBuche8", ["http://192.168.0.179/", "http://192.168.8.1/"]]], heltecs: ["192.168.0.222"] },
                        { name: "LaBuche10", ip: "192.168.0.180", role: "member", ap_ip: "192.168.10.1", heltecs: ["192.168.0.223"] },
                        { name: "LaBuche5", ip: "192.168.0.185", role: "member", ap_ip: "192.168.5.1", heltecs: [] }], "192.168.0.50");
    check("offers: the first Stump; the numbers taken and the next free; the Heltec addresses taken and the next free",
          o.roots.length === 1 && o.roots[0].name === "LaBuche" && o.numbers.join() === "4,5,8,10" && o.number === 6
          && o.heltecs.join() === "192.168.0.222,192.168.0.223" && o.heltec === "192.168.0.224", JSON.stringify(o));
    const cid = await R.chainIdOf("abc");
    check("chainIdOf: as chain.py makes it (sha256 of stump-chain:<secret>, 6 bytes)", cid === "29ea9d39caf0", cid);
  }
  // -- the radio (the CAM's host-controlled Heltec settings) --
      check("no radio saved yet: readRadio is null", (await R.readRadio(repl)) === null);
      const rf = R.radioFrom({ freqMhz: "915", bandwidth: 250000, sf: 10, cr: 7, txpower: 17 });
      check("radioFrom: MHz -> Hz, the canonical dict", !rf.error && rf.values.frequency === 915000000
            && rf.values.bandwidth === 250000 && rf.values.sf === 10 && rf.values.cr === 7 && rf.values.txpower === 17, JSON.stringify(rf));
      check("writeRadio reports it saved", (await R.writeRadio(repl, rf.values)) === true);
      check("written to /radio.json by the firmware's radio.save", fs.existsSync(path.join(root, "radio.json")));
      const back = await R.readRadio(repl);
      check("readRadio: what was written", back && ["frequency", "bandwidth", "sf", "cr", "txpower"].every((k) => back[k] === rf.values[k]), JSON.stringify(back));
      check("radioAnswers: back to the form (Hz -> MHz)", R.radioAnswers(back).freqMhz === "915"
            && R.radioAnswers(back).bandwidth === 250000 && R.radioAnswers(back).sf === 10, JSON.stringify(R.radioAnswers(back)));
      const rerr = [
        [{ freqMhz: "99", bandwidth: 125000, sf: 8, cr: 5, txpower: 22 }, /frequency/],
        [{ freqMhz: "868.8", bandwidth: 999, sf: 8, cr: 5, txpower: 22 }, /bandwidth/],
        [{ freqMhz: "868.8", bandwidth: 125000, sf: 13, cr: 5, txpower: 22 }, /spreading/],
        [{ freqMhz: "868.8", bandwidth: 125000, sf: 8, cr: 9, txpower: 22 }, /coding/],
        [{ freqMhz: "868.8", bandwidth: 125000, sf: 8, cr: 5, txpower: 30 }, /TX power/],
      ];
      check("radioFrom refuses out-of-range values", rerr.every(([o, re]) => re.test(R.radioFrom(o).error || "")),
            JSON.stringify(rerr.filter(([o, re]) => !re.test(R.radioFrom(o).error || "")).map(([o]) => [o, R.radioFrom(o)])));
      // config.py, whole: read, put back; something that isn't Python: refused.
      const file = await R.readConfigFile(repl);
      check("its config.py read whole, byte for byte", Buffer.from(file).equals(fs.readFileSync(path.join(root, "config.py"))));
      const before = fs.readFileSync(path.join(root, "config.py"));
      let err = null;
      try { const junk = new Uint8Array(Buffer.from("NODE_NAME = 'x\n")); await R.putConfigFile(repl, junk, sha(junk)); } catch (e) { err = e; }
      check("a file put back that isn't Python: refused, the board's kept", err && /not Python/.test(err.message)
            && fs.readFileSync(path.join(root, "config.py")).equals(before), err && err.message);
      const mine = new Uint8Array(Buffer.from(tplText));
      await R.putConfigFile(repl, mine, sha(mine));
      check("a config.py put back: on it as it is, the old one as .bak", fs.readFileSync(path.join(root, "config.py"), "utf8") === tplText
            && fs.readFileSync(path.join(root, "config.py.bak")).equals(before));
      fs.writeFileSync(path.join(root, "config.py"), "NODE_NAME = 'x'\nCONFIG = {'interfaces': []}\n");
      err = null;
      try { await R.writeSettings(repl, S({}).values); } catch (e) { err = e; }
      check("a config.py with no Heltec Bridge to set: says so", err && /Heltec Bridge/.test(err.message), err && err.message);
    } catch (e) {
      check("settings on a board: ran through", false, e.stack + (e.traceback ? "\n" + e.traceback : ""));
    } finally { repl.stop(); }
  }

  // -- the SD card: status, wipe, a pack, the Tools page ------------------------
  {
    const root = path.join(SCRATCH, "card");
    fs.rmSync(root, { recursive: true, force: true });
    const card = path.join(root, "card");                       // the card, inside the board's folder
    for (const d of ["shared/sub", "tools/provisioner", "home"]) fs.mkdirSync(path.join(card, d), { recursive: true });
    fs.writeFileSync(path.join(card, "shared/a.txt"), "x".repeat(100));
    fs.writeFileSync(path.join(card, "shared/sub/b.txt"), "y".repeat(30));
    fs.writeFileSync(path.join(card, "tools/provisioner/Stump-1.0.0-beta.1.zip"), "old zip");
    fs.writeFileSync(path.join(card, "fserv_ledger.json"), "{}");
    R.CARD.root = "card"; R.CARD.mount = "";
    // Packs made the Provisioner's way (hash_pack), zipped as a release does.
    const { spawnSync } = require("child_process");
    const mk = spawnSync("python3", ["-c", `
import sys, json, zipfile, shutil
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(path.join(FW, ".."))})
import provisioner as P
out = Path(sys.argv[1])
def make(name, plugin, tamper=False):
    d = out / (name + "-pack")
    shutil.rmtree(d, ignore_errors=True)
    (d / plugin).mkdir(parents=True)
    (d / "tools").mkdir()
    (d / "home").mkdir()
    (d / plugin / "install.py").write_text("def setup(stump):\\n    pass\\n")
    (d / plugin / "README.md").write_text("# readme\\n")
    pre = plugin.upper() + "_"
    (d / plugin / "plugin.json").write_text(json.dumps({"plugin": plugin, "title": plugin, "version": "1.0", "wizard": {"note": "n", "prompts": [
        {"key": pre + "ON", "prompt": "On?", "type": "choice", "options": ["off", "on"], "default": "off", "writes_to": "config.py"},
        {"key": pre + "ROOM", "prompt": "Room", "type": "string", "default": "lobby", "writes_to": "config.py", "advanced": True},
        {"key": pre + "EVERY", "prompt": "Every", "type": "int", "default": 60, "min": 10, "max": 3600, "writes_to": "config.py", "advanced": True},
        {"key": "CHAIN_SECRET", "prompt": "x", "type": "string", "default": "pwned", "writes_to": "config.py"},
        {"key": "WIFI_PASS", "prompt": "x", "type": "string", "default": "pwned", "writes_to": "config.py"},
        {"key": "OTHER_THING", "prompt": "x", "type": "string", "default": "x", "writes_to": "config.py"}]}}))
    (d / "tools" / "README.md").write_text("# " + name + " tools\\n")
    (d / "tools" / "thing.bin").write_bytes(bytes(range(256)) * 20)
    (d / "home" / "banner.txt").write_text("hello")
    (d / "pack.json").write_text(json.dumps({"name": name, "version": "1.0", "author": "t", "license": "MIT",
        "plugins": [plugin], "theme": {"name": name, "label": name, "colors": {"--bg": "#000"}}}))
    P.hash_pack(d)
    if tamper:
        (d / "tools" / "thing.bin").write_bytes(b"swapped")
    z = out / (name + "-pack-1.0.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(d.rglob("*")):
            if f.is_file():
                zf.write(f, d.name + "/" + f.relative_to(d).as_posix())
make("demo", "demo")
make("bad", "badplug", tamper=True)
make("clash", "fservbot")
with zipfile.ZipFile(out / "climb.zip", "w") as zf:
    zf.writestr("../evil.txt", "x")
`, root], { encoding: "utf8" });
    check("test packs made", mk.status === 0, mk.stderr);
    const zipOf = (n) => new Uint8Array(fs.readFileSync(path.join(root, n)));
    const repl = board(root);
    try {
      await R.prepare(repl, 8000);
      const st = await R.cardStatus(repl);
      check("card status: present, its size, what's in each folder", st.present && st.total > 0 && st.free > 0
            && JSON.stringify(st.dirs.shared) === "[130,2]" && st.dirs.tools[1] === 1 && st.dirs[""][1] === 1, JSON.stringify(st));
      const known = { core: ["rrc", "barkeep", "fserv"], builtin: ["fservbot", "stumpid"] };
      const demo = await R.packFrom(await R.readZip(zipOf("demo-pack-1.0.zip")), known);
      check("a pack: read and checked, its parts sorted out", demo.plan && demo.plan.plugins.map((f) => f.path).join() === "demo/install.py"
            && demo.plan.card.map((f) => f.path).sort().join() === "home/banner.txt,tools/demo/README.md,tools/demo/thing.bin"
            && demo.plan.theme.name === "demo", JSON.stringify(demo.error || demo.plan && demo.plan.card.map((f) => f.path)));
      const bad = await R.packFrom(await R.readZip(zipOf("bad-pack-1.0.zip")), known);
      check("a pack with a file changed after it was hashed: refused", bad.error && /changed since/.test(bad.error), JSON.stringify(bad.error));
      const clash = await R.packFrom(await R.readZip(zipOf("clash-pack-1.0.zip")), known);
      check("a pack replacing a built-in plugin: refused", clash.error && /built-in plugin/.test(clash.error), JSON.stringify(clash.error));
      let zerr = null;
      try { await R.readZip(zipOf("climb.zip")); } catch (e) { zerr = e; }
      check("a zip with a path that climbs out: refused whole", zerr && /climbs out/.test(zerr.message), zerr && zerr.message);
      const Q = demo.plan.questions;
      check("a pack's questions: its own settings only (DEMO_...), the Stump's refused (CHAIN_SECRET, WIFI_PASS, another prefix)",
            Q.length === 1 && Q[0].prompts.map((q) => q.key).join() === "DEMO_ON,DEMO_ROOM,DEMO_EVERY"
            && demo.plan.refused.sort().join() === "CHAIN_SECRET,OTHER_THING,WIFI_PASS", JSON.stringify([Q, demo.plan.refused]));
      let pv = R.packValues(Q, {}, {});
      check("... nothing changed, nothing on the board: the shown one's default written, the optional ones left to the plugin",
            JSON.stringify(pv.values) === '{"DEMO_ON":"off"}', JSON.stringify(pv));
      pv = R.packValues(Q, { DEMO_ON: { value: "on", touched: true } }, { DEMO_ON: "off", DEMO_ROOM: "kept" });
      check("... changed here: written; one already on the board and not changed: kept", JSON.stringify(pv.values) === '{"DEMO_ON":"on"}', JSON.stringify(pv));
      pv = R.packValues(Q, {}, { DEMO_ON: "on" });
      check("... a reinstall with nothing changed: nothing written (on stays on)", JSON.stringify(pv.values) === "{}", JSON.stringify(pv));
      check("... checked: a choice from its options, a whole number in its range",
            /one of off, on/.test(R.packValues(Q, { DEMO_ON: { value: "maybe", touched: true } }, {}).error || "")
            && /whole number/.test(R.packValues(Q, { DEMO_EVERY: { value: "ten", touched: true } }, {}).error || "")
            && /10 to 3600/.test(R.packValues(Q, { DEMO_EVERY: { value: "5", touched: true } }, {}).error || "")
            && R.packValues(Q, { DEMO_EVERY: { value: "120", touched: true } }, { DEMO_ON: "on" }).values.DEMO_EVERY === 120);
      fs.mkdirSync(path.join(root, "demo"), { recursive: true });
      fs.writeFileSync(path.join(root, "demo/install.mpy"), "stale compiled copy");
      await R.installPack(repl, demo.plan);
      const th = JSON.parse(fs.readFileSync(path.join(root, "themes.json"), "utf8"));
      fs.writeFileSync(path.join(root, "config.py"), "NODE_NAME = 'Demo'\nDEMO_ROOM = 'kept'\n");
      for (const f of ["settings.py", "store.py", "lora_boards.py"]) fs.copyFileSync(path.join(FW, f), path.join(root, f));
      const cur = await R.packCurrent(repl, ["DEMO_ON", "DEMO_ROOM", "DEMO_EVERY"]);
      check("its settings on the board read (only what's there)", JSON.stringify(cur) === '{"DEMO_ROOM":"kept"}', JSON.stringify(cur));
      const wv = R.packValues(Q, { DEMO_ON: { value: "on", touched: true } }, cur);
      await R.writeSettings(repl, wv.values);
      const cfgD = fs.readFileSync(path.join(root, "config.py"), "utf8");
      check("... the answer written into config.py, what was there kept, nothing of the Stump's touched",
            /DEMO_ON = 'on'/.test(cfgD) && /DEMO_ROOM = 'kept'/.test(cfgD) && /NODE_NAME = 'Demo'/.test(cfgD) && !/pwned|CHAIN_SECRET|WIFI_PASS/.test(cfgD), cfgD);
      check("installed: the plugin on the flash (no README, no plugin.json), an old compiled copy gone",
            fs.existsSync(path.join(root, "demo/install.py")) && !fs.existsSync(path.join(root, "demo/README.md"))
            && !fs.existsSync(path.join(root, "demo/plugin.json")) && !fs.existsSync(path.join(root, "demo/install.mpy")));
      check("... its tools in their section, its home files, its theme",
            fs.readFileSync(path.join(card, "tools/demo/thing.bin")).length === 5120 && fs.existsSync(path.join(card, "home/banner.txt"))
            && th.demo && th.demo.colors["--bg"] === "#000");
      // The Tools page.
      const tools = [["tools/provisioner/provisioner.py", "print('p')\n"], ["tools/provisioner/Stump-1.0.0-beta.9.zip", "new zip"],
                     ["tools/firefly/README.md", "# FireFly\n"]].map(([p, d]) => ({ path: p, data: new Uint8Array(Buffer.from(d)), sha256: sha(Buffer.from(d)) }));
      const ft = await R.fillTools(repl, tools);
      check("the Tools page filled; the older firmware zip gone", ft.byReader.length === 0
            && fs.readFileSync(path.join(card, "tools/provisioner/Stump-1.0.0-beta.9.zip"), "utf8") === "new zip"
            && !fs.existsSync(path.join(card, "tools/provisioner/Stump-1.0.0-beta.1.zip")) && fs.existsSync(path.join(card, "tools/firefly/README.md")));
      const n1 = await R.wipeCard(repl, "shared");
      check("wipe the shared files: they go, the folder stays, the rest stays", n1 === 2 && fs.existsSync(path.join(card, "shared"))
            && fs.readdirSync(path.join(card, "shared")).length === 0 && fs.existsSync(path.join(card, "tools/demo/thing.bin")), n1);
      const n2 = await R.wipeCard(repl, "all");
      check("wipe the whole card: nothing left on it; the flash untouched", n2 > 3 && fs.readdirSync(card).length === 0
            && fs.existsSync(path.join(root, "demo/install.py")), n2 + " " + fs.readdirSync(card));
      fs.rmSync(card, { recursive: true, force: true });
      const none = await R.cardStatus(repl);
      let nerr = null;
      try { await R.wipeCard(repl, "all"); } catch (e) { nerr = e; }
      check("no card: status says so, a wipe says so", none.present === false && nerr && /No SD card/.test(nerr.message), JSON.stringify(none));
    } catch (e) {
      check("the card on a board: ran through", false, e.stack + (e.traceback ? "\n" + e.traceback : ""));
    } finally { repl.stop(); R.CARD.root = "/sd"; }
  }

  // -- an update removes the files the release no longer has (1.0.0-beta.74) ---
  {
    const root = path.join(SCRATCH, "retired");
    fs.rmSync(root, { recursive: true, force: true });
    fs.mkdirSync(path.join(root, "lib"), { recursive: true });
    fs.mkdirSync(path.join(root, "meter"), { recursive: true });
    fs.mkdirSync(path.join(root, "fservbot"), { recursive: true });
    for (const [p, d] of [["flasher_ui.mpy", "old"], ["lib/ed25519_fast_xtensawin.mpy", "old"], ["config.py", "NODE_NAME = 'Kept'\n"],
                          ["rooms.json", "{}"], ["meter/install.py", "def setup(s):\n pass\n"], ["meter/old.py", "a pack's"],
                          ["fservbot/install.mpy", "x"], ["fservbot/gone.mpy", "old"]])
      fs.writeFileSync(path.join(root, p), d);
    const repl = board(root);
    try {
      await R.prepare(repl, 8000);
      const gone = await R.removeRetired(repl, ["flasher_ui.py", "flasher_ui.mpy", "lib/ed25519_fast_xtensawin.mpy",
                                                "meter/old.py", "fservbot/gone.mpy"], ["fservbot", "stumpid"]);
      check("an update: the files the release no longer has go (a .mpy, one in a folder, one in a built-in plugin)",
            !fs.existsSync(path.join(root, "flasher_ui.mpy")) && !fs.existsSync(path.join(root, "lib/ed25519_fast_xtensawin.mpy"))
            && !fs.existsSync(path.join(root, "fservbot/gone.mpy")) && gone.length === 3, JSON.stringify(gone));
      check("... its settings, its data and a pack's own file stay",
            fs.existsSync(path.join(root, "config.py")) && fs.existsSync(path.join(root, "rooms.json"))
            && fs.readFileSync(path.join(root, "meter/old.py"), "utf8") === "a pack's" && fs.existsSync(path.join(root, "fservbot/install.mpy")));
      check("... nothing to remove: nothing asked of the board", (await R.removeRetired(repl, [], [])).length === 0);
    } catch (e) {
      check("retired files on a board: ran through", false, e.stack + (e.traceback ? "\n" + e.traceback : ""));
    } finally { repl.stop(); }
  }

  // -- the Heltec's Wi-Fi: RNode serial commands, to a fake RNode ---------------
  {
    function fakeRNode(opts) {
      opts = opts || {};
      const eeprom = new Uint8Array(256).fill(0xFF), cfg = new Uint8Array(0x50).fill(0xFF), sent = [];
      eeprom[0xBA] = 0x00;
      const out = new R.KissReader();
      const io = { sent: sent, reader: new R.KissReader(), sleep: (ms) => R.sleep(Math.min(ms, 5)), reset: false };
      function reply(cmd, data) { io.reader.feed(R.kissFrame(cmd, Array.from(data))); }
      const handle = new R.KissReader();
      handle._got = function (f) {
        sent.push(f);
        const d = f.data;
        if (opts.silent) return;
        if (f.cmd === 0x08 && d[0] === 0x73) reply(0x08, [0x46]);
        else if (f.cmd === 0x50) reply(0x50, [1, 82]);
        else if (f.cmd === 0x51) reply(0x51, eeprom);
        else if (f.cmd === 0x6D) { if (!opts.old) reply(0x6D, cfg); }
        else if (f.cmd === 0x6B) { cfg.fill(0xFF, 0x00, 0x21); d.forEach((b, i) => { if (i < 33) cfg[i] = b; }); }
        else if (f.cmd === 0x6C) { cfg.fill(0xFF, 0x21, 0x42); d.forEach((b, i) => { if (i < 33) cfg[0x21 + i] = b; }); }
        else if (f.cmd === 0x84) { if (!opts.dropIp) d.forEach((b, i) => { cfg[0x42 + i] = b; }); }
        else if (f.cmd === 0x85) d.forEach((b, i) => { cfg[0x46 + i] = b; });
        else if (f.cmd === 0x6A) eeprom[0xBA] = d[0];
        else if (f.cmd === 0x55 && d[0] === 0xF8) io.reset = true;
      };
      io.write = async (u8) => { handle.feed(u8); };
      return io;
    }
    check("KISS frames: FEND and FESC in the data escaped", Buffer.from(R.kissFrame(0x6B, [0x41, 0xC0, 0xDB])).toString("hex") === "c06b41dbdcdbddc0");
    let plan = R.heltecWifiPlan({ ssid: "Café du Coin 192.168.4.1", psk: "", ip: "192.168.4.200" });
    check("the Heltec's Wi-Fi: an open Wi-Fi, its name (UTF-8) and address (192 escaped: it is the frame marker), as rnodeconf sends them",
          !plan.error && plan.open && plan.frames.length === 5 && plan.frames[0][1] === 0x6B && plan.frames[1][1] === 0x6C
          && Buffer.from(plan.frames[1]).toString("hex") === "c06c00c0" && Buffer.from(plan.frames[2]).toString("hex") === "c084dbdca804c8c0"
          && Buffer.from(plan.frames[4]).toString("hex") === "c06a01c0", JSON.stringify(plan.error));
    check("... checked: a name too long, a short password, an address that isn't one",
          /1 to 32/.test(R.heltecWifiPlan({ ssid: "x".repeat(33), ip: "192.168.4.200" }).error)
          && /8 to 32/.test(R.heltecWifiPlan({ ssid: "Home", psk: "short", ip: "192.168.0.222" }).error)
          && /looks like/.test(R.heltecWifiPlan({ ssid: "Home", ip: "192.168.4" }).error));
    let io = fakeRNode();
    let res = await R.heltecSetWifi(io, plan);
    check("set up on an RNode: it said hello, kept the name, the address, open, station mode; then restarted",
          res.ok && res.fw === "1.82" && res.after.ssid === "Café du Coin 192.168.4.1" && res.after.ip === "192.168.4.200"
          && res.after.hasPsk === false && res.after.mode === "station" && res.after.nm === "255.255.255.0" && io.reset
          && res.before.ssid === "" && res.before.mode === "off", JSON.stringify(res));
    plan = R.heltecWifiPlan({ ssid: "Home Net", psk: "pa'ss1234", ip: "192.168.0.222" });
    io = fakeRNode();
    res = await R.heltecSetWifi(io, plan);
    check("... the home Wi-Fi, with its password", res.ok && res.after.hasPsk === true && res.after.ip === "192.168.0.222");
    io = fakeRNode({ dropIp: true });
    res = await R.heltecSetWifi(io, R.heltecWifiPlan({ ssid: "X", ip: "192.168.4.200" }));
    check("... settings that didn't stick: not ok, no restart", !res.ok && !io.reset, JSON.stringify(res.after));
    let err = null;
    try { await R.heltecSetWifi(fakeRNode({ old: true }), R.heltecWifiPlan({ ssid: "X", ip: "192.168.4.200" })); } catch (e) { err = e; }
    check("an RNode firmware without Wi-Fi settings: says to update it first", err && /doesn't take Wi-Fi settings/.test(err.message), err && err.message);
    err = null;
    try { await R.heltecSetWifi(fakeRNode({ silent: true }), R.heltecWifiPlan({ ssid: "X", ip: "192.168.4.200" })); } catch (e) { err = e; }
    check("something else on the port (no RNode hello): says which board to choose", err && /No answer from an RNode/.test(err.message), err && err.message);
  }

  const sum = R.bootSummary("ESP-ROM\r\nMicroPython v1.28.0 on 2026\r\nProject Stump -- Beta A (1.0.0-beta.38)\r\n[watchdog] armed: x\r\n" +
                            "WiFi unavailable (no AP)\r\nTraceback (most recent call last):\r\nOSError: [Errno 19] ENODEV\r\n[web] serving on 192.168.4.1:80\r\n");
  check("what it said at start, summed up", sum.release === "1.0.0-beta.38" && sum.micropython === "v1.28.0" && sum.serving
        && sum.problems.length === 2 && sum.notes.some((n) => /WiFi unavailable/.test(n)), JSON.stringify(sum));
  
  // -- the room an update takes (kit finding 15) -------------------------------
  {
    const f = (p, n, h) => ({ path: p, data: new Uint8Array(n), sha256: h || "a".repeat(64) });
    const files = [f("web/opus.wasm", 333000, "1".repeat(64)), f("rrc.mpy", 20000, "2".repeat(64)), f("new.mpy", 5000, "3".repeat(64))];
    const have = { files: { "web/opus.wasm": ["1".repeat(64), 333000], "rrc.mpy": ["9".repeat(64), 18000] }, free: 60000 };
    const plan = R.uploadPlan(files, have);
    check("an update's plan: opus.wasm already there and the same is skipped; the room it needs: what it adds + the largest + a margin",
          plan.skip.length === 1 && plan.write.length === 2 && plan.need === (2000 + 5000) + 20000 + 16384 && !plan.short, JSON.stringify({ need: plan.need, short: plan.short }));
    const fresh = R.uploadPlan(files, { files: {}, free: 300000 });
    check("... with nothing there and 300 KB free: short (opus.wasm alone needs 333 KB)", fresh.short && fresh.need > 300000, fresh.need);
    // Not enough room even after the leftovers: said before anything is written.
    const said = [];
    const stub = { run: async (code) => {
      said.push(code);
      if (/print\('HAVE'/.test(code)) return 'HAVE{"files":{},"free":100000}';
      if (/GONE/.test(code)) return "GONE3";
      if (/print\('USE'/.test(code)) return 'USE{"bavard/":250000,"web/":540000,"rns/":90000,"config.py":4000}';
      throw new Error("wrote something: " + code.slice(0, 60));
    } };
    let err = null;
    try { await R.upload(stub, files); } catch (e) { err = e; }
    check("a full board: stopped before writing anything, and says what takes the room",
          err && /storage is full/.test(err.message) && /Nothing was written/.test(err.message) && /web\/ 527 KB/.test(err.message)
          && /bavard\/ 244 KB/.test(err.message) && /3 leftovers/.test(err.message) && !said.some((c) => /a2b_base64/.test(c)), err && err.message);
    // Filling up while writing (a board fuller than it said): plain words, the old one kept.
    const stub2 = { run: async (code) => {
      if (/print\('HAVE'/.test(code)) return 'HAVE{"files":{},"free":10000000}';
      if (/a2b_base64/.test(code)) throw new Error("Traceback (most recent call last):\nOSError: 28");
      return "";
    } };
    err = null;
    try { await R.upload(stub2, [f("web/opus.wasm", 4000, "1".repeat(64))]); } catch (e) { err = e; }
    check("OSError 28 while writing: \"the board's storage filled up\", which file, the old one still there",
          err && /storage filled up while writing web\/opus\.wasm/.test(err.message) && /old one is still there/.test(err.message), err && err.message);
  }
  console.log("FAILURES: " + JSON.stringify(FAIL));
  process.exit(FAIL.length ? 1 : 0);
})();
