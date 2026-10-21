# Project Stump -- stumpid activation
# Place at: firmware/stumpid/install.py
#
# Registers with the plugin API (plugins.py, docs/PLUGINS.md):
#
#   gate, priority 10     access control: room tiers on /join, and the
#                         node's AUTH_MODE on everything else. Priority
#                         0-19 is reserved for this plugin, so no other
#                         gate can run before it.
#   /auth /whoami         identity proof
#   /admin /invite        (names reserved for stumpid in the core)
#   on_result             tier labels on /rooms; keeps a verified
#                         identity's saved nick in step with /nick
#   service "identity"    this plugin's core module, for the parts of
#                         the node that ask about access (the mesh
#                         bridge, voice notes, the admin password) --
#                         so they no longer import stumpid by name.
#
# Before the plugin API this wrapped rrc.handle_input and had to be the
# OUTERMOST wrapper, which it was only because "stumpid" sorts after
# "fservbot". The priority makes that explicit.

import rrc
import i18n
from stumpid import core

VERSION = "1.1"
STUMP_API = 1

_api = []      # the plugin API object, once setup() has run


def _t(key, lang, **kw):
    """This plugin's strings (strings.py), then the core's."""
    if _api:
        return _api[0].t(key, lang, **kw)
    return i18n.t(key, lang, **kw)


def setup(stump):
    _api.append(stump)
    core.STORAGE_FILE = stump.storage_file("stumpid.json", legacy=core.STORAGE_FILE)
    core.STATE_FILE = stump.storage_file("access.json")
    if core.load_state():
        print("[stumpid] access settings restored: mode=%s, %d tiered room(s)"
              % (core.AUTH_MODE, len(core._room_policy)))
        for room in core._room_policy:
            rrc.create_room(room)
    # First, so the admin password check exists even if something below fails.
    stump.provide("identity", core)
    stump.gate(_gate, priority=10)
    # trigger=False: never answered as "!admin ..." -- a !line is posted
    # to the room, password included. limit=False: a client answers the
    # /auth challenge straight away. mesh_lines=0: replies as before.
    for word, fn in (("auth", _on_auth), ("whoami", _on_whoami),
                     ("admin", _on_admin), ("invite", _on_invite)):
        stump.command(word, fn, trigger=False, limit=False, mesh_lines=0)
    stump.on_result(_after, priority=10)
    print("[stumpid] active: mode=%s" % core.AUTH_MODE)
    if not core.admin_available():
        print("[stumpid] no admin password set (own or fservbot's) -- /admin is disabled")


def _on_auth(ctx, arg):
    return _cmd_auth(ctx.client_id, ctx.text.strip())


def _on_whoami(ctx, arg):
    if arg:
        # "/whoami x" was never a command: answered as rrc would.
        return [rrc.tok_unknown("whoami", i18n.t("unknown_command", ctx.lang, cmd="whoami"))]
    return _cmd_whoami(ctx.client_id)


def _on_admin(ctx, arg):
    return _cmd_admin(ctx.client_id, ctx.text.strip())


def _on_invite(ctx, arg):
    return _cmd_invite(ctx.client_id, ctx.text.strip(), ctx.room)


def _gate(ctx):
    stripped = ctx.text.strip()
    low = stripped.lower()
    client_id = ctx.client_id

    # Our own commands are always allowed through: they are how someone
    # verifies in the first place, and /admin carries its own password.
    if ctx.command in ("auth", "whoami", "admin", "invite") and stripped.startswith("/"):
        return None

    if low.startswith("/join ") or low.startswith("/j "):
        # Room-tier gate, checked BEFORE rrc.create_room() -- a blocked
        # attempt never creates or enters the room at all. Unconditional,
        # not skipped under global 'open': a room's tier is an ADDITIONAL
        # restriction on top of AUTH_MODE, never a substitute for it.
        arg = stripped.split(" ", 1)[1].strip() if " " in stripped else ""
        if arg:
            target_room = rrc.clean_room(arg)
            ok, reason_code = core.can_join_room(client_id, target_room)
            if not ok:
                key = "room_needs_verified" if reason_code == "needs_verified" else "room_invite_only"
                tier = core.room_tier(target_room)
                return [rrc.tok_refused(target_room, core.AUTH_MODE if tier == "open" else tier, i18n.t(key, ctx.lang))]

    # Everything else is subject to the node's AUTH_MODE. A blocked line
    # never reaches another plugin or rrc, so a gated node can't be
    # written to by any path that funnels through handle_input.
    if core.requires_verification(client_id, stripped, getattr(ctx, "room", None)):
        return [i18n.t("auth_required_generic", ctx.lang)]
    return None


def _after(ctx, replies, new_room):
    # Not after our own commands: they never reached rrc before either,
    # so the saved nick catches up on the next line, exactly as it did.
    if ctx.command in ("auth", "whoami", "admin", "invite") and ctx.text.startswith("/"):
        return None
    out = None
    # Annotate /rooms with each room's tier, by POSITION rather than by
    # parsing rrc's reply text: rrc.room_names() is what rrc's own /rooms
    # iterates, in the same order.
    if ctx.text.strip().lower() == "/rooms" and replies:
        names = rrc.room_names()
        if len(replies) == len(names):
            annotated = []
            for line, name in zip(replies, names):
                tier = core.room_tier(name)
                annotated.append(line if tier == "open" else line + "  [%s]" % tier)
            out = (annotated, new_room)

    # A verified session's saved nick follows whatever rrc settled on --
    # read back AFTER rrc handled the line, so a rejected /nick changes
    # nothing. Never rrc's own placeholder (default_nick, "guest-XXXX").
    hexhash = core.verified_identity_for(ctx.client_id)
    if hexhash is not None:
        user = rrc.get_user(ctx.client_id)
        nick = user.get("nick") if user else None
        if nick and nick != rrc.default_nick(ctx.client_id):
            core.set_nick_for(hexhash, nick)
    return out


# ---------------------------------------------------------------------
# /auth                          -- step 1, request a challenge
# /auth <pubkey_hex> <sig_hex>   -- step 2, answer it
#
# The hash a client ends up verified as is never something they assert;
# it's computed server-side from the public key that just proved
# possession of the matching private key (see core.complete_challenge).
# One field simpler than asking the client to also state a hash the
# server could derive itself -- confirmed that's genuinely redundant,
# not just shorter.
# ---------------------------------------------------------------------

def _cmd_auth(client_id, stripped):
    parts = stripped.split(" ")
    args = parts[1:]

    if len(args) == 0:
        nonce_hex = core.start_challenge(client_id)
        return ["AUTH-CHALLENGE " + nonce_hex]

    if len(args) == 2:
        pubkey_hex, sig_hex = args
        ok, result = core.complete_challenge(client_id, pubkey_hex, sig_hex)
        if not ok:
            return ["AUTH-FAIL " + result]
        core.mark_verified(client_id, result)
        if not core._is_mesh_client(client_id):
            # Proved over Wi-Fi: if this identity is known on LoRa too,
            # it's one person now (rrc_mesh.same_person).
            try:
                import rrc_mesh
                rrc_mesh.same_person(client_id, result)
            except Exception as e:
                print("[stumpid] couldn't link the LoRa identity:", e)
        return ["AUTH-OK " + result]

    return ["usage: /auth   (then)   /auth <pubkey_hex> <signature_hex>"]


def _cmd_whoami(client_id):
    hexhash = core.verified_identity_for(client_id)
    if not hexhash:
        return ["not verified this session -- /auth to prove an identity"]
    info = core.identity_info(hexhash)
    nick = (info or {}).get("nick") or "(no nick set)"
    return ["verified as " + hexhash + "  nick=" + nick]


# ---------------------------------------------------------------------
# /admin <password> mode <open|hybrid|mandatory>
# /admin <password> revoke <hexhash>
# ---------------------------------------------------------------------

def enforce_mode():
    """The node's mode just changed: whoever is in a room they may no
    longer be in (mandatory: the unverified, anywhere but #main) goes back
    to #main, told why. Returns their nicks."""
    moved = []
    for room in list(rrc.room_names()):
        if room != rrc.DEFAULT_ROOM:
            moved += _enforce_room(room, core.AUTH_MODE)
    return moved


def _enforce_room(room, tier):
    """A room just became stricter: whoever is in it and no longer
    qualifies goes back to #main, told why -- the same refusal a /join
    would get. Returns their nicks."""
    home = rrc.DEFAULT_ROOM
    if room == home:
        return []
    try:
        import rrc_mesh
        mesh = rrc_mesh._peers
    except ImportError:
        rrc_mesh, mesh = None, {}
    moved = []
    for cid, u in list(rrc._users.items()):
        if u.get("room") != room:
            continue
        ok, reason = core.can_join_room(cid, room)
        if ok:
            continue
        lang = i18n.get_lang(cid)
        key = "room_needs_verified" if reason == "needs_verified" else "room_invite_only"
        notice = rrc.tok_refused(room, tier, i18n.t(key, lang))
        on_mesh = cid in mesh
        shown = ("~" if on_mesh else "") + u["nick"]
        rrc.system(room, rrc.ev_leave(shown))
        rrc.touch_user(cid, room=home)
        rrc.system(home, rrc.ev_join(shown))
        if on_mesh:
            mesh[cid]["room"] = home
            rrc_mesh.notify(cid, rrc.moved(home) + "\n" + notice)
        else:
            rrc.send_dm("*", u["nick"], notice)
        moved.append(shown)
    return moved


def _cmd_admin(client_id, stripped):
    parts = stripped.split(" ")
    args = parts[1:]

    import guard
    ok, wait = (False, 0) if len(args) < 2 else guard.password(lambda: core.check_admin_password(args[0]), client_id)
    if wait:
        import i18n as _i18n, rrc as _rrc
        return [_rrc.tok_rate(_i18n.t("admin_locked", _i18n.get_lang(client_id), s=wait))]
    if not ok:
        # One ambiguous message for both "wrong password" and "no
        # password configured anywhere" -- matching fservbot's own
        # denial wording. Distinguishing the two would tell an anonymous
        # prompter whether this node currently has ANY admin protection
        # at all, which is a real (if minor) thing not to hand out for
        # free just because someone typed /admin.
        return ["not authorised -- or no admin password is configured yet, "
                "in which case /admin is off until a technician sets one."]

    sub = args[1].lower()
    rest = args[2:]

    if sub == "mode":
        if not rest:
            return ["current mode: " + core.AUTH_MODE]
        if core.set_mode(rest[0]):
            enforce_mode()
            return ["mode set to " + rest[0]]
        return ["unknown mode -- use open, hybrid, or mandatory"]

    if sub == "meshroom":
        # The room itself is fixed (rrc.MESH_ROOM, "#lxmf") -- always
        # present, not something this command creates or names any
        # more. What's left worth surfacing here is just its current
        # tier, since that's the one thing an operator can still change
        # about it, via the existing, unmodified /admin room command.
        tier = core.room_tier(rrc.MESH_ROOM)
        if tier == "open":
            status = "open to everyone (default)"
        else:
            status = "tiered '" + tier + "'"
        return ["#" + rrc.MESH_ROOM + " is always available, mesh peers land there "
                "by default -- currently " + status + ". Change the tier with "
                "/admin <password> room " + rrc.MESH_ROOM + " <open|minted|hybrid>."]

    if sub == "revoke":
        if not rest:
            return ["usage: /admin <password> revoke <hexhash>"]
        return ["revoked" if core.revoke(rest[0]) else "no such identity"]

    if sub == "room":
        # /admin <pw> room <name> <open|minted|hybrid>
        if len(rest) < 2:
            return ["usage: /admin <password> room <name> <open|minted|hybrid>"]
        target_room = rrc.clean_room(rest[0])
        tier = rest[1].lower()
        if not target_room:
            return ["room names need at least one letter or number"]
        if tier not in core.VALID_TIERS:
            return ["tier must be open, minted, or hybrid"]
        if not core.set_room_tier(target_room, tier):
            return ["could not set that tier"]
        moved = []
        if tier != "open":
            # Admin-created, so it exists right away rather than only
            # coming into being the first time someone already minted
            # happens to join it -- otherwise an unminted visitor could
            # never even SEE a freshly-restricted room in /rooms until
            # somebody qualified got there first.
            new_room, err = rrc.create_room(target_room)
            if err:
                return [err]
            moved = _enforce_room(target_room, tier)
        return ["#%s is now %s" % (target_room, tier)] + (
            ["moved to #%s: %s" % (rrc.DEFAULT_ROOM, ", ".join(moved))] if moved else [])

    if sub == "reset-access":
        # Back to config.py: its mode, no room tiers, the saved settings gone.
        core.reset_access()
        return ["access back to config.py: mode " + core.AUTH_MODE + ", no room tiers"]

    if sub == "plugin":
        # /admin <pw> plugin [on|off <name>] -- the plugin off switch
        # (plugins.py), here because this is where /admin's password lives.
        try:
            import plugins
            return plugins.admin_chat(rest, i18n.get_lang(client_id))
        except ImportError:
            pass

    return ["unknown admin command -- try mode, meshroom, revoke, room, or reset-access"]


# ---------------------------------------------------------------------
# /invite <nick> <room>
#
# Not password-gated -- gated by "you are minted, and you are currently
# IN this room" instead. That's a deliberately different axis from
# /admin: admin controls the node's structure (which rooms exist, what
# tier they carry); invites are ordinary participation, something any
# verified person already inside a hybrid room can extend without
# needing the technician's password. Membership control requires
# presence, on purpose -- inviting people into a room you have no
# connection to isn't something this exposes.
# ---------------------------------------------------------------------

def _cmd_invite(client_id, stripped, current_room):
    lang = i18n.get_lang(client_id)
    parts = stripped.split(" ")
    args = parts[1:]
    if len(args) < 1:
        return [_t("invite_usage", lang)]

    target_nick = args[0]
    target_room = rrc.clean_room(args[1]) if len(args) > 1 else current_room

    my_hash = core.verified_identity_for(client_id)
    if my_hash is None:
        return [_t("invite_need_verified", lang)]

    if target_room != current_room:
        return [_t("invite_wrong_room", lang)]

    # Checked before resolving the nick: if the room isn't invite-gated
    # at all, that's the useful thing to say regardless of whether the
    # named person even exists -- "not needed here" beats "no one by
    # that name" when the real answer is that neither matters.
    if core.room_tier(target_room) != "hybrid":
        return [_t("invite_not_gated", lang)]

    target_cid = rrc.find_client_by_nick(target_nick)
    if target_cid is None:
        return [_t("invite_no_such_user", lang, nick=target_nick)]

    ok, reason_code = core.invite_to_room(target_room, target_cid)
    if not ok:
        # core.invite_to_room only ever has the INVITED person's
        # client_id in scope, not this inviter's -- so it can't safely
        # translate for itself (see its own docstring). It returns a
        # plain marker instead; client_id here is genuinely the right
        # one to translate against, since this IS the person reading
        # the reply.
        return [_t("invite_not_gated", lang)]
    return [_t("invite_done", lang, nick=target_nick, room=target_room)]
