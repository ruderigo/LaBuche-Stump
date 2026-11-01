# Two computer Stumps with no Wi-Fi between them -- no chain -- each with
# a Heltec on USB, the two "Heltecs" simulated and joined to each other as
# if over the air (what one transmits, the other receives). The second
# Stump starts after the first has already announced itself, so they only
# meet because a Stump answers a Stump it hears for the first time. Then a
# line in #lora-mesh on one shows in the other's #lora-mesh as nick@stump,
# both ways. Linux only (two loopback addresses, pseudo-terminals).
# Run: python3 tests/pc/pc_radio_only_test.py
import json, os, pty, subprocess, sys, tempfile, threading, time, tty, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
A, B = ("127.0.9.1", 8098, "Radioa"), ("127.0.9.2", 8099, "Radiob")
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
    print("SKIP (needs two loopback addresses: Linux)")
    sys.exit(0)


def kiss(cmd, payload):
    body = bytearray()
    for b in payload:
        body += bytes([FESC, TFEND]) if b == FEND else bytes([FESC, TFESC]) if b == FESC else bytes([b])
    return bytes([FEND, cmd]) + bytes(body) + bytes([FEND])


class Heltec:
    """A simulated RNode on a pseudo-terminal: answers the handshake, and
    hands every data frame it's given to the other one (the air)."""

    def __init__(self):
        self.master, slave = pty.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self.slave = slave
        self.other = None
        self.buf, self.cur, self.esc, self.inside = bytearray(), None, False, False
        self.sent = 0

    def feed(self, data):
        for b in data:
            if b == FEND:
                if self.inside and self.cur:
                    self.frame(self.cur[0], bytes(self.cur[1:]))
                self.cur, self.inside = bytearray(), True
            elif self.inside:
                if b == FESC:
                    self.esc = True
                else:
                    if self.esc:
                        b = FEND if b == TFEND else FESC if b == TFESC else b
                        self.esc = False
                    self.cur.append(b)

    def frame(self, cmd, payload):
        if cmd == 0x08 and payload[:1] == b"\x73":
            os.write(self.master, kiss(0x08, b"\x46") + kiss(0x50, b"\x01\x4e") + kiss(0x48, b"\x80"))
        elif cmd == 0x00 and payload and self.other is not None:
            self.sent += 1
            os.write(self.other.master, kiss(0x00, payload))        # over the air


air = [Heltec(), Heltec()]
air[0].other, air[1].other = air[1], air[0]
stop = threading.Event()


def run_air():
    for h in air:
        os.set_blocking(h.master, False)
    while not stop.is_set():
        for h in air:
            try:
                h.feed(os.read(h.master, 4096))
            except (BlockingIOError, OSError):
                pass
        time.sleep(0.01)

threading.Thread(target=run_air, daemon=True).start()

work = tempfile.mkdtemp(prefix="stump-radio-")
nodes = {}


def start(who, heltec):
    ip, port, name = who
    folder = os.path.join(work, name)
    os.makedirs(folder)
    P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "usb", "pc_heltec_port": heltec.port})
    cfg += "\nNODE_NAME = %r\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = False\n" % (name, port, ip)
    open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
    nodes[name] = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def get(who, path, data=None):
    ip, port, _ = who
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request("http://%s:%d%s" % (ip, port, path), data=data), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def lora(who):
    return json.loads(get(who, "/rrc/poll?room=lora-mesh&since=0"))["messages"]


def wait(cond, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def up(who):
    try:
        get(who, "/node.json")
        return True
    except Exception:
        return False

start(A, air[0])
check("the first Stump starts, its radio online", wait(lambda: up(A) and json.loads(get(A, "/hw.json"))["state"] == "online", 60))
time.sleep(15)                    # its boot announces are gone by the time the second starts
start(B, air[1])
check("the second Stump starts, its radio online", wait(lambda: up(B) and json.loads(get(B, "/hw.json"))["state"] == "online", 60))
if up(A) and up(B):
    for who in (A, B):
        get(who, "/rrc/send", b"/join lora-mesh")
    met = wait(lambda: any("~Radiob" in m["body"] for m in lora(A)) and any("~Radioa" in m["body"] for m in lora(B)), 120)
    check("they meet over the radio alone: each in the other's #lora-mesh", met, (lora(A)[-4:], lora(B)[-4:]))
    get(A, "/rrc/send", b"over the air from A")
    got = wait(lambda: any(m["body"] == "over the air from A" and m["nick"].endswith("@Radioa") for m in lora(B)), 60)
    check("a line in #lora-mesh crosses over the radio, as nick@stump", got, lora(B)[-4:])
    get(B, "/rrc/send", b"and back from B")
    got = wait(lambda: any(m["body"] == "and back from B" and m["nick"].endswith("@Radiob") for m in lora(A)), 60)
    check("... and back", got, lora(A)[-4:])
    check("it went over the (simulated) air", air[0].sent > 0 and air[1].sent > 0, (air[0].sent, air[1].sent))
for name, proc in nodes.items():
    proc.terminate()
    try:
        log = proc.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        log = proc.communicate()[0]
    check("no tracebacks in %s's log" % name, "Traceback" not in log, log[log.find("Traceback"):][:1500])
    if FAIL:
        print("---- %s log (tail) ----\n%s" % (name, log[-3000:]))
stop.set()
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
