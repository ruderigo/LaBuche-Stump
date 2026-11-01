#!/usr/bin/env python3
"""Arborist (scripts/build_arborist.py, scripts/arborist/):

  - the config.py template: every asked setting and password out, the
    rest still config.py, still Python; and a template that would keep a
    password is refused
  - the page built (a stand-in MicroPython image: the real one comes from
    micropython.org at release)
  - repl_test.js: the raw REPL and the uploader against a pretend board
    (tests/arborist/board_sim.py, the code run by MicroPython's Unix port)
  - page_test.js: the page from file:// in Chromium, Web Serial piped to
    the pretend board: a new Stump made, one updated, one refused

Needs node, and Playwright's Chromium for page_test (skipped without).
MICROPYTHON as for tests/run.py."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
MP = os.environ.get("MICROPYTHON", "micropython")
FAIL = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(info)[:1500]))
    if not ok:
        FAIL.append(name)


def node_test(script, *args):
    r = subprocess.run(["node", str(HERE / "arborist" / script)] + [str(a) for a in args],
                       capture_output=True, text=True, timeout=1200)
    out = r.stdout + r.stderr
    for line in out.splitlines():
        if line.startswith(("PASS", "FAIL")):
            print("  " + line)
    check(script, r.returncode == 0 and "FAILURES: []" in out, out[-1500:])


def main():
    import build_arborist as B
    tpl = B.config_template()
    for k in B.ASKED:
        check("template: %s empty" % k, "\n%s = ''\n" % k in tpl)
    import re
    check("template: the Heltec bridge off", re.search(r'"name": "Heltec Bridge",\s*"enabled": False,', tpl))
    check("template: LaBuche's names and passwords gone", not any(s in tpl for s in ("'TeK'", "TeK_Knoh", "SalutComment",
                                                                                     "Bob's Glitch", "'LaBuche'", "Concierge")))
    check("template: a fresh Stump's defaults (no chain, no greeting)", "CHAIN_ENABLED = False" in tpl and "CHAIN_SECRET = ''" in tpl
          and "MESH_GREETING = ''" in tpl)
    check("template: the rest of config.py kept (the radio boards, the interfaces)", "from lora_boards import LORA_BOARDS" in tpl
          and '"type": "WiFiSerialInterface"' in tpl and "REANNOUNCE_INTERVAL = 120" in tpl)
    # A setting that isn't scrubbed, holding a password, is caught.
    real = B.FW
    with tempfile.TemporaryDirectory() as t:
        fw = Path(t) / "final_firmware"
        fw.mkdir()
        cfg = (real / "config.py").read_text(encoding="utf-8")
        (fw / "config.py").write_text(cfg + "\nEXTRA_NOTE = 'see WIFI_PASS'\nX = %r\n" % "SalutComment-x", encoding="utf-8")
        B.FW = fw
        try:
            B.config_template()
            caught = False
        except SystemExit as e:
            caught = "WIFI_PASS" in str(e)
        finally:
            B.FW = real
        check("template: a password still in it somewhere: the build stops", caught)

    if not shutil.which("node"):
        print("skip: no node")
        print("FAILURES", FAIL)
        return
    work = Path(tempfile.mkdtemp(prefix="stump-arborist-"))
    try:
        fake = bytearray(os.urandom(600000))
        fake[0] = 0xE9
        (work / "mp.bin").write_bytes(bytes(fake))
        try:
            B.build(work / "x", work / "mp.bin")
            pinned = False
        except SystemExit as e:
            pinned = "expected 67c19ae1" in str(e)
        check("an image that isn't the pinned MicroPython: the build stops", pinned)
        B.MICROPYTHON_SHA256 = None              # the stand-in, from here on
        page = B.build(work / "dist", work / "mp.bin")
        html = page.read_text(encoding="utf-8")
        check("built: Arborist-<release>.html", page.name == "Arborist-%s.html" % B.release())
        import re
        left = re.findall(r"@@(?:RELEASE|MICROPYTHON|SSID|PAYLOAD|ESPTOOL|ESPTOOL_LICENSE|REPL)@@", html)
        check("built: nothing left to fill in", not left, left)
        check("built: esptool-js in it, its licence too", "window.esptool={" in html and "Apache License" in html)
        (work / "template.py").write_text(tpl, encoding="utf-8")
        node_test("repl_test.js", MP, work / "repl", work / "template.py", B.FW)
        try:
            subprocess.run(["node", "-e", "require('playwright')"], check=True, capture_output=True, timeout=60)
            node_test("page_test.js", page, MP, work / "page")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("skip: page_test.js (no Playwright here)")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("FAILURES", FAIL)


main()
