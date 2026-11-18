# A computer Stump found with no setup on a shared network: the official
# Reticulum library with its default AutoInterface (what NomadNet,
# Sideband and MeshChat run) on one machine, a computer Stump on another,
# a link between them and nothing configured. Two network namespaces and
# a veth pair stand for two machines on one Wi-Fi (Linux, root).
#   sudo python3 tests/pc/pc_autointerface_test.py        (the whole test)
# It runs itself as "stump" in one namespace and "ref" in the other.
import os, shutil, subprocess, sys, tempfile, time, json, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PORT = 8098
A, B = "stA", "stB"           # namespaces: the reference app, the Stump
IP_A, IP_B = "10.9.0.1", "10.9.0.2"


def sh(*cmd, check=True):
    return subprocess.run(list(cmd), check=check, capture_output=True, text=True)


def post(path, body):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request("http://%s:%d%s" % (IP_B, PORT, path), data=body,
                                 headers={"Cookie": "stump_v=v" + "5" * 20})
    with opener.open(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def get(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # As a browser (the Stump's cookie): an app without one must greet first.
    req = urllib.request.Request("http://%s:%d%s" % (IP_B, PORT, path), headers={"Cookie": "stump_v=v" + "5" * 20})
    with opener.open(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def role_stump(work):
    sys.path.insert(0, ROOT)
    import builtins
    builtins.input = lambda *a: ""
    import provisioner as P
    folder = os.path.join(work, "Stump")
    os.makedirs(folder)
    P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
    cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nNODE_NAME = 'Souche'\nAUTH_ADMIN_PASSWORD = 'pw'\n" % (PORT, IP_B)
    open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
    os.execv(sys.executable, [sys.executable, "-u", os.path.join(folder, "stump_pc.py")])


def role_ref(work):
    import RNS, LXMF
    FAIL = []

    def check(name, cond, info=""):
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:500]), flush=True)
        if not cond:
            FAIL.append(name)

    cdir = os.path.join(work, "ref")
    os.makedirs(cdir)
    # Reticulum's default: an AutoInterface, nothing else.
    open(os.path.join(cdir, "config"), "w").write("""[reticulum]
  enable_transport = False
  share_instance = No
[logging]
  loglevel = 4
[interfaces]
  [[Default Interface]]
    type = AutoInterface
    enabled = Yes
""")
    RNS.Reticulum(configdir=cdir)

    # Reticulum 1.6.1 stamps announces, and waits for the stamp before sending
    # one (16 s or more on CI): a cheap one here, so the test never hangs on it.
    # Still the real format; the Stump's patience with a slow one is its own
    # matter (urns/lxmf.py LXMF_PATH_WAIT).
    if hasattr(RNS.Transport, "DESTINATION_VALUE"):
        RNS.Transport.DESTINATION_VALUE = 8

    me = RNS.Identity()
    router = LXMF.LXMRouter(identity=me, storagepath=os.path.join(work, "lxmf"))
    got = []
    router.register_delivery_callback(lambda m: got.append(m.content_as_string()))
    src = router.register_delivery_identity(me, display_name="RefUser")
    t0 = time.time()
    node = None
    while time.time() - t0 < 60 and node is None:
        try:
            node = json.loads(get("/node.json"))
        except Exception:
            time.sleep(1)
    check("the Stump runs (its own namespace)", node is not None)
    stump = bytes.fromhex(node["lxmf"])
    router.announce(src.hash)
    t0 = time.time()
    while not RNS.Transport.has_path(stump) and time.time() - t0 < 90:
        if int(time.time() - t0) % 10 == 0:
            RNS.Transport.request_path(stump)
        time.sleep(1)
    check("found with no setup: a path to the Stump over AutoInterface", RNS.Transport.has_path(stump))
    ident = RNS.Identity.recall(stump)
    dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    m = LXMF.LXMessage(dest, src, "hello over the local network", desired_method=LXMF.LXMessage.DIRECT)
    router.handle_outbound(m)
    t0 = time.time()
    seen = False
    while time.time() - t0 < 60 and not seen:
        time.sleep(2)
        try:
            seen = False
            for r in ("lora-mesh", "main"):
                post("/rrc/send", ("/join #" + r).encode())          # a visitor reads the room it's in
                seen = seen or "hello over the local network" in get("/rrc/poll?room=%s&since=0" % r)
        except Exception:
            pass
    check("a message from the app reaches the Stump's chat", seen)
    t0 = time.time()
    while time.time() - t0 < 60 and not got:
        time.sleep(1)
    check("and the Stump answers it, back over the same network", bool(got), got)
    print("FAILURES", FAIL, flush=True)
    sys.exit(1 if FAIL else 0)


def main():
    if os.geteuid() != 0:
        print("needs root (network namespaces): sudo python3 " + __file__)
        sys.exit(2)
    work = tempfile.mkdtemp(prefix="stump-auto-")
    os.chmod(work, 0o777)
    for ns in (A, B):
        sh("ip", "netns", "del", ns, check=False)
        sh("ip", "netns", "add", ns)
    sh("ip", "link", "add", "vA", "type", "veth", "peer", "name", "vB")
    for ns, dev, ip in ((A, "vA", IP_A), (B, "vB", IP_B)):
        sh("ip", "link", "set", dev, "netns", ns)
        sh("ip", "netns", "exec", ns, "sysctl", "-qw", "net.ipv6.conf.all.disable_ipv6=0", check=False)
        sh("ip", "netns", "exec", ns, "ip", "link", "set", "lo", "up")
        sh("ip", "netns", "exec", ns, "ip", "addr", "add", ip + "/24", "dev", dev)
        sh("ip", "netns", "exec", ns, "ip", "link", "set", dev, "up")
    time.sleep(4)                       # the link-local addresses settle (DAD)
    print(sh("ip", "netns", "exec", B, "ip", "-6", "addr").stdout)
    log = open(os.path.join(work, "stump.out"), "w")
    stump = subprocess.Popen(["ip", "netns", "exec", B, sys.executable, __file__, "--stump", work],
                             stdout=log, stderr=subprocess.STDOUT)
    try:
        r = subprocess.run(["ip", "netns", "exec", A, sys.executable, __file__, "--ref", work], timeout=400)
        code = r.returncode
    finally:
        stump.terminate()
        try:
            stump.wait(10)
        except Exception:
            stump.kill()
        log.close()
        if code != 0:
            print(open(os.path.join(work, "stump.out")).read()[-4000:])
        for ns in (A, B):
            sh("ip", "netns", "del", ns, check=False)
    sys.exit(code)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--stump":
        role_stump(sys.argv[2])
    elif len(sys.argv) > 2 and sys.argv[1] == "--ref":
        role_ref(sys.argv[2])
    else:
        main()
