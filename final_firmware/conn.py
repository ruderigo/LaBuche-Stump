# The web server's connection hygiene: nobody can hold the Stump by
# keeping connections open.
#
# One loop serves everyone (uasyncio), so a connection that waits doesn't
# cost CPU -- but each one holds a socket, and the CAM has only about ten.
# A few phones opening connections and sending a byte a minute (the
# "slowloris" attack) used to lock every visitor out, and nothing ever
# timed them out. So:
#
#   admission   at most MAX connections at once (the CAM's sockets), and
#               PER_CLIENT from one address; beyond that a quick "503,
#               retry in 2 s" instead of a socket held.
#   headers     the request line and headers must arrive within HEAD_S
#               (HEAD_BUSY_S once the Stump is three quarters full), each
#               line at most LINE_MAX bytes -- a line that never ends
#               can't fill the CAM's memory either.
#   body        a read where nothing arrives for IDLE_S ends the body
#               (the callers already treat that as "the client went away":
#               a half upload is discarded), and past RATE_GRACE_S a body
#               must average MIN_BPS.
#   response    a write the client doesn't take within IDLE_S drops the
#               connection (a download nobody reads).
#
# Each can be changed in config.py (CONN_MAX, CONN_PER_CLIENT, CONN_HEAD_S,
# CONN_HEAD_BUSY_S, CONN_IDLE_S, CONN_MIN_BPS, CONN_RATE_GRACE_S).
# Counts of what was dropped: stats().

import time

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

LINE_MAX = 4096           # a request or header line, bytes
HEAD_MAX = 16384          # the request line and all headers together

_DEFAULTS = {"CONN_HEAD_S": 10, "CONN_HEAD_BUSY_S": 3, "CONN_IDLE_S": 15,
             "CONN_MIN_BPS": 512, "CONN_RATE_GRACE_S": 30}
_CAM = {"CONN_MAX": 10, "CONN_PER_CLIENT": 6}
_COMPUTER = {"CONN_MAX": 128, "CONN_PER_CLIENT": 16}

_open = {}                # address -> connections open now
_total = [0]
_stats = {"served": 0, "refused_busy": 0, "refused_client": 0, "dropped_head": 0,
          "dropped_long": 0, "dropped_idle": 0, "dropped_slow": 0, "dropped_write": 0}
_said = [0]               # when the log last summed up drops (ms)


class Dropped(Exception):
    """This connection is being let go (why: head, long, write)."""


_IS_PC = []


def _computer():
    if not _IS_PC:            # looked up once (on the CAM the import fails: costly each time)
        try:
            import stump_host  # noqa: F401
            _IS_PC.append(True)
        except ImportError:
            _IS_PC.append(False)
    return _IS_PC[0]


def setting(key):
    try:
        import config
        v = getattr(config, key, None)
    except ImportError:
        v = None
    if v is None:
        v = _DEFAULTS.get(key)
        if v is None:
            v = (_COMPUTER if _computer() else _CAM)[key]
    return v


def _now():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _since(t0):
    try:
        return time.ticks_diff(time.ticks_ms(), t0)
    except AttributeError:
        return _now() - t0


def _local(ip):
    return ip in ("127.0.0.1", "::1", "localhost")


def admit(ip):
    """A new connection from ip: None (taken in, counted), or why not:
    "busy" (the Stump is full) or "client" (that address has enough)."""
    if _total[0] >= setting("CONN_MAX"):
        _stats["refused_busy"] += 1
        _say()
        return "busy"
    if not _local(ip) and _open.get(ip, 0) >= setting("CONN_PER_CLIENT"):
        _stats["refused_client"] += 1
        _say()
        return "client"
    _open[ip] = _open.get(ip, 0) + 1
    _total[0] += 1
    _stats["served"] += 1
    return None


def leave(ip):
    n = _open.get(ip, 0) - 1
    if n > 0:
        _open[ip] = n
    else:
        _open.pop(ip, None)
    if _total[0] > 0:
        _total[0] -= 1


def head_timeout():
    """Seconds the request line and headers may take: shorter once the
    Stump is three quarters full, so held connections go first."""
    if _total[0] * 4 >= setting("CONN_MAX") * 3:
        return setting("CONN_HEAD_BUSY_S")
    return setting("CONN_HEAD_S")


def open_now():
    return _total[0]


def stats():
    out = dict(_stats)
    out["open"] = _total[0]
    return out


def note(why):
    """A connection let go: counted (and summed up in the log now and
    then, never once per connection -- an attack would flood it)."""
    key = "dropped_" + str(why)
    _stats[key] = _stats.get(key, 0) + 1
    _say()


def _say():
    if _said[0] and _since(_said[0]) < 60000:
        return
    _said[0] = _now() or 1
    dropped = sum(v for k, v in _stats.items() if k.startswith("dropped_"))
    refused = _stats["refused_busy"] + _stats["refused_client"]
    if dropped or refused:
        print("[web] connections: %d open, %d refused (full or too many from one address), "
              "%d dropped (too slow or silent) so far" % (_total[0], refused, dropped))


async def refuse(reader, writer, why):
    """The quick answer to a connection not taken in. What the client
    already sent is read first (briefly): closing a socket with unread
    bytes resets it, and Windows then shows the client a reset instead of
    the 503."""
    try:
        await asyncio.wait_for(reader.read(4096), 0.3)      # (rare: only when refusing)
    except Exception:
        pass
    try:
        await asyncio.wait_for(writer.awrite(
            "HTTP/1.1 503 Service Unavailable\r\nRetry-After: 2\r\nContent-Type: text/plain\r\n"
            "Content-Length: 5\r\nConnection: close\r\n\r\nbusy\n"), 2)
    except Exception:
        pass
    try:
        await asyncio.wait_for(writer.aclose(), 2)
    except Exception:
        pass


# ── Deadlines without a timer per read ─────────────────────────────────
# asyncio.wait_for on every read and write made a task each time -- about
# 14x the cost of the read itself, and garbage for the collector: on the
# ESP32 a noticeable share of each request, and much more of an upload.
# Instead each connection's task has ONE deadline here, moved forward as
# bytes move; a single reaper looks once a second and cancels the tasks
# whose deadline passed. Reads and writes are plain awaits again.
_live = {}            # task -> [deadline ms, why]
_reaper = [None]


def _current():
    try:
        return asyncio.current_task()
    except Exception:
        return None


def _set(task, ms, why):
    if task is not None:
        e = _live.get(task)
        if e is None:
            _live[task] = [_now() + int(ms), why]
        else:
            e[0], e[1] = _now() + int(ms), why


def watch(task, head_s=None):
    """A connection's task starts: its headers' deadline. Starts the
    reaper the first time."""
    _set(task, (head_s if head_s is not None else head_timeout()) * 1000, "head")
    r = _reaper[0]
    if r is None or (hasattr(r, "done") and r.done()):     # (a new event loop: a new reaper)
        try:
            _reaper[0] = asyncio.create_task(_reap())
        except Exception:
            _reaper[0] = None


def unwatch(task):
    _live.pop(task, None)


def why_ended(task):
    """Why the reaper cancelled this task (or None)."""
    e = _ended.pop(task, None)
    return e


_ended = {}


async def _reap():
    while True:
        await asyncio.sleep(1)
        now = _now()
        for task, e in list(_live.items()):
            try:
                late = time.ticks_diff(now, e[0]) if hasattr(time, "ticks_diff") else now - e[0]
            except Exception:
                late = now - e[0]
            if late > 0:
                _live.pop(task, None)
                _ended[task] = e[1]
                if len(_ended) > 64:
                    _ended.pop(next(iter(_ended)))
                try:
                    task.cancel()
                except Exception:
                    pass


class Reader:
    """The connection's reader: readline() for the request line and
    headers (their deadline set by watch(); lines at most LINE_MAX,
    HEAD_MAX in all), read() for the body (the idle deadline moved
    forward on each read; a body trickled under MIN_BPS past the grace
    time ends: b"", as a client gone away)."""

    def __init__(self, raw, task=None):
        self._raw = raw
        self._task = task
        self._buf = b""
        self._head_bytes = 0
        self._body_t0 = None
        self._body_n = 0
        self.why = None
        # Read once per connection, not once per read.
        self._idle_ms = int(setting("CONN_IDLE_S") * 1000)
        self._grace_ms = int(setting("CONN_RATE_GRACE_S") * 1000)
        self._min_bps = setting("CONN_MIN_BPS")

    def start_body(self):
        """The headers are in: from here, read() and the body's limits."""
        self._body_t0 = _now()
        _set(self._task, self._idle_ms, "idle")

    async def readline(self):
        while b"\n" not in self._buf:
            if len(self._buf) >= LINE_MAX or self._head_bytes + len(self._buf) >= HEAD_MAX:
                raise Dropped("long")
            data = await self._raw.read(512)
            if not data:
                line, self._buf = self._buf, b""
                return line
            self._buf += data
        i = self._buf.index(b"\n") + 1
        line, self._buf = self._buf[:i], self._buf[i:]
        self._head_bytes += len(line)
        return line

    async def read(self, n=-1):
        if self._buf:
            if n is None or n < 0 or n >= len(self._buf):
                out, self._buf = self._buf, b""
            else:
                out, self._buf = self._buf[:n], self._buf[n:]
            self._body_n += len(out)
            return out
        if self.why:
            return b""
        if self._body_t0 is None:
            self.start_body()
        _set(self._task, self._idle_ms, "idle")
        data = await self._raw.read(n if n and n > 0 else 2048)
        if not data:
            return b""
        self._body_n += len(data)
        t = _since(self._body_t0)
        if t > self._grace_ms and self._body_n * 1000 < self._min_bps * t:
            self.why = "slow"
            note("slow")
            return b""
        return data


class Writer:
    """The connection's writer: each write moves the deadline forward by
    CONN_IDLE_S -- a client that doesn't take it in that time is let go
    by the reaper. Attributes set on it (the page's cookie, no-store) stay
    on it."""

    def __init__(self, raw, task=None):
        self._raw = raw
        self._task = task
        self._idle_ms = int(setting("CONN_IDLE_S") * 1000)

    async def awrite(self, data, *a):
        _set(self._task, self._idle_ms, "write")
        await self._raw.awrite(data, *a)

    async def aclose(self):
        try:
            await self._raw.aclose()
        except BaseException:
            pass

    def get_extra_info(self, name):
        return self._raw.get_extra_info(name)
