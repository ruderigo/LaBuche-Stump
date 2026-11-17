# Path answers on a board (1.0.0-beta.81). A Stump stores recent announces
# to answer path requests at once. On a board it threw out any one when
# full (a MicroPython dict keeps no order), often the one a live path
# answers with, and never renewed a destination's while it kept
# announcing the same way: the kit's S3 didn't answer the Mac's path
# request for a FireFly. Also: /node.json never says "name": null.
import sys, json
exec(open(sys.argv[1] + '/harness.py').read())
from urns.log import LOG_NONE, set_loglevel
from urns import const
from urns.transport import Transport
from urns.identity import Identity
from urns.destination import Destination
from urns.interfaces import Interface
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
Transport.announce_inline = True
LORA, CH = Sink("Heltec"), Sink("Chain hub")
Transport.interfaces[:] = [LORA, CH]
ff = Identity()
D = Destination(ff, Destination.IN, Destination.SINGLE, "lxmf", "delivery")
H = D.hash
Transport.destinations[:] = [x for x in Transport.destinations if x is not D]


def raw_announce():
    p = D.announce(send=False)
    p.pack()
    return bytes(p.raw)


first = raw_announce()
Transport.inbound(first, LORA)
check("(the FireFly heard over LoRa: a path, its announce stored)",
      H in Transport.path_table and Transport.get_cached(Transport.path_table[H][const.IDX_PT_ANNOUNCE]) is not None)

# The store fills with other announces, many times over.
for i in range(const.MAX_PACKET_CACHE * 3):
    Transport.cache_announce(bytes([i % 256, i // 256]) + bytes(30), b"x" * 40)
check("the store full many times over: the announce a live path answers with kept",
      Transport.get_cached(Transport.path_table[H][const.IDX_PT_ANNOUNCE]) is not None)
check("... the store stays its size", len(Transport.packet_cache) <= const.MAX_PACKET_CACHE)
check("... the oldest unused ones went", bytes([0, 0]) + bytes(30) not in Transport.packet_cache
      and bytes([(3 * const.MAX_PACKET_CACHE - 1) % 256, (3 * const.MAX_PACKET_CACHE - 1) // 256]) + bytes(30) in Transport.packet_cache)

# Its stored announce lost anyway (a path table from before, say): renewed
# by its next announce the same way, and a path request answered with it.
Transport.packet_cache.pop(Transport.path_table[H][const.IDX_PT_ANNOUNCE], None)
Transport.inbound(first, LORA)                 # the same announce again: not a new route
check("its announce heard again the same way: stored again",
      Transport.get_cached(Transport.path_table[H][const.IDX_PT_ANNOUNCE]) is not None)
CH.sent.clear()
Transport._answer_path_request(H, CH, bytes(16), b"tag")
check("a chained Stump's path request: answered from the store (queued for the chain)",
      H in Transport.announce_table and Transport.announce_table[H][8] is CH, Transport.announce_table.get(H))

# /node.json: its name even before the node has set it.
import rrc, config
saved = dict(rrc.NODE)
rrc.NODE.clear()
d = json.loads(req("/node.json")[2])
check("/node.json while the Stump starts: its name from its settings, never null", d.get("name") == config.NODE_NAME and d.get("name"), d.get("name"))
rrc.NODE.update(saved)

print("FAILURES", FAIL)
