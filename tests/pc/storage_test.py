# A computer Stump's "card" (its sd/ folder, PC_STORAGE_MB): os.statvfs
# answers with the quota, never more than the disk has free -- and doesn't
# call itself (it did on macOS and Linux through shutil.disk_usage, which
# read as "no card" and refused every upload with 507). End to end: an
# upload is kept, one past the 75% line of the quota is refused, and the
# oldest shared file makes room for a new one.
# Run: python3 tests/pc/storage_test.py
import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8094, "127.0.0.1"
FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    with OPENER.open("http://%s:%d%s" % (IP, PORT, path), timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def upload(name, size):
    body = os.urandom(size)
    req = urllib.request.Request("http://%s:%d/upload" % (IP, PORT), data=body, method="POST",
                                 headers={"X-Filename": name, "Accept": "application/json",
                                          "Content-Length": str(size)})
    try:
        with OPENER.open(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


work = tempfile.mkdtemp(prefix="stump-store-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
cfg += "\nPC_HTTP_PORT = %d\nPC_HOTSPOT_IP = %r\nPC_STORAGE_MB = 2\nFRESH_S = 0\nUPLOAD_DAY_SHARE = 10\n" % (PORT, IP)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)

# In process first: the quota, the disk, no recursion.
sys.path[:0] = [os.path.join(folder, "pc_compat"), folder]
import stump_host
stump_host.install(folder)
real_free = stump_host._disk_free(folder)
st = os.statvfs("/sd")
check("os.statvfs on the card: an answer (it used to call itself forever)", len(st) == 10 and st[2] > 0, st)
check("... at most the quota, never more than the disk has free",
      st[2] <= 2 * 1024 * 1024 and st[3] <= real_free and st[3] > 0, (st, real_free))

import plugins
check("no per-plugin RAM figure on a computer (it showed 0 B)", plugins._mem_free() is None)

node = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
t0 = time.time()
up = False
while time.time() - t0 < 60 and not up:
    try:
        up = bool(get("/node.json"))
    except Exception:
        time.sleep(1)
check("the Stump starts", up)
if up:
    code, r = upload("small.bin", 20000)
    check("an upload is kept", code == 201 or (code == 200 and r.get("ok")), (code, r))
    if code == 507:
        info = "statvfs here: %r sd/: %r" % (os.statvfs("/sd"), sorted(os.listdir(os.path.join(folder, "sd"))))
        print(info)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + info)
    kept = os.path.join(folder, "sd", "shared", "small.bin")
    check("... it's in sd/shared", os.path.exists(kept) and os.path.getsize(kept) == 20000)
    code, r = upload("huge.bin", 1800 * 1024)
    check("past 75% of PC_STORAGE_MB: refused (507), nothing kept",
          code == 507 and not os.path.exists(os.path.join(folder, "sd", "shared", "huge.bin")), (code, r))
    code, r = upload("big1.bin", 900 * 1024)
    code2, r2 = upload("big2.bin", 900 * 1024)
    shared = sorted(os.listdir(os.path.join(folder, "sd", "shared")))
    check("full: the oldest shared files make room for a new one",
          code2 in (200, 201) and "big2.bin" in shared and "big1.bin" not in shared, (code, code2, shared))
node.terminate()
try:
    log = node.communicate(timeout=20)[0]
except subprocess.TimeoutExpired:
    node.kill()
    log = node.communicate()[0]
check("no tracebacks", "Traceback" not in log, log[log.find("Traceback"):][:1500])
if FAIL:
    why = "\n".join(l for l in log.splitlines() if not l.startswith("[1") and "DBG" not in l)[-2500:]
    print(why)
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::node log: " + why.replace("\n", "%0A"))
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
