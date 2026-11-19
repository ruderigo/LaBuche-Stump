# Stump OS's updater (pi/stump_update.py, 1.0.0-beta.87), end to end on
# this computer: a Stump folder made as the image makes it, a fake GitHub
# serving a newer release signed with a test key, and the update run as
# the Pi runs it. Checks it updates in place -- settings and identity
# kept -- and that nothing changes when the signature, the download or
# GitHub itself isn't right. Run: python3 tests/pc/stump_update_test.py
import hashlib, http.server, json, os, re, shutil, subprocess, sys, tempfile, threading, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:800]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

work = tempfile.mkdtemp(prefix="stump-update-")
KEY = Ed25519PrivateKey.generate()
PUB = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
RELEASE_KEY = (b"\x00" * 32 + PUB).hex()          # a Reticulum public key: X25519 half, Ed25519 half


def source_tree(dest, version=None):
    """The parts of the repository a release zip carries, with the test key."""
    for d in ("final_firmware", "pc", "packs", "pi", "scripts"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dest, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for f in ("provisioner.py", "CHANGELOG.md"):
        shutil.copy(os.path.join(ROOT, f), os.path.join(dest, f))
    open(os.path.join(dest, "RELEASE_SIGNING_KEY.txt"), "w").write(RELEASE_KEY + "\n")
    if version:
        p = os.path.join(dest, "final_firmware", "example_node.py")
        t = open(p, encoding="utf-8").read()
        open(p, "w", encoding="utf-8").write(re.sub(r'^STUMP_RELEASE = ".*"$', 'STUMP_RELEASE = "%s"' % version, t, flags=re.M))


# The Pi: a Stump folder as pi/stage-stump makes it, and the source it came from.
src = os.path.join(work, "stump-src")
os.makedirs(src)
source_tree(src)
folder = os.path.join(work, "Stump")
answers = json.load(open(os.path.join(ROOT, "pi", "answers.json")))
answers["folder for this stump"] = folder
open(os.path.join(work, "answers.json"), "w").write(json.dumps(answers))
r = subprocess.run([sys.executable, os.path.join(src, "provisioner.py"), "--pc", folder, "--answers",
                    os.path.join(work, "answers.json")], cwd=src, stdin=subprocess.DEVNULL, capture_output=True, text=True)
check("the Stump folder made as the image makes it", r.returncode == 0 and os.path.exists(os.path.join(folder, "rns", "identity")),
      (r.stdout + r.stderr)[-800:])
have = re.search(r'^STUMP_RELEASE = "(.*)"$', open(os.path.join(folder, "example_node.py")).read(), re.M).group(1)
ident = open(os.path.join(folder, "rns", "identity"), "rb").read()
open(os.path.join(folder, "config.py"), "a").write("\nNODE_NAME = 'Stump-TEST'\n")
cfg_before = open(os.path.join(folder, "config.py")).read()

# Fake GitHub: /releases and its assets.
SITE = {}


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = SITE.get(self.path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]


def publish(version, sign_with=KEY, tamper=False):
    rel = os.path.join(work, "rel-" + version)
    top = os.path.join(rel, "Stump-" + version)
    os.makedirs(top)
    source_tree(top, version)
    zpath = os.path.join(rel, "Stump-%s.zip" % version)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for d, _, fs in os.walk(top):
            for f in fs:
                p = os.path.join(d, f)
                z.write(p, os.path.relpath(p, rel))
    zbytes = open(zpath, "rb").read()
    sums = ("%s  Stump-%s.zip\n" % (hashlib.sha256(zbytes).hexdigest(), version)).encode()
    if tamper:
        zbytes = zbytes[:-10] + b"0123456789"
    names = {"Stump-%s.zip" % version: zbytes, "SHA256SUMS": sums, "SHA256SUMS.rnsig": sign_with.sign(sums).hex().encode()}
    assets = []
    for n, b in names.items():
        SITE["/dl/%s/%s" % (version, n)] = b
        assets.append({"name": n, "browser_download_url": BASE + "/dl/%s/%s" % (version, n)})
    SITE["/releases"] = json.dumps([{"tag_name": "v" + version, "draft": False, "prerelease": True, "assets": assets}]).encode()


def run_update(api=None):
    r = subprocess.run([sys.executable, os.path.join(src, "pi", "stump_update.py"), folder, "--api", api or BASE + "/releases",
                        "--src", src], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       env=dict(os.environ, STUMP_PROVISIONER_PYTHON=sys.executable))
    try:
        st = json.load(open(os.path.join(folder, "update-status.json")))
    except Exception:
        st = {}
    return r, st


def release_of(path):
    return re.search(r'^STUMP_RELEASE = "(.*)"$', open(path).read(), re.M).group(1)


NEW = "9.0.0-beta.1"
publish(NEW)
open(os.path.join(folder, ".update-request"), "w").write("1")
r, st = run_update()
check("a newer signed release: updated in place", r.returncode == 0 and st.get("state") == "updated" and st.get("release") == NEW,
      (st, (r.stdout + r.stderr)[-800:]))
check("... the Stump's firmware is the new release's", release_of(os.path.join(folder, "example_node.py")) == NEW)
check("... its identity kept", open(os.path.join(folder, "rns", "identity"), "rb").read() == ident)
check("... its settings kept", open(os.path.join(folder, "config.py")).read() == cfg_before)
check("... the running Stump told to restart on the new files (.updated)", os.path.exists(os.path.join(folder, ".updated")))
check("... the request file gone, so it doesn't run again", not os.path.exists(os.path.join(folder, ".update-request")))
check("... the new source in the old one's place, nothing left over",
      release_of(os.path.join(src, "final_firmware", "example_node.py")) == NEW
      and not os.path.exists(src + ".new") and not os.path.exists(src + ".old"))

r, st = run_update()
check("run again: already up to date", r.returncode == 0 and st.get("state") == "current", st)

before = open(os.path.join(folder, "example_node.py")).read()
publish("9.0.0-beta.2", sign_with=Ed25519PrivateKey.generate())
r, st = run_update()
check("signed with another key: refused, nothing changed", r.returncode != 0 and st.get("state") == "failed"
      and "signature" in st.get("why", "") and open(os.path.join(folder, "example_node.py")).read() == before, st)

publish("9.0.0-beta.3", tamper=True)
r, st = run_update()
check("a download that doesn't match the signed list: refused, nothing changed", r.returncode != 0
      and "match" in st.get("why", "") and open(os.path.join(folder, "example_node.py")).read() == before, st)

r, st = run_update(api="http://127.0.0.1:9/releases")
check("GitHub unreachable: said, nothing changed", r.returncode != 0 and "reach" in st.get("why", "")
      and open(os.path.join(folder, "example_node.py")).read() == before, st)

srv.shutdown()
shutil.rmtree(work, ignore_errors=True)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
