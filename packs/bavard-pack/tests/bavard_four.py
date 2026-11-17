# The Bavard pack (run it with tests/run.py beside this file): two bots in one process, on a fake
# clock and a fake Reticulum (announces and links are in-memory), through
# the real plugins.py and its posting budget. Alone they post numbers;
# once they hear each other they play the corpus turn by turn, losing and
# repeating nothing; a silent link sends them back to numbers, and they
# pick the play up where it was when they find each other again.
import sys, os, time
class _M: pass
_m = _M(); _m.aes = lambda *a, **k: None
sys.modules['ucryptolib'] = _m; sys.modules['cryptolib'] = _m
sys.path.insert(0, '.')
try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

FAIL = []
def check(name, cond, info=''):
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(info)))
    if not cond: FAIL.append(name)

# Four copies of the plugin: four bots (four names, four budgets).
for x in 'bcd':
    os.system('rm -rf bavard%s && cp -r bavard bavard%s' % (x, x))

import config
for x in ('', 'B', 'C', 'D'):
    setattr(config, 'BAVARD%s_NICK' % x, 'Bavard' + x); setattr(config, 'BAVARD%s_ROOM' % x, 'bavard')
    setattr(config, 'BAVARD%s_PACE' % x, 'quick'); setattr(config, 'BAVARD%s_ON' % x, 'on')

SD = os.getcwd() + '/sdx'
os.system('rm -rf %s && mkdir -p %s/shared %s/tools/bavard %s/home' % (SD, SD, SD, SD))
for f in ('plugins_state.json', 'plugins_loading'):
    try: os.remove(f)
    except OSError: pass
import fserv
fserv.SD_MOUNT = SD; fserv.SHARED_DIR = SD + '/shared'; fserv.TOOLS_DIR = SD + '/tools'
fserv.HOME_DIR = SD + '/home'; fserv.LEDGER_FILE = SD + '/l.json'; fserv.sd_ok = True

# A tiny corpus: two plays, so the bots also go from one to the next.
PLAYS = {
    'un': ('Pièce Une', [('S', 'I, 1'), ('ALCESTE', 'a1'), ('PHILINTE', 'p1'), ('ALCESTE', 'a2'),
                         ('ORONTE', 'o1'), ('ALCESTE', 'a3'), ('S', 'I, 2'), ('PHILINTE', 'p2'),
                         ('ORONTE', 'o2'), ('ORONTE', 'o3'), ('ALCESTE', 'a4')]),
    'deux': ('Pièce Deux', [('S', 'I, 1'), ('VALÈRE', 'v1'), ('ÉLISE', 'e1'), ('VALÈRE', 'v2'),
                            ('HARPAGON', 'h1'), ('ÉLISE', 'e2')]),
}
with open(SD + '/tools/bavard/plays.txt', 'w') as idx:
    for slug in ('un', 'deux'):
        title, recs = PLAYS[slug]
        n = len([r for r in recs if r[0] != 'S'])
        idx.write('%s\t%s\t%d\n' % (slug, title, n))
        with open(SD + '/tools/bavard/%s.txt' % slug, 'w') as f:
            f.write('T\t%s\n' % title)
            for who, text in recs:
                f.write(('S\t%s\n' % text) if who == 'S' else ('L\t%s\t%s\n' % (who, text)))
SCRIPT = []
for slug in ('un', 'deux'):
    SCRIPT += [(PLAYS[slug][0], w, t) for w, t in PLAYS[slug][1] if w != 'S']

import plugins

# ── a fake clock for the bots and the budget ──
class Clock:
    t = 0
    base = int(time.time())
    def ticks_ms(self): return self.t
    def ticks_add(self, a, b): return a + b
    def ticks_diff(self, a, b): return a - b
    def time(self): return self.base + self.t // 1000
    def time_ns(self): return (self.base * 1000 + self.t) * 1000000
    def gmtime(self, s=None): return time.gmtime(self.time() if s is None else s)
CLOCK = Clock()
plugins._now_ms = CLOCK.ticks_ms
import barkeep
barkeep.plugin_posts_reset()

class FakeAsync:
    async def sleep_ms(self, ms):
        CLOCK.t += int(ms)
        await asyncio.sleep(0)
    async def sleep(self, s):
        CLOCK.t += int(s * 1000)
        await asyncio.sleep(0)

# ── a fake Reticulum: announces and links in memory ──
HEX = {'bavard': 'aa' * 16, 'bavardb': 'bb' * 16, 'bavardc': 'cc' * 16, 'bavardd': 'dd' * 16}
NET, ANN, QUEUE = {}, [], []
WIRE = [True]                     # False: packets are lost, silently

class FakeDoor:
    def __init__(self, plugin):
        self.plugin = plugin; self.hexhash = HEX[plugin]; self._on_link = None
        NET[self.hexhash] = self
    def on_link(self, fn): self._on_link = fn
    def handle(self, path, fn): pass
    def announce(self, app_data=None):
        for pl, fn in ANN:
            if pl != self.plugin:
                QUEUE.append(lambda fn=fn: fn(self.hexhash, app_data))
        return True

class End:
    def __init__(self): self.peer = None; self.cb = None; self.open = True; self.close_cb = None
    def send(self, data):
        if not self.open: return False
        if WIRE[0]:
            p = self.peer
            QUEUE.append(lambda: p.cb and p.cb(bytes(data)))
        return True
    def on_packet(self, fn): self.cb = fn
    def on_close(self, fn): self.close_cb = fn
    def is_open(self): return self.open
    ident = None
    def remote_hex(self): return self.ident
    def close(self):
        if self.open:
            self.open = self.peer.open = False
            if self.peer.close_cb: QUEUE.append(self.peer.close_cb)

LINKS = [0]
def fake_node(self, app, *aspects): return FakeDoor(self.name)
def fake_on_announce(self, app, aspects, fn): ANN.append((self.name, fn))
def fake_connect(self, dest_hex, app, aspects, identify=False):
    a, b = End(), End(); a.peer, b.peer = b, a
    if identify:
        b.ident = 'id-' + HEX.get(self.name, '??')     # what the called side sees
    door = NET[dest_hex]; LINKS[0] += 1
    QUEUE.append(lambda: door._on_link(b))
    return a
plugins.Api.node = fake_node
plugins.Api.on_announce = fake_on_announce
plugins.Api.connect = fake_connect

# Every post the budget sees, accepted or not.
POSTS, REFUSED = [], [0]          # what was posted; how many were refused (the heap)
_real_post = plugins._post
def spy(plugin, nick, room, text, wait=True):
    ok = _real_post(plugin, nick, room, text, wait)
    if ok:
        POSTS.append((CLOCK.t, plugin, text, ok))
    else:
        REFUSED[0] += 1
    return ok
plugins._post = spy

running = plugins.load_all()
check('four bots loaded', all(n in running for n in ('bavard', 'bavardb', 'bavardc', 'bavardd')), running)
import bavard.install as A
import bavardb.install as B
import bavardc.install as C
import bavardd.install as D
BOTS = (A, B, C, D)
for mod in BOTS:
    mod._dest_of = lambda rid: rid[3:]       # a fake identity -> its bot's destination
    mod.time = CLOCK
    mod.asyncio = FakeAsync()
    mod._st['started'] = 0
    mod._st['next_ms'] = 0

def pump():
    while QUEUE:
        QUEUE.pop(0)()

DUE = {'ann': 0, 'flush': 0}
TWO = [0]                          # times two duets were up at once
def duets():
    return sorted(m._pair() for m in BOTS if m._st['mode'] == 'duet' and m._peer.get('up'))
def watch():
    if len(set(duets())) > 1:
        TWO[0] += 1
async def run(seconds, announce=True):
    end = CLOCK.t + seconds * 1000
    while CLOCK.t < end:
        for mod in BOTS:
            await mod._tick()
            pump()
        watch()
        # The node's other timers, by time gone by (a bot's turn can jump
        # the clock by minutes).
        if announce and CLOCK.t - DUE['ann'] >= 60000:
            DUE['ann'] = CLOCK.t
            for mod in BOTS: mod._announce()
            pump()
        if CLOCK.t - DUE['flush'] >= 10000:
            DUE['flush'] = CLOCK.t
            for mod in BOTS: mod._flush(); mod._save_state()
        CLOCK.t += 1000

def window_max(plugin, span_ms=60000):
    """Most lines a bot got posted in any sliding window of span_ms."""
    ts = [t for t, pl, _, ok in POSTS if pl == plugin and ok]
    best, j = 0, 0
    for i in range(len(ts)):
        while ts[i] - ts[j] >= span_ms:
            j += 1
        best = max(best, i - j + 1)
    return best

def budget_held(since=0):
    """BarKeep's budget: 6 lines in ANY rolling minute (plugin API 4; a
    fixed window let 11 through)."""
    ok = True
    for name in ('bavard', 'bavardb'):
        ok = ok and window_max(name) <= barkeep.PLUGIN_POSTS_PER_MIN
    return ok

def molière(plugin, since=0):
    # (order posted, bot, speaker, line): POSTS is in the order the budget saw them.
    out = []
    for k, (t, pl, text, ok) in enumerate(POSTS):
        if pl == plugin and ok and t >= since and ' · ' in text:
            title_scene, _, rest = text.partition('] ')[2].partition(' · ')
            who, _, line = rest.partition(' — ')
            out.append((k, pl, who, line))
    return out


def mode(m): return m._st['mode']
def played(since=0):
    """(bot, speaker, line) of the play lines posted, in order."""
    out = []
    for t, pl, text, ok in POSTS:
        if ok and t >= since and ' · ' in text:
            rest = text.partition('] ')[2].partition(' · ')[2]
            who, _, line = rest.partition(' — ')
            out.append((pl, who, line))
    return out

# ── 1. four bots: one duet, the two smallest; the others stay out ──
for m in BOTS:
    m._room_shared = lambda: True          # #bavard goes to other Stumps: only the duet shows
asyncio.run(run(400))
check('one duet: the two smallest (aa+bb)', mode(A) == 'duet' and mode(B) == 'duet' and A._peer.get('hex') == HEX['bavardb'],
      [(mode(m), m._peer.get('hex', '')[:4]) for m in BOTS])
check('... the other two stay out of it (no second duet)', mode(C) == 'solo' and mode(D) == 'solo' and TWO[0] == 0, TWO)
check('... and they know a duet is going', C._duet_elsewhere() and D._duet_elsewhere())
heard_at = 60000                           # the first announces (run() sends them every minute)
nums = [p for p in POSTS if p[1] in ('bavardc', 'bavardd') and '#' in p[2] and p[3] and p[0] > heard_at + 2000]
check('... no numbers from them in the shared room, once they hear the others', not nums, nums[:3])
early = [p for p in POSTS if p[1] in ('bavardc', 'bavardd') and '#' in p[2] and p[3] and p[0] < heard_at]
check('... before that, alone as far as each knows: their numbers (1.3)', early, len(early))
t1 = CLOCK.t
asyncio.run(run(300))
gaps = [b[0] - a[0] for a, b in zip([p for p in POSTS if p[0] > t1 and ' · ' in p[2]], [p for p in POSTS if p[0] > t1 and ' · ' in p[2]][1:])]
check('the duet goes on steadily (a gap at most a budget minute, never the old 90 s stall)', played(t1) and max(gaps) < 65000, (len(played(t1)), max(gaps) if gaps else None))

# ── 2. a turn pass lost: passed again within seconds, not 90 s ──
lost = [0]
real_send = End.send
def lossy(self, data):
    if lost[0] == 0 and b'"k": "go"' in bytes(data):
        lost[0] = 1
        return True                        # gone
    return real_send(self, data)
End.send = lossy
t2 = CLOCK.t
n2 = len(played())
asyncio.run(run(60))
End.send = real_send
check('a lost turn pass: the duet carries on within a minute', lost[0] == 1 and len(played()) > n2 + 2, (lost, len(played()) - n2))

# ── 3. one of the two drops: another takes over, from where it was ──
before = played()
last_line = before[-1]
B._panel_post({'do': 'pause'}, 'en')       # bb leaves
pump()
t3 = CLOCK.t
asyncio.run(run(200))
check('bb left: aa holds its place, cc (the smallest free) takes over', mode(A) == 'duet' and mode(C) == 'duet'
      and A._peer.get('hex') == HEX['bavardc'] and mode(D) == 'solo' and mode(B) == 'solo',
      [(mode(m), m._peer.get('hex', '')[:4]) for m in BOTS])
after = played(t3)
lines_only = [(w, t) for _, w, t in SCRIPT]
k = lines_only.index((last_line[1], last_line[2]))
nxt = lines_only[(k + 1) % len(lines_only)]
check('... the play goes on from there (the next line; at most the last one again, nothing skipped)',
      after and (after[0][1], after[0][2]) in (nxt, (last_line[1], last_line[2])), (last_line, nxt, after[:3]))
check('never two duets at once', TWO[0] == 0, TWO)

# ── 4. two duets formed at once (heard late): the one with the smaller pair stays ──
fake = '00' * 16 + '01' * 16
for m in (A, C):
    m._announce_heard('01' * 16, b'bavard/1|Ghost|d' + fake.encode())
pump()
asyncio.run(run(5, announce=False))
check('another duet with a smaller pair heard: ours stops', mode(A) == 'solo' and mode(C) == 'solo', (mode(A), mode(C)))
check('... and nobody starts one while it goes on', all(mode(m) == 'solo' for m in BOTS))
asyncio.run(run(A._seen_s() + 200))
check('... once it\'s gone quiet, a duet again (one)', len(set(duets())) == 1 and TWO[0] == 0, duets())

# ── 4b. the others go quiet for good: a lone Bavard says its numbers, shared room or not (1.3) ──
for m in (A, B, C):
    m._panel_post({'do': 'pause'}, 'en')
pump()
t4 = CLOCK.t
asyncio.run(run(D._seen_s() + 120, announce=False))
alone = [p for p in POSTS if p[1] == 'bavardd' and '#' in p[2] and p[3] and p[0] > t4 + D._seen_s() * 1000]
check('the others gone quiet: the last Bavard, alone in the shared room, says its numbers', mode(D) == 'solo' and alone, len(alone))
check('... and not while the others were still heard', not [p for p in POSTS if p[1] == 'bavardd' and '#' in p[2] and p[3]
                                                        and t4 + 5000 < p[0] < t4 + D._seen_s() * 1000 - 60000])
for m in (A, B, C):
    m._panel_post({'do': 'resume'}, 'en')
pump()

# ── 5. a call under way isn't dropped for another one ──
D._peer.clear(); D._st['mode'] = 'solo'
D._dial(HEX['bavardb'], 'B')
link_before = D._peer.get('link')
other = End(); other.peer = End(); other.peer.peer = other
D._incoming(other.peer)
check('a call under way: an incoming one is refused, ours kept', D._peer.get('link') is link_before and not other.peer.open)

print('FAILURES:', FAIL)
