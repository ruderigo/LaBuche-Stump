import sys, json, re, hashlib, shutil, zipfile, builtins
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
check('api', P.firmware_plugin_api(fw) == 1)
check('core manifest current', P._check_local_files_current(fw.resolve()) == [], P._check_local_files_current(fw.resolve()))
for n in ('fservbot', 'stumpid'):
    check(n + ' verifies', P.verify_plugin(fw / n) == ([], []), P.verify_plugin(fw / n))
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
# tamper -> warning
t = [p for p in packs if p['spec']['name'] == 'tavern'][0]
f = t['dir'] / 'tavern/install.py'; f.write_text(f.read_text() + '\nimport time\ntime.sleep(5)\n')
r, w = P.check_pack(t, 1)
check('tamper + lint noticed', any('changed since' in x for x in w) and any('time.sleep' in x for x in w), w)
P.describe_pack(t, 1)
# fake mpremote for install + upload
calls = []
BOARD_CONFIG = ['CONFIG:1 TRACES:1']
def fake_run(cmd, timeout=None, **kw):
    calls.append(cmd)
    if 'exec' in cmd and any('SHA:' in str(c) for c in cmd): return True, 'SHA:none'
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
# --skip-tools still brings the sections' READMEs up to date (sections on the card only)
calls.clear()
P.refresh_tool_readmes('/dev/fake')
rd = [c[-1] for c in calls if 'cp' in c]
check('READMEs refreshed, every language', ':/sd/tools/flasher/README.fr.md' in rd and ':/sd/tools/flasher/README.es.md' in rd
      and ':/sd/tools/rnfs/README.fr.md' in rd and ':/sd/tools/flasher/README.md' in rd, rd)
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
                                    "install_packs", "run", "get_stump_ip", "diagnostics", "keep_awake")}
P.check_tools = lambda: True
P.resolve_path = lambda *a, **k: fw
P.choose_board = lambda role=None: "/dev/fake-cam"
def fake_wizard(role):
    calls.append("wizard"); return {"node_name": "t", "wifi_ssid": "", "packs": []}, None
P.config_wizard = fake_wizard
def flash(port): phase[0] = "auto"; calls.append("flash"); return True, port
P.flash_cam_micropython = flash
P.upload_stump_app = lambda port, app_dir=None: calls.append("upload") or True
P.push_config_to_board = lambda port, role, prof: calls.append("push") or True
P.install_packs = lambda *a: True
P.run = lambda *a, **k: None
P.get_stump_ip = lambda port: {"sta": "192.168.0.161", "ap": "192.168.4.1"}
P.diagnostics = lambda port, role=None, interactive=True: calls.append("diag") or {"serial": True}
P.keep_awake = lambda: True
real_sleep = P.time.sleep
P.time.sleep = lambda s: None
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.interactive_wizard()
P.time.sleep = real_sleep
for n, f in saved.items():
    setattr(P, n, f)
sys.stdout = sys.__stdout__
out = buf.getvalue()
check('CAM route: the questions come before anything touches the board', calls == ["wizard", "flash", "upload", "push", "diag"], calls)
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
check("... can't tell: kept (unknown)", P._board_config_state("/dev/x") == "unknown" and P._board_has_config("/dev/x"))
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
print('FAILURES', FAIL)
