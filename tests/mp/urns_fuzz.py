# The LoRa side, from an attacker's radio (pentest, step 3 of
# docs/ROADMAP.md), on the board's MicroPython: µReticulum's parsers fed
# real packets with bits flipped, bytes cut or added, lengths lied about,
# and plain noise -- announces (a person's, a Stump's beacon, a
# propagation node's), LXMF messages, msgpack, and whole packets through
# Transport.inbound as an interface hands them over. For each: no
# exception out of the parser's caller, no call that takes long, and the
# memory back where it was. A fixed seed: the same bytes every run.
import sys, time, gc
exec(open(sys.argv[1] + '/harness.py').read())
from urns.log import LOG_NONE, set_loglevel
set_loglevel(LOG_NONE)
from urns import umsgpack, const
from urns.identity import Identity
from urns.destination import Destination
from urns.transport import Transport
from urns.packet import Packet
from urns.lxmf import LXMessage, APP_NAME
import urandom
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:600]))
    if not c:
        FAIL.append(n)


urandom.seed(20261024)


def rbytes(n):
    return bytes(urandom.getrandbits(8) for _ in range(n))


def mutants(raw, n):
    """n variations of raw: bit flips, cuts, additions, splices, noise."""
    out = []
    for i in range(n):
        b = bytearray(raw)
        k = i % 6
        if k == 0 and b:                                   # a few bits flipped
            for _ in range(1 + urandom.getrandbits(3)):
                j = urandom.getrandbits(16) % len(b)
                b[j] ^= 1 << urandom.getrandbits(3)
        elif k == 1 and b:                                 # cut short
            b = b[:urandom.getrandbits(16) % len(b)]
        elif k == 2:                                       # bytes added
            b = b + rbytes(1 + urandom.getrandbits(7))
        elif k == 3 and b:                                 # a byte set to an edge value
            b[urandom.getrandbits(16) % len(b)] = (0x00, 0xff, 0x7f, 0x80, 0xdd, 0xdf, 0xdb, 0xc6)[i % 8]
        elif k == 4 and len(b) > 8:                        # a chunk repeated
            j = urandom.getrandbits(16) % (len(b) - 4)
            b = b[:j] + bytes(b[j:j + 4]) * (1 + urandom.getrandbits(5)) + b[j:]
        else:                                              # noise of the same length
            b = bytearray(rbytes(len(b) or 8))
        out.append(bytes(b))
    return out


slow = []


def timed(label, fn, *args):
    t0 = time.ticks_ms()
    try:
        fn(*args)
        err = None
    except Exception as e:
        err = e
    ms = time.ticks_diff(time.ticks_ms(), t0)
    if ms > 1500:
        slow.append((label, ms))
    return err


# ── 1. msgpack ────────────────────────────────────────────────────────
seeds = [umsgpack.packb(["stump", "Beta A", "LaBuche", b"\x11" * 16, ["http://192.168.4.1/"],
                         {"p": 2, "r": "1.0.0-beta.37", "s": ["sd", 3000], "pl": ["tavern"]}]),
         umsgpack.packb([b"Rod", None, [b"x" * 20]]),
         umsgpack.packb({"a": [1, 2, {"b": b"\x00" * 30}], "c": "é" * 10})]
bombs = [b"\x91" * 3000 + b"\xc0",                      # 3000 arrays deep
         b"\x81\xc0" * 2000 + b"\xc0",                  # 2000 maps deep
         b"\xdd\xff\xff\xff\xff" + b"\xc0" * 50,         # an array of 4 billion, 50 given
         b"\xdf\xff\xff\xff\xff\xc0",                   # a map of 4 billion
         b"\xdb\xff\xff\xff\xff" + b"a" * 10,           # a string of 4 billion
         b"\xc6\xff\xff\xff\xff" + b"a" * 10,           # bin32 the same
         b"\xa5\xff\xfe\xfd\xfc\xfb"]                   # a string that isn't UTF-8
gc.collect()
m0 = gc.mem_alloc()
errs = 0
for d in bombs + [m for s in seeds for m in mutants(s, 300)]:
    e = timed("msgpack", umsgpack.unpackb, d)
    if e is not None and not isinstance(e, (ValueError, IndexError, TypeError, KeyError, UnicodeError,
                                            RuntimeError, MemoryError, OverflowError)):
        errs += 1
        print("  unexpected", type(e).__name__, e)
check("msgpack: bombs and 900 mutants -- only the parser's own errors (ValueError, ...), nothing else", errs == 0)
check("... none slow", not slow, slow[:5])
gc.collect()
check("... memory back where it was", gc.mem_alloc() - m0 < 64 * 1024, gc.mem_alloc() - m0)
nested = umsgpack.unpackb(b"\x91" * 3000 + b"\xc0") if False else None
try:
    umsgpack.unpackb(b"\x91" * 3000 + b"\xc0")
    deep_ok = False
except ValueError:
    deep_ok = True
except Exception as e:
    deep_ok = repr(e)
check("msgpack nested deeper than any real packet: refused as invalid (not the stack running out)", deep_ok is True, deep_ok)

# ── 2. LXMF messages ─────────────────────────────────────────────────
alice, bob = Identity(), Identity()
a_in = Destination(alice, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")
b_out = Destination(bob, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")
Identity.remember(b"\x00" * 32, a_in.hash, alice.get_public_key())
m = LXMessage(b_out, a_in, "hello from alice " * 4, title="#wifi-mesh")
m.pack()
packed = bytes(m.packed)
del slow[:]
bad = []
verified_forgeries = 0
for d in mutants(packed, 600):
    try:
        got = LXMessage.unpack_from_bytes(d)
        if got is not None and getattr(got, "signature_validated", False) and d != packed:
            verified_forgeries += 1
    except Exception as e:
        if not isinstance(e, (ValueError, IndexError, TypeError, KeyError, UnicodeError, RuntimeError)):
            bad.append(type(e).__name__)
check("LXMF: 600 mutants of a real message -- none taken as verified", verified_forgeries == 0, verified_forgeries)
check("... and only parsing errors raised", not bad, bad[:5])

# ── 3. Whole packets through Transport, as an interface hands them in ─
from urns import Reticulum
import os
try:
    os.mkdir(ROOT + "/rns")
except OSError:
    pass
rns = Reticulum(config_path=ROOT + "/rns/config.json", loglevel=LOG_NONE)
rns.config = {"enable_transport": True, "interfaces": []}
rns.setup_interfaces()
from node_common import setup_node
dest, router = setup_node(rns, "fuzztarget")
sent = []
_out = Transport.outbound


def capture(packet):
    try:
        sent.append(bytes(packet.raw))
    except Exception:
        pass
    return True


Transport.outbound = capture
# Real packets to start from: announces of a person, a Stump's beacon, a
# propagation node; a path request; an LXMF delivery to this node.
peer = Identity()
p_lx = Destination(peer, Destination.IN, Destination.SINGLE, APP_NAME, "delivery")
p_lx.announce(app_data=umsgpack.packb([b"Mallory", None]))
p_st = Destination(peer, Destination.IN, Destination.SINGLE, "stump", "node")
p_st.announce(app_data=seeds[0])
p_pn = Destination(peer, Destination.IN, Destination.SINGLE, APP_NAME, "propagation")
p_pn.announce(app_data=umsgpack.packb([False, 1700000000, True, 256, 256, [16, 16, 18], {}]))
try:
    Transport.request_path(Destination.hash(Identity(), APP_NAME, "delivery"))
except Exception:
    pass
Transport.outbound = _out


class FakeIf:
    name = "Fuzz radio"
    online = True
    HW_MTU = 500
    bitrate = 1200
    mode = 1
    rxb = rx = txb = tx = 0
    ifac_size = 0
    ifac_identity = None
    ifac_signing_key = None
    announce_rate_target = None
    def process_outgoing(self, data):
        return True
    def __str__(self):
        return self.name


iface = FakeIf()
Transport.interfaces.append(iface)
corpus = [r for r in sent if r]
check("a corpus of real packets to start from", len(corpus) >= 3, len(corpus))
del slow[:]
gc.collect()
m0 = gc.mem_alloc()
escaped = []
n = 0
for raw in corpus:
    for d in mutants(raw, 400):
        n += 1
        try:
            t0 = time.ticks_ms()
            Transport.inbound(d, iface)
            ms = time.ticks_diff(time.ticks_ms(), t0)
            if ms > 1500:
                slow.append(("inbound", ms))
        except Exception as e:
            escaped.append(type(e).__name__ + ": " + str(e)[:60])
for _ in range(400):
    n += 1
    try:
        Transport.inbound(rbytes(2 + urandom.getrandbits(9)), iface)
    except Exception as e:
        escaped.append(type(e).__name__ + ": " + str(e)[:60])
check("Transport.inbound: %d mutated and random packets, no exception out" % n, not escaped, escaped[:5])
check("... none slow (each under 1.5 s on this machine)", not slow, slow[:5])
gc.collect()
grew = gc.mem_alloc() - m0
check("... memory stays bounded (tables capped)", grew < 512 * 1024, grew)

# ── 4. A flood: the same announce, many times; then many new ones ─────
gc.collect()
m0 = gc.mem_alloc()
t0 = time.ticks_ms()
for _ in range(300):
    Transport.inbound(corpus[0], iface)
for _ in range(150):
    i = Identity()
    d = Destination(i, Destination.IN, Destination.SINGLE, APP_NAME, "delivery")
    sent.clear()
    Transport.outbound = capture
    d.announce(app_data=umsgpack.packb([b"x", None]))
    Transport.outbound = _out
    if sent:
        Transport.inbound(sent[0], iface)
gc.collect()
check("an announce flood: path and identity tables stay capped", len(Transport.path_table) <= 1024
      and len(Identity.known_destinations) <= 1024, (len(Transport.path_table), len(Identity.known_destinations)))
check("... memory bounded", gc.mem_alloc() - m0 < 1024 * 1024, gc.mem_alloc() - m0)
print("  %d ms for the flood" % time.ticks_diff(time.ticks_ms(), t0))
print('FAILURES:', FAIL)
