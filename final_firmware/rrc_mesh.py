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
PEER_TIMEOUT   = 300   # seconds -- matches rrc.USER_TIMEOUT
POLL_LIMIT     = 10    # max messages forwarded in one poll cycle per peer
ROOM_PREFIX    = "#"   # optional room prefix in message body

# dest_hash_hex (str) -> {nick, room, last_seen, last_sent_id}
_peers = {}

# [(router, dest_hash_bytes, text_str), ...]  -- drained by poll_loop
_send_queue = []


# ── Peer state ────────────────────────────────────────────────────────

def _prune_peers(now):
    stale = [h for h, p in _peers.items() if now - p["last_seen"] > PEER_TIMEOUT]
    for h in stale:
        p = _peers.pop(h)
        rrc.system(p["room"], p["nick"] + " left the mesh")
    if len(_peers) > MAX_MESH_PEERS:
        ordered = sorted(_peers.items(), key=lambda kv: kv[1]["last_seen"])
        for h, p in ordered[: len(_peers) - MAX_MESH_PEERS]:
            rrc.system(p["room"], p["nick"] + " left the mesh (evicted)")
            del _peers[h]


def _get_peer(dest_hash_hex, display_name):
    """Return peer state, creating it on first contact."""
    now = time.time()
    if dest_hash_hex not in _peers:
        nick = _make_nick(display_name, dest_hash_hex)
        # Defers to stumpid's operator-settable mesh landing room if one
        # is configured, else today's unchanged default (#main). The
        # import is defensive, not required -- rrc_mesh has no hard
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
            room = rrc.DEFAULT_ROOM
        _peers[dest_hash_hex] = {
            "nick": nick,
            "room": room,
            "last_seen": now,
            "last_sent_id": 0,
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
        if room == rrc.DEFAULT_ROOM:
            return ["you're in #main -- nowhere to part to"], None
        rrc.system(room, nick + " left")
        rrc.system(rrc.DEFAULT_ROOM, nick + " joined from the mesh")
        return [], rrc.DEFAULT_ROOM

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

    if not msgs:
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
    for m in rrc.dms_since(dest_hash_hex, last_id):
        lines.append("[private] <" + m["nick"] + "> " + m["body"])
        if m["id"] > peer["last_sent_id"]:
            peer["last_sent_id"] = m["id"]

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
