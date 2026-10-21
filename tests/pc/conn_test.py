# Holding a Stump with connections (conn.py), against a real computer
# Stump over real sockets: idle connections fill it, then are let go and
# it answers again; a request dribbled a byte a second, a header line that
# never ends, an upload that stops halfway -- each let go, nothing kept,
# and the Stump serving everyone else throughout.
# Run: python3 tests/pc/conn_test.py
import json, os, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8095, "127.0.0.1"
FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


def get(path, timeout=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://%s:%d%s" % (IP, PORT, path), timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, repr(e)


def sock():
    s = socket.create_connection((IP, PORT), timeout=10)
    return s


def closed_within(s, seconds):
    """Did the Stump close this connection within `seconds`? (What it
    sent first, if anything, is read and ignored.)"""
    s.settimeout(0.5)
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            if s.recv(4096) == b"":
                return time.time() - t0
        except socket.timeout:
            continue
        except OSError:
            return time.time() - t0
    return None


work = tempfile.mkdtemp(prefix="stump-conn-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += ("\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nCONN_MAX = 8\nCONN_HEAD_S = 4\nCONN_HEAD_BUSY_S = 2\n"
        "CONN_IDLE_S = 3\n" % (PORT, IP))
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
t0 = time.time()
up = False
while time.time() - t0 < 60 and not up:
    try:
        up = get("/node.json")[0] == 200
    except Exception:
        time.sleep(1)
check("the Stump starts", up)
def attacks():
    # 1. Eight connections that send nothing: the Stump is full...
    idle = [sock() for _ in range(8)]
    time.sleep(0.3)
    code, body = get("/node.json")
    check("full of silent connections: the next visitor gets '503, retry' at once", code == 503, (code, body[:80]))
    # ... and they're let go at the short deadline (it's full), then it answers again.
    gone = [closed_within(s, 6) for s in idle]
    # (their deadline -- 4 s, or 2 s once the Stump is three quarters full --
    # plus up to a second: the reaper looks once a second)
    check("... the silent ones are let go at their deadline",
          all(g is not None and g < 6 for g in gone), gone)
    for s in idle:
        s.close()
    time.sleep(0.3)
    check("... then it answers again", get("/node.json")[0] == 200)

    # 2. A request dribbled a byte a second: let go at the normal deadline.
    s = sock()
    t0 = time.time()
    still = True
    for ch in b"GET /node.json HTTP/1.1\r\nHost: x\r\n":
        try:
            s.send(bytes([ch]))
        except OSError:
            still = False
            break
        time.sleep(0.5)
        if time.time() - t0 > 8:
            break
    gone = closed_within(s, 6)
    check("a request dribbled a byte at a time: let go within its deadline", not still or gone is not None,
          (still, gone, time.time() - t0))
    s.close()

    # 3. A header line that never ends: let go, the memory left alone.
    s = sock()
    try:
        s.sendall(b"GET / HTTP/1.1\r\nX-Junk: " + b"A" * 200000)
    except OSError:
        pass
    check("a header line that never ends: let go", closed_within(s, 6) is not None)
    s.close()

    # 4. An upload that stops halfway: let go, nothing kept.
    s = sock()
    try:
        s.sendall(b"POST /upload HTTP/1.1\r\nX-Filename: half.bin\r\nAccept: application/json\r\n"
                  b"Content-Length: 1000000\r\n\r\n" + b"x" * 20000)
    except OSError:
        pass
    gone = closed_within(s, 10)
    s.close()
    time.sleep(0.5)
    shared = os.listdir(os.path.join(folder, "sd", "shared")) if os.path.isdir(os.path.join(folder, "sd", "shared")) else []
    check("an upload that stops halfway: let go after the idle time, nothing kept",
          gone is not None and not any("half" in n for n in shared), (gone, shared))

    # 5. Throughout: everyone else served, and the count is back to zero.
    t1 = time.time()
    code, _ = get("/node.json")
    check("everyone else is served at once", code == 200 and time.time() - t1 < 2, (code, time.time() - t1))


if up:
    try:
        attacks()
    except Exception as e:
        import traceback
        check("the attacks ran to the end", False, traceback.format_exc())
node.terminate()
try:
    log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired:
    node.kill()
    log = node.communicate()[0]
check("the log sums it up (not a line per connection)", "[web] connections:" in log
      and log.count("[web] connections:") <= 3, [l for l in log.splitlines() if "[web]" in l][-5:])
check("no tracebacks", "Traceback" not in log, log[log.find("Traceback"):][:1500])
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
