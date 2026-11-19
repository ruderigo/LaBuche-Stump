# Stump OS, the Raspberry Pi image (pi/README.md, 1.0.0-beta.87), the parts
# a computer can check: a Stump with ADMIN_CLAIM and no admin password asks
# the first visitor to /admin to choose it (and only until then), keeps it
# across a restart, and with STUMP_OS shows the Pi's card: an update or a
# shutdown asked from it lands as a request file in the Stump's folder (a
# systemd path unit picks it up on the Pi). Run: python3 tests/pc/stump_os_test.py
import os, re, subprocess, sys, tempfile, time, urllib.request, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
import builtins
builtins.input = lambda *a: ""
import provisioner as P

PORT, IP = 8097, "127.0.0.1"
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:600]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def req(path, data=None, cookie=None):
    r = urllib.request.Request("http://%s:%d%s" % (IP, PORT, path),
                               data=urllib.parse.urlencode(data).encode() if data is not None else None)
    if cookie:
        r.add_header("Cookie", cookie)
    try:
        with OPENER.open(r, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), e.headers


work = tempfile.mkdtemp(prefix="stump-os-")
folder = os.path.join(work, "Stump")
os.makedirs(folder)
P._pc_copy_firmware(P.STUMP_APP_DIR, folder)
cfg = open(os.path.join(ROOT, "tests", "config.py"), encoding="utf-8").read()
cfg += P._pc_config_block({"pc_hotspot_name": "x", "pc_heltec": "none"})
# As pi/stage-stump does: each key on one line, no admin password.
want = [("PC_HTTP_PORT", "%d" % PORT), ("PC_HOTSPOT_IP", repr(IP)), ("AUTH_ADMIN_PASSWORD", "''"),
        ("FSERVBOT_OP_PASSWORD", "''"), ("ADMIN_CLAIM", "True"), ("STUMP_OS", "True")]
for k, _ in want:
    cfg = re.sub(r"^%s\s*=.*\n?" % k, "", cfg, flags=re.M)
cfg += "\n" + "".join("%s = %s\n" % kv for kv in want)
open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(cfg)


def start():
    p = subprocess.Popen([sys.executable, "-u", "stump_pc.py"], cwd=folder,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            if req("/node.json")[0] == 200:
                return p
        except Exception:
            pass
        time.sleep(1)
    p.kill()
    annotate("Stump log: " + p.communicate()[0][-3000:])
    return None


def stop(p):
    p.terminate()
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()


node = start()
check("the Stump starts", node is not None)
if node:
    s, body, _ = req("/admin")
    check("no admin password yet: /admin asks its first visitor to choose it", s == 200 and "admin_claim" in body
          and "admin_pass" not in body, body[-400:])
    s, body, _ = req("/admin", {"admin_claim": "abc", "admin_claim2": "abc"})
    check("... too short: asked again", "admin_claim" in body and "6" in body)
    s, body, _ = req("/admin", {"admin_claim": "forest-pw-1", "admin_claim2": "forest-pw-2"})
    check("... the two differ: asked again", "admin_claim" in body)
    s, body, h = req("/admin", {"admin_claim": "forest-pw-1", "admin_claim2": "forest-pw-1"})
    cookie = (h.get("Set-Cookie") or "").split(";")[0]
    check("... chosen: logged in at once (a session)", s == 303 and cookie.startswith("stump_admin="), (s, dict(h)))
    text = open(os.path.join(folder, "config.py"), encoding="utf-8").read()
    check("... kept in config.py, on its one line", re.findall(r"^AUTH_ADMIN_PASSWORD\s*=.*$", text, re.M)
          == ["AUTH_ADMIN_PASSWORD = 'forest-pw-1'"], re.findall(r"^AUTH_ADMIN_PASSWORD.*$", text, re.M))
    s, body, _ = req("/admin")
    check("claimed: a new visitor gets the login, not the claim", "admin_pass" in body and "admin_claim" not in body)
    s, body, _ = req("/admin", {"admin_claim": "taken-over", "admin_claim2": "taken-over"})
    check("... and can't claim it again", s == 403 and "taken-over" not in open(os.path.join(folder, "config.py")).read())
    s, body, _ = req("/admin", cookie=cookie)
    check("the admin page shows the Pi's card (update, shutdown)", s == 200 and "/admin/osupdate" in body
          and "/admin/osshutdown" in body)
    s, body, _ = req("/admin/osupdate", {"admin_pass": "wrong"})
    check("an update asked without the password: refused", s == 403 and not os.path.exists(os.path.join(folder, ".update-request")))
    s, body, _ = req("/admin/osupdate", {"admin_pass": "forest-pw-1"})
    check("an update asked from /admin: the request file for the Pi's updater", s == 200
          and os.path.exists(os.path.join(folder, ".update-request")))
    open(os.path.join(folder, "update-status.json"), "w").write('{"state": "updated", "release": "1.0.0-beta.99"}')
    s, body, _ = req("/admin", cookie=cookie)
    check("... what the updater said, shown on the card", "1.0.0-beta.99" in body)
    s, body, _ = req("/admin/osshutdown", {"admin_pass": "forest-pw-1"})
    check("a shutdown asked from /admin: its request file", s == 200 and os.path.exists(os.path.join(folder, ".shutdown-request")))
    stop(node)
    node = start()
    check("restarted", node is not None)
    if node:
        s, body, _ = req("/admin")
        check("after a restart: still claimed (the login)", "admin_pass" in body and "admin_claim" not in body)
        s, body, h = req("/admin", {"admin_pass": "forest-pw-1"})
        check("... and the chosen password opens it", s == 303)
        stop(node)
    # Without STUMP_OS / ADMIN_CLAIM (any other Stump): neither.
    text = open(os.path.join(folder, "config.py"), encoding="utf-8").read()
    text = re.sub(r"^STUMP_OS\s*=.*$", "STUMP_OS = False", text, flags=re.M)
    text = re.sub(r"^ADMIN_CLAIM\s*=.*$", "ADMIN_CLAIM = False", text, flags=re.M)
    text = re.sub(r"^AUTH_ADMIN_PASSWORD\s*=.*$", "AUTH_ADMIN_PASSWORD = ''", text, flags=re.M)
    open(os.path.join(folder, "config.py"), "w", encoding="utf-8").write(text)
    node = start()
    if node:
        s, body, _ = req("/admin")
        check("any other Stump with no password: no claim, /admin stays locked", "admin_claim" not in body)
        s, body, _ = req("/admin/osupdate", {"admin_pass": ""})
        check("... and no Pi card to ask anything of", s == 404)
        stop(node)

print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
