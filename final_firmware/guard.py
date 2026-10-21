# Project Stump -- guard.py: rate limits and password lockouts.
#
# One small place for "not so fast", shared by the chat (web and mesh),
# the billboard and every password prompt, all in RAM:
#
#   allow(kind, key, ...)   a token bucket per (kind, key): `burst` at once,
#                           then one every `every` seconds.
#   window(kind, key, ...)  at most `n` in any `per` seconds.
#   password(check, key)    runs check(); after LOCK_AFTER wrong passwords
#                           in a row from the same key, refuses for
#                           LOCK_S seconds, doubling each time (up to
#                           LOCK_MAX_S). A right password clears it.
#
# Keys are whatever identifies a sender: a visitor's IP on the web, an
# LXMF address on the mesh. The tables are bounded: the oldest key goes
# first, so a crowd of addresses can't fill the memory.
#
# The chat sets the current caller (caller()) for the length of one line,
# so a password check deep inside a plugin's command knows who's asking
# without every plugin passing it along.

import time

MAX_KEYS = 64
LOCK_AFTER = 5
LOCK_S = 30
LOCK_MAX_S = 900

_buckets = {}       # (kind, key) -> [milli-tokens, last_ms]
_windows = {}       # (kind, key) -> [times_ms]
_fails = {}         # key -> [count, locked_until_ms, lock_s]
_caller = ["?"]
# A visitor's ID (a browser's cookie, a logged-in profile) -> its network
# address: the limits count per address, so a new cookie or another
# profile doesn't reset them (barkeep sets it on each request).
_net = {}


def net(visitor, address):
    if visitor and address and visitor != address:
        _net[visitor] = address
        _bound(_net)


def _k(key):
    return _net.get(key, key)


def _clock():
    """Milliseconds, as an integer: the ESP32's floats are single
    precision, which can't even hold today's time to the second."""
    try:
        return time.time_ns() // 1000000
    except AttributeError:
        return int(time.time()) * 1000


def _bound(table):
    while len(table) > MAX_KEYS:
        oldest = None
        for k in table:
            oldest = k
            break
        table.pop(oldest, None)


def allow(kind, key, every=2, burst=3):
    """True if one more is allowed now (and counts it)."""
    now = _clock()
    every_ms = max(1, int(every * 1000))
    full = burst * 1000
    k = (kind, _k(key))
    b = _buckets.get(k)
    if b is None:
        b = _buckets[k] = [full, now]
        _bound(_buckets)
    tokens = min(full, b[0] + (now - b[1]) * 1000 // every_ms)
    b[1] = now
    if tokens < 1000:
        b[0] = tokens
        return False
    b[0] = tokens - 1000
    return True


def window(kind, key, n=3, per=60):
    """True if fewer than n happened in the last `per` seconds (and counts
    this one); False otherwise."""
    now = _clock()
    k = (kind, _k(key))
    times = [t for t in _windows.get(k, []) if now - t < per * 1000]
    if len(times) >= n:
        _windows[k] = times
        return False
    times.append(now)
    _windows[k] = times
    _bound(_windows)
    return True


def locked(key=None):
    """Seconds left before `key` may try a password again (0: it may)."""
    now = _clock()
    f = _fails.get(_k(_caller[0] if key is None else key))
    if not f or f[1] <= now:
        return 0
    return (f[1] - now) // 1000 + 1


def _fail(key, after=LOCK_AFTER):
    """One more wrong password for key: seconds it's locked now (0: not)."""
    f = _fails.get(key)
    if f is None:
        f = _fails[key] = [0, 0, LOCK_S // 2]
        _bound(_fails)
    f[0] += 1
    if f[0] >= after:
        f[2] = min(LOCK_MAX_S, f[2] * 2)
        f[1] = _clock() + f[2] * 1000
        f[0] = after - 1             # one more wrong one locks again, for longer
        print("[guard] %s: too many wrong passwords, locked for %d s" % (key, f[2]))
        return f[2]
    return 0


# All password checks together, from everyone: at most PW_BUDGET every
# PW_BUDGET_S. Hashing a profile's password is long on the CAM; several
# devices each within their own limit could otherwise keep it hashing.
PW_BUDGET = 10
PW_BUDGET_S = 10


def pw_budget():
    """True if one more costly password check may run now (counts it)."""
    return window("pwcost", "*", PW_BUDGET, PW_BUDGET_S)


async def password_async(check, keys, afters=None):
    """(ok, wait_s) for an async check, locked by EVERY key in keys: the
    address, and the account or the door ("nick:rod", "admin"), so wrong
    guesses from many devices lock the account too. afters: per key, how
    many wrong ones lock it (LOCK_AFTER by default). wait_s > 0: not run."""
    keys = [_k(k) for k in keys]
    afters = afters or {}
    wait = max([locked(k) for k in keys] or [0])
    if wait:
        return False, wait
    try:
        ok = bool(await check())
    except Exception:
        ok = False
    if ok:
        for k in keys:
            _fails.pop(k, None)
        return True, 0
    wait = 0
    for k in keys:
        wait = max(wait, _fail(k, afters.get(k, LOCK_AFTER)))
    return False, wait


def password_many(check, keys, afters=None):
    """password() for a cheap sync check, locked by every key in keys
    (see password_async)."""
    keys = [_k(k) for k in keys]
    afters = afters or {}
    wait = max([locked(k) for k in keys] or [0])
    if wait:
        return False, wait
    try:
        ok = bool(check())
    except Exception:
        ok = False
    if ok:
        for k in keys:
            _fails.pop(k, None)
        return True, 0
    wait = 0
    for k in keys:
        wait = max(wait, _fail(k, afters.get(k, LOCK_AFTER)))
    return False, wait


def password(check, key=None):
    """(ok, wait_s): runs check() unless `key` is locked out. wait_s > 0
    means it wasn't run: too many wrong passwords, try again then."""
    key = _k(_caller[0] if key is None else key)
    wait = locked(key)
    if wait:
        return False, wait
    try:
        ok = bool(check())
    except Exception:
        ok = False
    if ok:
        _fails.pop(key, None)
        return True, 0
    f = _fails.get(key)
    if f is None:
        f = _fails[key] = [0, 0, LOCK_S // 2]
        _bound(_fails)
    f[0] += 1
    if f[0] >= LOCK_AFTER:
        f[2] = min(LOCK_MAX_S, f[2] * 2)
        f[1] = _clock() + f[2] * 1000
        f[0] = LOCK_AFTER - 1        # one more wrong one locks again, for longer
        print("[guard] %s: too many wrong passwords, locked for %d s" % (key, f[2]))
        return False, f[2]
    return False, 0


def caller(key):
    """Sets who's asking, for password() calls with no key."""
    _caller[0] = key or "?"


def reset():
    """Forgets everything (tests)."""
    _buckets.clear()
    _windows.clear()
    _fails.clear()
    _net.clear()
    _caller[0] = "?"
