// The built Arborist page, opened from file:// in Chromium, Web Serial
// replaced by a pipe to the pretend board (board_sim.py), and esptool-js by
// a stand-in (the flashing itself is esptool-js's, tested upstream): a new
// Stump made, and one updated, end to end.
//   node page_test.js <Arborist.html> <micropython> <scratch dir>
"use strict";
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), path = require("path");
const [PAGE, MP, SCRATCH] = process.argv.slice(2);
const FAIL = [];
function check(name, ok, info) {
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : "  " + String(info === undefined ? "" : info).slice(0, 600)));
  if (!ok) FAIL.push(name);
}

// In the page, before its own scripts: a navigator.serial whose one port is the pipe.
const FAKE_SERIAL = `
(() => {
  const listeners = [];
  window.__simFeed = (b64) => { const s = atob(b64), u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); listeners.forEach((f) => f(u)); };
  class FakePort {
    constructor() { this.readable = null; this.writable = null; this.opens = 0; }
    getInfo() { return { usbVendorId: 0x1a86, usbProductId: 0x55d3 }; }
    async open(o) {
      if (this.readable) throw new Error("The port is already open.");
      this.opens++; window.__opens = this.opens; window.__baud = o.baudRate;
      let ctl;
      this.readable = new ReadableStream({ start(c) { ctl = c; } });
      const feed = (u) => { try { ctl.enqueue(u); } catch (e) {} };
      listeners.push(feed);
      this._feed = feed;
      this.writable = new WritableStream({ write: (u) => window.__simWrite(btoa(String.fromCharCode.apply(null, u))) });
    }
    async setSignals() {}
    async close() { listeners.splice(listeners.indexOf(this._feed), 1); this.readable = this.writable = null; }
  }
  const port = new FakePort();
  Object.defineProperty(navigator, "serial", { value: { requestPort: async () => port, getPorts: async () => [port] } });
  // esptool-js stand-in: says what it was asked to write.
  window.__flashed = null;
  const fake = {
    Transport: class { constructor(p) { this.p = p; } async disconnect() {} },
    ESPLoader: class {
      constructor(o) { this.o = o; }
      async main() { this.o.terminal.writeLine("Chip is ESP32-S3 (QFN56)"); return "ESP32-S3 (QFN56) (revision v0.2)"; }
      async writeFlash(a) { window.__flashed = { n: a.fileArray.length, len: a.fileArray[0].data.length, addr: a.fileArray[0].address, erase: a.eraseAll };
                            for (let i = 0; i <= 4; i++) a.reportProgress(0, i * 25, 100); }
      async after(m) { window.__after = m; }
    }
  };
  Object.defineProperty(window, "esptool", { get: () => fake, set: () => {} });
})();`;

async function run(name, root, simArgs, fill, keep) {
  if (!keep) {
    fs.rmSync(root, { recursive: true, force: true });
    fs.mkdirSync(root, { recursive: true });
  }
  if (fill) fill(root);
  const sim = spawn("python3", [path.join(__dirname, "board_sim.py"), "--root", root, "--micropython", MP].concat(simArgs),
                    { stdio: ["pipe", "pipe", "inherit"] });
  const browser = await chromium.launch();
  const page = await browser.newPage({ acceptDownloads: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("dialog", (d) => d.accept());
  await page.exposeFunction("__simWrite", (b64) => new Promise((ok) => sim.stdin.write(Buffer.from(b64, "base64"), ok)));
  sim.stdout.on("data", (d) => page.evaluate((b) => window.__simFeed(b), d.toString("base64")).catch(() => {}));
  await page.addInitScript(FAKE_SERIAL);
  await page.goto("file://" + path.resolve(PAGE));
  return { page, browser, sim, errors };
}

(async () => {
  // -- the page itself, no board -------------------------------------------------
  {
    const browser = await chromium.launch();
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("file://" + path.resolve(PAGE));
    check("opened from file://: the form, not the gate (Chromium has Web Serial)", await page.isVisible("#f-name") && !(await page.isVisible("#gate")));
    check("no errors on load", errors.length === 0, errors.join("\n"));
    await page.click("#make-go");
    check("an empty password: the form says so", /password/i.test(await page.textContent("#make-err")), await page.textContent("#make-err"));
    await page.fill("#f-pw", "secret1"); await page.fill("#f-pw2", "secret2");
    await page.click("#make-go");
    check("two passwords that differ: says so", /aren't the same/.test(await page.textContent("#make-err")), await page.textContent("#make-err"));
    await page.fill("#f-pw2", "secret1");
    await page.check("input[name=f-radio][value=lan]");
    await page.fill("#f-hhost", "not an address");
    await page.click("#make-go");
    check("a Heltec address that isn't one: says so", /192\.168/.test(await page.textContent("#make-err")), await page.textContent("#make-err"));
    await page.check("input[name=mode][value=update]");
    check("update: the settings form goes away", !(await page.isVisible("#f-name")) && /Update/.test(await page.textContent("#make-go")));
    await page.click("nav button[data-tab=settings]");
    check("Settings: no form until a Stump's are read", !(await page.isVisible("#f-name")) && await page.isVisible("#set-read"));
    await page.click("nav button[data-tab=check]");
    check("Check: its button", await page.isVisible("#chk-go"));
    const ver = await page.textContent(".sub");
    check("the page names its release", /Stump 1\.0\.0-\S+/.test(ver), ver);
    await page.click("nav button[data-tab=make]");
    await page.check("input[name=mode][value=new]");
    await page.check("input[name=f-radio][value=none]");
    check("radio section hidden when there's no Heltec", !(await page.isVisible("#f-radio-params")));
    await page.check("input[name=f-radio][value=ap]");
    check("radio section appears with a Heltec, drop-downs filled", await page.isVisible("#f-radio-params")
          && (await page.locator("#f-bw option").count()) === 10 && (await page.locator("#f-sf option").count()) === 8
          && (await page.locator("#f-cr option").count()) === 4);
    await browser.close();
  }

  // -- a new Stump --------------------------------------------------------------------
  {
    const root = path.join(SCRATCH, "page_new");
    const t = await run("new", root, []);
    const { page } = t;
    await page.fill("#f-name", "Café du Coin");
    await page.fill("#f-pw", "s3cret!"); await page.fill("#f-pw2", "s3cret!");
    await page.fill("#f-opw", "barkeep55");
    await page.fill("#f-ssid", "Home \"Net\""); await page.fill("#f-wpass", "pa'ss1234");
    await page.check("input[name=f-radio][value=ap]");
    check("radio section shows when a Heltec is enabled", await page.isVisible("#f-radio-params")
          && (await page.inputValue("#f-freq")) === "868.8" && (await page.inputValue("#f-bw")) === "125000");
    await page.fill("#f-freq", "915"); await page.selectOption("#f-sf", "10");
    await page.click("#make-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 240000 }); } catch (e) {}
    const title = await page.textContent("#worktitle"), result = await page.textContent("#result");
    check("a new Stump: done", /Done/.test(title), title + " | " + result + " | " + (await page.textContent("#log")).slice(-800));
    const fl = await page.evaluate(() => window.__flashed);
    check("... MicroPython written at 0, the board erased first, then reset", fl && fl.addr === 0 && fl.erase && fl.len > 500000
          && (await page.evaluate(() => window.__after)) === "hard_reset", JSON.stringify(fl));
    check("... then the REPL at 115200", (await page.evaluate(() => window.__baud)) === 115200);
    check("... the Stump's files on the board, compiled", fs.existsSync(path.join(root, "barkeep.mpy")) && fs.existsSync(path.join(root, "main.py"))
          && fs.existsSync(path.join(root, "urns")) && !fs.existsSync(path.join(root, "barkeep.py")));
    check("... the plugins too (their plugin.json stays on the computer, as with the Provisioner)", fs.existsSync(path.join(root, "stumpid/core.mpy")) && !fs.existsSync(path.join(root, "stumpid/plugin.json")) && fs.existsSync(path.join(root, "fservbot/core.mpy")));
    const cfgPath = path.join(root, "config.py");
    check("... and a config.py", fs.existsSync(cfgPath));
    if (fs.existsSync(cfgPath)) {
      const cfg = fs.readFileSync(cfgPath, "utf8");
      fs.writeFileSync(path.join(root, "lora_boards.py"), "LORA_BOARDS = {}\n");
      const out = execFileSync(MP, ["-c", "import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
        "print(repr((c.NODE_NAME, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.WIFI_SSID, c.WIFI_PASS, b['enabled'], b['target_host'], b['target_port'], c.CHAIN_ENABLED, c.CHAIN_SECRET, c.BOT_NAME)))"],
        { cwd: root }).toString().trim();
      check("... with the answers (the two passwords independent), as MicroPython reads them", out === "('Caf\u00e9 du Coin', 's3cret!', 'barkeep55', 'Home \"Net\"', \"pa'ss1234\", True, '192.168.4.2', 7633, False, '', 'BarKeep')", out);
      check("... nothing of LaBuche's own config.py", !/LaBuche'|TeK|SalutComment|Bob's Glitch|Concierge/.test(cfg));
    }
    check("... no errors in the page", t.errors.length === 0, t.errors.join("\n"));
    await t.browser.close(); t.sim.kill();
  }

  // -- Settings: read, changed, saved; a copy kept; one put back --------------------
  {
    const root = path.join(SCRATCH, "page_new");
    const cfgPath = path.join(root, "config.py");
    const t = await run("settings", root, ["--running"], null, true);
    const { page } = t;
    const wait = async () => {           // this action's end, not the one before's
      try { await page.waitForFunction(() => !document.querySelector("#worktitle span"), null, { timeout: 10000 }); } catch (e) {}
      try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 120000 }); } catch (e) {}
      return page.textContent("#worktitle");
    };
    await page.click("nav button[data-tab=settings]");
    await page.click("#set-read");
    let title = await wait();
    check("Settings: read (a running Stump stopped, then started again)", /Done/.test(title), title + " | " + (await page.textContent("#result")) + (await page.textContent("#log")).slice(-500));
    check("... the form shows its settings", (await page.inputValue("#f-name")) === "Café du Coin" && (await page.inputValue("#f-ssid")) === 'Home "Net"'
          && (await page.isChecked("input[name=f-radio][value=ap]")), await page.inputValue("#f-name"));
    check("... its passwords not shown: blank means unchanged (both admin and BarKeep)", (await page.inputValue("#f-pw")) === ""
          && (await page.getAttribute("#f-pw", "placeholder")) === "unchanged" && (await page.getAttribute("#f-opw", "placeholder")) === "unchanged");
    const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#set-download")]);
    const copy = fs.readFileSync(await dl.path());
    check("a copy of its config.py, as on the board", copy.equals(fs.readFileSync(cfgPath)) && /^config-Caf.*\.py$/.test(dl.suggestedFilename()), dl.suggestedFilename());
    await page.fill("#f-name", "Le Coin");
    await page.click("#f-more summary");
    await page.check("input[name=f-chain][value=member]");
    await page.fill("#f-up", "First-Stump");
    await page.fill("#f-subnet", "6");
    await page.fill("#f-opw", "newbar42");            // change only the BarKeep password
    check("a chain member: a secret made for it", /^[0-9a-f]{16}$/.test(await page.inputValue("#f-secret")));
    await page.click("#set-save");
    title = await wait();
    check("... saved", /Done/.test(title), title + " | " + (await page.textContent("#result")) + (await page.textContent("#log")).slice(-500));
    fs.writeFileSync(path.join(root, "lora_boards.py"), "LORA_BOARDS = {}\n");
    const out = execFileSync(MP, ["-c", "import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
      "print(repr((c.NODE_NAME, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.WIFI_PASS, c.CHAIN_ENABLED, c.CHAIN_UPLINK_SSID, c.CHAIN_SUBNET, b['target_host'])))"], { cwd: root }).toString().trim();
    check("... as MicroPython reads them: name + chain changed, admin + Wi-Fi kept, BarKeep password changed on its own",
          out === "('Le Coin', 's3cret!', 'newbar42', \"pa'ss1234\", True, 'First-Stump', 6, '192.168.6.2')", out);
    fs.appendFileSync(cfgPath, "\n# changed by someone else\n");
    await page.fill("#f-name", "Other");
    await page.click("#set-save");
    title = await wait();
    check("saving over a config.py that changed since it was read: refused", /Stopped/.test(title) && /read earlier/.test(await page.textContent("#result")), await page.textContent("#result"));
    check("... and the Stump started again, not left stopped at its prompt", /restarted the board/.test(await page.textContent("#log")));
    await page.click("#tab-settings > details > summary");
    await page.setInputFiles("#set-file", { name: "config-old.py", mimeType: "text/x-python", buffer: copy });
    await page.click("#set-put");
    title = await wait();
    check("a kept copy put back: the board has it again", /Done/.test(title) && fs.readFileSync(cfgPath).equals(copy), title + (await page.textContent("#result")) + (await page.textContent("#log")).slice(-1500));
    check("... no errors in the page", t.errors.length === 0, t.errors.join("\n"));
    await t.browser.close(); t.sim.kill();
  }

  // -- Check: restarted, listened to ---------------------------------------------------
  {
    const root = path.join(SCRATCH, "page_new");
    const t = await run("check", root, ["--running"], null, true);
    const { page } = t;
    await page.click("nav button[data-tab=check]");
    await page.click("#chk-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 120000 }); } catch (e) {}
    const sum = await page.textContent("#chk-sum");
    check("Check: what it runs, its pages up", /1\.0\.0-beta\.38/.test(sum) && /up/.test(sum) && /v1\.28/.test(sum), sum + " | " + (await page.textContent("#result")));
    check("... what it said, on the page", /\[web\] serving/.test(await page.textContent("#chk-log")));
    await t.browser.close(); t.sim.kill();
  }

  // -- updating a Stump -----------------------------------------------------------------
  {
    const root = path.join(SCRATCH, "page_update");
    const t = await run("update", root, ["--running"], (r) => {
      fs.writeFileSync(path.join(r, "config.py"), "NODE_NAME = 'Mine'\n");
      fs.mkdirSync(path.join(r, "rns")); fs.writeFileSync(path.join(r, "rns/identity"), "its identity");
      fs.writeFileSync(path.join(r, "barkeep.py"), "old");
    });
    const { page } = t;
    await page.check("input[name=mode][value=update]");
    await page.click("#make-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 240000 }); } catch (e) {}
    const title = await page.textContent("#worktitle");
    check("update: done (the running Stump stopped first)", /Done/.test(title), title + " | " + (await page.textContent("#result")) + " | " + (await page.textContent("#log")).slice(-600));
    check("... nothing flashed", (await page.evaluate(() => window.__flashed)) === null);
    check("... its config.py and identity kept", fs.readFileSync(path.join(root, "config.py"), "utf8") === "NODE_NAME = 'Mine'\n"
          && fs.readFileSync(path.join(root, "rns/identity"), "utf8") === "its identity");
    check("... the new files there, the old source gone", fs.existsSync(path.join(root, "barkeep.mpy")) && !fs.existsSync(path.join(root, "barkeep.py")));
    await t.browser.close(); t.sim.kill();
  }

  // -- updating a board with no Stump: refused ---------------------------------------
  {
    const root = path.join(SCRATCH, "page_update_none");
    const t = await run("none", root, []);
    await t.page.check("input[name=mode][value=update]");
    await t.page.click("#make-go");
    try { await t.page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 60000 }); } catch (e) {}
    const res = await t.page.textContent("#result");
    check("update on a board with no Stump: stopped, with why", /no Stump on this board/.test(res), res);
    check("... nothing copied", !fs.existsSync(path.join(root, "barkeep.mpy")));
    await t.browser.close(); t.sim.kill();
  }
  console.log("FAILURES: " + JSON.stringify(FAIL));
  process.exit(FAIL.length ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + e.stack); console.log("FAILURES: [\"crashed\"]"); process.exit(1); });
