# /admin lives in admin_web.py, loaded the first time someone opens it:
# a Stump that's only chatted with, browsed and uploaded to never loads
# it; then /admin works as before, and every admin route answers.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


for p in ("/", "/files", "/billboard", "/about", "/tools", "/node.json", "/rrc/poll?since=0", "/files.json"):
    req(p)
s, ct, b = req("/upload", body=b"hello", headers=("X-Filename: a.txt",))
check("chat, pages, data and an upload: admin_web not loaded", "admin_web" not in sys.modules,
      [m for m in sys.modules if "admin" in m])
s, ct, b = req("/admin")
check("/admin: the login page, admin_web loaded now", s.startswith("200") and "admin_web" in sys.modules
      and "admin_pass" in b, (s, b[-200:]))
b = admin_page()[2]
check("logged in: the admin page", "/admin/logout" in b, b[-300:])
import admin_web
check("its routes: every GET and POST under /admin", admin_web.route is not None)
for path in ("/admin/layout", "/admin/theme", "/admin/radio", "/admin/plugins", "/admin/delete", "/admin/home"):
    s, ct, b = req(path, body=b"admin_pass=wrong", headers=("Content-Type: application/x-www-form-urlencoded",))
    check("POST %s with a wrong password: refused, not the chat page" % path, s.startswith("403") or "Invalid" in b
          or "admin_pass" in b, (s, b[:160]))
s, ct, b = req("/admin/nothing-here", method="PUT")
check("another method under /admin: as before, the chat page", s.startswith("200"), s)
print('FAILURES:', FAIL)
