# The hardening fixes (October 2026 review):
#   H-06  LXMF: a forged signature is never "verified"; unverifiable
#         senders are refused when the node isn't open
#   H-03  speed limits: chat (web and mesh), billboard posts, and a
#         lockout after wrong passwords (/admin page and chat commands)
#   H-05  live access settings (mode, room tiers) survive a restart
#   H-04  a room made stricter moves out whoever no longer qualifies
#   ledger and leftovers: saves are whole or not at all; a power cut's
#         .tmp files are swept at start-up
import sys, os, time
exec(open(sys.argv[1] + '/harness.py').read())
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


import config
config.AUTH_ADMIN_PASSWORD = 'pw'; config.FSERVBOT_OP_PASSWORD = 'op'
import plugins
plugins.load_all()
import rrc, rrc_mesh, guard, i18n
from stumpid import core as sid

# ── H-06 ────────────────────────────────────────────────────────────
from urns.identity import Identity
from urns.destination import Destination
from urns.lxmf import LXMessage, APP_NAME
alice, bob = Identity(), Identity()
a_in = Destination(alice, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")   # signs; not registered here
b_out = Destination(bob, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")
m = LXMessage(b_out, a_in, "hello from alice")
m.pack()
packed = bytes(m.packed)
Identity.known_destinations.pop(a_in.hash, None)    # not heard from yet
from urns import transport as _tr
for d in [d for d in _tr.Transport.destinations if d.hash == a_in.hash]:
    _tr.Transport.destinations.remove(d)
got = LXMessage.unpack_from_bytes(packed)
check("unknown sender: not verified (no key to check with)", not got.signature_validated
      and got.unverified_reason == LXMessage.SOURCE_UNKNOWN, got.unverified_reason)
Identity.remember(b"\x00" * 32, a_in.hash, alice.get_public_key())
got = LXMessage.unpack_from_bytes(packed)
check("known sender, right signature: verified", got.signature_validated)
forged = bytearray(packed)
forged[40] ^= 0xFF                       # inside the signature
got = LXMessage.unpack_from_bytes(bytes(forged))
check("forged signature: never verified", not got.signature_validated
      and got.unverified_reason == LXMessage.SIGNATURE_INVALID, got.unverified_reason)
import urns.lxmf as lxmf_mod
src = open("urns/lxmf.py").read()
check("forged messages are dropped before delivery (all three paths)",
      src.count("if message.unverified_reason == LXMessage.SIGNATURE_INVALID:") == 3)


class FakeMsg:
    def __init__(self, h, text, ok):
        self.source_hash = bytes.fromhex(h); self.signature_validated = ok
        self.timestamp = None; self.fields = {}; self.title = b""; self._t = text
    def content_as_string(self):
        return self._t


class FakeRouter:
    peers = {}
    def send_message(self, *a, **k): pass


U = "ab" * 16
sid.set_mode("hybrid")
rrc_mesh.on_message(FakeRouter(), FakeMsg(U, "hello", False))
check("hybrid node: an unverifiable sender is ignored", U not in rrc_mesh._peers)
sid.set_mode("open")
rrc_mesh.on_message(FakeRouter(), FakeMsg(U, "hello", False))
check("open node: an unverifiable sender is taken, as LXMF clients do", U in rrc_mesh._peers)
rrc_mesh._send_queue.clear()

# ── H-03 ────────────────────────────────────────────────────────────
guard.reset()
rrc.CHAT_EVERY, rrc.CHAT_BURST = 2, 3
W = '192.168.4.77'; i18n.set_lang(W, 'en')
rrc.touch_user(W)
out = [rrc.handle_input(W, 'main', 'line %d' % i)[0] for i in range(5)]
check("chat: a burst of 3 goes through", out[:3] == [[], [], []], out)
check("chat: then slow down (⧗)", out[3] and out[3][0].startswith("⧗"), out[3:])
check("chat: commands that only answer you aren't counted", rrc.handle_input(W, 'main', '/names')[0]
      and not rrc.handle_input(W, 'main', '/names')[0][0].startswith("⧗"))
check("chat: /msg counts", rrc.handle_input(W, 'main', '/msg nobody hi')[0][0].startswith("⧗"))
real_clock = guard._clock
guard._clock = lambda: real_clock() + 2100
check("chat: one line per 2 s after that", guard.allow("chat", W, 2, 3))
guard._clock = real_clock
M = "cd" * 16
peer = rrc_mesh._get_peer(M, "Rod")
outs = [rrc_mesh._handle(M, peer, "mesh line %d" % i)[0] for i in range(4)]
check("mesh: the same limit (each line costs airtime)", outs[3] and outs[3][0].startswith("⧗"), outs)

guard.reset()
i18n.set_lang('192.168.4.50', 'en')
for i in range(5):
    s, ct, b = req('/admin', b'admin_pass=wrong')
check("admin: 5 wrong passwords lock the page", "too many wrong passwords" in b, b[-300:])
s, ct, b = req('/admin', b'admin_pass=pw')
check("admin: even the right one waits while locked", s.startswith('403') and "too many" in b, s)
guard._fails.clear()
s, ct, b = req('/admin', b'admin_pass=pw')
check("admin: the right one works once the lock is over", s.startswith('303'), s)

guard.reset()
C = '192.168.4.88'; i18n.set_lang(C, 'en'); rrc.touch_user(C)
rrc.CHAT_BURST = 1000
for i in range(5):
    r = rrc.handle_input(C, 'main', '/admin nope mode')[0]
check("chat /admin: wrong passwords lock too", r and "too many wrong passwords" in r[0], r)
r = rrc.handle_input(C, 'main', '/admin pw mode')[0]
check("chat /admin: locked even with the right one", r and "too many" in r[0], r)
check("chat /admin: another visitor isn't locked", "current mode" in rrc.handle_input(W, 'main', '/admin pw mode')[0][0])
for i in range(5):
    r = rrc.handle_input('192.168.4.89', 'main', '/fsadd nope x :: y')[0]
check("fserv bot: wrong passwords lock too", r and "wrong password" in r[0] and guard.locked('192.168.4.89') > 0, r)

guard.reset()
import barkeep
barkeep.POST_MAX = 3
codes = [req('/post', b'title=post+%d' % i)[0][:3] for i in range(4)]
check("billboard: 3 posts a minute, then 429", codes == ['303', '303', '303', '429'], codes)
s, ct, b = req('/post', b'title=more', headers=('Accept: application/json',))
check("billboard: JSON clients get rate_limited", s.startswith('429') and 'rate_limited' in b, (s, b))

# ── H-05 ────────────────────────────────────────────────────────────
guard.reset()
check("the access settings have a file", bool(sid.STATE_FILE), sid.STATE_FILE)
rrc.handle_input(W, 'main', '/admin pw mode hybrid')
rrc.handle_input(W, 'main', '/admin pw room vault minted')
saved = json.load(open(sid.STATE_FILE))
check("mode and tiers saved", saved == {"mode": "hybrid", "rooms": {"vault": "minted"}}, saved)
check("no .tmp left behind", not any(n.endswith('.tmp') for n in os.listdir(sid.STATE_FILE.rsplit('/', 1)[0] or '.')))
sid.reset()                                   # a restart: RAM gone
check("after a restart, before loading: defaults", sid.AUTH_MODE == "open" and sid.room_tier("vault") == "open")
sid.load_state()
check("restored at start-up", sid.AUTH_MODE == "hybrid" and sid.room_tier("vault") == "minted")
r = rrc.handle_input(W, 'main', '/admin pw reset-access')[0]
check("reset-access: back to config.py", sid.AUTH_MODE == "open" and sid.room_tier("vault") == "open"
      and r and "config.py" in r[0], r)
try:
    os.stat(sid.STATE_FILE); gone = False
except OSError:
    gone = True
check("reset-access: the saved settings are gone", gone)

# ── H-04 ────────────────────────────────────────────────────────────
V = '192.168.4.90'; i18n.set_lang(V, 'en')
rrc.touch_user(V, nick='val', room='main')
rrc.handle_input(V, 'main', '/join den')
check("val is in #den", rrc.get_user(V)['room'] == 'den')
P2 = "ef" * 16
p2 = rrc_mesh._get_peer(P2, "Meshy")
rrc_mesh._handle(P2, p2, "/join den")
p2['room'] = 'den'; rrc.touch_user(P2, nick=p2['nick'], room='den')
rrc_mesh._notices.clear()
r = rrc.handle_input(W, 'main', '/admin pw room den minted')[0]
check("re-tier says who was moved", len(r) == 2 and "val" in r[1] and "~" in r[1], r)
check("web visitor moved to #main", rrc.get_user(V)['room'] == 'main')
dms = rrc.dms_since(V, 0)
check("web visitor told why (⊘ #den minted)", any("⊘ #den minted" in d.get("body", d.get("text", "")) for d in [rrc.dm_public(x) for x in dms]), dms)
check("mesh user moved to #main", rrc_mesh._peers[P2]['room'] == 'main')
check("mesh user told why", rrc_mesh._notices and "⊘ #den minted" in rrc_mesh._notices[-1][1], rrc_mesh._notices)
r = rrc.handle_input(W, 'main', '/admin pw room den open')[0]
check("back to open moves no one", r == ['#den is now open'], r)

# ── ledger and leftovers ────────────────────────────────────────────
import fserv
fserv.CREDITS_ENABLED = True
fserv.credit_add('x', 5)
check("ledger saved whole, no .tmp left", fserv._ledger().get('x') == 5
      and not any(n.endswith('.tmp') for n in os.listdir(ROOT)))
open(ROOT + '/billboard.txt.tmp', 'w').write('half a save')
open(ROOT + '/ledger.json.tmp', 'w').write('{"x": ')
open(ROOT + '/shared/upload.tmp', 'w').write('a visitor file')
n = fserv.sweep_tmp()
check("start-up sweep removes unfinished saves", n == 2 and 'billboard.txt.tmp' not in os.listdir(ROOT)
      and 'ledger.json.tmp' not in os.listdir(ROOT), (n, os.listdir(ROOT)))
check("the last whole ledger is kept", fserv._ledger().get('x') == 5)
check("a visitor's own .tmp file is left alone", 'upload.tmp' in os.listdir(ROOT + '/shared'))

print('FAILURES:', FAIL)
