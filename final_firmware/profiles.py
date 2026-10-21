# Registered profiles: a nick and a password, for visitors without a
# FireFly. Logged in from any browser, a profile carries its nick (no one
# else can take it), its credits and its private messages. Shared by the
# Stumps of a chain (rrc_mesh sends the records over the chain link only,
# never the radio). Nothing here opens hybrid's other rooms: those take a
# FireFly's /auth.
#
# A record, by lower-case nick:
#   {"nick": "Rod", "id": "<8 hex, made at registration>",
#    "salt": hex, "hash": hex, "pv": <password version>, "by": <Stump>,
#    "c": {<Stump>: [earned, spent]},   credits, one counter per Stump
#    "del": <0, or the password version it was deleted at>}
# Merging two copies is conflict-free: the higher password version wins
# (then the Stump id, for a tie); each Stump's credit counters only grow,
# so each pair is the larger of the two; a balance is every Stump's
# earned minus every Stump's spent. Two people who register the same nick
# on two Stumps before they've synced: the smaller id keeps the nick, the
# other is renamed "nick-xxxx" (its password and credits kept).
#
# Passwords: salted, sha256 repeated ROUNDS times, never stored as typed.
# Wrong-password lockouts are guard.py's (by network address).

import os
import time

try:
    import ujson as json
except ImportError:
    import json
try:
    import uhashlib as hashlib
except ImportError:
    import hashlib
try:
    import ubinascii as binascii
except ImportError:
    import binascii

ROUNDS = 2000
PW_MIN, PW_MAX = 6, 64
NICK_MAX = 16       # rrc.MAX_NICK_LEN
MAX_PROFILES = 2000
MAX_SESSIONS = 400
FILE_NAME = "profiles.json"

_db = None          # {"me": <this Stump's id>, "p": {key: record}, "s": {token: key}, "seq": n}
_path = None


def _rand_hex(n):
    try:
        return binascii.hexlify(os.urandom(n)).decode()
    except Exception:
        import random
        return "".join("%02x" % random.getrandbits(8) for _ in range(n))


def _file():
    global _path
    if _path:
        return _path
    try:
        import fserv
        if fserv.sd_ok:
            return fserv.SD_MOUNT + "/" + FILE_NAME
    except Exception:
        pass
    return "/" + FILE_NAME


def _load():
    global _db
    if _db is not None:
        return _db
    db = None
    try:
        with open(_file()) as f:
            db = json.load(f)
    except (OSError, ValueError):
        db = None
    if not isinstance(db, dict) or "p" not in db:
        db = {"me": _rand_hex(4), "p": {}, "s": {}, "seq": 0}
    db.setdefault("s", {})
    db.setdefault("seq", 0)
    db.setdefault("me", _rand_hex(4))
    _db = db
    return db


def _save():
    db = _load()
    path = _file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(db, f)
        os.rename(tmp, path)
        return True
    except OSError as e:
        print("[profiles] couldn't save:", e)
        return False


def reset(path=None):
    """Tests: start over (optionally with another file)."""
    global _db, _path
    _db, _path = None, path


def me():
    return _load()["me"]


def _touch(rec):
    db = _load()
    db["seq"] += 1
    rec["_s"] = db["seq"]


# -- passwords ----------------------------------------------------------------

def _digest(salt, password):
    pw = password.encode() if isinstance(password, str) else password
    h = binascii.unhexlify(salt) + pw
    for _ in range(ROUNDS):
        h = hashlib.sha256(h + pw).digest()
    return binascii.hexlify(h).decode()


def _same(a, b):
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a, b):
        r |= ord(x) ^ ord(y)
    return r == 0


def _set_password(rec, password, digested=None):
    if digested:                     # (salt, hash) worked out by prepare(), not blocking
        rec["salt"], rec["hash"] = digested
    else:
        rec["salt"] = _rand_hex(8)
        rec["hash"] = _digest(rec["salt"], password)


# The same hashing, a turn given to everyone else every YIELD_EVERY rounds:
# ROUNDS of sha256 is a long while on the CAM, and the Stump served nobody
# meanwhile. Same result, same strength.
YIELD_EVERY = 250


async def _digest_async(salt, password):
    try:
        import uasyncio as asyncio
    except ImportError:
        import asyncio
    pw = password.encode() if isinstance(password, str) else password
    h = binascii.unhexlify(salt) + pw
    for i in range(ROUNDS):
        h = hashlib.sha256(h + pw).digest()
        if i % YIELD_EVERY == YIELD_EVERY - 1:
            await asyncio.sleep(0)
    return binascii.hexlify(h).decode()


async def check_async(nick, password):
    """check(), without holding up the Stump while it hashes."""
    rec = get(nick)
    if rec is None or not isinstance(password, str):
        await _digest_async("00", password or "")      # the same time either way
        return False
    return _same(await _digest_async(rec["salt"], password), rec["hash"])


async def prepare(password):
    """(salt, hash) for a new password, without holding up the Stump: give
    it to register() or set_password() as digested=."""
    salt = _rand_hex(8)
    return salt, await _digest_async(salt, password if isinstance(password, str) else "")


def valid_password(password):
    return isinstance(password, str) and PW_MIN <= len(password) <= PW_MAX


# -- the records ----------------------------------------------------------------

def key(nick):
    return (nick or "").strip().lower()


def get(nick):
    rec = _load()["p"].get(key(nick))
    return rec if rec and not deleted(rec) else None


def deleted(rec):
    return rec.get("del", 0) > 0 and rec.get("del", 0) >= rec.get("pv", 0)


def is_registered(nick):
    return get(nick) is not None


def all_profiles():
    return sorted((r for r in _load()["p"].values() if not deleted(r)), key=lambda r: r["nick"].lower())


def balance(nick):
    rec = get(nick)
    if rec is None:
        return 0
    return sum(e for e, s in rec["c"].values()) - sum(s for e, s in rec["c"].values())


def add_credits(nick, amount):
    """Credits earned (amount > 0) or spent (< 0) here. Returns the
    balance."""
    rec = get(nick)
    if rec is None or amount == 0:
        return balance(nick)
    c = rec["c"].setdefault(me(), [0, 0])
    if amount > 0:
        c[0] += amount
    else:
        c[1] += -amount
    _touch(rec)
    _save()
    return balance(nick)


def register(nick, password, credits=0, digested=None):
    """A new profile. Returns (record, None) or (None, reason):
    "taken", "nick", "password", "full"."""
    k = key(nick)
    try:
        import rrc
        ok = rrc.clean_nick(nick.strip()) == nick.strip()
    except ImportError:
        ok = True
    if not k or len(nick.strip()) > NICK_MAX or not ok or k.startswith("guest-"):
        return None, "nick"
    if not valid_password(password):
        return None, "password"
    db = _load()
    old = db["p"].get(k)
    if old is not None and not deleted(old):
        return None, "taken"
    if len([r for r in db["p"].values() if not deleted(r)]) >= MAX_PROFILES:
        return None, "full"
    rec = {"nick": nick.strip(), "id": _rand_hex(4), "pv": 1, "by": me(), "c": {}, "del": 0}
    _set_password(rec, password, digested)
    if credits:
        rec["c"][me()] = [credits, 0] if credits > 0 else [0, -credits]
    db["p"][k] = rec
    _touch(rec)
    _save()
    return rec, None


def check(nick, password):
    """The right password for this nick? (The lockout is the caller's.)"""
    rec = get(nick)
    if rec is None or not isinstance(password, str):
        _digest("00", password or "")       # the same time either way
        return False
    return _same(_digest(rec["salt"], password), rec["hash"])


def set_password(nick, password, digested=None):
    """A new password (the person, or the admin's reset). True if done."""
    rec = get(nick)
    if rec is None or not valid_password(password):
        return False
    _set_password(rec, password, digested)
    rec["pv"] = rec.get("pv", 1) + 1
    rec["by"] = me()
    _touch(rec)
    end_sessions(nick)
    _save()
    return True


def delete(nick):
    """The admin removes a profile (on every Stump of the chain)."""
    rec = get(nick)
    if rec is None:
        return False
    rec["del"] = rec.get("pv", 1)
    rec["by"] = me()
    _touch(rec)
    end_sessions(nick)
    _save()
    return True


# -- sessions (this Stump only) -----------------------------------------------

def start_session(nick):
    db = _load()
    tok = "s" + _rand_hex(12)
    db["s"][tok] = [key(nick), int(time.time())]
    if len(db["s"]) > MAX_SESSIONS:
        oldest = sorted(db["s"].items(), key=lambda kv: kv[1][1])[: len(db["s"]) - MAX_SESSIONS]
        for t, _ in oldest:
            del db["s"][t]
    _save()
    return tok


def session_nick(tok):
    """The profile a session cookie is logged into, or None."""
    if not tok:
        return None
    s = _load()["s"].get(tok)
    if not s:
        return None
    rec = get(s[0])
    return rec["nick"] if rec else None


def end_session(tok):
    db = _load()
    if db["s"].pop(tok, None) is not None:
        _save()


def end_sessions(nick):
    db = _load()
    k = key(nick)
    for t in [t for t, s in db["s"].items() if s[0] == k]:
        del db["s"][t]


# -- the chain: what changed, and merging what another Stump sent -------------

def changed_since(seq, limit=10):
    """Records changed here after `seq` (oldest first), and the seq to
    ask from next time."""
    recs = sorted((r for r in _load()["p"].values() if r.get("_s", 0) > seq), key=lambda r: r["_s"])[:limit]
    out = [{k: v for k, v in r.items() if k != "_s"} for r in recs]
    return out, (recs[-1]["_s"] if recs else seq)


def seq():
    return _load()["seq"]


def _wins(a, b):
    """Does a's password (and deletion) win over b's?"""
    return (a.get("pv", 0), a.get("del", 0), a.get("by", "")) > (b.get("pv", 0), b.get("del", 0), b.get("by", ""))


_NICK_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_[]{}\\^`|"


def _clean(r):
    """A record from another Stump, checked field by field (it's shown in
    pages and the chat): None if anything's off."""
    try:
        nick = str(r["nick"])
        if not nick or len(nick) > NICK_MAX + 5 or any(ch not in _NICK_CHARS for ch in nick):
            return None
        c = {str(k): [int(v[0]), int(v[1])] for k, v in (r.get("c") or {}).items()}
        return {"nick": nick, "id": str(r["id"])[:16], "salt": str(r["salt"]),
                "hash": str(r["hash"]), "pv": int(r.get("pv", 1)), "by": str(r.get("by", "")),
                "c": c, "del": int(r.get("del", 0))}
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _sig(r):
    return (r["nick"], r["salt"], r["hash"], r.get("pv", 0), r.get("by", ""), r.get("del", 0),
            tuple(sorted((k, v[0], v[1]) for k, v in r["c"].items())))


def merge(records):
    """Takes in records from another Stump of the chain. Returns how many
    changed here (those go on to the other Stumps)."""
    db = _load()
    changed = 0
    for raw in records or ():
        r = _clean(raw if isinstance(raw, dict) else {})
        if r is None:
            continue
        k = key(r["nick"])
        mine = db["p"].get(k)
        if mine is None:
            db["p"][k] = r
            _touch(r)
            changed += 1
            continue
        if mine["id"] != r["id"]:
            # The same nick registered twice before the Stumps synced. A
            # deleted one gives way; else the smaller id keeps the nick.
            if deleted(mine) and not deleted(r):
                db["p"][k] = r
                _touch(r)
                changed += 1
                continue
            if deleted(r) or mine["id"] < r["id"]:
                loser = r
            else:
                loser, db["p"][k] = mine, r
                _touch(r)
                end_sessions(mine["nick"])
            loser["nick"] = loser["nick"] + "-" + loser["id"][:4]
            lk = key(loser["nick"])
            if lk not in db["p"]:
                db["p"][lk] = loser
                _touch(loser)
            changed += 1
            continue
        before = _sig(mine)
        if _wins(r, mine):
            for f in ("nick", "salt", "hash", "pv", "by", "del"):
                mine[f] = r[f]
            if deleted(mine):
                end_sessions(mine["nick"])
        for origin, (e, s) in r["c"].items():
            cur = mine["c"].get(origin, [0, 0])
            mine["c"][origin] = [max(cur[0], e), max(cur[1], s)]
        if _sig(mine) != before:
            _touch(mine)
            changed += 1
    if changed:
        _save()
    return changed
