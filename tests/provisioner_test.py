import os
import sys, json, re, hashlib, shutil, zipfile, builtins, subprocess
from pathlib import Path
import tempfile
W = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(W)); import provisioner as P
fw = W / 'final_firmware'
FAIL = []
def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c: FAIL.append(n)
# manifest excludes plugin files but keeps plugin.json
files = P.manifest_candidates(fw)
check('manifest keeps plugin.json', 'fservbot/plugin.json' in files and 'stumpid/plugin.json' in files)
check('manifest drops plugin files', not any(f.startswith(('fservbot/', 'stumpid/')) and not f.endswith('plugin.json') for f in files))
# a tool's own files left in the folder (a linter's cache, an editor's) are never firmware
_fw2 = Path(tempfile.mkdtemp()) / 'fw'
shutil.copytree(fw, _fw2, ignore=shutil.ignore_patterns('__pycache__', 'third_party', 'tools_payload'))
_before = P.manifest_candidates(_fw2)
(_fw2 / '.ruff_cache' / '0.1').mkdir(parents=True); (_fw2 / '.ruff_cache' / '0.1' / '123').write_text('x')
(_fw2 / 'urns' / '.idea').mkdir(); (_fw2 / 'urns' / '.idea' / 'x.xml').write_text('x')
(_fw2 / '.DS_Store').write_text('x'); (_fw2 / 'urns' / '.lxmf.py.swo').write_text('x')
check('manifest drops hidden files and folders', P.manifest_candidates(_fw2) == _before,
      sorted(set(P.manifest_candidates(_fw2)) - set(_before)))
check('api', P.firmware_plugin_api(fw) == 4)
check('the firmware zip is named with its release', re.match(r'^Stump-1\.0\.0-beta\.\d+\.zip$', P.firmware_zip_name(fw)) is not None
      and P._is_old_firmware_zip('Stump_Beta_A.zip', P.firmware_zip_name(fw)) and not P._is_old_firmware_zip(P.firmware_zip_name(fw), P.firmware_zip_name(fw)), P.firmware_zip_name(fw))
check('core manifest current', P._check_local_files_current(fw.resolve()) == [], P._check_local_files_current(fw.resolve()))
for n in ('fservbot', 'stumpid'):
    check(n + ' verifies', P.verify_plugin(fw / n) == ([], []), P.verify_plugin(fw / n))
# Every pack the release ships matches its own hashes: Arborist and the
# Provisioner's shelf refuse one that doesn't (Meter 1.3 and Marco Polo 1.0
# shipped in beta.82 edited after they were hashed). Fix: --hash-pack.
for p in P.discover_packs():
    r, w = P.check_pack(p, P.firmware_plugin_api(fw))
    check('shipped pack %s matches its hashes' % p['dir'].name, r is None and w == [], (r, w))
# packs: folder + zip of same -> listed once; zip extraction
P.PACKS_DIR = Path(tempfile.mkdtemp()) / 'packs'
P.PACK_CACHE_DIR = Path(tempfile.mkdtemp()) / 'cache'
shutil.rmtree(P.PACKS_DIR, ignore_errors=True); P.PACKS_DIR.mkdir()
shutil.copytree(W / 'packs/tavern-pack', P.PACKS_DIR / 'tavern-pack')
with zipfile.ZipFile(P.PACKS_DIR / 'rnfs-pack-1.0.zip', 'w') as z:
    for f in (W / 'packs/rnfs-pack').rglob('*'):
        if f.is_file(): z.write(f, 'rnfs-pack/' + str(f.relative_to(W / 'packs/rnfs-pack')))
with zipfile.ZipFile(P.PACKS_DIR / 'evil.zip', 'w') as z:
    z.writestr('../../escape.txt', 'x')
(P.PACKS_DIR / 'notapack').mkdir()
packs = P.discover_packs()
check('discover', sorted(p['spec']['name'] for p in packs) == ['rnfs', 'tavern'], [p['spec']['name'] for p in packs])
for p in packs:
    r, w = P.check_pack(p, 1); check('check ' + p['spec']['name'], r is None and w == [], (r, w))
    r, w = P.check_pack(p, 0); check('refuse old firmware ' + p['spec']['name'], r and 'needs plugin API' in r, r)
# A pack can't replace the core or a built-in plugin, nor list files outside its folder.
def _evil(name, files=None):
    root = Path(tempfile.mkdtemp()) / ("evil-" + name.replace("/", "_"))
    (root / name).mkdir(parents=True)
    (root / name / "install.py").write_text("def setup(stump):\n    pass\n")
    spec = {"name": name, "files": files} if files is not None else {"name": name}
    (root / name / "plugin.json").write_text(json.dumps(spec))
    (root / "pack.json").write_text(json.dumps({"name": "evil", "version": "1", "author": "x", "license": "x",
                                                 "stump_api": 1, "plugins": [name]}))
    return {"spec": json.loads((root / "pack.json").read_text()), "dir": root}
for nm, why in (("barkeep", "part of the Stump's own code"), ("stumpid", "built-in plugin"), ("urns", "can't be called"),
                ("Bad.Name", "lowercase"), ("config", "can't be called")):
    r, w = P.check_pack(_evil(nm), 1)
    check("a pack plugin called %s: refused (%s)" % (nm, why), r is not None and why in r, r)
r, w = P.check_pack(_evil("sneaky", ["sneaky/install.py", "sneaky/../barkeep.py"]), 1)
check("a plugin.json listing a file outside its folder: refused", r is not None and "outside its folder" in r, r)
r, w = P.check_pack(_evil("sneaky2", ["../config.py"]), 1)
check("... a path that climbs out: refused", r is not None and "outside" in r, r)
check("plugin_files never returns a path outside the plugin", all(f.startswith("tavern/") and ".." not in f
      for f in P.plugin_files(W / "packs/tavern-pack/tavern")))
# tamper -> warning
t = [p for p in packs if p['spec']['name'] == 'tavern'][0]
f = t['dir'] / 'tavern/install.py'; f.write_text(f.read_text() + '\nimport time\ntime.sleep(5)\n')
r, w = P.check_pack(t, 1)
check('tamper + lint noticed', any('changed since' in x for x in w) and any('time.sleep' in x for x in w), w)
P.describe_pack(t, 1)
# The lint: asyncio's sleep_ms yields, time's doesn't (#53).
_ld = Path(tempfile.mkdtemp()) / "lintme"; _ld.mkdir()
def _lint(src):
    (_ld / "install.py").write_text(src)
    return [w for _, _, w in P.lint_plugin(_ld)]
check("lint: await asyncio.sleep_ms() is fine", _lint("async def f():\n    await asyncio.sleep_ms(50)\n") == [])
check("lint: uasyncio.sleep_ms() and an awaited sleep_ms() are fine",
      _lint("async def f():\n    await uasyncio.sleep_ms(5)\n    await sleep_ms(5)\n") == [])
check("lint: time.sleep_ms() is still flagged", any("sleep_ms" in w for w in _lint("import time\ntime.sleep_ms(50)\n")))
check("lint: a bare sleep_ms() not awaited is still flagged", any("sleep_ms" in w for w in _lint("sleep_ms(50)\n")))
# fake mpremote for install + upload
calls = []
BOARD_CONFIG = ['CONFIG:1 TRACES:1']
MACHINE = ['ESP32S3 module with Octal-SPIRAM with ESP32S3']
def fake_run(cmd, timeout=None, **kw):
    calls.append(cmd)
    if 'exec' in cmd and any('SHA:' in str(c) for c in cmd): return True, 'SHA:none'
    if 'exec' in cmd and any("'MACHINE:'" in str(c) for c in cmd): return True, 'MACHINE:' + MACHINE[0]
    if 'exec' in cmd and any('TRACES' in str(c) for c in cmd): return True, BOARD_CONFIG[0]
    if 'exec' in cmd and any("'DIRS:'" in str(c) for c in cmd): return True, 'DIRS:flasher,rnfs,notes.txt'
    if cmd[-2:] == ['cat', ':themes.json']: return True, '{"old": {"label": "Old", "colors": {"--bg": "#000"}}}'
    if 'exec' in cmd and any('def walk' in str(c) for c in cmd):
        sizes = {}
        for c in calls:
            if 'cp' in c and str(c[-1]).startswith(':') and not str(c[-1]).startswith(':/sd'):
                sizes[c[-1][1:]] = Path(c[-2]).stat().st_size
        return True, repr(sizes)
    return True, ''
P.run = fake_run
# No downloads in tests: the Provisioner's own mpy-cross install is
# stood in for (asked for once, here: no mpy-cross on the test machine).
ENSURED = []
P.ensure_mpy_cross = lambda: ENSURED.append(1) or None
pushed_theme = {}
orig = P._merge_board_themes
def spy(port, themes):
    r = orig(port, themes)
    for c in calls[::-1]:
        if c[-1] == ':themes.json' and 'cp' in c:
            pushed_theme.update(json.loads(Path(c[-2]).read_text())); break
    return r
P._merge_board_themes = spy
ok = P.install_packs('/dev/fake', packs)
cps = [c for c in calls if 'cp' in c]
dests = [c[-1] for c in cps]
check('plugin files to flash', ':tavern/install.py' in dests and ':rnfs/strings.py' in dests and ':tavern/plugin.json' not in dests, dests)
check('tools to SD', ':/sd/tools/rnfs/rnfs-0.1.1-py3-none-any.whl' in dests and ':/sd/tools/rnfs/README.md' in dests, dests)
check('themes merged', set(pushed_theme) == {'old', 'tavern'} and pushed_theme['tavern']['colors']['--bg'] == '#1a120b', pushed_theme)
check('install ok', ok)
# upload_stump_app includes built-in plugins
calls.clear()
P.resolve_path = lambda *a, **k: fw
P.ask_yes_no = lambda *a, **k: True
P.install_tools_on_node = lambda port: None
ok = P.upload_stump_app('/dev/fake')
dests = [c[-1] for c in calls if 'cp' in c]
check('upload has core', ':plugins.py' in dests and ':rrc.py' in dests)
check('upload has builtin plugins', ':stumpid/strings.py' in dests and ':fservbot/templates.py' in dests and ':fservbot/plugin.json' not in dests and ':fservbot/README.md' not in dests, [d for d in dests if 'fserv' in d or 'stumpid' in d])
check('upload ok', ok)
check('update keeps the node config', ':config.py' not in dests, [d for d in dests if 'config' in d])
calls.clear(); BOARD_CONFIG[0] = 'CONFIG:0 TRACES:0'
P.upload_stump_app('/dev/fake')
check('fresh board gets a config', ':config.py' in [c[-1] for c in calls if 'cp' in c])
import io, contextlib
# A board a Stump ran on, its config.py gone: asked, never replaced silently
calls.clear(); BOARD_CONFIG[0] = 'CONFIG:0 TRACES:2'
_cb, P.CONFIG_BACKUPS = P.CONFIG_BACKUPS, Path(tempfile.mkdtemp()) / "none"
_ay, P.ask_yes_no = P.ask_yes_no, lambda *a, **k: False
with contextlib.redirect_stdout(io.StringIO()) as said:
    P.upload_stump_app('/dev/fake')
check("a Stump's board with its config.py gone: the folder's isn't copied without a yes",
      ':config.py' not in [c[-1] for c in calls if 'cp' in c] and "missing" in said.getvalue(), said.getvalue()[-400:])
P.ask_yes_no, P.CONFIG_BACKUPS = _ay, _cb
calls.clear(); BOARD_CONFIG[0] = 'CONFIG:1 TRACES:1'; sys.argv.append('--replace-config')
P.upload_stump_app('/dev/fake')
check('--replace-config replaces it', ':config.py' in [c[-1] for c in calls if 'cp' in c])
sys.argv.remove('--replace-config')
# A WROVER board (1.0.0-beta.56): the voice engines go on its SD card,
# not in its 2 MB of flash; a CAM keeps them in flash.
cam_dests = [c[-1] for c in calls if 'cp' in c]
check('a CAM: voice engines in flash', ':web/opus.wasm' in cam_dests and ':/sd/web/opus.wasm' not in cam_dests, cam_dests[-6:])
calls.clear(); MACHINE[0] = 'Generic ESP32 module with SPIRAM with ESP32'
P.upload_stump_app('/dev/fake')
w_dests = [c[-1] for c in calls if 'cp' in c]
check('a WROVER: voice engines on its SD card, not in flash',
      ':/sd/web/opus.wasm' in w_dests and ':/sd/web/codec2.wasm' in w_dests
      and ':web/opus.wasm' not in w_dests and ':web/opus.js' in w_dests, [d for d in w_dests if 'web' in d])
MACHINE[0] = 'ESP32S3 module with Octal-SPIRAM with ESP32S3'
check('no mpy-cross here: the Provisioner goes to install its own (not left to memory)', ENSURED, ENSURED)
check('chip from esptool: an S3, an original ESP32',
      P.chip_from_esptool('Detecting chip type... ESP32-S3') == 'esp32s3'
      and P.chip_from_esptool('Chip type:          ESP32-D0WD-V3 (revision v3.1)') == 'esp32'
      and P.chip_from_esptool('Chip is ESP32-D0WDQ6 (revision v1.0)') == 'esp32' and P.chip_from_esptool('nothing') is None)
check('each chip its image and offset (WROVER: generic SPIRAM build at 0x1000)',
      P.FIRMWARE_FOR_CHIP['esp32']['offset'] == '0x1000' and 'ESP32_GENERIC-SPIRAM' in P.FIRMWARE_FOR_CHIP['esp32']['url']
      and P.FIRMWARE_FOR_CHIP['esp32s3']['offset'] == '0x0' and 'SPIRAM_OCT' in P.FIRMWARE_FOR_CHIP['esp32s3']['url'])
# --skip-tools still brings the sections' READMEs up to date (sections on the card only)
calls.clear()
P.refresh_tool_readmes('/dev/fake')
rd = [c[-1] for c in calls if 'cp' in c]
check('READMEs refreshed for a section the Provisioner has (rnfs), the retired flasher skipped',
      ':/sd/tools/rnfs/README.fr.md' in rd and ':/sd/tools/rnfs/README.md' in rd
      and not any('/flasher/' in d for d in rd), rd)
check('no README for a section not on the card', not any('/provisioner/' in d or '/firefly/' in d for d in rd), rd)
# Stump on a computer: the config.py block (docs/PC.md)
b = P._pc_config_block({"pc_hotspot_name": "Apt 3B", "pc_heltec": "usb", "pc_heltec_port": "COM5",
                        "chain": {"enabled": True, "parent_ip": "192.168.4.1"}})
ns = {"CONFIG": {"interfaces": [{"type": "WiFiSerialInterface"}, {"type": "UDPInterface"}]}}
exec(b, ns)
check('pc block: USB Heltec replaces the Wi-Fi bridge', [i["type"] for i in ns["CONFIG"]["interfaces"]] ==
      ["UDPInterface", "RNodeSerialInterface"] and ns["CONFIG"]["interfaces"][1]["port"] == "COM5", ns["CONFIG"])
check('pc block: hotspot, parent, own clock', ns["PC_HOTSPOT_NAME"] == "Apt 3B" and ns["PC_CHAIN_PARENT_IP"] == "192.168.4.1"
      and ns["CONFIG"]["time_sync"] == {"enabled": False} and ns["PC_HTTP_PORT"] == 80)
ns = {"CONFIG": {"interfaces": [{"type": "WiFiSerialInterface", "target_host": "192.168.137.2"}]}}
exec(P._pc_config_block({"pc_heltec": "wifi"}), ns)
check('pc block: a Wi-Fi Heltec is kept', ns["CONFIG"]["interfaces"][0]["target_host"] == "192.168.137.2")
# Wi-Fi networks offered by the wizard instead of typed names
NETSH = """
Interface name : Wi-Fi
There are 3 networks currently visible.

SSID 1 : Bob's Glitch
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 60%
    BSSID 2                 : aa:bb:cc:dd:ee:00
         Signal             : 91%
SSID 2 : LaBuche-Stump.web.app
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 75%
SSID 3 :
    Authentication          : WPA2-Personal
"""
n = P.parse_netsh(NETSH)
check('netsh: names, best signal, open', [(x["ssid"], x["signal"], x["open"]) for x in n][:2] ==
      [("Bob's Glitch", 91, False), ("LaBuche-Stump.web.app", 75, True)], n)
n = P.parse_netsh("SSID 1 : Café\n    Authentification : Ouvrir\n    Signal : 40 %\n")
check('netsh in French', n == [{"ssid": "Café", "signal": 40, "open": True}], n)
n = P.parse_nmcli("Bob's Glitch:82:WPA2\nStump\\:4:55:\n:30:WPA2\n")
check('nmcli: escaped colon, open, hidden', [(x["ssid"], x["signal"], x["open"]) for x in n] ==
      [("Bob's Glitch", 82, False), ("Stump:4", 55, True), ("", 30, False)], n)
SP = json.dumps({"SPAirPortDataType": [{"spairport_airport_interfaces": [{
    "spairport_current_network_information": {"_name": "Bob's Glitch", "spairport_signal_noise": "-48 dBm / -95 dBm",
                                              "spairport_security_mode": "spairport_security_mode_wpa2_personal"},
    "spairport_airport_other_local_wireless_networks": [
        {"_name": "LaBuche-Stump.web.app", "spairport_signal_noise": "-70 dBm / -95 dBm",
         "spairport_security_mode": "spairport_security_mode_none"},
        {"_name": "<redacted>", "spairport_signal_noise": "-60 dBm"}]}]}]})
n = P.parse_system_profiler(SP)
check('system_profiler: current and nearby', sorted((x["ssid"], x["signal"], x["open"]) for x in n) ==
      [("<redacted>", 80, True), ("Bob's Glitch", 100, False), ("LaBuche-Stump.web.app", 60, True)], n)
nets = [{"ssid": "Bob's Glitch", "signal": 90, "open": False}, {"ssid": "LaBuche-Stump.web.app", "signal": 60, "open": True}]
def answers(*a):
    it = iter(a)
    builtins.input = lambda *x: next(it)
answers("2")
check('ask_wifi: pick by number', P.ask_wifi("Wi-Fi", "", scan=lambda: (nets, "")) == "LaBuche-Stump.web.app")
answers("r", "1")
check('ask_wifi: look again, then pick', P.ask_wifi("Wi-Fi", "", scan=lambda: (nets, "")) == "Bob's Glitch")
answers("Not Listed")
check('ask_wifi: type a name', P.ask_wifi("Wi-Fi", "", scan=lambda: (nets, "")) == "Not Listed")
answers("")
check('ask_wifi: blank keeps the default', P.ask_wifi("Wi-Fi", "Home", scan=lambda: (nets, "")) == "Home")
answers("Typed")
check('ask_wifi: no scan -> typed as before', P.ask_wifi("Wi-Fi", "", scan=lambda: ([], "no Wi-Fi")) == "Typed")
P.WIFI_SCAN.update({n["ssid"]: n for n in nets})
check('open network needs no password', P.wifi_is_open("LaBuche-Stump.web.app") and not P.wifi_is_open("Bob's Glitch")
      and not P.wifi_is_open("unknown"))
# The layout: a route, its sections, the summary
import io, contextlib
P.TIMINGS_FILE = Path(tempfile.mkdtemp()) / 'timings.json'
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    r = P.Route("Test route", [("One", "first"), ("Two", "second"), ("Three", "third")], needs=["a board"])
    r.show(); r.start("One"); print("inside one"); r.end()
    r.start("Two"); r.end("skip", "not asked")
    r.start("Three"); r.end("fail", "broke")
    r.note("Address", "http://192.168.4.1/")
    ok = r.summary("closing line")
    print("after")
out = buf.getvalue()
check('route: card lists the sections and needs', "1  One" in out and "a board" in out, out[:600])
check('route: section header with number', "1/3  ONE" in out and "3/3  THREE" in out, out)
check('route: content indented under its section', "\n  inside one\n" in out, out)
check('route: summary shows results and notes, fails overall', ok is False and "skipped" in out
      and "didn't work" in out and "http://192.168.4.1/" in out and "closing line" in out, out[-900:])
check('route: indentation off after the summary', out.endswith("\nafter\n"), out[-40:])
sys.stdout = sys.__stdout__
P._save_timing("cam", "Flash", 100); P._save_timing("cam", "Flash", 300); P._save_timing("cam", "Flash", 200)
check('route: the card uses the median of this computer\'s runs', P.route_for("cam").expected("Flash") == (200, True)
      and P.route_for("cam").expected("Verify") == (120, False))
for i in range(7):
    P._save_timing("cam", "Verify", i)
check('route: only the last five runs kept', P._load_timings()["cam"]["Verify"] == [2, 3, 4, 5, 6])
for role in ("cam", "heltec", "heltec_transport"):
    check('route for ' + role, len(P.route_for(role).steps) >= 4)
answers("9", "4")
with contextlib.redirect_stdout(io.StringIO()):
    picked = P.start_menu()
check('start menu: re-asks, then picks', picked == "pc", picked)
# The CAM route: every question in section 3, nothing asked after it
import io, contextlib
asked_after, phase, calls = [], [None], []
def fake_input(prompt=""):
    if phase[0] == "auto":
        asked_after.append(prompt)
    if prompt.startswith("Choose 1-") and "blank to quit" in prompt:
        return "1"                       # a Stump CAM
    return ""                            # defaults everywhere
builtins.input = fake_input
saved = {n: getattr(P, n) for n in ("check_tools", "resolve_path", "choose_board", "config_wizard",
                                    "flash_cam_micropython", "upload_stump_app", "push_config_to_board",
                                    "install_packs", "run", "get_stump_ip", "diagnostics", "keep_awake",
                                    "pick_board_firmware", "network_check", "board_image")}
P.board_image = lambda port: None                        # a board without the image
P.run = lambda *a, **k: (True, "")
P.network_check = lambda port, prof, addrs=None, **k: calls.append("reach") or (True, "its page answers", "http://192.168.0.161/")
P.pick_board_firmware = lambda port, path=None: calls.append("image") or ("esp32", fw / "main.py")
_cfg_dir = P.CONFIG_OUT_DIR
P.CONFIG_OUT_DIR = Path(tempfile.mkdtemp()) / "profiles"        # no saved settings
P.check_tools = lambda: True
P.resolve_path = lambda *a, **k: fw
P.choose_board = lambda role=None: "/dev/fake-cam"
def fake_wizard(role):
    calls.append("wizard"); return {"node_name": "t", "wifi_ssid": "", "packs": []}, None
P.config_wizard = fake_wizard
def flash(port, image=None, chip=None, erase=True): phase[0] = "auto"; calls.append("flash"); return True, port
P.flash_cam_micropython = flash
P.upload_stump_app = lambda port, app_dir=None: calls.append("upload") or True
P.push_config_to_board = lambda port, role, prof: calls.append("push") or True
P.install_packs = lambda *a: True
P.run = lambda *a, **k: (True, "")
P.get_stump_ip = lambda port: {"sta": "192.168.0.161", "ap": "192.168.4.1"}
P.diagnostics = lambda port, role=None, interactive=True, final=True: calls.append("diag") or {"serial": True}
P.keep_awake = lambda: True
real_sleep = P.time.sleep
P.time.sleep = lambda s: None
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.interactive_wizard()
out = buf.getvalue()
calls_first = list(calls)
# No MicroPython image found in section 3: nothing is put on a board left bare
calls.clear(); phase[0] = ""
P.pick_board_firmware = lambda port, path=None: calls.append("image") or ("esp32", None)
with contextlib.redirect_stdout(io.StringIO()) as said2:
    P.interactive_wizard()
P.CONFIG_OUT_DIR = _cfg_dir
check('CAM route: no image, nothing flashed, nothing installed on the bare board',
      calls == ["image", "wizard"] and "the flash didn't work" in said2.getvalue(), (calls, said2.getvalue()[-800:]))
P.time.sleep = real_sleep
for n, f in saved.items():
    setattr(P, n, f)
sys.stdout = sys.__stdout__
check('CAM route: the questions (the image too) come before anything touches the board',
      calls_first == ["image", "wizard", "flash", "upload", "push", "diag", "reach"], calls_first)
check('CAM route: nothing is asked after section 3', asked_after == [], asked_after)
check('CAM route: it says the rest runs by itself', "run by themselves now" in out and "step away" in out, out[-1500:])
check('CAM route: the card says who does what, and why it is slow', "Why so long" in out and "auto = it runs by itself" in out)
check('CAM route: the summary has the addresses', "http://192.168.4.1/" in out and "Took" in out)
# The CAM's config.py is saved in ~/.provisioner, never into the firmware
# folder (a changed file there made `git pull` refuse to run)
before = (fw / 'config.py').read_bytes()
P.CONFIG_OUT_DIR = Path(tempfile.mkdtemp()) / 'profiles'
sent = []
saved_run, saved_resolve = P.run, P.resolve_path
P.run = lambda cmd, **k: (sent.append(cmd), (True, ""))[1]
P.resolve_path = lambda *a, **k: fw
ok = P.push_config_to_board('/dev/fake', 'cam', {"node_name": "Test Node", "wifi_ssid": "x", "wifi_pass": "y",
                                                 "heltec_host": "192.168.4.2", "heltec_port": 7633})
P.run, P.resolve_path = saved_run, saved_resolve
out_file = P.CONFIG_OUT_DIR / 'Test_Node.config.py'
check('config push: the firmware folder is left alone', (fw / 'config.py').read_bytes() == before)
check('config push: saved in the profiles folder and sent from there', ok and out_file.is_file()
      and "Test Node" in out_file.read_text() and sent and str(out_file) in sent[0], sent)
# Which release: this folder, GitHub, the board
check('release: read from the firmware folder', P._folder_release(fw) == P._folder_release() and
      P._folder_release().startswith("1."), P._folder_release(fw))
lines = P.release_lines({"release": "1.2.3", "git": "v1.2.3", "behind": 0})
check('release: up to date', "1.2.3" in lines[0] and "Up to date" in lines[1], lines)
lines = P.release_lines({"release": "1.2.3", "git": "v1.2.2-4-gabc", "behind": 4})
check('release: behind GitHub says git pull', "4 newer commits" in lines[1] and "git pull" in lines[1], lines)
check('release: offline says so', "Couldn't ask GitHub" in P.release_lines({"release": "1", "git": "x", "behind": None})[1])
check('release: outside git, one line', len(P.release_lines({"release": "1", "git": None, "behind": None})) == 1)
check('release: no network from a test', P.checkout_status()["behind"] is None)
saved_run = P.run
here = P._folder_release()
P.run = lambda cmd, **k: (True, "STUMP_RELEASE:%s\n" % here)
check('release: the board matches', P.release_check('/dev/fake')[0] and P.release_check('/dev/fake')[2] == here)
P.run = lambda cmd, **k: (True, "STUMP_RELEASE:0.9\n")
ok, text, rel = P.release_check('/dev/fake')
check('release: an old board is caught', not ok and "0.9" in text and "re-upload" in text, text)
P.run = lambda cmd, **k: (False, "failed to access port")
check('release: unreadable board', P.release_check('/dev/fake')[0] is False)
P.run = saved_run
with contextlib.redirect_stdout(io.StringIO()) as b2:
    answers("1")
    P.start_menu(["Installs Stump 9.9 (this folder)"])
check('start menu: shows the release first', "Installs Stump 9.9" in b2.getvalue().split("What are you")[0])
# A computer Stump updated after a Heltec was plugged in by USB
pcdir = Path(tempfile.mkdtemp())
shutil.copy(P.STUMP_APP_DIR / "lora_boards.py", pcdir / "lora_boards.py")   # config.py imports it
base = open(Path(P.__file__).parent / "tests" / "config.py", encoding="utf-8").read()
(pcdir / "config.py").write_text(base + P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none",
                                 "chain": {"enabled": True, "parent_ip": "192.168.0.161"}})
                                 + "\nCHAIN_ENABLED = True\nNODE_NAME = 'Laptop'\n", encoding="utf-8")
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
check('pc: no Heltec read from config.py', P._pc_heltec_in_config(cfg) == "none")
check('pc: settings summary', P._pc_settings_summary(cfg) == "Laptop · no Heltec · 2048 MB of storage · joins the Stump at 192.168.0.161",
      P._pc_settings_summary(cfg))
P._pc_add_usb_heltec(pcdir)
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
kinds = [i["type"] for i in cfg["CONFIG"]["interfaces"]]
check('pc: the USB Heltec added, once, the rest kept', P._pc_heltec_in_config(cfg) == "usb"
      and kinds.count("RNodeSerialInterface") == 1 and "WiFiSerialInterface" not in kinds
      and cfg["PC_CHAIN_PARENT_IP"] == "192.168.0.161" and cfg["NODE_NAME"] == "Laptop", kinds)
# ... and through the update's Configure section
(pcdir / "config.py").write_text(base + P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
                                 + "\nNODE_NAME = 'Laptop'\n", encoding="utf-8")
saved_ports = P.list_serial_ports
P.list_serial_ports = lambda: [{"device": "/dev/cu.usbserial-0001", "description": "CP2102",
                                "guess": "CP2102 UART bridge — likely the Heltec V3"}]
answers("")                                   # "Use it?" -> yes
rt = P.Route("pc", key="pc", steps=[("Configure", "auto", "x")])
with contextlib.redirect_stdout(io.StringIO()) as said:
    rt.start("Configure")
    again = P._pc_configure_existing(rt, pcdir)
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
check('pc update: a Heltec plugged in is noticed and used, no questions again', not again
      and P._pc_heltec_in_config(cfg) == "usb" and "/dev/cu.usbserial-0001" in said.getvalue()
      and rt.state[0] == "done", said.getvalue()[-400:])
P.list_serial_ports = lambda: []
_yes_no = P.ask_yes_no
_yn = iter([False])                           # "Bluetooth instead?" -> no
P.ask_yes_no = lambda *a, **k: next(_yn)
answers("a")                                  # "Change something?" -> all the questions
rt = P.Route("pc", key="pc", steps=[("Configure", "auto", "x")])
with contextlib.redirect_stdout(io.StringIO()) as said:
    rt.start("Configure")
    again = P._pc_configure_existing(rt, pcdir)
check('pc update: says the settings, and can ask again', again and "Laptop" in said.getvalue(), said.getvalue()[-300:])
check('pc update: the USB Heltec unplugged: it waits for it, and reconnects by itself',
      "connect by itself" in said.getvalue(), said.getvalue()[-400:])
# ... or the Heltec over Bluetooth instead, the rest kept
P.ask_yes_no = _yes_no                         # "Bluetooth instead?" -> yes
answers("RNode 3B87")
rt = P.Route("pc", key="pc", steps=[("Configure", "auto", "x")])
with contextlib.redirect_stdout(io.StringIO()) as said:
    rt.start("Configure")
    again = P._pc_configure_existing(rt, pcdir)
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
ifs = [i for i in cfg["CONFIG"]["interfaces"] if i.get("type", "").startswith("RNode")]
check('pc update: switched to the Heltec over Bluetooth, by name, the rest kept', not again
      and P._pc_heltec_in_config(cfg) == "ble" and [i["type"] for i in ifs] == ["RNodeBLEInterface"]
      and ifs[0]["target"] == "RNode 3B87" and cfg["NODE_NAME"] == "Laptop"
      and "/admin" in said.getvalue() and "--bluetooth" not in said.getvalue() and rt.state[0] == "done",
      (ifs, said.getvalue()[-400:]))
check('pc: the summary says Bluetooth', "a Heltec over Bluetooth" in P._pc_settings_summary(cfg))
# A Bluetooth Stump with its Heltec plugged in to charge: left on Bluetooth
P.list_serial_ports = lambda: [{"device": "/dev/cu.usbserial-0001", "description": "CP2102",
                                "guess": "CP2102 UART bridge — likely the Heltec V3"}]
_yn2 = iter([])
P.ask_yes_no = lambda *a, **k: next(_yn2)
answers("")                                   # "Change something?" -> nothing
rt = P.Route("pc", key="pc", steps=[("Configure", "auto", "x")])
with contextlib.redirect_stdout(io.StringIO()) as said:
    rt.start("Configure")
    again = P._pc_configure_existing(rt, pcdir)
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
check('pc update: a Bluetooth Heltec plugged in to charge stays on Bluetooth (USB left alone)',
      P._pc_heltec_in_config(cfg) == "ble" and "charging is fine" in said.getvalue(), said.getvalue()[-300:])
P.ask_yes_no = _yes_no
P.list_serial_ports = lambda: []
# An update that changes only the storage: the menu, a wrong answer or
# two, then the size -- written in place, everything else kept.
before = (pcdir / "config.py").read_text(encoding="utf-8")
answers("s", "lots", "100", "99999999999", "8192")
rt = P.Route("pc", key="pc", steps=[("Configure", "auto", "x")])
with contextlib.redirect_stdout(io.StringIO()) as said:
    rt.start("Configure")
    again = P._pc_configure_existing(rt, pcdir)
after = (pcdir / "config.py").read_text(encoding="utf-8")
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
check('pc update: only the storage changed, in place, no questions again', not again and cfg["PC_STORAGE_MB"] == 8192
      and after.count("PC_STORAGE_MB") == 1 and P._pc_heltec_in_config(cfg) == "ble" and cfg["NODE_NAME"] == "Laptop"
      and [l for l in before.splitlines() if "PC_STORAGE_MB" not in l] == [l for l in after.splitlines() if "PC_STORAGE_MB" not in l]
      and rt.state[0] == "done", said.getvalue()[-500:])
check('... it said what the disk has free, and refused a wrong size',
      "free" in said.getvalue() and "At least 256 MB" in said.getvalue() and "at most" in said.getvalue()
      and "A number of MB" in said.getvalue(), said.getvalue()[-700:])
check('... the summary says it', "8192 MB of storage" in P._pc_settings_summary(cfg))
check('... and the questions again start from it', P._pc_storage_in_folder(pcdir) == 8192)
# An older config.py without the line: the storage is added at the end
(pcdir / "config.py").write_text("\n".join(l for l in after.splitlines() if "PC_STORAGE_MB" not in l) + "\n", encoding="utf-8")
P._set_config_value(pcdir, "PC_STORAGE_MB", 4096)
with contextlib.redirect_stdout(io.StringIO()):
    cfg = P._read_folder_config(pcdir)
check('pc: storage added to an older config.py', cfg["PC_STORAGE_MB"] == 4096 and cfg["NODE_NAME"] == "Laptop")
check('pc: a new Stump gets the size asked', "PC_STORAGE_MB = 8192 " in P._pc_config_block({"pc_hotspot_name": "x", "pc_storage_mb": 8192}))
check('pc: a nearly full disk gets a smaller default', P._pc_storage_default(3 * 1024 ** 3) == 768
      and P._pc_storage_default(500 * 1024 ** 3) == 2048)
block = P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "ble", "pc_heltec_ble": "auto",
                            "pc_radio": {"frequency": 917112000}})
check('pc: a new Stump with the Heltec over Bluetooth', "'type': 'RNodeBLEInterface'" in block
      and "'target': 'auto'" in block and "917112000" in block, block)
P.list_serial_ports = saved_ports
# Keeping a board's settings on an update: has it a config.py, or did a
# Stump run there and lose it (never replaced silently then)?
_run = P.run
for out, want in (("CONFIG:1 TRACES:2", "yes"), ("CONFIG:0 TRACES:0", "fresh"),
                  ("CONFIG:0 TRACES:1", "lost"), ("garbage", "unknown")):
    P.run = lambda cmd, out=out, **k: (True, out)
    check("a board's config: %s" % want, P._board_config_state("/dev/x") == want)
P.run = lambda cmd, **k: (False, "could not enter raw repl")
check("... can't tell: kept (unknown)", P._board_config_state("/dev/x") == "unknown")
P.run = _run
check("what a config.py sets, in one line", P._config_summary("NODE_NAME = 'LaBuche'\nCHAIN_ENABLED = True\n"
      "FEATURES = ['chat', 'about']\n") == "name 'LaBuche', chain on, features ['chat', 'about']")
# Erasing a board's flash (a CAM or a Heltec)
_run2, _ay2 = P.run, P.ask_yes_no
seen = []
P.run = lambda cmd, **k: (seen.append(cmd), (True, ""))[1]
P.ask_yes_no = lambda *a, **k: False
with contextlib.redirect_stdout(io.StringIO()):
    r = P.erase_flash("/dev/cu.usbserial-0001", board="heltec")
check("erase the flash: nothing without a yes", r is False and not seen, seen)
P.ask_yes_no = lambda *a, **k: True
with contextlib.redirect_stdout(io.StringIO()) as said:
    r = P.erase_flash("/dev/cu.usbserial-0001", board="heltec")
check("... a Heltec: esptool erase-flash on that port, and how to set it up again",
      r and seen == [["esptool", "--port", "/dev/cu.usbserial-0001", "erase-flash"]]
      and "A Heltec" in said.getvalue(), (seen, said.getvalue()[-300:]))
seen.clear()
with contextlib.redirect_stdout(io.StringIO()) as said:
    P.erase_flash("/dev/cu.usbmodem1", board="cam")
check("... a CAM: its config.py copied to this computer first, then erased",
      len(seen) >= 2 and ":/config.py" in seen[0] and seen[-1][-1] == "erase-flash"
      and "SD card" in said.getvalue(), seen)
P.run, P.ask_yes_no = _run2, _ay2
# Before every USB command to a board: its watchdog given 2 hours
cmd = P._with_quiet_watchdog(["mpremote", "connect", "/dev/x", "fs", "cp", "a", ":b"])
check('a USB command to a board: its watchdog given 2 hours first', cmd[3] == "exec"
      and "WDT(timeout=7200000)" in cmd[4] and cmd[5:] == ["fs", "cp", "a", ":b"], cmd)
check('... not before a reset, nor for other tools', P._with_quiet_watchdog(["mpremote", "connect", "/dev/x", "reset"])
      == ["mpremote", "connect", "/dev/x", "reset"] and P._with_quiet_watchdog(["esptool.py", "x"]) == ["esptool.py", "x"])
# macOS: the networks this Mac has joined, when it hides the ones around
ports_txt = "Hardware Port: Ethernet\nDevice: en1\nEthernet Address: x\n\nHardware Port: Wi-Fi\nDevice: en0\nEthernet Address: y\n"
pref_txt = "Preferred networks on en0:\n\tBob's Glitch\n\tLaBuche-Stump.web.app\n"
check('mac: the Wi-Fi device', P.parse_mac_ports(ports_txt) == "en0")
check('mac: saved networks, in order', P.mac_saved_networks(lambda cmd: ports_txt if "-listallhardwareports" in cmd
      else (pref_txt if cmd[-1] == "en0" else "")) == ["Bob's Glitch", "LaBuche-Stump.web.app"])
# Without pyserial, a Heltec's port is still recognised by its name
import types
saved_serial = sys.modules.get("serial.tools")
sys.modules["serial.tools"] = None                     # import fails
saved_listdir, saved_isdir = P.os.listdir, P.os.path.isdir
P.os.listdir = lambda d: ["cu.usbserial-0001", "cu.usbmodem5C372383661", "tty.Bluetooth"]
P.os.path.isdir = lambda d: True
ports = P.list_serial_ports()
P.os.listdir, P.os.path.isdir = saved_listdir, saved_isdir
if saved_serial is None:
    del sys.modules["serial.tools"]
else:
    sys.modules["serial.tools"] = saved_serial
guess = {p["device"]: p["guess"] for p in ports}
check('no pyserial: the Heltec and the CAM told apart by port name', "Heltec" in guess.get("/dev/cu.usbserial-0001", "")
      and "CAM" in guess.get("/dev/cu.usbmodem5C372383661", ""), guess)
# remove_plugin refuses core
check('remove refuses urns', P.remove_plugin('/dev/fake', 'urns') is False)
# A Heltec on the router: each its own address, the first free from .222
# (1.0.0-beta.58: always .222, three Heltecs on one router took the same).
check('Heltec address: .222 when nothing is there',
      P._free_heltec_ip("192.168.0.", probe=lambda ip: False, stumps=()) == "192.168.0.222")
check('... .223 when something answers at .222',
      P._free_heltec_ip("192.168.0.", probe=lambda ip: ip.endswith(".222"), stumps=()) == "192.168.0.223")
check('... .224 when a Stump says its Heltec (switched off) is at .223',
      P._free_heltec_ip("192.168.0.", probe=lambda ip: ip.endswith(".222"), stumps={"192.168.0.223"}) == "192.168.0.224")
hw = {"http://192.168.0.173/hw.json": {"radios": [{"name": "Heltec", "where": "Wi-Fi 192.168.0.223:7633"}]}}
check('a Stump\'s Heltec address read from its /hw.json',
      P._stumps_heltecs("192.168.0.", find=lambda prefix, get=None: [("192.168.0.173", {})],
                        get=lambda url, timeout=0: hw[url]) == {"192.168.0.223"})
# --answers FILE.json (the RK3326 Provisioner): the wizards with no one at the keyboard.
import io, contextlib, os, importlib
P = importlib.reload(P)                  # (the stand-ins above for ask_yes_no and others, gone)
builtins.input = lambda *a: (_ for _ in ()).throw(AssertionError("asked the keyboard"))
check('answers: a question is keyed by its text, to its "?", no parentheses, any case',
      P._qkey("Use it for this Stump (the other settings stay as they are)?") == "use it for this stump"
      and P._qkey("Change something? s: the storage (now 2048 MB)") == "change something"
      and P._qkey("  Storage for this Stump, in MB ") == "storage for this stump, in mb")
tmp = Path(tempfile.mkdtemp())
SECRET = "Hunter2-not-printed"
(tmp / "a.json").write_text(json.dumps({
    "_comment": "ignored", "Node name": "Handheld", "Admin password (/admin)": SECRET + "-admin", "Is this Stump part of a chain?": "no",
    "The Heltec": 3, "Theme": "amb", "FSERV bot operator password": SECRET,
    "How should file sharing work?": "Free", "Identity requirement": "open", "nobody asks this": 1}))
P.load_answers(tmp / "a.json")
heltec_opts = None
real_choice = P.ask_choice
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        prof, _ = P.config_wizard("pc")
    ok = True
except SystemExit as e:
    prof, ok = {}, ("exit", e.code)
text = out.getvalue()
check('answers: the --pc wizard runs from the file, nothing asked on the keyboard', ok is True and prof.get("node_name") == "Handheld",
      (ok, text[-300:]))
check('... a choice by its number or the start of its text', ok is True and "theme" in json.dumps(prof).lower(), prof)
check('... an answer is never printed (passwords among them)', SECRET not in text and "(answered from a.json)" in text)
check('... a question left out takes its default, said so', "(the default)" in text)
check('... keys no question asked are reported', P.answers_unused() == ["nobody asks this"], P.answers_unused())
P.ANSWERS.update(map=None, file=None, asked={}, used=set())
# A pack chosen in its own step (1.0.0-beta.75): its settings asked there,
# its bot and look offered (taken by default), kept by "Everything else".
(tmp / "c.json").write_text(json.dumps({
    "Node name": "Packed", "Admin password (/admin)": SECRET + "-admin", "Is this Stump part of a chain?": "no",
    "The Heltec": 3, "Install tavern?": True, "Keep these defaults? (n: go through them)": True}))
P.load_answers(tmp / "c.json")
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        prof, _ = P.config_wizard("pc")
    ok = True
except SystemExit as e:
    prof, ok = {}, ("exit", e.code)
text = out.getvalue()
check('setup: a pack in its own step; its settings, bot and look kept with the other defaults',
      ok is True and any("tavern-pack" in x for x in prof.get("packs", [])) and prof.get("bot_name") == "Tavernier"
      and prof.get("theme") == "tavern" and 0 <= text.find("TAVERN_NICK") < text.find("Everything else")
      and "pack_config" not in prof and "builtin_config" not in prof, (ok, {k: prof.get(k) for k in ("packs", "bot_name", "theme")}, text[-400:]))
check('... the Packs step comes before Everything else', 0 <= text.find("Packs") < text.find("Everything else"), text[:300])
P.ANSWERS.update(map=None, file=None, asked={}, used=set())
(tmp / "b.json").write_text(json.dumps({"Node name": "X"}))
P.load_answers(tmp / "b.json")
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.ask("A question with no default")
        code = None
    except SystemExit as e:
        code = e.code
check('answers: a question not in the file with no default stops the run (exit 3)', code == 3, code)
P.ANSWERS["map"]["pick"] = "o"
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.ask_choice("Pick?", ["one", "other"])
        code = None
    except SystemExit as e:
        code = e.code
check('answers: a choice that matches several options stops the run', code == 3, code)
P.ANSWERS["map"]["again"] = True
with contextlib.redirect_stdout(io.StringIO()):
    try:
        for _ in range(10):
            P.ask_yes_no("Again?", False)
        code = None
    except SystemExit as e:
        code = e.code
check('answers: the same question over and over (a retry loop) stops the run', code == 3, code)
P.ANSWERS.update(map=None, file=None, asked={}, used=set())
with contextlib.redirect_stdout(io.StringIO()):
    tpl = P.pc_answers_template()
check('--answers-template: the questions with their defaults', tpl.get("node name") == "unnamed-stump"
      and tpl.get("start this stump automatically when you log in") is True and "_about" in tpl
      and P.ANSWERS["record"] is None, sorted(tpl)[:8])
shutil.rmtree(tmp, ignore_errors=True)

# Arborist on the Tools page (since 1.0.0-beta.49): a computer Stump gets it.
tmp = Path(tempfile.mkdtemp())
rel = P._folder_release(P.STUMP_APP_DIR)
check('arborist_page: none here', P.arborist_page(tmp) is None)
(tmp / "Arborist-0.0.1-old.html").write_text("other release")
check('arborist_page: one from another release is never used', P.arborist_page(tmp) is None)
(tmp / "dist").mkdir()
page = tmp / "dist" / ("Arborist-%s.html" % rel)
page.write_text("<html>arborist</html>")
check('arborist_page: this release, in dist/', P.arborist_page(tmp) == page)
folder = tmp / "Stump"
(folder / "sd" / "tools" / "arborist").mkdir(parents=True)
(folder / "sd" / "tools" / "arborist" / "Arborist-0.0.1-old.html").write_text("old")
real_page = P.arborist_page
P.arborist_page = lambda here=None, fw=None: page
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_install_tools(P.STUMP_APP_DIR, folder)
P.arborist_page = real_page
arb = folder / "sd" / "tools" / "arborist"
check('computer Stump: Arborist on its Tools page, with its READMEs, the old one gone',
      (arb / page.name).read_text() == "<html>arborist</html>" and (arb / "README.md").is_file()
      and (arb / "README.fr.md").is_file() and not (arb / "Arborist-0.0.1-old.html").exists(),
      sorted(x.name for x in arb.iterdir()))
check('the Tools page READMEs include Arborist\'s (kept up to date on a card)',
      any(sec == "arborist" for _, sec in P._tool_readmes(P.Path(P.__file__).resolve().parent)))
# Updating a computer Stump: with an answers file (the RK3326 Provisioner)
# the packs it doesn't have are asked one by one, as before; at the
# keyboard, "p" opens its packs screen instead (1.0.0-beta.75: changing
# packs isn't the whole setup again). The installed ones are brought up
# to date either way, and each now records its pack.
upd = P.Path(tempfile.mkdtemp(prefix="stump-upd-"))
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_copy_firmware(P.STUMP_APP_DIR, upd)
shutil.copy(P.Path(P.__file__).resolve().parent / "tests" / "config.py", upd / "config.py")
saved = (P._pc_ensure_venv, P._pc_configure_existing, P._pc_install_tools, P.ask_yes_no, P.ask, P.packs_screen)
asked, screens = [], []
P._pc_ensure_venv = lambda f: sys.executable
P._pc_configure_existing = lambda r, f: False
P._pc_install_tools = lambda fw, f: None
P.ask_yes_no = lambda q, d=False: (asked.append(q), "nomadnet" in q)[1]
P.ANSWERS["map"] = {}
with contextlib.redirect_stdout(io.StringIO()) as out:
    P.setup_pc(folder=str(upd))
P.ANSWERS["map"] = None
check('a --pc update with an answers file: each pack it lacks asked; the ones said yes to installed, with their pack recorded',
      any("Install nomadnet?" in q for q in asked) and any("Install simple?" in q for q in asked)
      and (upd / "nomadnet" / "install.py").is_file() and not (upd / "simple").exists()
      and json.loads((upd / "nomadnet" / P.PACK_MARK).read_text())["pack"] == "nomadnet", asked)
asked.clear()
P.ask = lambda q, d=None: (asked.append(P._plain(q)), "p" if P._plain(q).startswith("p: its packs") else d)[1]
P.packs_screen = lambda target, packs=None: screens.append(str(target)) or True
(upd / "nomadnet" / "install.py").write_text("# an older copy\n")
with contextlib.redirect_stdout(io.StringIO()):
    P.setup_pc(folder=str(upd))
check('... at the keyboard: no pack asked one by one; p opens its packs screen, for this Stump',
      not any(q.startswith("Install ") for q in asked) and screens == [str(upd)], (asked, screens))
check('... and nomadnet, installed, brought up to date', "older copy" not in (upd / "nomadnet" / "install.py").read_text())
P._pc_ensure_venv, P._pc_configure_existing, P._pc_install_tools, P.ask_yes_no, P.ask, P.packs_screen = saved
# An update removes the files an earlier release put there and this one
# no longer has (1.0.0-beta.74): flasher_ui.py, retired in beta.41, was
# still on a computer Stump updated through beta.73. Never its settings,
# data, identity, nor a pack's files.
shipped = set(P._board_files(P.STUMP_APP_DIR))
check('retired files: none of them shipped by this release', not (set(P.RETIRED_FILES) & shipped),
      set(P.RETIRED_FILES) & shipped)
ret = P.retired_files(P.STUMP_APP_DIR)
check('... a retired module, as its source and its compiled copy', "flasher_ui.py" in ret and "flasher_ui.mpy" in ret, ret)
for rel, data in (("flasher_ui.py", "old"), ("lib/ed25519_fast_xtensawin.mpy", "old"), ("rooms.json", "{}"),
                  ("rns/identity", "its identity"), ("meter/install.py", "def setup(s):\n    pass\n"), ("meter/old.py", "a pack's"),
                  ("fservbot/gone.py", "old")):
    (upd / rel).parent.mkdir(parents=True, exist_ok=True)
    (upd / rel).write_text(data)
cfg_before = (upd / "config.py").read_text()
saved_ret = P.RETIRED_FILES
P.RETIRED_FILES = saved_ret + ("meter/old.py", "fservbot/gone.py", "olddir/gone.py")
(upd / "olddir").mkdir(); (upd / "olddir" / "gone.py").write_text("old")
with contextlib.redirect_stdout(io.StringIO()) as out:
    gone = P._pc_remove_retired(P.STUMP_APP_DIR, upd)
check('a --pc update: the files the release no longer has go, and are named',
      sorted(gone) == ["flasher_ui.py", "fservbot/gone.py", "lib/ed25519_fast_xtensawin.mpy", "olddir/gone.py"]
      and "flasher_ui.py" in out.getvalue(), (gone, out.getvalue()))
check('... a folder left empty goes too; one with anything else in it stays', not (upd / "olddir").exists() and (upd / "fservbot").is_dir())
check('... its settings, data, identity and a pack\'s own file stay',
      (upd / "config.py").read_text() == cfg_before and (upd / "rooms.json").is_file()
      and (upd / "rns" / "identity").read_text() == "its identity" and (upd / "meter" / "old.py").read_text() == "a pack's")
with contextlib.redirect_stdout(io.StringIO()):
    check('... the next update: nothing left to remove', P._pc_remove_retired(P.STUMP_APP_DIR, upd) == [])
# On a board: the same, in one call (the code run here as the board would).
sent = []
real_run = P.run
board = Path(tempfile.mkdtemp(prefix="board-"))
for rel in ("flasher_ui.mpy", "meter/install.mpy", "meter/old.py", "fservbot/install.mpy", "fservbot/gone.py", "config.py"):
    (board / rel).parent.mkdir(parents=True, exist_ok=True)
    (board / rel).write_text("x")
def _on_board(cmd, timeout=None):
    sent.append(cmd)
    here = os.getcwd(); os.chdir(board)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(cmd[-1], {})
    finally:
        os.chdir(here)
    return True, buf.getvalue()
P.run = _on_board
with contextlib.redirect_stdout(io.StringIO()) as out:
    n = P._remove_retired_on_board("/dev/ttyUSB0", P.STUMP_APP_DIR)
P.run = real_run
check('a board update: the same files go, in one call', n == 2 and len(sent) == 1 and not (board / "flasher_ui.mpy").exists()
      and not (board / "fservbot" / "gone.py").exists(), (n, sent[-1][:4], sorted(str(p.relative_to(board)) for p in board.rglob("*"))))
check('... a pack\'s own file and its settings stay', (board / "meter" / "old.py").exists() and (board / "config.py").exists())
P.RETIRED_FILES = saved_ret
shutil.rmtree(board, ignore_errors=True)
# scripts/update_manifest.py: a file that leaves the firmware is listed
# as retired; one that comes back isn't.
mt = Path(tempfile.mkdtemp(prefix="manifest-"))
shutil.copy(W / "provisioner.py", mt / "provisioner.py")
shutil.copytree(W / "final_firmware", mt / "final_firmware", ignore=shutil.ignore_patterns("__pycache__"))
(mt / "scripts").mkdir(); shutil.copy(W / "scripts" / "update_manifest.py", mt / "scripts" / "update_manifest.py")
(mt / "final_firmware" / "qr.py").unlink()
r = subprocess.run([sys.executable, str(mt / "scripts" / "update_manifest.py")], capture_output=True, text=True)
src = (mt / "provisioner.py").read_text()
blk = re.search(r"RETIRED_FILES = \((.*?)\n\)", src, re.S).group(1)
check('update_manifest: a file that left the firmware becomes retired, and is said',
      '"qr.py"' in blk and '"flasher_ui.py"' in blk and '"qr.py":' not in src and "retired: qr.py" in r.stdout, r.stdout + r.stderr)
shutil.copy(W / "final_firmware" / "qr.py", mt / "final_firmware" / "qr.py")
r = subprocess.run([sys.executable, str(mt / "scripts" / "update_manifest.py")], capture_output=True, text=True)
blk = re.search(r"RETIRED_FILES = \((.*?)\n\)", (mt / "provisioner.py").read_text(), re.S).group(1)
check('... and back in the firmware: not retired any more', '"qr.py"' not in blk and '"flasher_ui.py"' in blk, r.stdout + r.stderr)
shutil.rmtree(mt, ignore_errors=True)
shutil.rmtree(upd, ignore_errors=True)
# Packs, on their own (1.0.0-beta.75): --packs lists a Stump's packs and
# installs, updates or removes one; removing asks whether what it keeps
# goes too. Nothing else about the Stump is asked.
ps = P.Path(tempfile.mkdtemp(prefix="stump-packs-"))
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_copy_firmware(P.STUMP_APP_DIR, ps)
shutil.copy(P.Path(P.__file__).resolve().parent / "tests" / "config.py", ps / "config.py")
(ps / "meter").mkdir(); (ps / "meter" / "install.py").write_text("def setup(s):\n    pass\n")   # copied in by hand
here = [P.load_pack(W / "packs" / n) for n in ("tavern-pack", "simple-pack")]
# Announce stamps (1.0.0-beta.86): --stamp-announces works out what a
# Stump lists in rns/stamps_wanted.json and gives it rns/stamps.json. A
# light cost so the test is quick.
_st = Path(tempfile.mkdtemp(prefix="stump-stamps-"))
(_st / "stump_pc.py").write_text("x"); (_st / "rns").mkdir()
with contextlib.redirect_stdout(io.StringIO()) as out:
    none_yet = P.stamp_announces(str(_st))
check('--stamp-announces before the Stump lists anything: says how', not none_yet and "Announce stamps" in out.getvalue(), out.getvalue())
_m1, _m2 = os.urandom(90), os.urandom(90)
_good = None
_keep_cost, P.STAMP_COST = P.STAMP_COST, 10
while _good is None:
    _good = P._stamp_search((_m2, 100000, 10))
(_st / "rns" / "stamps_wanted.json").write_text(json.dumps({"aa" * 16: _m1.hex(), "bb" * 16: _m2.hex()}))
(_st / "rns" / "stamps.json").write_text(json.dumps({"bb" * 16: _good.hex(), "cc" * 16: "00" * 14}))
with contextlib.redirect_stdout(io.StringIO()) as out:
    ok = P.stamp_announces(str(_st))
got = json.loads((_st / "rns" / "stamps.json").read_text())
check('--stamp-announces: the missing one worked out and checks; the one there kept; the rest of the file kept',
      ok and P._stamp_ok(_m1, bytes.fromhex(got["aa" * 16])) and got["bb" * 16] == _good.hex() and "cc" * 16 in got, out.getvalue())
with contextlib.redirect_stdout(io.StringIO()) as out:
    again = P.stamp_announces(str(_st))
check('... run again: nothing to do', again and "all there" in out.getvalue(), out.getvalue())
P.STAMP_COST = _keep_cost
shutil.rmtree(_st, ignore_errors=True)
# A pack's name becomes a folder and goes in its record (1.0.0-beta.84): a
# downloaded pack named "../.." is refused, as Arborist refuses it.
_bad = Path(tempfile.mkdtemp(prefix="pack-badname-"))
for nm in ("../..", "tools/x", "Bad Name", ".hidden"):
    (_bad / "pack.json").write_text(json.dumps({"name": nm, "version": "1", "author": "a", "license": "MIT"}))
    with contextlib.redirect_stdout(io.StringIO()) as out:
        got = P.load_pack(_bad)
    check('a pack named %r is refused' % nm, got is None and "plain name" in out.getvalue(), out.getvalue())
shutil.rmtree(_bad, ignore_errors=True)
said = []
real_ask, real_yn = P.ask, P.ask_yes_no
def script(*a):
    it = iter(a)
    P.ask = lambda q, d=None: (said.append(P._plain(q)), next(it))[1]
    P.ask_yes_no = lambda q, d=True: (said.append(P._plain(q)), next(it) == "y")[1]
script("2", "i", "1", "i", "")
with contextlib.redirect_stdout(io.StringIO()) as out:
    P.packs_screen(str(ps), packs=here)
rec = json.loads((ps / "tavern" / P.PACK_MARK).read_text())
check('--packs on a computer Stump: each pack listed, the one copied in by hand too',
      all(n in out.getvalue() for n in ("simple", "tavern", "meter")) and "not among the packs here" in out.getvalue(), out.getvalue()[-600:])
check('... install: its plugin there, its pack recorded (version, theme), its theme added',
      (ps / "tavern" / "install.py").is_file() and rec["pack"] == "tavern" and rec["version"] == here[0]["spec"]["version"]
      and rec["theme"] == "tavern" and "tavern" in json.loads((ps / "themes.json").read_text())
      and (ps / "simple" / P.PACK_MARK).is_file(), rec)
check('... nothing else asked (no setup question)', all(q.startswith(("A pack's number", "tavern:", "simple:")) for q in said), said)
rows = P.pack_rows(here, P.pc_installed_packs(ps))
check('... then: up to date, and the hand-copied one as not among the packs here',
      [(r["name"], r["state"]) for r in rows] == [("simple", "same"), ("tavern", "same"), ("meter", "yours")], rows)
r0 = json.loads((ps / "simple" / P.PACK_MARK).read_text()); r0["version"] = "0.9"
(ps / "simple" / P.PACK_MARK).write_text(json.dumps(r0))
check('... an older version installed: offered as an update', P.pack_rows(here, P.pc_installed_packs(ps))[0]["state"] == "update")
for d in ("tavern", "meter"):
    (ps / "sd" / "plugins" / d).mkdir(parents=True, exist_ok=True); (ps / "sd" / "plugins" / d / "data.json").write_text("{}")
said.clear()
script("1", "u", "2", "r", "y", "y", "3", "r", "y", "n", "")
with contextlib.redirect_stdout(io.StringIO()) as out:
    P.packs_screen(str(ps), packs=here)
check('... update: the newer one, recorded', json.loads((ps / "simple" / P.PACK_MARK).read_text())["version"] == here[1]["spec"]["version"])
check('... remove, its data too (said yes): plugin, saved data and theme gone',
      not (ps / "tavern").exists() and not (ps / "sd" / "plugins" / "tavern").exists()
      and "tavern" not in json.loads((ps / "themes.json").read_text()), out.getvalue()[-500:])
check('... the question names what it keeps', any(q.startswith("Delete what it keeps too: its saved data (sd/plugins/tavern)")
      and "its theme (tavern)" in q for q in said), said)
check('... remove, its data kept (said no): the plugin gone, its data stays',
      not (ps / "meter").exists() and (ps / "sd" / "plugins" / "meter" / "data.json").is_file())
check('... a running Stump restarts on the change', (ps / ".updated").is_file())
P.ask, P.ask_yes_no = real_ask, real_yn
shutil.rmtree(ps, ignore_errors=True)
# On a board: what it has, read in one call (run here as the board would).
brd = Path(tempfile.mkdtemp(prefix="board-"))
for rel, data in (("tavern/install.mpy", "x"), ("tavern/" + P.PACK_MARK, json.dumps({"pack": "tavern", "version": "1.2"})),
                  ("simple/install.py", "x"), ("stumpid/install.mpy", "x"), ("urns/__init__.mpy", "x"), ("config.py", "x")):
    (brd / rel).parent.mkdir(parents=True, exist_ok=True); (brd / rel).write_text(data)
real_run = P.run
def _brd(cmd, timeout=None):
    here_ = os.getcwd(); os.chdir(brd)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(cmd[-1], {})
    finally:
        os.chdir(here_)
    return True, buf.getvalue()
P.run = _brd
got = P.board_installed_packs("/dev/ttyUSB0")
P.run = real_run
check('a board\'s packs: its plugins that aren\'t built in, with their record (none before beta.75)',
      got == {"tavern": {"pack": "tavern", "version": "1.2"}, "simple": None}, got)
shutil.rmtree(brd, ignore_errors=True)
# A tampered pack record (1.0.0-beta.84): removing the pack "with what it
# keeps" read its places from the record, and could have emptied the card
# -- on a computer Stump, anything its user owns ("/" in the record reached
# the disk's root). Only the pack's own places go now; home files one by one.
# SAFETY: every entry here is relative (never "/" nor an absolute path), so
# even against code without the fix it reaches no further than the test's
# own temporary folder.
CRAFTED = {"pack": "tavern", "version": "1.2", "plugins": ["tavern"], "theme": {"x": 1},
           "tools": ["tools/", "tools", "tools/..", "tools/../home", "tools/provisioner", "tools/firefly", "tools/apps",
                     "../shared", "", "..", "tools/tavern"],
           "home": ["", ".", "..", "../", "../tools/provisioner/provisioner.py", "sub/dir", "sub", ".hidden", "banner.txt"]}
for _e in CRAFTED["tools"] + CRAFTED["home"]:
    assert not _e.startswith("/"), "a crafted entry must stay relative"
KEEP = ["sd/tools/provisioner/provisioner.py", "sd/tools/firefly/README.md", "sd/tools/apps/x.zip",
        "sd/home/index.html", "sd/home/sub/dir", "sd/home/.hidden", "sd/notes.txt", "shared/photo.jpg", "config.py"]
def _stump(root):
    for k in KEEP + ["sd/plugins/tavern/data.json", "sd/tools/tavern/plays.txt", "sd/home/banner.txt"]:
        (root / k).parent.mkdir(parents=True, exist_ok=True); (root / k).write_text("x")
def _stump_ok(root):
    return ([k for k in KEEP if not (root / k).is_file()],
            [k for k in ("sd/plugins/tavern", "sd/tools/tavern", "sd/home/banner.txt") if (root / k).exists()])
row = {"name": "tavern", "pack": None, "plugins": ["tavern"], "record": CRAFTED, "state": "yours"}
_jail = Path(tempfile.mkdtemp(prefix="crafted-jail-"))
ps = _jail / "stump"
(ps / "tavern").mkdir(parents=True); (ps / "tavern" / "install.py").write_text("x")
_stump(ps)
with contextlib.redirect_stdout(io.StringIO()):
    P.pc_remove_pack(ps, row, True)
check('a tampered pack record, on a computer Stump: only its own places go, everything else stays',
      _stump_ok(ps) == ([], []) and not (ps / "tavern").exists(), _stump_ok(ps))
brd = _jail / "board"
(brd / "tavern").mkdir(parents=True); (brd / "tavern" / "install.mpy").write_text("x")
(brd / "lib").mkdir(); (brd / "lib" / "install.py").write_text("x")
_stump(brd)
def _brd_sd(cmd, timeout=None):
    if cmd[3:4] != ["exec"]:
        return True, ""
    here_ = os.getcwd(); os.chdir(brd)
    sys.modules["fserv"] = type(sys)("fserv"); sys.modules["fserv"].mount_sd = lambda: None
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(cmd[-1].replace("'/sd/'", repr(str(brd / "sd") + "/")), {})
    finally:
        os.chdir(here_); sys.modules.pop("fserv", None)
    return True, buf.getvalue()
P.run = _brd_sd
with contextlib.redirect_stdout(io.StringIO()):
    P.board_remove_pack("/dev/ttyUSB0", dict(row, plugins=["tavern", "lib"]), True)
P.run = real_run
check('... and on a board (a core folder given an install.py not taken for a plugin either)',
      _stump_ok(brd) == ([], []) and not (brd / "tavern").exists() and (brd / "lib" / "install.py").is_file(), _stump_ok(brd))
shutil.rmtree(_jail, ignore_errors=True)
# The identity keepsake (1.0.0-beta.76): a Stump's identity in one file,
# to bring the same Stump back on a fresh install or another board.
home = Path(tempfile.mkdtemp(prefix="home-")); old_home = os.environ.get("HOME"); os.environ["HOME"] = str(home)
kf = P.Path(tempfile.mkdtemp(prefix="stump-id-"))
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_copy_firmware(P.STUMP_APP_DIR, kf)
(kf / "config.py").write_text("NODE_NAME = \"Bob's Glitch\"\n")
first = P.pc_new_identity(kf)
check('a new computer Stump: its identity made before it starts (64 bytes), kept the next time',
      len(first["rns/identity"]) == 64 and P.pc_new_identity(kf) == first)
(kf / "pn_mailbox_identity").write_bytes(b"m" * 64)
files = P.pc_identity(kf)
p1 = P.save_keepsake(files, P._folder_name(kf))
d1 = json.loads(p1.read_text())
check('saved: one file named after the Stump, in the home folder, readable by its owner only',
      p1 == home / "Bob's Glitch.stump-identity" and (os.name == "nt" or (p1.stat().st_mode & 0o077) == 0)
      and d1["name"] == "Bob's Glitch" and "keep it safe" in d1["note"].lower(), (p1, oct(p1.stat().st_mode)))
check('... the identity only: its two keys and its mailbox, no setting nor password',
      sorted(d1["files"]) == ["pn_mailbox_identity", "rns/identity"] and "config" not in p1.read_text().lower().replace("config.py", ""))
got, nm = P.read_identity_source(p1)
check('... read back whole', got == files and nm == "Bob's Glitch")
got2, nm2 = P.read_identity_source(kf)
check('... and an old Stump folder reads the same way', got2 == files and nm2 == "Bob's Glitch")
for bad, why in (('{"a":1}', "isn't a saved"), (json.dumps(dict(d1, files={"rns/identity": "AAAA"})), "isn't whole"), ("not json", "isn't a saved")):
    (home / "bad.stump-identity").write_text(bad)
    try:
        P.read_identity_source(home / "bad.stump-identity"); err = ""
    except ValueError as e:
        err = str(e)
    check('... a file that isn\'t one, or damaged: said (%s)' % why, why in err, err)
# Brought back onto another computer Stump (a fresh install): its own
# identity saved first, the mailbox with it, the Stump restarted.
fresh = P.Path(tempfile.mkdtemp(prefix="stump-fresh-"))
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_copy_firmware(P.STUMP_APP_DIR, fresh)
(fresh / "config.py").write_text("NODE_NAME = 'New one'\n")
P.pc_new_identity(fresh)
yn = P.ask_yes_no
P.ask_yes_no = lambda q, d=True: True
with contextlib.redirect_stdout(io.StringIO()) as out:
    ok = P.restore_identity(str(fresh), str(p1))
P.ask_yes_no = yn
saved_first = [x for x in home.glob("New one (replaced*")]
check('--restore-identity onto a computer Stump: it is that Stump again, its mailbox too',
      ok and P.pc_identity(fresh) == files and (fresh / ".updated").is_file(), out.getvalue())
check('... the identity it had is saved first, and said', len(saved_first) == 1 and "saved first" in out.getvalue(),
      (list(home.iterdir()), out.getvalue()))
check('... it says the old one must not run any more', "must not run any more" in out.getvalue())
only = home / "only.stump-identity"; only.write_bytes(P.keepsake_bytes({"rns/identity": files["rns/identity"]}, "x"))
with contextlib.redirect_stdout(io.StringIO()):
    P.ask_yes_no = lambda q, d=True: True
    P.restore_identity(str(fresh), str(only))
    P.ask_yes_no = yn
check('... a keepsake without a mailbox: the mailbox left from before goes (it was another Stump\'s)',
      not (fresh / "pn_mailbox_identity").exists())
# Asked in a first setup: a path that's wrong is said and asked again; Enter: a new Stump.
said = []
real_ask = P.ask
it = iter([str(home / "nope"), str(p1)])
P.ask = lambda q, d=None: (said.append(q), next(it))[1]
with contextlib.redirect_stdout(io.StringIO()) as out:
    back = P.ask_restore_identity()
check('setup: a saved identity to bring back, asked; a wrong path said, then the file taken',
      back and back[0] == files and "No file or folder" in out.getvalue() and len(said) == 2, out.getvalue())
P.ask = lambda q, d=None: ""
with contextlib.redirect_stdout(io.StringIO()):
    check('... Enter: none, a new Stump', P.ask_restore_identity() is None)
answers_ = []
P.ask_yes_no = lambda q, d=True: (answers_.append((q, d)), True)[1]
P.ask = lambda q, d=None: d
with contextlib.redirect_stdout(io.StringIO()) as out:
    kept = P.offer_save_identity(files, "Bob's Glitch 2")
check('setup: saving it offered (yes by default at the keyboard), where asked, said with its fingerprint',
      kept == home / "Bob's Glitch 2.stump-identity" and answers_ and answers_[0][1] is True
      and P.identity_fingerprint(files) in out.getvalue(), out.getvalue())
P.ask, P.ask_yes_no = real_ask, yn
# On a board: read and written over mpremote (a pretend board here).
bd = Path(tempfile.mkdtemp(prefix="board-id-"))
(bd / "rns").mkdir(); (bd / "rns" / "identity").write_bytes(b"b" * 64); (bd / "config.py").write_text("NODE_NAME = 'Gearn'\n")
GARBLE = {"cp": False, "exec": False}
def _mp(cmd, timeout=None):
    a = cmd[3:]
    if a[:1] == ["exec"]:
        if GARBLE["exec"]:
            return True, "IDSTAT:{\"rns/ident"
        here_ = os.getcwd(); os.chdir(bd)
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(a[1], {})
        finally:
            os.chdir(here_)
        return True, buf.getvalue()
    if a[:2] == ["fs", "cp"]:
        src, dst = a[2], a[3]
        rd = lambda x: (bd / x[1:]) if x.startswith(":") else Path(x)
        try:
            data = rd(src).read_bytes()
        except OSError:
            return False, "no such file"
        if GARBLE["cp"] and src.startswith(":"):
            data = data[:40] + b"\x00" * 3 + data[43:]
        rd(dst).parent.mkdir(parents=True, exist_ok=True); rd(dst).write_bytes(data)
        return True, ""
    if a[:2] == ["fs", "cat"]:
        try:
            return True, (bd / a[2][1:]).read_text()
        except OSError:
            return False, ""
    if a[:2] == ["fs", "rm"]:
        try:
            (bd / a[2][1:]).unlink(); return True, ""
        except OSError:
            return False, ""
    return True, ""
real_run = P.run
P.run = _mp
bid = P.board_identity("/dev/ttyUSB0")
check('a board\'s identity read (its mailbox only if it has one), and its name', bid == {"rns/identity": b"b" * 64}
      and P.board_name("/dev/ttyUSB0") == "Gearn", bid)
P.ask_yes_no = lambda q, d=True: True
with contextlib.redirect_stdout(io.StringIO()) as out:
    ok = P.restore_identity("/dev/ttyUSB0", str(p1))
P.ask_yes_no = yn
P.run = real_run
check('--restore-identity onto a board: its identity and mailbox there, the one it had saved first',
      ok and (bd / "rns" / "identity").read_bytes() == files["rns/identity"] and (bd / "pn_mailbox_identity").read_bytes() == b"m" * 64
      and list(home.glob("Gearn (replaced*")), out.getvalue())
# A garbled read (1.0.0-beta.84: a CAM's serial): never taken as "no
# identity". The restore stops and changes nothing, and nothing new is saved.
(bd / "rns" / "identity").write_bytes(b"g" * 64); (bd / "pn_mailbox_identity").write_bytes(b"q" * 64)
other = home / "other.stump-identity"; other.write_bytes(P.keepsake_bytes({"rns/identity": b"o" * 64}, "Other"))
for mode in ("cp", "exec"):
    GARBLE[mode] = True
    before = sorted(home.iterdir())
    P.run = _mp
    P.ask_yes_no = lambda q, d=True: True
    with contextlib.redirect_stdout(io.StringIO()) as out:
        try:
            P.board_identity("/dev/ttyUSB0"); raised = False
        except P.IdentityUnreadable:
            raised = True
        ok = P.restore_identity("/dev/ttyUSB0", str(other))
    P.run, P.ask_yes_no = real_run, yn
    GARBLE[mode] = False
    check('a garbled read of a board\'s identity (%s): not taken as none; the restore stops, nothing changed nor saved' %
          {"cp": "the file", "exec": "the board's answer"}[mode],
          raised and not ok and (bd / "rns" / "identity").read_bytes() == b"g" * 64 and (bd / "pn_mailbox_identity").read_bytes() == b"q" * 64
          and sorted(home.iterdir()) == before and "nothing changed" in out.getvalue(), out.getvalue())
# Only the mailbox key differs: it is saved first too.
same_id = home / "same-id.stump-identity"
same_id.write_bytes(P.keepsake_bytes({"rns/identity": b"g" * 64, "pn_mailbox_identity": b"n" * 64}, "Gearn"))
for f_ in home.glob("Gearn (replaced*"):
    f_.unlink()
P.run = _mp
P.ask_yes_no = lambda q, d=True: True
with contextlib.redirect_stdout(io.StringIO()) as out:
    ok = P.restore_identity("/dev/ttyUSB0", str(same_id))
P.run, P.ask_yes_no = real_run, yn
saved = list(home.glob("Gearn (replaced*"))
check('... the same identity, another mailbox key: the mailbox it had is saved first, then replaced',
      ok and len(saved) == 1 and P.read_identity_source(saved[0])[0].get("pn_mailbox_identity") == b"q" * 64
      and (bd / "pn_mailbox_identity").read_bytes() == b"n" * 64, out.getvalue())
# A computer Stump whose identity file is damaged: the same.
dmg = Path(tempfile.mkdtemp(prefix="stump-dmg-"))
with contextlib.redirect_stdout(io.StringIO()):
    P._pc_copy_firmware(P.STUMP_APP_DIR, dmg)
(dmg / "rns").mkdir(exist_ok=True); (dmg / "rns" / "identity").write_bytes(b"d" * 30)
P.ask_yes_no = lambda q, d=True: True
with contextlib.redirect_stdout(io.StringIO()) as out:
    ok = P.restore_identity(str(dmg), str(other))
    made = P.pc_new_identity(dmg)
P.ask_yes_no = yn
check('... a computer Stump\'s damaged identity file: the restore stops, and no new one is made over it',
      not ok and made is None and (dmg / "rns" / "identity").read_bytes() == b"d" * 30, out.getvalue())
shutil.rmtree(dmg, ignore_errors=True)
if old_home is None:
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = old_home
for d_ in (home, kf, fresh, bd):
    shutil.rmtree(d_, ignore_errors=True)
# Choices in prompts, bold (1.0.0-beta.77): the key and what it does, on a
# terminal; plain text elsewhere; an answers file keys a question by its
# words, marks or not.
q = "Change something? %s: %s · %s: %s" % (P._k("s"), P._k("the storage"), P._k("Enter"), P._k("nothing"))
check('a choice in a prompt: marked, the words the same', P._plain(q) == "Change something? s: the storage · Enter: nothing")
check('... an answers file keys it as before', P._qkey(q) == "change something" and P._qkey(P._k("Keep these defaults") + "?") == "keep these defaults")
real_tty = P._tty
P._tty = lambda: True
check('... on a terminal: the key and its meaning bold', P._shown(q) == "Change something? \033[1ms\033[0m: \033[1mthe storage\033[0m · \033[1mEnter\033[0m: \033[1mnothing\033[0m")
P._tty = lambda: False
check('... piped or in a log: plain', P._shown(q) == P._plain(q))
P._tty = real_tty
check('... a numbered option: its number and its name', P._plain(P._option(2, "Amber -- warm dark")) == "2) Amber -- warm dark"
      and P._option(2, "Amber -- warm dark").startswith(P._k("2)") + " " + P._k("Amber")))
typed = []
builtins.input = lambda pr="": (typed.append(pr), "")[1]
with contextlib.redirect_stdout(io.StringIO()):
    P.ask_yes_no("Go on?", True)
check('... yes or no: [Y/n] bold, and no mark left in what the terminal gets', typed and P._B0 not in typed[0] and "Y/n" in typed[0], typed)
builtins.input = lambda *a: (_ for _ in ()).throw(AssertionError("asked the keyboard"))
src_ = P.Path(P.__file__).read_text()
check('... every "Enter to/Enter:" a prompt offers is marked', not re.search(r'(ask|_in|input)\((f?)"[^"\n]*\bEnter( to|:) ', src_),
      re.findall(r'(?:ask|_in|input)\(f?"[^"\n]*\bEnter(?: to|:) [^"\n]*"', src_)[:5])
# The setup: packs are a step of their own, before "Everything else"
# (which no longer has them); a pack's bot and look are offered there.
src = P.Path(P.__file__).read_text()
check('setup: Packs is its own step, before Everything else',
      '("Packs", sec_packs), ("Everything else"' in src and "Answer n to go through them now: the bot" in src)
shutil.rmtree(tmp, ignore_errors=True)
# The board step, a Stump board on a USB bridge (the WROVER: one USB port,
# a CH340): the chip is asked, not the USB ids, and a WROVER goes through
_sv = (P.scan_boards, P.detect_chip, P.ask_yes_no)
P.scan_boards = lambda: [{"device": "/dev/cu.usbserial-1120", "description": "USB Serial",
                          "guess": P._guess_board(None, None, "/dev/cu.usbserial-1120", "")}]
asked = []
P.ask_yes_no = lambda q, d=False: asked.append(q) or False
for chip, want in (("esp32", "/dev/cu.usbserial-1120"), ("esp32s3", None), (None, "/dev/cu.usbserial-1120")):
    P.detect_chip = lambda port, c=chip: c
    answers("1")
    with contextlib.redirect_stdout(io.StringIO()) as said:
        got = P.choose_board("cam")
    check('board step, a bridge port, the chip says %s: %s' % (chip, want), got == want
          and "doesn't look like a CAM" not in said.getvalue()
          and (chip != "esp32" or "WROVER" in said.getvalue())
          and (chip != "esp32s3" or "Heltec" in said.getvalue()), said.getvalue()[-400:])
check('... only the S3 behind a bridge asks before going on', len(asked) == 1, asked)
check('a CH340 is a WROVER or a Heltec', "WROVER" in P.KNOWN_BOARDS[("1a86", "7523")]
      and "Heltec" in P.KNOWN_BOARDS[("1a86", "7523")])
P.scan_boards, P.detect_chip, P.ask_yes_no = _sv
# One image found nearby: Enter takes it (beta.56: Enter skipped it)
_rd = Path(tempfile.mkdtemp())
(_rd / "ESP32_GENERIC-SPIRAM-20260406-v1.28.0.bin").write_bytes(b"x" * 64)
_sv2 = (P._remembered_path, P.ask_yes_no)
P._remembered_path = lambda k: None
P.ask_yes_no = lambda *a, **k: False
_find = lambda roots: P._find_cam_firmware_candidates(roots, markers=("esp32_generic-spiram",))
answers("")
with contextlib.redirect_stdout(io.StringIO()):
    got = P.resolve_path(None, "image", remember_key="x", is_dir=False, search_roots=[_rd], hint_finder=_find)
check('firmware: the one image found is used on Enter', got is not None and got.name.startswith("ESP32_GENERIC-SPIRAM"), got)
answers("s")
with contextlib.redirect_stdout(io.StringIO()):
    got = P.resolve_path(None, "image", remember_key="x", is_dir=False, search_roots=[_rd], hint_finder=_find)
check("firmware: 's' still skips", got is None, got)
P._remembered_path, P.ask_yes_no = _sv2
# No saved settings (beta.56): a flashed board gets fresh answers; an
# update chooses between fresh answers and keeping the board's own
_cw = P.config_wizard
P.config_wizard = lambda bt: ({"node_name": "asked"}, None)
seen = []
_ac = P.ask_choice
P.ask_choice = lambda q, opts: seen.append(opts) or opts[0]
with contextlib.redirect_stdout(io.StringIO()):
    prof = P._ask_profile("cam", erased=True)
check('settings: a flashed board gets fresh answers, nothing offered', prof == {"node_name": "asked"} and not seen, seen)
with contextlib.redirect_stdout(io.StringIO()):
    P._ask_profile("cam", erased=False)
check("settings: an update: fresh answers or keep the board's own, nothing saved",
      len(seen[0]) == 2 and "Keep" in seen[0][1] and not any("saved" in o for o in seen[0]), seen)
P.ask_choice, P.config_wizard = _ac, _cw
# The release on a board whose modules went compiled (no example_node.py:
# beta.56's first WROVER said "couldn't read the board's release")
_br = []
_rn = P.run
P.run = lambda cmd, **k: _br.append(cmd[-1]) or (True, "")
P.board_release("/dev/fake")
P.run = _rn
_bd = Path(tempfile.mkdtemp())
(_bd / "example_node.mpy").write_bytes(b"M\x06\x00\x1f192.168.4.1\x00STUMP_RELEASE\x00\x0d1.0.0-beta.56\x00ap 10.0.0.1")
_o = io.StringIO()
_cwd = os.getcwd()
os.chdir(_bd)
with contextlib.redirect_stdout(_o):
    exec(_br[0], {})
os.chdir(_cwd)
check('release: read from a compiled example_node (.mpy), not an IP', _o.getvalue().strip() == "STUMP_RELEASE:1.0.0-beta.56", _o.getvalue())
# The hotspot name shown is the one broadcast (the firmware's own rule)
check('hotspot preview: the default name, no address', P.preview_ssid(None, "192.168.4.1", False)[0] == "LaBuche-Stump.web.app")
check('hotspot preview: a name of its own and the address, as the board writes it',
      P.preview_ssid("Wrover", "192.168.5.1", True)[0] == "Wrover 192.168.5.1")
# The port held by another program (a `screen` left running): said, with who
_rs = P.subprocess.run
class _R:
    stdout = "p812\ncscreen\n"
P.subprocess.run = lambda *a, **k: _R()
check('port busy: who holds it', P.port_holder("/dev/cu.usbserial-120") == "screen (pid 812)")
with contextlib.redirect_stdout(io.StringIO()) as said:
    P.explain_port_busy("/dev/cu.usbserial-120")
check('... said, not a cable fault', "screen (pid 812)" in said.getvalue() and "Not a cable fault" in said.getvalue(), said.getvalue())
check('... esptool\'s "Resource busy" is known', P._port_busy("[Errno 16] Resource busy: '/dev/cu.usbserial-120'"))
P.subprocess.run = _rs
# A chain number never the one of the Stump it joins
check('chain: the number in a Stump hotspot\'s name', P._uplink_number("LaBuche1 192.168.5.1") == 5
      and P._uplink_number("LaBuche-Stump.web.app") is None and P._uplink_number("Souche 192.168.4.1") is None)
check('... mpremote\'s "failed to access" is a busy port', P._port_busy("mpremote: failed to access /dev/cu.x (it may be in use by another program)"))
# The checks stop the Stump (mpremote): it's started again after them
_calls = []
_rn = P.run
P.run = lambda cmd, **k: _calls.append(cmd) or (True, "SSID:LaBuche-Stump.web.app\nSTA:192.168.4.3 AP:192.168.6.1")
with contextlib.redirect_stdout(io.StringIO()):
    got = P.get_stump_ip("/dev/fake")
with contextlib.redirect_stdout(io.StringIO()) as said:
    P._print_discovered_addrs(got)
check('address lookup: a chain member\'s Wi-Fi said to be the Stump above\'s hotspot',
      "LaBuche-Stump.web.app" in said.getvalue() and "not your home" in said.getvalue(), said.getvalue())
check('address lookup: the Stump started again after it', got == {"sta": "192.168.4.3", "ap": "192.168.6.1", "ssid": "LaBuche-Stump.web.app"}
      and _calls[-1][-1] == "reset", _calls[-1:])
_calls.clear()
_sv3 = (P.sd_card_status, P.release_check)
P.sd_card_status = lambda port: (True, "OK:mounted", False)
P.release_check = lambda port, app_dir=None: (True, "same", "1.0.0-beta.56")
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.diagnostics("/dev/fake", "cam", interactive=False)
    except Exception as e:
        _calls.append(["error", repr(e)])
check('diagnostics: the Stump started again at the end', _calls and _calls[-1][-1] == "reset", _calls[-2:])
P.run = _rn
P.sd_card_status, P.release_check = _sv3
# A board in a chain over the home Wi-Fi: CHAIN_PARENT_IP written (and
# cleared for a hotspot member), the result valid Python
_cp = Path(tempfile.mkdtemp()) / "config.py"
_cp.write_text(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.py"), encoding="utf-8").read(), encoding="utf-8")
txt = P._generate_config_py(_cp, "LaBucheD", "Bob's Glitch", "pw", "192.168.6.200", 7633,
                            chain={"enabled": True, "uplink_ssid": "Bob's Glitch", "uplink_pass": "pw", "subnet": 6,
                                   "channel": 0, "secret": "s", "lan_parent_ip": "192.168.0.173"})
txt = txt if isinstance(txt, str) else _cp.read_text(encoding="utf-8")
ns = {}
exec(compile(txt, "config.py", "exec"), ns)
check('chain over the home Wi-Fi: CHAIN_PARENT_IP written', ns.get("CHAIN_PARENT_IP") == "192.168.0.173"
      and ns.get("CHAIN_UPLINK_SSID") == "Bob's Glitch" and ns.get("CHAIN_SUBNET") == 6, {k: ns.get(k) for k in ("CHAIN_PARENT_IP", "CHAIN_UPLINK_SSID")})
check('summary: a chain member\'s address named for the Stump above\'s Wi-Fi',
      P._sta_note({"sta": "192.168.4.3", "ssid": "LaBuche-Stump.web.app"}) == ("Via the chain", "http://192.168.4.3/ on LaBuche-Stump.web.app")
      and P._sta_note({"sta": "192.168.0.179", "ssid": "Bob's Glitch"})[0] == "On the Wi-Fi")
# Its hotspot not up yet ("AP:" and nothing): no crash (beta.56)
_rn = P.run
P.run = lambda cmd, **k: (True, "SSID:Bob's Glitch\nSTA:192.168.0.120 AP:")
with contextlib.redirect_stdout(io.StringIO()):
    got = P.get_stump_ip("/dev/fake", retries=1)
P.run = _rn
check('address lookup: no hotspot yet, no crash', got == {"sta": "192.168.0.120", "ap": None, "ssid": "Bob's Glitch"}, got)
# The last check: the board's page from this computer at its router
# address, and its Heltec connected (beta.56: "certified" with an address
# that worked from nowhere this computer was)
PROF = {"node_name": "LaBucheD", "heltec_enabled": True}
def _get(pages):
    def get(url, timeout=4):
        v = pages.get(url)
        if isinstance(v, Exception) or v is None:
            raise v or OSError("timed out")
        return v
    return get
nop = lambda s: None
with contextlib.redirect_stdout(io.StringIO()):
    r1 = P.network_check("/dev/x", PROF, {"sta": "192.168.4.3", "ssid": "LaBuche-Stump.web.app"}, get=_get({}), sleep=nop, mine="192.168.0.50")
    r2 = P.network_check("/dev/x", PROF, {"sta": "192.168.0.120", "ssid": "Bob's Glitch"}, get=_get({}), sleep=nop, mine="10.0.0.5")
    ok_pages = {"http://192.168.0.120/node.json": {"name": "LaBucheD"}, "http://192.168.0.120/hw.json": {"state": "online"}}
    r3 = P.network_check("/dev/x", PROF, {"sta": "192.168.0.120"}, get=_get(ok_pages), sleep=nop, mine="192.168.0.50")
    r4 = P.network_check("/dev/x", PROF, {"sta": "192.168.0.120"}, sleep=nop, mine="192.168.0.50", wait_s=0.2,
                         get=_get({"http://192.168.0.120/node.json": {"name": "LaBucheD"},
                                   "http://192.168.0.120/hw.json": {"state": "connecting", "radios": [{"error": "no answer from 192.168.0.222:7633 in 3 s"}]}}))
    r5 = P.network_check("/dev/x", PROF, {"sta": "192.168.0.120"}, sleep=nop, mine="192.168.0.50",
                         get=_get({"http://192.168.0.120/node.json": {"name": "LaBuche"}}))
    r6 = P.network_check("/dev/x", PROF, {"sta": "192.168.0.120"}, sleep=nop, mine="192.168.0.50", wait_s=0.2, get=_get({}))
check("reach: joined another Stump's hotspot, not the router: not a pass, and said", not r1[0] and "hotspot" in r1[1] and "router" in r1[1], r1)
check("reach: this computer on another network: not a pass, and said", not r2[0] and "isn't on the board's network" in r2[1], r2)
check("reach: page from here and the Heltec connected: a pass, with the address to open",
      r3 == (True, "its page answers at http://192.168.0.120/, and its Heltec is connected", "http://192.168.0.120/"), r3)
check("reach: the Heltec not connected: not a pass, with its error", not r4[0] and "Heltec isn't connected" in r4[1] and "7633" in r4[1], r4)
check("reach: another Stump at that address: not a pass", not r5[0] and "another Stump" in r5[1], r5)
check("reach: no answer: not a pass", not r6[0] and "doesn't answer" in r6[1], r6)
# A Stump image (image/README.md): the board says what it froze in, the
# release comes from it, the upload skips those modules and clears stale
# copies, and the partition tag the Provisioner carries is the table's
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "image"))
import stage as _stage
check('image: the Provisioner\'s partition tag is image/partitions-stump.csv\'s', P.STUMP_IMAGE_PARTITIONS == _stage.partitions_tag(),
      (P.STUMP_IMAGE_PARTITIONS, _stage.partitions_tag()))
_fz = _stage.frozen_modules()
check('image: what\'s frozen is the Stump\'s modules, not config.py or main.py', "barkeep.py" in _fz and "urns/lxmf.py" in _fz
      and "config.py" not in _fz and "main.py" not in _fz and not any(f.startswith(("fservbot/", "stumpid/")) for f in _fz), _fz[:5])
_rn = P.run
_sent = []
P.run = lambda cmd, **k: _sent.append(cmd[-1]) or (True, 'IMAGE:{"release": "1.0.0-beta.56", "partitions": "%s", "frozen": ["barkeep.py", "urns/lxmf.py"]}' % P.STUMP_IMAGE_PARTITIONS)
img = P.board_image("/dev/fake")
check('image: the board\'s image read', img and img["release"] == "1.0.0-beta.56" and img["frozen"] == ["barkeep.py", "urns/lxmf.py"], img)
P.run = lambda cmd, **k: (True, "IMAGE:none")
check('image: a board without one', P.board_image("/dev/fake") is None)
P.run = lambda cmd, **k: (True, "STUMP_RELEASE:1.0.0-beta.56") if "stump_image" in cmd[-1] else (True, "")
check('image: the release read from it', P.board_release("/dev/fake") == "1.0.0-beta.56")
_sent.clear()
P.run = lambda cmd, **k: _sent.append(cmd[-1]) or (True, "REMOVED:3")
with contextlib.redirect_stdout(io.StringIO()):
    n = P._remove_stale_frozen("/dev/fake", ["barkeep.py", "urns/lxmf.py", "urns/interfaces/udp.py"])
check('image: stale copies and empty package folders removed, deepest first', n == 3 and "'urns/interfaces'" in _sent[0]
      and _sent[0].index("'urns/interfaces'") < _sent[0].index("'urns'"), _sent[0][-200:])
P.run = _rn
# The firmware question: in place on a board with this release's layout; erase otherwise
_bi, _pb, _ac = P.board_image, P.pick_board_firmware, P.ask_choice
P.pick_board_firmware = lambda port, path=None: ("esp32", Path("/x/stump-1.0.0-beta.56-wrover.bin"))
seen = []
P.ask_choice = lambda q, opts: seen.append(opts) or opts[0]
P.board_image = lambda port: {"release": "1.0.0-beta.55", "partitions": P.STUMP_IMAGE_PARTITIONS, "frozen": []}
with contextlib.redirect_stdout(io.StringIO()):
    got = P.ask_flash("/dev/fake")
check('image: same layout: update in place offered first, settings kept', got[0] and got[1] and len(seen[0]) == 3 and seen[0][0].startswith("Update"), (got, seen))
seen.clear()
P.board_image = lambda port: {"release": "1.0.0-beta.55", "partitions": "stump-old", "frozen": []}
with contextlib.redirect_stdout(io.StringIO()):
    got = P.ask_flash("/dev/fake")
check('image: another layout: erase, no in-place update', got[0] and not got[1] and len(seen[0]) == 2 and seen[0][0].startswith("Put"), (got, seen))
seen.clear()
P.board_image = lambda port: None
P.ask_choice = lambda q, opts: seen.append(opts) or opts[-1]
with contextlib.redirect_stdout(io.StringIO()):
    got = P.ask_flash("/dev/fake")
check('image: nothing flashed when asked', got == (False, False, None, None), got)
P.board_image, P.pick_board_firmware, P.ask_choice = _bi, _pb, _ac
check('image: the release URL and markers', P.stump_image_url("esp32", "1.0.0-beta.56").endswith("/v1.0.0-beta.56/stump-1.0.0-beta.56-wrover.bin")
      and P.stump_image_markers("esp32s3", "1.0.0-beta.56") == ("stump-1.0.0-beta.56-s3cam",))
print('FAILURES', FAIL)
