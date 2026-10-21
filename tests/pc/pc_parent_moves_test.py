# A computer Stump follows the Stump it joins when that one's address on
# the shared Wi-Fi changes (a router's DHCP, after a restart): the first
# Stump stops, comes back at a new address with the same identity, and
# the second finds it on the network by its LXMF address and rejoins,
# without anyone running the Provisioner. Two computer Stumps on loopback
# (127.0.9.x); Linux only. Run: python3 tests/pc/pc_parent_moves_test.py
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

SECRET = "moves-secret"
FIRST, MOVED, SECOND = ("127.0.9.1", 8098, "Rootpc"), ("127.0.9.3", 8098, "Rootpc"), ("127.0.9.2", 8099, "Joinerpc")
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


if not sys.platform.startswith("linux"):
    print("SKIP (needs several loopback addresses: Linux)")
    sys.exit(0)

work = tempfile.mkdtemp(prefix="stump-moves-")
fw = P.STUMP_APP_DIR
procs = {}


def folder_of(name):
    return os.path.join(work, name)


def write_config(who, parent=None, extra=""):
    ip, port, name = who
    folder = folder_of(name)
    if not os.path.isdir(folder):
        os.makedirs(folder)
        P._pc_copy_firmware(fw, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "none",
                               "chain": {"enabled": True, "parent_ip": parent} if parent else {"enabled": True}})
    cfg += ("\nNODE_NAME = %r\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = True\n"
            "CHAIN_UPLINK_SSID = %r\nCHAIN_SECRET = %r\n" % (name, port, ip, "(home)" if parent else "", SECRET)) + extra
    open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)


def start(name):
    procs[name] = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder_of(name),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def stop(name):
    p = procs.pop(name)
    p.terminate()
    try:
        return p.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        return p.communicate()[0]


def get(who, path):
    ip, port, _ = who
    with urllib.request.urlopen("http://%s:%d%s" % (ip, port, path), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def wait(cond, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def uplink():
    return json.loads(get(SECOND, "/chain.json"))["uplink"]


write_config(FIRST)
write_config(SECOND, parent=FIRST[0], extra="CHAIN_QUIET_S = 20\nPC_CHAIN_PARENT_HTTP_PORT = %d\n" % FIRST[1])
start("Rootpc")
wait(lambda: get(FIRST, "/node.json"), 90)
lxmf = json.loads(get(FIRST, "/node.json"))["lxmf"]
start("Joinerpc")
linked = wait(lambda: uplink()["parent_heard_s"] is not None and uplink()["parent_heard_s"] < 30, 120)
check("the second Stump joins the first", linked, uplink() if not linked else "")
saved = os.path.join(folder_of("Joinerpc"), "chain_parent.json")
learnt = wait(lambda: json.load(open(saved)).get("lxmf") == lxmf, 60)
check("it remembers who the Stump above is (its LXMF address)", learnt,
      open(saved).read() if os.path.exists(saved) else "no chain_parent.json")

# The first Stump restarts and the router gives it another address.
log1 = stop("Rootpc")
write_config(MOVED)
start("Rootpc")
wait(lambda: get(MOVED, "/node.json"), 90)
check("same identity at the new address", json.loads(get(MOVED, "/node.json"))["lxmf"] == lxmf)
followed = wait(lambda: uplink()["parent"] == MOVED[0] and uplink()["parent_heard_s"] is not None
                and uplink()["parent_heard_s"] < 30, 180)
check("the second Stump finds it at its new address and rejoins, by itself", followed, uplink())
below = json.loads(get(MOVED, "/chain.json"))["below"]
check("the first Stump lists it below again", SECOND[0] in below, below)

logs = {n: stop(n) for n in list(procs)}
if os.environ.get("SHOWLOG"): print("\n".join(l for l in logs.get("Joinerpc","").splitlines() if "chain" in l.lower() or "follow" in l.lower())[-3000:])
check("its log says what happened", "looking for it on the network" in logs.get("Joinerpc", "")
      and "found it at %s" % MOVED[0] in logs.get("Joinerpc", ""), logs.get("Joinerpc", "")[-1500:])
for n, log in list(logs.items()) + [("Rootpc (first run)", log1)]:
    check("no tracebacks in %s's log" % n, "Traceback" not in log, log[log.find("Traceback"):][:1500])
shutil.rmtree(work, ignore_errors=True)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
