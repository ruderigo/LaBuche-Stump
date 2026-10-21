# Project Stump -- RRC ↔ Mesh bridge
# Place at: firmware/rrc_mesh.py
#
# Connects the local RRC chat rooms to the Reticulum mesh so that a
# MeshCommander (or any LXMF-capable) peer can participate in the room
# as a named user alongside the local walk-up crowd.

import time
import rrc
import i18n
import plugins

# Operator-set greeting, sent once to each mesh peer on first contact.
try:
    from config import MESH_GREETING
except ImportError:
    MESH_GREETING = ""
try:
    from config import MESH_GREETING_MAX
except ImportError:
    MESH_GREETING_MAX = 200

MAX_MESH_PEERS = 20
# How long a mesh peer counts as present -- in the room, reachable by
# /msg, and still receiving room traffic over LoRa -- after their LAST
# inbound message. Operator-configurable because it's a genuine
# trade-off, not a fixed right answer: a LoRa user can't poll the way a
# browser does, so they only "check in" when they actually transmit,
# and a short window drops them (and their DM-ability) quickly; but
# every live peer also gets room traffic pushed to them over the radio
# every cycle, so a long window spends airtime on people who may have
# left. Default unchanged from before this was configurable.
try:
    from config import MESH_PEER_TIMEOUT as PEER_TIMEOUT
except ImportError:
    PEER_TIMEOUT = 300
# Announce-only peers: heard by their LXMF announce but never messaged
# LaBuche. Reachable by /msg and delivered DMs -- never room traffic,
# which stays opt-in by messaging. Refreshed by each new announce, so
# this should be comfortably longer than clients' announce interval.
try:
    from config import MESH_ANNOUNCE_TIMEOUT as ANNOUNCE_TIMEOUT
except ImportError:
    ANNOUNCE_TIMEOUT = 3600
MAX_REACHABLE  = 20
POLL_LIMIT     = 10    # max messages forwarded in one poll cycle per peer
ROOM_PREFIX    = "#"   # optional room prefix in message body

# dest_hash_hex (str) -> {nick, room, last_seen, last_sent_id}
_peers = {}

# dest_hash_hex -> {nick, last_seen, last_dm_id, hinted} -- announce-only
_reach = {}
_announce_installed = [False]

# [(router, dest_hash_bytes, text_str), ...]  -- drained by poll_loop
_send_queue = []

# [(dest_hash_hex, text)] from code with no router at hand (stumpid moving
# someone out of a room) -- turned into sends by poll_loop.
_notices = []


def notify(dest_hash_hex, text):
    """Sends `text` to a mesh user on the next cycle."""
    _notices.append((dest_hash_hex, text))
    del _notices[:-20]


# ── Stumps of the chain ───────────────────────────────────────────────
# Another Stump reached over the chain (chain.py: Wi-Fi) is a peer like a
# LoRa user, but lands in rrc.CHAIN_ROOM, not #lora-mesh, and is taken in as
# soon as its beacon is heard -- no one has to message it first. Between
# Stumps only chat lines travel, each Stump sending its own people's
# lines (never what it relays, never joins and parts), and each line
# shows as "<nick@thatstump>" on the other side.

def _via_chain(dest_hash_bytes):
    """True if the path to this destination comes in over a chain link."""
    try:
        from urns.transport import Transport
        from urns import const
        e = Transport.path_table.get(bytes(dest_hash_bytes))
        return e is not None and type(e[const.IDX_PT_RECV_IF]).__name__.startswith("Chain")
    except Exception:
        return False


def _on_chain(dest_hash_hex, message=None):
    """Someone reached over this Stump's chain (when it's in one): the
    path to them, or -- before any announce gave a path -- the link their
    message came in on."""
    if not rrc.room_exists(rrc.CHAIN_ROOM):
        return False
    iface = getattr(message, "receiving_interface", None) if message is not None else None
    if iface is not None:
        return type(iface).__name__.startswith("Chain")
    try:
        return _via_chain(bytes.fromhex(dest_hash_hex))
    except Exception:
        return False


def _chain_stump(dest_hash_hex):
    """A Stump (heard by its beacon) on this Stump's chain."""
    return rrc.is_stump(dest_hash_hex) and _on_chain(dest_hash_hex)


# People on the other Stumps of the chain, as DM contacts "rod@LaBuche":
# client id "@<stump hash>:<nick>" -> {"h", "nick", "seen", "last_dm"}.
# A DM to one goes to their Stump as "[DM>rod] <me> text"; their Stump
# hands it to rod as a DM from "me@thisstump".
_remote = {}
MAX_REMOTE = 40


def _remote_seen(h, nick, who, ident=None):
    """Someone on Stump h, as a DM contact "nick@who" -- unless they're
    here too (their identity: listed here already), and only once if two
    other Stumps both list them (the latest). Returns the contact's id,
    or None."""
    cid = "@" + h + ":" + nick.lower()
    if ident and rrc.local_identity(ident):
        if cid in _remote:
            _remote.pop(cid)
            rrc.drop_reachable(cid)
        return None
    if ident:
        for k in [k for k, r in _remote.items() if r.get("ident") == ident and k != cid]:
            _remote.pop(k)
            rrc.drop_reachable(k)
    r = _remote.get(cid)
    if r is None:
        if len(_remote) >= MAX_REMOTE:
            old = min(_remote, key=lambda k: _remote[k]["seen"])
            _remote.pop(old)
            rrc.drop_reachable(old)
        r = _remote[cid] = {"h": h, "nick": nick, "seen": 0, "last_dm": 0}
    r["seen"] = time.time()
    if ident:
        r["ident"] = ident
    rrc.set_reachable(cid, nick + "@" + who)
    return cid


def _prune_remote(now):
    for cid in [k for k, r in _remote.items()
                if r["h"] not in _peers or now - r["seen"] > ANNOUNCE_TIMEOUT]:
        _remote.pop(cid)
        rrc.drop_reachable(cid)


def _remote_dm_lines(h):
    """DMs from this Stump's people to people on Stump h, as lines."""
    lines = []
    for cid, r in _remote.items():
        if r["h"] != h:
            continue
        for m in rrc.dms_since(cid, r["last_dm"]):
            lines.append("[DM>%s] <%s> %s" % (r["nick"], m["nick"], m["body"]))
            r["last_dm"] = max(r["last_dm"], m["id"])
    return lines


def _has_path(h):
    try:
        from urns.transport import Transport
        return Transport.has_path(bytes.fromhex(h))
    except Exception:
        return False


def _radio_reachable(h):
    """Another Stump this one can still reach over its own radio: the radio
    online and the other's keys known. No path needed -- a radio send goes
    out on the radio whatever the path table says (it loses every path
    through the radio when the radio blinks offline, and the other Stump
    then dropped out until it spoke first)."""
    if _radio() is None:
        return False
    try:
        from urns.identity import Identity
        return Identity.recall(bytes.fromhex(h)) is not None
    except Exception:
        return False


# How far #lora-mesh has gone to each other Stump, kept while it's away
# (a peer can drop and come back): lines written meanwhile still go, if
# recent enough (LORA_BACKLOG_S).
_lora_marks = {}
LORA_BACKLOG_S = 300


def _lora_start(h):
    """Where #lora-mesh starts for Stump h: where it left off, else the
    last LORA_BACKLOG_S seconds."""
    msgs = rrc.since(rrc.MESH_ROOM, 0)
    if not msgs:
        return 0
    cutoff = time.time() - LORA_BACKLOG_S
    floor = 0
    for m in msgs:
        if m.get("ts", 0) >= cutoff:
            break
        floor = m["id"]
    return max(_lora_marks.get(h, floor), floor)


def _take_in_chain_stumps(router):
    """Each cycle: Stumps on the chain become peers in #wifi-mesh, and stay
    while their path is over the chain; other Stumps heard (over LoRa)
    become peers in #lora-mesh. Either way #lora-mesh is shared with them
    (_flush_to_peer)."""
    now = time.time()
    for h in list(rrc._stumps):
        if not _chain_stump(h):
            if _has_path(h) or _radio_reachable(h):
                p = _peers.get(h)
                if p is None:
                    _get_peer(h, _known_name(router, h))
                else:
                    p["last_seen"] = now
                    p["stump"] = True
                    _fix_nick(router, h, p)
            continue
        p = _peers.get(h)
        if p is not None:
            p["last_seen"] = now
            _fix_nick(router, h, p)
            if not p.get("stump") or p["room"] != rrc.CHAIN_ROOM:
                # Talked to us before it was known as a chain Stump (or first
                # heard over the radio, before its chain link): to #wifi-mesh.
                rrc.system(p["room"], rrc.ev_leave("~" + p["nick"]))
                p["stump"], p["room"], p["home_room"] = True, rrc.CHAIN_ROOM, rrc.CHAIN_ROOM
                p["last_sent_id"] = rrc.since(rrc.CHAIN_ROOM, 0)[-1]["id"] if rrc.since(rrc.CHAIN_ROOM, 0) else 0
                p["lora_sent_id"] = _lora_start(h)
                rrc.system(rrc.CHAIN_ROOM, rrc.ev_join("~" + p["nick"]))
                rrc.touch_user(h, nick=p["nick"], room=rrc.CHAIN_ROOM)
            continue
        name = None
        try:
            entry = router.peers.get(bytes.fromhex(h))
            name = entry.get("name") if entry else None
        except Exception:
            pass
        _get_peer(h, name)


def _known_name(router, h):
    try:
        entry = router.peers.get(bytes.fromhex(h))
        return entry.get("name") if entry else None
    except Exception:
        return None


def _fix_nick(router, h, p):
    """A Stump met before its name was known goes by "mesh-xxxx": once its
    announce gives the name, it goes by that (its lines then show as
    nick@ThatStump, not nick@mesh-xxxx)."""
    if not p["nick"].startswith("mesh-"):
        return
    name = _known_name(router, h)
    if not name:
        return
    nick = _make_nick(name, h)
    if nick and nick != p["nick"]:
        old = p["nick"]
        p["nick"] = nick
        rrc.system(p["room"], rrc.ev_rename("~" + old, "~" + nick))
        rrc.touch_user(h, nick=nick, room=p["room"])
        for cid, r in list(_remote.items()):
            if r["h"] == h:
                rrc.set_reachable(cid, r["nick"] + "@" + nick)


def _from_stump(dest_hash_hex, peer, text, router=None, title=""):
    """Lines from another Stump of the chain: "<nick> text" into #wifi-mesh
    as nick@thatstump, "* nick text" as an action, "[DM>rod] <nick> text"
    as a DM to rod here from nick@thatstump, "[DM] <nick>: text" (a DM to
    this Stump itself) into #wifi-mesh, addressed to it. Marked as relayed,
    so they go no further. A batch titled "#lora-mesh" lands in
    #lora-mesh: the radio's room, shared by the Stumps."""
    who = peer["nick"]
    me = (rrc.NODE or {}).get("name") or "?"
    room = rrc.MESH_ROOM if rrc.canon((title or "").lstrip("#")) == rrc.MESH_ROOM else (
        rrc.CHAIN_ROOM if peer.get("room") == rrc.CHAIN_ROOM else rrc.MESH_ROOM)
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[NAMES]"):
            # Who's in that room on that Stump (for /names here); each one
            # a DM contact as nick@thatstump.
            entries = []
            for n in line[7:].split():
                nick, _, ident = n.partition("~")
                if nick and nick != "-" and "@" not in nick:
                    entries.append((nick, ident[:8] if all(c in "0123456789abcdef" for c in ident) and ident else None))
            rrc.set_remote_names(dest_hash_hex, who, room, entries)
            listed = set()
            for nick, ident in entries[:20]:
                cid = _remote_seen(dest_hash_hex, nick, who, ident)
                if cid:
                    listed.add(cid)
            # Gone from that Stump's list: gone as a contact here too (not
            # an hour later).
            for cid in [k for k, r in _remote.items() if r["h"] == dest_hash_hex and k not in listed]:
                _remote.pop(cid)
                rrc.drop_reachable(cid)
            continue
        if line.startswith("[DM>") and "] <" in line and "> " in line:
            to, rest = line[4:].split("] <", 1)
            nick, body = rest.split("> ", 1)
            _remote_seen(dest_hash_hex, nick, who)
            ok, _ = rrc.send_dm(nick + "@" + who, to, body)
            if not ok and router is not None:
                # No one here by that name: say so, as a DM back.
                _send_queue.append((router, bytes.fromhex(dest_hash_hex),
                                    "[DM>%s] <%s> %s" % (nick, me, rrc.no_such(to))))
            continue
        if line.startswith("[DM] <") and ">: " in line:
            nick, body = line[6:].split(">: ", 1)
            _remote_seen(dest_hash_hex, nick, who)
            rrc.post(room, nick + "@" + who, "@" + me + " " + body, via=dest_hash_hex)
            continue
        if line.startswith("<") and "> " in line:
            nick, body = line[1:].split("> ", 1)
            if nick and " " not in nick:
                _remote_seen(dest_hash_hex, nick, who)
                rrc.post(room, nick + "@" + who, body, via=dest_hash_hex)
                continue
        if line.startswith("* ") and " " in line[2:]:
            nick, body = line[2:].split(" ", 1)
            rrc.post(room, nick + "@" + who, body, kind="action", via=dest_hash_hex)
            continue
        if line[:1] in ("✓", "✗", "✎", "→", "⊖", "⊘", "⧗"):
            continue           # an older Stump's room notices: not chat
        rrc.post(room, who, line, via=dest_hash_hex)


# ── Peer state ────────────────────────────────────────────────────────

def _prune_peers(now):
    stale = [h for h, p in _peers.items() if now - p["last_seen"] > PEER_TIMEOUT]
    for h in stale:
        p = _peers.pop(h)
        rrc.system(p["room"], rrc.ev_leave("~" + p["nick"]))
        rrc.drop_user(h)
        if h in _reach:
            # Still announcing: stays reachable by DM, and continues
            # from where room-peer delivery left off -- no re-sends.
            _reach[h]["last_dm_id"] = max(_reach[h]["last_dm_id"], p.get("last_dm_id", 0))
    if len(_peers) > MAX_MESH_PEERS:
        ordered = sorted(_peers.items(), key=lambda kv: kv[1]["last_seen"])
        for h, p in ordered[: len(_peers) - MAX_MESH_PEERS]:
            rrc.system(p["room"], rrc.ev_leave("~" + p["nick"]))
            del _peers[h]
            rrc.drop_user(h)


def _get_peer(dest_hash_hex, display_name, message=None):
    """Return peer state, creating it on first contact."""
    now = time.time()
    if dest_hash_hex not in _peers:
        # Someone already reachable by DM keeps the exact nick people have
        # been using for them, instead of re-deriving it.
        nick = _reach[dest_hash_hex]["nick"] if dest_hash_hex in _reach else _make_nick(display_name, dest_hash_hex)
        # Lands in rrc.MESH_ROOM ("#lora-mesh", once "#lxmf") unconditionally
        # (a Stump of this node's chain lands in rrc.CHAIN_ROOM instead) -- that room
        # is always present (seeded in rrc.py exactly like #main), so
        # this needs no configuration and no stumpid dependency to do
        # the basic triaging. Auth is secondary to this, not a
        # precondition for it: if stumpid IS installed and #lxmf has
        # been explicitly tiered, its gate still applies (below); if
        # stumpid isn't installed at all, or #lxmf is untiered (the
        # default), every mesh peer simply lands in #lxmf, full stop.
        #
        # The import is defensive, not required -- rrc_mesh has no hard
        # dependency on stumpid being installed at all, matching this
        # project's own plugin-optionality convention elsewhere (see
        # stumpid's own soft coupling to fservbot for the same reasoning).
        #
        # mesh_landing_room() runs the SAME can_join_room() gate an
        # explicit /join already goes through -- this is the one thing
        # that actually has to happen here: automatic first-contact
        # placement is a different code path from /join, and would
        # otherwise land an unqualified peer straight inside a tiered
        # room with no check at all, since they never typed a command
        # stumpid's dispatch layer could intercept.
        redirected = False
        redirect_reason = None
        ident = plugins.service_lookup("identity")
        stump = _chain_stump(dest_hash_hex)
        if not stump and rrc.is_stump(dest_hash_hex) and not _on_chain(dest_hash_hex, message):
            # Another Stump, over LoRa: shares #lora-mesh with this one.
            room, stump = rrc.MESH_ROOM, True
        elif stump or _on_chain(dest_hash_hex, message):
            # Over the chain: #wifi-mesh, not #lora-mesh (which is for LoRa).
            room = rrc.CHAIN_ROOM
        elif ident is not None:
            room, allowed, redirect_reason = ident.mesh_landing_room(dest_hash_hex)
            redirected = not allowed
        else:
            room = rrc.MESH_ROOM
        _peers[dest_hash_hex] = {
            "nick": nick,
            "room": room,
            # Recorded once, at first contact, distinct from "room"
            # (which changes as the peer /joins elsewhere). This is
            # where /part should return them -- whatever room they
            # actually, legitimately landed in, whether that's #lxmf
            # (the normal case) or #main (if a tiered #lxmf redirected
            # them here instead). Hardcoding either constant was wrong
            # in one of the two cases; this is correct in both, since
            # it's just "wherever mesh_landing_room() actually decided,"
            # not an assumption about what that should have been.
            "home_room": room,
            "stump": stump,
            "last_seen": now,
            "last_sent_id": 0,
            # A chain Stump's room is #wifi-mesh; #lora-mesh is shared
            # with it too, on its own marker (_flush_to_peer).
            "lora_sent_id": _lora_start(dest_hash_hex) if stump else 0,
            # DMs get their own delivery marker, separate from the room
            # one. They used to share last_sent_id, which /join resets
            # to 0 (to replay recent context in the new room) -- so
            # every room change re-delivered every DM still in this
            # peer's 72-hour inbox. Confirmed directly: one DM, one
            # /join, delivered twice over LoRa.
            "last_dm_id": _reach.get(dest_hash_hex, {}).get("last_dm_id", 0),
        }
        rrc.system(room, rrc.ev_join("~" + nick))
        if redirected:
            # Told directly, not left to wonder why they didn't land
            # where an operator may have said to expect. Reuses the
            # SAME translated messages /join's own rejection already
            # shows for these two reasons, keyed by the mesh peer's own
            # language preference exactly like a web visitor's -- their
            # dest_hash_hex IS their client_id everywhere else in this
            # project, so i18n.get_lang() already works for them with
            # no special-casing needed.
            import i18n
            lang = i18n.get_lang(dest_hash_hex)
            key = "room_needs_verified" if redirect_reason == "needs_verified" else "room_invite_only"
            rrc.system(room, "~" + nick + ": " + i18n.t(key, lang))
    else:
        _peers[dest_hash_hex]["last_seen"] = now
    _prune_peers(now)
    return _peers[dest_hash_hex]


def _make_nick(display_name, dest_hash_hex):
    """Produce a valid, unique-enough nick from an LXMF display name."""
    raw = (display_name or "").strip() or ("mesh-" + dest_hash_hex[:4])
    nick = rrc.clean_nick(raw)[:rrc.MAX_NICK_LEN] or ("mesh-" + dest_hash_hex[:4])
    base = nick
    suffix = 1
    while rrc.nick_taken(nick, dest_hash_hex):
        nick = (base[: rrc.MAX_NICK_LEN - 2] + str(suffix))
        suffix += 1
    return nick


# ── Announces: DM reachability only ───────────────────────────────────

def _is_person(router, dest, packet):
    """True only for an LXMF delivery destination (a person's inbox),
    excluding our own. The router is handed EVERY announce on the mesh
    -- nodes, propagation servers, other apps -- so this compares the
    announce's name hash (bytes after the 64-byte public key) with our
    own delivery destination's, which is lxmf.delivery by construction."""
    own = router.delivery_destination
    if own is None or dest == own.hash:
        return False
    try:
        from urns import const
        off = const.KEYSIZE // 8
        return bytes(packet.data[off:off + const.NAME_HASH_LENGTH // 8]) == bytes(own.name_hash)
    except Exception:
        return False


def _note_stump_beacon(router, app_data, packet):
    """A "stump.node" beacon from another node: remember its LXMF
    delivery hash (carried in the beacon's announce data) as a Stump,
    so chat lists can label it. Returns True if this was a beacon."""
    try:
        from urns import const
        from urns.identity import Identity
        off = const.KEYSIZE // 8
        n = const.NAME_HASH_LENGTH // 8
        if bytes(packet.data[off:off + n]) != Identity.full_hash(b"stump.node")[:n]:
            return False
        from urns import umsgpack
        fields = umsgpack.unpackb(app_data)
        lxmf_hash = bytes(fields[3])
        if len(fields) > 4:
            try:
                import discovery
                discovery.heard(lxmf_hash.hex(), str(fields[2]), fields[4])
            except Exception:
                pass
        own = router.delivery_destination
        if own is None or lxmf_hash != bytes(own.hash):
            h = lxmf_hash.hex()
            if not rrc.is_stump(h):
                # A Stump new to this one: answer with this one's beacon,
                # so it learns this Stump too (over the radio alone there's
                # no chain link to do it).
                try:
                    import chain
                    chain.stump_heard()
                except Exception:
                    pass
            rrc.mark_stump(h)
        return True
    except Exception:
        return False


def on_announce(router, dest, app_data, packet):
    if _note_stump_beacon(router, app_data, packet):
        return
    if not _is_person(router, dest, packet):
        return
    h = dest.hex()
    now = time.time()
    name = None
    try:
        name = router._parse_display_name(app_data) if app_data else None
    except Exception:
        pass
    r = _reach.get(h)
    if r is None:
        # Same nick a full peer would get, so someone who later messages
        # keeps the name people have been DMing.
        nick = _peers[h]["nick"] if h in _peers else _make_nick(name, h)
        r = _reach[h] = {"nick": nick, "last_seen": now, "last_dm_id": 0, "hinted": False, "name": name}
        rrc.set_reachable(h, nick)
    elif name and name != r.get("name"):
        # Their app announces a new name (FireFly: a new nickname): the
        # Stump follows -- unless they're here on Wi-Fi too, whose nick wins.
        had = r.get("name")
        r["name"] = name
        web = rrc.same_web(h)
        if had is not None:
            new = _make_nick(name, h)
            if web is None:
                rename(h, new)
            elif rrc._users[web]["nick"] in (r["nick"],) or rrc.is_default_nick(rrc._users[web]["nick"]):
                # Here on Wi-Fi too, under the name the app just changed:
                # both follow it.
                old = rrc._users[web]["nick"]
                rename(h, new)
                rrc.touch_user(web, nick=new)
                rrc.note_rename(old, new)
    r["last_seen"] = now
    if len(_reach) > MAX_REACHABLE:
        idle = sorted((k for k in _reach if k not in _peers), key=lambda k: _reach[k]["last_seen"])
        for k in idle[:len(_reach) - MAX_REACHABLE]:
            _reach.pop(k)
            rrc.drop_reachable(k)


def rename(h, new):
    """A LoRa contact (announced, or a room peer) known by a new nick:
    everywhere it's listed, the rooms told, and the web chat's threads
    folded into the new name (rrc.note_rename)."""
    old = None
    p = _peers.get(h)
    if p is not None and p["nick"] != new:
        old = p["nick"]
        p["nick"] = new
        rrc.touch_user(h, nick=new, room=p["room"])
        rrc.system(p["room"], rrc.ev_rename("~" + old, "~" + new))
    r = _reach.get(h)
    if r is not None and r["nick"] != new:
        old = old or r["nick"]
        r["nick"] = new
        rrc.set_reachable(h, new)
    if old:
        rrc.note_rename(old, new)
    return old


def lxmf_address(identity_hexhash):
    """The LXMF address (hex) of an identity, from its hash: what a FireFly
    proving that identity over Wi-Fi (/auth) is known by on LoRa."""
    from urns.destination import Destination
    import ubinascii
    raw = ubinascii.unhexlify(identity_hexhash)
    return ubinascii.hexlify(Destination.hash(raw, "lxmf", "delivery")).decode()


def same_person(web_cid, identity_hexhash):
    """A Wi-Fi visitor just proved this identity (/auth). If the Stump
    knows it on LoRa too, they're one person from now on (rrc.link_same):
    the LoRa entry takes the Wi-Fi nick, and the DMs still waiting to go
    out over LoRa go to the Wi-Fi inbox instead. Returns the LXMF address."""
    try:
        h = lxmf_address(identity_hexhash)
    except Exception:
        return None
    rrc.link_same(web_cid, h)
    known = h in _reach or h in _peers
    if not known:
        return h
    sent = max(_reach.get(h, {}).get("last_dm_id", 0), _peers.get(h, {}).get("last_dm_id", 0))
    waiting = [m for m in rrc._dms.get(h, []) if m["id"] > sent]
    if waiting:
        box = rrc._dms.setdefault(web_cid, [])
        box.extend(waiting)
        box.sort(key=lambda m: m["id"])
        rrc._dms[h] = [m for m in rrc._dms.get(h, []) if m["id"] <= sent]
        for t in (_reach.get(h), _peers.get(h)):
            if t is not None:
                t["last_dm_id"] = max(t.get("last_dm_id", 0), waiting[-1]["id"])
    web = rrc._users.get(web_cid)
    if web is not None:
        _one_name(h, web_cid)
    return h


def _lora_nick(h):
    p = _peers.get(h) or _reach.get(h)
    return p["nick"] if p else None


def _one_name(h, web_cid):
    """The same person on LoRa (h) and Wi-Fi (web_cid): one name, the one
    they chose -- a "guest-xxxx" (or "mesh-xxxx") gives way to it, in
    whichever direction. Both chosen: the Wi-Fi one (the newest /nick)."""
    web = rrc._users.get(web_cid)
    lora = _lora_nick(h)
    if web is None or lora is None or web["nick"] == lora:
        return
    if rrc.is_default_nick(web["nick"]) and rrc.is_default_nick(lora):
        return            # nobody chose yet: neither name spreads
    if rrc.is_default_nick(web["nick"]) and not rrc.is_default_nick(lora):
        old = web["nick"]
        rrc.touch_user(web_cid, nick=lora)
        rrc.note_rename(old, lora)
    else:
        rename(h, web["nick"])


def install_announce_handler(router):
    """Registered once, from poll_loop, directly with Transport so the
    raw packet (and its aspect name hash) is available -- the LXMF
    router's own callback only passes a hash and display name."""
    if _announce_installed[0]:
        return
    from urns.transport import Transport
    def _handler(dest, app_data, packet):
        try:
            on_announce(router, dest, app_data, packet)
        except Exception as e:
            print("[rrc_mesh] announce error:", e)
    Transport.register_announce_handler(_handler)
    _announce_installed[0] = True


def _prune_reach(now):
    for h in [k for k, r in _reach.items() if now - r["last_seen"] > ANNOUNCE_TIMEOUT]:
        _reach.pop(h)
        rrc.drop_reachable(h)


def _deliver_reach_dms(router, h, r):
    """DMs only, to a peer that has announced but never messaged us.
    Room traffic is never sent here -- that stays opt-in."""
    dms = rrc.dms_since(h, r["last_dm_id"])
    if not dms:
        return
    lines = []
    if not r["hinted"]:
        # They've never talked to this node: say what this is and how
        # to answer, once.
        lines.append(i18n.t("dm_hint", i18n.get_lang(h)))
        r["hinted"] = True
    for m in dms:
        if "audio" not in m:
            lines.append(rrc.dm_line(m["nick"], m["body"]))
        if m["id"] > r["last_dm_id"]:
            r["last_dm_id"] = m["id"]
    if lines:
        _send_queue.append((router, bytes.fromhex(h), "\n".join(lines)))
    _send_queue.extend(_voice_items(router, bytes.fromhex(h), dms))


# ── Inbound: LXMF → RRC ───────────────────────────────────────────────

STALE_VIA_PROPAGATION = 1800   # seconds


def _stale_age(message):
    """Seconds since a message was sent, if it reached this node through
    its propagation node and is older than STALE_VIA_PROPAGATION; else
    None. Unknown (node clock unset, no timestamp) counts as not stale."""
    if not getattr(message, "via_propagation", False):
        return None
    try:
        import propagation
        now = propagation.unix_now()
        ts = getattr(message, "timestamp", None)
        if ts is None or now < 1735689600:
            return None
        age = int(now - ts)
        return age if age > STALE_VIA_PROPAGATION else None
    except Exception:
        return None


def _strict():
    """True when the node's access mode (stumpid) isn't 'open'."""
    try:
        ident = plugins.service_lookup("identity")
        return ident is not None and getattr(ident, "AUTH_MODE", "open") != "open"
    except Exception:
        return False


def on_message(router, message):
    """Call this from the LXMF delivery callback in example_node.py."""
    try:
        dest_hash_hex = message.source_hash.hex()
        # A sender this node can't verify (no announce heard from it yet,
        # so no key to check the signature with) could be anyone using
        # that address. An open node takes it, as LXMF clients do; a node
        # in hybrid or mandatory mode doesn't. (Forged signatures never
        # get here: urns/lxmf.py drops them.)
        if not getattr(message, "signature_validated", True) and _strict():
            print("[rrc_mesh] ignored a message from %s: its sender can't be verified yet "
                  "(no announce heard), and this node isn't open" % dest_hash_hex[:8])
            return
        # A /msg, /nick or voice note left at a propagation node can reach
        # the chat hours after it was sent; acting on it then would surprise
        # everyone. Tell the sender instead.
        try:
            title0 = message.title_as_string() or ""
        except Exception:
            title0 = ""
        if title0 == PROFILES_TITLE:
            _take_profiles(message)          # another Stump of the chain's profiles
            return
        age = _stale_age(message)
        if age is not None:
            _send_queue.append((router, message.source_hash,
                                i18n.t("msg_expired", i18n.get_lang(dest_hash_hex), minutes=age // 60)))
            return
        # Display names live on the ROUTER, learned from announces --
        # LXMessage carries no source_display_name, so the original
        # lookup raised AttributeError into a bare except and every
        # mesh peer silently ended up as "mesh-xxxx" forever. This is
        # the same lookup node_common.peer_name() already uses, and
        # it keys on the raw hash bytes, not the hex string.
        display_name = None
        try:
            entry = router.peers.get(message.source_hash)
            if entry:
                display_name = entry.get("name")
        except Exception:
            pass

        first_contact = dest_hash_hex not in _peers
        peer = _get_peer(dest_hash_hex, display_name, message)
        # For plugins (ctx.via_propagation): this line was collected from a
        # propagation node rather than heard live.
        peer["via_propagation"] = bool(getattr(message, "via_propagation", False))

        # Register with rrc's own user table on ANY inbound message.
        # /msg resolves nicks against rrc._users, not against _peers --
        # and before this, the only thing that ever put a mesh peer
        # into rrc._users was the fall-through at the bottom of
        # _handle(), reached only by a plain public message (or /msg,
        # /me, /topic). A peer whose traffic so far was /help, /rooms,
        # /names, /nick, /join or /part existed here but was invisible
        # to /msg: "no one here called X". Confirmed as the real,
        # reported cause of DMs to an LXMF client only working after
        # that client had posted publicly first.
        rrc.touch_user(dest_hash_hex, nick=peer["nick"], room=peer["room"])

        # Greet once, on first contact only. _get_peer() can't do this
        # itself -- it has no router to send with, and it is also called
        # from prune paths where sending would be wrong.
        text0 = (message.content_as_string() or "").strip()
        if not peer.get("stump") and _on_chain(dest_hash_hex, message):
            # Its beacon not heard yet (it comes every half hour): another
            # Stump shows by what it sends -- a room batch titled "#room",
            # or a DM line.
            try:
                title = message.title_as_string() or ""
            except Exception:
                title = ""
            if title.startswith("#") or text0.startswith("[DM"):
                rrc.mark_stump(dest_hash_hex)
                peer["stump"] = True
                if peer["room"] != rrc.CHAIN_ROOM:
                    rrc.system(peer["room"], rrc.ev_leave("~" + peer["nick"]))
                    peer["room"] = peer["home_room"] = rrc.CHAIN_ROOM
                    rrc.system(rrc.CHAIN_ROOM, rrc.ev_join("~" + peer["nick"]))
                rrc.touch_user(dest_hash_hex, nick=peer["nick"], room=peer["room"])
        if not peer.get("stump") and not rrc.is_stump(dest_hash_hex):
            # Over the radio too, another Stump shows by what it sends, before
            # its beacon is heard: a batch titled with a Stumps' room, or a
            # DM line. (Missed, its "<nick> text" lines showed as said by the
            # Stump itself.)
            try:
                t = message.title_as_string() or ""
            except Exception:
                t = ""
            if t in ("#" + rrc.MESH_ROOM, "#" + rrc.CHAIN_ROOM) or text0.startswith("[DM>"):
                rrc.mark_stump(dest_hash_hex)
                try:
                    import chain
                    chain.stump_heard()      # and introduce this one back
                except Exception:
                    pass
        if not peer.get("stump") and rrc.is_stump(dest_hash_hex):
            peer["stump"] = True
        if peer.get("stump"):
            if text0:
                try:
                    t = message.title_as_string() or ""
                except Exception:
                    t = ""
                _from_stump(dest_hash_hex, peer, text0, router, t)
            return
        if first_contact:
            # Landing -- first time, or again after going quiet past
            # PEER_TIMEOUT, or redirected by a tiered #lxmf -- puts this
            # user in a room the client didn't choose; say which.
            _send_queue.append((router, message.source_hash, rrc.moved(peer["room"])))
        if first_contact and MESH_GREETING:
            _send_queue.append((router, message.source_hash,
                                MESH_GREETING[:MESH_GREETING_MAX]))

        text = (message.content_as_string() or "").strip()

        audio = _audio_field(message)
        if audio is not None:
            # A voice note (LXMF FIELD_AUDIO). Relayed as a DM when sent as
            # "/msg <nick>" -- the RRC chat itself is text; a note on its
            # own has no recipient here.
            replies = _voice_from_mesh(dest_hash_hex, text, audio)
            if replies:
                _send_queue.append((router, message.source_hash, "\n".join(replies)))
            return

        if not text:
            return

        replies, new_room = _handle(dest_hash_hex, peer, text)

        if new_room:
            peer["room"] = new_room
            peer["last_sent_id"] = 0   # reset so they get recent room context

        # Re-sync after the command ran: _handle() intercepts /nick,
        # /join and /part itself and only updates the peer record here,
        # so rrc kept the OLD nick and room -- a /msg to the new nick
        # failed the same "no one here called X" way, and /names in the
        # new room didn't list them. One sync point after every
        # command covers all of them.
        rrc.touch_user(dest_hash_hex, nick=peer["nick"], room=peer["room"])

        if replies:
            _send_queue.append((router, message.source_hash, "\n".join(replies)))

    except Exception as e:
        print("[rrc_mesh] on_message error:", e)


FIELD_AUDIO = 7           # LXMF.FIELD_AUDIO
AUDIO_MAX_BYTES = 65536   # FireFly's cap on the field


def _audio_field(message):
    """[mode, bytes] from an LXMF message's audio field, or None. Malformed
    or oversized fields are ignored, as FireFly's spec asks."""
    try:
        f = (getattr(message, "fields", None) or {}).get(FIELD_AUDIO)
    except AttributeError:
        return None
    if not isinstance(f, (list, tuple)) or len(f) < 2:
        return None
    mode, data = f[0], f[1]
    if not isinstance(mode, int) or not isinstance(data, (bytes, bytearray)):
        return None
    if not data or len(data) > AUDIO_MAX_BYTES:
        return None
    return [mode, bytes(data)]


def _voice_from_mesh(dest_hash_hex, text, audio):
    lang = i18n.get_lang(dest_hash_hex)
    parts = text.split(None, 2)
    if len(parts) < 2 or parts[0].lower() not in ("/msg", "/m", "/w"):
        return [i18n.t("voice_needs_target", lang)]
    return rrc.send_voice(dest_hash_hex, parts[1], audio[0], audio[1])


def _voice_items(router, dest_hash_bytes, dms):
    """Each voice note to a mesh user goes as its own LXMF message: the
    audio in FIELD_AUDIO and "[DM] <sender>: ♪ 5.0 s" as its text, which
    is how FireFly shows a note with text under it."""
    return [(router, dest_hash_bytes, rrc.dm_line(m["nick"], m["body"]), "",
             {FIELD_AUDIO: [m["audio"][0], m["audio"][1]]}) for m in dms if "audio" in m]


def _tier_refusal(dest_hash_hex, room, lang):
    """None if this mesh user may enter `room`, else the refusal line
    ("⊘ #room minted — ..."). The web's /join goes through stumpid's own
    gate; this bridge answers /join and the "#room text" shortcut itself,
    so it has to apply the same can_join_room() -- before this, a mesh
    user could join, or post into, a minted or hybrid room unchecked.
    Without stumpid active there are no tiers. With it, an error in the
    check refuses rather than lets through."""
    if rrc.canon(room) == rrc.MESH_ROOM and rrc.LORA_LOCKED[0] and _on_chain(dest_hash_hex):
        # The radio's room stays off the Wi-Fi chain.
        return rrc.tok_refused(rrc.MESH_ROOM, "LoRa", i18n.t("lora_room_locked", lang))
    sid = plugins.service_lookup("identity")
    if sid is None:
        return None
    try:
        ok, reason = sid.can_join_room(dest_hash_hex, room)
        tier = sid.room_tier(room)
    except Exception as e:
        print("[rrc_mesh] tier check failed, refusing:", e)
        ok, reason, tier = False, "needs_verified", "?"
    if ok:
        return None
    key = "room_needs_verified" if reason == "needs_verified" else "room_invite_only"
    return rrc.tok_refused(room, tier, i18n.t(key, lang))


def _tier_suffix(room):
    """"  [minted]" / "  [hybrid]" after a gated room, exactly as the web
    /rooms shows it (stumpid annotates that one). This bridge answers
    /rooms itself, so without this a mesh user never saw a room's tier.
    Only while stumpid is actually active, same as on the web."""
    sid = plugins.service_lookup("identity")
    if sid is None:
        return ""
    try:
        t = sid.room_tier(room)
        return "" if t == "open" else "  [%s]" % t
    except Exception:
        return ""


def _handle(dest_hash_hex, peer, text):
    """Parse text and act on it. Returns (reply_lines, new_room_or_None)."""
    room = peer["room"]
    lang = i18n.get_lang(dest_hash_hex)
    if text.startswith(ROOM_PREFIX) and not text.startswith("/#"):
        parts = text.split(None, 1)
        candidate = parts[0][1:]   # strip leading #
        if len(parts) > 1 and rrc.room_exists(candidate):
            if candidate != room:
                refused = _tier_refusal(dest_hash_hex, candidate, lang)
                if refused:
                    return [refused], None
            room = candidate
            text  = parts[1]

    nick = peer["nick"]

    if text.lower().startswith("/join "):
        arg = text[6:].strip()
        target, err = rrc.create_room(arg)
        if err:
            return [err], None
        if target == room:
            return [rrc.tok_already(target, i18n.t("join_already", lang, room=target))], None
        refused = _tier_refusal(dest_hash_hex, target, lang)
        if refused:
            return [refused], None
        rrc.system(room, rrc.ev_leave("~" + nick))
        rrc.system(target, rrc.ev_join("~" + nick))
        return [rrc.moved(target)], target

    if text.lower().startswith("/nick "):
        new = rrc.clean_nick(text[6:].strip())
        if not new:
            return [i18n.t("nick_usage", lang)], None
        if new.lower() == nick.lower():
            return [i18n.t("nick_already", lang)], None
        if rrc.nick_taken(new, dest_hash_hex):
            return [i18n.t("nick_taken", lang, nick=new)], None
        old = nick
        peer["nick"] = new
        rrc.system(room, rrc.ev_rename("~" + old, "~" + new))
        return [], None

    if text.lower() == "/rooms":
        return [rrc.rooms_line(r, len(rrc.names_in_room(r)), rrc.topic(r, lang)) + _tier_suffix(r)
                for r in rrc.room_names()], None

    if text.lower() in ("/names", "/who"):
        names = rrc.names_in_room(room)
        return [rrc.names_line(room, names)], None

    if text.lower() == "/part":
        home = peer["home_room"]
        if room == home:
            return [rrc.tok_already(home, i18n.t("join_already", lang, room=home))], None
        rrc.system(room, rrc.ev_leave("~" + nick))
        rrc.system(home, rrc.ev_join("~" + nick))
        return [rrc.moved(home)], home

    if text.lower() in ("/help", "/?"):
        # The chat's own help (same wording as the web, in this user's
        # language, ending with the symbol key) plus the one mesh-only form.
        first = "#" + i18n.t("help_arg_room", lang) + " " + i18n.t("help_arg_text", lang)
        return ([first + " " * max(1, 18 - len(first)) + i18n.t("help_room_prefix", lang)]
                + rrc.help_lines(lang, mesh=True)), None

    rrc.touch_user(dest_hash_hex, nick=nick, room=room)
    replies, new_room = rrc.handle_input(dest_hash_hex, room, text)
    # "__CLEAR__" is a sentinel the WEB client interprets as "wipe your
    # local scrollback". It is meaningless to a mesh peer, and sending
    # it spends real LoRa airtime transmitting a magic word to someone
    # who will just see it as gibberish.
    replies = [r for r in replies if r != "__CLEAR__"]
    if not replies and text.lower().startswith("/clear"):
        replies = [i18n.t("clear_mesh", lang)]
    return replies, new_room


# ── Outbound: RRC → mesh ──────────────────────────────────────────────

# Registered profiles (profiles.py) shared by the Stumps of a chain: sent
# to a Stump whose path comes in on the chain link, never over the radio,
# and taken in only from a signed message that came in on a chain link
# (which needs the chain secret).
PROFILES_TITLE = "#profiles"
PROFILES_PER_MSG = 4
PROFILES_MSGS_PER_FLUSH = 3


def _flush_profiles(router, dest_hash_hex, peer):
    try:
        import profiles
        dest = bytes.fromhex(dest_hash_hex)
    except Exception:
        return
    if _chain_iface(dest) is None:
        return
    cur = peer.get("prof_seq", 0)
    if profiles.seq() <= cur:
        return
    try:
        import ujson as json
    except ImportError:
        import json
    for _ in range(PROFILES_MSGS_PER_FLUSH):
        recs, nxt = profiles.changed_since(cur, PROFILES_PER_MSG)
        if not recs:
            break
        # No "via": too big for one packet; the path to it is the chain's.
        _send_queue.append((router, dest, json.dumps(recs), PROFILES_TITLE))
        cur = nxt
    peer["prof_seq"] = cur


def _take_profiles(message):
    iface = getattr(message, "receiving_interface", None)
    if not getattr(message, "signature_validated", False) or not type(iface).__name__.startswith("Chain"):
        print("[rrc_mesh] profiles from %s ignored: not signed, or not over the chain link"
              % message.source_hash.hex()[:8])
        return
    try:
        import profiles
        try:
            import ujson as json
        except ImportError:
            import json
        n = profiles.merge(json.loads(message.content_as_string() or "[]"))
        if n:
            rrc.release_registered_nicks()
            print("[rrc_mesh] %d profile(s) from %s" % (n, message.source_hash.hex()[:8]))
    except Exception as e:
        print("[rrc_mesh] profiles from %s unreadable: %s" % (message.source_hash.hex()[:8], e))


def _flush_to_peer(router, dest_hash_hex, peer):
    """Forward any new RRC messages in this peer's room to them."""
    room    = peer["room"]
    last_id = peer["last_sent_id"]
    msgs    = rrc.since(room, last_id)
    # Checked independently of room traffic -- this used to return
    # early whenever the room was quiet, before the DM check below
    # ever ran, so a DM to a peer in a silent room waited until
    # someone happened to speak in that room.
    dms     = rrc.dms_since(dest_hash_hex, peer.get("last_dm_id", 0))

    remote = _remote_dm_lines(dest_hash_hex) if peer.get("stump") else []
    if peer.get("stump"):
        _flush_profiles(router, dest_hash_hex, peer)
        # #lora-mesh to another Stump always goes the radio-first way
        # (_flush_lora_to_stump), whatever room it sits in here; so its
        # room lines don't go a second time below.
        _flush_lora_to_stump(router, dest_hash_hex, peer)
        if room == rrc.MESH_ROOM:
            if msgs:
                peer["last_sent_id"] = msgs[-1]["id"]
            msgs = []
    if not msgs and not dms and not remote:
        return
    try:
        dest_hash_bytes = bytes.fromhex(dest_hash_hex)
    except Exception:
        return

    if len(msgs) > POLL_LIMIT:
        msgs = msgs[-POLL_LIMIT:]

    nick  = peer["nick"]
    stump = peer.get("stump")
    lines = []
    for m in msgs:
        # Advance the marker for EVERY message examined, including the
        # peer's own. Skipping the update on a self-message meant that
        # whenever the newest line in a room was the peer's own, the
        # marker stalled and the same messages were re-scanned on every
        # poll cycle, forever.
        peer["last_sent_id"] = m["id"]
        if m["nick"] == nick:
            continue
        if stump and (m["kind"] == "system" or m.get("via")):
            # To another Stump: its own people's lines only, no notices.
            continue
        if m["kind"] == "system":
            lines.append(m["body"])
        elif m["kind"] == "action":
            lines.append("* " + m["nick"] + " " + m["body"])
        else:
            lines.append("<" + m["nick"] + "> " + m["body"])

    # Private messages addressed to this peer ride the same flush. They
    # are pulled from the DM store rather than the room, so they reach
    # only the intended peer -- a mesh user can be /msg'd from the web
    # UI and vice versa, and nobody else on the mesh or in the room
    # sees it.
    for m in dms:
        if "audio" not in m:
            lines.append(rrc.dm_line(m["nick"], m["body"]))
        if m["id"] > peer.get("last_dm_id", 0):
            peer["last_dm_id"] = m["id"]
    _send_queue.extend(_voice_items(router, dest_hash_bytes, dms))
    lines.extend(remote)

    if lines:
        # Room lines in this batch belong to `room`; say so in the title.
        # A DM-only batch has no room (its lines name their author).
        text, title = "\n".join(lines), ("#" + room if msgs else "")
        chain_if = _chain_iface(dest_hash_bytes) if stump and room == rrc.CHAIN_ROOM else None
        if chain_if is not None and len((text + title).encode()) <= RADIO_CHUNK:
            # #wifi-mesh stays on Wi-Fi: a short batch to a Stump one hop
            # away would otherwise go out on every interface, the radio too.
            _send_queue.append((router, dest_hash_bytes, text, title, None, chain_if))
        else:
            _send_queue.append((router, dest_hash_bytes, text, title))


def _stump_lines(msgs, skip_nick=None):
    """A room's lines for another Stump: its own people's only (not what
    came from other Stumps, no notices)."""
    out = []
    for m in msgs:
        if m["nick"] == skip_nick or m["kind"] == "system" or m.get("via"):
            continue
        out.append(("* " + m["nick"] + " " if m["kind"] == "action" else "<" + m["nick"] + "> ") + m["body"])
    return out


# One radio packet holds about 295 bytes of LXMF content; #lora-mesh lines
# for another Stump go in pieces well under that (title included).
RADIO_CHUNK = 200


def _radio():
    """This Stump's own radio, if it's online (the Heltec on USB, Bluetooth
    or the CAM's bridge), else None."""
    try:
        import radio
        for i in radio.bridges():
            if getattr(i, "online", False):
                return i
    except Exception:
        pass
    return None


def _chain_iface(dest_hash_bytes):
    """The chain interface the path to this Stump comes in on, or None."""
    try:
        from urns.transport import Transport
        from urns import const
        e = Transport.path_table.get(bytes(dest_hash_bytes))
        i = e[const.IDX_PT_RECV_IF] if e is not None else None
        if i is not None and type(i).__name__.startswith("Chain"):
            return i
    except Exception:
        pass
    return None


def _radio_pieces(lines, limit=RADIO_CHUNK):
    """Lines grouped into texts of at most `limit` bytes; a longer line is
    cut, each piece keeping its "<nick> " so the other side files it."""
    out, cur = [], ""
    for line in lines:
        pieces = [line]
        if len(line.encode()) > limit:
            head = line[:line.index("> ") + 2] if line.startswith("<") and "> " in line else ""
            body, pieces = line[len(head):], []
            step = max(20, (limit - len(head.encode())) // 2)    # utf-8: up to 2+ bytes a char
            while body:
                pieces.append(head + body[:step])
                body = body[step:]
        for p in pieces:
            if cur and len((cur + "\n" + p).encode()) > limit:
                out.append(cur)
                cur = p
            else:
                cur = (cur + "\n" + p) if cur else p
    if cur:
        out.append(cur)
    return out


def _queue_lora(router, dest_hash_hex, lines):
    """#lora-mesh lines for another Stump: over this Stump's radio when it
    has one online -- even when Wi-Fi reaches that Stump too, #lora-mesh is
    the radio's room -- in single packets; without a radio, the usual way
    (the chain)."""
    dest = bytes.fromhex(dest_hash_hex)
    title = "#" + rrc.MESH_ROOM
    r = _radio()
    if r is None:
        _send_queue.append((router, dest, "\n".join(lines), title))
        return
    for text in _radio_pieces(lines):
        _send_queue.append((router, dest, text, title, None, r))


def _flush_lora_to_stump(router, dest_hash_hex, peer):
    """#lora-mesh, shared with a Stump whose own room here is #wifi-mesh
    (the chain): a batch titled "#lora-mesh" -- that Stump files it in
    its #lora-mesh. A short message to a Stump one hop away goes out on
    every interface, the Heltec included."""
    msgs = rrc.since(rrc.MESH_ROOM, peer.get("lora_sent_id", 0))
    if len(msgs) > POLL_LIMIT:
        msgs = msgs[-POLL_LIMIT:]
    lines = []
    if msgs:
        peer["lora_sent_id"] = _lora_marks[dest_hash_hex] = msgs[-1]["id"]
        lines = _stump_lines(msgs, peer["nick"])
    # Who's in #lora-mesh here, for that Stump's /names: when it changes,
    # and every NAMES_EVERY s (it forgets after rrc.REMOTE_NAMES_S).
    roster = " ".join(_lora_roster())
    now = time.time()
    if roster != peer.get("lora_roster") or now - peer.get("lora_roster_at", 0) > NAMES_EVERY:
        peer["lora_roster"], peer["lora_roster_at"] = roster, now
        lines.append("[NAMES] " + (roster or "-"))
    if lines:
        _queue_lora(router, dest_hash_hex, lines)


NAMES_EVERY = 600


def _lora_roster():
    """The people in #lora-mesh on THIS Stump (not the other Stumps, not
    people this one only knows from them)."""
    stumps = set(p["nick"] for p in _peers.values() if p.get("stump"))
    out = []
    for cid, u in list(rrc._users.items()):
        n = u["nick"]
        if (u["room"] != rrc.MESH_ROOM or n in stumps or "@" in n or cid in rrc._hidden
                or rrc.same_web(cid) is not None):
            continue
        ident = rrc.identity_of(cid)
        # "nick~3f2a91bc": who they are, so the other Stumps don't list
        # someone they have themselves (older Stumps read the nick alone).
        out.append(n + "~" + ident if ident else n)
    return sorted(set(out))[:60]


# ── Poll loop (scheduled as asyncio task) ─────────────────────────────

def _send_item(item):
    """Sends one queued message: (router, dest, text) or, for a pushed
    room batch, (router, dest, text, title) -- the title names the room
    the batch belongs to, so a client never has to guess (lines queued
    just before a /join could otherwise land under the new room)."""
    rtr, dest_hash_bytes, text = item[0], item[1], item[2]
    title = item[3] if len(item) > 3 else ""
    fields = item[4] if len(item) > 4 else None
    via = item[5] if len(item) > 5 else None
    # Each send signs and encrypts without yielding: reset the Heltec
    # link's quiet timer right before it, not once per cycle, so no run of
    # sends can add up to the ~7 s of silence that drops the link.
    # (A cheap socket write; the loop yields between sends.)
    try:
        from urns.interfaces.wifi_serial import prime_all_bridges
        prime_all_bridges()
    except Exception:
        pass
    try:
        if via is not None:
            rtr.send_message(dest_hash_bytes, text, title=title, via=via)
        elif fields:
            rtr.send_message(dest_hash_bytes, text, title=title, fields=fields)
        elif title:
            rtr.send_message(dest_hash_bytes, text, title=title)
        else:
            rtr.send_message(dest_hash_bytes, text)
    except Exception as e:
        print("[rrc_mesh] send error:", e)


async def poll_loop(router):
    """Drain _send_queue and push pending room messages to subscribed peers."""
    import uasyncio as asyncio
    import gc

    try:
        install_announce_handler(router)
    except Exception as e:
        print("[rrc_mesh] announce handler not installed:", e)

    while True:
        await asyncio.sleep(5)
        try:
            # Primed ONCE per cycle, before whatever sends this cycle is
            # about to make -- not once per individual send, which would
            # just be extra synchronous socket work for no benefit. The
            # point is resetting the bridge's inactivity clock right
            # before the burst of blocking router.send_message() calls
            # below begins, since each one freezes the whole event loop
            # (confirmed: zero yield points anywhere in that call chain)
            # for however long the signing takes. See the identical
            # reasoning in example_node.py's reannounce_loop, which this
            # mirrors for the same underlying cause.
            try:
                from urns.interfaces.wifi_serial import prime_all_bridges
                prime_all_bridges()
            except Exception:
                pass  # never let a diagnostic aid block real forwarding

            while _notices:
                h, text = _notices.pop(0)
                _send_queue.append((router, bytes.fromhex(h), text))
            while _send_queue:
                _send_item(_send_queue.pop(0))
                await asyncio.sleep(0)

            # Presence is decided HERE, on one clock, for both tables.
            # Before this, the two disagreed: _prune_peers() only ever
            # ran when some OTHER mesh message happened to arrive (so a
            # peer could keep receiving room traffic over LoRa
            # indefinitely), while rrc dropped the same peer from
            # rrc._users -- and so from /msg -- after USER_TIMEOUT of
            # silence regardless, because a LoRa client never polls to
            # refresh itself the way a browser does. Now: a peer is
            # present for PEER_TIMEOUT after their last transmission,
            # and exactly as reachable by /msg as they are live on the
            # mesh, no more and no less.
            try:
                _take_in_chain_stumps(router)
            except Exception as e:
                print("[rrc_mesh] chain peers:", e)
            now = time.time()
            _prune_peers(now)
            _prune_reach(now)
            _prune_remote(now)
            # The same person on Wi-Fi and LoRa (rrc.link_same): the LoRa
            # entry follows the Wi-Fi nick (a /nick there included).
            for h in list(rrc._same):
                web = rrc.same_web(h)
                if web is not None and (h in _peers or h in _reach):
                    _one_name(h, web)
            for dest_hash_hex, peer in list(_peers.items()):
                rrc.touch_user(dest_hash_hex, nick=peer["nick"], room=peer["room"])
                r = _reach.get(dest_hash_hex)
                if r is not None and r["nick"] != peer["nick"]:
                    # A /nick as a room peer carries over to reachability.
                    r["nick"] = peer["nick"]
                    rrc.set_reachable(dest_hash_hex, peer["nick"])

            for h, r in list(_reach.items()):
                if h not in _peers:
                    _deliver_reach_dms(router, h, r)
                    # Sent now, same as the room-peer loop below -- left
                    # queued, it waited a whole extra 5-second cycle.
                    while _send_queue:
                        _send_item(_send_queue.pop(0))
                        await asyncio.sleep(0)

            for dest_hash_hex, peer in list(_peers.items()):
                _flush_to_peer(router, dest_hash_hex, peer)
                while _send_queue:
                    _send_item(_send_queue.pop(0))
                    await asyncio.sleep(0)

            gc.collect()

        except Exception as e:
            print("[rrc_mesh] poll_loop error:", e)
