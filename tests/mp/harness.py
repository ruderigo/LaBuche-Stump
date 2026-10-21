# Test harness: loads barkeep under MicroPython with a temp SD tree, an admin
# password "pw", and fake stream objects, so requests run through the real
# request handler. Use: exec(open(HARNESS).read()) then req(...).
import sys, os
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
ROOT = os.getcwd() + '/_sd_web'
os.system('rm -rf %s && mkdir -p %s/shared %s/tools %s/home' % (ROOT, ROOT, ROOT, ROOT))
import fserv
fserv.SD_MOUNT = ROOT; fserv.SHARED_DIR = ROOT + '/shared'; fserv.TOOLS_DIR = ROOT + '/tools'
fserv.HOME_DIR = ROOT + '/home'; fserv.LEDGER_FILE = ROOT + '/ledger.json'
fserv.AWAITING_FILE = ROOT + '/awaiting.json'; fserv.sd_ok = True
import billboard
billboard.storage_file = lambda: ROOT + '/billboard.txt'
import barkeep
# Everything the pages can save goes in the test's own folder, never the
# real filesystem's root (a test that changed the theme once wrote
# /site_theme.json there).
import theme, layout, radio
theme.SITE_THEME_FILE = ROOT + '/site_theme.json'
layout.LAYOUT_FILE = ROOT + '/layout.json'
radio.SETTINGS_FILE = ROOT + '/radio.json'
radio.CHAT_FILE = ROOT + '/radio_chat.json'
barkeep._check_admin_pw_now = lambda pw: pw == 'pw'
import uasyncio as asyncio, ujson as json


class Rd:
    """A connection's bytes, as one stream (the request line, headers and
    body run together on a real socket)."""
    def __init__(s, lines, body=b''):
        s.body = b''.join(lines) + body
    async def readline(s):
        i = s.body.find(b'\n') + 1 or len(s.body)
        out, s.body = s.body[:i], s.body[i:]
        return out
    async def read(s, n=-1):
        if n < 0: n = len(s.body)
        out, s.body = s.body[:n], s.body[n:]
        return out
    async def readexactly(s, n):
        return await s.read(n)


class Wr:
    def __init__(s, ip='192.168.4.50'):
        s.w = []; s.ip = ip
    def get_extra_info(s, n): return (s.ip, 1)
    async def awrite(s, b, *a):
        s.w.append(b.encode() if isinstance(b, str) else bytes(b))
    def write(s, b):
        s.w.append(b.encode() if isinstance(b, str) else bytes(b))
    async def drain(s): pass
    async def aclose(s): pass
    def close(s): pass
    async def wait_closed(s): pass


def req(path, body=b'', headers=(), method=None, declared=None, secure=False):
    """-> (status, content_type, body_text)"""
    method = method or ('POST' if body or declared is not None else 'GET')
    hl = [('%s %s HTTP/1.1\r\n' % (method, path)).encode()]
    if method == 'POST':
        hl.append(('Content-Length: %d\r\n' % (len(body) if declared is None else declared)).encode())
    hl += [(h + '\r\n').encode() for h in headers] + [b'\r\n']
    w = Wr(); asyncio.run(barkeep._handle(Rd(hl, body), w, secure=secure))
    raw = b''.join(w.w); head, _, out = raw.partition(b'\r\n\r\n')
    head = head.decode()
    LAST_HEAD[0] = head
    ct = [l.split(': ', 1)[1] for l in head.split('\r\n') if l.lower().startswith('content-type')]
    return head.split('\r\n')[0][9:], (ct[0] if ct else '-'), out.decode('utf-8', 'replace')


LAST_HEAD = ['']


def header(name, head=None):
    for l in (head or LAST_HEAD[0]).split('\r\n'):
        if l.lower().startswith(name.lower() + ':'):
            return l.split(':', 1)[1].strip()
    return None


def login(pw='pw'):
    """Logs in to /admin: the session cookie value, or None."""
    req('/admin', ('admin_pass=' + pw).encode())
    c = header('Set-Cookie')
    return c.split(';')[0].split('=', 1)[1] if c else None


def admin_page(pw='pw'):
    """Logs in and opens /admin, as a browser does: (status, ct, body)."""
    tok = login(pw)
    return req('/admin', headers=('Cookie: stump_admin=' + (tok or ''),))
