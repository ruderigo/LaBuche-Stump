# Turning a computer Stump off from /admin, end to end: the Stump folder
# made by the Provisioner (with its Start/Stop files), stump_pc.py run as
# the login service runs it (--service, the supervisor), /admin showing
# "Turn off this Stump", and the turn-off itself: the page answers, the
# Stump exits cleanly (code 0), and the supervisor does NOT start it again
# -- so launchd/systemd/the scheduled task don't either (they restart only
# on a failure). Run: python3 tests/pc/pc_power_test.py
import os, subprocess, sys, tempfile, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8094, "127.0.0.1"
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


def get(path, data=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request("http://%s:%d%s" % (IP, PORT, path), data=data), timeout=10) as r:
        return r.status, r.read().decode("utf-8", "replace")


work = tempfile.mkdtemp(prefix="stump-power-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nAUTH_ADMIN_PASSWORD = 'pw'\n" % (PORT, IP)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)

start, stop = P._pc_launchers(folder)
check("the Start and Stop files are in the folder", start.exists() and stop.exists()
      and (os.name == "nt" or os.access(str(start), os.X_OK)), (start, stop))
check("Stop asks the Stump to turn off (radio off first), Start starts it",
      "/admin/poweroff" in stop.read_text(encoding="utf-8")
      and "stump_pc.py" in start.read_text(encoding="utf-8"), stop.read_text(encoding="utf-8")[:300])

svc = subprocess.Popen([sys.executable, "-u", "stump_pc.py", "--service"], cwd=folder,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
up = False
t0 = time.time()
while time.time() - t0 < 60 and not up:
    try:
        up = get("/node.json")[0] == 200
    except Exception:
        time.sleep(1)
check("the Stump runs under its supervisor", up)
def admin():
    """Logs in as a browser does (the session cookie), then opens /admin."""
    import http.client
    c = http.client.HTTPConnection(IP, PORT, timeout=10)
    c.request("POST", "/admin", body=b"admin_pass=pw", headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = c.getresponse()
    r.read()
    cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
    c.request("GET", "/admin", headers={"Cookie": cookie})
    r = c.getresponse()
    return r.status, r.read().decode("utf-8", "replace")


if up:
    get("/lang?set=en&next=/")
    s, page = admin()
    check("/admin: Turn off this Stump, and how to start it again", "action='/admin/poweroff'" in page
          and "Turn off this Stump" in page and os.path.basename(str(start)) in page
          and "confirm(" in page, page[page.find("poweroff") - 300:][:600])
    s, page = get("/admin/poweroff", b"")
    check("turning off (from the computer itself): it says so, and how to start it again",
          s == 200 and "turning off" in page and os.path.basename(str(start)) in page, page[:400])
    try:
        code = svc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        code = None
    check("the Stump and its supervisor stop, cleanly (code 0: not restarted)", code == 0, code)
    still = True
    try:
        get("/node.json")
    except Exception:
        still = False
    check("nothing answers any more", not still)
if svc.poll() is None:
    svc.kill()
log = open(os.path.join(folder, "stump.log"), encoding="utf-8", errors="replace").read() \
    if os.path.exists(os.path.join(folder, "stump.log")) else ""
check("the log says it was turned off, and not started again", "[stump] turned off" in log
      and "[stump] stopped\n" in log and "starting again" not in log, log[-1500:])
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
