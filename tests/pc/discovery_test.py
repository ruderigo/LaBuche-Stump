# Finding a Stump on a Wi-Fi without its address (discovery.py), end to
# end with a real computer Stump: "STUMP?" to UDP port 4244 gets its card
# (name, identity, web addresses with the port it really serves on);
# anything else gets nothing; /stumps.json has the same card; the beacon's
# data carries the addresses as a fifth item, and another Stump's beacon
# with them lands in /stumps.json. Run: python3 tests/pc/discovery_test.py
import json, os, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8093, "127.0.0.1"
FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


def get(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://%s:%d%s" % (IP, PORT, path), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def ask(payload, wait=2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(wait)
    s.sendto(payload, ("127.0.0.1", 4244))
    try:
        return s.recvfrom(2048)[0]
    except socket.timeout:
        return None
    finally:
        s.close()


work = tempfile.mkdtemp(prefix="stump-disc-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nNODE_NAME = 'Findme'\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\n" % (PORT, IP)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
t0 = time.time()
up = False
while time.time() - t0 < 60 and not up:
    try:
        up = bool(get("/node.json"))
    except Exception:
        time.sleep(1)
check("the Stump starts", up)
if up:
    time.sleep(2)
    raw = None
    for _ in range(5):
        raw = ask(b"STUMP? v1")
        if raw:
            break
        time.sleep(1)
    c = json.loads(raw) if raw else {}
    check("\"STUMP?\" on UDP 4244: its card, with its name and identity", c.get("stump") == 1
          and c.get("name") == "Findme" and len(c.get("lxmf", "")) == 32, c)
    check("... the addresses it serves its pages on, with the port it really uses",
          isinstance(c.get("http"), list) and all(u.startswith("http://") and u.endswith(":%d/" % PORT) for u in c["http"]),
          c.get("http"))
    node_json = json.loads(get("/node.json"))
    check("... the same identity as /node.json", c.get("lxmf") and c["lxmf"] in json.dumps(node_json), node_json)
    check("anything else: no answer", ask(b"hello there", wait=1.0) is None)
    d = json.loads(get("/stumps.json"))
    check("/stumps.json: this Stump's card first", d.get("self", {}).get("lxmf") == c.get("lxmf")
          and isinstance(d.get("stumps"), list), d)
node.terminate()
try:
    log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired:
    node.kill()
    log = node.communicate()[0]
check("it said it's answering", "[discovery] answering on UDP port 4244" in log, log[-1500:])
check("no tracebacks", "Traceback" not in log, log[log.find("Traceback"):][:1500])

# The beacon's data and another Stump's beacon, in process.
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))
from urns.log import set_loglevel, LOG_NONE
set_loglevel(LOG_NONE)
from urns import umsgpack
import discovery

data = umsgpack.unpackb(discovery.beacon_data("Beta A", "Findme", bytes(range(16))))
check("the beacon's data: the four items as before, then the web addresses",
      data[:4] == ["stump", "Beta A", "Findme", bytes(range(16))]
      and isinstance(data[4], list) and all(u.startswith("http://") for u in data[4]), data)
discovery.heard("ab" * 16, "LaBuche", ["http://192.168.0.173/", "http://192.168.4.1/"])
d = discovery.directory()
check("another Stump's beacon with its addresses: in the directory",
      d["stumps"][0]["lxmf"] == "ab" * 16 and d["stumps"][0]["http"][0] == "http://192.168.0.173/", d)
check("the card says which address is on the asker's network",
      all(discovery.card(ip)["here"] in ("",) + tuple(discovery.urls()) for ip in ("192.168.0.50", "10.9.9.9")))
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
