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
import plugins
import guard

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
MESH_ROOM = "lora-mesh"
# The other Stumps of a chain (chain.py) and the people on them talk in
# CHAIN_ROOM, so #lora-mesh is left to the mesh -- LoRa phones and handhelds.
# It exists only on a Stump that's in a chain (open_chain_room()).
CHAIN_ROOM = "wifi-mesh"
# Old names still reach the room: #lxmf (before 1.0.0-beta.4) and #lora
# are #lora-mesh; #chained and #mesh are #wifi-mesh (names it had on main
# for an afternoon).
ROOM_ALIASES = {"lxmf": MESH_ROOM, "lora": MESH_ROOM, "chained": CHAIN_ROOM, "mesh": CHAIN_ROOM}


# #lora-mesh on a Stump with its own radio (a computer with a Heltec on
# USB): the room of the radio. People on LoRa are always in; on the web,
# the computer's owner (on the computer itself) and whoever they invite
# (/lora invite <nick>). Lines there never go to the other Stumps of the
# chain (rrc_mesh sends them only #wifi-mesh). Set at boot (example_node).
LORA_LOCKED = [False]
_lora_invited = set()
_LOCAL = ("127.0.0.1", "::1", "localhost")


def is_local(client_id):
    """The person on the computer this Stump runs on? Known by address
    (127.0.0.1): a browser's visitor ID or a logged-in profile maps back to
    the address it came from (guard.net, set by barkeep on each request)."""
    try:
        import guard
        return guard._k(str(client_id or "")) in _LOCAL
    except ImportError:
        return str(client_id or "") in _LOCAL


def lora_allows(client_id):
    """May this client be in #lora-mesh? Always, unless it's locked."""
    if not LORA_LOCKED[0]:
        return True
    cid = str(client_id or "")
    if len(cid) == 32 and all(c in "0123456789abcdef" for c in cid):
        return True                    # an LXMF address: someone on the mesh
    return is_local(cid) or cid in _lora_invited


def room_allows(client_id, room):
    return canon(room) != MESH_ROOM or lora_allows(client_id)


def canon(room):
    """The room's current name for an old one (lxmf -> lora-mesh)."""
    return ROOM_ALIASES.get(room, room)

# room -> list of {"id", "ts", "nick", "body", "kind"}
# kind: "msg" (normal), "action" (/me), "system" (joins, parts, topic)
_rooms = {DEFAULT_ROOM: [], MESH_ROOM: []}
# Topics someone has set. The two built-in rooms' default topics aren't
# stored: topic() shows them in the reader's language until someone sets
# a real one (which then shows as written, for everyone).
_topics = {}
_DEFAULT_TOPIC_KEYS = {DEFAULT_ROOM: "topic_default_main", MESH_ROOM: "topic_default_lora",
                       CHAIN_ROOM: "topic_default_mesh"}


def open_chain_room():
    """Called at boot on a Stump in a chain."""
    _rooms.setdefault(CHAIN_ROOM, [])


# Room activity and DM lines are symbols, not sentences: every reader
# sees the same line whatever their language, and they stay short on
# LoRa. Web chat and mesh bridge both build them here, so the notation
# can't drift between the two.
#   ✓ joined   ✗ left   ✎ changed   → moved to (reply to you)
#   ⊖ no one by that name   ~ in front of a name: on the mesh
#   [DM] <author>: text -- a private message, always naming who wrote it
def ev_join(who):
    return "✓ " + who


def ev_leave(who):
    return "✗ " + who


def ev_rename(old, new):
    return "✎ " + old + " → " + new


def ev_topic(room, text):
    return "✎ #" + room + " " + text


def dm_line(author, body):
    """A DM, as its sender and recipient both see it: "[DM] <rod>: hi".
    Always the AUTHOR's nick, never the recipient's -- so a thread reads
    as a conversation, and the confirmation of a DM you sent is your own
    line ("[DM] <you>: ..."), not an arrow toward them."""
    return "[DM] <" + author + ">: " + body


def no_such(nick):
    return "⊖ " + nick


def moved(room):
    return "→ #" + room


# Replies a client acts on start with a stable token, the same in every
# language, so a client can react without reading the sentence; the
# sentence follows " — " for people. (Like AUTH-OK, but in the same
# symbol family as the room activity above.)
#   = #room            you're already there
#   ? /cmd             no such command
#   ⊘ #room <tier>     that room's tier refused you
def tok_already(room, sentence):
    return "= #" + room + " — " + sentence


def tok_unknown(cmd, sentence):
    return "? /" + cmd + " — " + sentence


def tok_refused(room, tier, sentence):
    return "⊘ #" + room + " " + tier + " — " + sentence


#   ⧗                  slow down (rate limited)
def tok_rate(sentence):
    return "⧗ — " + sentence


# ---- Voice notes ----
# A voice note is a DM with an "audio" attachment [LXMF mode, bytes], the
# LXMF FIELD_AUDIO convention FireFly and Sideband share (FireFly client
# quickstart, 0.2.10):
#   mode 16 (AM_OPUS_OGG): a complete Ogg Opus file, RFC 7845 -- FireFly's
#                          default, on every link and in Stump voice DMs
#   modes 3-9:             raw Codec 2 1.2.0 frames, back to back
# A note's length comes from its bytes, in whole milliseconds, exactly as
# that spec defines it, so every client gets the same number, the same
# limits and the same label. Notes are relayed byte for byte unchanged.
OPUS_OGG = 16
CODEC2_MODES = {3: (4, 40), 4: (6, 40), 5: (7, 40), 6: (7, 40), 7: (8, 40), 8: (6, 20), 9: (8, 20)}
VOICE_MIN_MS = 600
VOICE_MAX_MS_CODEC2 = 15000
VOICE_MAX_MS_OPUS = 15100      # 60 ms Opus frames don't land exactly on 15 s
VOICE_MAX_BYTES = 16384        # a 15 s Opus note at 8 kbit/s is ~13.4 KB
VOICE_MIN_GAP = 5              # seconds between one sender's notes
VOICE_PER_HOUR = 30            # per sender
VOICE_PER_RECIPIENT = 10       # voice notes held per inbox (RAM)
VOICE_TOTAL_BYTES = 2000000    # voice audio held across all inboxes (RAM)
_voice_sent = {}               # client_id -> [times]


def opus_ms(data):
    """Length of an Ogg Opus file in ms, or None if it isn't one we accept.
    RFC 7845: pages of the first logical stream only; its first packet must
    be OpusHead with 1 or 2 channels and mapping family 0; length =
    (last granule position - pre-skip) / 48, rounded down -- both in 48 kHz
    samples. Pages whose granule is -1 (no packet ends there) don't count."""
    n, i = len(data), 0
    serial = pre_skip = last = None
    while i < n:
        if i + 27 > n or data[i:i + 4] != b"OggS":
            return None
        nseg = data[i + 26]
        body = i + 27 + nseg
        if body > n:
            return None
        size = 0
        for k in range(i + 27, body):
            size += data[k]
        if body + size > n:
            return None
        sn = int.from_bytes(data[i + 14:i + 18], "little")
        if serial is None:
            serial = sn
        if sn == serial:
            if pre_skip is None:
                head = data[body:body + size]
                if len(head) < 19 or head[:8] != b"OpusHead" or head[9] not in (1, 2) or head[18] != 0:
                    return None
                pre_skip = head[10] | (head[11] << 8)
            else:
                g = int.from_bytes(data[i + 6:i + 14], "little")
                if g != 0xFFFFFFFFFFFFFFFF:
                    last = g
        i = body + size
    if pre_skip is None or last is None or last < pre_skip:
        return None
    return (last - pre_skip) // 48


def voice_ms(mode, audio):
    """A note's length in whole ms, or None if the mode isn't supported or
    the bytes aren't a valid note of that mode."""
    if mode == OPUS_OGG:
        return opus_ms(audio)
    if mode in CODEC2_MODES:
        bpf, frame_ms = CODEC2_MODES[mode]
        return (len(audio) // bpf) * frame_ms
    return None


def voice_label_ms(ms):
    """"♪ 5.0 s": U+266A, a space, the seconds with one decimal and a dot,
    a space, "s". Rounded half up, in integers -- no floats on the node."""
    tenths = (ms + 50) // 100
    return "♪ %d.%d s" % (tenths // 10, tenths % 10)


def send_voice(client_id, to_nick, mode, audio):
    """A voice note from client_id to to_nick. Returns reply lines, the same
    shapes as /msg: "[DM] <you>: ♪ 5.0 s" on success, "⊖ name", the
    rate-limit token, or a sentence."""
    lang = i18n.get_lang(client_id)
    user = touch_user(client_id)
    if not isinstance(audio, (bytes, bytearray)) or not audio or len(audio) > VOICE_MAX_BYTES:
        return [i18n.t("voice_bad", lang)]
    audio = bytes(audio)
    ms = voice_ms(mode, audio)
    if ms is None:
        return [i18n.t("voice_bad", lang)]
    limit = VOICE_MAX_MS_OPUS if mode == OPUS_OGG else VOICE_MAX_MS_CODEC2
    if ms < VOICE_MIN_MS or ms > limit:
        return [i18n.t("voice_length", lang)]
    now = time.time()
    recent = [t for t in _voice_sent.get(client_id, []) if now - t < 3600]
    if (recent and now - recent[-1] < VOICE_MIN_GAP) or len(recent) >= VOICE_PER_HOUR:
        _voice_sent[client_id] = recent
        return [tok_rate(i18n.t("voice_rate", lang))]
    target = (to_nick or "").lstrip("~")
    if target.lower() == user["nick"].lower():
        return [i18n.t("msg_self", lang)]
    label = voice_label_ms(ms)
    ok, info = send_dm(user["nick"], target, label, audio=[mode, audio, ms])
    if not ok:
        return [no_such(target)]
    recent.append(now)
    _voice_sent[client_id] = recent
    return [dm_line(user["nick"], label)]


def dm_public(m):
    """A DM as the web poll sends it: a voice note's audio is replaced by
    {"mode", "bytes", "secs"}; the bytes are fetched from /rrc/voice."""
    if "audio" not in m:
        return m
    out = dict(m)
    mode, audio, ms = out.pop("audio")
    # "ms" is exact; "secs" is the same length as a number, kept for
    # clients that already read it.
    out["voice"] = {"mode": mode, "bytes": len(audio), "ms": ms, "secs": ms / 1000}
    return out


def find_dm(client_id, dm_id):
    for m in _dms.get(client_id, []):
        if m["id"] == dm_id:
            return m
    return None


# Which node this is, for clients that reach it over both Wi-Fi and LoRa
# and need to see it's the same one: {"name": ..., "lxmf": <hex>}. Set by
# example_node once its LXMF destination exists; reported in /rrc/poll.
NODE = {}
NODE_INFO = {}       # version and release, for /node.json (set at boot)
NODE_IDENTITY = [None]   # this Stump's own identity (example_node), for /hello


# The route a person chose in their app (/hello's "prefer"), like the
# Stump's own USB / Bluetooth switch: "wifi" or "lora". LXMF address ->
# choice. With "lora", DMs go over LoRa even while the phone is on this
# Stump's Wi-Fi too (it's still listed once).
_prefer = {}


def set_prefer(lxmf_hex, how):
    if lxmf_hex and how in ("wifi", "lora"):
        _prefer[lxmf_hex] = how
        while len(_prefer) > MAX_SAME:
            _prefer.pop(next(iter(_prefer)))


def prefer_of(lxmf_hex):
    return _prefer.get(lxmf_hex, "wifi")


def names_line(room, names):
    return "#" + room + ": " + (", ".join(names) if names else "—")


def rooms_line(room, count, topic_text):
    return "#" + room + " ·" + str(count) + ("  " + topic_text if topic_text else "")
_next_id = [1]

# client_id -> list of {"id","ts","nick","body","kind"} addressed to
# that client only. Direct messages are kept per RECIPIENT rather than
# in a room, because that is what makes them private: a room is a
# broadcast surface, and anything posted to one reaches every poller
# and (since rrc_mesh) the radio as well.
_dms = {}
# Safety-valve ceiling only, not the primary way a DM disappears --
# that's DM_TTL_SECONDS above, checked first and given priority
# everywhere this cap is also checked. This exists purely to bound RAM
# if something floods a single recipient with more messages than
# DM_TTL_SECONDS would naturally clear -- all arriving within the same
# 72-hour window, which time-based pruning alone can't help with.
# Raised from an earlier, tighter value now that time-based pruning is
# doing the everyday work: normal use across a real 72-hour window,
# from more than one sender, shouldn't run into a count ceiling that
# was originally sized as the ONLY limit.
MAX_DMS_PER_USER = 60

# client_id -> {"nick", "room", "last_seen"}
_users = {}

# Mesh peers heard only by their LXMF announce: reachable by /msg, but
# not in any room -- so never in /names, room counts, or the MAX_USERS
# ceiling (a busy mesh must not crowd real web users out). client_id ->
# {"nick": str}. Maintained entirely by rrc_mesh, which decides who is
# reachable and for how long; this module only resolves nicks against it.
_reachable = {}


def set_reachable(client_id, nick):
    _reachable[client_id] = {"nick": nick}


def drop_reachable(client_id):
    _reachable.pop(client_id, None)


def reachable_nicks():
    return sorted(r["nick"] for cid, r in _reachable.items() if same_web(cid) is None)


# The same person reached two ways: a FireFly that proved its identity
# over Wi-Fi (stumpid's /auth) and is also known by its LXMF address on
# LoRa. LXMF address (hex) -> the Wi-Fi client id. While that Wi-Fi client
# is here, the two are one person: listed once, under the Wi-Fi nick, and
# DMs to that nick reach them over Wi-Fi; once it's gone, over LoRa again.
# Only an /auth links them -- a claim without the identity's signature
# could take someone else's messages.
_same = {}
MAX_SAME = 64


def link_same(web_cid, lxmf_hex):
    _same[lxmf_hex] = web_cid
    while len(_same) > MAX_SAME:
        _same.pop(next(iter(_same)))


def same_web(lxmf_hex):
    """The Wi-Fi client this LXMF address is the same person as, while
    it's here; else None."""
    web = _same.get(lxmf_hex)
    return web if web is not None and web in _users else None


def _same_person(a, b):
    return _same.get(a) == b or _same.get(b) == a


def lxmf_of(web_cid):
    """The LXMF address a Wi-Fi client proved (/auth), or None."""
    for lx, w in _same.items():
        if w == web_cid:
            return lx
    return None


def is_default_nick(nick):
    """A name nobody chose: a web guest's or an unnamed mesh contact's."""
    n = str(nick or "")
    return n.startswith("guest-") or n.startswith("mesh-")


def nick_chosen(client_id):
    u = _users.get(client_id)
    return u is not None and not is_default_nick(u["nick"])


# Apps (clients without the Stump's cookie) that haven't greeted yet --
# /auth, then /nick (barkeep's gate): not listed anywhere, so a half-
# connected app is never a stray "guest-xxxx" in the rooms.
_hidden = set()


def hide(client_id):
    _hidden.add(client_id)


def unhide(client_id):
    _hidden.discard(client_id)


def _is_lxmf(cid):
    s = str(cid)
    return len(s) == 32 and all(c in "0123456789abcdef" for c in s)


def local_identity(id8):
    """Is the person with this identity (the first 8 hex of their LXMF
    address) here, on this Stump -- on LoRa, or on Wi-Fi after /auth?"""
    if not id8:
        return False
    for cid in list(_users) + list(_reachable):
        if cid not in _hidden and _is_lxmf(cid) and cid.startswith(id8):
            return True
    for lx, web in _same.items():
        if lx.startswith(id8) and web in _users and web not in _hidden:
            return True
    return False


def identity_of(client_id):
    """The identity (8 hex) a client here is known by, or None."""
    if _is_lxmf(client_id):
        return client_id[:8]
    lx = lxmf_of(client_id)
    return lx[:8] if lx else None


# Renames others should follow (a LoRa contact's new announced name, or
# the LoRa name giving way to the Wi-Fi one): the web chat folds a thread
# under the old name into the new one. [(time, old, new)], kept RENAME_S.
_renames = []
RENAME_S = 1800


def note_rename(old, new):
    if not old or not new or old == new:
        return
    now = time.time()
    _renames[:] = [r for r in _renames if now - r[0] < RENAME_S and r[1] != old][-31:]
    _renames.append((now, old, new))


def renames():
    """{old nick: new nick} for recent renames (chains followed: a -> b -> c
    gives a: c)."""
    now = time.time()
    out = {}
    for t, old, new in _renames:
        if now - t < RENAME_S:
            for k in out:
                if out[k] == old:
                    out[k] = new
            out[old] = new
    out = dict((k, v) for k, v in out.items() if k != v)
    return out


# Client ids (LXMF delivery hashes, hex) known to be other Stump nodes,
# from their "stump.node" beacons. Resolved to nicks only when asked,
# so it doesn't matter whether the beacon or the LXMF announce arrived
# first, or whether they've changed nick since. Bounded: a type flag
# is tiny, but nothing here grows without limit.
_stumps = set()
MAX_STUMPS = 64


def is_stump(client_id):
    return client_id in _stumps


def mark_stump(client_id):
    if client_id in _stumps:
        return
    if len(_stumps) >= MAX_STUMPS:
        _stumps.pop()
    _stumps.add(client_id)


def stump_nicks():
    out = []
    for cid in _stumps:
        u = _users.get(cid) or _reachable.get(cid)
        if u is not None:
            out.append(u["nick"])
    return sorted(out)


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
    return canon(slug.strip("-"))


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


def drop_user(client_id):
    """Removes a client from the room roster immediately. Used by
    rrc_mesh when a mesh peer leaves, so /names stops listing someone
    the room was just told had left."""
    _users.pop(client_id, None)


def get_user(client_id):
    return _users.get(client_id) or touch_user(client_id)


def nick_taken(nick, by_client):
    """Prevents two people appearing under the same name. Case
    insensitive, because 'Bob' and 'bob' reading as different people in
    a chat window is a genuine source of confusion."""
    low = nick.lower()
    # A registered profile's nick (profiles.py) is its owner's alone: they
    # are "p:<nick>" while logged in.
    if by_client != "p:" + low:
        try:
            import profiles
            if profiles.is_registered(low):
                return True
        except ImportError:
            pass
    for table in (_users, _reachable):
        for cid, u in table.items():
            if cid != by_client and u["nick"].lower() == low and not _same_person(cid, by_client):
                return True
    return False


def release_registered_nicks():
    """A registered profile's nick (profiles.py: made here, or arrived from
    another Stump of the chain) held by someone else: they go back to
    their default name, the room told. Returns how many."""
    try:
        import profiles
    except ImportError:
        return 0
    n = 0
    for cid, u in list(_users.items()):
        low = u["nick"].lower()
        if cid != "p:" + low and not str(cid).startswith("p:") and profiles.is_registered(low):
            old, new = u["nick"], default_nick(cid)
            u["nick"] = new
            system(u["room"], ev_rename(old, new))
            n += 1
    return n


def users_in_room(room):
    _prune_users()
    room = canon(room)
    return sorted(set(u["nick"] for cid, u in _users.items()
                      if u["room"] == room and same_web(cid) is None and cid not in _hidden))


# Who is in a shared room on the OTHER Stumps (#lora-mesh), as each told
# this one (rrc_mesh: a "[NAMES] ..." line): stump id -> {"who", "rooms":
# {room: [nicks]}, "at"}. Kept REMOTE_NAMES_S after the last word.
_remote_names = {}
_remote_seq = [0]          # (which list came last, even within a second)
REMOTE_NAMES_S = 900
MAX_REMOTE_STUMPS = 16


def set_remote_names(stump_id, who, room, nicks):
    room = canon(room)
    e = _remote_names.get(stump_id)
    if e is None:
        if len(_remote_names) >= MAX_REMOTE_STUMPS:
            _remote_names.pop(min(_remote_names, key=lambda k: _remote_names[k]["at"]))
        e = _remote_names[stump_id] = {"who": who, "rooms": {}, "at": 0}
    _remote_seq[0] += 1
    e["who"], e["at"], e["seq"] = who, time.time(), _remote_seq[0]
    # Each one a nick, or (nick, identity): the identity (8 hex) lets this
    # Stump see it's someone who is here too, and not list them twice.
    e["rooms"][room] = [n if isinstance(n, tuple) else (n, None) for n in nicks if n][:60]


def remote_names(room):
    """"nick@stump" for everyone the other Stumps say is in this room."""
    room = canon(room)
    now = time.time()
    out, seen = [], set()
    # Newest first: someone on two other Stumps is listed once, where they
    # were heard last; someone who is here too, not at all (they're here).
    for k in sorted(_remote_names, key=lambda k: -_remote_names[k].get("seq", 0)):
        e = _remote_names[k]
        if now - e["at"] > REMOTE_NAMES_S:
            _remote_names.pop(k)
            continue
        for n, ident in e["rooms"].get(room, []):
            if ident and (ident in seen or local_identity(ident)):
                continue
            if ident:
                seen.add(ident)
            out.append(n + "@" + e["who"])
    return out


def names_in_room(room):
    """Who's in a room: here, then on the other Stumps (nick@stump)."""
    return users_in_room(room) + sorted(remote_names(room))


# ---------------------------------------------------------------------
# Rooms and messages
# ---------------------------------------------------------------------

def room_names():
    """'main' always first, the rest alphabetical -- a stable order, so
    the room list doesn't reshuffle under someone mid-click."""
    others = sorted(r for r in _rooms if r != DEFAULT_ROOM)
    return [DEFAULT_ROOM] + others


def room_exists(room):
    return canon(room) in _rooms


def topic(room, lang=None):
    room = canon(room)
    if room in _topics:
        return _topics[room]
    key = _DEFAULT_TOPIC_KEYS.get(room)
    return i18n.t(key, lang or i18n.DEFAULT_LANG) if key else ""


def set_topic(room, text):
    room = canon(room)
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


def post(room, nick, body, kind="msg", via=None):
    """Adds a line to a room. Returns the message, or None if rejected.

    Oldest messages are dropped past MAX_MESSAGES_PER_ROOM rather than
    refusing new ones -- a room that stops accepting messages when it
    fills up would be broken; one that forgets its oldest lines is just
    a scrollback limit, which is what every chat client has anyway."""
    room = canon(room)
    if room not in _rooms:
        return None
    body = _clean(body, MAX_MESSAGE_LEN)
    if not body:
        return None
    msg = {"id": _next_id[0], "ts": time.time(), "nick": nick,
           "body": body, "kind": kind}
    if via:
        # Relayed from another Stump of the chain (rrc_mesh): never sent
        # on to other Stumps, which hear the original themselves.
        msg["via"] = via
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
    room = canon(room)
    if room not in _rooms:
        return []
    return [m for m in _rooms[room] if m["id"] > last_id]


def find_client_by_nick(nick):
    """Resolves a nick to the client it belongs to. Case-insensitive,
    because 'Bob' and 'bob' being different people is the kind of
    confusion that loses a private message to the wrong person."""
    # "~" marks a mesh user in room notices ("✓ ~rod") and can't be part
    # of a nick (clean_nick doesn't allow it), so "/msg ~rod" copied from
    # a notice still reaches rod.
    low = (nick or "").lstrip("~").lower()
    for table in (_users, _reachable):
        for cid, u in table.items():
            if u["nick"].lower() == low and cid not in _hidden:
                lx = cid if _is_lxmf(cid) else lxmf_of(cid)
                if lx and _prefer.get(lx) == "lora" and (lx in _reachable or lx in _users):
                    return lx                    # they chose LoRa: reached there
                return same_web(cid) or cid      # (on Wi-Fi now: reached there)
    return None


DM_TTL_SECONDS = 72 * 3600   # DMs are kept up to 72 hours since receipt --
                             # in-memory only, same as everything else in
                             # this module: a power cycle clears _dms
                             # (see reset() below) exactly like it clears
                             # _rooms, there is no SD-card path for this
                             # data at all. This constant is the ONLY
                             # thing that removes a message before that.


def _prune_dms(cid, now=None):
    """Drops DMs in this recipient's box older than DM_TTL_SECONDS --
    the PRIMARY retention rule, checked first and given priority over
    the count-based ceiling below: a message inside its 72-hour window
    is not evicted just because a burst of other messages arrived after
    it, the way the file-storage FIFO would evict an old upload to make
    room for a new one. Time decides what goes; count is only a
    last-resort safety valve for the case time-based pruning alone
    doesn't bound (a flood of messages all arriving within the same
    72 hours), not the everyday mechanism -- MAX_DMS_PER_USER exists for
    exactly that narrower case, not as the normal way DMs disappear.
    """
    box = _dms.get(cid)
    if not box:
        return
    now = now if now is not None else time.time()
    box[:] = [m for m in box if now - m["ts"] <= DM_TTL_SECONDS]


def send_dm(from_nick, to_nick, body, audio=None):
    """Queues a private message. Returns (ok, error_or_recipient_nick).

    Delivered by polling, same as room messages -- the recipient picks
    it up on their next cycle. Bounded per recipient as a safety valve
    only (see MAX_DMS_PER_USER and _prune_dms's own docstring for why
    that's now secondary to the 72-hour rule): someone who never comes
    back must not accumulate messages forever on a board with finite
    RAM, so the oldest are dropped rather than new ones refused, but
    only once time-based pruning alone hasn't kept the count down.
    """
    body = _clean(body, MAX_MESSAGE_LEN)
    if not body:
        return False, "nothing to send"
    cid = find_client_by_nick(to_nick)
    if cid is None:
        return False, "no one here called '%s' -- /names shows who is" % to_nick
    user = _users.get(cid) or _reachable.get(cid)
    now = time.time()
    _prune_dms(cid, now)
    msg = {"id": _next_id[0], "ts": now, "nick": from_nick,
           "body": body, "kind": "dm"}
    if audio is not None:
        msg["audio"] = audio
    _next_id[0] += 1
    box = _dms.setdefault(cid, [])
    box.append(msg)
    if audio is not None:
        # Audio is the heavy part: the newest few per inbox, and a cap on
        # all of it together (an Opus note can be ~15 KB).
        voices = [m for m in box if "audio" in m]
        for old in voices[:-VOICE_PER_RECIPIENT]:
            box.remove(old)
        held = [(m["id"], c) for c, b in _dms.items() for m in b if "audio" in m]
        total = sum(len(m["audio"][1]) for b in _dms.values() for m in b if "audio" in m)
        for mid, c in sorted(held):
            if total <= VOICE_TOTAL_BYTES:
                break
            for m in _dms[c]:
                if m["id"] == mid:
                    total -= len(m["audio"][1])
                    _dms[c].remove(m)
                    break
    if len(box) > MAX_DMS_PER_USER:
        del box[:len(box) - MAX_DMS_PER_USER]
    return True, user["nick"]


def dms_since(client_id, last_id):
    """Private messages for this client newer than last_id.

    Also prunes this client's own box on the way -- polling is the one
    thing every connected client does regularly regardless of whether
    anyone is messaging them, so hooking the 72-hour cleanup in here
    too (not just in send_dm) means a recipient who stops receiving new
    DMs still gets their own expired ones cleared out on their next
    poll, rather than that box sitting there until someone happens to
    message them again -- which might be never.
    """
    _prune_dms(client_id)
    return [m for m in _dms.get(client_id, []) if m["id"] > last_id]


def reset():
    """Clears everything. Used by tests; also the honest answer to 'how
    do I clear the chat' -- there is no persistence to clear."""
    _rooms.clear()
    _rooms[DEFAULT_ROOM] = []
    _rooms[MESH_ROOM] = []
    _topics.clear()
    _users.clear()
    _reachable.clear()
    _same.clear()
    _hidden.clear()
    _prefer.clear()
    del _renames[:]
    _stumps.clear()
    _dms.clear()
    _lora_invited.clear()
    LORA_LOCKED[0] = False
    _next_id[0] = 1


# ---------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------

def help_lines(lang, mesh=False):
    """The /help listing, in the requesting client's language. Command
    WORDS and their column alignment stay fixed across all languages
    (a consistent vocabulary, same reasoning as barkeep.py's bot
    console) -- only the description after each one is translated, and
    the argument names (<name> is <nom> in French): the column stays
    aligned whatever their length."""
    def a(key):
        return i18n.t("help_arg_" + key, lang)

    def line(cmd, desc):
        return cmd + " " * max(1, 18 - len(cmd)) + i18n.t(desc, lang)
    return [
        line("/nick " + a("name"), "help_nick"),
        line("/join " + a("room"), "help_join"),
        line("/part", "help_part"),
        line("/rooms", "help_rooms"),
        line("/names", "help_names"),
        line("/topic " + a("text"), "help_topic"),
        line("/msg " + a("who") + " " + a("text"), "help_msg"),
        line("/me " + a("action"), "help_me"),
        line("/clear", "help_clear"),
        line("/help", "help_help"),
    ] + plugins.help_lines(lang, mesh) + [
        i18n.t("help_legend", lang),
    ]


try:
    from config import CHAT_EVERY        # seconds per line, once the burst is spent
except ImportError:
    CHAT_EVERY = 2
try:
    from config import CHAT_BURST        # lines at once
except ImportError:
    CHAT_BURST = 3
_SPEAKING_COMMANDS = ("msg", "m", "w", "me", "topic")


def _speaks(text):
    """True for a line other people will see: a message, or /msg, /me, /topic."""
    if not text.startswith("/"):
        return True
    return text[1:].split(" ", 1)[0].lower() in _SPEAKING_COMMANDS


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
    # Who's asking, for password checks inside commands (guard.py), and
    # a speed limit on lines that reach other people: a few at once, then
    # one every CHAT_EVERY seconds -- on the web and over the mesh alike,
    # where every line costs LoRa airtime. Commands that only answer the
    # sender (/help, /names...) aren't counted.
    guard.caller(client_id)
    if _speaks(text) and not guard.allow("chat", client_id, CHAT_EVERY, CHAT_BURST):
        # Said in the log, so a "too fast" nobody expected can be traced
        # (who, which line).
        print("[rrc] too fast: %s %r" % (str(client_id)[:12], text[:40]))
        return [tok_rate(i18n.t("chat_rate", lang))], None

    # Plugins (plugins.py): gates and /commands see the line before rrc
    # does; on_result hooks see what came of it.
    nick = user["nick"]
    handled = plugins.before(client_id, room, nick, text, lang)
    if handled is not None:
        kind, result = handled
        if kind == "gate":
            return result
    else:
        result = _handle_core(client_id, room, text, user, lang)
    return plugins.finish(client_id, room, nick, text, lang, result)


def _handle_core(client_id, room, text, user, lang):
    if not text.startswith("/"):
        touch_user(client_id, room=room)
        posted = post(room, user["nick"], text)
        if posted is None:
            return [i18n.t("room_gone", lang)], None
        # Posted: now !triggers and plugin listeners.
        return plugins.after(client_id, room, user["nick"], text, lang), None

    parts = text[1:].split(" ", 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "help":
        return help_lines(lang), None

    if cmd == "nick":
        new = clean_nick(arg)
        if not new:
            return [i18n.t("nick_usage", lang)], None
        if new.lower() == user["nick"].lower() and new == user["nick"]:
            return [i18n.t("nick_already", lang)], None
        if str(client_id).startswith("p:") and new.lower() != client_id[2:]:
            # Logged into a registered profile: its nick is the profile's.
            return [i18n.t("nick_profile", lang, nick=user["nick"])], None
        if nick_taken(new, client_id):
            return [i18n.t("nick_taken", lang, nick=new)], None
        old = user["nick"]
        touch_user(client_id, nick=new, room=room)
        if client_id not in _hidden:          # (an app's greeting isn't news)
            system(room, ev_rename(old, new))
            note_rename(old, new)
        lx = lxmf_of(client_id)
        if lx:
            # Its LoRa side too, now (not at the mesh loop's next turn).
            try:
                import rrc_mesh
                rrc_mesh._one_name(lx, client_id)
            except Exception:
                pass
        return [], None

    if cmd in ("join", "j"):
        if not arg:
            return [i18n.t("join_usage", lang)], None
        target, err = create_room(arg)
        if err:
            return [err], None
        if target == room:
            return [tok_already(target, i18n.t("join_already", lang, room=target))], None
        if not room_allows(client_id, target):
            return [tok_refused(target, "LoRa", i18n.t("lora_room_locked", lang))], None
        system(room, ev_leave(user["nick"]))
        touch_user(client_id, room=target)
        system(target, ev_join(user["nick"]))
        return [], target

    if cmd == "part":
        if room == DEFAULT_ROOM:
            return [tok_already(DEFAULT_ROOM, i18n.t("part_in_main", lang))], None
        system(room, ev_leave(user["nick"]))
        touch_user(client_id, room=DEFAULT_ROOM)
        system(DEFAULT_ROOM, ev_join(user["nick"]))
        return [], DEFAULT_ROOM

    if cmd == "rooms":
        return [rooms_line(r, len(names_in_room(r)), topic(r, lang))
                + ("  [LoRa]" if r == MESH_ROOM and LORA_LOCKED[0] else "") for r in room_names()], None

    if cmd == "lora":
        # /lora, /lora invite <nick>, /lora uninvite <nick>: who may use the
        # radio's room from the web. Only the computer's owner decides.
        sub, _, who = arg.partition(" ")
        if not LORA_LOCKED[0]:
            return [i18n.t("lora_room_open", lang)], None
        if sub not in ("invite", "uninvite") or not who.strip():
            names = sorted(u["nick"] for c, u in _users.items() if c in _lora_invited)
            return [i18n.t("lora_room_status", lang, names=", ".join(names) or "-")], None
        if not is_local(client_id):
            return [i18n.t("lora_room_owner_only", lang)], None
        target = find_client_by_nick(who.strip())
        if target is None:
            return [no_such(who.strip())], None
        if sub == "invite":
            _lora_invited.add(target)
        else:
            _lora_invited.discard(target)
            u = _users.get(target)
            if u and u.get("room") == MESH_ROOM:
                system(MESH_ROOM, ev_leave(u["nick"]))
                touch_user(target, room=DEFAULT_ROOM)
        return [i18n.t("lora_room_" + sub + "d", lang, nick=who.strip())], None

    if cmd == "names":
        return [names_line(room, names_in_room(room))], None

    if cmd == "topic":
        if not arg:
            t = topic(room, lang) or i18n.t("topic_none", lang)
            return [i18n.t("topic_show", lang, room=room, topic=t)], None
        set_topic(room, arg)
        system(room, ev_topic(room, topic(room)))
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
                return [no_such(target)], None
            return [info], None
        # Confirmed to the sender only. Nothing is posted to the room --
        # that is the whole point, and it also keeps private traffic off
        # the radio, since rrc_mesh forwards room messages but not these.
        return [dm_line(user["nick"], body)], None

    if cmd == "me":
        if not arg:
            return [i18n.t("me_usage", lang)], None
        touch_user(client_id, room=room)
        post(room, user["nick"], arg, kind="action")
        return [], None

    if cmd == "clear":
        return ["__CLEAR__"], None

    return [tok_unknown(cmd, i18n.t("unknown_command", lang, cmd=cmd))], None
