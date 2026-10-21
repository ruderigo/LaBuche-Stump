"""
Where does a CAM spend the 6.8 ms it takes to forward one file packet?

    mpremote connect PORT run bench/profile_bench.py
    mpremote connect PORT reset

Same setup as relay_bench.py (one CAM, node stopped, no network), but it
times every step of forwarding separately, checks how much is garbage
collection (the heap lives in the slow external PSRAM), and breaks the
encryption at the two ends into its parts. Paste the whole output back.
"""
import time, gc, sys, os
if "" not in sys.path:
    sys.path.insert(0, "")

from urns.log import LOG_NONE, set_loglevel
from urns import const
from urns.transport import Transport
from urns.identity import Identity
from urns.interfaces import Interface
from urns.packet import Packet

N = 500
PAYLOAD = 464
set_loglevel(LOG_NONE)
us = time.ticks_us
diff = time.ticks_diff


def timeit(label, fn, n=N):
    gc.collect()
    t0 = us()
    for _ in range(n):
        fn()
    per = diff(us(), t0) / n
    print("  %-44s %8.1f us" % (label, per))
    return per


class Sink(Interface):
    def __init__(self, name):
        super().__init__(name)
        self.online = True
        self.count = 0

    def process_outgoing(self, raw):
        self.count += 1
        return True

    def __str__(self):
        return self.name


print("=" * 64)
try:
    import machine
    print("CPU %d MHz" % (machine.freq() // 1000000))
except Exception:
    pass
gc.collect()
print("heap free %d KB, used %d KB" % (gc.mem_free() // 1024, gc.mem_alloc() // 1024))
print("=" * 64)

# ---- A. building blocks -------------------------------------------------
print("\nA. Building blocks (per call)")
raw480 = bytes(480)
x = [0]
def _loop():
    x[0] += 1
timeit("empty Python call", _loop)
timeit("slice 480 bytes", lambda: raw480[2:])
timeit("concat 1 + 480 bytes", lambda: b"\x00" + raw480)
timeit("bytes(480) (allocate)", lambda: bytes(480))
import uhashlib
timeit("SHA-256 of 480 bytes (uhashlib)", lambda: uhashlib.sha256(raw480).digest())
timeit("Identity.full_hash(480 bytes)", lambda: Identity.full_hash(raw480))
timeit("time.time()", time.time)
timeit("os.urandom(16)", lambda: os.urandom(16))
d = {bytes(16): 1}
k = bytes(16)
timeit("dict lookup, 16-byte key", lambda: d.get(k))
from urns.log import log
timeit("log() below level, string built first",
       lambda: log("Inbound: " + str(480) + " bytes, flags=0x" + ("%02x" % 12), 7))
timeit("bytes.hex()[:8] of 16 bytes", lambda: k.hex()[:8])

# ---- B. garbage collection ----------------------------------------------
print("\nB. Garbage collection")
t0 = us(); gc.collect(); gcms = diff(us(), t0) / 1000
print("  one full collection now: %.1f ms" % gcms)

Transport.identity = Identity()
Transport.transport_enabled = True
up, down = Sink("wifi-up"), Sink("wifi-down")
link_id = bytes(range(16))
Transport._add_link(link_id, bytes(16), down, 1, up, 1, bytes(range(16, 32)))
flags = (const.HDR_1 << 6) | (const.TRANSPORT_BROADCAST << 4) | (const.DEST_LINK << 2) | const.PKT_DATA
raw = bytes([flags, 0]) + link_id + bytes([const.CTX_RESOURCE]) + bytes(PAYLOAD)
fwd = lambda: Transport.inbound(raw, up)

with_gc = timeit("forward one packet, GC on (as in the field)", fwd)
gc.collect(); gc.disable()
no_gc = timeit("forward one packet, GC off", fwd, 100)   # 100: without GC the heap fills
gc.enable(); gc.collect()
print("  -> garbage collection is %.0f%% of forwarding time" % (100 * (with_gc - no_gc) / with_gc))

# ---- C. each step of forwarding (GC off, so steps aren't charged for it)
print("\nC. Each step of forwarding (GC off; nested steps are inside their parent)")
acc = {}
cnt = {}


def wrap_static(owner, name):
    orig = getattr(owner, name)
    def w(*a, **k):
        t0 = us()
        r = orig(*a, **k)
        acc[name] = acc.get(name, 0) + diff(us(), t0)
        cnt[name] = cnt.get(name, 0) + 1
        return r
    setattr(owner, name, staticmethod(w) if owner is Transport else w)


for nm in ("_ifac_validate", "packet_filter", "_should_remember", "_transit_forward",
           "_transit_link", "_cache_packet_hash", "transmit"):
    wrap_static(Transport, nm)
for nm in ("unpack", "update_hash", "get_hash", "get_hashable_part"):
    wrap_static(Packet, nm)
orig_init = Packet.__init__
def init_w(self, *a, **k):
    t0 = us()
    orig_init(self, *a, **k)
    acc["Packet()"] = acc.get("Packet()", 0) + diff(us(), t0)
    cnt["Packet()"] = cnt.get("Packet()", 0) + 1
Packet.__init__ = init_w

NC = 100
gc.collect(); gc.disable()
t0 = us()
for _ in range(NC):
    Transport.inbound(raw, up)
total = diff(us(), t0) / NC
gc.enable(); gc.collect()
print("  %-44s %8.1f us" % ("whole inbound() (with the timing wrappers)", total))
for nm in sorted(acc, key=lambda n: -acc[n]):
    print("  %-44s %8.1f us  (x%d)" % (nm, acc[nm] / NC, cnt[nm] // NC))

# ---- D. encryption at the ends, by part --------------------------------
print("\nD. Link encryption at the ends, by part (per packet)")
from urns.crypto.token import Token
from urns.crypto.pkcs7 import PKCS7
from urns.crypto.hmac import new as hmac_new
from urns.crypto import aes as A
key = Token.generate_key()
tok = Token(key)
plain = bytes(PAYLOAD - 64)
padded = PKCS7.pad(plain)
mode = tok.mode
ek, sk, iv = tok._encryption_key, tok._signing_key, os.urandom(16)
timeit("Token.encrypt (all)", lambda: tok.encrypt(plain), N // 2)
timeit("  os.urandom(16) IV", lambda: os.urandom(16))
timeit("  PKCS7.pad", lambda: PKCS7.pad(plain))
timeit("  AES-CBC encrypt %d bytes" % len(padded), lambda: mode.encrypt(plaintext=padded, key=ek, iv=iv))
timeit("  HMAC-SHA256 of %d bytes" % (len(padded) + 16), lambda: hmac_new(sk, iv + padded).digest())
from ucryptolib import aes as raw_aes
timeit("  (raw ucryptolib AES-CBC, same size)", lambda: raw_aes(ek, 2, iv).encrypt(padded))
print("\nDone. Reboot the board: mpremote connect PORT reset")
