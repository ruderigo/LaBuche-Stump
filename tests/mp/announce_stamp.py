# Reticulum 1.6.1 stamps announces (1.0.0-beta.85): it appends to the app
# data a marker (0xC1), flags, a 14-byte stamp and the 2-byte link cost.
# Reference Reticulum hands applications the app data without it; so does
# urns, or a 1.6.1 node's name read with 18 bytes of stamp after it (CI's
# NomadNet test: the Stump listed the node under its hash, not "Aleph node").
import sys
exec(open(sys.argv[1] + '/harness.py').read())
from urns.log import LOG_NONE, set_loglevel
from urns.identity import Identity
set_loglevel(LOG_NONE)
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


STAMP = bytes(range(1, 15))
EXT = bytes([0xC1, 0x00]) + STAMP + bytes([0x00, 0x00])
check("a stamped name: the name alone", Identity.without_stamp(b"Aleph node" + EXT) == b"Aleph node")
check("stamped LXMF app data (msgpack): the msgpack alone",
      Identity.without_stamp(b"\x92\xa4Zoe\x08" + EXT) == b"\x92\xa4Zoe\x08")
check("a stamp with no app data before it: none", Identity.without_stamp(EXT) is None)
check("unstamped app data: as it was", Identity.without_stamp(b"Aleph node") == b"Aleph node")
check("... short, or empty, or none: as it was",
      Identity.without_stamp(b"x") == b"x" and Identity.without_stamp(b"") == b"" and Identity.without_stamp(None) is None)
other = b"0123456789abcdef" + bytes([0xC1, 0x00]) + STAMP + bytes([0x00, 0x80])
check("no marker where the stamp would start, or an unknown format: as it was",
      Identity.without_stamp(b"0123456789abcdefghijklmnop") == b"0123456789abcdefghijklmnop"
      and Identity.without_stamp(other) == other)
kd = Identity.known_destinations
h = bytes(16)
Identity.remember(b"p" * 32, h, bytes(64), b"Aleph node" + EXT)
check("remembered without it: what plugins and names read", Identity.recall_app_data(h) == b"Aleph node", Identity.recall_app_data(h))
kd.pop(h, None)

# A 1.6.1 app answers a path request only once its announce stamp is made
# (16 s on CI the first time, minutes on a phone): a reply to it waits up to
# LXMF_PATH_WAIT for its path, asking again every 30 s after the first 15 s
# (the radio's airtime). It gave up after 15 s and the reply was lost.
from urns import lxmf
from urns.transport import Transport
import time
asked = []
real_rp = Transport.request_path
Transport.request_path = staticmethod(lambda h, *a, **k: asked.append(h))
try:
    r = lxmf.LXMRouter(identity=Identity())
    far = bytes(range(16))
    Transport._path_waiters.pop(far, None)
    t0 = time.time()
    r.send_message(far, "welcome")
    w = Transport._path_waiters.get(far) or [{}]
    check("a reply to someone with no path yet waits up to 3 min for it", w[0].get("deadline", 0) - t0 >= lxmf.LXMF_PATH_WAIT - 2,
          w[0].get("deadline", 0) - t0)
    n = len(asked)
    Transport._path_since[far] = time.time() - 20
    Transport._path_request_times[far] = time.time() - 10
    Transport._process_path_waiters()
    check("... past 15 s, not asked again 10 s after the last time", len(asked) == n, len(asked) - n)
    Transport._path_request_times[far] = time.time() - 31
    Transport._process_path_waiters()
    check("... but 30 s after", len(asked) == n + 1, len(asked) - n)
    Transport._path_waiters.pop(far, None); Transport._path_since.pop(far, None); Transport._path_request_times.pop(far, None)
finally:
    Transport.request_path = real_rp

# Our own stamps (1.0.0-beta.86): urns/dstamp. A light cost so the test is
# quick; the reference tests check 1.6.1 takes them at the real one.
from urns import dstamp
from urns.destination import Destination
from urns.transport import Transport
import os
if not getattr(Transport, "identity", None):
    Transport.identity = Identity()
tmp = "/tmp/stamps-test-%d" % (os.urandom(2)[0])
try:
    os.mkdir(tmp)
except OSError:
    pass
dstamp._dir[0] = tmp
dstamp.COST = 10
me = Identity()
d = Destination(me, Destination.IN, Destination.SINGLE, "nomadnetwork", "node")
check("stamping off: no stamp, nothing listed", dstamp.want(d) is None and dstamp.status()["on"] is False)
pkt = d.announce(app_data=b"Aleph node", send=False)
check("... and the announce as before", pkt.data.endswith(b"Aleph node"))
dstamp.set_enabled(True)
check("on: listed for the Provisioner (rns/stamps_wanted.json), its material with it",
      dstamp._read(dstamp.WANTED_FILE).get(d.hash.hex()) == dstamp.material(d).hex())
check("... no stamp yet: the announce still goes, as before", dstamp.want(d) is None)
n = 0
while not dstamp.step(50):
    n += 1
st = dstamp.want(d)
check("the maker finds it a slice at a time; it checks", st is not None and dstamp.valid(dstamp.material(d), st), n)
check("... kept in rns/stamps.json", dstamp._read(dstamp.STAMPS_FILE).get(d.hash.hex()) == st.hex())
pkt = d.announce(app_data=b"Aleph node", send=False)
check("stamped: after the app data, as 1.6.1 puts it (0xC1, 0, the stamp, a link cost of 0)",
      pkt.data.endswith(b"Aleph node" + bytes([0xC1, 0]) + st + b"\x00\x00"))
Identity.known_destinations.pop(d.hash, None)
pkt.pack()
from urns.packet import Packet
p2 = Packet(None, pkt.raw)
p2.unpack()
check("another Stump takes it (signed with the stamp), and reads the name without it",
      Identity.validate_announce(p2) and Identity.recall_app_data(d.hash) == b"Aleph node")
dstamp._stamps.clear()
dstamp.load()
check("read back at the next start", dstamp.want(d) == st)
other = Destination(Identity(), Destination.IN, Destination.SINGLE, "nomadnetwork", "node")
dstamp._stamps[other.hash.hex()] = st                  # a stamp that isn't this one's
dstamp._wanted.pop(other.hash.hex(), None)
check("a stamp that doesn't check (another identity's) isn't used", dstamp.want(other) is None)
dstamp.set_enabled(False)
check("off again: announces as before", dstamp.want(d) is None)
s0 = dict(dstamp.stats)
dstamp.counted(200, True); dstamp.counted(180, False)
check("counted for /meter: announces, bytes, stamp bytes",
      dstamp.stats["announces"] == s0["announces"] + 2 and dstamp.stats["bytes"] == s0["bytes"] + 380
      and dstamp.stats["stamp_bytes"] == s0["stamp_bytes"] + 18)
for f in (dstamp.STAMPS_FILE, dstamp.WANTED_FILE):
    try:
        os.remove(tmp + "/" + f)
    except OSError:
        pass

print("FAILURES", FAIL)
