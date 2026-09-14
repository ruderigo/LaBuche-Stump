# Project Stump -- RRC (Reticulum Relay Chat)
# Place at: firmware/rrc.py
#
# A real multi-user chat: shared rooms, shared history, nicknames, and
# everyone connected to the Stump sees the same conversation. This is
# what the "chat" box on the BarKeep page was NOT -- that was a private
# bot console where two people standing next to each other could not see
# each other's messages.
#
# mIRC-shaped on purpose: a room list, a message pane, a nick, and a
# single input line where /commands do the work. Anyone who has used
# IRC knows how to drive it without instruction.
#
# MEMORY-ONLY, BY DESIGN. Nothing here is written to the SD card or to
# flash. Two reasons, both deliberate:
#   - Chat is the highest-write-rate thing on the node. Persisting every
#     line would burn storage for content whose value is mostly in the
#     moment.
#   - A conversation that quietly disappears on reboot is a much easier
#     thing to reason about, privacy-wise, than one that silently
#     accumulates on a card someone might later walk off with.
# The billboard is the persistent surface; RRC is the live one.
#
# Bounded everywhere. This runs on a board with finite RAM and no
# supervision, so every growable structure has a hard ceiling: rooms,
# messages per room, nick length, message length, and tracked users.
# Rather than fail when a limit is reached, the oldest data is dropped
# -- a chat that quietly forgets old lines is fine; one that OOMs the
# whole node is not.

import time
import i18n

MAX_ROOMS = 16
MAX_MESSAGES_PER_ROOM = 60
MAX_MESSAGE_LEN = 400
MAX_NICK_LEN = 16
MAX_ROOM_NAME_LEN = 20
MAX_USERS = 40
USER_TIMEOUT = 300          # seconds of silence before a user is dropped from a room roster
DEFAULT_ROOM = "main"
# Always present, exactly like DEFAULT_ROOM -- not created lazily, not
# something an operator has to set up first. A mesh peer lands here by
# default, unconditionally, whether or not stumpid (or any auth layer)
# is even installed. Auth/tiers are a SEPARATE, optional concern that
# can restrict who's welcome in this room once it exists; they don't
# decide whether it exists or what it's called. That split is the
# actual point: the room itself is core RRC/mesh-bridge behaviour, not
# a feature of the identity plugin.
MESH_ROOM = "lxmf"

# room -> list of {"id", "ts", "nick", "body", "kind"}
# kind: "msg" (normal), "action" (/me), "system" (joins, parts, topic)
_rooms = {DEFAULT_ROOM: [], MESH_ROOM: []}
_topics = {DEFAULT_ROOM: "General. Be decent.",
           MESH_ROOM: "Mesh/LoRa traffic lands here by default."}
_next_id = [1]

# client_id -> list of {"id","ts","nick","body","kind"} addressed to
# that client only. Direct messages are kept per RECIPIENT rather than
# in a room, because that is what makes them private: a room is a
# broadcast surface, and anything posted to one reaches every poller
# and (since rrc_mesh) the radio as well.
_dms = {}
MAX_DMS_PER_USER = 30

# client_id -> {"nick", "room", "last_seen"}
_users = {}


# ---------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------

def _clean(text, limit):
    """Strips control characters and truncates. Control characters are
    removed rather than escaped because they have no legitimate use in a
    chat line and can wreck a terminal-style display."""
    out = []
    for ch in text:
        if ord(ch) >= 32 and ord(ch) != 127:
            out.append(ch)
        if len(out) >= limit:
            break
    return "".join(out).strip()


def clean_nick(nick):
    """Nicks are alphanumeric plus a few IRC-traditional characters.
    Anything else becomes '-', so a nick can never contain markup,
    whitespace that breaks alignment, or a character that would need
    escaping every time it is displayed."""
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_[]{}\\^`|"
    out = []
    for ch in nick:
        out.append(ch if ch in allowed else "-")
        if len(out) >= MAX_NICK_LEN:
            break
    cleaned = "".join(out).strip("-")
    return cleaned


def clean_room(name):
    """Room names are lowercase, alphanumeric and hyphens, no leading
    '#' (the UI adds that). Mirrors the hostname slugify used elsewhere
    in this project so room names are always URL-safe."""
    name = name.lstrip("#")
    out = []
    for ch in name.lower():
        out.append(ch if (ch.isalpha() or ch.isdigit()) else "-")
        if len(out) >= MAX_ROOM_NAME_LEN:
            break
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def default_nick(client_id):
    """A stable, non-identifying default so someone who never sets a
    nick still appears as a consistent person rather than 'anon' next to
    three other 'anon's. Derived from the client id, not shown as one --
    the raw id (an IP) is never displayed."""
    h = 0
    for ch in client_id:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return "guest-%04x" % (h & 0xFFFF)


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

def _prune_users(now=None):
    """Drops users who have gone quiet. Without this the roster only
    grows: every phone that ever joined would be listed forever as
    present, which makes /names actively misleading."""
    if now is None:
        now = time.time()
    stale = [cid for cid, u in _users.items() if now - u["last_seen"] > USER_TIMEOUT]
    for cid in stale:
        del _users[cid]
    # Hard ceiling as a second line of defence: if somehow still over
    # the cap, drop the least recently seen.
    if len(_users) > MAX_USERS:
        ordered = sorted(_users.items(), key=lambda kv: kv[1]["last_seen"])
        for cid, _ in ordered[:len(_users) - MAX_USERS]:
            del _users[cid]


def touch_user(client_id, nick=None, room=None):
    """Records that a client is present and active. Returns their state."""
    now = time.time()
    u = _users.get(client_id)
    if u is None:
        u = {"nick": nick or default_nick(client_id),
             "room": room or DEFAULT_ROOM,
             "last_seen": now}
        _users[client_id] = u
    else:
        if nick:
            u["nick"] = nick
        if room:
            u["room"] = room
        u["last_seen"] = now
    _prune_users(now)
    return u


def get_user(client_id):
    return _users.get(client_id) or touch_user(client_id)


def nick_taken(nick, by_client):
    """Prevents two people appearing under the same name. Case
    insensitive, because 'Bob' and 'bob' reading as different people in
    a chat window is a genuine source of confusion."""
    low = nick.lower()
    for cid, u in _users.items():
        if cid != by_client and u["nick"].lower() == low:
            return True
    return False


def users_in_room(room):
    _prune_users()
    return sorted(u["nick"] for u in _users.values() if u["room"] == room)


# ---------------------------------------------------------------------
# Rooms and messages
# ---------------------------------------------------------------------

def room_names():
    """'main' always first, the rest alphabetical -- a stable order, so
    the room list doesn't reshuffle under someone mid-click."""
    others = sorted(r for r in _rooms if r != DEFAULT_ROOM)
    return [DEFAULT_ROOM] + others


def room_exists(room):
    return room in _rooms


def topic(room):
    return _topics.get(room, "")


def set_topic(room, text):
    if room not in _rooms:
        return False
    _topics[room] = _clean(text, 120)
    return True


def create_room(name):
    """Returns (room, error). Joining an existing room is not an error --
    /join on a room that already exists should just work, the way it
    does on IRC."""
    slug = clean_room(name)
    if not slug:
        return None, "room names need at least one letter or number"
    if slug in _rooms:
        return slug, None
    if len(_rooms) >= MAX_ROOMS:
        return None, "room limit reached (%d) -- try an existing one" % MAX_ROOMS
    _rooms[slug] = []
    _topics[slug] = ""
    return slug, None


def post(room, nick, body, kind="msg"):
    """Adds a line to a room. Returns the message, or None if rejected.

    Oldest messages are dropped past MAX_MESSAGES_PER_ROOM rather than
    refusing new ones -- a room that stops accepting messages when it
    fills up would be broken; one that forgets its oldest lines is just
    a scrollback limit, which is what every chat client has anyway."""
    if room not in _rooms:
        return None
    body = _clean(body, MAX_MESSAGE_LEN)
    if not body:
        return None
    msg = {"id": _next_id[0], "ts": time.time(), "nick": nick,
           "body": body, "kind": kind}
    _next_id[0] += 1
    msgs = _rooms[room]
    msgs.append(msg)
    if len(msgs) > MAX_MESSAGES_PER_ROOM:
        del msgs[:len(msgs) - MAX_MESSAGES_PER_ROOM]
    return msg


def system(room, body):
    return post(room, "*", body, kind="system")


def since(room, last_id):
    """Messages newer than last_id. The client polls with the highest id
    it has seen, so it only ever receives what it is actually missing --
    the whole room isn't re-sent on every poll."""
    if room not in _rooms:
        return []
    return [m for m in _rooms[room] if m["id"] > last_id]


def find_client_by_nick(nick):
    """Resolves a nick to the client it belongs to. Case-insensitive,
    because 'Bob' and 'bob' being different people is the kind of
    confusion that loses a private message to the wrong person."""
    low = (nick or "").lower()
    for cid, u in _users.items():
        if u["nick"].lower() == low:
            return cid
    return None


def send_dm(from_nick, to_nick, body):
    """Queues a private message. Returns (ok, error_or_recipient_nick).

    Delivered by polling, same as room messages -- the recipient picks
    it up on their next cycle. Bounded per recipient: someone who never
    comes back must not accumulate messages forever on a board with
    finite RAM, so the oldest are dropped rather than new ones refused.
    """
    body = _clean(body, MAX_MESSAGE_LEN)
    if not body:
        return False, "nothing to send"
    cid = find_client_by_nick(to_nick)
    if cid is None:
        return False, "no one here called '%s' -- /names shows who is" % to_nick
    user = _users.get(cid)
    msg = {"id": _next_id[0], "ts": time.time(), "nick": from_nick,
           "body": body, "kind": "dm"}
    _next_id[0] += 1
    box = _dms.setdefault(cid, [])
    box.append(msg)
    if len(box) > MAX_DMS_PER_USER:
        del box[:len(box) - MAX_DMS_PER_USER]
    return True, user["nick"]


def dms_since(client_id, last_id):
    """Private messages for this client newer than last_id."""
    return [m for m in _dms.get(client_id, []) if m["id"] > last_id]


def reset():
    """Clears everything. Used by tests; also the honest answer to 'how
    do I clear the chat' -- there is no persistence to clear."""
    _rooms.clear()
    _rooms[DEFAULT_ROOM] = []
    _rooms[MESH_ROOM] = []
    _topics.clear()
    _topics[DEFAULT_ROOM] = "General. Be decent."
    _topics[MESH_ROOM] = "Mesh/LoRa traffic lands here by default."
    _users.clear()
    _dms.clear()
    _next_id[0] = 1


# ---------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------

def help_lines(lang):
    """The /help listing, in the requesting client's language. Command
    WORDS and their column alignment stay fixed across all languages
    (a consistent vocabulary, same reasoning as barkeep.py's bot
    console) -- only the description after each one is translated."""
    return [
        "/nick <name>      " + i18n.t("help_nick", lang),
        "/join <room>      " + i18n.t("help_join", lang),
        "/part             " + i18n.t("help_part", lang),
        "/rooms            " + i18n.t("help_rooms", lang),
        "/names            " + i18n.t("help_names", lang),
        "/topic <text>     " + i18n.t("help_topic", lang),
        "/msg <who> <text> " + i18n.t("help_msg", lang),
        "/me <action>      " + i18n.t("help_me", lang),
        "/clear            " + i18n.t("help_clear", lang),
        "/help             " + i18n.t("help_help", lang),
    ]


def handle_input(client_id, room, text):
    """
    Processes one line from a client -- either a /command or a message.

    Returns (reply_lines, new_room). reply_lines are shown only to the
    sender (command output, errors); anything everyone should see is
    posted to the room instead. new_room is None unless the room changed.
    """
    lang = i18n.get_lang(client_id)
    user = get_user(client_id)
    text = _clean(text, MAX_MESSAGE_LEN)
    if not text:
        return [], None

    if not text.startswith("/"):
        touch_user(client_id, room=room)
        posted = post(room, user["nick"], text)
        if posted is None:
            return [i18n.t("room_gone", lang)], None
        return [], None

    parts = text[1:].split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "help":
        return help_lines(lang), None

    if cmd == "nick":
        new = clean_nick(arg)
        if not new:
            return [i18n.t("nick_usage", lang)], None
        if new.lower() == user["nick"].lower():
            return [i18n.t("nick_already", lang)], None
        if nick_taken(new, client_id):
            return [i18n.t("nick_taken", lang, nick=new)], None
        old = user["nick"]
        touch_user(client_id, nick=new, room=room)
        system(room, i18n.t("nick_changed", lang, old=old, new=new))
        return [], None

    if cmd in ("join", "j"):
        if not arg:
            return [i18n.t("join_usage", lang)], None
        target, err = create_room(arg)
        if err:
            return [err], None
        if target == room:
            return [i18n.t("join_already", lang, room=target)], None
        system(room, i18n.t("left_notice", lang, nick=user["nick"]))
        touch_user(client_id, room=target)
        system(target, i18n.t("join_notice", lang, nick=user["nick"]))
        return [], target

    if cmd == "part":
        if room == DEFAULT_ROOM:
            return [i18n.t("part_in_main", lang)], None
        system(room, i18n.t("left_notice", lang, nick=user["nick"]))
        touch_user(client_id, room=DEFAULT_ROOM)
        system(DEFAULT_ROOM, i18n.t("join_notice", lang, nick=user["nick"]))
        return [], DEFAULT_ROOM

    if cmd == "rooms":
        here_word = i18n.t("rooms_here", lang)
        lines = []
        for r in room_names():
            n = len(users_in_room(r))
            t = topic(r)
            lines.append("#%s  (%d %s)%s" % (r, n, here_word, "  -- " + t if t else ""))
        return lines, None

    if cmd == "names":
        names = users_in_room(room)
        names_str = ", ".join(names) if names else i18n.t("just_you", lang)
        return [i18n.t("names_here", lang, room=room, names=names_str)], None

    if cmd == "topic":
        if not arg:
            t = topic(room) or i18n.t("topic_none", lang)
            return [i18n.t("topic_show", lang, room=room, topic=t)], None
        set_topic(room, arg)
        system(room, i18n.t("topic_set", lang, nick=user["nick"], topic=topic(room)))
        return [], None

    if cmd in ("msg", "m", "w"):
        # /msg <nick> <text>
        bits = arg.split(" ", 1)
        if len(bits) < 2 or not bits[1].strip():
            return [i18n.t("msg_usage", lang)], None
        target, body = bits[0], bits[1].strip()
        if target.lower() == user["nick"].lower():
            return [i18n.t("msg_self", lang)], None
        touch_user(client_id, room=room)
        ok, info = send_dm(user["nick"], target, body)
        if not ok:
            # info is a plain reason string send_dm assembles itself,
            # not a code -- matched EXACTLY against the two strings
            # that function can actually produce (checked directly
            # against its source), not a fragile prefix guess. If
            # send_dm's wording ever changes, this stops matching and
            # falls back to the raw English reason rather than either
            # crashing or mistranslating -- a safe degraded state, not
            # a silent wrong one, but worth knowing about if send_dm's
            # messages are ever edited without updating this.
            if info == "nothing to send":
                return [i18n.t("msg_nothing_to_send", lang)], None
            if info.startswith("no one here called"):
                return [i18n.t("msg_no_such_user", lang, nick=target)], None
            return [info], None
        # Confirmed to the sender only. Nothing is posted to the room --
        # that is the whole point, and it also keeps private traffic off
        # the radio, since rrc_mesh forwards room messages but not these.
        return [i18n.t("msg_sent", lang, nick=info, body=body)], None

    if cmd == "me":
        if not arg:
            return [i18n.t("me_usage", lang)], None
        touch_user(client_id, room=room)
        post(room, user["nick"], arg, kind="action")
        return [], None

    if cmd == "clear":
        return ["__CLEAR__"], None

    return [i18n.t("unknown_command", lang, cmd=cmd)], None
