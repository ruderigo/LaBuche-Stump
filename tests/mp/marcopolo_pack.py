# The Marco Polo pack: one Stump calls "marco" in a shared room, every
# other Stump with the pack answers "polo", the caller times each round
# trip and counts the lost ones. The same on any firmware since beta.53.
# Both parts are played here: lines "from another Stump" are put in the
# room the way rrc_mesh puts them (a nick@Stump, via).
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import time
import config
config.AUTH_ADMIN_PASSWORD = 'pw'
import plugins, rrc, rrc_mesh, i18n
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


ROOM = 'lora-mesh'
rrc.post(ROOM, 'Marco@Old', 'marco 9 dead /9', via='x')      # said before the pack started
plugins.load_all()
check("the pack starts (plugin API 2)", plugins._plugins.get('marcopolo', {}).get('state') == 'on', plugins._plugins.get('marcopolo'))
import marcopolo.install as M
A = 'cd' * 16
W = 'v' + 'w' * 20
for h in (A, W):
    i18n.set_lang(h, 'en')
rrc.touch_user(A, nick='tester', room=ROOM)
rrc_mesh._peers[A] = {"nick": "tester", "room": ROOM, "stump": False}


def cmd(h, line, room=ROOM):
    plugins._cmd_use.clear()
    replies, _ = rrc.handle_input(h, room, line)
    return replies


def said(since=0):
    return [m for m in rrc.since(ROOM, since) if m.get("kind") != "system"]


def remote(nick, body):
    return rrc.post(ROOM, nick, body, via='x')


def last_id():
    m = rrc.since(ROOM, 0)
    return m[-1]["id"] if m else 0


M._tick()
check("what was in the room before it started isn't answered", not any(m["body"].startswith("polo") for m in said()), said())
r = cmd(A, '/marco')
check("/marco with no run: says so, and how to start", len(r) == 1 and "no run" in r[0] and "/marco start" in r[0], r)
check("nothing was put on the air by installing it", not any(m["nick"] == "Marco" for m in said()), said())

# 1. The one who answers.
mark = last_id()
remote('Marco@LaBuche2', 'marco 5 beef /5')
M._tick()
out = said(mark)
check("a call from another Stump: one polo, with its number and run", [m["body"] for m in out if m["nick"] == "Marco"] == ["polo 5 beef"], out)
polo = [m for m in out if m["nick"] == "Marco"][0]
check("... written as a person's line, so it goes back to the other Stumps (not marked bot, not relayed-in)",
      not polo.get("bot") and not polo.get("via"), polo)
check("... and the Stump's own choice of lines for another Stump takes it",
      "<Marco> polo 5 beef" in rrc_mesh._stump_lines(said(mark)), rrc_mesh._stump_lines(said(mark)))
mark = last_id()
remote('Marco@LaBuche2', 'marco 5 beef /5')
M._tick()
check("the same call heard twice (radio and Wi-Fi): answered once", not [m for m in said(mark) if m["nick"] == "Marco"], said(mark))
mark = last_id()
long_call = M._line(6, 'beef', 3)
cut = long_call.index(" Le vieux")
remote('Marco@LaBuche2', long_call[:cut])
M._tick()
check("a long call arriving in pieces: no answer on the first piece", not [m for m in said(mark) if m["nick"] == "Marco"], said(mark))
remote('laptop@LaBuche2', 'something else said meanwhile /6')
M._tick()
check("... nor on someone else's line that happens to end the same", not [m for m in said(mark) if m["nick"] == "Marco"])
remote('Marco@LaBuche2', long_call[cut + 1:])
M._tick()
check("... the polo goes when the piece with the end mark is in", [m["body"] for m in said(mark) if m["nick"] == "Marco"] == ["polo 6 beef"], said(mark))
mark = last_id()
for bad in ('marco', 'marco x beef /x', 'marco 7 /7', 'marco 7 zzzz /7', 'marco 7 beefbeef /7', 'marco 12345 beef /12345', 'marcopolo 7 beef /7', 'polo 7 beef'):
    remote('Marco@LaBuche2', bad)
M._tick()
check("lines that aren't a call: nothing said", not [m for m in said(mark) if m["nick"] == "Marco"], said(mark))
rrc.post(ROOM, 'someone', 'marco 8 beef /8')
rrc.post(ROOM, 'Marco', 'marco 8 beef /8')
M._tick()
check("a call written on this Stump (a person here, or itself): not answered",
      not [m for m in said(mark) if m["nick"] == "Marco" and m["body"].startswith("polo")], said(mark))
mark = last_id()
M._st["polos"] = []                            # (a minute later)
for i in range(40):
    remote('Marco@Flood', 'marco %d f00d /%d' % (i, i))
    M._tick()                                  # (the room keeps 60 lines: read as they come)
n = len([m for m in said(mark) if m["nick"] == "Marco"])
check("forty calls in a minute: %d answers at most, the rest counted" % M.POLOS_A_MINUTE,
      M._st["skipped"] == 40 - M.POLOS_A_MINUTE and len(M._st["polos"]) == M.POLOS_A_MINUTE, (n, M._st["skipped"]))
check("... what it remembers stays bounded", len(M._st["seen"]) <= M.SEEN_MAX and len(M._st["heard"]) <= M.STUMPS_MAX
      and len(M._st["pending"]) <= M.STUMPS_MAX)
r = cmd(A, '/marco')
check("/marco says how many calls from elsewhere it answered", ("answered: %d" % (2 + M.POLOS_A_MINUTE)) in r[0], r)
M._st["polos"] = []

# 2. The caller.
for bad in ('/marco go', '/marco start x', '/marco start 1 2 3 4', '/marco start 99999'):
    check("%s: how to use it" % bad, cmd(A, bad)[0].startswith("/marco ·"), cmd(A, bad))
for bad in ('/marco start 0', '/marco start 31', '/marco start 5 4', '/marco start 5 1 14', '/marco start 5 1 601'):
    check("%s: the limits" % bad, "1 to 30 calls" in cmd(A, bad)[0], cmd(A, bad))
check("... and nothing started", M._st["run"] is None)
mark = last_id()
r = cmd(A, '/marco start 3 2 15')
check("/marco start 3 2 15: said back", "3 calls of 247 B (size 2) in #lora-mesh, one every 15 s" in r[0], r)
check("a second start meanwhile: told a run is under way", "under way" in cmd(A, '/marco start')[0])
M._tick()
run = M._st["run"]
calls = [m for m in said(mark) if m["nick"] == "Marco"]
check("the first call is in the room, as a person's line", len(calls) == 1 and calls[0]["body"] == M._line(1, run["tag"], 2)
      and not calls[0].get("bot"), calls)
check("... one the Stump sends on to the others", ("<Marco> " + calls[0]["body"]) in rrc_mesh._stump_lines(said(mark)))
check("... %d bytes, ending with its mark" % len(calls[0]["body"].encode()),
      len(calls[0]["body"].encode()) == 247 and calls[0]["body"].endswith(" /1"))
M._tick(); M._tick()
check("the next one waits its turn (15 s)", run["sent"] == 1)
r = cmd(A, '/marco')
check("/marco meanwhile: 1 of 3 sent, running, no polo yet", "1 of 3 calls sent" in r[0] and "running" in r[0] and "No polo yet" in r[1], r)
remote('Marco@macOS', 'polo 1 ' + run["tag"])
remote('Marco@LaBuche3', 'polo 1 ' + run["tag"])
remote('Marco@macOS', 'polo 1 ' + run["tag"])            # heard twice
remote('Marco@macOS', 'polo 1 0000')                     # another run's
remote('Marco@macOS', 'polo 2 ' + run["tag"])            # a call not made yet
M._tick()
check("polos from two Stumps: each timed once, others ignored",
      sorted(run["back"]) == ["LaBuche3", "macOS"] and list(run["back"]["macOS"]) == [1] and run["back"]["macOS"][1] >= 0, run["back"])
run["next"] = time.ticks_ms()
M._tick()
check("the second call when its time comes", run["sent"] == 2 and said()[-1]["body"] == M._line(2, run["tag"], 2), said()[-1])
run["back"]["macOS"][1] = 2140                           # (times set by hand: 2.1 s, then 5.9 s)
remote('Marco@macOS', 'polo 2 ' + run["tag"])
M._tick()
run["back"]["macOS"][2] = 5860
run["next"] = time.ticks_ms()
M._tick()
check("the third and last call", run["sent"] == 3)
M._tick()
check("no fourth", run["sent"] == 3 and not M._running(run))
r = cmd(W, '/marco', room='main')
check("/marco on the Wi-Fi page: the run, then a line for each Stump", len(r) == 3 and "3 of 3 calls sent" in r[0] and "finished" in r[0], r)
row = [l for l in r if l.startswith("macOS:")]
check("... macOS: 2 polos, round trip 2.1 / 4.0 / 5.9 s", row and "2 polos of 2, 0 lost" in row[0] and "2.1 / 4.0 / 5.9 s" in row[0], r)
for seq in run["at"]:                                    # two minutes later
    run["at"][seq] = time.ticks_add(time.ticks_ms(), -(M.LOST_S + 5) * 1000)
r = cmd(A, '/marco')
row = [l for l in r if l.startswith("macOS:")]
check("calls not answered after %d s: lost" % M.LOST_S, row and "2 polos of 3, 1 lost" in row[0], r)
check("... LaBuche3 answered one of three", any(l.startswith("LaBuche3: 1 polos of 3, 2 lost") for l in r), r)
check("three lines over the mesh, each within its length", len(r) == 3 and all(len(l) <= plugins.MESH_LINE_CHARS for l in r), [len(l) for l in r])
remote('Marco@macOS', 'polo 3 ' + run["tag"])
M._tick()
check("a polo that comes in late still counts", "3 polos of 3, 0 lost" in [l for l in cmd(A, '/marco') if l.startswith("macOS:")][0])

# 3. Stopping, and a run nobody answers.
cmd(A, '/marco start 5 0 15')
M._tick()
run = M._st["run"]
check("size 0: the bare call, 15 bytes", said()[-1]["body"] == "marco 1 %s /1" % run["tag"] and run["bytes"] == 15, said()[-1])
check("/marco stop", "stopped" in cmd(A, '/marco stop')[0].lower() and not M._running(run))
run["next"] = time.ticks_ms()
M._tick()
check("... no more calls", run["sent"] == 1)
run["at"][1] = time.ticks_add(time.ticks_ms(), -(M.LOST_S + 5) * 1000)
r = cmd(A, '/marco')
check("nobody answered: said plainly", "stopped" in r[0] and "No polo: no other Stump with this pack heard the calls" in r[1], r)
config.MARCOPOLO_ROOM = 'nowhere'
check("a room that isn't there: refused, nothing started", "no #nowhere room here" in cmd(A, '/marco start')[0])
config.MARCOPOLO_ROOM = 'lora-mesh'
i18n.set_lang(A, 'fr')
check("in the person's language", "série" in cmd(A, '/marco')[0])
check("/help lists it", any("/marco" in l for l in plugins.help_lines('en')), plugins.help_lines('en'))
check("!marco is not a command", not any(m["nick"] == "Marco" and m["body"].startswith("/") for m in said()))

print("FAILURES:", FAIL)
