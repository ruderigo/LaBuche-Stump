# Stump on a computer (docs/PC.md), end to end:
#   1. a Stump folder made with the Provisioner's own --pc functions
#      (firmware, runtime, packs, Tools page, config with the PC block);
#   2. stump_pc.py started from it, as the first Stump of a chain;
#   3. a CAM -- the real MicroPython chain and LXMF code -- joins it with
#      the chain secret and sends a chat line;
#   4. checked over HTTP: pages, the Tools page, the chain, the line in #wifi-mesh.
# Needs: the cryptography package, and MICROPYTHON (a MicroPython unix port).
# Run: MICROPYTHON=micropython python3 tests/pc/pc_chain_test.py
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""            # the Provisioner never asks here
import provisioner as P

PORT = 8093
# With a MicroPython to play the CAM, the chain runs on 127.0.4.1 / 127.0.5.2;
# without one (Windows, macOS in CI), only the computer half runs, on
# 127.0.0.1 (macOS answers on no other loopback address).
MP = os.environ.get("MICROPYTHON") or "micropython"
HAVE_MP = shutil.which(MP) is not None or os.path.isfile(MP)
IP = "127.0.4.1" if HAVE_MP else "127.0.0.1"
SECRET = "pc-chain-secret"
FAIL = []


def _crash(kind, value, tb):
    import traceback
    text = "".join(traceback.format_exception(kind, value, tb))
    print(text)
    annotate("crashed: " + text[-2500:])
    sys.exit(1)

sys.excepthook = _crash


def annotate(text):
    """On GitHub Actions, a failure shows as an annotation (readable without the log)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:300]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


def get(path, data=None):
    req = urllib.request.Request("http://%s:%d%s" % (IP, PORT, path), data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


work = tempfile.mkdtemp(prefix="stump-pc-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
fw = P.STUMP_APP_DIR
n = P._pc_copy_firmware(fw, folder)
check("firmware copied", n > 50 and os.path.exists(os.path.join(folder, "example_node.py"))
      and os.path.exists(os.path.join(folder, "pc_compat", "stump_host.py")), n)
check("no ESP32 modules on the computer", not any(f.endswith(".mpy") for _, _, fs in os.walk(folder) for f in fs))
packs = [p for p in P.discover_packs() if p["spec"]["name"] in ("tavern", "rnfs")]
P._pc_install_packs(folder, packs)
check("packs installed", os.path.exists(os.path.join(folder, "tavern", "install.py"))
      and os.path.exists(os.path.join(folder, "sd", "tools", "rnfs", "README.fr.md")))
P._pc_install_tools(fw, folder)
check("Tools page sections", all(os.path.exists(os.path.join(folder, "sd", "tools", d, "README.md"))
                                 for d in ("provisioner", "flasher", "firefly")))
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "Test hotspot", "pc_heltec": "none",
                           "chain": {"enabled": True}})
cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = True\nCHAIN_UPLINK_SSID = ''\nCHAIN_SECRET = %r\n" % (
    PORT, IP, SECRET)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
again = P._pc_copy_firmware(fw, folder)
check("an update changes nothing, and keeps config.py", again == 0
      and "Test hotspot" in open(os.path.join(folder, "config.py"), encoding="utf-8").read(), again)

node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
lxmf = None
t0 = time.time()
while time.time() - t0 < 60:
    try:
        lxmf = json.loads(get("/node.json")[1])["lxmf"]
        break
    except Exception:
        time.sleep(1)
check("the computer Stump starts and serves /node.json", bool(lxmf))
early_log = None
if not lxmf:
    node.kill()
    early_log = node.communicate()[0]
    annotate("computer Stump log: " + early_log[-3000:])
if lxmf:
    for path in ("/", "/tools", "/rrc", "/admin", "/p/tavern/", "/p/rnfs/"):
        s, body = get(path)
        check("page " + path, s == 200 and len(body) > 1000, (s, len(body)))
    s, body = get("/tools")
    check("Tools page lists the sections", "Provisioner" in body and "rnfs" in body)
    chain = json.loads(get("/chain.json")[1])
    check("first Stump of a chain, on its hotspot", chain["role"] == "root" and chain["ap_ip"] == IP
          and chain["secret"], chain)
    about = get("/about")[1]
    check("About names the hotspot visitors join", "Test hotspot" in about)

if lxmf and not HAVE_MP:
    print("SKIP the CAM half (no MicroPython here)")
if lxmf and HAVE_MP:
    mp = MP
    camwork = os.path.join(work, "cam")
    os.makedirs(os.path.join(camwork, "cam"))
    cam = subprocess.Popen([mp, os.path.join(ROOT, "tests", "mp", "pc_chain_cam.py"), "x", camwork, lxmf],
                           cwd=fw, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    arrived = False
    t0 = time.time()
    get("/rrc/send", b"/join wifi-mesh")
    while time.time() - t0 < 90 and not arrived:
        time.sleep(3)
        poll = json.loads(get("/rrc/poll?room=wifi-mesh&since=0")[1])
        arrived = any(m["body"] == "hello from the CAM" and m["nick"] == "camtest" for m in poll["messages"])
    check("a CAM joins it over the chain, and its chat line arrives in #wifi-mesh (not #lora-mesh)", arrived)
    below = json.loads(get("/chain.json")[1])["below"]
    check("the CAM is listed below the computer", "127.0.5.2" in below, below)
    cam.kill()
    out = cam.communicate()[0]
    check("the CAM's hello was answered", "uplink answered" in out, out[-500:])

if early_log is not None:
    log = early_log
else:
    node.terminate()
    try:
        log = node.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        node.kill()
        log = node.communicate()[0]
check("no tracebacks in the computer Stump's log", "Traceback" not in log, log[log.find("Traceback"):][:1500])
shutil.rmtree(work, ignore_errors=True)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
