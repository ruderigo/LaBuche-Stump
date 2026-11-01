# #lora-mesh (once #lxmf) and #wifi-mesh: the other Stumps of a chain land in
# #wifi-mesh, are taken in from their beacon alone, and only chat lines go
# between Stumps -- each Stump's own people's, as "<nick@stump>" on the
# other side, never relayed twice, never joins and parts.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, rrc_mesh, i18n
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


# the rename
rrc.reset()
check("#lora-mesh is the mesh room", rrc.MESH_ROOM == "lora-mesh" and rrc.room_exists("lora-mesh"))
check("#lxmf still reaches it", rrc.room_exists("lxmf") and rrc.create_room("#lxmf") == ("lora-mesh", None)
      and rrc.clean_room("LXMF") == "lora-mesh")
W = '192.168.4.50'
i18n.set_lang(W, 'en')
rrc.touch_user(W, nick='bob', room='main')
r, room = rrc.handle_input(W, 'main', '/join lxmf')
check("/join lxmf lands in #lora-mesh", room == "lora-mesh", (r, room))
check("no #wifi-mesh outside a chain", not rrc.room_exists("wifi-mesh"))
rrc.post("lxmf", "bob", "hi")
check("a line to #lxmf is in #lora-mesh", rrc.since("lora-mesh", 0)[-1]["body"] == "hi")

# a chain
rrc.open_chain_room()
check("its afternoon names still work: #lora, #mesh", rrc.clean_room("lora") == "lora-mesh" and rrc.clean_room("#mesh") == "wifi-mesh")
check("#chained (its first name) still reaches #wifi-mesh", rrc.room_exists("wifi-mesh") and rrc.clean_room("chained") == "wifi-mesh")
check("#wifi-mesh once in a chain", rrc.room_exists("wifi-mesh") and "Stumps" in rrc.topic("wifi-mesh", "en"))


class ChainHubInterface:
    pass


class LoRaInterface:
    pass


from urns.transport import Transport
from urns import const
LAPTOP = 'ab' * 16
FAR = 'ef' * 16          # a Stump heard over LoRa only
PHONE = 'cd' * 16


def path(h, iface):
    e = [0, b'', 1, 0, iface, None, 0]
    Transport.path_table[bytes.fromhex(h)] = e


path(LAPTOP, ChainHubInterface())
path(FAR, LoRaInterface())
path(PHONE, ChainHubInterface())
rrc.mark_stump(LAPTOP)
rrc.mark_stump(FAR)
sent = []


class Router:
    peers = {bytes.fromhex(LAPTOP): {"name": "Laptop"}}
    delivery_destination = None

    def send_message(self, dest, text, title="", fields=None):
        sent.append((bytes(dest).hex(), text, title))


R = Router()
rrc_mesh._take_in_chain_stumps(R)
p = rrc_mesh._peers.get(LAPTOP)
check("a Stump on the chain is taken in from its beacon, into #wifi-mesh", p and p["room"] == "wifi-mesh"
      and p["stump"] and p["nick"] == "Laptop", p)
check("its arrival shows in #wifi-mesh", any("~Laptop" in m["body"] for m in rrc.since("wifi-mesh", 0)))
check("a Stump heard over LoRa only isn't", FAR not in rrc_mesh._peers)
check("a phone isn't a Stump: not taken in", PHONE not in rrc_mesh._peers)

# out: only local chat lines
rrc.touch_user(W, nick='bob', room='wifi-mesh')
rrc.post("wifi-mesh", "bob", "hello chain")
rrc.post("wifi-mesh", "bob", "waves", kind="action")
rrc.system("wifi-mesh", rrc.ev_join("carol"))
rrc_mesh._flush_to_peer(R, LAPTOP, p)
batch = rrc_mesh._send_queue[-1]
text = batch[2]
check("to the other Stump: the chat lines", "<bob> hello chain" in text and "* bob waves" in text, text)
check("... not the notices", "✓" not in text and "carol" not in text, text)
check("... titled #wifi-mesh", batch[3] == "#wifi-mesh", batch)
del rrc_mesh._send_queue[:]


# in: a batch from the laptop
class Msg:
    source_hash = bytes.fromhex(LAPTOP)
    signature_validated = True
    fields = {}

    def __init__(self, t):
        self.t = t

    def content_as_string(self):
        return self.t

    def title_as_string(self):
        return getattr(self, "title", "")


before = len(rrc.since("wifi-mesh", 0))
rrc_mesh.on_message(R, Msg("<macOS> hello\n✓ ~LaBuche\n* macOS smiles\n✎ guest-b57d → macOS"))
new = rrc.since("wifi-mesh", 0)[before:]
check("each line on its own, as nick@stump", [(m["nick"], m["body"], m["kind"]) for m in new]
      == [("macOS@Laptop", "hello", "msg"), ("macOS@Laptop", "smiles", "action")], new)
check("no greeting or 'moved' sent back to a Stump", rrc_mesh._send_queue == [], rrc_mesh._send_queue)
check("#lora-mesh stays quiet", not any("macOS" in m["nick"] for m in rrc.since("lora-mesh", 0)))
rrc_mesh._flush_to_peer(R, LAPTOP, p)
check("relayed lines never go back", rrc_mesh._send_queue == [], rrc_mesh._send_queue)

# a second Stump: it gets bob's lines, not the laptop's (it hears those itself)
OTHER = '12' * 16
path(OTHER, ChainHubInterface())
rrc.mark_stump(OTHER)
rrc_mesh._take_in_chain_stumps(R)
q = rrc_mesh._peers[OTHER]
rrc.post("wifi-mesh", "bob", "to everyone")
rrc_mesh._flush_to_peer(R, OTHER, q)
text = rrc_mesh._send_queue[-1][2] if rrc_mesh._send_queue else ""
check("another Stump: local lines only", "<bob> to everyone" in text and "macOS" not in text, text)
del rrc_mesh._send_queue[:]

# a phone on LoRa still lands in #lora-mesh, and gets the room's notices
del Transport.path_table[bytes.fromhex(PHONE)]
peer = rrc_mesh._get_peer(PHONE, "Rod")
check("a LoRa user lands in #lora-mesh", peer["room"] == "lora-mesh" and not peer["stump"], peer)

# a Stump that talked before its path was known moves to #wifi-mesh
LATE = '34' * 16
peer = rrc_mesh._get_peer(LATE, "Late")
check("unknown yet: #lora-mesh", peer["room"] == "lora-mesh")
rrc.mark_stump(LATE)
path(LATE, ChainHubInterface())
rrc_mesh._take_in_chain_stumps(R)
check("then moved to #wifi-mesh", rrc_mesh._peers[LATE]["room"] == "wifi-mesh" and rrc_mesh._peers[LATE]["stump"])

# A Stump whose beacon wasn't heard (it comes every half hour): known by
# what it sends -- a room batch titled "#wifi-mesh" -- and moved there.
NEW = '56' * 16
path(NEW, ChainHubInterface())
m = Msg("<ann> hi all")
m.source_hash = bytes.fromhex(NEW)
m.title = "#wifi-mesh"
rrc_mesh.on_message(R, m)
check("no beacon: a chain Stump is known by its #wifi-mesh batch", rrc_mesh._peers[NEW]["stump"]
      and rrc_mesh._peers[NEW]["room"] == "wifi-mesh" and rrc.is_stump(NEW), rrc_mesh._peers[NEW])
check("... and its line shows as ann@<it>", any(x["body"] == "hi all" and x["nick"].startswith("ann@")
                                                for x in rrc.since("wifi-mesh", 0)))
check("... not in #lora-mesh", not any("hi all" in x["body"] for x in rrc.since("lora-mesh", 0)))
del rrc_mesh._send_queue[:]

# Someone on the chain who isn't a Stump (Sideband over the chain): #wifi-mesh too
PERSON = '78' * 16
path(PERSON, ChainHubInterface())
peer = rrc_mesh._get_peer(PERSON, "Sid")
check("a person over the chain lands in #wifi-mesh, not #lora-mesh", peer["room"] == "wifi-mesh" and not peer["stump"], peer)
del rrc_mesh._send_queue[:]

# DMs between people on two Stumps
rrc.NODE = {"name": "LaBuche", "lxmf": "00" * 16}
rrc_mesh.on_message(R, Msg("<rod> anyone?"))
check("people on the other Stump become DM contacts as rod@Laptop", "rod@Laptop" in rrc.reachable_nicks(),
      rrc.reachable_nicks())
r, _ = rrc.handle_input(W, 'wifi-mesh', '/msg rod@Laptop see you at 6')
p = rrc_mesh._peers[LAPTOP]
rrc_mesh._flush_to_peer(R, LAPTOP, p)
text = rrc_mesh._send_queue[-1][2] if rrc_mesh._send_queue else ""
check("/msg rod@Laptop goes to the laptop Stump as a DM line", "[DM>rod] <bob> see you at 6" in text, (r, text))
check("... and not into #wifi-mesh", not any("see you" in x["body"] for x in rrc.since("wifi-mesh", 0)))
del rrc_mesh._send_queue[:]
rrc_mesh._flush_to_peer(R, LAPTOP, p)
check("... once", rrc_mesh._send_queue == [], rrc_mesh._send_queue)
rrc_mesh.on_message(R, Msg("[DM>bob] <rod> ok, 6 it is"))
got = rrc.dms_since(W, 0)
check("a DM from rod on the laptop reaches bob here, from rod@Laptop", any(d["nick"] == "rod@Laptop" and d["body"] == "ok, 6 it is"
                                                                       for d in got), got)
check("... privately: not in #wifi-mesh", not any("6 it is" in x["body"] for x in rrc.since("wifi-mesh", 0)))
rrc_mesh.on_message(R, Msg("[DM>nobody] <rod> hello?"))
q = rrc_mesh._send_queue[-1][2] if rrc_mesh._send_queue else ""
check("a DM to no one here: the sender is told", q == "[DM>rod] <LaBuche> ⊖ nobody", rrc_mesh._send_queue)
del rrc_mesh._send_queue[:]
rrc_mesh.on_message(R, Msg("[DM] <rod>: hey LaBuche"))
check("a DM to this Stump itself shows in #wifi-mesh, addressed to it", any(x["nick"] == "rod@Laptop" and x["body"] == "@LaBuche hey LaBuche"
                                                                         for x in rrc.since("wifi-mesh", 0)))
check("... never in #lora-mesh", not any("hey LaBuche" in x["body"] for x in rrc.since("lora-mesh", 0)))

# No announce heard yet (no path): the link the message came in on decides
EARLY = '9a' * 16
m = Msg("hello?")
m.source_hash = bytes.fromhex(EARLY)
m.receiving_interface = ChainHubInterface()
rrc_mesh.on_message(R, m)
check("no path yet: a message that came over the chain lands in #wifi-mesh", rrc_mesh._peers[EARLY]["room"] == "wifi-mesh",
      rrc_mesh._peers[EARLY])
LORA = 'bc' * 16
m = Msg("hi from the radio")
m.source_hash = bytes.fromhex(LORA)
m.receiving_interface = LoRaInterface()
rrc_mesh.on_message(R, m)
check("... and one that came over LoRa in #lora-mesh", rrc_mesh._peers[LORA]["room"] == "lora-mesh")
del rrc_mesh._send_queue[:]
print('FAILURES:', FAIL)
