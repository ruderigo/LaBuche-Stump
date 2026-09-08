# Project Stump -- stumpid (identity verification plugin)
# Place at: firmware/stumpid/core.py
#
# Answers one question rrc.py deliberately doesn't: is the client behind
# this connection provably the same client as last time?
#
# rrc.py's client_id is the peer IP (or, for mesh peers via rrc_mesh, the
# LXMF destination hash). Both are honest about what they are: identifiers
# of a CONNECTION, not of a PERSON. An IP is reassigned by DHCP and shared
# across reconnects; nothing stops two different people from presenting
# the same one to two different rooms. That's fine for ordinary chat, and
# rrc.py says so plainly -- "fine for per-device chat state, not a
# security boundary."
#
# stumpid adds a second, optional layer on top: a real Ed25519 identity
# (RNS's Identity class, the same one this node already uses for its own
# LXMF address) that a client can PROVE it holds, via a signed nonce. That
# proof is what makes an identity portable across reconnects, across
# transports (WiFi today, LoRa via rrc_mesh), and eventually across
# Stumps -- the identity_hash survives a changed IP or a new LoRa session
# in a way client_id never could.

import time
import rrc
import ujson as json
import os

from urns.identity import Identity

try:
    from config import AUTH_MODE
except ImportError:
    AUTH_MODE = "open"
try:
    from config import AUTH_ADMIN_PASSWORD
except ImportError:
    AUTH_ADMIN_PASSWORD = ""
# Deliberately NOT "from config import FSERVBOT_OP_PASSWORD" here. That
# would bind a second, independent snapshot of the same config key
# under stumpid's own name -- the wrong layer to share at, and it
# bypasses fservbot.core.check_op_password entirely, which is the one
# place that comparison is supposed to happen (see check_admin_password
# below). Confirmed the divergence risk is real: a test that changed
# fservbot's password at the fservbot.core level left stumpid's copy
# silently stale.

STORAGE_FILE = "/sd/stumpid.json"

# A challenge is only good for this long and only good once. Both limits
# matter: an unbounded window lets a captured nonce sit around waiting to
# be signed later; letting a nonce be reused lets a captured (nonce,
# signature) pair be replayed against a DIFFERENT later request.
CHALLENGE_TTL = 60
MAX_PENDING_CHALLENGES = 40
MAX_KNOWN_IDENTITIES = 500

# client_id -> {"hexhash", "nonce", "issued"}
_pending = {}

# identity_hexhash -> {"nick", "verified_since", "credits", "last_seen",
#                       "pubkey_hex"}
# Flat and hash-keyed on purpose -- Firefly's README calls out exactly
# this shape for a reason: "send this table" is the whole job of a
# future Stump-to-Stump sync, versus redesigning it later. Nothing here
# is built for that sync, but nothing here would need to change either.
_identities = {}
_loaded = False


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def _sd_available():
    try:
        os.stat("/sd")
        return True
    except OSError:
        return False


from urns.identity import Identity as _Identity   # for exact size constants only

HASH_HEXLEN = _Identity.TRUNCATED_HASHLENGTH // 8 * 2      # 32
PUBKEY_HEXLEN = _Identity.KEYSIZE // 8 * 2                  # 128


def _load():
    """Reads the saved identity table. Every entry is shape-checked
    individually, the same discipline fservbot's own load() uses --
    a file that PARSES as JSON but isn't the expected shape (a string
    where a record should be, a hash-shaped key with a non-dict value,
    wrong-typed fields) must degrade one entry, never crash the whole
    table into something later code assumes is safe to index into.

    Confirmed this matters: the earlier version merged the raw parsed
    top level straight into _identities with only a dict-vs-not check,
    so a file like {"identities": "nope"} silently created an entry
    whose 'record' was a bare string -- harmless only by accident,
    because "identities" happens not to look like a real hash. A
    genuinely hash-shaped key pointing at a non-dict value would have
    crashed the first thing that tried entry["nick"] = ... on it.
    """
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not _sd_available():
        return
    try:
        with open(STORAGE_FILE) as f:
            data = json.load(f)
    except OSError:
        return   # nothing saved yet
    except Exception as e:
        print("[stumpid] storage unreadable, starting empty:", e)
        return

    if not isinstance(data, dict):
        return

    for h, rec in data.items():
        if len(_identities) >= MAX_KNOWN_IDENTITIES:
            break
        if not isinstance(h, str) or len(h) != HASH_HEXLEN:
            continue
        if not isinstance(rec, dict):
            continue
        nick = rec.get("nick")
        vs = rec.get("verified_since")
        ls = rec.get("last_seen")
        cr = rec.get("credits")
        pk = rec.get("pubkey_hex")
        if nick is not None and not isinstance(nick, str):
            continue
        if not isinstance(vs, (int, float)):
            vs = time.time()
        if not isinstance(ls, (int, float)):
            ls = vs
        if not isinstance(cr, int):
            cr = 0
        if not isinstance(pk, str) or len(pk) != PUBKEY_HEXLEN:
            continue
        _identities[h] = {"nick": nick, "verified_since": vs,
                           "last_seen": ls, "credits": cr, "pubkey_hex": pk}


def _save():
    if not _sd_available():
        return
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump(_identities, f)
    except Exception as e:
        print("[stumpid] could not save identity table:", e)


# ---------------------------------------------------------------------
# Challenge / response
# ---------------------------------------------------------------------

def _prune_pending(now):
    stale = [c for c, p in _pending.items() if now - p["issued"] > CHALLENGE_TTL]
    for c in stale:
        del _pending[c]
    if len(_pending) > MAX_PENDING_CHALLENGES:
        ordered = sorted(_pending.items(), key=lambda kv: kv[1]["issued"])
        for c, _ in ordered[: len(_pending) - MAX_PENDING_CHALLENGES]:
            del _pending[c]


def start_challenge(client_id):
    """Issues a nonce for client_id to sign. Takes no claim of identity
    up front -- see module docstring: asking the client to assert a hash
    that the server can derive from the public key itself is the same
    computation twice, confirmed directly (Identity(pubkey).hexhash ==
    the hash that same key would ever produce, by definition). One field
    removed, one failure mode removed, nothing lost: the hash the client
    ends up verified as is authoritative because the SERVER computed it
    from a key that just proved possession of the matching private half,
    not because the client was trusted to state it correctly.
    """
    _load()
    now = time.time()
    _prune_pending(now)
    nonce = os.urandom(16)
    _pending[client_id] = {"nonce": nonce, "issued": now}
    return nonce.hex()


def complete_challenge(client_id, pubkey_hex, signature_hex):
    """Verifies signature over the pending nonce, using the public key
    supplied HERE (not claimed earlier -- there is no earlier claim).

    Returns (ok, hexhash_or_error). One-shot: the pending entry is
    consumed here regardless of outcome, so neither a wrong guess nor a
    correct signature can be replayed against the same nonce twice.

    THE SIGNED MESSAGE IS THE ASCII BYTES OF THE NONCE'S HEX STRING (the
    literal text after AUTH-CHALLENGE), not the decoded nonce bytes.
    Verified directly that these are NOT interchangeable -- a signature
    over one fails validate() against the other. Client implementers:
    sign nonce_hex.encode(), not bytes.fromhex(nonce_hex).
    """
    pending = _pending.pop(client_id, None)
    if pending is None:
        return False, "no challenge pending -- start with /auth"

    if time.time() - pending["issued"] > CHALLENGE_TTL:
        return False, "challenge expired -- start again with /auth"

    try:
        pubkey = bytes.fromhex(pubkey_hex)
        sig = bytes.fromhex(signature_hex)
    except Exception:
        return False, "bad public key or signature encoding"

    check = Identity(create_keys=False)
    try:
        check.load_public_key(pubkey)
    except Exception as e:
        return False, "bad public key: %s" % e

    message = pending["nonce"].hex().encode()
    if not check.validate(sig, message):
        return False, "signature does not match"

    hexhash = check.hexhash
    _load()
    entry = _identities.get(hexhash)
    now = time.time()
    if entry is None:
        if len(_identities) >= MAX_KNOWN_IDENTITIES:
            # Evict the identity least recently seen -- an identity
            # nobody has used in a long time is the right one to forget
            # first, the same policy rrc.py already uses for its own
            # bounded tables.
            oldest = min(_identities.items(), key=lambda kv: kv[1].get("last_seen", 0))
            del _identities[oldest[0]]
        entry = {
            "nick": None, "verified_since": now, "credits": 0,
            "last_seen": now, "pubkey_hex": pubkey_hex,
        }
        _identities[hexhash] = entry
    else:
        entry["last_seen"] = now
    _save()
    return True, hexhash


def identity_info(hexhash):
    _load()
    return _identities.get(hexhash)


def set_nick_for(hexhash, nick):
    """Updates the saved nick for a verified identity -- but only writes
    to SD when it actually changed. Without that check, syncing the
    nick after every message (not just after /nick) would mean an SD
    write per chat line from every verified user, which is real card
    wear and latency for zero benefit once the nick has already
    settled."""
    _load()
    entry = _identities.get(hexhash)
    if entry and entry.get("nick") != nick:
        entry["nick"] = nick
        _save()


# ---------------------------------------------------------------------
# Per-connection verification state
# ---------------------------------------------------------------------

# client_id -> hexhash, for connections that have completed a challenge
# THIS SESSION. Deliberately not persisted and not the same thing as
# _identities: a verified hexhash is durable, but which client_id it's
# currently attached to is not -- client_id is still just an IP or an
# LXMF hash, and reconnecting means re-proving, by design. Persisting a
# client_id-to-identity binding would silently trust whoever holds that
# IP next, which defeats the point of verification.
_verified_this_session = {}


def mark_verified(client_id, hexhash):
    _verified_this_session[client_id] = hexhash


def verified_identity_for(client_id):
    return _verified_this_session.get(client_id)


def is_verified(client_id):
    return client_id in _verified_this_session


# ---------------------------------------------------------------------
# Mode gating
# ---------------------------------------------------------------------

# Commands that never require verification, even under "mandatory" --
# otherwise a client could never reach /auth in the first place.
_ALWAYS_ALLOWED = ("auth", "whoami", "help")

# Slash commands treated as WRITES under "hybrid". Reading room history,
# listing rooms, checking who's present are not gated -- hybrid's whole
# point (per the spec) is "reads open, writes require a verified ID".
_WRITE_COMMANDS = ("join", "part", "msg", "me", "topic")


def requires_verification(client_id, text):
    """True if this input should be blocked pending verification, under
    the node's current AUTH_MODE.

    Plain messages (no leading '/') are always writes -- posting to a
    room is the paradigm case hybrid mode exists to gate.
    """
    if AUTH_MODE == "open":
        return False
    if is_verified(client_id):
        return False

    stripped = text.strip()
    if not stripped.startswith("/"):
        is_write = True
    else:
        rest = stripped[1:].split(" ", 1)
        cmd = rest[0].lower()
        arg = rest[1].strip() if len(rest) > 1 else ""
        if cmd in _ALWAYS_ALLOWED:
            return False
        # Bare "/topic" READS the current topic; "/topic <text>" SETS
        # it. Same command name, opposite classification -- gating the
        # bare form under hybrid would block a read hybrid's own
        # definition says stays open, so this needs the argument, not
        # just the verb.
        if cmd == "topic" and not arg:
            is_write = (AUTH_MODE == "mandatory")
        else:
            is_write = (AUTH_MODE == "mandatory") or (cmd in _WRITE_COMMANDS)

    return is_write


# ---------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------

def check_admin_password(pw):
    """True if pw unlocks /admin on this node.

    Tries stumpid's own AUTH_ADMIN_PASSWORD first, fail-closed exactly
    like fservbot's own check (a blank supplied password can never
    match a blank configured one). If stumpid has none configured,
    falls back to fservbot's operator password -- read LIVE via
    fservbot.core.check_op_password rather than a second independent
    config import, so this can never silently diverge from what
    fservbot itself considers valid, and so there remains exactly one
    place this exact comparison happens.

    stumpid does not REQUIRE fservbot to be installed: the two plugins
    are independently toggleable, and a hard dependency between them
    would mean uninstalling one silently breaks the other's admin path.
    If fservbot isn't present, the import fails, caught, and admin
    commands are refused outright -- same as fservbot does when its own
    password is unset.
    """
    if AUTH_ADMIN_PASSWORD:
        return bool(pw) and pw == AUTH_ADMIN_PASSWORD
    try:
        import fservbot.core as fservbot_core
    except ImportError:
        return False
    return fservbot_core.check_op_password(pw)


def admin_available():
    """Whether ANY admin path exists on this node -- for the startup
    hint only, never for the actual access decision (see
    check_admin_password, and the denial message below, for why the
    two paths are deliberately not distinguishable to whoever typed the
    command)."""
    if AUTH_ADMIN_PASSWORD:
        return True
    try:
        import fservbot.core as fservbot_core
    except ImportError:
        return False
    return bool(fservbot_core.FSERVBOT_OP_PASSWORD)


def set_mode(new_mode):
    global AUTH_MODE
    if new_mode not in ("open", "mandatory", "hybrid"):
        return False
    AUTH_MODE = new_mode
    return True


# A live, operator-settable landing room for mesh peers -- unset (None)
# by default, meaning today's exact behaviour: a mesh peer's first
# message lands them in rrc.DEFAULT_ROOM ("main"), same as any web
# visitor. Runtime-only, like a live AUTH_MODE change: it does not
# rewrite config.py, so it reverts on next boot. That's deliberate,
# matching set_mode()'s own reasoning -- this is an operational choice
# an operator might want to try, rename, or turn back off while the
# node is already running, not something that should need a reflash to
# change or to undo.
MESH_DEFAULT_ROOM = None


def set_mesh_room(name):
    """Sets or clears the mesh-peer landing room. name="" or name=None
    clears it back to unset (today's behaviour). Returns the room's
    clean slug on success, or None if the name was empty/invalid --
    same clean-then-validate shape rrc.create_room() already uses, so
    this can't produce a room name rrc.py itself would reject."""
    global MESH_DEFAULT_ROOM
    if not name:
        MESH_DEFAULT_ROOM = None
        return None
    slug = rrc.clean_room(name)
    if not slug:
        return None
    MESH_DEFAULT_ROOM = slug
    return slug


def mesh_landing_room(client_id):
    """The room a mesh peer should land in on first contact.

    Runs the SAME can_join_room() gate an explicit /join already goes
    through -- confirmed this has to happen here, not skipped, because
    automatic first-contact placement is a completely different code
    path from /join and would otherwise bypass a tiered mesh room's own
    protection entirely: a peer who never typed /join never hits
    stumpid's dispatch layer at all, so nothing was checking them
    without this. Falls back to rrc.DEFAULT_ROOM (always open, by
    definition) for anyone who doesn't qualify -- they can /auth and
    /join manually afterward if the room allows it.

    Returns (room, allowed, reason_code). reason_code is None when
    allowed, else the same "needs_verified"/"invite_only" marker
    can_join_room() itself returns -- passed through rather than
    collapsed into one assumed reason, so the caller can show the
    actually-correct explanation instead of guessing which of the two
    gates fired.
    """
    if MESH_DEFAULT_ROOM is None:
        return rrc.DEFAULT_ROOM, True, None
    ok, reason_code = can_join_room(client_id, MESH_DEFAULT_ROOM)
    if ok:
        return MESH_DEFAULT_ROOM, True, None
    return rrc.DEFAULT_ROOM, False, reason_code


# ---------------------------------------------------------------------
# Per-room access tiers
# ---------------------------------------------------------------------
#
# rrc.py's own room model stays untouched -- no tier concept added
# there, no membership list added there. Rooms are, by design elsewhere
# in this project, memory-only and gone on reboot (rrc.reset() wipes
# everything back to just #main); a persisted policy for a room that
# itself won't exist after a reboot would be pointless, so this state
# is memory-only too, for the identical reason rrc.py's own room dict
# is. That keeps the two concerns cleanly separated: rrc.py still knows
# nothing about identity, and this module still knows nothing about
# message history -- it only decides "is this client_id allowed to be
# in this room," the same shape as requires_verification() above,
# scoped to a room instead of the whole node.
#
# room name (rrc.clean_room()'s slug) -> {"tier": "minted"|"hybrid",
#                                          "invited": set(client_id)}
# Absence means "open" -- the common case (an ordinary room) costs
# nothing, no entry needed.
_room_policy = {}

VALID_TIERS = ("open", "minted", "hybrid")


def room_tier(room):
    """'open' unless a stricter tier was explicitly set."""
    pol = _room_policy.get(room)
    return pol["tier"] if pol else "open"


def set_room_tier(room, tier):
    """Sets or clears a room's access tier.

    tier == 'open' REMOVES any existing entry rather than storing an
    explicit 'open' record -- keeps the dict holding only rooms that
    actually differ from the zero-cost default, and makes 'reset a room
    back to normal' and 'this room was never restricted' indistinguishable
    from the outside, which is the correct behaviour either way.
    """
    if tier not in VALID_TIERS:
        return False
    if tier == "open":
        _room_policy.pop(room, None)
    else:
        pol = _room_policy.setdefault(room, {"tier": tier, "invited": set()})
        pol["tier"] = tier
    return True


def invite_to_room(room, target_client_id):
    """Adds target_client_id to a hybrid room's invite list.

    Invites are tracked by client_id, not by a verified hash -- on
    purpose. The whole point of this feature is inviting someone who
    HASN'T minted an identity; they have no hash to key an invite by.
    This means an invite is honestly only as durable as client_id
    itself already is everywhere else in this project (an IP, which can
    change between sessions) -- not a new weakness, the same one
    documented for client_id generally, just visible here too.

    Only meaningful for hybrid rooms: inviting into an open room is a
    no-op (anyone can already join) and inviting into a minted-only
    room is a no-op (invites don't apply there -- only a verified
    identity ever qualifies). Returns (ok, reason_if_not).
    """
    pol = _room_policy.get(room)
    if pol is None or pol["tier"] != "hybrid":
        # A plain internal marker, NOT user-facing text: this function
        # only has target_client_id in scope, but the reply goes to
        # whoever typed /invite (a different person, with their own
        # language preference) -- translating here would use the WRONG
        # client's language. install.py's _cmd_invite, which has the
        # actual inviter's client_id, does the translation instead.
        return False, "not_invite_gated"
    pol["invited"].add(target_client_id)
    return True, None


def can_join_room(client_id, room):
    """The actual gate for entering a room. Returns (ok, reason_code).

    reason_code is a plain internal marker ("needs_verified" /
    "invite_only"), not user-facing text -- install.py's _dispatch,
    which has this same client_id in scope at the call site, translates
    it into that client's own language. Keeping core.py free of i18n
    entirely means a function like this can never accidentally
    translate for the wrong person, the way an earlier draft of
    invite_to_room almost did (it only has the INVITED person's
    client_id in scope, not the inviter who actually reads the reply --
    translating there would have shown the message in the wrong
    person's language).

    Runs unconditionally, regardless of the node's global AUTH_MODE. A
    room's own tier is always an ADDITIONAL restriction layered on top
    of whatever the global mode already allows, never a relaxation of
    it -- under global 'open', where nothing else is gated, a
    minted-only or hybrid room still enforces its own rule; under
    'mandatory', where everything is already gated at the node level,
    this still runs but is never the deciding factor, since the
    broader gate already blocked an unverified client before this
    point is ever reached.
    """
    tier = room_tier(room)
    if tier == "open":
        return True, None

    my_hash = verified_identity_for(client_id)
    if my_hash is not None:
        # A verified identity satisfies BOTH restricted tiers. Minted
        # people never need an invite into a hybrid room -- invites
        # exist specifically for people who haven't minted one.
        return True, None

    if tier == "minted":
        return False, "needs_verified"

    # tier == "hybrid"
    pol = _room_policy.get(room)
    if pol and client_id in pol.get("invited", ()):
        return True, None
    return False, "invite_only"


def reset():
    """Clears all state. For tests -- every other stateful module in
    this project has the equivalent (rrc.reset(), fservbot.core.reset())."""
    global _loaded
    _pending.clear()
    _identities.clear()
    _verified_this_session.clear()
    _room_policy.clear()
    _loaded = False


def revoke(hexhash):
    _load()
    if hexhash in _identities:
        del _identities[hexhash]
        _save()
        stale = [c for c, h in _verified_this_session.items() if h == hexhash]
        for c in stale:
            del _verified_this_session[c]
        return True
    return False
