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

print("FAILURES", FAIL)
