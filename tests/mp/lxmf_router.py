# The LXMF router's way in (urns/lxmf.py), after the review of Nov 2026:
#  - a message with a stamp keeps its sender's id on a board: the id is
#    made from the bytes received, not from the payload packed again (a
#    board's floats are 32 bits; the timestamp didn't survive, and every
#    stamped message was dropped as forged);
#  - what it remembers is bounded: the ids of messages taken in and the
#    names learned from announces grew with every message and every new
#    address, checked or not, until the board ran out of memory;
#  - a packet, a link packet and a resource are taken in by the same code:
#    forged dropped, delivered once, a packet proved (again when repeated).
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import os
from urns import lxmf, umsgpack
from urns.identity import Identity
from urns.destination import Destination
from urns.crypto.hashes import sha256
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:300]))
    if not c:
        FAIL.append(n)


me, them = Identity(), Identity()
r = lxmf.LXMRouter(identity=me)
mine = r.register_delivery_identity(me, display_name="Stump")
src = Destination(them, Destination.OUT, Destination.SINGLE, "lxmf", "delivery")
Identity.remember(b"\x00" * 32, src.hash, them.get_public_key())
got = []
r.register_delivery_callback(got.append)


class Packet:
    receiving_interface = None

    def __init__(self):
        self.proved = 0

    def prove(self):
        self.proved += 1


# 1. A timestamp no 32-bit float holds, as a phone or a computer writes it
#    (1794750955.7723 as a double), signed over those bytes, with a stamp.
TS = bytes.fromhex("41dabe6e7af16d5d")
four = b"\x94\xcb" + TS + umsgpack.packb(b"")[0:] + umsgpack.packb(b"stamped, from a phone") + b"\x80"
check("(the timestamp doesn't survive this MicroPython's floats: the test means something)",
      umsgpack.packb(umsgpack.unpackb(four)) != four or 1 / 3 != 0.33333334)
hashed = mine.hash + src.hash + four
sig = them.sign(hashed + sha256(hashed))
stamp = os.urandom(32)
wire = mine.hash + src.hash + sig + b"\x95" + four[1:] + umsgpack.packb(stamp)
m = lxmf.LXMessage.unpack_from_bytes(wire)
check("a stamped message: its signature checks out", m.signature_validated, m.unverified_reason)
check("... its id is its sender's", m.hash == sha256(hashed))
check("... its text read", m.content == b"stamped, from a phone")
plain = lxmf.LXMessage.unpack_from_bytes(mine.hash + src.hash + sig + four)
check("the same without a stamp: the same id, as before", plain.signature_validated and plain.hash == m.hash)
forged = lxmf.LXMessage.unpack_from_bytes(mine.hash + src.hash + bytes(64) + b"\x95" + four[1:] + umsgpack.packb(stamp))
check("a forged one is still forged", not forged.signature_validated
      and forged.unverified_reason == lxmf.LXMessage.SIGNATURE_INVALID)
for bad in (b"\x95\x01\x02", b"\xc0", b"\x91\x01", b"\xdc\x00\x05\x01"):
    try:
        lxmf._first_four(bad)
        ok = False
    except Exception:
        ok = True
    check("a payload cut short or of another shape: refused (%r)" % bad[:4], ok)


# 2. One way in. From an address not heard yet (no key to check with), as
#    an open Stump takes them: cheap to make by the thousand.
def raw(i, src_hash=None):
    payload = umsgpack.packb([1794750000 + i, b"", b"line %d" % i, {}])
    return (src_hash or os.urandom(16)) + bytes(64) + payload


p = Packet()
one = raw(1)
r._delivery_packet(one, p)
check("a packet: delivered, proved", len(got) == 1 and p.proved == 1 and got[0].content == b"line 1", (len(got), p.proved))
p2 = Packet()
r._delivery_packet(one, p2)
check("the same again (our proof was lost): proved again, not delivered twice", len(got) == 1 and p2.proved == 1)
r._link_packet_received(mine.hash + raw(2), Packet())
check("over a link: delivered", len(got) == 2 and got[1].content == b"line 2")
r._link_packet_received(mine.hash + one, Packet())
check("what came as a packet, again over a link: not delivered twice", len(got) == 2)


class Resource:
    status = __import__("urns.resource").resource.COMPLETE
    request_id = None
    link = None

    def __init__(self, data):
        self.data = data


r._handle_resource_concluded(Resource(mine.hash + raw(3)))
check("as a resource: delivered", len(got) == 3 and got[2].content == b"line 3")
r._handle_resource_concluded(Resource(mine.hash + raw(3)[:16] + bytes(64) + umsgpack.packb([1, b"", b"x", {}])))
n = len(got)
p3 = Packet()
r._delivery_packet(src.hash + bytes(64) + four, p3)
check("a wrong signature from a known address: dropped, not proved", len(got) == n and p3.proved == 0, (len(got), n, p3.proved))
r._link_packet_received(mine.hash + src.hash + bytes(64) + four, Packet())
r._handle_resource_concluded(Resource(mine.hash + src.hash + bytes(64) + four))
check("... dropped over a link and as a resource too", len(got) == n, (len(got), n))
r._delivery_packet(src.hash + sig + four, Packet())
check("... and the same message rightly signed is delivered", len(got) == n + 1 and got[-1].signature_validated)
check("every message says how it came in", all(getattr(g, "transport_encrypted", False) for g in got))

# 3. Bounded. (Counted, not waited for: the caps are the point.)
lxmf.DELIVERED_MAX = 50
first = raw(1000)
r._delivery_packet(first, Packet())
for i in range(1001, 1200):
    r._delivery_packet(raw(i), Packet())
check("ids of messages taken in: held at the cap", len(r.delivered_ids) <= 50, len(r.delivered_ids))
last = raw(5000)
r._delivery_packet(last, Packet())
n = len(got)
r._delivery_packet(last, Packet())
check("... a recent one is still known (not delivered twice)", len(got) == n)
r.MESSAGE_EXPIRY = -1
r._cleaned_at = 0
r._remember(b"z" * 32)
check("... expired ones go", list(r.delivered_ids) == [b"z" * 32], len(r.delivered_ids))

lxmf.PEERS_MAX = 40
r.handle_announce(src.hash, umsgpack.packb([b"Rod", None, []]))
import time
for i in range(300):
    r.handle_announce(bytes([i & 255, i >> 8]) + bytes(14), umsgpack.packb([b"n%d" % i, None, []]))
check("names from announces: held at the cap", len(r.peers) <= 40, len(r.peers))
check("... the newest kept", r.peers.get(bytes([299 & 255, 299 >> 8]) + bytes(14), {}).get("name") == "n299")
r.handle_announce(src.hash, umsgpack.packb([b"Rod", None, []]))
check("... someone announcing again is known again", r.peers[src.hash]["name"] == "Rod")

# 4. The announce: made in one place, with the stamp cost asked for.
r2 = lxmf.LXMRouter(identity=Identity())
d2 = r2.register_delivery_identity(r2.identity, display_name="Stump", stamp_cost=8)
check("a stamp cost asked for is in every announce (it was in the first only)",
      umsgpack.unpackb(r2._get_announce_app_data())[:3] == [b"Stump", 8, []]
      and umsgpack.unpackb(d2.default_app_data)[:3] == [b"Stump", 8, []], umsgpack.unpackb(r2._get_announce_app_data()))
check("none asked: None, as before", umsgpack.unpackb(r._get_announce_app_data())[:3] == [b"Stump", None, []])

print("FAILURES:", FAIL)
