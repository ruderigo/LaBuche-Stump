# Keeping a Stump alive on its own (keepalive.py) on the board's
# MicroPython: failed starts counted (a crash, a hang, a power cut -- not
# a planned stop), RESCUE_AFTER in a row give a rescue start, a stable one
# clears the count, a rescue start tries a normal one after its time, and
# a rescue start that keeps failing rests and starts from scratch. /admin
# says so, with a button; /node.json says "rescue".
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import keepalive as W
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


W.STATE_FILE = ROOT + '/boot_state.json'
W.RESCUE_WAIT_S = 0
try:
    __import__('os').remove(W.STATE_FILE)
except OSError:
    pass
restarts = []
import settings
settings.restart = lambda: restarts.append(1)

check("a first start: normal", W.begin() == "normal" and W.status()["failed_starts"] == 0)
W.check()
check("... not stable yet (under STABLE_S)", not W._st["stable"])
# It crashes before it's up for long (boot_common keeps the error).
W.note_crash("OSError: [Errno 5] EIO")
check("crashed early: a failed start", W.begin() == "normal" and W.status()["failed_starts"] == 1)
check("... the error kept for /admin", "EIO" in str(W.status()["last_failure"]), W.status())
W.planned()
check("a planned restart (Ctrl+C, /admin): not counted", W.begin() == "normal" and W.status()["failed_starts"] == 1)
mode = W.begin()                       # the last start: a hang, or the power
check("a hang or a power cut early on: counted", mode == "normal" and W.status()["failed_starts"] == 2
      and "stopped before" in str(W.status()["last_failure"]), W.status())
mode = W.begin()
check("three failed starts in a row: a rescue start", mode == "rescue" and W.status()["failed_starts"] == 3)

# /admin and /node.json say so.
html = barkeep._render_admin_health("en", "pw")
check("/admin: the rescue notice, what failed, and 'Start normally'", "Rescue start" in html
      and "3 starts in a row failed" in html and "Start normally" in html and "/admin/restart" in html, html)
s, ct, b = req('/node.json')
check("/node.json: \"mode\": \"rescue\"", '"mode": "rescue"' in b, b[:200])


# The rescue start runs fine; after its time it tries a normal one.
W._st["t0"] -= W.STABLE_S + 1
W.check()
check("a stable rescue start keeps its count (a restart now stays a rescue start)",
      W._load().get("short") == 3 and W._load().get("stable"))
W._st["t0"] -= W.RESCUE_RETRY_S
W.check()
check("... after RESCUE_RETRY_S: a normal start tried (restarted, count cleared)",
      restarts == [1] and W._load().get("short") == 0)
check("... and the next start is a normal one", W.begin() == "normal" and W.status()["failed_starts"] == 0)
W._st["t0"] -= W.STABLE_S + 1
W.check()
check("a normal start that stays up: stable, the count cleared", W._load().get("stable") and W._load().get("short") == 0)
check("... no notice on /admin", barkeep._render_admin_health("en", "pw") == "")

# The rescue start failing too: it rests, then starts from scratch.
for _ in range(2 * W.RESCUE_AFTER + 1):     # (the first ends the stable run)
    W.begin()
check("six failed starts (the rescue ones too): a rest, then a normal start", W._st["mode"] == "normal"
      and W.status()["failed_starts"] == 0, W.status())
# The computer's supervisor restarted a Stump that stopped responding.
d = W._load()
d.update(wd=True)
W._save(d)
W.begin()
check("restarted by the watchdog: said so", W.status()["cause"] == "watchdog"
      and "watchdog restarted it" in barkeep._render_admin_health("en", "pw"), W.status())
n = W.status()["failed_starts"]
d = W._load()
d.update(wd=True)
W._save(d)
W.begin()
check("... early on, it's a failed start, and the reason says the watchdog",
      W.status()["failed_starts"] == n + 1 and "watchdog" in str(W.status()["last_failure"]), W.status())
W._st["lag_ms"] = 0
W.check(late=2500)
check("how late the loop ran: kept for /admin", W.status()["lag_ms"] == 2500)
# Into a rescue start, then someone at the console (an upload): the next
# start is a normal one.
try:
    __import__('os').remove(W.STATE_FILE)
except OSError:
    pass
for _ in range(W.RESCUE_AFTER + 1):
    W.begin()
check("(a rescue start)", W.mode() == "rescue")
W.console_stop()
check("a stop at the console (Ctrl+C, the Provisioner): the next start is a normal one",
      W.begin() == "normal" and W.status()["failed_starts"] == 0, W.status())
print('FAILURES:', FAIL)
