# Packed text (urns/ztext.py): a chat line written in fewer bytes for those
# who say they read it, so more of it goes in each LoRa packet. The format
# is fixed (its tables' hash, messages packed once and for all); what the
# radio brings is read by MicroPython's own inflate, whatever it is; the
# router packs only for a peer whose announce says "z", and reads it back.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import os
import uhashlib, ubinascii
import rrc, rrc_mesh
from urns import ztext, lxmf, umsgpack
from urns.identity import Identity
from urns.destination import Destination
rrc.LORA_NONE[0] = False
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


# The format never changes: these are what every FireFly and Stump holds.
h = uhashlib.sha256(ztext._DICT + ztext._LL.encode() + ztext._DL.encode() + ztext._HEAD).digest()
check("the format's tables are version 1's", ubinascii.hexlify(h).decode() == "6ef2028f82238d884eae1b29f9fd270f3c2a036fae07421835d5427d4c120bd5")
check("this MicroPython can read it", ztext.available())

VECTORS = [
    ('<alice> hey is anyone around the north trail tonight?',
     "ff09ab44ce53c2a5860a878def14caf7987e4fc38a763b75c301"),
    ("<Rod@LaBuche> on se rejoint au camp à 18h, apporte les piles et de l'eau\n<Luc@Camp2> oui bien reçu, 5 sur 5",
     "ffc958159b19bb69f3194fd11c79f0c605829b85a55cbc40abd76611b1a6f1380abd6d043e7665f00a67f6fb2699cd37b758dd0c525256047ef12813d1655fe15c76"),
    ('<maria> hola, ¿alguien me copia? estoy cerca del campamento',
     "ff49cdb460a987b4885efcfa69aac5f63682dde8a827906981e4734aa10e"),
    ('[NAMES] Rod alice bob guest-3f2a maria Luc@Camp2 jean-guy mesh-fd92',
     "ffcbc31fab825885459faa69f7abac950314645a30ca6cbeb9c5ea6640879cc9fe819d87dfb32cd6070e"),
    ('→ #lora-mesh',
     "fffee79d5e2007"),
]
for text, packed in VECTORS:
    data = text.encode()
    z = ztext.pack(data)
    check("packs as it always did: " + text[:24], ubinascii.hexlify(z).decode() == packed, ubinascii.hexlify(z))
    check("... shorter (%d -> %d), and read back" % (len(data), len(z)), len(z) < len(data) and ztext.unpack(z) == data)

help_fr = "\n".join(rrc.help_lines("fr", mesh=True)).encode()
z = ztext.pack(help_fr)
check("/help (%d bytes) fits one LoRa packet packed (%d)" % (len(help_fr), len(z)),
      len(help_fr) > 295 and len(z) < 200 and ztext.unpack(z) == help_fr, len(z))

# Never longer than what it was given, bar the one byte that keeps a
# content starting with 0xFE or 0xFF unambiguous.
check("too short to bother: as it is", ztext.pack(b"ok") == b"ok" and ztext.unpack(b"ok") == b"ok")
check("nothing: nothing", ztext.pack(b"") == b"" and ztext.unpack(b"") == b"")
rnd = bytes(b & 0x7f for b in os.urandom(300))
check("what doesn't pack goes as it is", ztext.pack(rnd) == rnd)
for odd in (b"\xff", b"\xfe\x01\x02", b"\xff" + rnd):
    z = ztext.pack(odd)
    check("a content starting 0x%02x: marked, read back" % odd[0], z[0] >= ztext.PLAIN and len(z) <= len(odd) + 1
          and ztext.unpack(z) == odd, z[:4])
big = b"a line said again and again. " * 200
check("longer than MAX: not packed", len(big) > ztext.MAX and ztext.pack(big) == big)
edge = (b"x" * ztext.MAX)
check("MAX bytes: packed and read back", ztext.unpack(ztext.pack(edge)) == edge and len(ztext.pack(edge)) < 64)

# What the radio brings: anything. Read by the platform's inflate, never
# more than MAX bytes out, never an exception.
bomb = b"\xff" + bytes(ztext._deflate(b"x" * ztext.MAX))[:-2] + b"\x00" * 40
bad = 0
for i in range(400):
    junk = b"\xff" + os.urandom(i % 97)
    try:
        r = ztext.unpack(junk)
        if not (r is None or (isinstance(r, bytes) and len(r) <= ztext.MAX)):
            bad += 1
    except Exception:
        bad += 1
check("400 random packed contents: None or at most MAX bytes, no exception", bad == 0, bad)
z = ztext.pack(VECTORS[0][0].encode())
check("cut short: None", all(ztext.unpack(z[:n]) is None for n in range(1, len(z) - 1)))
r = ztext.unpack(bomb)
check("made to unpack past MAX: None, or cut at MAX", r is None or len(r) <= ztext.MAX)

# The router: who reads it (their announce), what goes to them.
me, them, other = Identity(), Identity(), Identity()
r = lxmf.LXMRouter(identity=me)
r.register_delivery_identity(me, display_name="Stump")
mine = umsgpack.unpackb(r._get_announce_app_data())
check("this Stump's announce says it reads packed text, after LXMF's three items and no app tag",
      mine[:3] == [b"Stump", None, []] and mine[3] is None and mine[4] == {"z": 1}, mine)
check("... its announce still reads as an LXMF one (name, no stamp cost), not as a Stump app's",
      r._parse_display_name(r._get_announce_app_data()) == "Stump"
      and lxmf.stamp_cost_from_app_data(r._get_announce_app_data()) is None
      and not rrc.app_tag_in(r._get_announce_app_data()))
check("... and its own announce reads as one that does", lxmf.reads_packed(r._get_announce_app_data()))
A = Destination(them, Destination.OUT, Destination.SINGLE, "lxmf", "delivery").hash
B = Destination(other, Destination.OUT, Destination.SINGLE, "lxmf", "delivery").hash
r.handle_announce(A, umsgpack.packb([b"Rod", None, [], "stump:firefly-android/0.6.3.b.74", {"z": 1}]))
r.handle_announce(B, umsgpack.packb([b"Sideband", None, [0]]))
check("FireFly's announce with z: packed for it", r.packs_for(A))
check("an LXMF app's without: plain", not r.packs_for(B))
for odd in (b"Rod", umsgpack.packb([b"Rod", None, [], "stump:x"]), umsgpack.packb([b"Rod", None, [], None, "z"]),
            umsgpack.packb([b"Rod", None, [], None, {"z": True}]), umsgpack.packb([b"Rod", None, [], None, {"z": 0}]), b"", b"\x95"):
    check("not a z announce: %r" % odd[:12], not lxmf.reads_packed(odd))
text = VECTORS[1][0]
check("content to FireFly: packed", r._packed(A, text)[0] == ztext.PACKED and len(r._packed(A, text)) < len(text.encode()))
check("content to the other: as given", r._packed(B, text) == text)
check("a short line to FireFly: as it is", r._packed(A, "ok") == b"ok")
check("its size as sent", r.packed_size(A, text) == len(ztext.pack(text.encode())) and r.packed_size(B, text) == len(text.encode()))


class Msg:
    def __init__(self, content):
        self.content = content


m = Msg(r._packed(A, text))
r._unpack(m)
check("a packed message in: its text", m.content == text.encode())
m = Msg(b"plain text")
r._unpack(m)
check("a plain one: untouched", m.content == b"plain text")
m = Msg(b"\xff\x00\x01 not packed at all")
r._unpack(m)
check("one that can't be read: as it came", m.content == b"\xff\x00\x01 not packed at all")
r.handle_announce(A, umsgpack.packb([b"Rod", None, [], "stump:firefly-android/0.6.2.b.73"]))
check("its app without z again (an older build): plain again", not r.packs_for(A))
for i in range(lxmf.PACKS_MAX + 20):
    r.handle_announce(bytes([i & 255, i >> 8]) + b"\x00" * 14, umsgpack.packb([b"n", None, [], None, {"z": 1}]))
check("only so many remembered", len(r.packs) == lxmf.PACKS_MAX, len(r.packs))

# Switched off (config.LXMF_PACKED_TEXT = False): nothing said, packed or read.
lxmf.PACKED_TEXT[0] = False
r.handle_announce(A, umsgpack.packb([b"Rod", None, [], "stump:firefly-android/0.6.3.b.74", {"z": 1}]))
off = umsgpack.unpackb(r._get_announce_app_data())
m = Msg(ztext.pack(text.encode()))
r._unpack(m)
check("off: the announce is LXMF's three items", off == [b"Stump", None, []], off)
check("off: nothing packed, nothing unpacked", r._packed(A, text) == text and m.content[0] == ztext.PACKED)
lxmf.PACKED_TEXT[0] = True

# #lora-mesh between Stumps: more lines in a radio piece of the same size.
ls = ["<nick%d> the radio is slow so keep it short please, over" % i for i in range(12)]
plain = rrc_mesh._radio_pieces(ls)
size = rrc_mesh._packed_size(r, [A])
packed = rrc_mesh._radio_pieces(ls, size=size)
check("to a Stump that reads it: fewer radio packets (%d -> %d)" % (len(plain), len(packed)),
      size is not None and len(packed) < len(plain), (len(plain), len(packed)))
check("... each still within a radio piece as sent", all(size(t.encode()) <= rrc_mesh.RADIO_CHUNK for t in packed))
check("... the same lines, in order", "\n".join(packed) == "\n".join(ls))
check("to one that doesn't: pieces as before", rrc_mesh._packed_size(r, [B]) is None and rrc_mesh._packed_size(r, [A, B]) is None)
names = "[NAMES] " + " ".join("visitor%d~Stump" % i for i in range(30))
one = rrc_mesh._radio_pieces([names], parts=True, size=size)
check("a long names list that packs into one piece: whole", one == [names], len(one))
long = "<bob> " + "".join(chr(97 + b % 26) for b in os.urandom(700))
cut = rrc_mesh._radio_pieces([long], size=size)
check("a line too long even packed: cut as before, each piece with its <nick>",
      len(cut) > 1 and all(l.startswith("<bob> ") for t in cut for l in t.split("\n")))

# The radio broadcast, packed (b"lb2") when every Stump it is for reads it.
class Dest:
    hash = bytes.fromhex("aa" * 16)


class Router:
    peers = {}
    delivery_destination = Dest()

    def send_message(self, *a, **k):
        pass


rrc.reset()
rrc_mesh._lb["router"] = Router()
gear = Identity()
G = "6e" * 16
Identity.remember(b"\x00" * 32, bytes.fromhex(G), gear.get_public_key())
rrc.mark_stump(G)
boot = b"\x01\x02\x03\x04"


def lb2(seq, body, ident=gear):
    sig = ident.sign(rrc_mesh._lb_signed(bytes.fromhex(G), boot, seq, body, b"lb2"))
    return umsgpack.packb([b"lb2", bytes.fromhex(G), boot, seq, body, sig])


def lines():
    return [m["body"] for m in rrc.since(rrc.MESH_ROOM, 0) if m.get("kind") != "system"]


two = "<ann> hello from Gearn, packed\n<ann> and a second line with it"
rrc_mesh._lb_in(lb2(1, ztext.pack(two.encode())), None)
check("a packed broadcast: its lines", lines() == ["hello from Gearn, packed", "and a second line with it"], lines())
rrc_mesh._lb_in(lb2(2, ztext.pack(b"<ann> forged, this one is, by somebody else"), Identity()), None)
rrc_mesh._lb_in(lb2(3, b"\xff\x00garbage"), None)
rrc_mesh._lb_in(lb2(1, ztext.pack(two.encode())), None)
check("forged, unreadable, played again: nothing more", len(lines()) == 2, lines())
sig = gear.sign(rrc_mesh._lb_signed(bytes.fromhex(G), boot, 4, "<ann> plain as before"))
rrc_mesh._lb_in(umsgpack.packb([b"lb1", bytes.fromhex(G), boot, 4, "<ann> plain as before", sig]), None)
check("a plain broadcast (b\"lb1\"): as before", lines()[-1] == "plain as before", lines())

print("FAILURES:", FAIL)
