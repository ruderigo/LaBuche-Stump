# Every web route with the inputs an attacker sends (pentest, step 3 of
# docs/ROADMAP.md), on the board's MicroPython, through the real request
# handler: paths that climb out (`..`, encoded, doubled), odd encodings,
# huge and negative sizes, broken headers, deep JSON, every method. For
# each: an HTTP answer (never a dropped request from an exception), a
# status that makes sense, nothing from config.py in it, no file written
# outside the card's folders -- and the Stump still answering at the end.
import sys, os
exec(open(sys.argv[1] + '/harness.py').read())
import config
FAIL = []
ERRORS = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:600]))
    if not c:
        FAIL.append(n)


# Exceptions the request handler caught and logged ("request error") are
# what an attacker hunts for: each one is counted here.
def _capture(*a, **k):
    text = " ".join(str(x) for x in a)
    if "request error" in text or "Traceback" in text:
        ERRORS.append(text[:200])


barkeep.print = _capture
SECRETS = [str(getattr(config, k)) for k in ("AUTH_ADMIN_PASSWORD", "FSERVBOT_OP_PASSWORD", "WIFI_PASS")
           if getattr(config, k, None) and len(str(getattr(config, k))) >= 3]
OK_STATUS = ("200", "201", "204", "302", "303", "304", "400", "401", "403", "404", "405", "409",
             "413", "414", "429", "431", "500", "503", "507")
# A file beside the firmware (where config.py is), outside every folder a
# route serves from: its words must never come back.
CANARY = "canary-7f3a91-never-served"
open("canary.txt", "w").write(CANARY)
SECRETS.append(CANARY)
before_files = set(os.listdir('.'))
seen = []


def hit(label, path, body=b'', headers=(), method=None, declared=None):
    try:
        s, ct, b = req(path, body=body, headers=headers, method=method, declared=declared)
    except Exception as e:
        seen.append((label, "EXC " + repr(e)[:120]))
        return None
    if not s or s[:3] not in OK_STATUS:
        seen.append((label, "status " + repr(s)))
    for x in SECRETS:
        if x in b:
            seen.append((label, "a config secret in the answer"))
    if "def " in b and "import " in b:
        seen.append((label, "source code in the answer"))
    return s, ct, b


# 1. Paths that climb out of their folder, on every route that names a file.
CLIMB = ["../canary.txt", "../../canary.txt", "../../../canary.txt", "..%2F..%2Fcanary.txt", "%2e%2e%2f%2e%2e%2fcanary.txt",
         "../barkeep.py", "../../barkeep.py", "../config.py", "../../config.py", "..%2Fconfig.py", "..%2f..%2fconfig.py", "%2e%2e/config.py",
         "%2E%2E%2Fconfig.py", "....//config.py", "..\\config.py", "..%5cconfig.py", "/config.py",
         "%00config.py", "config.py%00.txt", "shared/../../config.py", "..", ".", "%", "%G1", "%2",
         "a" * 600, "éè/../config.py"]
FILE_ROUTES = ["/download?f=", "/tool?f=", "/home-file?f=", "/fw?f=", "/rrc/voice?id=", "/files?q=",
               "/billboard?q=", "/p/tavern/", "/p/../", "/admin/../"]
for r in FILE_ROUTES:
    for c in CLIMB:
        hit("GET " + r + c[:20], r + c)
check("the test knows what mustn't come back", len(SECRETS) >= 2, SECRETS)
check("paths that climb out of their folder: answered, nothing from config.py or the code",
      not seen, seen[:6])
del seen[:]

# 2. Every route, every method, odd bodies.
ROUTES = ["/", "/rrc", "/rrc/poll?room=main&since=0", "/rrc/send", "/rrc/voice?to=x&mode=16", "/rrc/rec",
          "/hello", "/node.json", "/stumps.json", "/chain.json", "/hw.json", "/plugins.json", "/files.json",
          "/billboard.json", "/tools.json", "/files", "/billboard", "/about", "/tools", "/profile",
          "/profile/register", "/profile/login", "/profile/logout", "/upload", "/post", "/chat", "/lang?l=xx",
          "/concierge", "/flash", "/admin", "/admin/theme", "/admin/layout", "/admin/radio", "/admin/delete",
          "/admin/delete_post", "/admin/setup", "/admin/plugins", "/admin/profiles", "/admin/home",
          "/admin/propagation", "/admin/radio.json", "/admin/bt.json", "/admin/radiolink.json",
          "/admin/poweroff", "/admin/logout", "/codec2.wasm", "/p/tavern/", "/nothing-here"]
BODIES = [b"", b"x", b"admin_pass=&reset=1", b"=&&&==", b"\xff\xfe\x00\x01" * 8, b'{"a":' * 40,
          b"[" * 3000 + b"]" * 3000, b'{"pub": "zz", "sig": "", "name": "' + b"N" * 300 + b'", "prefer": 7}',
          b"nick=" + b"%ff" * 50 + b"&pw=1"]
for r in ROUTES:
    for m in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
        bodies = BODIES if m == "POST" else [b""]
        for body in bodies:
            hdr = ("Content-Type: application/json",) if body[:1] in (b"{", b"[") else \
                  ("Content-Type: application/x-www-form-urlencoded",)
            hit(m + " " + r, r, body=body, headers=hdr, method=m)
check("every route, every method, odd and deep bodies: an answer, a sane status", not seen, seen[:6])
del seen[:]

# 3. Broken requests and headers.
for label, path, hdrs, declared in (
        ("negative length", "/upload", ("X-Filename: a.txt",), -5),
        ("a huge length", "/upload", ("X-Filename: a.txt",), 10 ** 12),
        ("length that isn't a number", "/post", ("Content-Length: abc",), None),
        ("a header with no colon", "/", ("NoColonHere",), None),
        ("a header line 4000 bytes long", "/", ("X-Long: " + "A" * 4000,), None),
        ("a cookie of junk", "/rrc/poll?room=main", ("Cookie: stump_v=" + "\x7f" * 40 + "; stump_admin=../../x",), None),
        ("a cookie that names another's session", "/admin", ("Cookie: stump_admin=0000000000000000",), None),
        ("a Host trying to split the answer", "/flash", ("Host: evil\r\nSet-Cookie: x=1",), None),
        ("Accept-Language junk", "/", ("Accept-Language: " + "x," * 500,), None)):
    hit(label, path, body=b"data" if declared is not None else b"", headers=hdrs, declared=declared)
check("broken requests and headers: an answer, a sane status", not seen, seen[:6])
del seen[:]

# 4. Uploads with names that climb or hide.
for name in ("../../pwned.txt", "..%2F..%2Fpwned.txt", "/pwned.txt", "a/../../pwned.txt", "con", ".htaccess",
             "x\x00y.txt", "‮txt.exe", "a" * 400 + ".txt", "....", "%2e%2e%2fpwned.txt"):
    hit("upload " + name[:20], "/upload", body=b"hello", headers=("X-Filename: " + name, "X-Filename-Encoding: url"))
after = set(os.listdir('.'))
check("uploads named to climb out: nothing written outside the card's folders",
      not [f for f in after - before_files if "pwned" in f or f.startswith(".")], sorted(after - before_files))
check("... and nothing named so in the card's own folder either", not any("pwned" in n and "/" in n
                                                                              for n in os.listdir(fserv.SHARED_DIR)))
check("... answered", not seen, seen[:6])
del seen[:]


# 5b. Sessions: forged cookies get nothing; a real one works only where it's for.
r = req("/admin", headers=("Cookie: stump_admin=" + "0" * 32,))
check("a made-up admin session: the login page, not /admin", "/admin/logout" not in r[2] and "admin_pass" in r[2])
r = req("/admin", headers=("Cookie: stump_p=" + "0" * 32 + "; stump_v=p:admin",))
check("a made-up profile session, a visitor ID shaped like a profile's: nobody special",
      "/admin/logout" not in r[2], r[0])
import webtext
check("passwords and tags are compared in constant time (webtext.same)", webtext.same("pw", "pw")
      and not webtext.same("pw", "pW") and not webtext.same("pw", "pwx") and not webtext.same("", "pw"))

# 5. Nothing the handler had to catch, and still serving.
check("no request ended in an exception the handler had to catch", not ERRORS, ERRORS[:6])
s, ct, b = req("/node.json")
check("the Stump still answers", s.startswith("200") and '"lxmf"' in b, s)
print('FAILURES:', FAIL)
