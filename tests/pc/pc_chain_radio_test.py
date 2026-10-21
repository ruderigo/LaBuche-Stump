# Two computer Stumps in a chain (the first, and a second joining it) AND
# each with a Heltec on USB, the two simulated Heltecs joined over a
# simulated air: the LaBuche + laptop setup. The SECOND Stump writes in
# #lora-mesh first, before anyone else has said anything: it must reach
# the first Stump's #lora-mesh (over the radio), and the other way round.
# Linux only. Run: python3 tests/pc/pc_chain_radio_test.py
import json, os, pty, subprocess, sys, tempfile, threading, time, tty, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
A, B = ("127.0.10.1", 8100, "Firstr"), ("127.0.10.2", 8101, "Secondr")
SECRET = "chain-radio-secret"
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


def start(who, heltec, parent=None):
    ip, port, name = who
    folder = os.path.join(work, name)
    os.makedirs(folder)
    P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "usb", "pc_heltec_port": heltec.port,
                               "chain": {"enabled": True, "parent_ip": parent} if parent else {"enabled": True}})
    cfg += ("\nNODE_NAME = %r\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = True\n"
            "CHAIN_UPLINK_SSID = %r\nCHAIN_SECRET = %r\n" % (name, port, ip, "(home)" if parent else "", SECRET))
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
start(B, air[1], parent=A[0])
check("the second Stump starts, its radio online", wait(lambda: up(B) and json.loads(get(B, "/hw.json"))["state"] == "online", 60))
if up(A) and up(B):
    wifi = lambda who: json.loads(get(who, "/rrc/poll?room=wifi-mesh&since=0"))["messages"]
    for who in (A, B):
        get(who, "/rrc/send", b"/join wifi-mesh")
    met = wait(lambda: any("Secondr" in m["body"] for m in wifi(A)) and any("Firstr" in m["body"] for m in wifi(B)), 120)
    check("each knows the other (in #wifi-mesh, the chain)", met, (wifi(A)[-4:], wifi(B)[-4:]))
    for who in (A, B):
        get(who, "/rrc/send", b"/join lora-mesh")
    sent0 = (air[0].sent, air[1].sent)
    get(B, "/rrc/send", b"the second speaks first")
    got = wait(lambda: any(m["body"] == "the second speaks first" and m["nick"].endswith("@Secondr") for m in lora(A)), 30)
    check("the SECOND Stump writes first: on the first within 30 s, as nick@stump", got, lora(A)[-5:])
    check("... over the radio", air[1].sent > sent0[1], (sent0, air[0].sent, air[1].sent))
    get(A, "/rrc/send", b"the first answers")
    got = wait(lambda: any(m["body"] == "the first answers" and m["nick"].endswith("@Firstr") for m in lora(B)), 30)
    check("... and the first back to the second", got, lora(B)[-5:])
    names = lambda who: " ".join(json.loads(get(who, "/rrc/send", b"/names"))["replies"])
    check("/names in #lora-mesh on the first: the second Stump's person too, as nick@Secondr",
          wait(lambda: "@Secondr" in names(A), 30), names(A))
    get(B, "/rrc/send", b"the second again")
    got = wait(lambda: any(m["body"] == "the second again" for m in lora(A)), 30)
    check("... and the second again", got, lora(A)[-5:])
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
