# A message's "ts" is Unix time on every Stump (1.0.0-beta.88). A board's
# MicroPython counts from 2000; without the 946684800 s the rest of the
# firmware adds, the Android team saw a DM relayed by LaBuche dated
# 1996-11-21. This port counts from 1970 like a computer, so the test
# switches the board's offset on (rrc.EPOCH_OFFSET) and checks: the "ts"
# /poll sends is today's Unix time, a fresh DM isn't taken for an old
# one, and #lora-mesh still catches another Stump up on its last 5 min.
import sys, time
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, rrc_mesh, i18n
rrc.LORA_NONE[0] = False
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


BOARD = 946684800
check("a computer (this port): no offset", rrc.EPOCH_OFFSET == 0 and abs(rrc.unix_now() - time.time()) < 2)

# As on a board: the offset on. (This port's clock already reads Unix
# time, so here "Unix" comes out 30 years ahead: what matters is that the
# offset is added to ts, and that whatever compares with ts adds it too --
# a leftover time.time() there would be 30 years off and fail below.)
rrc.EPOCH_OFFSET = BOARD
unix = time.time() + BOARD
rrc.reset()

rrc.post("main", "ana", "a room line")
ts = rrc.since("main", 0)[-1]["ts"]
check("a room line's ts: Unix time, not the board's 2000 clock", abs(ts - unix) < 5, (ts, unix))
check("... the board's clock plus 946684800 s (without it, 1996 for 2026)", abs((ts - time.time()) - BOARD) < 5, ts)

A, B = 'aa' * 16, 'bb' * 16
for h, n in ((A, "ana"), (B, "bo")):
    i18n.set_lang(h, 'en')
    rrc.touch_user(h, nick=n, room='main')
ok, why = rrc.send_dm("ana", "bo", "a private word")
check("a DM sent", ok, why)
d = rrc.dms_since(B, 0)
check("a DM's ts: Unix time", d and abs(d[-1]["ts"] - unix) < 5, d)
rrc._prune_dms(B)
check("... and still there after the expiry check (same clock on both sides)", len(rrc.dms_since(B, 0)) == 1, rrc.dms_since(B, 0))
rrc._dms[B][-1]["ts"] = rrc.unix_now() - rrc.DM_TTL_SECONDS - 60
rrc._prune_dms(B)
check("... one older than the DM lifetime does expire", rrc.dms_since(B, 0) == [], rrc.dms_since(B, 0))

# #lora-mesh: a Stump heard for the first time gets the last LORA_BACKLOG_S.
rrc.post("lora-mesh", "ana", "an old line")
old = rrc.since("lora-mesh", 0)[-1]
old["ts"] = rrc.unix_now() - rrc_mesh.LORA_BACKLOG_S - 60
rrc.post("lora-mesh", "ana", "a fresh line")
S = 'cd' * 16
rrc_mesh._lora_marks.pop(S, None)
start = rrc_mesh._lora_start(S)
check("#lora-mesh catches a new Stump up from the last 5 minutes: the old line left out, the fresh one in",
      start == old["id"] and [m["body"] for m in rrc.since("lora-mesh", start)] == ["a fresh line"],
      (start, rrc.since("lora-mesh", 0)))

rrc.EPOCH_OFFSET = 0
print("FAILURES:", FAIL)
