# /admin login sessions and Log out (Back can't show the admin page or
# resend the password), the order of the nav buttons and home tiles, and
# (the plugins list markup is checked in plugin_web).
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import layout
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


layout.LAYOUT_FILE = ROOT + '/layout.json'
layout.save('nav', None); layout.save('tiles', None)

# Sessions
s, ct, b = req('/admin', b'admin_pass=wrong')
check("wrong password: prompt again", s.startswith('403') and header('Set-Cookie') is None)
s, ct, b = req('/admin', b'admin_pass=pw')
cookie = header('Set-Cookie') or ''
check("login redirects to GET /admin", s.startswith('303') and header('Location') == '/admin', s)
check("session cookie: HttpOnly, SameSite, /admin", 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
      and 'Path=/admin' in cookie, cookie)
tok = cookie.split(';')[0].split('=', 1)[1]
s, ct, b = req('/admin', headers=('Cookie: stump_admin=' + tok,))
check("GET /admin with the session: the admin page", "action='/admin/logout'" in b and "action='/admin/theme'" in b)
check("admin pages aren't stored", header('Cache-Control') == 'no-store', header('Cache-Control'))
check("forms carry the session, not the password", "value='" + tok + "'" in b and "name='admin_pass' value='pw'" not in b)
s, ct, b = req('/admin')
check("no session: the prompt", "type='password'" in b and "/admin/logout" not in b)
s, ct, b = req('/admin', headers=('Cookie: stump_admin=forged',))
check("forged session: the prompt", "type='password'" in b)
s, ct, b = req('/admin/theme', ('admin_pass=' + tok + '&theme=oled').encode())
check("a form with the session works", s.startswith('200'), s)
s, ct, b = req('/admin/logout', ('admin_pass=' + tok).encode(), headers=('Cookie: stump_admin=' + tok,))
check("log out: back to the prompt, cookie cleared", s.startswith('303') and header('Location') == '/admin'
      and 'Max-Age=0' in (header('Set-Cookie') or ''), (s, header('Set-Cookie')))
s, ct, b = req('/admin', headers=('Cookie: stump_admin=' + tok,))
check("after log out, Back shows the prompt", "type='password'" in b and "/admin/theme" not in b)
s, ct, b = req('/admin/theme', ('admin_pass=' + tok + '&theme=paper').encode())
check("after log out, a resent form is refused", s.startswith('403'), s)
import time
tok2 = login()
barkeep._sessions[tok2] -= barkeep.SESSION_IDLE_S + 1
check("an idle session ends", not barkeep._session_ok(tok2))
toks = [login() for _ in range(barkeep.SESSION_MAX + 3)]
check("sessions are capped", len(barkeep._sessions) <= barkeep.SESSION_MAX and barkeep._session_ok(toks[-1]))
req('/admin/theme', b'admin_pass=pw&theme=')

# Order
def nav_hrefs(b):
    i = b.rfind("<div class='tiles nav'>")
    out, j = [], i
    while True:
        j = b.find("href='", j)
        if j < 0 or j > b.find("</div>", i):
            break
        out.append(b[j + 6:b.find("'", j + 6)]); j += 6
    return out
st, ct, b = req('/tools')
check("home first by default", nav_hrefs(b)[0] == '/', nav_hrefs(b))
s, ct, b = admin_page()
check("admin: both lists to drag", "data-field='nav'" in b and "data-field='tiles'" in b
      and "data-key='home'" in b and "data-key='about'" in b)
s, ct, b = req('/admin/layout', b'admin_pass=pw&nav=about,tools,home,chat,board,files,bogus&tiles=files,chat,nope')
check("nav saved", nav_hrefs(req('/tools')[2]) == ['/about', '/', '/rrc', '/billboard', '/files'], nav_hrefs(req('/tools')[2]))
home = req('/')[2]
check("tiles saved; the rest follow", home.find("href='/files'") < home.find("href='/rrc'") < home.find("href='/billboard'"),
      home[home.find("tiles home"):][:300])
check("chat page follows the order", (lambda b: b.find("href='/about'") < b.find("href='/'"))(req('/rrc')[2]))
layout._load()
check("order survives a restart", layout.nav()[0] == 'about')
req('/admin/layout', b'admin_pass=pw&reset=1')
check("default order again", nav_hrefs(req('/tools')[2])[0] == '/')

# Hiding
req('/admin/layout', b'admin_pass=pw&nav=home,chat,board,files,tools,about&tiles=chat,board,files,tools,about'
    b'&nav_hidden=tools,bogus&tiles_hidden=about,files')
check("hidden nav button gone from every page", '/tools' not in nav_hrefs(req('/about')[2])
      and '/tools' not in nav_hrefs(req('/files')[2]), nav_hrefs(req('/about')[2]))
check("hidden from the chat page's nav too", "href='/tools'" not in req('/rrc')[2])
home = req('/')[2]
t = home[home.find("tiles home"):]
check("hidden tiles gone from home", "href='/about'" not in t and "href='/files'" not in t and "href='/rrc'" in t, t[:400])
check("a hidden page still answers", req('/tools')[0].startswith('200') and req('/about')[0].startswith('200'))
s, ct, b = admin_page()
check("admin still lists hidden ones, marked", "data-key='tools' class='off'" in b and "data-key='about' class='off'" in b
      and "data-key='chat' class='off'" not in b)
req('/admin/layout', b'admin_pass=pw&nav=home,chat,board,files,tools,about&tiles=chat,board,files,tools,about'
    b'&nav_hidden=&tiles_hidden=')
check("shown again", '/tools' in nav_hrefs(req('/about')[2]) and "href='/about'" in req('/')[2])
req('/admin/layout', b'admin_pass=pw&nav_hidden=tools&tiles_hidden=about')
req('/admin/layout', b'admin_pass=pw&reset=1')
check("default order shows everything", '/tools' in nav_hrefs(req('/about')[2]) and not layout.hidden('tiles'))

print("FAILURES", FAIL)
