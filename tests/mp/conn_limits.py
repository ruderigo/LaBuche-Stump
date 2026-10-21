# The web server's connection hygiene (conn.py) on the board's MicroPython:
# how many connections at once and per address, the deadline for the
# request line and headers (shorter when nearly full), a line that never
# ends, a body that stalls or trickles, a client that doesn't read the
# answer -- and the slot always given back.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import conn, config, time
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


config.CONN_MAX, config.CONN_PER_CLIENT = 4, 2
config.CONN_HEAD_S, config.CONN_HEAD_BUSY_S, config.CONN_IDLE_S = 0.4, 0.15, 0.3
config.CONN_MIN_BPS, config.CONN_RATE_GRACE_S = 1000, 0.2

# Admission
got = [conn.admit("10.0.0.1"), conn.admit("10.0.0.1"), conn.admit("10.0.0.1")]
check("one address: two connections, then 'client'", got == [None, None, "client"], got)
check("... the deadline for headers is the normal one", conn.head_timeout() == 0.4)
got = [conn.admit("10.0.0.2"), conn.admit("10.0.0.3"), conn.admit("10.0.0.4")]
check("the Stump full (4): 'busy' for anyone", got == [None, None, "busy"], got)
check("... three quarters full or more: the short deadline", conn.head_timeout() == 0.15)
for ip in ("10.0.0.1", "10.0.0.1", "10.0.0.2", "10.0.0.3"):
    conn.leave(ip)
check("all given back", conn.open_now() == 0 and conn.admit("10.0.0.1") is None)
conn.leave("10.0.0.1")


class Stall:
    """A client that connected and sends nothing more (or only `data`, a
    piece at a time, `gap` seconds apart)."""
    def __init__(s, data=b'', piece=1, gap=None):
        s.data, s.piece, s.gap = data, piece, gap
    async def read(s, n=-1):
        if s.data and s.gap is not None:
            await asyncio.sleep(s.gap)
        if not s.data or s.gap is None and not s.data:
            await asyncio.sleep(3600)
        out, s.data = s.data[:min(n, s.piece)], s.data[min(n, s.piece):]
        if not out:
            await asyncio.sleep(3600)
        return out


def timed(coro):
    t0 = time.ticks_ms()
    out = asyncio.run(coro)
    return out, time.ticks_diff(time.ticks_ms(), t0)


def served(raw, writer=None):
    """A connection through the web server; how long until it let go."""
    w = writer or Wr("10.0.0.30")
    _, ms = timed(barkeep._handle(raw, w))
    return w, ms


# The deadlines: one per connection, moved as bytes move, enforced by the
# reaper once a second (no timer per read: conn.py's top).
st = conn.stats()
w, ms = served(Stall())
check("headers that never come: let go at the deadline (by the reaper)",
      conn.stats()["dropped_head"] == st["dropped_head"] + 1 and 300 <= ms < 2600 and conn.open_now() == 0, (ms, conn.stats()))
st = conn.stats()
w, ms = served(Stall(b"GET / HTTP/1.1", piece=1, gap=0.05))
check("a request line dribbled a byte at a time: let go at the same deadline (bytes don't move it)",
      conn.stats()["dropped_head"] == st["dropped_head"] + 1 and ms < 2600, (ms, conn.stats()))
st = conn.stats()
w, ms = served(Stall(b"G" * 6000, piece=512, gap=0))
check("a line that never ends: let go before it fills the memory", conn.stats()["dropped_long"] == st["dropped_long"] + 1, conn.stats())
st = conn.stats()
up = (b"POST /upload HTTP/1.1\r\nX-Filename: stalled.bin\r\nAccept: application/json\r\n"
      b"Content-Length: 5000\r\n\r\n" + b"x" * 100)
w, ms = served(Stall(up, piece=512, gap=0))
check("an upload whose bytes stop: let go after the idle time, nothing kept",
      conn.stats()["dropped_idle"] == st["dropped_idle"] + 1 and ms < 2600
      and "stalled.bin" not in str(__import__("os").listdir(fserv.SHARED_DIR) if fserv.sd_ok else []), (ms, conn.stats()))


async def body_of(raw, want):
    r = conn.Reader(raw)
    r.start_body()
    got = b""
    while len(got) < want:
        d = await r.read(want - len(got))
        if not d:
            break
        got += d
    return got, r.why


(out, why), ms = timed(body_of(Stall(b"y" * 400, piece=10, gap=0.02), 400))
check("a body trickled too slowly: ended once past the grace time", why == "slow" and len(out) < 400, (len(out), why, ms))


class Deaf:
    """A client that doesn't read what it's sent."""
    def get_extra_info(s, n): return ("10.0.0.9", 1)
    async def awrite(s, b, *a): await asyncio.sleep(3600)
    async def aclose(s): pass


st = conn.stats()
w, ms = served(Rd([b"GET /node.json HTTP/1.1\r\n", b"\r\n"]), Deaf())
check("an answer nobody reads: let go after the idle time", conn.stats()["dropped_write"] == st["dropped_write"] + 1
      and ms < 2600 and conn.open_now() == 0, (ms, conn.stats()))

# Cheap: no task made per read any more (each read is a plain await).
class Fast:
    def __init__(s, n): s.n = n
    async def read(s, k=-1):
        if s.n <= 0:
            return b""
        s.n -= 1
        return b"z" * 64


async def many():
    r = conn.Reader(Fast(2000))
    r.start_body()
    t0 = time.ticks_us()
    while await r.read(64):
        pass
    return time.ticks_diff(time.ticks_us(), t0) / 2000


per, _ = timed(many())
check("a body read costs a few microseconds (no timer task each time)", per < 8, per)

# Through the web server itself: a silent connection takes no slot for
# long, a full Stump says 503 at once, and a normal request still works.
class Silent:
    async def read(s, n=-1):
        await asyncio.sleep(3600)


before = dict(conn.stats())
w = Wr("10.0.0.20")
_, ms = timed(barkeep._handle(Silent(), w))
check("the web server: a silent connection closed, its slot given back", conn.open_now() == 0 and ms < 1500
      and conn.stats()["dropped_head"] == before["dropped_head"] + 1, (conn.stats(), ms))
for i in range(4):
    conn.admit("10.0.1.%d" % i)
w = Wr("10.0.0.21")
_, ms = timed(barkeep._handle(Rd([b"GET /node.json HTTP/1.1\r\n", b"\r\n"]), w))
check("full: '503, retry in 2 s', at once", b"".join(w.w).startswith(b"HTTP/1.1 503") and b"Retry-After: 2" in b"".join(w.w)
      and ms < 500, (b"".join(w.w)[:80], ms))
for i in range(4):
    conn.leave("10.0.1.%d" % i)
s, ct, b = req("/node.json")
check("a normal request: served as before", s.startswith("200") and conn.open_now() == 0, (s, conn.open_now()))
s, ct, b = req("/upload", body=b"z" * 50, declared=5000, headers=("X-Filename: half.bin",))
check("an upload whose bytes stop coming: answered, nothing kept", not s.startswith("201")
      and "half.bin" not in str(__import__("os").listdir(fserv.SHARED_DIR) if fserv.sd_ok else []), (s, b[:200]))
print('FAILURES:', FAIL)
