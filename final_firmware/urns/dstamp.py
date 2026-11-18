# Destination stamps (1.0.0-beta.86): the proof of work Reticulum 1.6.1
# puts on announces. Off unless config.ANNOUNCE_STAMP (/admin -> Stump
# settings -> Announcing).
#
# A stamp is 14 bytes such that SHA-256(destination hash + public key +
# name hash + stamp) starts with COST zero bits (Reticulum 1.6.1's
# DESTINATION_VALUE, 25: what a node enforcing stamps asks by default is
# less, 23.5). It depends only on the destination, never on the time, so
# it is found once and kept (rns/stamps.json). Finding it takes about
# 2^25 tries: a computer Stump does it in the background in about a
# minute; a board does it a slice at a time between everything else
# (much longer), or the Provisioner does it for the board on a computer
# (python3 provisioner.py --stamp-announces) from rns/stamps_wanted.json,
# the destinations the Stump announces.
#
# On an announce the stamp goes after the app data as 1.6.1 puts it:
# 0xC1, flags (0), the stamp, then the link-request cost as two bytes --
# 0 here: a Stump asks no stamp of links to it. Without a stamp yet the
# announce goes as before. Reading other nodes' stamps: Identity.without_stamp.

import os
import time

try:
    import ujson as json
except ImportError:
    import json

COST = 25
STAMP_LEN = 14
EXT_MARKER = 0xC1
EXT_LEN = 2 + STAMP_LEN + 2

ENABLED = [False]
ON_FOUND = [None]      # called when a stamp is found (the node announces again soon)
STAMPS_FILE = "stamps.json"
WANTED_FILE = "stamps_wanted.json"

_dir = [None]          # the rns/ folder
_stamps = {}           # dest hex -> stamp bytes (checked)
_wanted = {}           # dest hex -> stamp material (bytes), the destinations announced
_wanted_saved = [0]    # how many were in the saved list
_job = {"dest": None, "tries": 0, "since": None}
stats = {"announces": 0, "bytes": 0, "stamp_bytes": 0, "found": 0}


def _ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


def _since(t0):
    try:
        return time.ticks_diff(time.ticks_ms(), t0)
    except AttributeError:
        return int(time.time() * 1000) - t0


def _sha256(data):
    try:
        import uhashlib as hl
    except ImportError:
        import hashlib as hl
    return hl.sha256(data).digest()


def _zeros_ok(digest, cost):
    """The digest starts with at least `cost` zero bits."""
    full, rest = cost // 8, cost % 8
    for i in range(full):
        if digest[i]:
            return False
    return not rest or digest[full] < (1 << (8 - rest))


def valid(material, stamp, cost=None):
    return len(stamp) == STAMP_LEN and _zeros_ok(_sha256(material + stamp), COST if cost is None else cost)


def material(dest):
    return dest.hash + dest.identity.get_public_key() + dest.name_hash


def ext(stamp):
    return bytes([EXT_MARKER, 0x00]) + stamp + b"\x00\x00"


def _path(name):
    d = _dir[0]
    if not d:
        return None
    return d + "/" + name


def _read(name):
    p = _path(name)
    if not p:
        return {}
    try:
        with open(p) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(name, d):
    p = _path(name)
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        try:
            os.remove(p)
        except OSError:
            pass
        os.rename(tmp, p)
    except Exception:
        pass


def load(rns_dir=None):
    """The stamps kept in rns/stamps.json (the Stump's own, and those the
    Provisioner computed for it). Only ones that check are taken."""
    if rns_dir:
        _dir[0] = rns_dir
    for h, s in _read(STAMPS_FILE).items():
        try:
            if h not in _stamps:
                stamp = bytes.fromhex(s)
                if h in _wanted and not valid(_wanted[h], stamp):
                    continue
                _stamps[h] = stamp
        except Exception:
            pass


def _save():
    # Merged with what's on disk: the Provisioner may have added some.
    d = _read(STAMPS_FILE)
    for h, s in _stamps.items():
        d[h] = s.hex()
    _write(STAMPS_FILE, d)


def want(dest):
    """A destination this Stump announces: listed for the stamp maker and
    the Provisioner. Its stamp, when it has one and stamping is on."""
    h = dest.hash.hex()
    if h not in _wanted:
        _wanted[h] = material(dest)
        st = _stamps.get(h)
        if st is not None and not valid(_wanted[h], st):
            del _stamps[h]          # kept for another identity: not this one's
        if ENABLED[0]:
            _save_wanted()
    if not ENABLED[0]:
        return None
    return _stamps.get(h)


def _save_wanted():
    if len(_wanted) == _wanted_saved[0]:
        return
    d = _read(WANTED_FILE)
    for h, m in _wanted.items():
        d[h] = m.hex()
    d = {h: m for h, m in d.items() if h in _wanted}
    _write(WANTED_FILE, d)
    _wanted_saved[0] = len(_wanted)


def set_enabled(on):
    ENABLED[0] = bool(on)
    if ENABLED[0]:
        _save_wanted()


def counted(packet_len, stamped):
    stats["announces"] += 1
    stats["bytes"] += packet_len
    if stamped:
        stats["stamp_bytes"] += EXT_LEN


def missing():
    return [h for h in _wanted if h not in _stamps]


def status():
    """For /admin and /meter: on?, stamped, wanted, the one being worked
    on and its tries so far."""
    return {"on": ENABLED[0], "stamped": sum(1 for h in _wanted if h in _stamps), "wanted": len(_wanted),
            "working": _job["dest"], "tries": _job["tries"]}


def step(budget_ms=40):
    """Works on a missing stamp for about budget_ms. True when one was
    found (it is then kept and saved)."""
    if not ENABLED[0]:
        return False
    todo = missing()
    if not todo:
        _job["dest"] = None
        return False
    h = _job["dest"] if _job["dest"] in todo else todo[0]
    if _job["dest"] != h:
        _job["dest"], _job["tries"] = h, 0
    m = _wanted[h]
    try:
        import uhashlib as hl
    except ImportError:
        import hashlib as hl
    full, rest = COST // 8, COST % 8
    limit = 1 << (8 - rest) if rest else 0
    t0 = _ms()
    urandom = os.urandom
    n = 0
    while True:
        s = urandom(STAMP_LEN)
        d = hl.sha256(m + s).digest()
        n += 1
        ok = True
        for i in range(full):
            if d[i]:
                ok = False
                break
        if ok and (not rest or d[full] < limit):
            _stamps[h] = s
            _job["tries"] += n
            stats["found"] += 1
            _job["dest"] = None
            _save()
            return True
        if n & 255 == 0 and _since(t0) >= budget_ms:
            _job["tries"] += n
            return False


async def loop(budget_ms=40, pause_ms=10):
    """The stamp maker: idle while stamping is off or every destination
    has its stamp; otherwise slices of work, and a look at rns/stamps.json
    every minute (the Provisioner may have filled it)."""
    try:
        import uasyncio as asyncio
    except ImportError:
        import asyncio
    last_read = _ms()
    while True:
        if not ENABLED[0] or not missing():
            await asyncio.sleep(2)
            continue
        if _since(last_read) > 60000:
            last_read = _ms()
            load()
            if not missing():
                continue
        if step(budget_ms):
            print("[stamps] announce stamp found: " + _job_last() + " (" + str(COST) + " bits)")
            if ON_FOUND[0] is not None:
                try:
                    ON_FOUND[0]()
                except Exception:
                    pass
        try:
            await asyncio.sleep_ms(pause_ms)
        except AttributeError:
            await asyncio.sleep(pause_ms / 1000)


def _job_last():
    return str(len(_stamps)) + "/" + str(len(_wanted))
