# One person on LoRa and Wi-Fi (Rodrigo's report, 19 Oct): a FireFly known
# on LoRa as "rods-cell" (its announce), its radio then off; a DM sent to
# rods-cell waits for LoRa. The same phone comes back over Wi-Fi as
# "RodRod" and proves its identity (/auth). From then on it's one person:
# listed once as RodRod, the waiting DM delivered over Wi-Fi, a DM to
# either name reaching it there, and the web chat told rods-cell is now
# RodRod (its thread folded). Once it leaves the Wi-Fi, RodRod is reached
# over LoRa again. And a LoRa contact announcing a new name is renamed.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config
config.AUTH_ADMIN_PASSWORD = 'pw'
import plugins
plugins.load_all()
import rrc, rrc_mesh, i18n, time
from urns.identity import Identity
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


ident = Identity(create_keys=False)
ident.load_private_key(bytes(range(64)))
H = rrc_mesh.lxmf_address(ident.hexhash)
check("an identity's LXMF address: what LXMF itself makes of it", len(H) == 32
      and H == __import__('ubinascii').hexlify(__import__('urns.destination', None, None, ['Destination']).Destination.hash(
          ident, "lxmf", "delivery")).decode(), H)

# The FireFly heard on LoRa (its announce), then its radio off.
rrc_mesh._reach[H] = {"nick": "rods-cell", "last_seen": time.time(), "last_dm_id": 0, "hinted": False, "name": "rods-cell"}
rrc.set_reachable(H, "rods-cell")
ALICE = '192.168.4.60'
rrc.touch_user(ALICE, nick='alice')
ok, to = rrc.send_dm('alice', 'rods-cell', 'are you there?')
check("a DM to rods-cell: waiting for LoRa", ok and to == 'rods-cell' and len(rrc.dms_since(H, 0)) == 1)

# The same phone over Wi-Fi as RodRod, then /auth (FireFly's one button).
WEB = '192.168.4.50'
i18n.set_lang(WEB, 'en')
rrc.touch_user(WEB, nick='RodRod')
check("before /auth: two people (nothing says they're one)",
      'rods-cell' in rrc.reachable_nicks() and rrc.find_client_by_nick('RodRod') == WEB)
r, _ = rrc.handle_input(WEB, 'main', '/auth')
nonce = [x for x in r if x.startswith('AUTH-CHALLENGE ')][0].split()[1]
sig = ident.sign(nonce.encode()).hex() if hasattr(ident.sign(b'x'), 'hex') else __import__('ubinascii').hexlify(ident.sign(nonce.encode())).decode()
pub = ident.get_public_key()
pub = pub.hex() if hasattr(pub, 'hex') else __import__('ubinascii').hexlify(pub).decode()
r, _ = rrc.handle_input(WEB, 'main', '/auth ' + pub + ' ' + sig)
check("/auth over Wi-Fi: verified", any(x.startswith('AUTH-OK') for x in r), r)
check("one person: rods-cell no longer listed, RodRod once",
      'rods-cell' not in rrc.reachable_nicks() and 'RodRod' not in rrc.reachable_nicks()
      and rrc.users_in_room('main').count('RodRod') == 1, (rrc.reachable_nicks(), rrc.users_in_room('main')))
check("... the DM that waited for LoRa: delivered over Wi-Fi instead",
      [m['body'] for m in rrc.dms_since(WEB, 0)] == ['are you there?'] and rrc.dms_since(H, 0) == [],
      (rrc.dms_since(WEB, 0), rrc.dms_since(H, 0)))
check("... the web chat is told rods-cell is RodRod now", rrc.renames() == {'rods-cell': 'RodRod'}, rrc.renames())
s, ct, b = req('/rrc/poll?room=main&since=0')
check("... in the poll", '"renamed"' in b and 'rods-cell' in b and 'RodRod' in b, b[:300])
ok, to = rrc.send_dm('alice', 'RodRod', 'got it')
check("a DM to RodRod: over Wi-Fi, where the phone is", ok and rrc.dms_since(WEB, 0)[-1]['body'] == 'got it'
      and rrc.dms_since(H, 0) == [])
check("rods-cell isn't anyone any more", rrc.find_client_by_nick('rods-cell') is None)
check("the name isn't 'taken' from itself (the LoRa side can be RodRod too)",
      not rrc.nick_taken('RodRod', H) and rrc.nick_taken('RodRod', ALICE))

# The phone leaves the Wi-Fi: RodRod is reached over LoRa again.
rrc._users[WEB]['last_seen'] = time.time() - rrc.USER_TIMEOUT - 1
rrc._prune_users()
check("gone from the Wi-Fi: RodRod is the LoRa contact again",
      rrc.find_client_by_nick('RodRod') == H and 'RodRod' in rrc.reachable_nicks(), rrc.reachable_nicks())
ok, to = rrc.send_dm('alice', 'RodRod', 'over the air')
check("... and a DM to RodRod waits for LoRa", ok and rrc.dms_since(H, 0)[-1]['body'] == 'over the air')

# A LoRa contact announcing a new name (a new nickname in its app).
class Router:
    def _parse_display_name(self, data):
        return data.decode()
rrc_mesh._is_person = lambda *a: True
rrc_mesh._note_stump_beacon = lambda *a: False
B = 'ab' * 16
rrc_mesh.on_announce(Router(), bytes.fromhex(B) if hasattr(bytes, 'fromhex') else __import__('ubinascii').unhexlify(B), b'Bob', None)
check("a LoRa contact: listed by its announced name", 'Bob' in rrc.reachable_nicks())
rrc_mesh.on_announce(Router(), __import__('ubinascii').unhexlify(B), b'Bob', None)
check("... the same announce again: nothing changes", 'Bob' in rrc.reachable_nicks() and 'Bob' not in rrc.renames())
rrc_mesh.on_announce(Router(), __import__('ubinascii').unhexlify(B), b'Robert', None)
check("... it announces a new name: renamed, the old one gone",
      'Robert' in rrc.reachable_nicks() and 'Bob' not in rrc.reachable_nicks() and rrc.renames().get('Bob') == 'Robert',
      (rrc.reachable_nicks(), rrc.renames()))
s, ct, b = req('/rrc')
check("the web chat folds a renamed person's thread (the page knows how)", 'followRenames(d.renamed)' in b, s)
print('FAILURES:', FAIL)
