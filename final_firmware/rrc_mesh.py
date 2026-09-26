# Project Stump -- RRC ↔ Mesh bridge
# Place at: firmware/rrc_mesh.py
#
# Connects the local RRC chat rooms to the Reticulum mesh so that a
# MeshCommander (or any LXMF-capable) peer can participate in the room
# as a named user alongside the local walk-up crowd.

import time
import rrc

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


# ── Peer state ────────────────────────────────────────────────────────

def _prune_peers(now):
    stale = [h for h, p in _peers.items() if now - p["last_seen"] > PEER_TIMEOUT]
    for h in stale:
        p = _peers.pop(h)
        rrc.system(p["room"], p["nick"] + " left the mesh")
        rrc.drop_user(h)
        if h in _reach:
            # Still announcing: stays reachable by DM, and continues
            # from where room-peer delivery left off -- no re-sends.
            _reach[h]["last_dm_id"] = max(_reach[h]["last_dm_id"], p.get("last_dm_id", 0))
    if len(_peers) > MAX_MESH_PEERS:
        ordered = sorted(_peers.items(), key=lambda kv: kv[1]["last_seen"])
        for h, p in ordered[: len(_peers) - MAX_MESH_PEERS]:
            rrc.system(p["room"], p["nick"] + " left the mesh (evicted)")
            del _peers[h]
            rrc.drop_user(h)


def _get_peer(dest_hash_hex, display_name):
    """Return peer state, creating it on first contact."""
    now = time.time()
    if dest_hash_hex not in _peers:
        # Someone already reachable by DM keeps the exact nick people have
        # been using for them, instead of re-deriving it.
        nick = _reach[dest_hash_hex]["nick"] if dest_hash_hex in _reach else _make_nick(display_name, dest_hash_hex)
        # Lands in rrc.MESH_ROOM ("#lxmf") unconditionally -- that room
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
        try:
            import stumpid.core as _stumpid
            room, allowed, redirect_reason = _stumpid.mesh_landing_room(dest_hash_hex)
            redirected = not allowed
        except ImportError:
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
            "last_seen": now,
            "last_sent_id": 0,
            # DMs get their own delivery marker, separate from the room
            # one. They used to share last_sent_id, which /join resets
            # to 0 (to replay recent context in the new room) -- so
            # every room change re-delivered every DM still in this
            # peer's 72-hour inbox. Confirmed directly: one DM, one
            # /join, delivered twice over LoRa.
            "last_dm_id": _reach.get(dest_hash_hex, {}).get("last_dm_id", 0),
        }
        rrc.system(room, nick + " joined from the mesh")
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
            rrc.system(room, nick + ": " + i18n.t(key, lang))
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
        own = router.delivery_destination
        if own is None or lxmf_hash != bytes(own.hash):
            rrc.mark_stump(lxmf_hash.hex())
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
        r = _reach[h] = {"nick": nick, "last_seen": now, "last_dm_id": 0, "hinted": False}
        rrc.set_reachable(h, nick)
    r["last_seen"] = now
    if len(_reach) > MAX_REACHABLE:
        idle = sorted((k for k in _reach if k not in _peers), key=lambda k: _reach[k]["last_seen"])
        for k in idle[:len(_reach) - MAX_REACHABLE]:
            _reach.pop(k)
            rrc.drop_reachable(k)


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
        lines.append("(private message via this node's chat -- reply with: /msg NICK your text)")
        r["hinted"] = True
    for m in dms:
        lines.append("[private] <" + m["nick"] + "> " + m["body"])
        if m["id"] > r["last_dm_id"]:
            r["last_dm_id"] = m["id"]
    _send_queue.append((router, bytes.fromhex(h), "\n".join(lines)))


# ── Inbound: LXMF → RRC ───────────────────────────────────────────────

def on_message(router, message):
    """Call this from the LXMF delivery callback in example_node.py."""
    try:
        dest_hash_hex = message.source_hash.hex()
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
        peer = _get_peer(dest_hash_hex, display_name)

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
        if first_contact and MESH_GREETING:
            _send_queue.append((router, message.source_hash,
                                MESH_GREETING[:MESH_GREETING_MAX]))

        text = (message.content_as_string() or "").strip()

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


def _handle(dest_hash_hex, peer, text):
    """Parse text and act on it. Returns (reply_lines, new_room_or_None)."""
    room = peer["room"]
    if text.startswith(ROOM_PREFIX) and not text.startswith("/#"):
        parts = text.split(None, 1)
        candidate = parts[0][1:]   # strip leading #
        if len(parts) > 1 and rrc.room_exists(candidate):
            room = candidate
            text  = parts[1]

    nick = peer["nick"]

    if text.lower().startswith("/join "):
        arg = text[6:].strip()
        target, err = rrc.create_room(arg)
        if err:
            return [err], None
        if target == room:
            return ["you're already in #" + target], None
        rrc.system(room,   nick + " left")
        rrc.system(target, nick + " joined from the mesh")
        return ["now in #" + target], target

    if text.lower().startswith("/nick "):
        new = rrc.clean_nick(text[6:].strip())
        if not new:
            return ["usage: /nick <name>"], None
        if new.lower() == nick.lower():
            return ["that's already your name"], None
        if rrc.nick_taken(new, dest_hash_hex):
            return ["'" + new + "' is taken"], None
        old = nick
        peer["nick"] = new
        rrc.system(room, old + " is now known as " + new)
        return [], None

    if text.lower() == "/rooms":
        lines = []
        for r in rrc.room_names():
            n = len(rrc.users_in_room(r))
            t = rrc.topic(r)
            lines.append("#" + r + " (" + str(n) + " here)" + ("  -- " + t if t else ""))
        return lines, None

    if text.lower() in ("/names", "/who"):
        names = rrc.users_in_room(room)
        return ["in #" + room + ": " + (", ".join(names) if names else "(just you)")], None

    if text.lower() == "/part":
        home = peer["home_room"]
        if room == home:
            return ["you're in #" + home + " -- nowhere to part to"], None
        rrc.system(room, nick + " left")
        rrc.system(home, nick + " returned")
        return [], home

    if text.lower() in ("/help", "/?"):
        return [
            "Mesh commands:",
            "  <text>             post to your current room",
            "  #<room> <text>     post to a specific room",
            "  /join <room>       join or create a room",
            "  /part              return to #main",
            "  /nick <name>       change your display name",
            "  /rooms             list rooms",
            "  /names             who is in your room",
        ], None

    rrc.touch_user(dest_hash_hex, nick=nick, room=room)
    replies, new_room = rrc.handle_input(dest_hash_hex, room, text)
    # "__CLEAR__" is a sentinel the WEB client interprets as "wipe your
    # local scrollback". It is meaningless to a mesh peer, and sending
    # it spends real LoRa airtime transmitting a magic word to someone
    # who will just see it as gibberish.
    replies = [r for r in replies if r != "__CLEAR__"]
    if not replies and text.lower().startswith("/clear"):
        replies = ["nothing to clear over the mesh -- you only receive new lines"]
    return replies, new_room


# ── Outbound: RRC → mesh ──────────────────────────────────────────────

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

    if not msgs and not dms:
        return

    if len(msgs) > POLL_LIMIT:
        msgs = msgs[-POLL_LIMIT:]

    nick  = peer["nick"]
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
        if m["kind"] == "system":
            lines.append("* " + m["body"])
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
        lines.append("[private] <" + m["nick"] + "> " + m["body"])
        if m["id"] > peer.get("last_dm_id", 0):
            peer["last_dm_id"] = m["id"]

    if lines:
        try:
            dest_hash_bytes = bytes.fromhex(dest_hash_hex)
        except Exception:
            return
        _send_queue.append((router, dest_hash_bytes, "\n".join(lines)))


# ── Poll loop (scheduled as asyncio task) ─────────────────────────────

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

            while _send_queue:
                item = _send_queue.pop(0)
                rtr, dest_hash_bytes, text = item
                try:
                    rtr.send_message(dest_hash_bytes, text)
                except Exception as e:
                    print("[rrc_mesh] send error:", e)
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
            now = time.time()
            _prune_peers(now)
            _prune_reach(now)
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
                        rtr, dest_hash_bytes, text = _send_queue.pop(0)
                        try:
                            rtr.send_message(dest_hash_bytes, text)
                        except Exception as e:
                            print("[rrc_mesh] send error:", e)
                        await asyncio.sleep(0)

            for dest_hash_hex, peer in list(_peers.items()):
                _flush_to_peer(router, dest_hash_hex, peer)
                while _send_queue:
                    item = _send_queue.pop(0)
                    rtr, dest_hash_bytes, text = item
                    try:
                        rtr.send_message(dest_hash_bytes, text)
                    except Exception as e:
                        print("[rrc_mesh] send error:", e)
                    await asyncio.sleep(0)

            gc.collect()

        except Exception as e:
            print("[rrc_mesh] poll_loop error:", e)
