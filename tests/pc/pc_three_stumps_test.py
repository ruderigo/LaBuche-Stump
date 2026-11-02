# Three Stumps in a line -- Alpha (the first), Bravo (joins Alpha, and
# takes Charlie below it), Charlie (joins Bravo) -- the real firmware on
# three computers (stump_pc.py), on loopback. Bravo is a computer with two
# networks (PC_STA_IP), so it sits in the middle as a CAM would.
#
# The path matrix, across one and two hops: rooms, DMs between people on
# the ends, a DM to a Stump, the Stumps' own room (#main) staying home,
# names listed once, nothing echoed or doubled -- and the paths that must
# not exist (a DM seen by anyone else, #main crossing). Then the chain
# under stress: the middle Stump restarts (the ends find each other again,
# and nothing said before arrives twice), an end Stump restarts (it lists
# and names the others again at once), and a Stump joins with the wrong
# secret (refused, and the Stump above says so) then the right one.
# Also what each Stump knows of the others (/chain.json "stumps": name,
# release, chain protocol). Linux only. Run: python3 tests/pc/pc_three_stumps_test.py
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

SECRET = "three-stumps-secret"
# name: (hotspot ip, http port, sta ip, parent ip, parent http port)
STUMPS = {"Alpha": ("127.0.8.1", 8101, "", None, None),
          "Bravo": ("127.0.9.1", 8102, "127.0.8.2", "127.0.8.1", 8101),
          "Charlie": ("127.0.9.2", 8103, "", "127.0.9.1", 8102)}
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:700]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


if not sys.platform.startswith("linux"):
    print("SKIP (needs several loopback addresses: Linux)")
    print("FAILURES", [])
    sys.exit(0)

work = tempfile.mkdtemp(prefix="stump-three-")
procs, logs = {}, {}


def folder(name):
    return os.path.join(work, name)


def write_config(name, secret=SECRET):
    ip, port, sta, parent, pport = STUMPS[name]
    cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
    cfg += P._pc_config_block({"pc_hotspot_name": name, "pc_heltec": "none",
                               "chain": {"enabled": True, "parent_ip": parent} if parent else {"enabled": True}})
    cfg += ("\nNODE_NAME = %r\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nPC_STA_IP = %r\nCHAIN_ENABLED = True\n"
            "CHAIN_UPLINK_SSID = %r\nCHAIN_SECRET = %r\nCHAIN_LAN = False\n"
            % (name, port, ip, sta, "(up)" if parent else "", secret))
    if pport:
        cfg += "PC_CHAIN_PARENT_HTTP_PORT = %d\n" % pport
    open(os.path.join(folder(name), "config.py"), "w", encoding="utf-8").write(cfg)


def start(name):
    procs[name] = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder(name),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def stop(name):
    p = procs.pop(name)
    p.terminate()
    try:
        out = p.communicate(timeout=20)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        out = p.communicate()[0]
    logs[name] = logs.get(name, "") + out
    return out


def get(name, path, data=None, timeout=10):
    ip, port = STUMPS[name][:2]
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(urllib.request.Request("http://%s:%d%s" % (ip, port, path), data=data), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def poll(name, room):
    return json.loads(get(name, "/rrc/poll?room=%s&since=0" % room))


def room(name, r):
    return poll(name, r)["messages"]


def say(name, text):
    return get(name, "/rrc/send", text.encode())


def wait(cond, secs, step=2):
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            if cond():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def count(name, r, body):
    return sum(m["body"] == body for m in room(name, r))


def chain_json(name):
    return json.loads(get(name, "/chain.json"))


for name in STUMPS:
    os.makedirs(folder(name))
    P._pc_copy_firmware(P.STUMP_APP_DIR, folder(name))
    write_config(name)
for name in STUMPS:
    start(name)
    time.sleep(3)
up = wait(lambda: all(get(n, "/node.json") for n in STUMPS), 90)
check("three Stumps start", up)

try:
    # ── 1. The chain itself ───────────────────────────────────────────
    linked = wait(lambda: "127.0.8.2" in json.dumps(chain_json("Alpha")["below"])
                  and "127.0.9.2" in json.dumps(chain_json("Bravo")["below"])
                  and chain_json("Charlie")["uplink"].get("parent_heard_s") is not None, 90)
    check("the chain links up: Alpha > Bravo > Charlie", linked,
          {n: chain_json(n) for n in STUMPS})
    # One person on each Stump (the test reaches all three from one
    # address, so each gets its own name: otherwise they'd share a guest's).
    for n, who in (("Alpha", "ann"), ("Bravo", "bob"), ("Charlie", "cal")):
        say(n, "/nick " + who)
        say(n, "/join wifi-mesh")
    nick = {n: poll(n, "wifi-mesh")["nick"] for n in STUMPS}
    check("each Stump's person has their name", nick == {"Alpha": "ann", "Bravo": "bob", "Charlie": "cal"}, nick)

    # ── 2. Rooms, one hop and two ─────────────────────────────────────
    say("Alpha", "from alpha")
    got = wait(lambda: count("Bravo", "wifi-mesh", "from alpha") and count("Charlie", "wifi-mesh", "from alpha"), 150)
    check("a line in #wifi-mesh on the first Stump reaches the middle one and the last (two hops)", got,
          {n: room(n, "wifi-mesh") for n in ("Bravo", "Charlie")})
    say("Charlie", "from charlie")
    got = wait(lambda: count("Alpha", "wifi-mesh", "from charlie") and count("Bravo", "wifi-mesh", "from charlie"), 90)
    check("... and from the last one back up to the first", got,
          {n: room(n, "wifi-mesh") for n in ("Alpha", "Bravo")})
    check("... as nick@stump", any(m["body"] == "from charlie" and m["nick"] == nick["Charlie"] + "@Charlie"
                                   for m in room("Alpha", "wifi-mesh")), room("Alpha", "wifi-mesh"))

    # ── 3. DMs between the ends, and to a Stump ───────────────────────
    users_a = lambda: poll("Alpha", "wifi-mesh")["users"]
    check("people two Stumps away are DM contacts", wait(lambda: nick["Charlie"] + "@Charlie" in users_a(), 60), users_a())
    say("Alpha", "/msg %s@Charlie across two" % nick["Charlie"])
    dms = lambda n: poll(n, "wifi-mesh").get("dms", [])
    got = wait(lambda: any(d["body"] == "across two" and d["nick"] == nick["Alpha"] + "@Alpha" for d in dms("Charlie")), 90)
    check("a DM from the first Stump reaches a person on the last one", got, dms("Charlie"))
    say("Charlie", "/msg %s@Alpha and back" % nick["Alpha"])
    check("... and the answer comes back", wait(lambda: any(d["body"] == "and back" for d in dms("Alpha")), 90), dms("Alpha"))
    say("Charlie", "/msg Alpha to the stump")
    check("a DM to a Stump two hops away: it answers privately, a DM from the Stump itself",
          wait(lambda: any(d["nick"] == "Alpha" and "@Alpha" in d["body"] for d in dms("Charlie")), 90),
          dms("Charlie"))
    check("... and it's in no room", not any("to the stump" in m["body"] for n in STUMPS
                                              for m in room(n, "wifi-mesh")), room("Alpha", "wifi-mesh"))

    # ── 4. Paths that must not exist ─────────────────────────────────
    time.sleep(12)
    everywhere = {n: room(n, "wifi-mesh") + [{"body": d["body"]} for d in dms(n)] for n in STUMPS}
    check("the DM between the ends: not seen by the Stump in the middle, nor in any room",
          not any(m["body"] in ("across two", "and back") for m in everywhere["Bravo"])
          and not any(m["body"] in ("across two", "and back") for n in STUMPS for m in room(n, "wifi-mesh")),
          everywhere["Bravo"])
    for n in STUMPS:
        say(n, "/join main")
    say("Alpha", "only on alpha")
    time.sleep(15)
    check("#main stays on its Stump", count("Alpha", "main", "only on alpha") == 1
          and not count("Bravo", "main", "only on alpha") and not count("Charlie", "main", "only on alpha")
          and not any("only on alpha" in m["body"] for n in ("Bravo", "Charlie") for m in room(n, "wifi-mesh")))
    for n in STUMPS:
        say(n, "/join wifi-mesh")

    # ── 5. Once, everywhere ───────────────────────────────────────────
    once = {n: (count(n, "wifi-mesh", "from alpha"), count(n, "wifi-mesh", "from charlie")) for n in STUMPS}
    check("every line once on every Stump, never echoed back", all(v == (1, 1) for v in once.values()), once)
    names = lambda: {n: poll(n, "wifi-mesh")["users"] for n in STUMPS}
    names_cmd = lambda: {n: json.loads(say(n, "/names"))["replies"][0] for n in STUMPS}
    want = {"Alpha": ["ann", "bob@Bravo", "cal@Charlie"], "Bravo": ["ann@Alpha", "bob", "cal@Charlie"],
            "Charlie": ["ann@Alpha", "bob@Bravo", "cal"]}
    # (Everyone just came back from #main: the lists follow within a cycle.)
    listed = wait(lambda: all(sorted(u for u in names()[n] if u.split("@")[0] in ("ann", "bob", "cal")) == want[n]
                              for n in STUMPS), 60)
    check("each person listed once on each Stump, as nick@stump when elsewhere -- someone quiet too (bob)",
          listed, names())
    check("... and /names says the same", all(all(w in names_cmd()[n] for w in want[n]) and "mesh-" not in names_cmd()[n]
                                              for n in STUMPS), names_cmd())

    # ── 6. What each Stump knows of the others ───────────────────────
    st = wait(lambda: {s.get("name") for s in chain_json("Alpha").get("stumps", [])} >= {"Bravo", "Charlie"}, 60)
    stumps = chain_json("Alpha").get("stumps", [])
    check("/chain.json lists the other Stumps, with their release and chain protocol", st and all(
        s.get("release") and s.get("proto") for s in stumps if s.get("name") in ("Bravo", "Charlie")), stumps)

    # ── 7. The middle Stump restarts ─────────────────────────────────
    stop("Bravo")
    time.sleep(5)
    start("Bravo")
    back = wait(lambda: "127.0.9.2" in json.dumps(chain_json("Bravo")["below"])
                and "127.0.8.2" in json.dumps(chain_json("Alpha")["below"]), 120)
    check("the middle Stump restarts: both ends link to it again, by themselves", back,
          {n: chain_json(n) for n in STUMPS})
    say("Bravo", "/join wifi-mesh")
    say("Charlie", "after the restart")
    got = wait(lambda: count("Alpha", "wifi-mesh", "after the restart"), 120)
    check("... and the ends talk again across it", got, room("Alpha", "wifi-mesh")[-5:])
    time.sleep(15)
    again = {n: (count(n, "wifi-mesh", "from alpha"), count(n, "wifi-mesh", "from charlie"),
                 count(n, "wifi-mesh", "after the restart")) for n in ("Alpha", "Charlie")}
    check("... nothing said before arrives twice", all(v == (1, 1, 1) for v in again.values()), again)

    # ── 7b. An end Stump restarts ────────────────────────────────────
    # It forgot the others, while they all still know it. Back on the
    # chain it lists them again (/chain.json "stumps", from their beacons)
    # and names them in #wifi-mesh within moments -- not at their next
    # half-hourly beacon and 2-minute announce. (A handheld Stump, the
    # R36MAX, restarts often.)
    stop("Charlie")
    time.sleep(5)
    start("Charlie")
    relinked = wait(lambda: "127.0.9.2" in json.dumps(chain_json("Bravo")["below"]), 90)
    knows = wait(lambda: {s.get("name") for s in chain_json("Charlie").get("stumps", [])} >= {"Alpha", "Bravo"}, 45)
    check("an end Stump restarts: within 45 s it lists the other Stumps again", relinked and knows,
          chain_json("Charlie").get("stumps"))
    say("Charlie", "/join wifi-mesh")
    named = wait(lambda: {"Alpha", "Bravo"} <= set(poll("Charlie", "wifi-mesh").get("stumps") or []), 45)
    check("... and names them in #wifi-mesh (no mesh-xxxx)", named, poll("Charlie", "wifi-mesh").get("stumps"))

    # ── 8. The wrong secret, then the right one ──────────────────────
    stop("Charlie")
    write_config("Charlie", secret="not-the-chain-secret")
    start("Charlie")
    time.sleep(40)
    b = chain_json("Bravo")
    check("a Stump with the wrong secret isn't taken in", "127.0.9.2" not in json.dumps(b["below"])
          and chain_json("Charlie")["uplink"].get("parent_heard_s") is None, (b, chain_json("Charlie")))
    check("... and the Stump above says who tried", any(r.get("ip") == "127.0.9.2" for r in b.get("refused", [])), b)
    stop("Charlie")
    write_config("Charlie")
    start("Charlie")
    check("the right secret again: taken in", wait(lambda: "127.0.9.2" in json.dumps(chain_json("Bravo")["below"]), 90),
          chain_json("Bravo"))
except Exception:
    import traceback
    check("the test ran to the end", False, traceback.format_exc())

for name in list(procs):
    stop(name)
for name, log in logs.items():
    tb = log[log.find("Traceback"):]
    check("no tracebacks in %s's log" % name, "Traceback" not in log, tb[:1200] + " ... " + tb[-1500:])
    check("no 'TX failed' warnings in %s's log (a link with nobody on it isn't a failure)" % name,
          "TX failed" not in log, [l for l in log.splitlines() if "TX failed" in l][:3])
    if FAIL:
        annotate("%s log: %s" % (name, log[-2500:]))
shutil.rmtree(work, ignore_errors=True)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
