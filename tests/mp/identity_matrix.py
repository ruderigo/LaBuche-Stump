# One person, one name, every path (Rodrigo's report, 20 Oct: the same
# FireFly was guest-f678, guest-f678@macOS1, RodCell and RodCell@macOS1).
# On the board's MicroPython, through the web server where a client would
# go through it, with: two browsers (B1, B2: the Stump's cookie, guests),
# an app (FireFly over Wi-Fi: no cookie), the same phone on LoRa (H,
# announced as RodCell), another LoRa user (Zed), and two other Stumps of
# the chain (S2, S3) telling this one who they have.
# Each path is checked both ways: it arrives where it should, and NOWHERE
# else -- no other inbox, no room, no second listing.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config
config.AUTH_ADMIN_PASSWORD = 'pw'
config.APP_HELLO = True
import plugins
plugins.load_all()
import rrc, rrc_mesh, i18n, time, ubinascii
from urns.identity import Identity
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


def hx(b):
    return ubinascii.hexlify(b).decode()


ident = Identity(create_keys=False)
ident.load_private_key(bytes(range(64)))
H = rrc_mesh.lxmf_address(ident.hexhash)          # the phone, on LoRa
APP = '192.168.4.50'                              # the phone, over Wi-Fi (no cookie)
B1 = 'v' + '1' * 20                               # browsers (the Stump's cookie)
B2 = 'v' + '2' * 20
ZED = 'cd' * 16                                   # another LoRa user
for c in (APP, B1, B2, ZED, H):
    i18n.set_lang(c, 'en')


def web(path, body=b'', who=None, method=None):
    """A request as a client makes it: a browser sends its cookie, the app
    none."""
    hd = ('Cookie: stump_v=' + who,) if who else ()
    s, ct, b = req(path, body=body, headers=hd, method=method)
    try:
        return s, json.loads(b)
    except ValueError:
        return s, b


def send(text, who=None):
    return web('/rrc/send', text.encode(), who, 'POST')[1]


def poll(who=None, room='main'):
    return web('/rrc/poll?room=%s&since=0' % room, who=who)[1]


def everyone():
    """Every name a client could see listed anywhere on this Stump."""
    out = []
    for r in rrc.room_names():
        out += rrc.names_in_room(r)
    return out + rrc.reachable_nicks()


def inbox(cid):
    return [m['body'] for m in rrc.dms_since(cid, 0)]


def room_bodies(room):
    return [m.get('body') for m in rrc.since(room, 0)]


# The phone heard on LoRa: an announce named RodCell (its radio then off).
rrc_mesh._reach[H] = {"nick": "RodCell", "last_seen": time.time(), "last_dm_id": 0, "hinted": False,
                      "name": "RodCell"}
rrc.set_reachable(H, "RodCell")
# Zed: a LoRa user in #lora-mesh.
rrc_mesh._peers[ZED] = {"nick": "Zed", "room": rrc.MESH_ROOM, "home_room": rrc.MESH_ROOM, "last_seen": time.time(),
                        "last_id": 0, "last_dm_id": 0}
rrc.touch_user(ZED, nick="Zed", room=rrc.MESH_ROOM)

# ── Browsers: guests, as before ────────────────────────────────────────
p = poll(B1)
check("a browser: no greeting asked (no 'hello'), it reads the room", 'hello' not in p and 'messages' in p, p)
send('/nick bob', B1)
send('/nick alice', B2)
check("two browsers: two people, their own names", rrc.get_user(B1)['nick'] == 'bob' and rrc.get_user(B2)['nick'] == 'alice')

# ── The app before its greeting ────────────────────────────────────────
before_main = list(room_bodies('main'))
p = poll()
check("the app, before /auth and /nick: 'hello required', nothing to read", p.get('hello') == 'required'
      and p['messages'] == [] and p['dms'] == [] and p['users'] == [] and 'auth' in p['topic'], p)
guest = rrc.get_user(APP)['nick']
check("... and it isn't listed anywhere (no stray guest-xxxx)", guest not in everyone() and APP in rrc._hidden,
      (guest, everyone()))
r = send('hello everyone')
check("... writing in a room: refused, said why", r.get('error') == 'hello_required' and room_bodies('main') == before_main, r)
r = send('/msg bob hi')
check("... a DM: refused, nothing delivered", r.get('error') == 'hello_required' and inbox(B1) == [], (r, inbox(B1)))
r = send('/join lora-mesh')
check("... other commands: refused", r.get('error') == 'hello_required' and rrc.get_user(APP)['room'] == 'main', r)
send('/msg ' + guest + ' psst', B1)
check("... a DM to its guest name: nobody by that name", inbox(APP) == [], inbox(APP))
s, b = web('/rrc/voice?to=bob&mode=4', b'\x00' * 40, None, 'POST')
check("... a voice note: refused", 'say who they are' in str(b) and inbox(B1) == [], b)

# ── The greeting: /auth (the phone is known on LoRa as RodCell) ────────
r = send('/auth')
nonce = [x for x in r['replies'] if x.startswith('AUTH-CHALLENGE ')][0].split()[1]
sig = hx(ident.sign(nonce.encode()))
r = send('/auth ' + hx(ident.get_public_key()) + ' ' + sig)
check("/auth: verified, and the name it chose (its announce) taken at once -- greeting done",
      any(x.startswith('AUTH-OK') for x in r['replies']) and r.get('hello') == 'done'
      and rrc.get_user(APP)['nick'] == 'RodCell' and APP not in rrc._hidden, (r, rrc.get_user(APP)))
names = everyone()
check("one person: RodCell listed once, no guest-xxxx, nothing else of theirs", names.count('RodCell') == 1
      and not any(n.startswith('guest-') for n in names) and not any(n.startswith('RodCell@') for n in names), names)
check("... the web chat told guest -> RodCell (a thread under the guest name folds in)",
      rrc.renames().get(guest) == 'RodCell', rrc.renames())
p = poll()
check("... its poll: 'hello done', the room", p.get('hello') == 'done' and isinstance(p['messages'], list), p.get('hello'))

# ── DMs, every direction, and nowhere else ─────────────────────────────
send('/msg RodCell from bob', B1)
check("browser -> the phone: in its Wi-Fi inbox only", inbox(APP) == ['from bob'] and inbox(H) == []
      and inbox(B2) == [] and 'from bob' not in room_bodies('main') and 'from bob' not in room_bodies(rrc.MESH_ROOM),
      (inbox(APP), inbox(H)))
send('/msg rodcell case', B1)
check("... the name in any case: the same person", inbox(APP)[-1] == 'case')
send('/msg alice from bob', B1)
check("browser -> browser: that one only", inbox(B2) == ['from bob'] and 'from bob' not in inbox(APP)[1:]
      and inbox(B1) == [], (inbox(B2), inbox(B1)))
send('/msg bob from rod', None)
check("the phone -> a browser: that one only", inbox(B1) == ['from rod'] and 'from rod' not in inbox(B2)
      and 'from rod' not in room_bodies('main'), (inbox(B1), inbox(B2)))
ok, _ = rrc.send_dm('Zed', 'RodCell', 'over the air')
check("LoRa user -> the phone (here on Wi-Fi): its Wi-Fi inbox, not LoRa", ok and inbox(APP)[-1] == 'over the air'
      and inbox(H) == [], inbox(H))
ok, _ = rrc.send_dm('RodCell', 'Zed', 'back')
check("the phone -> LoRa user: Zed's inbox only", ok and inbox(ZED) == ['back'] and 'back' not in inbox(APP))
send('/msg RodCell myself')
check("a DM to oneself: not delivered to oneself", 'myself' not in inbox(APP))

# ── Rooms ──────────────────────────────────────────────────────────────
send('morning all', B1)
pa, pb = poll(), poll(B2)
check("browser in #main: the app and the other browser read it",
      'morning all' in [m['body'] for m in pa['messages']] and 'morning all' in [m['body'] for m in pb['messages']])
check("... not in #lora-mesh", 'morning all' not in room_bodies(rrc.MESH_ROOM))
send('rod here')
check("the app in #main: as RodCell", any(m.get('nick') == 'RodCell' and m.get('body') == 'rod here'
                                          for m in rrc.since('main', 0)))
send('/join lora-mesh', B2)
send('radio room', B2)
check("a line in #lora-mesh: not in #main", 'radio room' in room_bodies(rrc.MESH_ROOM) and 'radio room' not in room_bodies('main'))

# ── The chain: other Stumps' lists, with identities ────────────────────
S2, S3 = 'aa' * 16, 'bb' * 16
for s, nm in ((S2, 'macOS1'), (S3, 'Third')):
    rrc_mesh._peers[s] = {"nick": nm, "room": rrc.CHAIN_ROOM, "stump": True, "last_seen": time.time(),
                          "last_id": 0, "last_dm_id": 0}
C8 = 'ee' * 4
rrc_mesh._from_stump(S2, rrc_mesh._peers[S2], "[NAMES] RodCell~%s carol~%s guest-9999 -" % (H[:8], C8), title="#lora-mesh")
rn = rrc.remote_names(rrc.MESH_ROOM)
check("macOS1 lists RodCell (here already), carol, a guest: RodCell not repeated as @macOS1",
      'RodCell@macOS1' not in rn and 'carol@macOS1' in rn and 'guest-9999@macOS1' in rn, rn)
check("... as DM contacts too: carol@macOS1, guest-9999@macOS1, no RodCell@macOS1",
      'RodCell@macOS1' not in rrc.reachable_nicks() and 'carol@macOS1' in rrc.reachable_nicks(), rrc.reachable_nicks())
rrc_mesh._from_stump(S3, rrc_mesh._peers[S3], "[NAMES] carol~%s -" % C8, title="#lora-mesh")
rn = rrc.remote_names(rrc.MESH_ROOM)
check("carol on two other Stumps: listed once (where she was heard last)",
      sum(1 for n in rn if n.startswith('carol@')) == 1 and 'carol@Third' in rn, rn)
check("... once as a DM contact", sum(1 for n in rrc.reachable_nicks() if n.startswith('carol@')) == 1, rrc.reachable_nicks())
rrc_mesh._from_stump(S2, rrc_mesh._peers[S2], "[NAMES] guest-9999 -", title="#lora-mesh")
check("someone gone from macOS1's list: gone here at once (not an hour later)",
      'carol@macOS1' not in rrc.reachable_nicks() and 'guest-9999@macOS1' in rrc.reachable_nicks(), rrc.reachable_nicks())
roster = rrc_mesh._lora_roster()
check("this Stump's own list: Zed with his identity, alice (a browser) without one, no hidden app",
      ('Zed~' + ZED[:8]) in roster and 'alice' in roster and not any(n.startswith(guest) for n in roster), roster)

# ── Point 2: whichever side chose the name ─────────────────────────────
APP2 = '192.168.4.61'
i18n.set_lang(APP2, 'en')
other = Identity(create_keys=False)
other.load_private_key(bytes(range(1, 65)))
H2 = rrc_mesh.lxmf_address(other.hexhash)
rrc_mesh._reach[H2] = {"nick": "mesh-" + H2[:4], "last_seen": time.time(), "last_dm_id": 0, "hinted": False, "name": None}
rrc.set_reachable(H2, "mesh-" + H2[:4])
w = Wr(APP2)


def send2(text):
    w2 = Wr(APP2)
    hl = [b'POST /rrc/send HTTP/1.1\r\n', ('Content-Length: %d\r\n' % len(text)).encode(), b'\r\n']
    asyncio.run(barkeep._handle(Rd(hl, text.encode()), w2))
    return json.loads(b''.join(w2.w).partition(b'\r\n\r\n')[2])


r = send2('/auth')
nonce = [x for x in r['replies'] if x.startswith('AUTH-CHALLENGE ')][0].split()[1]
r = send2('/auth ' + hx(other.get_public_key()) + ' ' + hx(other.sign(nonce.encode())))
check("an app whose LoRa side has no name: /auth alone isn't a greeting (it still needs a name)",
      r.get('hello') == 'required', r)
r = send2('/nick Kim')
check("... /nick Kim: greeting done, and the LoRa side takes Kim (no mesh-xxxx left)",
      r.get('hello') == 'done' and rrc_mesh._reach[H2]['nick'] == 'Kim' and ('mesh-' + H2[:4]) not in everyone(),
      (r, rrc_mesh._reach[H2]))
# The phone renames itself in its app (a new announce): both follow.
rrc_mesh._is_person = lambda *a: True
rrc_mesh._note_stump_beacon = lambda *a: False


class Router:
    def _parse_display_name(self, data):
        return data.decode()


rrc_mesh.on_announce(Router(), ubinascii.unhexlify(H), b'Rodrigo', None)
check("the phone's new name announced: its Wi-Fi side follows, the old name gone everywhere",
      rrc.get_user(APP)['nick'] == 'Rodrigo' and 'RodCell' not in everyone() and everyone().count('Rodrigo') == 1
      and rrc.renames().get('RodCell') == 'Rodrigo', (rrc.get_user(APP), everyone()))

# ── The phone leaves the Wi-Fi ─────────────────────────────────────────
rrc._users[APP]['last_seen'] = time.time() - rrc.USER_TIMEOUT - 1
rrc._prune_users()
ok, _ = rrc.send_dm('bob', 'Rodrigo', 'still there?')
check("gone from the Wi-Fi: a DM waits for LoRa, not in a Wi-Fi inbox", ok and inbox(H)[-1:] == ['still there?']
      and 'still there?' not in inbox(APP), (inbox(H), inbox(APP)))
check("... and it's listed once (its LoRa side)", everyone().count('Rodrigo') == 1, everyone())

# ── /hello: the greeting as one exchange, both sides proving who they are ─
stump = Identity(create_keys=False)
stump.load_private_key(bytes(range(2, 66)))
rrc.NODE = {"name": "LaBuche", "lxmf": stump.hexhash}
rrc.NODE_IDENTITY[0] = stump
phone = Identity(create_keys=False)
phone.load_private_key(bytes(range(3, 67)))
H4 = rrc_mesh.lxmf_address(phone.hexhash)
rrc_mesh._reach[H4] = {"nick": "mesh-" + H4[:4], "last_seen": time.time(), "last_dm_id": 0, "hinted": False, "name": None}
rrc.set_reachable(H4, "mesh-" + H4[:4])
APP4 = '192.168.4.64'
i18n.set_lang(APP4, 'en')


def at(ip, path, body=None):
    w4 = Wr(ip)
    if body is None:
        hl = [('GET %s HTTP/1.1\r\n' % path).encode(), b'\r\n']
        body = b''
    else:
        hl = [('POST %s HTTP/1.1\r\n' % path).encode(), ('Content-Length: %d\r\n' % len(body)).encode(), b'\r\n']
    asyncio.run(barkeep._handle(Rd(hl, body), w4))
    raw = b''.join(w4.w)
    head, _, b = raw.partition(b'\r\n\r\n')
    return head.split(b'\r\n')[0][9:12].decode(), json.loads(b)


s, g = at(APP4, '/hello')
chk = Identity(create_keys=False)
chk.load_public_key(ubinascii.unhexlify(g['pub']))
check("/hello: the Stump's identity, a one-time code, its signature over code + LXMF address -- it checks out",
      s == '200' and g['lxmf'] == stump.hexhash and g['hello'] == 'required'
      and chk.validate(ubinascii.unhexlify(g['sig']), (g['nonce'] + g['lxmf']).encode()), g)
s, r = at(APP4, '/hello', json.dumps({"pub": hx(phone.get_public_key()), "sig": "00" * 64, "name": "Mia"}).encode())
check("... a wrong signature: refused, still not listed", s == '403' and r['hello'] == 'required'
      and APP4 in rrc._hidden and 'Mia' not in everyone(), (s, r))
s, g = at(APP4, '/hello')
good = json.dumps({"pub": hx(phone.get_public_key()), "sig": hx(phone.sign(g['nonce'].encode())),
                   "name": "Mia", "prefer": "lora"}).encode()
s, r = at(APP4, '/hello', good)
check("... the right one, with its name and 'prefer: lora': done in one exchange, one person 'Mia'",
      s == '200' and r['hello'] == 'done' and r['nick'] == 'Mia' and r['lxmf'] == H4 and r['prefer'] == 'lora'
      and everyone().count('Mia') == 1 and ('mesh-' + H4[:4]) not in everyone(), (r, everyone()))
s, r = at(APP4, '/hello', good)
check("... the same answer sent again (a replay): refused", s == '403', (s, r))
send('/msg Mia via the radio', B1)
check("it chose LoRa: a DM waits for LoRa even while it's on the Wi-Fi, not in its Wi-Fi inbox",
      inbox(H4) == ['via the radio'] and inbox(APP4) == [], (inbox(H4), inbox(APP4)))
import guard
guard.reset()            # (a while later: /hello is limited per address)
s, g = at(APP4, '/hello')
s, r = at(APP4, '/hello', json.dumps({"pub": hx(phone.get_public_key()), "sig": hx(phone.sign(g['nonce'].encode())),
                                      "prefer": "wifi"}).encode())
send('/msg Mia over wifi', B1)
check("switched to Wi-Fi: DMs come over Wi-Fi, still one Mia", r['prefer'] == 'wifi' and inbox(APP4)[-1:] == ['over wifi']
      and 'over wifi' not in inbox(H4) and everyone().count('Mia') == 1, (r, inbox(APP4), inbox(H4)))

codes = [at('192.168.4.77', '/hello')[0] for _ in range(10)]
check("/hello asked over and over: a few, then 'wait' (nobody keeps the CAM busy signing)",
      codes[:6] == ['200'] * 6 and '429' in codes[6:], codes)

# ── A logged-in profile and the gate off ───────────────────────────────
config.APP_HELLO = False
APP3 = '192.168.4.62'
w3 = Wr(APP3)
asyncio.run(barkeep._handle(Rd([b'GET /rrc/poll?room=main&since=0 HTTP/1.1\r\n', b'\r\n']), w3))
p3 = json.loads(b''.join(w3.w).partition(b'\r\n\r\n')[2])
check("APP_HELLO = False: an app reads and writes as before (no 'hello')", 'hello' not in p3 and 'messages' in p3, p3)
print('FAILURES:', FAIL)
