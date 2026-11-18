# The NomadNet pack end to end: a computer Stump with the pack, and the
# official Reticulum library (what NomadNet and MeshChat run) on the
# other side of a TCP link, browsing it as NomadNet does: a path request
# to the Stump's nomadnetwork.node, a link, Identify, page requests, and
# a line said in a room through a form; then its RRC hub, from a bare
# client (cbor2) and from NomadNet's own chat client (nomadnet/RRC.py).
# Needs rns, cbor2 and nomadnet (pip): run by the "reference" job. Run: python3 tests/pc/pc_nomadnet_test.py
import os, shutil, subprocess, sys, tempfile, time, json, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P
import RNS

PORT, IP, RNS_PORT = 8097, "127.0.0.1", 4297
FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("%", "%25").replace("\n", "%0A"))


def get(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://%s:%d%s" % (IP, PORT, path), timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


# The reference side: a Reticulum instance with a TCP server the Stump
# connects to (as a NomadNet laptop on the same network would be).
work = tempfile.mkdtemp(prefix="stump-nn-")
rcfg = os.path.join(work, "rns")
os.makedirs(rcfg)
open(os.path.join(rcfg, "config"), "w").write("""[reticulum]
  enable_transport = True
  share_instance = No
  panic_on_interface_error = No
[logging]
  loglevel = 2
[interfaces]
  [[Stump link]]
    type = TCPServerInterface
    enabled = yes
    listen_ip = 127.0.0.1
    listen_port = %d
""" % RNS_PORT)
RNS.Reticulum(configdir=rcfg)

# Reticulum 1.6.1 stamps announces, and waits for the stamp before sending
# one (16 s or more on CI): a cheap one here, so the test never hangs on it.
# Still the real format; the Stump's patience with a slow one is its own
# matter (urns/lxmf.py LXMF_PATH_WAIT).
if hasattr(RNS.Transport, "DESTINATION_VALUE"):
    RNS.Transport.DESTINATION_VALUE = 8
me = RNS.Identity()

# A NomadNet node of our own, for the Stump to browse: a page with a form.
nn_dest = RNS.Destination(me, RNS.Destination.IN, RNS.Destination.SINGLE, "nomadnetwork", "node")
got_forms = []


def _page(path, data, request_id, link_id, remote_identity, requested_at):
    if path == "/page/form.mu":
        got_forms.append(data)
        return ("Thanks, " + str((data or {}).get("field_who"))).encode()
    return (">Aleph node\nWelcome to `!Aleph`!.\n`[Next`:/page/next.mu]\n"
            "Who: `<16|who`>\n`[Send`:/page/form.mu`who|mode=x]\n").encode()


for _p in ("/page/index.mu", "/page/form.mu"):
    nn_dest.register_request_handler(_p, response_generator=_page, allow=RNS.Destination.ALLOW_ALL)

# A real rrcd hub (github.com/kc1awv/rrcd), over its own Reticulum
# instance linked to ours: the Stump joins its room "lobby".
rrcd_proc = None
hub_hash = None
try:
    import rrcd  # noqa: F401
    rh = os.path.join(work, "rrcd")
    os.makedirs(os.path.join(rh, "rns"))
    open(os.path.join(rh, "rns", "config"), "w").write("""[reticulum]
  enable_transport = False
  share_instance = No
[logging]
  loglevel = 2
[interfaces]
  [[to ref]]
    type = TCPClientInterface
    enabled = yes
    target_host = 127.0.0.1
    target_port = %d
""" % RNS_PORT)
    args = [sys.executable, "-m", "rrcd", "--config", os.path.join(rh, "rrcd.toml"),
            "--identity", os.path.join(rh, "hub_identity"), "--room-registry", os.path.join(rh, "rooms.toml")]
    subprocess.run(args, capture_output=True, timeout=60)          # first run: writes its files, exits
    toml = open(os.path.join(rh, "rrcd.toml")).read().replace('configdir = ""', 'configdir = %r' % os.path.join(rh, "rns"))
    open(os.path.join(rh, "rrcd.toml"), "w").write(toml)
    hub_id = RNS.Identity.from_file(os.path.join(rh, "hub_identity"))
    hub_hash = RNS.Destination.hash(hub_id, "rrc", "hub")
    rrcd_proc = subprocess.Popen(args + ["--hub-name", "Aleph"], stdout=open(os.path.join(rh, "out"), "w"),
                                 stderr=subprocess.STDOUT)
except ImportError:
    print("(rrcd not installed: the hub-joining part is skipped)")

# The Stump: a computer Stump folder, the pack's plugin in it, a TCP
# client to the reference instance.
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
shutil.copytree(os.path.join(ROOT, "packs", "nomadnet-pack", "nomadnet"), os.path.join(folder, "nomadnet"),
                ignore=shutil.ignore_patterns("__pycache__"))
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += ("\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nAUTH_ADMIN_PASSWORD = 'pw'\nNODE_NAME = 'Souche'\n"
        "CONFIG['interfaces'].append({'type': 'TCPClientInterface', 'name': 'ref', 'enabled': True,"
        " 'target_host': '127.0.0.1', 'target_port': %d})\n") % (PORT, IP, RNS_PORT)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
if hub_hash:
    os.makedirs(os.path.join(folder, "sd", "plugins", "nomadnet"), exist_ok=True)
    json.dump([{"hub": hub_hash.hex(), "name": "aleph", "rooms": ["lobby"]}],
              open(os.path.join(folder, "sd", "plugins", "nomadnet", "hubs.json"), "w"))
log = open(os.path.join(work, "stump.out"), "w")
stump = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder, stdout=log, stderr=subprocess.STDOUT)

try:
    up, t0 = False, time.time()
    while time.time() - t0 < 60 and not up:
        try:
            up = get("/node.json")[0] == 200
        except Exception:
            time.sleep(1)
    check("the computer Stump runs, with the pack", up)
    s, hello = get("/hello")
    pub = bytes.fromhex(json.loads(hello)["pub"])
    stump_id = RNS.Identity(create_keys=False)
    stump_id.load_public_key(pub)
    node = RNS.Destination.hash(stump_id, "nomadnetwork", "node")

    # Like NomadNet: ask for a path, then open a link.
    t0 = time.time()
    RNS.Transport.request_path(node)
    while not RNS.Transport.has_path(node) and time.time() - t0 < 60:
        time.sleep(0.5)
        if int(time.time() - t0) % 10 == 0:
            RNS.Transport.request_path(node)
    check("its NomadNet node answers a path request", RNS.Transport.has_path(node))
    ident = RNS.Identity.recall(node)
    dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE, "nomadnetwork", "node")
    link = RNS.Link(dest)
    t0 = time.time()
    while link.status != RNS.Link.ACTIVE and time.time() - t0 < 30:
        time.sleep(0.2)
    check("a link to it, as NomadNet's browser opens", link.status == RNS.Link.ACTIVE, link.status)

    def page(path, data=None):
        box = {}
        link.request(path, data=data, response_callback=lambda r: box.update(ok=r.response),
                     failed_callback=lambda r: box.update(failed=True), timeout=30)
        t0 = time.time()
        while not box and time.time() - t0 < 35:
            time.sleep(0.2)
        r = box.get("ok")
        return r.decode("utf-8", "replace") if isinstance(r, bytes) else r

    b = page("/page/index.mu")
    check("its home page, in micron, with the Stump's name", b is not None and "Souche" in b
          and ":/page/rooms.mu]" in b, b)
    b = page("/page/rooms.mu")
    check("the rooms", b is not None and "room=main]" in b, b)
    link.identify(me)
    time.sleep(1)
    b = page("/page/room.mu", {"var_room": "main"})
    check("identified: the room has a field to say something", b is not None and "<48|msg`>" in b, b)
    b = page("/page/say.mu", {"var_room": "main", "field_msg": "hello from the reference library"})
    check("said through the form: back on the room's page", b is not None and "hello from the reference library" in b, b)
    s, poll = get("/rrc/poll?room=main&since=0")
    check("... and in the Stump's chat, for everyone", "hello from the reference library" in poll, poll[-600:])
    b = page("/page/board.mu")
    check("the billboard", b is not None and "<40|title`>" in b, b)
    s, rooms_page = 200, page("/page/rooms.mu")
    link.teardown()

    # The RRC hub, as NomadNet's Channels speaks it (nomadnet/RRC.py), with
    # an independent CBOR library (cbor2).
    import cbor2, os as _os
    hubh = RNS.Destination.hash(stump_id, "rrc", "hub")
    check("the rooms page links each room into NomadNet's chat", "`rrc://" + hubh.hex() + "/main]" in (rooms_page or ""), rooms_page)
    RNS.Transport.request_path(hubh)
    t0 = time.time()
    while not RNS.Transport.has_path(hubh) and time.time() - t0 < 60:
        time.sleep(0.5)
        if int(time.time() - t0) % 10 == 0:
            RNS.Transport.request_path(hubh)
    hdest = RNS.Destination(RNS.Identity.recall(hubh), RNS.Destination.OUT, RNS.Destination.SINGLE, "rrc", "hub")
    got = []
    hl = RNS.Link(hdest)
    t0 = time.time()
    while hl.status != RNS.Link.ACTIVE and time.time() - t0 < 30:
        time.sleep(0.2)
    check("a link to its RRC hub", hl.status == RNS.Link.ACTIVE)
    hl.set_packet_callback(lambda data, pkt: got.append(cbor2.loads(data)))
    hl.identify(me)
    time.sleep(1)

    def env(t, room=None, body=None, nick=None):
        e = {0: 1, 1: t, 2: _os.urandom(8), 3: int(time.time() * 1000), 4: me.hash}
        if room is not None: e[5] = room
        if body is not None: e[6] = body
        if nick: e[7] = nick
        RNS.Packet(hl, cbor2.dumps(e)).send()

    def wait_for(pred, secs=20):
        t0 = time.time()
        while time.time() - t0 < secs:
            for e in list(got):
                if pred(e):
                    return e
            time.sleep(0.2)
        return None

    env(1, body={0: "nomadnet", 1: "0.1", 2: {0: True, 1: True}}, nick="Zoe")
    w = wait_for(lambda e: e.get(1) == 2)
    check("HELLO -> WELCOME, named after the Stump", w is not None and w[6][0] == "Souche", got)
    env(10, room="main")
    check("JOIN -> JOINED", wait_for(lambda e: e.get(1) == 11 and e.get(5) == "main") is not None, got)
    check("... with the room's recent lines", wait_for(lambda e: e.get(1) == 20
          and e.get(6) == "hello from the reference library") is not None, got)
    env(20, room="main", body="hi from Channels", nick="Zoe")
    time.sleep(2)
    s, poll = get("/rrc/poll?room=main&since=0")
    check("MSG from the hub: in the Stump's room", "hi from Channels" in poll, poll[-500:])
    import http.client
    c = http.client.HTTPConnection(IP, PORT, timeout=10)
    c.request("POST", "/rrc/send?room=main", body="hello Zoe, from the web".encode(),
              headers={"Cookie": "stump_v=v" + "7" * 20})
    c.getresponse().read()
    m = wait_for(lambda e: e.get(1) == 20 and e.get(6) == "hello Zoe, from the web")
    check("a line said on the web reaches NomadNet's chat", m is not None and m.get(5) == "main", got[-5:])
    env(20, room="main", body="/list")
    check("/list", wait_for(lambda e: e.get(1) == 21 and str(e.get(6)).startswith("Registered public rooms:")) is not None, got[-3:])
    hl.teardown()
    # NomadNet's own chat client (nomadnet/RRC.py), unchanged.
    from nomadnet import RRC as NRRC
    class App:
        identity = me
        class rns:
            @staticmethod
            def get_medium_path_timeout():
                return 30
        peer_settings = {"display_name": "Zoe"}
        storagepath = work
        rrc_history_per_room_cap = 500
        rrc_filter_loaded_history = True
        rrc_max_accepted_resource_size = 262144
        rrc_ephemeral_notices = 600
    mgr = NRRC.RRCManager(App())
    mgr._store_path = lambda: os.path.join(work, "rrc.store")
    mgr._history_root = lambda: os.path.join(work, "rrc_history")
    hubh = RNS.Destination.hash(stump_id, "rrc", "hub")
    hub = mgr.add_hub(hubh, name="Souche")
    hub.connect()
    t0 = time.time()
    while hub.status != NRRC.RRCHub.STATUS_CONNECTED and time.time() - t0 < 90:
        time.sleep(0.5)
    check("NomadNet's client connects to the Stump's hub", hub.status == NRRC.RRCHub.STATUS_CONNECTED, (hub.status, getattr(hub, "status_text", "")))
    check("... and learns its name", hub.hub_name == "Souche", hub.hub_name)
    hub.join_room("main")
    t0 = time.time()
    while "main" not in hub.rooms and time.time() - t0 < 20:
        time.sleep(0.3)
    check("NomadNet joins #main", "main" in hub.rooms, hub.rooms)
    time.sleep(3)
    texts = [m.text for m in hub.get_messages("main")]
    check("... and shows its recent lines", "hello from the reference library" in texts, texts)
    hub.send_message("main", "typed in NomadNet's Channels")
    time.sleep(2)
    s, poll = get("/rrc/poll?room=main&since=0")
    check("what Zoe types reaches the Stump's room", "typed in NomadNet's Channels" in poll, poll[-400:])
    import http.client
    c = http.client.HTTPConnection(IP, PORT, timeout=10)
    c.request("POST", "/rrc/send?room=main", body="hi Zoe from a browser".encode(), headers={"Cookie": "stump_v=v" + "8" * 20})
    c.getresponse().read()
    t0 = time.time()
    while time.time() - t0 < 15 and not any(m.text == "hi Zoe from a browser" for m in hub.get_messages("main")):
        time.sleep(0.3)
    got = [(m.nick, m.text) for m in hub.get_messages("main")]
    check("a browser's line shows in NomadNet, with its nick", any(t == "hi Zoe from a browser" and n for n, t in got), got[-5:])
    hub.disconnect()
    # ── Browsing NomadNet from the Stump ───────────────────────────
    nn_dest.announce(app_data=b"Aleph node")
    t0 = time.time()
    s, nodes = get("/p/nomadnet/nodes")
    while "Aleph node" not in nodes and time.time() - t0 < 30:
        time.sleep(1)
        s, nodes = get("/p/nomadnet/nodes")
    check("browse: the Stump lists the NomadNet node it heard", "Aleph node" in nodes, nodes[-500:])
    url = "/p/nomadnet/view?n=" + nn_dest.hash.hex() + "&p=/page/index.mu"
    t0 = time.time()
    s, view = get(url)
    while "refresh" in view and time.time() - t0 < 40:
        time.sleep(2)
        s, view = get(url)
    check("... and shows its page, fetched over Reticulum", "Aleph node</div>" in view and "font-weight:bold'>Aleph</span>" in view, view[-800:])
    import http.client
    c = http.client.HTTPConnection(IP, PORT, timeout=15)
    c.request("POST", "/p/nomadnet/view?n=" + nn_dest.hash.hex() + "&p=/page/form.mu&v=mode=x&f=who",
              body=b"mu_who=Rod", headers={"Content-Type": "application/x-www-form-urlencoded"})
    c.getresponse().read()
    t0 = time.time()
    while not got_forms and time.time() - t0 < 40:
        time.sleep(1)
    check("... a form sent from the Stump reaches the node as NomadNet sends it",
          got_forms and got_forms[0].get("field_who") == "Rod" and got_forms[0].get("var_mode") == "x", got_forms)

    # ── Joining another community's hub (rrcd) ─────────────────────
    if hub_hash:
        import cbor2
        RNS.Transport.request_path(hub_hash)
        t0 = time.time()
        while not RNS.Transport.has_path(hub_hash) and time.time() - t0 < 60:
            time.sleep(1)
        zoe = RNS.Identity()
        zl = RNS.Link(RNS.Destination(RNS.Identity.recall(hub_hash), RNS.Destination.OUT, RNS.Destination.SINGLE, "rrc", "hub"))
        t0 = time.time()
        while zl.status != RNS.Link.ACTIVE and time.time() - t0 < 30:
            time.sleep(0.3)
        zgot = []
        zl.set_packet_callback(lambda d, p: zgot.append(cbor2.loads(d)))
        zl.identify(zoe)
        time.sleep(1)

        def zenv(t, room=None, body=None, nick=None):
            e = {0: 1, 1: t, 2: os.urandom(8), 3: int(time.time() * 1000), 4: zoe.hash}
            if room is not None: e[5] = room
            if body is not None: e[6] = body
            if nick: e[7] = nick
            RNS.Packet(zl, cbor2.dumps(e)).send()

        zenv(1, body={0: "test", 1: "0.1"}, nick="Zoe")
        time.sleep(2)
        zenv(10, room="lobby", nick="Zoe")
        time.sleep(2)
        VISITOR = {"Cookie": "stump_v=v" + "6" * 20}

        def web(path, body=None):
            c = http.client.HTTPConnection(IP, PORT, timeout=10)
            c.request("POST" if body is not None else "GET", path, body=body, headers=VISITOR)
            return c.getresponse().read().decode("utf-8", "replace")

        t0 = time.time()
        bridged = False
        while time.time() - t0 < 120 and not bridged:
            zenv(20, room="lobby", body="hi from rrcd", nick="Zoe")
            time.sleep(5)
            try:
                web("/rrc/send", "/join #lobby-aleph".encode())       # as a visitor would
                bridged = "hi from rrcd" in web("/rrc/poll?room=lobby-aleph&since=0")
            except Exception:
                pass
        check("hub joined: a line said on a real rrcd hub reaches the Stump's room lobby-aleph", bridged)
        web("/rrc/send?room=lobby-aleph", "hello Aleph from the Stump".encode())
        t0 = time.time()
        m = None
        while time.time() - t0 < 30 and m is None:
            m = next((e for e in zgot if e.get(1) == 20 and e.get(6) == "hello Aleph from the Stump"), None)
            time.sleep(0.5)
        check("... and a line said on the Stump reaches the hub, signed with its speaker",
              m is not None and str(m.get(7, "")).endswith("@Souche"), zgot[-5:])
        zl.teardown()
finally:
    if rrcd_proc:
        rrcd_proc.terminate()
    stump.terminate()
    try:
        stump.wait(10)
    except Exception:
        stump.kill()
    log.close()
    if FAIL:
        print(open(os.path.join(work, "stump.out")).read()[-3000:])
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
