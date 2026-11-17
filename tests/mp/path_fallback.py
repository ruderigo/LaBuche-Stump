# A route that doesn't deliver is set aside (1.0.0-beta.80). The kit: the
# Mac kept a 1-hop path to a FireFly over the home Wi-Fi (its Local
# network) that the phone didn't take messages on, while the FireFly's
# announces came over the chain (2 hops) and were turned down as worse:
# every answer lost, /auth never answered. Now two messages running with
# no proof, over a route while the client's announces come over another:
# that route set aside for an hour, another asked for and taken.
import sys, time
exec(open(sys.argv[1] + '/harness.py').read())
from urns.log import LOG_NONE, set_loglevel
from urns import const
from urns.transport import Transport
from urns.identity import Identity
from urns.destination import Destination
from urns.interfaces import Interface
import rrc, rrc_mesh
set_loglevel(LOG_NONE)
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


class Sink(Interface):
    def __init__(self, name):
        super().__init__(name)
        self.online = True
        self.sent = []

    def process_outgoing(self, raw):
        self.sent.append(bytes(raw))
        return True

    def __str__(self):
        return self.name


Transport.identity = Identity()
Transport.transport_enabled = True
Transport.announce_inline = True               # handled at once, not queued for the loop
LAN, CH = Sink("Local network"), Sink("Chain up")
Transport.interfaces[:] = [LAN, CH]
asked = []
Transport.request_path = lambda d, on_interface=None: asked.append(d)

ff = Identity()                                 # the FireFly
D = Destination(ff, Destination.IN, Destination.SINGLE, "lxmf", "delivery")
H = D.hash
Transport.destinations[:] = [x for x in Transport.destinations if x is not D]


def announce_raw():
    """A fresh announce of the FireFly's (its own random blob)."""
    p = D.announce(send=False)
    p.pack()
    return bytes(p.raw)


def heard(iface, hops):
    raw = announce_raw()
    if hops > 1:                                 # relayed: HDR_2, a relay's transport id
        raw = bytes([raw[0] | 0x50, hops - 1]) + bytes(range(16)) + raw[2:]
    Transport.inbound(raw, iface)


heard(LAN, 1)
check("(its announce over the home Wi-Fi, 1 hop: the path)", Transport.path_interface(H) == "Local network")
heard(CH, 2)
check("its announces over the chain, 2 hops: turned down as worse, as before", Transport.path_interface(H) == "Local network")
check("... but heard there (noted)", Transport.heard_elsewhere(H, "Local network", 60))

# Messages to it get no proof over that route.
h = H.hex()
rrc_mesh._peers[h] = {"nick": "fly", "room": rrc.MESH_ROOM, "home_room": rrc.MESH_ROOM, "last_seen": time.time(),
                      "last_sent_id": 0, "last_dm_id": 0}


class Receipt:
    def __init__(self):
        self.t = self.ok = self.no = None

    def set_timeout(self, t):
        self.t = t

    def set_delivery_callback(self, f):
        self.ok = f

    def set_timeout_callback(self, f):
        self.no = f


class Msg:
    def __init__(self):
        self.packet = type("P", (), {})()
        self.packet.receipt = Receipt()


def sent():
    m = Msg()
    rrc_mesh._watch_proof(h, m)
    return m.packet.receipt


r1 = sent()
check("a message to it: its proof waited for (longer than a hop's default: LoRa is slow)", r1.t == rrc_mesh.PROOF_WAIT_S and r1.no)
r1.no(r1)
check("one message with no proof: nothing changes yet", Transport.path_interface(H) == "Local network" and not asked)
r2 = sent()
r2.no(r2)
check("two running: the route over the home Wi-Fi set aside", Transport.path_interface(H) is None
      and Transport.avoided(H, "Local network"))
check("... and another asked for", asked and asked[-1] == H)
heard(LAN, 1)
check("its next announce over the home Wi-Fi (1 hop): not taken", Transport.path_interface(H) is None)
heard(CH, 2)
check("... over the chain (2 hops): taken -- its answers reach it now", Transport.path_interface(H) == "Chain up")

# A proof in between: no count.
Transport.avoid.clear()
Transport.expire_path(H)
heard(LAN, 1)
a = sent(); a.no(a)
b = sent(); b.ok(b)
c = sent(); c.no(c)
check("no proof, a proof, no proof: never two running, the route kept", Transport.path_interface(H) == "Local network")

# Heard only that way: gone, not a bad route -- kept (it may come back).
Transport.heard_on.pop(H, None)
heard(LAN, 1)
Transport.heard_on[H] = {"Local network": time.time()}
a = sent(); a.no(a)
b = sent()
b.no(b)
check("no proofs, but heard no other way: the route kept (it's away, not misrouted)",
      Transport.path_interface(H) == "Local network" and not Transport.avoided(H, "Local network"))

# An hour later: the home Wi-Fi taken again if it's the best.
Transport.avoid[H] = {"Local network": time.time() - 1}
Transport.expire_path(H)
heard(LAN, 1)
check("set aside an hour only: then taken again", Transport.path_interface(H) == "Local network" and H not in Transport.avoid)

print("FAILURES", FAIL)
