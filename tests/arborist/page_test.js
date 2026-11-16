// The built Arborist page, opened from file:// in Chromium, Web Serial
// replaced by a pipe to the pretend board (board_sim.py), and esptool-js by
// a stand-in (the flashing itself is esptool-js's, tested upstream): a new
// Stump made, and one updated, end to end.
//   node page_test.js <Arborist.html> <micropython> <scratch dir> [<Arborist.html with images>]
// With the second page (built with stand-in Stump images, 1.0.0-beta.58):
// a new CAM and a new WROVER get their image, a Stump on an image is updated
// in place, one with an older layout refused; a router chain member and the
// Heltec's addresses written as the Provisioner writes them.
"use strict";
const { chromium } = require("playwright");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), path = require("path"), nodeCrypto = require("crypto");
const [PAGE, MP, SCRATCH, PAGE_IMAGES] = process.argv.slice(2);
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
      async main() { const c = window.__chip || "ESP32-S3 (QFN56) (revision v0.2)"; this.o.terminal.writeLine("Chip is " + c); return c; }
      async writeFlash(a) { const d = a.fileArray[0].data;
                            window.__flashed = { n: a.fileArray.length, len: d.length, addr: a.fileArray[0].address, erase: a.eraseAll,
                                                 tag: String.fromCharCode.apply(null, d.subarray(1, 9)) };
                            for (let i = 0; i <= 4; i++) a.reportProgress(0, i * 25, 100); }
      async after(m) { window.__after = m; }
    }
  };
  Object.defineProperty(window, "esptool", { get: () => fake, set: () => {} });
})();`;

async function run(name, root, simArgs, fill, keep, pageFile, chip) {
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
  await page.addInitScript("window.__listenMs = 3000;");    // a setup's listening, short (the sim prints no boot)
  if (chip) await page.addInitScript("window.__chip = " + JSON.stringify(chip) + ";");
  await page.goto("file://" + path.resolve(pageFile || PAGE));
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
    await page.uncheck("#f-same");
    await page.fill("#f-opw", "botonly1");
    check("the fserv bot's password typed once: asks for it again, as it's typed", /once more/i.test(await page.textContent("#f-opw-match")),
          await page.textContent("#f-opw-match"));
    await page.fill("#f-opw2", "botonly2");
    check("... typed differently: \"not the same\", in red, at once", /Not the same/.test(await page.textContent("#f-opw-match"))
          && (await page.getAttribute("#f-opw-match", "class")) === "err" && /mismatch/.test(await page.getAttribute("#f-opw2", "class")));
    await page.click("#make-go");
    check("... and Make refuses it", /aren't the same/.test(await page.textContent("#make-err")), await page.textContent("#make-err"));
    await page.fill("#f-opw2", "botonly1");
    check("... the same: says so, the red gone", /are the same/.test(await page.textContent("#f-opw-match"))
          && !/mismatch/.test(await page.getAttribute("#f-opw2", "class") || ""));
    await page.click("#make-go");
    check("the fserv bot's password alone: not enough, the admin one is needed", /admin password/i.test(await page.textContent("#make-err")),
          await page.textContent("#make-err"));
    await page.fill("#f-opw", ""); await page.fill("#f-opw2", "");
    await page.click("#f-more summary");
    await page.check("input[name=f-chain][value=lan]");
    check("joining over the router: the first Stump's address and this one's number asked, no secret made up",
          (await page.isVisible("#f-parent")) && (await page.isVisible("#f-subnet")) && !(await page.isVisible("#f-up"))
          && (await page.inputValue("#f-secret")) === "");
    await page.fill("#f-parent", "10.0.7.173");
    await page.check("input[name=f-radio][value=lan]");
    check("a Heltec on the router: .222 on the home network suggested, filled in to edit", (await page.getAttribute("#f-hhost", "placeholder")) === "10.0.7.222"
          && (await page.inputValue("#f-hhost")) === "10.0.7.222", await page.inputValue("#f-hhost"));
    await page.fill("#f-hhost", "");
    await page.check("input[name=f-chain][value=none]");
    await page.check("input[name=f-radio][value=none]");
    await page.click("#f-more summary");
    await page.fill("#f-pw", "secret1"); await page.fill("#f-pw2", "sec");
    check("the admin password typed again: \"so far so good\" while it matches the start", /So far/.test(await page.textContent("#f-pw-match")));
    await page.fill("#f-pw2", "secret2");
    check("... then \"not the same\", in red, before anything is pressed", /Not the same/.test(await page.textContent("#f-pw-match"))
          && /mismatch/.test(await page.getAttribute("#f-pw2", "class")));
    await page.click("#make-go");
    check("two passwords that differ: says so", /aren't the same/.test(await page.textContent("#make-err")), await page.textContent("#make-err"));
    await page.fill("#f-pw2", "secret1");
    check("... the same: a tick", /are the same/.test(await page.textContent("#f-pw-match")));
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
    await page.click("#f-more summary");
    check("chains explained: what they're for, that one Stump leaves it as is, and that it can change later",
          /links several Stumps/.test(await page.textContent("#form")) && /With one Stump, leave it/.test(await page.textContent("#form"))
          && /later, from the Settings tab/.test(await page.textContent("#form")) && await page.isChecked("input[name=f-chain][value=none]"));
    await page.fill("#f-hotspot", "Le Café du Coin de la Gare");
    check("the Wi-Fi's name as phones will see it, counted as it's typed", /Phones will see “Le Café du Coin de la Gare” · 6 characters left/.test(await page.textContent("#f-hotspot-count")),
          await page.textContent("#f-hotspot-count"));
    await page.check("#f-hotspot-ip");
    check("... with the address ticked: too long, said at once, in red", /6 characters too long/.test(await page.textContent("#f-hotspot-count"))
          && (await page.getAttribute("#f-hotspot-count", "class")) === "err", await page.textContent("#f-hotspot-count"));
    check("its number, with its Wi-Fi's name: 4 by default, 2 to 254, its address shown", (await page.inputValue("#f-subnet")) === "4"
          && (await page.getAttribute("#f-subnet", "min")) === "2" && /192\.168\.4\.1/.test(await page.textContent("#f-subnet-addr"))
          && !(await page.isVisible("#f-chain-number")));
    await page.fill("#f-subnet", "12");
    check("... changed: the address and the name follow it", /192\.168\.12\.1/.test(await page.textContent("#f-subnet-addr"))
          && /192\.168\.12\.1/.test(await page.textContent("#f-hotspot-count")));
    await page.check("input[name=f-chain][value=member]");
    check("... a chain points to that number (no second selector)", (await page.isVisible("#f-chain-number"))
          && /set above, with its Wi-Fi's name/.test(await page.textContent("#f-chain-number")) && (await page.$$("#f-subnet")).length === 1);
    await page.check("input[name=f-chain][value=none]");
    await page.fill("#f-subnet", "4");
    await page.fill("#f-hotspot", "Café du Coin");
    check("... shortened: fits, the address shown", /Phones will see “Café du Coin 192\.168\.4\.1”/.test(await page.textContent("#f-hotspot-count")),
          await page.textContent("#f-hotspot-count"));
    await page.click("#f-more summary");
    await page.fill("#f-pw", "s3cret!"); await page.fill("#f-pw2", "s3cret!");
    check("the fserv bot: the same password by default (the Provisioner's), its own field hidden",
          (await page.isChecked("#f-same")) && !(await page.isVisible("#f-opw")));
    await page.uncheck("#f-same");
    await page.fill("#f-opw", "barkeep55"); await page.fill("#f-opw2", "barkeep55");
    await page.fill("#f-ssid", "Home \"Net\""); await page.fill("#f-wpass", "pa'ss1234");
    await page.check("input[name=f-radio][value=ap]");
    check("a Heltec on its hotspot: at .200, past the addresses the hotspot hands out", /192\.168\.4\.200/.test(await page.textContent("#f-ap-addr")),
          await page.textContent("#f-ap-addr"));
    check("radio section shows when a Heltec is enabled", await page.isVisible("#f-radio-params")
          && (await page.inputValue("#f-freq")) === "868.8" && (await page.inputValue("#f-bw")) === "125000");
    await page.fill("#f-freq", "915"); await page.selectOption("#f-sf", "10");
    await page.click("#make-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 240000 }); } catch (e) {}
    const title = await page.textContent("#worktitle"), result = await page.textContent("#result");
    check("a new Stump: done", /Done/.test(title), title + " | " + result + " | " + (await page.textContent("#log")).slice(-800));
    check("... the result gives the address to open on its Wi-Fi, and its name with the address", /If it doesn't, go to http:\/\/192\.168\.4\.1/.test(result)
          && /Café du Coin 192\.168\.4\.1/.test(result), result);
    const fl = await page.evaluate(() => window.__flashed);
    check("... MicroPython written at 0, the board erased first, then reset", fl && fl.addr === 0 && fl.erase && fl.len > 500000
          && (await page.evaluate(() => window.__after)) === "hard_reset", JSON.stringify(fl));
    check("... then the REPL at 115200", (await page.evaluate(() => window.__baud)) === 115200);
    // A MicroPython with the Stump frozen in (tests/run.py's frozen mode)
    // is a board running an image: the modules it has built in aren't copied.
    let simFrozen = false;
    try { execFileSync(MP, ["-c", "import stump_image"], { cwd: root, stdio: "ignore" }); simFrozen = true; } catch (e) {}
    check("... the Stump's files on the board, compiled" + (simFrozen ? " (the built-in ones not copied: this MicroPython has them)" : ""),
          (simFrozen ? !fs.existsSync(path.join(root, "barkeep.mpy")) : fs.existsSync(path.join(root, "barkeep.mpy") ) && fs.existsSync(path.join(root, "urns")))
          && fs.existsSync(path.join(root, "main.py")) && !fs.existsSync(path.join(root, "barkeep.py")));
    check("... the plugins too (their plugin.json stays on the computer, as with the Provisioner)", fs.existsSync(path.join(root, "stumpid/core.mpy")) && !fs.existsSync(path.join(root, "stumpid/plugin.json")) && fs.existsSync(path.join(root, "fservbot/core.mpy")));
    const cfgPath = path.join(root, "config.py");
    check("... and a config.py", fs.existsSync(cfgPath));
    if (fs.existsSync(cfgPath)) {
      const cfg = fs.readFileSync(cfgPath, "utf8");
      fs.writeFileSync(path.join(root, "lora_boards.py"), "LORA_BOARDS = {}\n");
      const out = execFileSync(MP, ["-c", "import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
        "print(repr((c.NODE_NAME, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.WIFI_SSID, c.WIFI_PASS, b['enabled'], b['target_host'], b['target_port'], c.CHAIN_ENABLED, c.CHAIN_SECRET, c.BOT_NAME)))"],
        { cwd: root }).toString().trim();
      check("... with the answers (the two passwords independent), as MicroPython reads them", out === "('Caf\u00e9 du Coin', 's3cret!', 'barkeep55', 'Home \"Net\"', \"pa'ss1234\", True, '192.168.4.200', 7633, False, '', 'BarKeep')", out);
      check("... nothing of LaBuche's own config.py", !/LaBuche'|TeK|SalutComment|Bob's Glitch|Concierge/.test(cfg));
    }
    // Finding 8: the next board doesn't take this one's settings, only the home Wi-Fi's name.
    check("the Stump's language in the form (1.0.0-beta.67): French by default", (await page.inputValue("#f-lang")) === "fr");
    check("after it, the form for the next Stump: the defaults (name, hotspot, radio, chain, passwords), the home Wi-Fi's name kept",
          (await page.inputValue("#f-name")) === "Stump" && (await page.inputValue("#f-hotspot")) === ""
          && (await page.isChecked("input[name=f-radio][value=none]")) && (await page.isChecked("input[name=f-chain][value=none]"))
          && (await page.inputValue("#f-pw")) === "" && (await page.inputValue("#f-wpass")) === "" && (await page.inputValue("#f-secret")) === ""
          && (await page.inputValue("#f-ssid")) === 'Home "Net"', await page.inputValue("#f-name") + " / " + await page.inputValue("#f-ssid"));
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
    check("Settings, its Heltec on its Wi-Fi: no warning while what pairs them is unchanged", !(await page.isVisible("#f-heltec-pair")));
    await page.evaluate(() => { document.getElementById("f-more").open = true; });
    await page.fill("#f-hotspot", "Un autre nom");
    check("... its Wi-Fi's name changed: the radio would stop, said at once", (await page.isVisible("#f-heltec-pair"))
          && /stops the radio until the Heltec is set up again/.test(await page.textContent("#f-heltec-pair")), await page.textContent("#f-heltec-pair"));
    check("... and the button to set the Heltec up again, beside it", (await page.isVisible("#f-hw-go"))
          && /“Un autre nom/.test(await page.textContent("#f-hw-what")), await page.textContent("#f-hw-what"));
    await page.fill("#f-hotspot", (await page.evaluate(() => EDITING.hotspot)));
    check("... put back: the warning goes", !(await page.isVisible("#f-heltec-pair")));
    check("... and the button too (finding 11)", !(await page.isVisible("#f-hw-go")));
    await page.evaluate(() => { document.getElementById("f-more").open = true; });   // (open already when it has a Wi-Fi name)
    await page.check("input[name=f-chain][value=member]");
    await page.fill("#f-up", "First-Stump");
    await page.fill("#f-subnet", "6");
    await page.fill("#f-opw", "newbar42"); await page.fill("#f-opw2", "newbar42");            // change only the BarKeep password
    check("a chain member: a secret made for it", /^[0-9a-f]{16}$/.test(await page.inputValue("#f-secret")));
    await page.click("#set-save");
    title = await wait();
    check("... saved", /Done/.test(title), title + " | " + (await page.textContent("#result")) + (await page.textContent("#log")).slice(-500));
    fs.writeFileSync(path.join(root, "lora_boards.py"), "LORA_BOARDS = {}\n");
    const out = execFileSync(MP, ["-c", "import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
      "print(repr((c.NODE_NAME, c.AUTH_ADMIN_PASSWORD, c.FSERVBOT_OP_PASSWORD, c.WIFI_PASS, c.CHAIN_ENABLED, c.CHAIN_UPLINK_SSID, c.CHAIN_SUBNET, b['target_host'])))"], { cwd: root }).toString().trim();
    check("... as MicroPython reads them: name + chain changed, admin + Wi-Fi kept, BarKeep password changed on its own",
          out === "('Le Coin', 's3cret!', 'newbar42', \"pa'ss1234\", True, 'First-Stump', 6, '192.168.6.200')", out);
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
      fs.writeFileSync(path.join(r, "flasher_ui.py"), "retired in beta.41");
    });
    const { page } = t;
    await page.check("input[name=mode][value=update]");
    await page.click("#make-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 240000 }); } catch (e) {}
    const title = await page.textContent("#worktitle");
    check("update: done (the running Stump stopped first)", /Done/.test(title), title + " | " + (await page.textContent("#result")) + " | " + (await page.textContent("#log")).slice(-600));
    check("... and says to copy Arborist itself onto the card by card reader", /tools\/arborist\//.test(await page.textContent("#result")), await page.textContent("#result"));
    check("... nothing flashed", (await page.evaluate(() => window.__flashed)) === null);
    check("... its config.py and identity kept", fs.readFileSync(path.join(root, "config.py"), "utf8") === "NODE_NAME = 'Mine'\n"
          && fs.readFileSync(path.join(root, "rns/identity"), "utf8") === "its identity");
    check("... the new files there, the old source gone", fs.existsSync(path.join(root, "barkeep.mpy")) && !fs.existsSync(path.join(root, "barkeep.py")));
    check("... a file the release no longer has removed, and said (beta.74)", !fs.existsSync(path.join(root, "flasher_ui.py"))
          && /no longer has removed: flasher_ui\.py/.test(await page.textContent("#log")), (await page.textContent("#log")).slice(-600));
    await t.browser.close(); t.sim.kill();
  }

  // -- its identity, saved and brought back (1.0.0-beta.76) ----------------------------
  {
    const root = path.join(SCRATCH, "page_identity");
    const mine = nodeCrypto.randomBytes(64);
    const t = await run("identity", root, [], (r) => { fs.mkdirSync(path.join(r, "rns")); fs.writeFileSync(path.join(r, "rns/identity"), mine); });
    const { page } = t;
    await page.click("nav button[data-tab=settings]");
    check("Settings: its identity, with what the file is (keep it safe)", await page.isVisible("#id-save")
          && /keep it safe/i.test(await page.textContent("#tab-settings")));
    const dl = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#id-save");
    const got = await dl;
    const kp = path.join(SCRATCH, "page_identity.stump-identity");
    await got.saveAs(kp);
    const k = JSON.parse(fs.readFileSync(kp, "utf8"));
    check("Save its identity: a .stump-identity file, the board's identity in it",
          /\.stump-identity$/.test(got.suggestedFilename()) && Buffer.from(k.files["rns/identity"], "base64").equals(mine), got.suggestedFilename());
    const other = nodeCrypto.randomBytes(64);
    const okp = path.join(SCRATCH, "other.stump-identity");
    fs.writeFileSync(okp, JSON.stringify({ stump_identity: 1, name: "Gearn", files: { "rns/identity": other.toString("base64") } }));
    await page.click("#tab-settings details:last-of-type summary");
    await page.setInputFiles("#id-file", okp);
    await page.waitForFunction(() => /Gearn, fingerprint/.test(document.getElementById("id-info").textContent), null, { timeout: 15000 });
    const dl2 = page.waitForEvent("download", { timeout: 60000 });
    await page.click("#id-put");
    const prev = await dl2;
    try { await page.waitForFunction(() => /again/.test(document.getElementById("result").textContent), null, { timeout: 60000 }); } catch (e) {}
    check("Bring it back: the board has that identity; the one it had saved first, to keep",
          fs.readFileSync(path.join(root, "rns/identity")).equals(other) && /replaced/.test(prev.suggestedFilename())
          && /Gearn again/.test(await page.textContent("#result")), (await page.textContent("#result")) + " | " + prev.suggestedFilename());
    check("... no errors in the page", t.errors.length === 0, t.errors.join(" | "));
    await t.browser.close(); t.sim.kill();
  }

  // -- the SD card tab (the board side: repl_test.js) ---------------------------------
  {
    const root = path.join(SCRATCH, "page_card");
    const t = await run("card", root, [], (r) => {
      fs.mkdirSync(path.join(r, "meter"), { recursive: true });                     // a plugin copied in by hand
      fs.writeFileSync(path.join(r, "meter/install.py"), "def setup(s):\n    pass\n");
    });
    const page = t.page;
    await page.click("nav button[data-tab=card]");
    check("SD card tab: shown, the form hidden", await page.isVisible("#tab-card") && !(await page.isVisible("#form")));
    const size = await page.textContent("#card-tools-size"), when = await page.textContent("#card-tools-time");
    check("... the Tools page's size and time, from what the page carries", /MB/.test(size) && /minutes/.test(when), size + " / " + when);
    // A real pack, zipped as a release does; and the same with one file swapped.
    const zips = path.join(SCRATCH, "page_card_zips");
    fs.rmSync(zips, { recursive: true, force: true }); fs.mkdirSync(zips, { recursive: true });
    execFileSync("python3", ["-c", `
import sys, zipfile
from pathlib import Path
pack = Path(sys.argv[1]); out = Path(sys.argv[2])
for name, swap in (("good.zip", False), ("bad.zip", True)):
    with zipfile.ZipFile(out / name, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pack.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                data = f.read_bytes()
                if swap and f.name == "install.py":
                    data += b"\\n# changed\\n"
                zf.writestr(pack.name + "/" + f.relative_to(pack).as_posix(), data)
`, path.join(__dirname, "../../packs/tavern-pack"), zips]);
    // The Packs tab (1.0.0-beta.75): what it has, removed (both questions answered yes here).
    await page.click("nav button[data-tab=packs]");
    check("Packs tab: shown, with the pack install", await page.isVisible("#tab-packs") && await page.isVisible("#card-pack")
          && !(await page.isVisible("#tab-card")));
    await page.click("#pk-read");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 60000 }); } catch (e) {}
    check("... its packs read: the one copied in by hand, version not recorded", /meter \(version not recorded\)/.test(await page.textContent("#pk-list")),
          (await page.textContent("#pk-list")) + " | " + (await page.textContent("#worktitle")) + " | " + (await page.textContent("#result")) + " | " + (await page.textContent("#log")).slice(-1500));
    await page.click("#pk-list button[data-pack=meter]");
    try { await page.waitForFunction(() => /off the Stump/.test(document.getElementById("result").textContent), null, { timeout: 60000 }); } catch (e) {}
    check("... removed (its data on the card: repl_test.js): its folder gone, the list says so", !fs.existsSync(path.join(root, "meter"))
          && /No packs on this Stump/.test(await page.textContent("#pk-list")) && /with what it kept/.test(await page.textContent("#result")),
          (await page.textContent("#result")) + " | " + (await page.textContent("#log")).slice(-400));
    await page.setInputFiles("#card-pack", path.join(zips, "good.zip"));
    await page.waitForFunction(() => document.getElementById("card-pack-info").textContent.length > 0, null, { timeout: 15000 });
    const info = await page.textContent("#card-pack-info");
    check("a pack chosen: checked, what it brings and how long", /tavern .*checked/.test(info) && /a theme/.test(info)
          && !(await page.isDisabled("#card-pack-go")), info);
    check("... the Tavern's settings: all optional, under \"More settings\" with their defaults",
          (await page.isVisible("#card-pack-qs fieldset")) && !(await page.isVisible("#pq-TAVERN_NICK"))
          && (await page.inputValue("#pq-TAVERN_NICK")).length > 0);
    execFileSync("python3", ["-c", `
import sys, zipfile
from pathlib import Path
pack = Path(sys.argv[1]); out = Path(sys.argv[2])
with zipfile.ZipFile(out / "bavard.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    for f in sorted(pack.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts and "tests" not in f.parts and "gutenberg" not in f.parts:
            zf.writestr(pack.name + "/" + f.relative_to(pack).as_posix(), f.read_bytes())
`, path.join(__dirname, "../../packs/bavard-pack"), zips]);
    await page.setInputFiles("#card-pack", path.join(zips, "bavard.zip"));
    await page.waitForFunction(() => /bavard/.test(document.getElementById("card-pack-info").textContent), null, { timeout: 30000 });
    check("Bavard's zip: its question shown (talk from the start?), off by default", (await page.isVisible("#pq-BAVARD_ON"))
          && (await page.inputValue("#pq-BAVARD_ON")) === "off" && !(await page.isVisible("#pq-BAVARD_ROOM")),
          await page.textContent("#card-pack-qs"));
    await page.setInputFiles("#card-pack", path.join(zips, "bad.zip"));
    await page.waitForFunction(() => document.getElementById("card-pack-info").className === "err", null, { timeout: 15000 });
    const bad = await page.textContent("#card-pack-info");
    check("a pack with a file changed: refused before anything is copied", /changed since/.test(bad) && await page.isDisabled("#card-pack-go"), bad);
    await page.click("nav button[data-tab=card]");
    await page.check("input[name=wipe][value=all]");
    const before = await page.textContent("#worktitle");
    await page.click("#card-wipe-go");
    check("wipe without typing WIPE: asks for it, nothing done", /Type WIPE/.test(await page.textContent("#card-err"))
          && (await page.textContent("#worktitle")) === before);
    await t.browser.close(); t.sim.kill();
  }

  // -- the Heltec's Wi-Fi, set up over USB (a fake RNode on the port) -----------------
  {
    const root = path.join(SCRATCH, "page_heltec");
    const t = await run("heltec", root, []);
    const page = t.page;
    check("no Heltec chosen: nothing about its Wi-Fi", !(await page.isVisible("#f-heltec-wifi")) && !(await page.isVisible("#f-heltec-next")));
    await page.check("input[name=f-radio][value=ap]");
    check("a new Stump with a Heltec: no Heltec button in the form, only what comes after (finding 11)",
          !(await page.isVisible("#f-heltec-wifi")) && (await page.isVisible("#f-heltec-next"))
          && /Set up a Heltec V3/.test(await page.textContent("#f-heltec-next")));
    check("... the radio settings: set by the Stump (not \"the CAM\")", /the Stump sets these/.test(await page.textContent("#f-radio-params")));
    await page.check("input[name=mode][value=heltec]");
    await page.fill("#hel-ssid", "Café du Coin"); await page.check("#hel-ip"); await page.fill("#hel-num", "7");
    check("a Heltec on a Stump's Wi-Fi: what it will join, by the name phones see, at .200",
          /“Café du Coin 192\.168\.7\.1” \(open\), at 192\.168\.7\.200/.test(await page.textContent("#hel-what")), await page.textContent("#hel-what"));
    // In the page, a fake RNode in place of the board: it answers its serial commands.
    await page.evaluate(() => {
      const frames = [], eeprom = new Uint8Array(256).fill(255), cfg = new Uint8Array(80).fill(255);
      eeprom[0xBA] = 0;
      const reply = (cmd, data) => {
        const b = [0xC0, cmd]; data.forEach((x) => { if (x === 0xC0) b.push(0xDB, 0xDC); else if (x === 0xDB) b.push(0xDB, 0xDD); else b.push(x); }); b.push(0xC0);
        setTimeout(() => window.__simFeed(btoa(String.fromCharCode.apply(null, b))), 5);
      };
      let cur = null, esc = false;
      window.__rnode = { frames: frames, reset: false };
      window.__simWrite = (b64) => {
        const s = atob(b64);
        for (let i = 0; i < s.length; i++) {
          const x = s.charCodeAt(i);
          if (x === 0xC0) {
            if (cur && cur.length) {
              const c = cur[0], d = cur.slice(1); frames.push([c, d]);
              if (c === 0x08) reply(0x08, [0x46]); else if (c === 0x50) reply(0x50, [1, 82]);
              else if (c === 0x51) reply(0x51, Array.from(eeprom)); else if (c === 0x6D) reply(0x6D, Array.from(cfg));
              else if (c === 0x6B) { cfg.fill(255, 0, 33); d.forEach((v, j) => { cfg[j] = v; }); }
              else if (c === 0x6C) { cfg.fill(255, 33, 66); d.forEach((v, j) => { cfg[33 + j] = v; }); }
              else if (c === 0x84) d.forEach((v, j) => { cfg[66 + j] = v; }); else if (c === 0x85) d.forEach((v, j) => { cfg[70 + j] = v; });
              else if (c === 0x6A) eeprom[0xBA] = d[0]; else if (c === 0x55) window.__rnode.reset = true;
            }
            cur = []; esc = false;
          } else if (cur) { if (esc) { cur.push(x === 0xDC ? 0xC0 : 0xDD === x ? 0xDB : x); esc = false; } else if (x === 0xDB) esc = true; else cur.push(x); }
        }
      };
    });
    await page.click("#make-go");
    try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 60000 }); } catch (e) {}
    const res = await page.textContent("#result");
    const rn = await page.evaluate(() => window.__rnode);
    check("set up over USB: it joins “Café du Coin 192.168.7.1” (open) at 192.168.7.200, checked, then restarted",
          /Done/.test(await page.textContent("#worktitle")) && /joins “Café du Coin 192\.168\.7\.1” \(open\) at 192\.168\.7\.200/.test(res)
          && /RNode firmware 1\.82/.test(res) && rn.reset && rn.frames.some((f) => f[0] === 0x6A && f[1][0] === 1),
          res + " | " + (await page.textContent("#log")).slice(-400));
    // Its own choice under Make or update (finding 9): only what the Heltec needs.
    await page.check("input[name=mode][value=new]");
    await page.check("input[name=mode][value=heltec]");
    check("\"Set up a Heltec V3\": its own short form, not the Stump's; the button says so",
          (await page.isVisible("#hel")) && !(await page.isVisible("#f-name")) && /Set up the Heltec/.test(await page.textContent("#make-go")));
    await page.fill("#hel-ssid", "La Buche"); await page.uncheck("#hel-ip"); await page.fill("#hel-num", "9");
    check("... a Stump's Wi-Fi: what it joins, said as it's typed", /“La Buche” \(open\), at 192\.168\.9\.200/.test(await page.textContent("#hel-what")),
          await page.textContent("#hel-what"));
    await page.fill("#hel-num", "300");
    check("... a number past 254: said", /2 to 254/.test(await page.textContent("#hel-what")));
    await page.fill("#hel-num", "9");
    await page.evaluate(() => { window.__rnode.frames.length = 0; window.__rnode.reset = false; });
    await page.click("#make-go");
    try { await page.waitForFunction(() => /joins “La Buche”/.test(document.querySelector("#result").textContent), null, { timeout: 60000 }); } catch (e) {}
    const res2 = await page.textContent("#result"), rn2 = await page.evaluate(() => window.__rnode);
    check("... set up: it joins “La Buche” (open) at 192.168.9.200, then restarted",
          /joins “La Buche” \(open\) at 192\.168\.9\.200/.test(res2) && rn2.reset, res2 + " | " + (await page.textContent("#log")).slice(-300));
    await page.check("input[name=hel-where][value=home]");
    await page.fill("#hel-hssid", "Home"); await page.fill("#hel-hpass", "homepass1"); await page.fill("#hel-hpass2", "homepass2");
    await page.fill("#hel-hip", "192.168.0.222");
    check("... the home Wi-Fi: its password typed twice, checked as it's typed", /Not the same/.test(await page.textContent("#hel-hpass-match")));
    await page.click("#make-go");
    check("... two passwords that differ: stopped before the Heltec is asked for", /aren't the same/.test(await page.textContent("#make-err")),
          await page.textContent("#make-err"));
    await page.fill("#hel-hpass2", "homepass1");
    check("... the same: it joins “Home” (with its password) at 192.168.0.222", /“Home” \(with its password\), at 192\.168\.0\.222/.test(await page.textContent("#hel-what")),
          await page.textContent("#hel-what"));
    check("... no errors in the page", t.errors.length === 0, t.errors.join("\n"));
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
  await lookTests();
  if (PAGE_IMAGES) await imageTests();
  console.log("FAILURES: " + JSON.stringify(FAIL));
  process.exit(FAIL.length ? 1 : 0);
})().catch((e) => { console.log("FAIL crashed: " + e.stack); console.log("FAILURES: [\"crashed\"]"); process.exit(1); });

// -- the Stump images (1.0.0-beta.58) ---------------------------------------------------
// The board_sim stands in for a board that runs an image: a stump_image.py
// on it says what's frozen in, its release and partition layout.
const TAG = "stump-9eba0468";
function imageOn(r, partitions, frozen) {
  fs.writeFileSync(path.join(r, "stump_image.py"), "RELEASE = '1.0.0-beta.57'\nPARTITIONS = " + JSON.stringify(partitions) +
                   "\nFROZEN = " + JSON.stringify(frozen) + "\n");
}
async function finish(page, ms) {
  try { await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: ms || 240000 }); } catch (e) {}
  return { title: await page.textContent("#worktitle"), result: (await page.textContent("#make-err")) + " " + (await page.textContent("#result")), log: (await page.textContent("#log")).slice(-800) };
}
async function newStump(page, extra) {
  await page.fill("#f-name", "Img"); await page.fill("#f-pw", "s3cret!"); await page.fill("#f-pw2", "s3cret!");
  if (extra) await extra(page);
  await page.click("#make-go");
}
async function imageTests() {
  const FROZEN = ["barkeep.py", "urns/transport.py", "rrc.py"];
  // A new CAM: the S3-CAM image at 0, erased first; the frozen modules not copied.
  {
    const root = path.join(SCRATCH, "img_cam");
    const t = await run("img-cam", root, [], (r) => imageOn(r, TAG, FROZEN), false, PAGE_IMAGES);
    await newStump(t.page);
    const r = await finish(t.page);
    const fl = await t.page.evaluate(() => window.__flashed);
    check("images: a new CAM, done", /Done/.test(r.title), r.title + " | " + r.result + " | " + r.log);
    check("... the S3-CAM image written at 0x0, the board erased first", fl && fl.addr === 0 && fl.erase && fl.tag === "S3CAMIMG", JSON.stringify(fl));
    check("... the modules the image has built in: not copied", !fs.existsSync(path.join(root, "barkeep.mpy")) && !fs.existsSync(path.join(root, "rrc.mpy"))
          && !fs.existsSync(path.join(root, "urns/transport.mpy")));
    check("... the rest copied (main.py, other modules, a config.py)", fs.existsSync(path.join(root, "main.py")) && fs.existsSync(path.join(root, "chain.mpy"))
          && fs.existsSync(path.join(root, "config.py")));
    check("... no errors in the page", t.errors.length === 0, t.errors.join("\n"));
    await t.browser.close(); t.sim.kill();
  }
  // A new WROVER: its own image at 0x1000; a router chain member and its Heltec on the router.
  {
    const root = path.join(SCRATCH, "img_wrover");
    const t = await run("img-wrover", root, [], (r) => imageOn(r, TAG, FROZEN), false, PAGE_IMAGES, "ESP32-D0WD-V3 (revision v3.1)");
    await newStump(t.page, async (page) => {
      await page.fill("#f-ssid", "Home"); await page.fill("#f-wpass", "homepass1");
      await page.click("#f-more summary");
      await page.check("input[name=f-chain][value=lan]");
      await page.fill("#f-parent", "192.168.0.173"); await page.fill("#f-subnet", "9"); await page.fill("#f-secret", "chainsecret");
      await page.check("input[name=f-radio][value=lan]");
      await page.fill("#f-hhost", "192.168.0.223");
    });
    const r = await finish(t.page);
    const fl = await t.page.evaluate(() => window.__flashed);
    check("images: a new WROVER, done", /Done/.test(r.title), r.title + " | " + r.result + " | " + r.log);
    check("... the WROVER image written at 0x1000, erased first", fl && fl.addr === 0x1000 && fl.erase && fl.tag === "WROVERIM", JSON.stringify(fl));
    check("... the result says which address to give its Heltec", /192\.168\.0\.223/.test(r.result), r.result);
    fs.writeFileSync(path.join(root, "lora_boards.py"), "LORA_BOARDS = {}\n");
    let out = "";
    try {
      out = execFileSync(MP, ["-c", "import config as c\nb=[i for i in c.CONFIG['interfaces'] if i['name']=='Heltec Bridge'][0]\n" +
        "print(repr((c.CHAIN_ENABLED, c.CHAIN_PARENT_IP, c.CHAIN_UPLINK_SSID, c.CHAIN_UPLINK_PASS, c.CHAIN_SUBNET, c.CHAIN_SECRET, c.CHAIN_CHANNEL, c.FSERVBOT_OP_PASSWORD, b['target_host'])))"],
        { cwd: root }).toString().trim();
    } catch (e) { out = String(e.stdout || e); }
    check("... a member over the router, as the Provisioner writes it: the first Stump's address, the home Wi-Fi as its uplink",
          out === "(True, '192.168.0.173', 'Home', 'homepass1', 9, 'chainsecret', 0, 's3cret!', '192.168.0.223')", out);
    await t.browser.close(); t.sim.kill();
  }
  // A Stump on an image, updated: the new image in place (no erase), its settings and identity kept, stale copies gone.
  {
    const root = path.join(SCRATCH, "img_update");
    const t = await run("img-update", root, ["--running"], (r) => {
      imageOn(r, TAG, FROZEN);
      fs.writeFileSync(path.join(r, "config.py"), "NODE_NAME = 'Mine'\n");
      fs.mkdirSync(path.join(r, "rns")); fs.writeFileSync(path.join(r, "rns/identity"), "its identity");
      fs.writeFileSync(path.join(r, "barkeep.mpy"), "a stale copy");
    }, false, PAGE_IMAGES, "ESP32-D0WD-V3 (revision v3.1)");
    await t.page.check("input[name=mode][value=update]");
    await t.page.click("#make-go");
    const r = await finish(t.page);
    const fl = await t.page.evaluate(() => window.__flashed);
    check("images: an image Stump updated, done", /Done/.test(r.title), r.title + " | " + r.result + " | " + r.log);
    check("... the image written in place: not erased", fl && fl.addr === 0x1000 && fl.erase === false && fl.tag === "WROVERIM", JSON.stringify(fl));
    check("... its config.py and identity kept", fs.readFileSync(path.join(root, "config.py"), "utf8") === "NODE_NAME = 'Mine'\n"
          && fs.readFileSync(path.join(root, "rns/identity"), "utf8") === "its identity");
    check("... the stale copy of a built-in module removed, the rest copied", !fs.existsSync(path.join(root, "barkeep.mpy")) && fs.existsSync(path.join(root, "chain.mpy")));
    await t.browser.close(); t.sim.kill();
  }
  // An image with an older partition layout: refused (an erase would lose its identity).
  {
    const root = path.join(SCRATCH, "img_old");
    const t = await run("img-old", root, ["--running"], (r) => {
      imageOn(r, "stump-00000000", FROZEN);
      fs.writeFileSync(path.join(r, "config.py"), "NODE_NAME = 'Mine'\n");
    }, false, PAGE_IMAGES, "ESP32-D0WD-V3 (revision v3.1)");
    await t.page.check("input[name=mode][value=update]");
    await t.page.click("#make-go");
    const r = await finish(t.page, 60000);
    check("images: an older layout: stopped, with why, nothing written", /older layout/.test(r.result) && (await t.page.evaluate(() => window.__flashed)) === null, r.result);
    await t.browser.close(); t.sim.kill();
  }
  // A WROVER with the page built without images: refused, with where to go.
  {
    const root = path.join(SCRATCH, "img_none");
    const t = await run("img-none", root, [], null, false, PAGE, "ESP32-D0WD-V3 (revision v3.1)");
    await newStump(t.page);
    const r = await finish(t.page, 60000);
    check("no images in the page, a WROVER: stopped, it says so", /no Stump image for an ESP32-WROVER/.test(r.result), r.result);
    await t.browser.close(); t.sim.kill();
  }
}

// -- Look around with the board (1.0.0-beta.60) ----------------------------------------
// The board_sim gets a stand-in network module (two networks, "Home" with a
// password), and a stand-in first Stump answers "who's a Stump here?" (UDP
// 4244) and serves its /chain.json and /hw.json.
const NETWORK_PY = `STA_IF = 0
AP_IF = 1
STAT_IDLE = 0
STAT_GOT_IP = 1010
STAT_NO_AP_FOUND = 201
STAT_WRONG_PASSWORD = 202
class WLAN:
    _on = False
    def __init__(self, i=0): pass
    def active(self, *a): return True
    def scan(self): return [(b"Home", b"a", 6, -48, 3, 0), (b"Cafe", b"b", 1, -71, 0, 0), (b"Home", b"c", 6, -60, 3, 0), (b"Box 192.168.7.1", b"d", 11, -50, 0, 0)]
    def connect(self, ssid, pw): WLAN._on = (ssid == "Home" and pw == "homepass1") or ssid == "Cafe"
    def disconnect(self): WLAN._on = False
    def isconnected(self): return WLAN._on
    def status(self): return STAT_GOT_IP if WLAN._on else STAT_WRONG_PASSWORD
    def ifconfig(self): return ("127.0.0.5", "255.0.0.0", "127.0.0.1", "8.8.8.8")
`;
function fakeFirstStump(chainId) {
  const http = require("http"), dgram = require("dgram");
  return new Promise((ok) => {
    const srv = http.createServer((req, res) => {
      const body = req.url.startsWith("/chain.json")
        ? { enabled: true, role: "root", chain: chainId, ap_ip: "192.168.4.1", lan_ip: "127.0.0.1", secret: true,
            stumps: [{ name: "LaBuche8", http: ["http://127.0.0.8/", "http://192.168.8.1/"] }] }
        : { state: "online", radios: [{ name: "Heltec Bridge", where: "127.0.0.222", state: "online" }] };
      res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(body));
    });
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port, u = dgram.createSocket({ type: "udp4", reuseAddr: true });
      u.on("message", (m, r) => {
        if (String(m).startsWith("STUMP?"))
          u.send(JSON.stringify({ stump: 1, name: "LaBuche", lxmf: "ab".repeat(16), release: "1.0.0-beta.59",
                                  http: ["http://127.0.0.1:" + port + "/"], here: "http://127.0.0.1:" + port + "/" }), r.port, r.address);
      });
      u.bind(4244, () => { try { u.setBroadcast(true); } catch (e) {} ok({ close() { srv.close(); u.close(); } }); });
    });
  });
}
async function lookTests() {
  const crypto = require("crypto");
  const chainId = crypto.createHash("sha256").update("stump-chain:sekrit").digest("hex").slice(0, 12);
  const first = await fakeFirstStump(chainId);
  const root = path.join(SCRATCH, "look");
  const t = await run("look", root, [], (r) => fs.writeFileSync(path.join(r, "network.py"), NETWORK_PY));
  const page = t.page;
  try {
    await page.fill("#f-name", "Next"); await page.fill("#f-pw", "s3cret!"); await page.fill("#f-pw2", "s3cret!");
    const go = async (sel) => { await page.click(sel || "#f-look"); await page.waitForSelector("#worktitle .ok, #worktitle .err", { timeout: 90000 }); };
    // 0. "Find it on the network" by the first Stump's address, with no home Wi-Fi yet: says what's needed.
    await page.click("#f-more summary");
    await page.check("input[name=f-chain][value=lan]");
    await page.click("#f-find-first");
    check("find the first Stump with no home Wi-Fi filled in: says to fill it in first", /home Wi-Fi above first/.test(await page.textContent("#f-find-err")),
          await page.textContent("#f-find-err"));
    await page.check("input[name=f-chain][value=none]");
    await page.click("#f-more summary");
    // 1. The networks it sees, as a list to pick from; a Stump's own Wi-Fi apart.
    await go();
    const nets = await page.$$eval("#f-look-out .net", (os) => os.map((o) => o.dataset.ssid));
    check("look: the networks the board sees, a list to pick from (each once, the strongest first)",
          JSON.stringify(nets) === JSON.stringify(["Home", "Cafe"]), JSON.stringify(nets) + " " + (await page.textContent("#result")));
    check("... a Stump's own Wi-Fi listed apart, not as a home Wi-Fi", /Stumps' own Wi-Fi.*Box 192\.168\.7\.1/.test(await page.textContent("#f-look-out")),
          await page.textContent("#f-look-out"));
    check("... nothing picked yet: says to pick one", /Pick the home Wi-Fi/.test(await page.textContent("#f-look-out")));
    await page.click("#f-look-out .net[data-ssid=Home]");
    check("picking one fills in the name", (await page.inputValue("#f-ssid")) === "Home");
    // 1b. Picked, no password: asked for, not a second scan's worth of text.
    await go();
    check("look: a locked network picked, no password: asked for it", /Type “Home”'s password/.test(await page.textContent("#f-look-out")),
          await page.textContent("#f-look-out"));
    // 2. A wrong password: said, from the board's own try.
    await page.fill("#f-wpass", "wrongpass");
    await go();
    check("look: a wrong Wi-Fi password, said by the board's own try", /password is wrong/.test(await page.textContent("#f-look-out")),
          await page.textContent("#f-look-out"));
    // 3. The right one, one click: the first Stump found and offered, a free number and Heltec address.
    await page.fill("#f-wpass", "homepass1");
    await page.check("input[name=f-radio][value=lan]");
    await page.fill("#f-hhost", "127.0.0.222");
    await go();
    const out = await page.textContent("#f-look-out");
    const row = await page.$$eval("#f-look-out table.stumps tr", (rs) => rs.map((r) => r.textContent));
    check("look: the password works, the first Stump in the table", /password works/.test(out) && row.length === 1
          && /LaBuche/.test(row[0]) && /127\.0\.0\.1/.test(row[0]) && /first Stump/.test(row[0]) && /number 4/.test(row[0]), JSON.stringify(row) + out);
    check("... what's free for this one said in one line", /Free for this one: number 5 · Heltec 127\.0\.0\.223/.test(out), out);
    check("... offered, not chosen: the board stays on its own until \"Join its chain\" is pressed (finding 8)",
          (await page.isChecked("input[name=f-chain][value=none]")) && (await page.inputValue("#f-parent")) === ""
          && /Join its chain/.test(await page.textContent("#f-look-out table.stumps button")) && /stays on its own/.test(out), out);
    await page.click("#f-look-out table.stumps button");
    check("... pressed: joining LaBuche's chain over the router, its address filled in",
          (await page.isChecked("input[name=f-chain][value=lan]")) && (await page.inputValue("#f-parent")) === "127.0.0.1"
          && /Joining/.test(await page.textContent("#f-look-out table.stumps button")), await page.inputValue("#f-parent"));
    check("... a free chain number (4 and 8 taken: 5)", (await page.inputValue("#f-subnet")) === "5", await page.inputValue("#f-subnet"));
    check("... the Heltec: .222 taken by LaBuche's, the next one offered", (await page.inputValue("#f-hhost")) === "127.0.0.223",
          await page.inputValue("#f-hhost"));
    check("... the chain secret unknown to this browser: asked, not made up", (await page.inputValue("#f-secret")) === ""
          && /Type LaBuche's chain secret/.test(await page.textContent("#f-secret-hint")), await page.textContent("#f-secret-hint"));
    // 3b. The same from "Find it on the network", by the first Stump's address.
    await page.fill("#f-parent", "");
    await go("#f-find-first");
    check("find it on the network (by the first Stump's address): filled in", (await page.inputValue("#f-parent")) === "127.0.0.1"
          && (await page.textContent("#f-find-err")) === "", await page.inputValue("#f-parent"));
    check("the Wi-Fi password field isn't autofilled by the browser", (await page.getAttribute("#f-wpass", "autocomplete")) === "new-password");
    // 4. A wrong secret: stopped before anything is written.
    await page.fill("#f-secret", "nope");
    await page.click("#make-go");
    check("look: a wrong chain secret, checked against LaBuche's chain: stopped before writing",
          /isn't LaBuche's chain secret/.test(await page.textContent("#make-err")) && (await page.evaluate(() => window.__flashed)) === null,
          await page.textContent("#make-err"));
    check("... the right one passes", (await page.evaluate(() => secretMismatch({ CHAIN_PARENT_IP: "127.0.0.1", CHAIN_SECRET: "sekrit" }))) === null);
    // 5. A secret this browser has seen: offered back when the chain is found again.
    await page.evaluate(() => rememberSecret("sekrit"));
    await page.fill("#f-secret", "");
    await go();
    check("look: a chain secret this browser knows, filled in", (await page.inputValue("#f-secret")) === "sekrit"
          && /knows LaBuche's chain secret/.test(await page.textContent("#f-secret-hint")), await page.inputValue("#f-secret"));
    // 6. The name typed is a Stump's already there: a tagged one offered, as a person would add.
    await page.fill("#f-name", "LaBuche");
    await go();
    const warn = await page.textContent("#f-look-out");
    check("look: a Stump called LaBuche already there: said, keep it if it's that board set up again", /already on this Wi-Fi/.test(warn)
          && /set up again\? Keep the name/.test(warn), warn);
    await page.click("#f-look-out button:has-text('Call it')");
    check("... or a name with a tag, like LaBuche-k7q2", /^LaBuche-[a-hjkmnp-z2-9]{4}$/.test(await page.inputValue("#f-name")) && /\d/.test(await page.inputValue("#f-name")),
          await page.inputValue("#f-name"));
    check("look: no errors in the page", t.errors.length === 0, t.errors.join("\n"));
  } finally {
    first.close();
    await t.browser.close(); t.sim.kill();
  }
}
