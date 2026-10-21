# Two Stumps of a chain talking in #wifi-mesh (and #lora-mesh), end to end: two computer
# Stumps (the real firmware, stump_pc.py), the first one and a second one
# joining it on the same network, on loopback. Each takes the other in
# from its beacon -- nobody messages anybody first -- and a line said in
# #wifi-mesh on one shows on the other as "<nick@stump>", once, while
# #lora-mesh stays empty. Linux only: it needs two loopback addresses.
# Run: python3 tests/pc/pc_two_stumps_test.py
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

SECRET = "two-stumps-secret"
FIRST, SECOND = ("127.0.8.1", 8096, "Firstpc"), ("127.0.8.2", 8097, "Secondpc")
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

work = tempfile.mkdtemp(prefix="stump-two-")
fw = P.STUMP_APP_DIR
nodes = {}


def start(who, parent=None):
    ip, port, name = who
    folder = os.path.join(work, name)
    os.makedirs(folder)
    P._pc_copy_firmware(fw, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "none",
                               "chain": {"enabled": True, "parent_ip": parent} if parent else {"enabled": True}})
    cfg += ("\nNODE_NAME = %r\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCHAIN_ENABLED = True\n"
            "CHAIN_UPLINK_SSID = %r\nCHAIN_SECRET = %r\n" % (name, port, ip, "(home)" if parent else "", SECRET))
    open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
    nodes[name] = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def get(who, path, data=None):
    ip, port, _ = who
    req = urllib.request.Request("http://%s:%d%s" % (ip, port, path), data=data)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def room(who, name):
    return json.loads(get(who, "/rrc/poll?room=%s&since=0" % name))["messages"]


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


start(FIRST)
# The second starts well after the first, as on the bench: it misses the
# first one's boot beacon (the next is half an hour away), so the chain
# link itself has to make them meet.
wait(lambda: get(FIRST, "/node.json"), 90)
time.sleep(25)
start(SECOND, parent=FIRST[0])
up = wait(lambda: get(FIRST, "/node.json") and get(SECOND, "/node.json"), 90)
check("both Stumps start", up)
if up:
    port_get = lambda url, timeout=4: P._get_json(url.replace("/node.json", ":%d/node.json" % FIRST[1]), timeout)
    found = P.find_stumps("127.0.8.", get=port_get)
    check("find_stumps finds the first Stump on the network", [ip for ip, _ in found] == [FIRST[0]], found)
    for who in (FIRST, SECOND):
        get(who, "/rrc/send", b"/join wifi-mesh")
    met = wait(lambda: any("~Secondpc" in m["body"] for m in room(FIRST, "wifi-mesh"))
               and any("~Firstpc" in m["body"] for m in room(SECOND, "wifi-mesh")), 150)
    check("each takes the other into #wifi-mesh from its beacon", met,
          (room(FIRST, "wifi-mesh"), room(SECOND, "wifi-mesh")))
    get(SECOND, "/rrc/send", b"hello from the second")
    got = wait(lambda: any(m["body"] == "hello from the second" and m["nick"].endswith("@Secondpc")
                           for m in room(FIRST, "wifi-mesh")), 60)
    check("a line in #wifi-mesh reaches the other Stump, as nick@stump", got, room(FIRST, "wifi-mesh"))
    get(FIRST, "/rrc/send", b"hello back")
    got = wait(lambda: any(m["body"] == "hello back" and m["nick"].endswith("@Firstpc")
                           for m in room(SECOND, "wifi-mesh")), 60)
    check("... and back", got, room(SECOND, "wifi-mesh"))
    # a DM between people on the two Stumps
    nick2 = json.loads(get(SECOND, "/rrc/poll?room=wifi-mesh&since=0"))["nick"]
    nick1 = json.loads(get(FIRST, "/rrc/poll?room=wifi-mesh&since=0"))["nick"]
    users1 = lambda: json.loads(get(FIRST, "/rrc/poll?room=wifi-mesh&since=0"))["users"]
    check("people on the other Stump are DM contacts", wait(lambda: nick2 + "@Secondpc" in users1(), 30), users1())
    get(FIRST, "/rrc/send", ("/msg %s@Secondpc psst" % nick2).encode())
    dms2 = lambda: json.loads(get(SECOND, "/rrc/poll?room=wifi-mesh&since=0")).get("dms", [])
    got = wait(lambda: any(d["body"] == "psst" and d["nick"] == nick1 + "@Firstpc" for d in dms2()), 60)
    check("a DM crosses to the person on the other Stump", got, dms2())
    check("... privately", not any("psst" in m["body"] for m in room(SECOND, "wifi-mesh") + room(FIRST, "wifi-mesh")))
    get(SECOND, "/rrc/send", ("/msg %s@Firstpc got it" % nick1).encode())
    dms1 = lambda: json.loads(get(FIRST, "/rrc/poll?room=wifi-mesh&since=0")).get("dms", [])
    check("... and the answer comes back", wait(lambda: any(d["body"] == "got it" for d in dms1()), 60), dms1())
    # a DM to the other Stump itself
    get(SECOND, "/rrc/send", b"/msg Firstpc hi node")
    check("a DM to a Stump shows in its #wifi-mesh, never #lora-mesh",
          wait(lambda: any(m["body"] == "@Firstpc hi node" for m in room(FIRST, "wifi-mesh")), 60), room(FIRST, "wifi-mesh"))
    time.sleep(12)
    second = room(SECOND, "wifi-mesh")
    check("said once, never echoed back", sum(m["body"] == "hello from the second" for m in second) == 1
          and sum(m["body"] == "hello back" for m in second) == 1, second)
    check("no notices crossed", not any("@" in m["nick"] and m["kind"] == "system" for m in second)
          and not any(m["body"].startswith(("✓", "✗", "✎")) and "@" in m["nick"] for m in second), second)
    lora = []
    for who in (FIRST, SECOND):
        get(who, "/rrc/send", b"/join lora-mesh")      # the poll answers the room you're in
        lora += [m for m in room(who, "lora-mesh")]
    check("#lora-mesh stays quiet", not any("hello" in m["body"] for m in lora), lora)
    # #lora-mesh is shared by the Stumps too, in its own room
    get(SECOND, "/rrc/send", b"over the radio room")
    got = wait(lambda: any(m["body"] == "over the radio room" and m["nick"].endswith("@Secondpc")
                           for m in room(FIRST, "lora-mesh")), 60)
    check("a line in #lora-mesh reaches the other Stump's #lora-mesh, as nick@stump", got, room(FIRST, "lora-mesh"))
    get(FIRST, "/rrc/send", b"radio room back")
    got = wait(lambda: any(m["body"] == "radio room back" and m["nick"].endswith("@Firstpc")
                           for m in room(SECOND, "lora-mesh")), 60)
    check("... and back", got, room(SECOND, "lora-mesh"))
    time.sleep(12)
    l2 = room(SECOND, "lora-mesh")
    check("#lora-mesh: once, never echoed back", sum(m["body"] == "over the radio room" for m in l2) == 1
          and sum(m["body"] == "radio room back" for m in l2) == 1, l2)
    for who in (FIRST, SECOND):
        get(who, "/rrc/send", b"/join wifi-mesh")
    w = room(FIRST, "wifi-mesh") + room(SECOND, "wifi-mesh")
    check("#lora-mesh lines stay out of #wifi-mesh", not any("radio room" in m["body"] for m in w), w)

    # Registered profiles (no FireFly): made on one Stump, the chain shares
    # them -- the nick is reserved on the other, and its password logs in
    # there. A browser keeps the Stump's cookies; the FireFly app doesn't,
    # and stays known by its address, as before.
    import http.cookiejar, urllib.parse

    def browser():
        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.ProxyHandler({}))
        op.jar = jar
        return op

    def bget(op, who, path, data=None):
        ip, port, _ = who
        with op.open(urllib.request.Request("http://%s:%d%s" % (ip, port, path), data=data), timeout=10) as r:
            return r.read().decode("utf-8", "replace")

    a = browser()
    bget(a, FIRST, "/rrc")
    check("a browser gets a visitor ID", any(c.name == "stump_v" for c in a.jar))
    page = bget(a, FIRST, "/profile/register",
                urllib.parse.urlencode({"nick": "Wanderer", "pw": "walking1", "pw2": "walking1"}).encode())
    check("a profile made on the first Stump: logged in", "Wanderer" in page and any(c.name == "stump_p" for c in a.jar), page[-600:])
    reserved = wait(lambda: "Wanderer" in json.dumps(json.loads(get(SECOND, "/rrc/send", b"/nick Wanderer")).get("replies")), 120)
    check("... the chain shares it: its nick is reserved on the second Stump", reserved,
          get(SECOND, "/rrc/send", b"/nick Wanderer"))
    b = browser()
    bget(b, SECOND, "/rrc")
    page = bget(b, SECOND, "/profile/login", urllib.parse.urlencode({"nick": "wanderer", "pw": "walking1"}).encode())
    check("... and its password logs in there", any(c.name == "stump_p" for c in b.jar) and "Wanderer" in page, page[-600:])
    poll = json.loads(bget(b, SECOND, "/rrc/poll?room=main&since=0"))
    check("... as that nick in the chat", poll.get("nick") == "Wanderer" and poll.get("registered") is True, poll.get("nick"))
    get(FIRST, "/rrc/send", b"/nick appuser")
    check("a client without cookies (the FireFly app) stays known by its address",
          json.loads(get(FIRST, "/rrc/poll?room=main&since=0"))["nick"] == "appuser")
for name, proc in nodes.items():
    proc.terminate()
    try:
        log = proc.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        log = proc.communicate()[0]
    check("no tracebacks in %s's log" % name, "Traceback" not in log, log[log.find("Traceback"):][:1500])
    if FAIL:
        annotate("%s log: %s" % (name, log[-2500:]))
shutil.rmtree(work, ignore_errors=True)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
