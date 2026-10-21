# Keeping a computer Stump alive on its own (keepalive.py, stump_pc.py's
# supervisor), end to end: a Stump that stops responding is restarted
# (its heartbeat goes stale; macOS and Linux: frozen with SIGSTOP), one
# that keeps failing to start (killed before it's up for long, three
# times) comes back in a rescue start -- /node.json says "rescue", /admin
# says why and still takes the password with no plugins -- and "Start
# normally" brings it back to a normal start.
# Run: python3 tests/pc/watchdog_test.py
import json, os, signal, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8096, "127.0.0.1"
FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


def get(path, data=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(urllib.request.Request("http://%s:%d%s" % (IP, PORT, path), data=data), timeout=5) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, repr(e)


work = tempfile.mkdtemp(prefix="stump-wd-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nAUTH_ADMIN_PASSWORD = 'pw'\nWATCHDOG_S = 6\n" % (PORT, IP)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)
HB = os.path.join(folder, ".heartbeat")


def running_pid(other_than=None, wait=90):
    """The running Stump's process (from its heartbeat), once it answers."""
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            with open(HB) as f:
                pid = int(f.read().split()[1])
            if pid != other_than and get("/node.json")[0] == 200:
                return pid
        except (OSError, ValueError, IndexError):
            pass
        time.sleep(0.5)
    return None


def kill(pid):
    """Gone at once, as a crash or a power cut (not a planned stop)."""
    os.kill(pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)


def state():
    try:
        with open(os.path.join(folder, "boot_state.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def mode():
    s, b = get("/node.json")
    try:
        return json.loads(b).get("mode")
    except ValueError:
        return None


svc = subprocess.Popen([sys.executable, "-u", "stump_pc.py", "--service"], cwd=folder,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def scenario():
    pid = running_pid()
    check("the Stump runs under its supervisor, its heartbeat beating", pid is not None and mode() == "normal",
          (pid, mode()))
    if pid is None:
        return
    failed = 0
    if hasattr(signal, "SIGSTOP"):
        # 1. It stops responding (frozen): the supervisor restarts it.
        os.kill(pid, signal.SIGSTOP)
        t0 = time.time()
        new = running_pid(other_than=pid, wait=60)
        check("frozen: restarted once its heartbeat went stale (6 s here)", new is not None and time.time() - t0 < 40,
              (new, time.time() - t0))
        log = open(os.path.join(folder, "stump.log"), encoding="utf-8", errors="replace").read()
        check("... the log says so", "stopped responding" in log, log[-600:])
        check("... and the start after it knows it was the watchdog", "watchdog" in str(state().get("last")), state())
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        pid = new
        failed = 1
    # 2. It keeps failing to start: killed early, until a rescue start.
    while pid is not None and failed < 3:
        kill(pid)
        failed += 1
        pid = running_pid(other_than=pid, wait=90)
    check("three failed starts in a row: a rescue start (/node.json: \"rescue\")", pid is not None and mode() == "rescue",
          (pid, mode(), state()))
    if pid is None:
        return
    # /admin: why, and the password still works with no plugins.
    import http.client
    get("/lang?set=en&next=/")
    c = http.client.HTTPConnection(IP, PORT, timeout=10)
    c.request("POST", "/admin", body=b"admin_pass=pw", headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
    c.request("GET", "/admin", headers={"Cookie": cookie})
    r = c.getresponse()
    page = r.read().decode("utf-8", "replace")
    check("/admin in a rescue start: logged in (no plugins), the notice and 'Start normally'",
          "Rescue start" in page and "Start normally" in page and "3 starts in a row failed" in page,
          page[page.find("<body"):][:600])
    # 3. Start normally.
    tok = cookie.split("=", 1)[1] if "=" in cookie else "pw"
    c.request("POST", "/admin/restart", body=("admin_pass=" + tok).encode(),
              headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})
    r = c.getresponse()
    r.read()
    new = running_pid(other_than=pid, wait=90)
    check("'Start normally': a normal start again", new is not None and mode() == "normal", (new, mode(), state()))


try:
    scenario()
except Exception:
    import traceback
    check("the scenario ran to the end", False, traceback.format_exc())
svc.terminate()
try:
    out = svc.communicate(timeout=30)[0]
except subprocess.TimeoutExpired:
    svc.kill()
    out = svc.communicate()[0]
log = open(os.path.join(folder, "stump.log"), encoding="utf-8", errors="replace").read()
# A traceback "during handling of" another is one chain: a chain that ends
# in KeyboardInterrupt is a stop this test made (SIGTERM, a kill).
chains, cur = [], ""
for part in log.split("Traceback (most recent call last)")[1:]:
    cur += part
    if "another exception occurred" not in part[-200:]:
        chains.append(cur)
        cur = ""
if cur:
    chains.append(cur)
tb = [c for c in chains if "KeyboardInterrupt" not in c]
check("no tracebacks (other than the stops this test made)", not tb, tb[:1])
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
