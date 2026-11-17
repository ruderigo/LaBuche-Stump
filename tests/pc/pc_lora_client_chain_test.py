# A LoRa client (a FireFly) talking to a Stump with no radio of its own,
# chained to the Stump with the radio: the kit's S3 + Heltec, with the Mac
# chained to it (1.0.0-beta.80). The client adds the Mac from its QR code
# (its key), finds its path, and writes to it; the Mac must get the
# client's key, answer it (/auth's challenge too), and the client must
# check the answers. NO_ANNOUNCE=1: a client that hasn't announced since
# the Mac started (it asks the chain for the key). Linux only. Run: python3 tests/pc/pc_lora_client_chain_test.py
import json, os, pty, subprocess, sys, tempfile, threading, time, tty, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
A, B = ("127.0.11.1", 8110, "Radior"), ("127.0.11.2", 8111, "Macos")
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
    """heltec None: no radio (the Mac)."""
    ip, port, name = who
    folder = os.path.join(work, name)
    os.makedirs(folder)
    P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "usb" if heltec else "none",
                               "pc_heltec_port": heltec.port if heltec else "",
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
check("the Stump with the radio starts, its radio online", wait(lambda: up(A) and json.loads(get(A, "/hw.json"))["state"] == "online", 60))
start(B, None, parent=A[0])
check("the Stump with no radio starts", wait(lambda: up(B), 60))
client = None
if up(A) and up(B):
    wifi = lambda who: json.loads(get(who, "/rrc/poll?room=wifi-mesh&since=0"))["messages"]
    for who in (A, B):
        get(who, "/rrc/send", b"/join wifi-mesh")
    check("chained (each in the other's #wifi-mesh)", wait(lambda: any("Macos" in m["body"] for m in wifi(A))
                                                       and any("Radior" in m["body"] for m in wifi(B)), 120))
    import re
    page = get(B, "/address")
    m = re.search(r"lxmf://([0-9a-f]{32})\?stump=[^&']*&(?:amp;)?key=([A-Za-z0-9_-]{86})", page)
    check("(the Mac's QR code: its address and key)", m is not None)
    cdir = os.path.join(work, "client")
    os.makedirs(cdir)
    P._pc_copy_firmware(P.STUMP_APP_DIR, cdir)
    open(os.path.join(cdir, "config.py"), "w").write(open(os.path.join(ROOT, "tests", "config.py")).read()
                                                      + "\nPC_HTTP_PORT = 8119\nPC_HOTSPOT_IP = '127.0.11.9'\n")
    lines = []
    client = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "lora_client.py"), cdir, air[1].port, m.group(1), m.group(2)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def reader():
        for ln in client.stdout:
            lines.append(ln.rstrip("\n"))
    threading.Thread(target=reader, daemon=True).start()

    def say(c):
        client.stdin.write(c + "\n")
        client.stdin.flush()

    def inbox():
        return [json.loads(x[3:]) for x in lines if x.startswith("IN ")]

    me = wait(lambda: any(x.startswith("ME ") for x in lines), 30) and [x[3:] for x in lines if x.startswith("ME ")][0]
    time.sleep(8)
    if os.environ.get("NO_ANNOUNCE") != "1":
        say("announce")                             # as FireFly does when it starts
    time.sleep(6)
    say("path")                                     # the path to the Mac, through the radio Stump
    check("the client finds its path to the Mac (through the radio Stump)",
          wait(lambda: any(x.startswith("DID path path=True") for x in lines) or (say("path") or False) or
               any("path=True" in x for x in lines), 60), lines[-6:])
    say("send hello from LoRa")
    peer = lambda: json.loads(get(B, "/rrc/poll?room=wifi-mesh&since=0"))["messages"] + json.loads(get(B, "/rrc/poll?room=lora-mesh&since=0"))["messages"]
    check("the Mac gets the client's line", wait(lambda: any(x["body"] == "hello from LoRa" for x in peer()), 60), peer()[-5:])
    print("ROOMS lora:", [x["nick"] + ":" + x["body"] for x in json.loads(get(B, "/rrc/poll?room=lora-mesh&since=0"))["messages"]][-3:],
          "wifi:", [x["nick"] + ":" + x["body"] for x in json.loads(get(B, "/rrc/poll?room=wifi-mesh&since=0"))["messages"]][-3:])
    say("send /auth")
    ch = wait(lambda: any("AUTH-CHALLENGE" in x["text"] for x in inbox()), 90)
    check("/auth: the Mac's challenge reaches the client", ch, (inbox()[-4:], lines[-10:]))
    check("... and the client can check it (the Mac's signature)", ch and all(x["ok"] for x in inbox() if "AUTH-CHALLENGE" in x["text"]), inbox()[-4:])
for name, proc in list(nodes.items()) + ([("client", client)] if client else []):
    proc.terminate()
    try:
        log = proc.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        log = proc.communicate()[0]
    if name == "client":
        log = "\n".join(lines)
    check("no tracebacks in %s's log" % name, "Traceback" not in log, log[log.find("Traceback"):][:1500])
    if FAIL:
        print("---- %s log (tail) ----\n%s" % (name, log[-4000:]))
stop.set()
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
