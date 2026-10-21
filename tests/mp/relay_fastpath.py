# The relay fast path (Transport._relay_fast) must forward exactly what the
# general path (_transit_link) forwards: same bytes, same interface, same
# refusals -- for every packet type, context, direction and hop count.
import sys
sys.path.insert(0, '.')
from urns.log import LOG_NONE, set_loglevel
set_loglevel(LOG_NONE)
from urns import const
from urns.transport import Transport
from urns.identity import Identity
from urns.interfaces import Interface

FAIL = []


def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(info)[:300]))
    if not cond:
        FAIL.append(name)


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
A, B, S = Sink("A"), Sink("B"), Sink("shared")
L1, L2 = bytes(range(16)), bytes(range(1, 17))
Transport._add_link(L1, bytes(16), B, 2, A, 1, bytes(16))     # A <-> B, 1 hop taken, 2 to go
Transport._add_link(L2, bytes(16), S, 1, S, 1, bytes(16))     # same interface both ways
fast = Transport._relay_fast


def run(raw, iface, use_fast):
    for s in (A, B, S):
        s.sent = []
    Transport.packet_hashlist = set()          # each run starts unseen (dedup is
    Transport.packet_hashlist_prev = set()     # per packet, not per path)
    Transport._relay_fast = fast if use_fast else (lambda r, i: False)
    before = dict((k, list(v)) for k, v in Transport.link_table.items())
    Transport.inbound(raw, iface)
    Transport.link_table.update(before)        # undo timestamp refresh between runs
    return [(s.name, s.sent) for s in (A, B, S) if s.sent]


cases = 0
for link in (L1, L2):
    for ptype in (const.PKT_DATA, const.PKT_PROOF, const.PKT_ANNOUNCE, const.PKT_LINKREQUEST):
        for ctx in (const.CTX_RESOURCE, const.CTX_RESOURCE_REQ, const.CTX_RESOURCE_PRF, const.CTX_KEEPALIVE,
                    const.CTX_CHANNEL, const.CTX_NONE, const.CTX_LRPROOF, 0x0A):
            for hops in (0, 1, 2, 3):
                for iface in (A, B, S):
                    flags = (const.HDR_1 << 6) | (const.TRANSPORT_BROADCAST << 4) | (const.DEST_LINK << 2) | ptype
                    raw = bytes([flags, hops]) + link + bytes([ctx]) + bytes(range(40)) + bytes([cases & 0xFF, hops])
                    slow = run(raw, iface, False)
                    quick = run(raw, iface, True)
                    cases += 1
                    if slow != quick:
                        check("case %d (link %d, type %d, ctx %d, hops %d, from %s)" % (cases, link[0], ptype, ctx, hops, iface.name),
                              False, (slow, quick))
check("fast and general paths agree on %d cases" % cases, not FAIL)
# and it really is the fast path doing the work for file packets
flags = (const.HDR_1 << 6) | (const.DEST_LINK << 2) | const.PKT_DATA
raw = bytes([flags, 0]) + L1 + bytes([const.CTX_RESOURCE]) + bytes(400)
Transport._relay_fast = fast
B.sent = []
check("file packet A->B forwarded with hops 1", fast(raw, A) and B.sent and B.sent[0][1] == 1 and B.sent[0][2:] == raw[2:])
# Big transfers stay off radio interfaces (bulk_ok False): a link may relay
# only BULK_RADIO_BYTES of file data onto one per window; requests, proofs
# and everything else still pass, and Wi-Fi interfaces have no cap.
R = Sink("radio")
R.bulk_ok = False
L3, L4 = bytes(range(2, 18)), bytes(range(3, 19))
Transport._add_link(L3, bytes(16), R, 1, A, 1, bytes(16))
Transport._add_link(L4, bytes(16), B, 1, A, 1, bytes(16))
Transport._bulk = {}
for link, sink in ((L3, R), (L4, B)):
    sink.sent = []
    for i in range(150):
        raw = bytes([flags, 0]) + link + bytes([const.CTX_RESOURCE]) + bytes(464)
        Transport.inbound(raw, A)
    if sink is R:
        want = Transport.BULK_RADIO_BYTES // len(raw)
        check("radio: only %d of 150 file packets relayed" % want, len(sink.sent) == want, len(sink.sent))
        sink.sent = []
        Transport.inbound(bytes([flags, 0]) + link + bytes([const.CTX_RESOURCE_REQ]) + bytes(40), A)
        Transport.inbound(bytes([flags, 0]) + link + bytes([const.CTX_NONE]) + bytes(60), A)
        check("radio: requests and ordinary link packets still relayed", len(sink.sent) == 2, len(sink.sent))
    else:
        check("Wi-Fi: all 150 file packets relayed", len(sink.sent) == 150, len(sink.sent))
print('FAILURES:', FAIL)
