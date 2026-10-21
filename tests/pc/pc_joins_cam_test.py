# A Stump on a computer JOINING a CAM's chain (docs/PC.md, "The computer
# joining a CAM"), and the Provisioner's chain checks (--check-chain):
#   1. each check that can fail says what's wrong (no network needed);
#   2. a CAM -- the real MicroPython chain and LXMF code -- is the first
#      Stump; the Provisioner's hello is let in with the right secret and
#      not with a wrong one;
#   3. the computer Stump starts as a member; the live check sees the CAM
#      answer it; chat lines cross both ways.
# Needs: the cryptography package, and MICROPYTHON (a MicroPython unix
# port); without MicroPython only part 1 runs.
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

MP = os.environ.get("MICROPYTHON") or "micropython"
HAVE_MP = shutil.which(MP) is not None or os.path.isfile(MP)
CAM, PC, PORT, SECRET = "127.0.4.1", "127.0.5.2", 8095, "pc-chain-secret"
CAM_LAN = "127.0.6.1"              # the CAM on the home Wi-Fi the computer stays on
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def _crash(kind, value, tb):
    import traceback
    text = "".join(traceback.format_exception(kind, value, tb))
    print(text)
    annotate("crashed: " + text[-2500:])
    sys.exit(1)

sys.excepthook = _crash


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:400]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


def fake_get(table):
    def get(url, timeout=4):
        for k, v in table.items():
            if url.endswith(k):
                if isinstance(v, Exception):
                    raise v
                return v
        raise OSError("connection refused")
    return get


def last(checks):
    return checks[-1]


# 1. what each failure says
ME = "127.0.4.9"                   # the computer, on the CAM's network
on = lambda ip: ME
r = P.chain_preflight(CAM, SECRET, "LaBuche-Stump.web.app", route=lambda ip: "192.168.0.161")
check("not on the CAM's network: says to join its Wi-Fi", not last(r)[0] and "Join 'LaBuche-Stump.web.app'" in last(r)[2], r)
r = P.chain_preflight(CAM, SECRET, route=on, get=fake_get({}))
check("nothing answers: says to check the Stump and its address", not last(r)[0] and "Nothing answers" in last(r)[2], r)
r = P.chain_preflight("192.168.4.3", SECRET, route=lambda ip: "192.168.4.3")
check("its own address as the parent: says so, names the Router", not last(r)[0] and "own address" in last(r)[2]
      and "192.168.4.1" in last(r)[2], r)
import urllib.error
noroute = urllib.error.URLError(OSError(65, "No route to host"))
real_platform = P.sys.platform
P.sys.platform = "darwin"
r = P.chain_preflight("192.168.0.161", SECRET, route=lambda ip: "192.168.0.172",
                      get=fake_get({"/chain.json": noroute, "/node.json": noroute}), wait=0)
P.sys.platform = real_platform
check("macOS blocking the local network: says where to allow it", not last(r)[0] and "Local Network" in last(r)[2]
      and "Python" in last(r)[2], r)
import socket
r = P.chain_preflight("192.168.0.161", SECRET, route=lambda ip: "192.168.0.172",
                      get=fake_get({"/chain.json": urllib.error.URLError(socket.timeout("timed out")),
                                    "/node.json": urllib.error.URLError(socket.timeout("timed out"))}), wait=0)
check("no answer on a shared Wi-Fi: its address may have changed, --get-ip", not last(r)[0] and "--get-ip" in last(r)[2], r)
# A Stump that got a new address on the home Wi-Fi: found, and followed
import contextlib, io, socket
def lan(table):
    def get(url, timeout=4):
        ip = url.split("/")[2]
        if ip in table:
            return table[ip]
        raise urllib.error.URLError(socket.timeout("timed out"))
    return get
moved_cam = {"name": "LaBuche", "chain": {"enabled": True, "role": "root", "lan_ip": "192.168.0.175"}}
other = {"name": "Phone", "chain": None}
found = P.find_stumps("192.168.0.", get=lan({"192.168.0.175": moved_cam, "192.168.0.20": other}))
check("find_stumps: only Stumps taking chain links there", [ip for ip, _ in found] == ["192.168.0.175"], found)
folder0 = tempfile.mkdtemp()
open(os.path.join(folder0, "config.py"), "w").write("PC_CHAIN_PARENT_IP = '192.168.0.161'\n")
P.builtins_input_saved = builtins.input
builtins.input = lambda *a: ""
fail = [(True, "This computer is on 192.168.0.161's network", ""), (False, "A Stump answers at http://192.168.0.161/", "x")]
with contextlib.redirect_stdout(io.StringIO()) as said:
    new = P._follow_moved_parent(folder0, "192.168.0.161", fail, find=lambda prefix, skip=(): found)
cfg0 = {}
exec(open(os.path.join(folder0, "config.py")).read(), cfg0)
check("a moved Stump is followed: config.py gets its new address", new == "192.168.0.175"
      and cfg0["PC_CHAIN_PARENT_IP"] == "192.168.0.175" and "LaBuche" in said.getvalue(), said.getvalue())
check("... not for a Stump's own Wi-Fi address", P._follow_moved_parent(folder0, "192.168.4.1", fail) is None)
builtins.input = P.builtins_input_saved
refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
r = P.chain_preflight(CAM, SECRET, route=on, get=fake_get({"/chain.json": refused, "/node.json": refused}), wait=0)
check("refused: its Wi-Fi is up, its program stopped, power-cycle", not last(r)[0] and "web server refused" in last(r)[2]
      and ME in last(r)[2] and "power-cycle" in last(r)[2], r)
tries = []
def starting(url, timeout=4):
    tries.append(url)
    if len(tries) < 3:
        raise refused
    return {"enabled": True, "role": "root", "secret": True} if url.endswith("/chain.json") else {"name": "LaBuche", "version": "Beta A"}
real_sleep = P.time.sleep
P.time.sleep = lambda s: None
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()) as said:
    r = P.chain_preflight(CAM, SECRET, route=on, get=starting, hello=lambda *a: True, wait=30)
P.time.sleep = real_sleep
check("refused while starting: waits, then goes on", all(ok for ok, _, _ in r) and "trying again" in said.getvalue()
      and "LaBuche" in r[1][1], (r, said.getvalue()))
import urllib.request
check("no proxy between this computer and a Stump", "ProxyHandler({})" in open(P.__file__, encoding="utf-8").read())
r = P.chain_preflight(CAM, SECRET, route=on, get=fake_get({"/node.json": {"name": "Old", "version": "0.9"}}))
check("old firmware: says to update it", not last(r)[0] and "--upload-app" in last(r)[2], r)
r = P.chain_preflight(CAM, SECRET, route=on, get=fake_get({"/chain.json": {"enabled": False}}))
check("chain off on the CAM: says how to turn it on", not last(r)[0] and "CHAIN_ENABLED = True" in last(r)[2], r)
r = P.chain_preflight(CAM, "", route=on, get=fake_get({"/chain.json": {"enabled": True, "role": "root", "secret": True}}))
check("secret on the CAM only: says to enter it", not last(r)[0] and "has a chain secret and this one has none" in last(r)[2], r)
r = P.chain_preflight(CAM, SECRET, route=on, get=fake_get({"/chain.json": {"enabled": True, "role": "root", "secret": False}}))
check("secret here only: says to give both the same", not last(r)[0] and "no chain secret and this one does" in last(r)[2], r)
r = P.chain_preflight(CAM, SECRET, route=on, hello=lambda *a: False,
                      get=fake_get({"/chain.json": {"enabled": True, "role": "root", "secret": True}}))
check("hello refused: says the secrets differ, or the firewall", not last(r)[0] and "wrong chain secret" in last(r)[2]
      and "firewall" in last(r)[2], r)
root_on_lan = {"enabled": True, "role": "root", "secret": True, "ap_ip": "192.168.4.1", "lan_ip": "192.168.0.161"}
r = P.chain_preflight("192.168.0.161", SECRET, "(this computer's Wi-Fi)", route=lambda ip: "192.168.0.172",
                      get=fake_get({"/chain.json": root_on_lan}), hello=lambda *a: True)
check("on the home Wi-Fi with the CAM: every check passes", all(ok for ok, _, _ in r), r)
old_cam = dict(root_on_lan, lan_ip=None)
r = P.chain_preflight("192.168.0.161", SECRET, route=lambda ip: "192.168.0.172",
                      get=fake_get({"/chain.json": old_cam}), hello=lambda *a: True)
check("a CAM that doesn't take links on the home Wi-Fi: says why, and the other way", not last(r)[0]
      and "own Wi-Fi only" in last(r)[2] and "192.168.4.1" in last(r)[2], r)
r = P.chain_preflight("192.168.0.161", SECRET, route=lambda ip: "192.168.4.3")
check("not on the CAM's network, home-Wi-Fi way: no made-up SSID", "(this" not in last(r)[2], r)
r = P.check_remote_chain("192.168.5.1", get=fake_get({"/chain.json": {"enabled": True, "role": "member",
                         "uplink": {"ssid": "Stump 4", "ip": None}}}))
check("a CAM member that hasn't joined: says what to check", not last(r)[0] and "CHAIN_UPLINK_SSID" in last(r)[2], r)
r = P.check_remote_chain("192.168.5.1", get=fake_get({"/chain.json": {"enabled": True, "role": "member",
                         "uplink": {"ssid": "Stump 4", "ip": "192.168.4.20", "parent": "192.168.4.1", "parent_heard_s": None}}}))
check("a CAM member not answered: says secrets / chain on", not last(r)[0] and "CHAIN_SECRET" in last(r)[2], r)
r = P.check_remote_chain("192.168.5.1", get=fake_get({"/chain.json": {"enabled": True, "role": "member",
                         "uplink": {"ssid": "Stump 4", "ip": "192.168.4.20", "parent": "192.168.4.1", "parent_heard_s": 5}}}))
check("a linked CAM member: all good", all(ok for ok, _, _ in r), r)

if not HAVE_MP:
    print("SKIP the CAM half (no MicroPython here)")
    print("FAILURES", FAIL)
    sys.exit(1 if FAIL else 0)

# 2. a real CAM as the first Stump; the Provisioner's hello
work = tempfile.mkdtemp(prefix="stump-join-")
fw = P.STUMP_APP_DIR
lxmf_file = os.path.join(work, "pc_lxmf")
os.makedirs(os.path.join(work, "cam", "cam"))
cam = subprocess.Popen([MP, os.path.join(ROOT, "tests", "mp", "pc_chain_cam_root.py"), "x", os.path.join(work, "cam"), lxmf_file],
                       cwd=fw, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(6)
check("a wrong secret isn't let in", P.chain_hello(CAM, "not-the-secret", ME) is False)
check("a wrong secret isn't let in on the home Wi-Fi either", P.chain_hello(CAM_LAN, "not-the-secret", "127.0.6.9") is False)
cam_chain = {"/chain.json": {"enabled": True, "role": "root", "secret": True, "ap_ip": CAM, "lan_ip": CAM_LAN}}
r = P.chain_preflight(CAM, SECRET, "CAM", route=on, get=fake_get(cam_chain))
check("the right secret is let in on its own Wi-Fi: every check passes", all(ok for ok, _, _ in r), r)
r = P.chain_preflight(CAM_LAN, SECRET, "(this computer's Wi-Fi)", route=lambda ip: "127.0.6.9", get=fake_get(cam_chain))
check("... and on the home Wi-Fi", all(ok for ok, _, _ in r), r)

# 3. the computer Stump joins it
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(fw, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "CAM", "pc_heltec": "none",
                           "chain": {"enabled": True, "uplink_ssid": "(this computer's Wi-Fi)", "parent_ip": CAM_LAN}})
cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = True\nCHAIN_UPLINK_SSID = 'CAM'\nCHAIN_SECRET = %r\n" % (
    PORT, PC, SECRET)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def get(path, data=None):
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


real_and_cam = lambda url, timeout=4: P._get_json(url, timeout) if "127.0.0.1" in url else {"below": [PC]}
r = P.chain_live(folder, CAM_LAN, wait=90, get=real_and_cam)
check("live check: running, a member, answered by the CAM, listed below", all(ok for ok, _, _ in r) and len(r) == 3, r)
lxmf = json.loads(get("/node.json"))["lxmf"]
open(lxmf_file, "w").write(lxmf)
get("/rrc/send", b"/join wifi-mesh")
arrived = False
t0 = time.time()
while time.time() - t0 < 90 and not arrived:
    time.sleep(3)
    arrived = any("hello from the root CAM" in m["body"] for m in json.loads(get("/rrc/poll?room=wifi-mesh&since=0"))["messages"])
check("the CAM's chat line reaches the computer's #wifi-mesh", arrived)
import re
names = " ".join(m["body"] for m in json.loads(get("/rrc/poll?room=wifi-mesh&since=0"))["messages"])
nick = (re.findall(r"~[\w-]+", names) or ["~camroot"])[-1]
reply = get("/rrc/send", ("/msg %s hello from the laptop" % nick).encode())
time.sleep(20)
cam.kill()
out = cam.communicate()[0]
check("the computer's /msg reaches the CAM", "hello from the laptop" in out, (reply, out[-800:]))
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
