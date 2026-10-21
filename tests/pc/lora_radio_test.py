# #lora-mesh between Stumps goes over the radio, even when Wi-Fi (the
# chain) reaches the other Stump too -- with the real firmware (urns, LXMF,
# rrc_mesh) on regular Python: a radio and a chain interface, an open link
# and a two-hop path over the chain, and a #lora-mesh line for the other
# Stump. Only the radio transmits it, as one plain packet straight to that
# Stump (not "via" the chain's next hop); without a radio online, it goes
# the usual way. Run: python3 tests/pc/lora_radio_test.py
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))
from urns.log import set_loglevel, LOG_NONE
set_loglevel(LOG_NONE)
from urns import const
from urns.identity import Identity
from urns.destination import Destination
from urns.transport import Transport
from urns.interfaces import Interface
from urns.lxmf import LXMRouter
import rrc, rrc_mesh

FAIL = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:500]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


class Fake(Interface):
    HW_MTU = 564

    def __init__(self, name):
        super().__init__(name)
        self.online = True
        self.sent = []

    def process_outgoing(self, data):
        self.sent.append(bytes(data))
        return True


class RNodeSerialInterface(Fake):      # radio.bridges() knows radios by class name
    pass


class ChainUplinkInterface(Fake):
    pass


radio_if, chain_if = RNodeSerialInterface("Heltec (USB)"), ChainUplinkInterface("chain")
Transport.interfaces[:] = [chain_if, radio_if]

# This Stump, and the other one (LaBuche): its keys known, its path two
# hops away over the chain.
me = LXMRouter(identity=Identity())
me.register_delivery_identity(me.identity, display_name="macOS")
other_id = Identity()
other = Destination(other_id, Destination.OUT, Destination.SINGLE, "lxmf", "delivery")
Identity.remember(b"\x00" * 32, other.hash, other_id.get_public_key())
Transport.path_table[other.hash] = [time.time(), b"\x11" * 16, 2, time.time() + 3600, chain_if, None, time.time()]
# An open link over the chain would carry a plain send: the radio send
# must not care.
me._find_active_link_for = lambda h: object()

msg = me.send_message(other.hash, "<rod> over the radio", title="#lora-mesh", via=radio_if)
check("sent as one packet", msg is not None and msg.method == msg.OPPORTUNISTIC, msg)
check("only the radio transmits it", len(radio_if.sent) == 1 and not chain_if.sent,
      (len(radio_if.sent), len(chain_if.sent)))
hdr = radio_if.sent[0][0] if radio_if.sent else 0
check("straight to that Stump (plain header), not via the chain's next hop",
      (hdr >> 6) & 0x01 == 0 and b"\x11" * 16 not in radio_if.sent[0][:40], hex(hdr))
big = me.send_message(other.hash, "x" * 600, title="#lora-mesh", via=radio_if)
check("too big for one packet: refused, not sent another way", big is None and len(radio_if.sent) == 1
      and not chain_if.sent)

# rrc_mesh: the lines cut into single packets, queued for the radio.
lines = ["<rod> short one", "<ana> " + "é" * 300, "* rod waves"]
pieces = rrc_mesh._radio_pieces(lines)
check("pieces: none over the limit, every piece keeps its <nick>",
      all(len(p.encode()) <= rrc_mesh.RADIO_CHUNK for p in pieces)
      and all(l.startswith(("<rod>", "<ana>", "* rod")) for p in pieces for l in p.split("\n")), pieces)
check("pieces: nothing lost", "".join(l.split("> ", 1)[1] for p in pieces for l in p.split("\n")
                                      if l.startswith("<ana>")) == "é" * 300)
del rrc_mesh._send_queue[:]
rrc_mesh._queue_lora(me, other.hash.hex(), lines)
q = list(rrc_mesh._send_queue)
check("with the radio online: queued for the radio, titled #lora-mesh", q and all(len(i) == 6 and i[5] is radio_if
                                                                                 and i[3] == "#lora-mesh" for i in q), q)
radio_if.sent.clear()
for item in q:
    rrc_mesh._send_item(item)
check("sent: on the radio only", len(radio_if.sent) == len(q) and not chain_if.sent)
del rrc_mesh._send_queue[:]
radio_if.online = False
rrc_mesh._queue_lora(me, other.hash.hex(), lines)
q = list(rrc_mesh._send_queue)
check("without a radio online: the usual way (the chain)", len(q) == 1 and len(q[0]) == 4, q)
del rrc_mesh._send_queue[:]

# #wifi-mesh stays on Wi-Fi: a short batch to a chain Stump goes on the
# chain interface only, not the radio as well.
radio_if.online = True
radio_if.sent.clear()
chain_if.sent.clear()
h = other.hash.hex()
rrc.mark_stump(h)
rrc.create_room(rrc.CHAIN_ROOM)
rrc_mesh._peers[h] = {"nick": "LaBuche", "room": rrc.CHAIN_ROOM, "home_room": rrc.CHAIN_ROOM, "stump": True,
                      "last_seen": time.time(), "last_sent_id": 0, "lora_sent_id": 10 ** 9, "last_dm_id": 0}
rrc.touch_user("127.0.0.1", nick="rod", room=rrc.CHAIN_ROOM)
rrc.post(rrc.CHAIN_ROOM, "rod", "on the wifi")
rrc_mesh._peers[h]["lora_roster"] = " ".join(rrc_mesh._lora_roster())   # names already told: only #wifi-mesh here
rrc_mesh._peers[h]["lora_roster_at"] = time.time()
rrc_mesh._flush_to_peer(me, h, rrc_mesh._peers[h])
q = list(rrc_mesh._send_queue)
check("#wifi-mesh: queued for the chain interface", q and q[-1][3] == "#" + rrc.CHAIN_ROOM and q[-1][5] is chain_if, q)
for item in q:
    rrc_mesh._send_item(item)
check("#wifi-mesh: sent on the chain only, nothing on the radio", chain_if.sent and not radio_if.sent,
      (len(chain_if.sent), len(radio_if.sent)))
del rrc_mesh._send_queue[:]
# A Stump not known yet (its beacon missed) sends a #lora-mesh batch over
# the radio: it's taken for a Stump by the title, and its lines show as
# nick@stump -- not as "<nick> text" said by the Stump itself.
class Msg:
    def __init__(self, src, title, text):
        self.source_hash, self._t, self._c = src, title, text
        self.receiving_interface = radio_if
        self.signature_validated = True
        self.fields = {}

    def title_as_string(self):
        return self._t

    def content_as_string(self):
        return self._c

stranger = bytes(range(16))
me.peers[stranger] = {"name": "Radioz"}
rrc.create_room(rrc.MESH_ROOM)
before = rrc.since(rrc.MESH_ROOM, 0)[-1]["id"] if rrc.since(rrc.MESH_ROOM, 0) else 0
rrc_mesh.on_message(me, Msg(stranger, "#lora-mesh", "<ana> hello over the air"))
new = rrc.since(rrc.MESH_ROOM, before)
check("an unknown Stump's #lora-mesh batch: shown as nick@stump", any(m["nick"] == "ana@Radioz"
      and m["body"] == "hello over the air" for m in new) and not any(m["body"].startswith("<ana>") for m in new), new)
check("... and it's known as a Stump from now on", rrc.is_stump(stranger.hex()))
plain = bytes(range(16, 32))
me.peers[plain] = {"name": "phone"}
rrc_mesh.on_message(me, Msg(plain, "", "hi from a phone"))
check("a LoRa phone's message is still a person's", not rrc.is_stump(plain.hex()))

# The radio blinked offline: every path through it is gone, and the other
# Stump would drop out until it spoke first. With the radio back and its
# keys known, it stays a #lora-mesh destination; lines written while it
# was missing still go (recent ones).
oh = other.hash.hex()
rrc.mark_stump(oh)
rrc_mesh._peers.pop(oh, None)
Transport.path_table.pop(other.hash, None)
radio_if.online = True
rrc.touch_user("127.0.0.1", nick="rod", room=rrc.MESH_ROOM)
rrc.post(rrc.MESH_ROOM, "rod", "written while it was away")
rrc_mesh._take_in_chain_stumps(me)
p = rrc_mesh._peers.get(oh)
check("no path, radio online, keys known: still a Stump to send to", p is not None and p.get("stump"), p)
del rrc_mesh._send_queue[:]
radio_if.sent.clear()
rrc_mesh._flush_to_peer(me, oh, p)
q = list(rrc_mesh._send_queue)
check("... the line written while it was away goes, over the radio", any("written while it was away" in i[2]
      and len(i) == 6 and i[5] is radio_if for i in q), q)
del rrc_mesh._send_queue[:]
rrc_mesh._flush_to_peer(me, oh, p)
check("... once", not any("written while it was away" in i[2] for i in rrc_mesh._send_queue), rrc_mesh._send_queue)
rrc_mesh._peers.pop(oh, None)
rrc_mesh._take_in_chain_stumps(me)
p = rrc_mesh._peers.get(oh)
rrc_mesh._flush_to_peer(me, oh, p)
check("dropped and back again: not sent twice (where it left off)",
      not any("written while it was away" in i[2] for i in rrc_mesh._send_queue), rrc_mesh._send_queue)
del rrc_mesh._send_queue[:]

# /names in #lora-mesh: everyone in it, on every Stump. This Stump tells
# the others who's in its #lora-mesh ("[NAMES] ..."), when it changes;
# what the others tell it shows as nick@stump.
p = rrc_mesh._peers.get(oh)
p.pop("lora_roster", None)
del rrc_mesh._send_queue[:]
rrc_mesh._flush_to_peer(me, oh, p)
q = list(rrc_mesh._send_queue)
check("names: this Stump's #lora-mesh people sent to the other Stump", any("[NAMES]" in i[2] and "rod" in i[2] for i in q), q)
del rrc_mesh._send_queue[:]
rrc_mesh._flush_to_peer(me, oh, p)
check("names: not again while nothing changed", not any("[NAMES]" in i[2] for i in rrc_mesh._send_queue), rrc_mesh._send_queue)
del rrc_mesh._send_queue[:]
rrc_mesh.on_message(me, Msg(stranger, "#lora-mesh", "[NAMES] ana bob"))
names = rrc.handle_input("127.0.0.1", rrc.MESH_ROOM, "/names")[0][0]
check("/names in #lora-mesh: here, then the other Stumps' people as nick@stump",
      "rod" in names and "ana@Radioz" in names and "bob@Radioz" in names, names)
check("... and they're DM contacts", "ana@Radioz" in rrc.reachable_nicks(), rrc.reachable_nicks())
check("... no '[NAMES]' line shown in the room", not any("[NAMES]" in m["body"] for m in rrc.since(rrc.MESH_ROOM, 0)))

# Off Wi-Fi, Stumps only meet over the radio: the radio coming up, and a
# Stump heard for the first time, make this one introduce itself soon
# (its beacon and LXMF announce), not more often than set.
import chain
chain._intro.update(radio=-10 ** 9, stump=-10 ** 9)
chain.take_link_event()
check("radio up: introduce this Stump", chain.radio_up(1000) and chain.take_link_event())
check("radio up again soon: not again (airtime)", not chain.radio_up(1100) and not chain.take_link_event())
check("radio up 10 min later: again", chain.radio_up(1000 + chain.RADIO_UP_EVERY) and chain.take_link_event())
check("a new Stump heard: answer it", chain.stump_heard(5000) and chain.take_link_event())
check("another within a minute: once is enough", not chain.stump_heard(5030) and not chain.take_link_event())
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
