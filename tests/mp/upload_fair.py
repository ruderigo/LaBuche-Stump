# Uploads, fair to everyone (fserv: the second step of hardening), on the
# board's MicroPython: a name already shared isn't replaced; one upload at
# a time per address; files an hour; a day's share of the card; files
# shared in the last hour not pushed out for someone else's; all uploads
# together held to UPLOAD_MAX_BPS.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config, os, time, guard
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


fserv.sd_ok = True
for d in (fserv.SHARED_DIR,):
    try:
        os.mkdir(d)
    except OSError:
        pass
config.UPLOAD_MAX_BPS = 0
config.UPLOAD_DAY_SHARE = 1
config.UPLOAD_FILES_PER_HOUR = 30
import i18n
for n in range(50, 70):
    i18n.set_lang('192.168.4.%d' % n, 'en')


def up(name, data, ip='192.168.4.50'):
    w = Wr(ip)
    hl = [b'POST /upload HTTP/1.1\r\n', ('Content-Length: %d\r\n' % len(data)).encode(),
          ('X-Filename: %s\r\n' % name).encode(), b'Accept: application/json\r\n', b'\r\n']
    asyncio.run(barkeep._handle(Rd(hl, data), w))
    raw = b''.join(w.w)
    head, _, body = raw.partition(b'\r\n\r\n')
    try:
        return head.split(b'\r\n')[0][9:12].decode(), json.loads(body)
    except ValueError:
        return head.split(b'\r\n')[0][9:12].decode(), {}


def content(name):
    with open(fserv.SHARED_DIR + '/' + name, 'rb') as f:
        return f.read()


s, r = up('map.pdf', b'the real map')
check("an upload: kept", s == '200' and r.get('name') == 'map.pdf' and content('map.pdf') == b'the real map', (s, r))
s, r = up('map.pdf', b'junk', ip='192.168.4.66')
check("the same name again: kept as 'map (2).pdf', the first one untouched",
      s == '200' and r.get('name') == 'map (2).pdf' and content('map.pdf') == b'the real map'
      and content('map (2).pdf') == b'junk', (s, r))

key = guard._k('192.168.4.51')
fserv.upload_start(key)
s, r = up('b.txt', b'x' * 10, ip='192.168.4.51')
check("a second upload while one is going on: refused, said why", s == '429' and r.get('error') == 'one_at_a_time'
      and 'still going' in r.get('message', ''), (s, r))
fserv.upload_end(key, 0)
s, r = up('b.txt', b'x' * 10, ip='192.168.4.51')
check("... once it's done: accepted", s == '200', (s, r))

config.UPLOAD_FILES_PER_HOUR = 3
got = [up('n%d.txt' % i, b'y', ip='192.168.4.52')[0] for i in range(4)]
check("files an hour: the 4th of 3 refused", got == ['200', '200', '200', '429'], got)
check("... someone else isn't affected", up('other.txt', b'z', ip='192.168.4.53')[0] == '200')
config.UPLOAD_FILES_PER_HOUR = 30

config.UPLOAD_DAY_SHARE = 1e-15
s, r = up('big.bin', b'q' * 5000, ip='192.168.4.54')
check("a day's share of the card: refused past it, said how much", s == '429' and r.get('error') == 'quota'
      and 'today' in r.get('message', ''), (s, r))
config.UPLOAD_DAY_SHARE = 1

# Fresh files aren't pushed out for someone else's upload.
with open(fserv.SHARED_DIR + '/old.bin', 'wb') as f:
    f.write(b'o' * 2000)
real_stats = fserv.get_storage_stats
fserv.get_storage_stats = lambda: (40000, 12500, 0.69)      # 27500 used: 3000 more needs 500 freed
names_before = sorted(os.listdir(fserv.SHARED_DIR))
check("a full card, every file shared just now: no room made (507), nothing deleted",
      not fserv.make_room_fifo(3000) and sorted(os.listdir(fserv.SHARED_DIR)) == names_before)
config.FRESH_S = 0
check("... once they're older than FRESH_S: the oldest make room", fserv.make_room_fifo(3000)
      and len(os.listdir(fserv.SHARED_DIR)) < len(names_before))
config.FRESH_S = None
fserv.get_storage_stats = real_stats


# All uploads together held to UPLOAD_MAX_BPS.
class Feed:
    def __init__(s, n, piece=4096):
        s.left, s.piece = n, piece
    async def read(s, n=-1):
        k = min(s.left, s.piece, n if n and n > 0 else s.piece)
        s.left -= k
        await asyncio.sleep(0)
        return b'v' * k


def timed(cap):
    t0 = time.ticks_ms()
    ok, n = asyncio.run(fserv.stream_to_file(Feed(96 * 1024), fserv.SHARED_DIR + '/cap.bin', 96 * 1024, max_bps=cap))
    return ok and n == 96 * 1024, time.ticks_diff(time.ticks_ms(), t0)


ok, fast = timed(0)
ok2, slow = timed(64 * 1024)
check("no cap: as fast as it comes", ok and fast < 1000, fast)
check("a 64 KB/s cap: 96 KB takes about 1.5 s", ok2 and 1100 <= slow <= 3000, slow)
fserv.upload_start('a')
fserv.upload_start('b')
ok3, shared = timed(128 * 1024)
fserv.upload_end('a', 0)
fserv.upload_end('b', 0)
check("two uploads going on: each gets half of the cap", ok3 and shared >= 1100, shared)
print('FAILURES:', FAIL)
