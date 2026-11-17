# Project Stump -- RRC ↔ Mesh bridge
# Place at: firmware/rrc_mesh.py
#
# Connects the local RRC chat rooms to the Reticulum mesh so that a
# MeshCommander (or any LXMF-capable) peer can participate in the room
# as a named user alongside the local walk-up crowd.

try:
    from keepalive import doing as _doing
except ImportError:
    def _doing(label):
        pass
from urns.mem import collect as _gc_collect
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
    """A Stump (heard by its beacon) on this Stump's chain: reached over
    the chain, and not one whose beacon says it's in no chain or another
    (a LoRa Stump reached through this chain's Stump with a radio: its
    path and its messages come over the chain link too; 1.0.0-beta.68)."""
    return rrc.is_stump(dest_hash_hex) and _on_chain(dest_hash_hex) and not _other_chain(dest_hash_hex)


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


# A Stump is alive while the time in its announces keeps rising: each one
# it sends carries the moment it was made (the path table's "emitted").
# A copy passed on later (a path request answered from another Stump's
# memory) carries the old moment, so it doesn't count (1.0.0-beta.60). Its
# clock may be off (no internet): only whether it rises matters, seen
# here: hash -> [emitted, when it last rose here].
STUMP_QUIET_S = 1800          # not risen for this long: gone (announces come every 2 minutes)
RETIRE_QUIET_S = 600          # ... and with another identity live under its name, at its address
_risen = {}


def _quiet_for(h, now=None):
    """Seconds since Stump h's announces last brought a newer time, or
    None when there's no route to it."""
    now = now or time.time()
    try:
        from urns.transport import Transport
        from urns import const
        e = Transport.path_table.get(bytes.fromhex(h))
    except Exception:
        return 0
    if e is None:
        return None
    em = e[const.IDX_PT_EMITTED]
    r = _risen.get(h)
    if r is None or em > r[0]:
        _risen[h] = [em, now]
        if len(_risen) > 128:
            _risen.pop(min(_risen, key=lambda k: _risen[k][1]))
        return 0
    return now - r[1]


def _alive(h, now=None):
    q = _quiet_for(h, now)
    return q is None or q < _setting("STUMP_QUIET_S", STUMP_QUIET_S)


def _tidy_replaced(now=None):
    """A Stump set up again: a new identity, the same name, at the same
    address on the home network as one already known whose announces have
    stopped -- that one is the old identity: retired, its name free for the
    new one (1.0.0-beta.60: it held it, and the new one showed as
    "LaBuche1" until every Stump restarted)."""
    try:
        import discovery
    except Exception:
        return
    now = now or time.time()
    by = {}
    for h, v in list(discovery._known.items()):
        n = (v.get("name") or "").lower()
        if n:
            by.setdefault(n, []).append(h)
    for n, hs in by.items():
        if len(hs) < 2:
            continue
        lim = _setting("STUMP_RETIRE_QUIET_S", RETIRE_QUIET_S)
        qs = {h: _quiet_for(h, now) for h in hs}
        live = [h for h in hs if qs[h] is not None and qs[h] < lim]
        for old in hs:
            # Gone: its announces stopped for lim, or no route to it and not
            # heard at all for lim. (One just heard, its route not there
            # yet, isn't: that's how a new one starts.)
            heard_ago = now - discovery._known[old].get("at", now)
            if old in live or not ((qs[old] is not None and qs[old] >= lim) or (qs[old] is None and heard_ago >= lim)):
                continue
            for new in live:
                if discovery.same_place(discovery._known[old].get("http"), discovery._known[new].get("http")):
                    discovery._known.pop(old, None)
                    _retire(old, new)
                    break


def _retire(old, new=None):
    """A Stump's old identity, gone (discovery: the same Stump set up again
    under a new one): out of the rooms, the contacts, the names lists and
    the route table, so its name is free and nothing more is sent to it."""
    p = _peers.pop(old, None)
    if p is not None:
        rrc.system(p["room"], rrc.ev_leave("~" + p["nick"]))
    rrc.drop_user(old)
    if old in _reach:
        _reach.pop(old)
    rrc.drop_reachable(old)
    rrc._remote_names.pop(old, None)
    _lora_marks.pop(old, None)
    rrc._stumps.discard(old)
    try:
        from urns.transport import Transport
        Transport.path_table.pop(bytes.fromhex(old), None)
    except Exception:
        pass
    print("[rrc_mesh] %s: set up again (%s -> %s); the old one retired"
          % (p["nick"] if p else "a Stump", old[:8], (new or "?")[:8]))


def _take_in_chain_stumps(router):
    """Each cycle: Stumps on the chain become peers in #wifi-mesh, and stay
    while their path is over the chain; other Stumps heard (over LoRa)
    become peers in #lora-mesh. Either way #lora-mesh is shared with them
    (_flush_to_peer)."""
    now = time.time()
    _tidy_replaced(now)
    for h in list(rrc._stumps):
        if not _alive(h, now):
            # No announce has confirmed its route for STUMP_QUIET_S: gone (a
            # board set up again under a new identity, one switched off).
            # Not kept alive by its route any more: it leaves after
            # PEER_TIMEOUT, its name free (1.0.0-beta.60: a route lasts a
            # day, and kept a dead identity a peer -- and its name -- as long).
            if h in _reach:
                _reach.pop(h)
                rrc.drop_reachable(h)
            continue
        if not _chain_stump(h):
            p = _peers.get(h)
            if p is not None and p["room"] == rrc.CHAIN_ROOM and _other_chain(h):
                # Placed in #wifi-mesh before its beacon said it's not of this
                # chain (its first message came through this chain's Stump
                # with a radio): to #lora-mesh (1.0.0-beta.68).
                rrc.system(rrc.CHAIN_ROOM, rrc.ev_leave("~" + p["nick"]))
                p["room"] = p["home_room"] = rrc.MESH_ROOM
                p["last_sent_id"] = rrc.since(rrc.MESH_ROOM, 0)[-1]["id"] if rrc.since(rrc.MESH_ROOM, 0) else 0
                rrc.system(rrc.MESH_ROOM, rrc.ev_join("~" + p["nick"]))
                rrc.touch_user(h, nick=p["nick"], room=rrc.MESH_ROOM)
                rrc._remote_names.pop(h, None)     # its lists come again, for #lora-mesh
                _names_soon(h)
            if _has_path(h) or _radio_reachable(h):
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
        if entry and entry.get("name"):
            return entry.get("name")
    except Exception:
        pass
    # Its LXMF announce (router.peers) hasn't arrived yet -- it's a
    # separate, crypto-heavy broadcast on its own ~2-minute cycle. The
    # Stump's beacon carries its name too and comes first, within seconds
    # of a restart (beta.43's early re-introduction), so resolve the name
    # from there rather than sitting on "mesh-xxxx" until the announce.
    try:
        import discovery
        e = discovery._known.get(h)
        if e and e.get("name"):
            return e["name"]
    except Exception:
        pass
    return None


def _fix_nick(router, h, p):
    """A Stump goes by its name: met before its name was known ("mesh-xxxx"),
    once its announce gives it; renamed (1.0.0-beta.60), its new one; and
    given a tag because its name was taken ("LaBuche-k7q2"), the plain
    name once it's free again (the identity holding it gone). Its lines then show
    as nick@ThatStump."""
    name = _known_name(router, h)
    if not name:
        return
    want = rrc.clean_nick(name.strip())[:rrc.MAX_NICK_LEN]
    if not want or p["nick"] == want:
        return
    # Its name with a tag (or a number, from before): the plain name only once it's free.
    numbered = not p["nick"].startswith("mesh-") and _from_name(p["nick"], want)
    nick = _make_nick(name, h)
    if numbered and nick != want:
        return                        # its name still taken: keeps its tag
    if nick and nick != p["nick"]:
        old = p["nick"]
        p["nick"] = nick
        rrc.system(p["room"], rrc.ev_rename("~" + old, "~" + nick))
        rrc.touch_user(h, nick=nick, room=p["room"])
        for cid, r in list(_remote.items()):
            if r["h"] == h:
                rrc.set_reachable(cid, r["nick"] + "@" + nick)
        # Its names lists (/names) too: they came in under the old name.
        e = rrc._remote_names.get(h)
        if e is not None:
            e["who"] = nick
        if h in _reach:
            _reach[h]["nick"] = nick
            rrc.set_reachable(h, nick)


def _from_name(nick, want):
    """Is this nick made from that name (itself, with a tag, or with a
    number as before 1.0.0-beta.60)?"""
    base = want[:rrc.MAX_NICK_LEN - 2]
    return rrc.is_tagged_from(nick, want) or (nick.startswith(base) and nick[len(base):].isdigit())


def _is_itself(nick, who):
    """Is this "<nick>" the Stump known here as `who` answering itself? Its
    nick, or its name as typed, which a Stump puts in its own answers: `who`
    is made from it (cleaned, cut to MAX_NICK_LEN, maybe tagged), so
    "LaBuche-Stump-eK0ms" is "LaBuche-Stump-eK" (1.0.0-beta.64). Only a
    name that isn't a nick itself (too long, or with a space...) is matched
    that way: a person's nick is already clean, so it can't pass for it."""
    if nick == who:
        return True
    c = rrc.clean_nick(nick)
    return bool(c) and c != nick and (c == who or _from_name(who, c))


def _from_stump(dest_hash_hex, peer, text, router=None, title=""):
    """Lines from another Stump of the chain: "<nick> text" into #wifi-mesh
    as nick@thatstump, "* nick text" as an action, "[DM>rod] <nick> text"
    as a DM to rod here from nick@thatstump, "[DM] <nick>: text" (a DM to
    this Stump itself) answered privately by BarKeep. Marked as relayed,
    so they go no further. A batch titled "#lora-mesh" lands in
    #lora-mesh: the radio's room, shared by the Stumps."""
    who = peer["nick"]
    me = (rrc.NODE or {}).get("name") or "?"
    t = rrc.canon((title or "").lstrip("#"))
    if t in (rrc.MESH_ROOM, rrc.CHAIN_ROOM):
        room = t
    elif t and rrc.clean_room(t):
        # Another room with a scope (1.0.0-beta.62): only if this Stump
        # shares it with that one; else its lines aren't for here.
        import rooms
        peer["rs"] = True
        room = t if rooms.shares(t, dest_hash_hex, _room_chain(dest_hash_hex, peer)) else None
    else:
        room = rrc.CHAIN_ROOM if peer.get("room") == rrc.CHAIN_ROOM else rrc.MESH_ROOM
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[ROOM"):
            _room_ctl(dest_hash_hex, peer, line)
            continue
        if room is None and not line.startswith("[DM"):
            continue
        if line.startswith("[NAMES"):
            # Who's in that room on that Stump (for /names here); each one
            # a DM contact as nick@thatstump. A long list comes in parts,
            # "[NAMES 1/3] ..." (1.0.0-beta.70): shown as they come, and
            # whoever isn't listed dropped only once the last part is in.
            # Anything else "[NAMES..." is never shown as a line.
            end = line.find("]")
            tag = line[1:end] if end > 0 else ""
            part = total = 1
            if tag != "NAMES":
                try:
                    part, total = [int(x) for x in tag[6:].split("/")]
                except Exception:
                    continue
                if not tag.startswith("NAMES ") or not 1 <= part <= total <= 99:
                    continue
            entries = []
            for n in line[end + 1:].split():
                nick, _, ident = n.partition("~")
                if nick and nick != "-" and "@" not in nick:
                    entries.append((nick, ident[:8] if all(c in "0123456789abcdef" for c in ident) and ident else None))
            pk = dest_hash_hex + " " + room
            if part > 1:
                got = _names_parts_in.get(pk)
                if got is None or got[0] != part or got[1] != total:
                    continue           # a part missed: the next list (10 min) is whole
                entries = got[2] + entries
            if part < total:
                _names_parts_in[pk] = (part + 1, total, entries)
                if len(_names_parts_in) > 32:
                    _names_parts_in.pop(next(iter(_names_parts_in)))
            else:
                _names_parts_in.pop(pk, None)
            rrc.set_remote_names(dest_hash_hex, who, room, entries)
            listed = set()
            for nick, ident in entries[:20]:
                cid = _remote_seen(dest_hash_hex, nick, who, ident)
                if cid:
                    listed.add(cid)
                    _remote[cid].setdefault("rooms", set()).add(room)
            if part < total:
                continue
            # Gone from that Stump's list for this room: gone as a contact
            # here too (not an hour later) -- unless that Stump still lists
            # them in its other room (a Stump sends one list per room).
            for cid in [k for k, r in _remote.items() if r["h"] == dest_hash_hex and k not in listed]:
                rooms = _remote[cid].get("rooms")
                if rooms:
                    rooms.discard(room)
                    if rooms:
                        continue
                _remote.pop(cid)
                rrc.drop_reachable(cid)
            continue
        if line.startswith("[DM>") and "] <" in line and "> " in line:
            to, rest = line[4:].split("] <", 1)
            nick, body = rest.split("> ", 1)
            if _is_itself(nick, who):
                # That Stump itself answering (its BarKeep, its fserv bot, a
                # "no one by that name"): in the thread with the Stump, not
                # "who@who" -- also under its name as typed (kit finding 12).
                frm = who
            else:
                _remote_seen(dest_hash_hex, nick, who)
                frm = nick + "@" + who
            ok, _ = rrc.send_dm(frm, to, body)
            if not ok and router is not None:
                # No one here by that name: say so, as a DM back.
                _send_queue.append((router, bytes.fromhex(dest_hash_hex),
                                    "[DM>%s] <%s> %s" % (nick, me, rrc.no_such(to))))
            continue
        if line.startswith("[DM] <") and ">: " in line:
            # A DM to this Stump itself (its entry under "Stumps"): private,
            # never into a room (it was posted to #wifi-mesh for all to read
            # until 1.0.0-beta.47). BarKeep answers it, back into the
            # sender's DM thread with this Stump.
            nick, body = line[6:].split(">: ", 1)
            _remote_seen(dest_hash_hex, nick, who)
            reply = _dm_to_me(dest_hash_hex, nick, body)
            if reply and router is not None:
                _send_queue.append((router, bytes.fromhex(dest_hash_hex), "\n".join(
                    "[DM>%s] <%s> %s" % (nick, me, l) for l in reply.split("\n") if l.strip())))
            continue
        if line[:1] in ("<", "*") and _twice_possible(room) and _seen_line(dest_hash_hex + room, line):
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


_names_parts_in = {}       # "stump room" -> (next part, of, entries so far): a list coming in parts


# A DM to this Stump itself: BarKeep's answer to a trigger (!help, !list,
# ...), else, at most every DM_HELP_EVERY s per sender, how to reach the
# people here (name@thisstump).
DM_HELP_EVERY = 600
_dm_helped = {}


def _dm_to_me(dest_hash_hex, nick, body):
    bot = plugins.service_lookup("bot")
    if bot is not None:
        try:
            r = bot.answer(nick, body)
            if r:
                return r
        except Exception as e:
            print("[rrc_mesh] BarKeep couldn't answer a DM:", e)
    key = dest_hash_hex + " " + nick
    now = time.time()
    if now - _dm_helped.get(key, -DM_HELP_EVERY) < DM_HELP_EVERY:
        return None
    if len(_dm_helped) > 100:
        _dm_helped.clear()
    _dm_helped[key] = now
    me = (rrc.NODE or {}).get("name") or "?"
    lang = i18n.DEFAULT_LANG
    if bot is not None:
        return i18n.t("stump_dm_bot", lang, stump=me, bot=bot.name(), p=bot.prefix())
    return i18n.t("stump_dm_nobot", lang, stump=me)


# ── Peer state ────────────────────────────────────────────────────────

# A LoRa client stays in its room while it's still here, at no airtime of
# its own (1.0.0-beta.79, asked by the FireFly team: a quiet one was
# dropped after PEER_TIMEOUT unless it sent something every 4 minutes):
# - the proof its app sends back for each line this Stump pushes it (it
#   sends them anyway) counts: while the room talks, a client that's
#   listening stays, one that's gone stops proving and leaves as before;
# - so does an announce of its own (signed), for a room gone quiet;
# - one that went quiet anyway and comes back within BACK_S returns to the
#   room it was in, not to #lora-mesh.
BACK_S = 3600
_was = {}                 # dest hash hex -> (the room it was in, when it went quiet)


def _still_here(h):
    p = _peers.get(h)
    if p is not None and not p.get("stump"):
        p["last_seen"] = time.time()


def _watch_proof(h, msg):
    """Its proof, when it comes, keeps a room peer here (an opportunistic
    message: one packet, its receipt). None, twice running, over a route
    while it's heard over another: that route is set aside (beta.80)."""
    if h not in _peers:
        return
    rc = getattr(getattr(msg, "packet", None), "receipt", None)
    if rc is not None:
        rc.set_timeout(PROOF_WAIT_S)
        rc.set_delivery_callback(lambda r, h=h: _proof_back(h))
        rc.set_timeout_callback(lambda r, h=h: _no_proof(h))


# A route that doesn't deliver (1.0.0-beta.80): a computer Stump kept a
# 1-hop path to a FireFly over the home Wi-Fi (its Local network) that the
# phone didn't take messages on, while the FireFly's own announces came
# over the chain (2 hops, turned down as worse): every answer lost, /auth
# never answered. Two messages running without a proof, over a route while
# the client's announces come in over another: that route is set aside for
# AVOID_S and another asked for (urns/transport.py: avoid_path).
PROOF_WAIT_S = 45
NO_PROOF_MAX = 2
AVOID_S = 3600
_misses = {}


def _proof_back(h):
    _misses.pop(h, None)
    _still_here(h)


def _no_proof(h):
    n = _misses.get(h, 0) + 1
    if n < NO_PROOF_MAX:
        _misses[h] = n
        return
    _misses.pop(h, None)
    try:
        from urns.transport import Transport
        dest = bytes.fromhex(h)
        via = Transport.path_interface(dest)
        if via and Transport.heard_elsewhere(dest, via, 2 * AVOID_S):
            print("[rrc_mesh] %s: no proof for %d messages over %s, while it's heard over another way: "
                  "that route set aside" % (h[:8], NO_PROOF_MAX, via))
            Transport.avoid_path(dest, via, AVOID_S)
    except Exception as e:
        print("[rrc_mesh] route check:", e)


def _back_room(h, landing, lang):
    """The room a client that went quiet was in, if it comes back within
    BACK_S and may still be there (tiers, bans: as a /join); else None."""
    w = _was.pop(h, None)
    if w is None or time.time() - w[1] > BACK_S or w[0] == landing or not rrc.room_exists(w[0]):
        return None
    try:
        if _tier_refusal(h, w[0], lang):
            return None
        import mods
        if mods.join_refusal(h, w[0], lang):
            return None
    except Exception:
        return None
    return w[0]

def _prune_peers(now):
    # While the radio is down nobody on LoRa can be heard: they aren't
    # gone, and what waits for them stays (up to LORA_KEEP_S).
    keep = _lora_waits() and _lora["since"] is not None and now - _lora["since"] < _setting("LORA_KEEP_S", LORA_KEEP_S)
    stale = [h for h, p in _peers.items() if now - p["last_seen"] > PEER_TIMEOUT
             and not (keep and not p.get("stump"))]
    for h in stale:
        p = _peers.pop(h)
        if not p.get("stump") and p["room"] != p.get("home_room"):
            _was[h] = (p["room"], now)          # back there if it comes back (_back_room)
            if len(_was) > MAX_MESH_PEERS:
                _was.pop(min(_was, key=lambda k: _was[k][1]))
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
        if not stump and rrc.is_stump(dest_hash_hex) and (not _on_chain(dest_hash_hex, message) or _other_chain(dest_hash_hex)):
            # Another Stump, over LoRa (or reached through this chain's
            # Stump with a radio, its beacon saying it's not of this
            # chain): shares #lora-mesh with this one.
            room, stump = rrc.MESH_ROOM, True
        elif stump or _on_chain(dest_hash_hex, message):
            # Over the chain: #wifi-mesh, not #lora-mesh (which is for LoRa).
            room = rrc.CHAIN_ROOM
        elif rrc.no_lora():
            # No LoRa on this Stump nor its chain: an app over the local
            # network (AutoInterface, TCP) isn't on LoRa, and #lora-mesh
            # lets no one in -- #main, where the web chat reads it.
            room = rrc.DEFAULT_ROOM
        elif ident is not None:
            room, allowed, redirect_reason = ident.mesh_landing_room(dest_hash_hex)
            redirected = not allowed
        else:
            room = rrc.MESH_ROOM
        landing = room
        if not stump and not redirected and dest_hash_hex in _was:
            import i18n
            room = _back_room(dest_hash_hex, landing, i18n.get_lang(dest_hash_hex)) or room
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
            "home_room": landing,
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


def _nick_held(nick, h):
    """Someone else goes by this nick: in the chat (rrc), or a peer or a
    contact here -- a quiet peer drops out of the chat's list after a few
    minutes but still goes by it (1.0.0-beta.60: two Stumps of the same
    name could both get the plain one)."""
    if rrc.nick_taken(nick, h):
        return True
    low = nick.lower()
    for k, p in _peers.items():
        if k != h and p.get("nick", "").lower() == low:
            return True
    for k, r in _reach.items():
        if k != h and r.get("nick", "").lower() == low:
            return True
    return False


def _make_nick(display_name, dest_hash_hex):
    """Produce a valid, unique-enough nick from an LXMF display name."""
    raw = (display_name or "").strip() or ("mesh-" + dest_hash_hex[:4])
    nick = rrc.clean_nick(raw)[:rrc.MAX_NICK_LEN] or ("mesh-" + dest_hash_hex[:4])
    # Taken: with a tag from its identity ("LaBuche-k7q2", the same on
    # every Stump), not a number (1.0.0-beta.60).
    return rrc.free_nick(nick, dest_hash_hex, _nick_held)


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


_forged = set()           # LXMF addresses named by beacons that weren't theirs (logged once)


# ── The radio, this Stump's own or one along the chain ───────────────
# "up": a radio online (this Stump's Heltec, by USB, Bluetooth or the
# CAM's bridge; or another Stump of the chain's, from its beacon).
# "connecting": none online, for less than LORA_GRACE_S -- the Heltec's
# own reconnect gets that long, nothing is said yet. "down": longer than
# that: someone writing to LoRa is told, once a minute, and their line
# waits (the room and DM marks don't move) to go out when it's back, up
# to LORA_KEEP_S. "none": this Stump has no radio, nor does any Stump
# of the chain: nothing to wait for.
_lora = {"state": "none", "since": None, "own": None, "told": {}}


def _setting(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def lora_status(now=None):
    """{"state": "none"|"up"|"connecting"|"down", "down_s": seconds or None}"""
    now = time.time() if now is None else now
    since = _lora["since"]
    return {"state": _lora["state"], "down_s": int(now - since) if since is not None else None}


def _lora_paths():
    """(this Stump or one of its chain has a radio, one is online now)."""
    own = None
    try:
        import radio
        own = radio.hw()["state"]
    except Exception:
        pass
    if own and own != "none":
        if own != _lora["own"] and _lora["own"] == "online":
            # Our radio just went: the chain hears it from our beacon, now
            # rather than at the half-hourly one.
            try:
                import chain
                chain.link_event()
            except Exception:
                pass
        _lora["own"] = own
        if own == "online":
            return True, True
    else:
        _lora["own"] = own
    has, up = bool(own and own != "none"), False
    try:
        import discovery
        for s in discovery.chain_stumps():
            if s.get("heard_s", 10 ** 9) > 7200:
                continue
            if s.get("has_radio"):
                has = True
            if s.get("radio"):
                up = True
    except Exception:
        pass
    return has, up


def lora_tick(now=None):
    now = time.time() if now is None else now
    has, up = _lora_paths()
    before = _lora["state"]
    if not has:
        _lora["state"], _lora["since"] = "none", None
    elif up:
        if before in ("down", "connecting") and _lora["since"] is not None:
            print("[rrc_mesh] the radio is back after %d s" % int(now - _lora["since"]))
            if now - _lora["since"] > _setting("LORA_KEEP_S", LORA_KEEP_S):
                _skip_backlog()
        _lora["state"], _lora["since"] = "up", None
    else:
        if _lora["since"] is None:
            _lora["since"] = now
        grace = _setting("LORA_GRACE_S", LORA_GRACE_S)
        _lora["state"] = "down" if now - _lora["since"] >= grace else "connecting"
        if _lora["state"] == "down" and before != "down":
            print("[rrc_mesh] the radio has been down %d s: telling who writes to LoRa, keeping their lines"
                  % int(now - _lora["since"]))
    return _lora["state"]


LORA_GRACE_S = 120
LORA_KEEP_S = 3600


def _lora_waits():
    """Lines for LoRa wait (the radio connecting or down)."""
    return _lora["state"] in ("connecting", "down")


def _skip_backlog():
    """Down longer than LORA_KEEP_S: what waited is let go, not sent all
    at once an hour late."""
    for h, p in _peers.items():
        if not p.get("stump"):
            msgs = rrc.since(p["room"], p.get("last_sent_id", 0))
            if msgs:
                p["last_sent_id"] = msgs[-1]["id"]
            dms = rrc.dms_since(h, p.get("last_dm_id", 0))
            if dms:
                p["last_dm_id"] = dms[-1]["id"]
    print("[rrc_mesh] the radio was down over an hour: what waited is let go")


def _lora_notice(client_id, kind, target=None):
    """The line someone writing to LoRa gets while the radio is down."""
    if _lora["state"] != "down":
        return None
    if kind == "dm":
        # Only a DM to someone on LoRa (not a person here, nor on another Stump by Wi-Fi).
        t = (target or "").lower()
        on_lora = any(p["nick"].lower() == t and not p.get("stump") for p in _peers.values()) or \
            any(r["nick"].lower() == t for r in _reach.values())
        if not on_lora:
            return None
    now = time.time()
    if now - _lora["told"].get(client_id, 0) < 60:
        return None
    _lora["told"][client_id] = now
    if len(_lora["told"]) > 200:
        _lora["told"].clear()
    mins = max(1, int((now - _lora["since"]) // 60))
    return rrc.tok_rate(i18n.t("lora_down_waits", i18n.get_lang(client_id), min=mins))


rrc.LORA_NOTICE[0] = _lora_notice
def _no_lora_now():
    """No radio on this Stump nor along its chain, now: the state lora_tick
    keeps, checked again when it says none (it starts there, and only
    turns every 5 s: a /join right after boot was refused)."""
    if _lora["state"] != "none":
        return False
    has, _ = _lora_paths()
    return not has


if rrc.LORA_NONE[0] is None:          # (a test sets False: #lora-mesh open)
    rrc.LORA_NONE[0] = _no_lora_now


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
        # The LXMF address must be the beacon's own identity's: the announce
        # is signed by that identity, but the address inside is just data.
        # Anyone could otherwise announce a beacon naming SOMEONE ELSE's
        # address, and this Stump would take that person for a Stump (their
        # messages read as a Stump's batches, their name off the lists).
        from urns.destination import Destination
        ident_hash = Identity.truncated_hash(bytes(packet.data[:off]))
        if lxmf_hash != Destination.hash(ident_hash, "lxmf", "delivery"):
            if lxmf_hash.hex() not in _forged:
                if len(_forged) > 32:
                    _forged.clear()
                _forged.add(lxmf_hash.hex())
                print("[rrc_mesh] a Stump beacon naming an address not its own (%s): ignored"
                      % lxmf_hash.hex()[:8])
            return True
        answer = False
        if len(fields) > 4:
            try:
                import discovery
                c = fields[5] if len(fields) > 5 else None
                # Its first beacon since this Stump started, or the first of
                # a new boot of its own ("b": it restarted, and forgot this
                # Stump while this one still knew it): answer, as a new Stump
                # is answered. Knowing it as a Stump from the chat alone
                # (rrc.is_stump) isn't enough: it may not know this one.
                answer = lxmf_hash.hex() not in discovery._known or discovery.restarted(lxmf_hash.hex(), c)
                discovery.heard(lxmf_hash.hex(), str(fields[2]), fields[4], c)
            except Exception:
                pass
        own = router.delivery_destination
        if own is None or lxmf_hash != bytes(own.hash):
            h = lxmf_hash.hex()
            if answer and h in _peers:
                _peers[h].pop("rsent", None)    # it forgot the rooms' scopes: told again next pass
            if answer:
                _names_soon(h)
            if answer or not rrc.is_stump(h):
                # A Stump new to this one (or new again, above): answer with
                # this one's beacon, so it learns this Stump too (over the
                # radio alone there's no chain link to do it).
                try:
                    import chain
                    chain.stump_heard()
                except Exception:
                    pass
            rrc.mark_stump(h)
            _lb_release(h)
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
    if app_data and rrc.app_tag_in(app_data):
        rrc.mark_app(h)                 # a Stump app says so in its announce
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
    _still_here(h)                      # in a room: an announce of its own keeps it there (1.0.0-beta.79)
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
    try:
        _lb_install(router)
    except Exception as e:
        print("[rrc_mesh] LoRa broadcast not installed:", e)


def _prune_reach(now):
    keep = _lora_waits() and _lora["since"] is not None and now - _lora["since"] < _setting("LORA_KEEP_S", LORA_KEEP_S)
    for h in [k for k, r in _reach.items() if now - r["last_seen"] > ANNOUNCE_TIMEOUT and not keep]:
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


# Whether a LoRa sender has answered for its address (1.0.0-beta.71):
# its last message's signature checked out against the key from its
# announce. An open node also takes a message it can't check yet (no
# announce heard: no key), as LXMF clients do -- as chat only: no role
# (op, mod), not verified for a room's tier, /invite or /whoami, until a
# message of theirs checks out. And it asks for that sender's announce
# (a path request), so the next one does. Before, anyone could send as
# an op's address to a Stump that hadn't heard the op's announce yet.
_proved = {}              # LXMF hex -> when its last message checked out
_unproved = {}            # LXMF hex -> when its last message couldn't be checked
_asked = {}               # LXMF hex -> when its announce was asked for
ASK_EVERY = 60
PROOF_MAX = 64


def _note_proof(h, message):
    now = time.time()
    if getattr(message, "signature_validated", True):
        _unproved.pop(h, None)
        _proved[h] = now
        table = _proved
    else:
        _proved.pop(h, None)
        _unproved[h] = now
        table = _unproved
        if now - _asked.get(h, -ASK_EVERY) >= ASK_EVERY:
            _asked[h] = now
            if len(_asked) > PROOF_MAX:
                _asked.pop(min(_asked, key=_asked.get))
            try:
                from urns.transport import Transport
                Transport.request_path(bytes.fromhex(h))
            except Exception as e:
                print("[rrc_mesh] couldn't ask for %s's announce: %s" % (h[:8], e))
    if len(table) > PROOF_MAX:
        table.pop(min(table, key=table.get))


def proved(h):
    """This LoRa sender's messages check out (this session)."""
    return str(h) in _proved


def unproved(h):
    """This LoRa sender's last message couldn't be checked (no key yet)."""
    return str(h) in _unproved


def identity_hash(h):
    """The identity (hex) behind an LXMF address, from its announce; or None."""
    try:
        from urns.identity import Identity
        i = Identity.recall(bytes.fromhex(h))
        return i.hexhash if i is not None else None
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
        _note_proof(dest_hash_hex, message)
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
                _lb_release(dest_hash_hex)
                peer["stump"] = True
                # Of this chain, unless its beacon says otherwise: a LoRa
                # Stump reached through this chain's Stump with a radio
                # (1.0.0-beta.68). No beacon yet: of the chain, as before
                # (a chain's Stump sends #lora-mesh too), moved once its
                # beacon -- an announce, it comes through the chain -- says.
                want = rrc.MESH_ROOM if _other_chain(dest_hash_hex) else rrc.CHAIN_ROOM
                if peer["room"] != want:
                    rrc.system(peer["room"], rrc.ev_leave("~" + peer["nick"]))
                    peer["room"] = peer["home_room"] = want
                    rrc.system(want, rrc.ev_join("~" + peer["nick"]))
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
            # Also a room's scope ("[ROOM] #bavard wifi 1", titled "#bavard";
            # it showed in #lora-mesh as said by "mesh-fd92"), and a names
            # list (1.0.0-beta.70).
            if (t in ("#" + rrc.MESH_ROOM, "#" + rrc.CHAIN_ROOM) or text0.startswith("[DM>")
                    or (t.startswith("#") and text0.startswith(("[ROOM", "[NAMES")))):
                rrc.mark_stump(dest_hash_hex)
                _lb_release(dest_hash_hex)
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


BANNED_TELL_S = 600
_banned_told = {}


def _handle(dest_hash_hex, peer, text):
    """Parse text and act on it. Returns (reply_lines, new_room_or_None)."""
    room = peer["room"]
    lang = i18n.get_lang(dest_hash_hex)
    import mods
    if not peer.get("stump") and mods.total(dest_hash_hex) and not (
            text[:1] == "/" and (mods.is_command(text) or text[:9].lower().startswith(("/create ", "/setchan", "/chanreq")))):
        # Banned from the chat altogether (mods.py): told so at most every
        # BANNED_TELL_S (each answer is LoRa airtime), nothing else.
        now = time.time()
        if now - _banned_told.get(dest_hash_hex, 0) < BANNED_TELL_S:
            return [], None
        _banned_told[dest_hash_hex] = now
        if len(_banned_told) > 64:
            _banned_told.pop(min(_banned_told, key=_banned_told.get))
        return ["✗ " + mods.total_refusal(dest_hash_hex, lang)], None
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
        import mods
        no = mods.join_refusal(dest_hash_hex, target, lang)
        if no:
            return ["✗ " + no], None          # banned or kicked (mods.py)
        rrc.system(room, rrc.ev_leave("~" + nick))
        rrc.system(target, rrc.ev_join("~" + nick))
        return [rrc.moved(target)], target

    if text.lower().startswith("/nick "):
        new = rrc.clean_nick(text[6:].strip())
        if not new:
            return [i18n.t("nick_usage", lang)], None
        if new.lower() == nick.lower():
            return [i18n.t("nick_already", lang)], None
        said = []
        if _nick_held(new, dest_hash_hex):
            want = new
            new = rrc.free_nick(new, dest_hash_hex, _nick_held)
            if new.lower() == nick.lower():
                return [i18n.t("nick_already", lang)], None
            said = [i18n.t("nick_tagged", lang, nick=want, tagged=new)]
        old = nick
        peer["nick"] = new
        rrc.system(room, rrc.ev_rename("~" + old, "~" + new))
        return said, None

    if text.lower() == "/rooms":
        return [rrc.rooms_line(r, len(rrc.names_in_room(r)), rrc.topic(r, lang)) + _tier_suffix(r)
                for r in rrc.room_names()], None

    if text.lower() in ("/names", "/who"):
        names = rrc.names_marked(room)
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
        _send_queue.append((router, dest, _profiles_out(json.dumps(recs)), PROFILES_TITLE))
        cur = nxt
    peer["prof_seq"] = cur


# Profiles (password hashes) cross the chain only with the chain secret's
# mark: {"r": <records, as JSON text>, "t": HMAC-SHA256(secret, that text),
# first 16 bytes, hex}. Coming in over a chain link isn't enough on its
# own -- a device on a Stump's Wi-Fi can send UDP that looks like it comes
# from the Stump above. Without a chain secret (an open chain), the plain
# list as before. Older Stumps (before 1.0.0-beta.37) send and expect the
# plain list: with a secret, update every Stump of the chain together.
def _profiles_tag(secret, text):
    from urns.crypto.hmac import new as hmac_new
    import ubinascii
    return ubinascii.hexlify(hmac_new(secret.encode(), text.encode()).digest()[:16]).decode()


def _chain_secret():
    try:
        import chain
        s = chain.settings()
        return s["secret"] if s else ""
    except Exception:
        return ""


def _profiles_out(text):
    secret = _chain_secret()
    if not secret:
        return text
    try:
        import ujson as json
    except ImportError:
        import json
    return json.dumps({"r": text, "t": _profiles_tag(secret, text)})


def _profiles_in(content):
    """The records in a profiles batch, or None if it isn't marked with
    this chain's secret (when it has one)."""
    try:
        import ujson as json
    except ImportError:
        import json
    data = json.loads(content or "[]")
    secret = _chain_secret()
    if not secret:
        return data if isinstance(data, list) else (json.loads(data["r"]) if isinstance(data, dict) and "r" in data else [])
    if not isinstance(data, dict) or not isinstance(data.get("r"), str) or not isinstance(data.get("t"), str):
        return None
    import webtext
    if not webtext.same(data["t"], _profiles_tag(secret, data["r"])):
        return None
    recs = json.loads(data["r"])
    return recs if isinstance(recs, list) else None


def _take_profiles(message):
    iface = getattr(message, "receiving_interface", None)
    if not getattr(message, "signature_validated", False) or not type(iface).__name__.startswith("Chain"):
        print("[rrc_mesh] profiles from %s ignored: not signed, or not over the chain link"
              % message.source_hash.hex()[:8])
        return
    try:
        import profiles
        recs = _profiles_in(message.content_as_string())
        if recs is None:
            print("[rrc_mesh] profiles from %s ignored: not marked with this chain's secret"
                  % message.source_hash.hex()[:8])
            return
        n = profiles.merge(recs)
        if n:
            rrc.release_registered_nicks()
            print("[rrc_mesh] %d profile(s) from %s" % (n, message.source_hash.hex()[:8]))
    except Exception as e:
        print("[rrc_mesh] profiles from %s unreadable: %s" % (message.source_hash.hex()[:8], e))


def _flush_to_peer(router, dest_hash_hex, peer):
    """Forward any new RRC messages in this peer's room to them."""
    if not peer.get("stump"):
        import mods
        if mods.total(dest_hash_hex):
            # Banned from the chat altogether: nothing goes to them (their
            # marks move on, so an unban doesn't send what they missed).
            m = rrc.since(peer["room"], peer["last_sent_id"])
            if m:
                peer["last_sent_id"] = m[-1]["id"]
            d = rrc.dms_since(dest_hash_hex, peer.get("last_dm_id", 0))
            if d:
                peer["last_dm_id"] = d[-1]["id"]
            return
    room    = peer["room"]
    last_id = peer["last_sent_id"]
    msgs    = rrc.since(room, last_id)
    # Checked independently of room traffic -- this used to return
    # early whenever the room was quiet, before the DM check below
    # ever ran, so a DM to a peer in a silent room waited until
    # someone happened to speak in that room.
    dms     = rrc.dms_since(dest_hash_hex, peer.get("last_dm_id", 0))

    remote = _remote_dm_lines(dest_hash_hex) if peer.get("stump") else []
    # Who's in #wifi-mesh here, for the other Stumps' /names and DMs (chain
    # protocol 2): when it changes, and every NAMES_EVERY s. It used to
    # list only who had spoken, so someone quiet on a Stump in the middle
    # was nobody's contact elsewhere.
    names_line = None
    if _names_due:
        _names_check(time.time())
    if peer.get("stump") and room == rrc.CHAIN_ROOM:
        roster = " ".join(_room_roster(rrc.CHAIN_ROOM))
        now = time.time()
        if roster != peer.get("wifi_roster") or now - peer.get("wifi_roster_at", 0) > NAMES_EVERY:
            peer["wifi_roster"], peer["wifi_roster_at"] = roster, now
            names_line = "[NAMES] " + (roster or "-")
    if peer.get("stump"):
        _flush_profiles(router, dest_hash_hex, peer)
        # #lora-mesh to another Stump always goes the radio-first way
        # (_flush_lora_to_stump), whatever room it sits in here; so its
        # room lines don't go a second time below.
        _flush_lora_to_stump(router, dest_hash_hex, peer)
        _flush_rooms(router, dest_hash_hex, peer)
        if room == rrc.MESH_ROOM:
            if msgs:
                peer["last_sent_id"] = msgs[-1]["id"]
            msgs = []
    if not msgs and not dms and not remote and not names_line:
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
        if stump and (m["kind"] == "system" or m.get("via") or m.get("bot")):
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
    if names_line:
        lines.append(names_line)

    if lines:
        # Room lines in this batch belong to `room`; say so in the title.
        # A DM-only batch has no room (its lines name their author).
        text, title = "\n".join(lines), ("#" + room if msgs or names_line else "")
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
        if m["nick"] == skip_nick or m["kind"] == "system" or m.get("via") or m.get("bot"):
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


def _radio_pieces(lines, limit=RADIO_CHUNK, parts=False):
    """Lines grouped into texts of at most `limit` bytes; a longer line is
    cut, each piece keeping its "<nick> " so the other side files it. A
    long names list is split between whole names: "[NAMES 1/2] ...",
    "[NAMES 2/2] ..." to a Stump that takes parts (`parts`), else as many
    whole names as one "[NAMES] ..." holds. (It was cut like any line,
    mid-name, the rest without its "[NAMES]": the other Stump kept the
    first piece, and showed the rest as a line in #lora-mesh -- the S3's
    /names on the kit, once a LoRa Stump had about 10 people.)"""
    out, cur = [], ""
    for line in lines:
        pieces = [line]
        if line.startswith("[NAMES] ") and len(line.encode()) > limit:
            pieces = _names_parts(line[8:].split(), limit, parts)
        elif len(line.encode()) > limit:
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


def _names_parts(entries, limit, parts):
    """A names list's entries in lines of at most `limit` bytes, whole
    names only (1.0.0-beta.70)."""
    room = limit - len("[NAMES 99/99] ")
    groups, cur = [], []
    for e in entries:
        if len(e.encode()) > room:
            continue
        if cur and len(" ".join(cur + [e]).encode()) > room:
            groups.append(cur)
            cur = []
        cur.append(e)
    if cur or not groups:
        groups.append(cur or ["-"])
    if not parts or len(groups) == 1:
        return ["[NAMES] " + " ".join(groups[0])]
    n = len(groups)
    return ["[NAMES %d/%d] %s" % (i + 1, n, " ".join(g)) for i, g in enumerate(groups)]


def _parts_capable(h):
    """Stump h takes a long names list in parts (its beacon's "np")."""
    try:
        import discovery
        v = discovery._known.get(h)
        c = v.get("caps") if v else None
        return isinstance(c, dict) and bool(c.get("np"))
    except Exception:
        return False


def _no_radio(h):
    """Stump h said in its beacon that it has no radio online. (Not heard
    yet, or from before the beacon said: not known, so False.)"""
    try:
        import discovery
        v = discovery._known.get(h)
        c = v.get("caps") if v else None
        return isinstance(c, dict) and not c.get("rad")
    except Exception:
        return False


def _queue_lora(router, dest_hash_hex, lines, title=None):
    """#lora-mesh lines for another Stump. Over the chain's Wi-Fi when the
    chain reaches it, over this Stump's radio (single packets) only when
    nothing else does.

    Until 1.0.0-beta.58 they went over the radio whenever this Stump had
    one, Wi-Fi or not: every line on the air once per Stump of the chain
    (LoRa airtime, a send each, the Stump held meanwhile), and never
    heard by a Stump with no radio of its own (a WROVER with no Heltec, a
    computer): its people could write to #lora-mesh, never read the
    answers."""
    dest = bytes.fromhex(dest_hash_hex)
    title = title or "#" + rrc.MESH_ROOM
    chain_if = _chain_iface(dest)
    r = None if chain_if is not None else _radio()
    if r is None or _no_radio(dest_hash_hex):
        text = "\n".join(lines)
        if chain_if is not None and len((text + title).encode()) <= RADIO_CHUNK:
            # Short: tied to the chain, or it goes out on every interface.
            _send_queue.append((router, dest, text, title, None, chain_if))
        else:
            _send_queue.append((router, dest, text, title))
        return
    for text in _radio_pieces(lines, parts=_parts_capable(dest_hash_hex)):
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

# A Stump that restarted (or is new): who's here (/names) goes to it next
# pass, and once more NAMES_AGAIN_S later -- not up to NAMES_EVERY later,
# which left its /names with only its own people meanwhile. Twice: the
# first may go before it knows this Stump again (a radio broadcast it
# can't check yet is dropped), or be lost on the air (1.0.0-beta.64).
NAMES_AGAIN_S = 30
_names_due = {}           # Stump LXMF hex -> when to send its names list again


def _names_soon(h):
    _names_resend(h)
    _names_due[h] = time.time() + NAMES_AGAIN_S
    if len(_names_due) > 32:
        _names_due.pop(min(_names_due, key=_names_due.get))


def _names_resend(h):
    p = _peers.get(h)
    if p is not None:
        for k in ("lora_roster", "wifi_roster"):
            p.pop(k, None)
    _lb["roster"] = None                  # the radio broadcast's list too


def _names_check(now):
    for h in [h for h, t in _names_due.items() if now >= t]:
        _names_due.pop(h)
        _names_resend(h)


# ── Rooms with a scope (rooms.py, 1.0.0-beta.62) ──
# Between Stumps whose beacon says "rs": each room's scope, "[ROOM] #room
# scope n" (to the chain: every scope, the newest n wins; outside it: a
# LoRa room offered), "[ROOMOK] #room" (shared, both agreed) and
# "[ROOMNO] #room" (refused), then the room's lines in batches titled
# "#room": a Wi-Fi room over the chain's Wi-Fi only, a LoRa room the
# #lora-mesh way (the chain's Wi-Fi where it reaches, else the radio).
# Plugin bots' lines go too (tagged "Bavard-k7q2", rrc.bot_nick), BarKeep's
# don't. No [NAMES] for these rooms: airtime.
ROOM_EVERY = 120              # the scopes again to the chain (Wi-Fi: cheap)
OFFER_EVERY = 1800            # a LoRa room offered again outside it, until answered
REFUSED_S = 86400
CATCH_UP = 5                  # lines a Stump newly sharing a room gets from before


_my_chain = [None, -999]      # this Stump's chain id, and when it was read


def _my_chain_id():
    now = time.time()
    if now - _my_chain[1] > 60:
        try:
            import chain
            _my_chain[0] = chain.chain_id() if chain.settings() else None
        except Exception:
            _my_chain[0] = None
        _my_chain[1] = now
    return _my_chain[0]


def _same_chain_id(h):
    """Stump h's beacon names this Stump's chain ("ch": its chain id)."""
    mine = _my_chain_id()
    if not mine:
        return False
    return _chain_says(h) == mine


def _says_chain(release):
    """A beacon from this release says its chain whenever it's in one
    ("ch", 1.0.0-beta.63): without it, it's in none."""
    r = str(release or "")
    if "-beta." in r:
        try:
            return int(r.split("-beta.", 1)[1].split("-")[0].split()[0]) >= 63
        except (ValueError, IndexError):
            return False
    return bool(r) and r[0].isdigit() and not r.startswith("0.")     # 1.0.0 and on


def _chain_says(h):
    """What Stump h's beacon says of its chain: its id, "" (in none), or
    None (no beacon heard, or one too old to say)."""
    try:
        import discovery
        v = discovery._known.get(h)
        c = v.get("caps") if v else None
    except Exception:
        return None
    if not isinstance(c, dict):
        return None
    if c.get("ch"):
        return str(c["ch"])
    return "" if _says_chain(c.get("r")) else None


def _other_chain(h):
    """Stump h's beacon says it's in no chain, or in another: a LoRa Stump,
    however its messages reach this one (1.0.0-beta.68). False when this
    Stump isn't in a chain, or h's beacon can't say."""
    mine = _my_chain_id()
    said = _chain_says(h)
    return bool(mine) and said is not None and said != mine


def _room_chain(h, peer=None):
    """A Stump of this chain, for the rooms with a scope: its route over
    the chain now, known as a chain Stump before (#wifi-mesh's peer
    record keeps that: a route that went over a Heltec's LoRa for a while
    doesn't make it a stranger), or its beacon naming this chain -- from
    the first beacon, whatever the route: a Stump first heard over the
    radio is on the chain at once (1.0.0-beta.63)."""
    if not rrc.room_exists(rrc.CHAIN_ROOM):
        return False
    if _other_chain(h):
        return False              # its beacon: no chain, or another (1.0.0-beta.68)
    if _on_chain(h) or _same_chain_id(h):
        return True
    p = peer if peer is not None else _peers.get(h)
    return bool(p) and p.get("home_room") == rrc.CHAIN_ROOM


def _twice_possible(room):
    """A line that can arrive twice (over the radio and over Wi-Fi):
    #lora-mesh and the LoRa rooms. Elsewhere the same words twice are two
    lines."""
    if room == rrc.MESH_ROOM:
        return True
    if room == rrc.CHAIN_ROOM or room == rrc.DEFAULT_ROOM:
        return False
    try:
        import rooms
        return rooms.scope(room) == "lora"
    except Exception:
        return False


def _rs_capable(h):
    p = _peers.get(h)
    if p and p.get("rs"):
        return True               # it sent us a room's scope or lines: it takes them (its beacon may not be heard yet)
    try:
        import discovery
        v = discovery._known.get(h)
        c = v.get("caps") if v else None
        return isinstance(c, dict) and bool(c.get("rs"))
    except Exception:
        return False


def _room_ctl(h, peer, line):
    import rooms
    parts = line.split()
    if len(parts) < 2:
        return
    room = rrc.clean_room(parts[1].lstrip("#"))
    peer["rs"] = True
    if not room or room in rooms.builtin():
        return
    on = _room_chain(h, peer)
    if parts[0] == "[ROOM]" and len(parts) >= 4:
        sc = parts[2]
        try:
            n = int(parts[3])
        except ValueError:
            return
        if sc not in rooms.SCOPES:
            return
        if on:
            mine = (rooms.version(room), rooms.scope(room))
            if (n, sc) > mine:
                if rooms.set_scope(room, sc, n):
                    print("[rrc_mesh] #%s is %s now (from %s)" % (room, sc, peer.get("nick")))
            elif (n, sc) < mine:
                # It's behind (it was off, or just updated): ours goes to it
                # on the next pass, not in ROOM_EVERY.
                peer.setdefault("rsent", {}).pop(room, None)
        elif sc == "lora":
            res = rooms.offered(room, h, peer.get("nick"))
            if res in ("ok", "no"):
                rooms._answers.append((h, room, res))
        else:
            rooms.unshare(room, h)
    elif parts[0] == "[ROOMOK]" and not on:
        if rooms.scope(room) == "lora":
            rooms.share(room, h)
    elif parts[0] == "[ROOMNO]":
        peer.setdefault("rno", {})[room] = time.time()
        rooms.unshare(room, h)


def _shared_lines(msgs, skip_nick=None):
    """A room's lines for another Stump: this Stump's people and plugin
    bots (not BarKeep, not what came from other Stumps, no notices)."""
    try:
        import barkeep
        keeper = barkeep.BOT_NAME
    except Exception:
        keeper = "BarKeep"
    out = []
    for m in msgs:
        if m["nick"] == skip_nick or m["kind"] == "system" or m.get("via") or (m.get("bot") and m["nick"] == keeper):
            continue
        nick = m["nick"].replace(" ", "_")
        out.append(("* " + nick + " " if m["kind"] == "action" else "<" + nick + "> ") + m["body"])
    return out


def _flush_rooms(router, h, peer):
    """The rooms with a scope, with Stump h: what each one is, then its new lines."""
    import rooms
    if not _rs_capable(h):
        return
    on = _room_chain(h, peer)
    now = time.time()
    sent = peer.setdefault("rsent", {})
    rno = peer.setdefault("rno", {})
    cur = peer.setdefault("rcur", {})
    ctl = []
    for room, sc in rooms.extra().items():
        n = rooms.version(room)
        last = sent.get(room)            # (scope, n, when) last told
        if on:
            if last is None or last[:2] != (sc, n) or now - last[2] > ROOM_EVERY:
                ctl.append("[ROOM] #%s %s %d" % (room, sc, n))
                sent[room] = (sc, n, now)
        elif sc == "lora":
            if rooms.shares(room, h, False) or now - rno.get(room, -REFUSED_S) < REFUSED_S:
                continue
            if last is None or last[:2] != (sc, n) or now - last[2] > OFFER_EVERY:
                ctl.append("[ROOM] #%s lora %d" % (room, n))
                sent[room] = (sc, n, now)
        elif last is not None and last[0] == "lora":
            ctl.append("[ROOM] #%s %s %d" % (room, sc, n))     # no longer shared
            sent[room] = (sc, n, now)
    for a in [a for a in rooms._answers if a[0] == h]:
        rooms._answers.remove(a)
        ctl.append("[ROOM%s] #%s" % ("OK" if a[2] == "ok" else "NO", a[1]))
    chain_if = None
    try:
        chain_if = _chain_iface(bytes.fromhex(h))
    except Exception:
        pass
    if ctl:
        if on and chain_if is not None:
            _send_queue.append((router, bytes.fromhex(h), "\n".join(ctl), "", None, chain_if))
        elif on:
            _send_queue.append((router, bytes.fromhex(h), "\n".join(ctl), ""))   # its route, whatever it is now
        else:
            _queue_lora(router, h, ctl, "#" + rrc.MESH_ROOM)
    for room, sc in rooms.extra().items():
        if sc == "local" or not rrc.room_exists(room):
            cur.pop(room, None)
            continue
        if not rooms.shares(room, h, on):
            continue                          # (its mark kept: back, it gets what it missed)
        if room not in cur:
            # Newly shared with it (it just joined, or was just updated): the
            # last few lines, so it doesn't start in the middle of a dialogue
            # -- then on from there.
            msgs = rrc.since(room, 0)
            cur[room] = msgs[-CATCH_UP - 1]["id"] if len(msgs) > CATCH_UP else 0
        msgs = rrc.since(room, cur[room])
        if not msgs:
            continue
        cur[room] = msgs[-1]["id"]
        lines = _shared_lines(msgs[-POLL_LIMIT:], peer["nick"])
        if not lines:
            continue
        if sc == "wifi":
            for text in _radio_pieces(lines, 1500):
                if chain_if is not None:
                    _send_queue.append((router, bytes.fromhex(h), text, "#" + room, None, chain_if))
                else:
                    # A chain Stump whose route went over a Heltec for now:
                    # its route anyway, rather than lose the line.
                    _send_queue.append((router, bytes.fromhex(h), text, "#" + room))
        else:
            _queue_lora(router, h, lines, "#" + room)


def _lora_roster():
    return _room_roster(rrc.MESH_ROOM)


def _room_roster(room):
    """The people in this room on THIS Stump (not the other Stumps, not
    people this one only knows from them)."""
    stumps = set(p["nick"] for p in _peers.values() if p.get("stump"))
    out = []
    for cid, u in list(rrc._users.items()):
        n = u["nick"]
        if (u["room"] != room or n in stumps or "@" in n or cid in rrc._hidden
                or rrc.same_web(cid) is not None):
            continue
        ident = rrc.identity_of(cid)
        # "nick~3f2a91bc": who they are, so the other Stumps don't list
        # someone they have themselves (older Stumps read the nick alone).
        out.append(n + "~" + ident if ident else n)
    return sorted(set(out))[:60]


# ── #lora-mesh once for several Stumps over the radio (1.0.0-beta.58) ──
# To the Stumps this one reaches only over LoRa, a line went once per
# Stump: N Stumps, N transmissions, N sends. Now, with two or more of them
# that understand it (their beacon's "lb"), it goes once: a broadcast on
# the radio (a plain Reticulum packet, one hop, never relayed), signed by
# this Stump's LXMF identity, which the others know from its announces.
# Not encrypted: #lora-mesh is a public room, its lines go to anyone who
# joins it. [b"lb1", sender's LXMF hash, boot, seq, text, signature]; seq
# rises with each packet and boot changes when the Stump restarts, so a
# recorded packet played again is ignored. A Stump that hears a line two
# ways (Wi-Fi and the radio) shows it once (_seen_line).
LB_APP = ("stump", "lora", "mesh")
_lb = {"in": None, "out": None, "router": None, "sent": None, "seq": 0, "boot": None,
       "roster": None, "roster_at": 0, "last": {}}


def _lb_install(router):
    from urns.destination import Destination
    import os
    _lb["router"] = router
    _lb["boot"] = bytes(os.urandom(4))
    _lb["in"] = Destination(None, Destination.IN, Destination.PLAIN, *LB_APP)
    _lb["in"].set_packet_callback(_lb_in)
    _lb["out"] = Destination(None, Destination.OUT, Destination.PLAIN, *LB_APP)


def _lb_signed(src, boot, seq, text):
    return b"lb1" + bytes(src) + bytes(boot) + seq.to_bytes(4, "big") + text.encode()


def _lb_capable(h):
    try:
        import discovery
        v = discovery._known.get(h)
        c = v.get("caps") if v else None
        return isinstance(c, dict) and bool(c.get("lb"))
    except Exception:
        return False


def _lb_peers():
    """The Stumps reached over this Stump's radio only, that understand
    the broadcast: their #lora-mesh goes in one transmission."""
    if _lb["out"] is None or _radio() is None:
        return []
    out = []
    for h, p in _peers.items():
        if not p.get("stump") or not _lb_capable(h) or _no_radio(h):
            continue
        try:
            if _chain_iface(bytes.fromhex(h)) is not None:
                continue
        except Exception:
            continue
        out.append(h)
    return out


def _flush_lora_broadcast(router):
    """Each pass: this Stump's own people's new #lora-mesh lines, once on
    the radio for all the Stumps of _lb_peers(), and their marks moved on
    (each one's own sends then have nothing left to carry)."""
    hs = _lb_peers()
    if len(hs) < 2:
        _lb["sent"] = None            # one, or none: their own sends, as before
        return
    if _lb["sent"] is None:
        # From where the most advanced left off; the others catch up by
        # their own sends (any line they'd get twice shows once).
        _lb["sent"] = max(_peers[h].get("lora_sent_id", 0) for h in hs)
    start = _lb["sent"]
    msgs = rrc.since(rrc.MESH_ROOM, start)
    if len(msgs) > POLL_LIMIT:
        msgs = msgs[-POLL_LIMIT:]
    lines = _stump_lines(msgs) if msgs else []
    roster = " ".join(_lora_roster())
    now = time.time()
    if roster != _lb["roster"] or now - _lb["roster_at"] > NAMES_EVERY:
        _lb["roster"], _lb["roster_at"] = roster, now
        lines.append("[NAMES] " + (roster or "-"))
    if msgs:
        _lb["sent"] = msgs[-1]["id"]
    for h in hs:
        p = _peers[h]
        if p.get("lora_sent_id", 0) >= start:
            p["lora_sent_id"] = _lora_marks[h] = max(p.get("lora_sent_id", 0), _lb["sent"])
            p["lora_roster"], p["lora_roster_at"] = _lb["roster"], _lb["roster_at"]
    if not lines:
        return
    r = _radio()
    for text in _radio_pieces(lines, RADIO_CHUNK - 30, all(_parts_capable(h) for h in hs)):
        _send_queue.append(("lb", text, r))


def _lb_send(item):
    _, text, iface = item
    router = _lb["router"]
    if router is None or _lb["out"] is None:
        return
    from urns.packet import Packet
    from urns import umsgpack
    src = bytes(router.delivery_destination.hash)
    _lb["seq"] += 1
    seq = _lb["seq"]
    sig = router.identity.sign(_lb_signed(src, _lb["boot"], seq, text))
    data = umsgpack.packb([b"lb1", src, _lb["boot"], seq, text, sig])
    Packet(_lb["out"], data, attached_interface=iface, create_receipt=False).send()


# Broadcasts from a Stump not known as one yet (its beacon not heard):
# LXMF hex -> [(when, data)], a few each, for LB_HOLD_S.
_lb_held = {}
LB_HOLD_S = 120
LB_HOLD_EACH = 4
LB_HOLD_FROM = 8


def _lb_hold(h, data):
    now = time.time()
    for k in [k for k, v in _lb_held.items() if now - v[-1][0] > LB_HOLD_S]:
        del _lb_held[k]
    q = _lb_held.get(h)
    if q is None:
        if len(_lb_held) >= LB_HOLD_FROM:
            _lb_held.pop(min(_lb_held, key=lambda k: _lb_held[k][-1][0]))
        q = _lb_held[h] = []
    q.append((now, bytes(data)))
    del q[:-LB_HOLD_EACH]


def _lb_release(h):
    """Stump h's beacon is in: its broadcasts kept meanwhile, taken now
    (checked as any: its signature, played once)."""
    q = _lb_held.pop(h, None)
    if not q:
        return
    now = time.time()
    for t, data in q:
        if now - t <= LB_HOLD_S:
            _lb_in(data, None)


def _lb_in(data, packet):
    """A Stump's #lora-mesh broadcast, heard on the radio."""
    try:
        from urns import umsgpack
        from urns.identity import Identity
        f = umsgpack.unpackb(bytes(data))
        if not isinstance(f, (list, tuple)) or len(f) != 6 or f[0] != b"lb1":
            return
        src, boot, seq, text, sig = bytes(f[1]), bytes(f[2]), int(f[3]), str(f[4]), bytes(f[5])
        router = _lb["router"]
        if router is None or src == bytes(router.delivery_destination.hash):
            return
        h = src.hex()
        if not rrc.is_stump(h):
            # Only a Stump's (by its beacon). One that just started sends
            # its first lines before its beacon reaches this Stump: kept a
            # while, and taken once the beacon says it's a Stump (they were
            # dropped: its first line after a start never showed on the
            # other radio Stumps; kit finding 19, 1.0.0-beta.72).
            _lb_hold(h, data)
            return
        ident = Identity.recall(src)
        if ident is None or not ident.validate(sig, _lb_signed(src, boot, seq, text)):
            return
        last = _lb["last"].get(h)
        if last is not None and last[0] == boot and seq <= last[1]:
            return                    # played again
        _lb["last"][h] = (boot, seq)
        if len(_lb["last"]) > 64:
            _lb["last"].pop(next(iter(_lb["last"])))
        p = _peers.get(h)
        if p is None:
            p = _get_peer(h, _known_name(router, h))
        else:
            p["last_seen"] = time.time()
        _from_stump(h, p, text, router, "#" + rrc.MESH_ROOM)
    except Exception as e:
        print("[rrc_mesh] a LoRa broadcast unreadable:", e)


# A #lora-mesh line from another Stump heard twice (over Wi-Fi and over the
# radio, or a Stump catching up): shown once. (who, line) -> when.
SEEN_LINE_S = 120
_seen = {}


def _seen_line(h, line):
    now = time.time()
    if len(_seen) > 200:
        for k in [k for k, t in _seen.items() if now - t > SEEN_LINE_S]:
            del _seen[k]
    k = h + line
    t = _seen.get(k)
    _seen[k] = now
    return t is not None and now - t < SEEN_LINE_S


# ── Poll loop (scheduled as asyncio task) ─────────────────────────────

def _send_item(item):
    """Sends one queued message: (router, dest, text) or, for a pushed
    room batch, (router, dest, text, title) -- the title names the room
    the batch belongs to, so a client never has to guess (lines queued
    just before a /join could otherwise land under the new room)."""
    if item[0] == "lb":
        _lb_send(item)
        return
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
    if via is None:
        # Plugins may reword what goes to a person's app (API 2: mesh_out),
        # never what goes to another Stump.
        try:
            h = dest_hash_bytes.hex()
            p = _peers.get(h)
            # Every way this Stump knows another Stump: its peer record
            # (chain or LoRa), its beacon. Stumps always get the original
            # lines, whatever packs are on.
            if not ((p and p.get("stump")) or rrc.is_stump(h)):
                import plugins
                if plugins._mesh_out:
                    text, title = plugins.mesh_out(h, text, title)
        except Exception as e:
            print("[rrc_mesh] mesh_out:", e)
    try:
        if via is not None:
            msg = rtr.send_message(dest_hash_bytes, text, title=title, via=via)
        elif fields:
            msg = rtr.send_message(dest_hash_bytes, text, title=title, fields=fields)
        elif title:
            msg = rtr.send_message(dest_hash_bytes, text, title=title)
        else:
            msg = rtr.send_message(dest_hash_bytes, text)
        _watch_proof(dest_hash_bytes.hex(), msg)
    except Exception as e:
        print("[rrc_mesh] send error:", e)


_gc_said = [0, 0]


def _gc_ms(ms):
    """How long a memory clean-up takes on this board, said when slow
    (at most once a minute). Only when memory is low since beta.56
    (urns/mem.py): on a WROVER one takes about 2 s."""
    now = time.time()
    if ms > 300 and now - _gc_said[0] > 60:
        _gc_said[0] = now
        try:
            import gc
            used = gc.mem_alloc() // 1024
        except Exception:
            used = -1
        print("[mem] a memory clean-up took %d ms (%d KB in use)" % (ms, used))
    _gc_said[1] = max(_gc_said[1], ms)


# Sends per pass of the loop, and a pause between them: each one signs
# and encrypts without letting anything else run, so a burst of them held
# the pages and the portal (1.0.0-beta.58). What's left goes next pass, 5 s
# later; a peer's lines aren't even gathered while QUEUE_HIGH wait (its
# marks don't move, nothing is lost).
SENDS_PER_PASS = 8
SEND_GAP_S = 0.03
QUEUE_HIGH = 16
_budget = [SENDS_PER_PASS, 0]     # sends left this pass, whose turn is first


async def _drain(what):
    import uasyncio as asyncio
    while _send_queue and _budget[0] > 0:
        _doing(what)
        _send_item(_send_queue.pop(0))
        _budget[0] -= 1
        await asyncio.sleep(SEND_GAP_S)


async def poll_loop(router):
    """Drain _send_queue and push pending room messages to subscribed peers."""
    import uasyncio as asyncio

    try:
        install_announce_handler(router)
    except Exception as e:
        print("[rrc_mesh] announce handler not installed:", e)
    try:
        import rooms
        rooms.boot()                    # the rooms with a scope, back after a restart
    except Exception as e:
        print("[rrc_mesh] rooms not restored:", e)

    while True:
        await asyncio.sleep(5)
        _doing("the chat bridge (mesh sends)")
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

            _budget[0] = SENDS_PER_PASS
            while _notices:
                h, text = _notices.pop(0)
                _send_queue.append((router, bytes.fromhex(h), text))
            await _drain("the chat bridge: a message out")

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
                _doing("the chat bridge: the chain's Stumps")
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

            _doing("the chat bridge: the radio's turn")
            lora_tick(now)
            for h, r in list(_reach.items()):
                if h not in _peers and not _lora_waits():
                    _doing("the chat bridge: direct messages for " + (r.get("nick") or h[:8]))
                    if len(_send_queue) >= QUEUE_HIGH:
                        break
                    _deliver_reach_dms(router, h, r)
                    # Sent now, same as the room-peer loop below -- left
                    # queued, it waited a whole extra 5-second cycle.
                    await _drain("the chat bridge: a message out")

            try:
                _doing("the chat bridge: #lora-mesh on the radio")
                _flush_lora_broadcast(router)
                await _drain("the chat bridge: #lora-mesh on the radio")
            except Exception as e:
                print("[rrc_mesh] LoRa broadcast:", e)
            # A different peer first each pass: with QUEUE_HIGH reached,
            # the ones at the end of the list wait a pass, not forever.
            order = list(_peers.items())
            if order:
                k = _budget[1] % len(order)
                order = order[k:] + order[:k]
                _budget[1] += 1
            for dest_hash_hex, peer in order:
                if _lora_waits() and not peer.get("stump"):
                    continue        # a LoRa person: their lines wait for the radio (lora_tick)
                if len(_send_queue) >= QUEUE_HIGH:
                    break           # the rest next pass, where they left off
                _doing("the chat bridge: lines for " + (peer.get("nick") or dest_hash_hex[:8]))
                _flush_to_peer(router, dest_hash_hex, peer)
                await _drain("the chat bridge: a message out to " + (peer.get("nick") or dest_hash_hex[:8]))

            _doing("the chat bridge: a memory clean-up")
            t0 = time.ticks_ms()
            _gc_collect()
            _gc_ms(time.ticks_diff(time.ticks_ms(), t0))

        except Exception as e:
            print("[rrc_mesh] poll_loop error:", e)
