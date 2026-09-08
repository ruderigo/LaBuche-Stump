# Project Stump -- stumpid activation
# Place at: firmware/stumpid/install.py
#
# Wraps rrc.handle_input exactly the way fservbot does: capture whatever
# is CURRENTLY bound to it, replace it, delegate to the captured value
# for anything this plugin doesn't own itself. Two plugins doing this
# compose correctly regardless of which activates first -- each only
# ever sees "the handler as it stood a moment ago", never a hardcoded
# assumption about what that is.
#
# ORDERING NOTE, stated plainly because it matters here in a way it
# didn't for fservbot: stumpid needs to be the OUTERMOST wrapper, since
# gating a write means intercepting it before anything else acts on it.
# load_plugins() activates folders in alphabetical order, and
# "stumpid" > "fservbot", so stumpid activates second and wraps outer.
# That is correct today by virtue of the name, not by any enforced
# priority -- worth knowing if a plugin sorting alphabetically ahead of
# "stumpid" is ever added and also wants to gate writes.

import rrc
import i18n
from stumpid import core

_original = None
_installed_as = None


def is_active():
    return _original is not None


def activate():
    global _original, _installed_as
    if _original is not None:
        return False
    _original = rrc.handle_input
    rrc.handle_input = _dispatch
    _installed_as = _dispatch
    print("[stumpid] active: mode=%s" % core.AUTH_MODE)
    if not core.admin_available():
        print("[stumpid] no admin password set (own or fservbot's) -- /admin is disabled")
    return True


def deactivate():
    """Restores the original handler. Only does so if rrc.handle_input
    is STILL exactly what activate() put there -- if something else
    (a plugin activated after this one) has wrapped it since, this is
    no longer the outermost layer, and blindly overwriting
    rrc.handle_input would silently discard whatever wrapped on top.
    Confirmed this corruption is real: deactivating two stacked plugins
    in activation order rather than the reverse produced a
    self-referential chain and blew MicroPython's recursion limit.
    Refuses with a clear reason instead; deactivate the outer plugin
    first."""
    global _original, _installed_as
    if _original is None:
        return False
    if rrc.handle_input is not _installed_as:
        print("[stumpid] cannot deactivate cleanly -- something else has "
              "wrapped rrc.handle_input since this plugin activated. "
              "Deactivate that plugin first.")
        return False
    rrc.handle_input = _original
    _original = None
    _installed_as = None
    print("[stumpid] deactivated")
    return True


def _dispatch(client_id, room, text):
    stripped = text.strip()
    low = stripped.lower()

    if low == "/auth" or low.startswith("/auth "):
        return _cmd_auth(client_id, stripped), None

    if low == "/whoami":
        return _cmd_whoami(client_id), None

    if low.startswith("/admin"):
        return _cmd_admin(client_id, stripped), None

    if low == "/invite" or low.startswith("/invite "):
        return _cmd_invite(client_id, stripped, room), None

    if low.startswith("/join ") or low.startswith("/j "):
        # Room-tier gate, checked BEFORE falling through to whatever
        # rrc.create_room()/handle_input() would otherwise do -- a
        # blocked attempt never creates or enters the room at all.
        #
        # Runs regardless of the outcome of requires_verification()
        # just below: a room's own tier is an ADDITIONAL restriction on
        # top of the node's global AUTH_MODE, never a substitute for
        # it. Confirmed this has to be unconditional, not skipped under
        # global 'open' -- under 'open' nothing else here gates
        # anything, so a minted-only or hybrid room would otherwise be
        # wide open the moment the node itself wasn't in a stricter
        # mode, which defeats the entire point of a PER-ROOM tier.
        arg = stripped.split(" ", 1)[1].strip() if " " in stripped else ""
        if arg:
            target_room = rrc.clean_room(arg)
            ok, reason_code = core.can_join_room(client_id, target_room)
            if not ok:
                lang = i18n.get_lang(client_id)
                key = "room_needs_verified" if reason_code == "needs_verified" else "room_invite_only"
                return [i18n.t(key, lang)], None

    # Everything else is subject to the node's AUTH_MODE. Checked here,
    # before falling through to whatever this plugin wrapped -- that is
    # the entire mechanism. A blocked action never reaches fservbot's
    # triggers or the real chat handler, so a gated node can't be
    # written to by any path that funnels through handle_input.
    if core.requires_verification(client_id, stripped):
        return [i18n.t("auth_required_generic", i18n.get_lang(client_id))], None

    result = _original(client_id, room, text)

    # Annotate /rooms with each room's tier, by POSITION rather than by
    # parsing rrc's own reply text. rrc.room_names() is what rrc's own
    # /rooms handler iterates internally too, in the same order, so
    # zipping this module's tier lookups against the returned lines
    # one-for-one gets the right answer without re-implementing any of
    # rrc's user-count/topic formatting here -- duplicating that was
    # exactly the pattern that caused real divergence bugs earlier in
    # this project (see barkeep.py/fserv.py's history).
    if low == "/rooms" and result[0]:
        lines, new_room = result
        names = rrc.room_names()
        if len(lines) == len(names):
            annotated = []
            for line, name in zip(lines, names):
                tier = core.room_tier(name)
                annotated.append(line if tier == "open" else line + "  [%s]" % tier)
            result = (annotated, new_room)

    # If this session is verified, keep the identity record's saved
    # nick in sync with whatever rrc actually settled on -- reading it
    # back via rrc.get_user() AFTER delegating, rather than re-parsing
    # "/nick" here ourselves, so this can never drift from rrc's own
    # collision handling (a requested nick that's taken gets rejected
    # by rrc, and this correctly does nothing in that case since the
    # nick rrc reports back is unchanged).
    #
    # Never sync rrc's own auto-generated placeholder (rrc.default_nick,
    # "guest-XXXX"). Confirmed this needs an explicit check, not just
    # "only fire on /nick": a REJECTED /nick attempt (name taken) still
    # reaches this point with the placeholder still in effect, and would
    # otherwise persist "guest-c0fe" as if it were a real chosen name --
    # silently defeating the None-means-nothing-chosen-yet signal
    # /whoami and /admin list both rely on. Compared against
    # default_nick(client_id) specifically, rather than a "guest-"
    # prefix match, so a genuine chosen nick that happens to start with
    # "guest-" is never wrongly excluded.
    hexhash = core.verified_identity_for(client_id)
    if hexhash is not None:
        user = rrc.get_user(client_id)
        nick = user.get("nick") if user else None
        if nick and nick != rrc.default_nick(client_id):
            core.set_nick_for(hexhash, nick)

    return result


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

def _cmd_admin(client_id, stripped):
    parts = stripped.split(" ")
    args = parts[1:]

    if len(args) < 2 or not core.check_admin_password(args[0]):
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
            return ["mode set to " + rest[0]]
        return ["unknown mode -- use open, hybrid, or mandatory"]

    if sub == "meshroom":
        if not rest:
            current = core.MESH_DEFAULT_ROOM
            return ["mesh landing room: " + (current or "(unset -- defaults to #main)")]
        arg0 = rest[0].lower()
        if arg0 in ("off", "none", "unset"):
            core.set_mesh_room(None)
            return ["mesh landing room cleared -- mesh peers default to #main again"]
        slug = core.set_mesh_room(rest[0])
        if slug is None:
            return ["that name isn't usable as a room name"]
        return ["mesh peers now land in #" + slug + " by default (until reboot; "
                "not written to config.py)"]

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
        if tier != "open":
            # Admin-created, so it exists right away rather than only
            # coming into being the first time someone already minted
            # happens to join it -- otherwise an unminted visitor could
            # never even SEE a freshly-restricted room in /rooms until
            # somebody qualified got there first.
            new_room, err = rrc.create_room(target_room)
            if err:
                return [err]
        return ["#%s is now %s" % (target_room, tier)]

    return ["unknown admin command -- try mode, revoke, or room"]


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
        return [i18n.t("invite_usage", lang)]

    target_nick = args[0]
    target_room = rrc.clean_room(args[1]) if len(args) > 1 else current_room

    my_hash = core.verified_identity_for(client_id)
    if my_hash is None:
        return [i18n.t("invite_need_verified", lang)]

    if target_room != current_room:
        return [i18n.t("invite_wrong_room", lang)]

    # Checked before resolving the nick: if the room isn't invite-gated
    # at all, that's the useful thing to say regardless of whether the
    # named person even exists -- "not needed here" beats "no one by
    # that name" when the real answer is that neither matters.
    if core.room_tier(target_room) != "hybrid":
        return [i18n.t("invite_not_gated", lang)]

    target_cid = rrc.find_client_by_nick(target_nick)
    if target_cid is None:
        return [i18n.t("invite_no_such_user", lang, nick=target_nick)]

    ok, reason_code = core.invite_to_room(target_room, target_cid)
    if not ok:
        # core.invite_to_room only ever has the INVITED person's
        # client_id in scope, not this inviter's -- so it can't safely
        # translate for itself (see its own docstring). It returns a
        # plain marker instead; client_id here is genuinely the right
        # one to translate against, since this IS the person reading
        # the reply.
        return [i18n.t("invite_not_gated", lang)]
    return [i18n.t("invite_done", lang, nick=target_nick, room=target_room)]
