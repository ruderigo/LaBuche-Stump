# Project Stump -- fservbot activation
# Place at: final_firmware/fservbot/install.py
#
# ONE LINE activates this plugin, and it is the only change any Stump
# file needs:
#
#     import fservbot.install          # near the other imports
#     fservbot.install.activate()      # after RRC exists, before serving
#
# (example_node.py is the natural home -- it is the module whose stated
# job is wiring things together.)
#
# ---------------------------------------------------------------------
# WHY A WRAPPER AND NOT AN EDIT TO barkeep.py
# ---------------------------------------------------------------------
# The bot has to see what people type in the channel. That text arrives
# at barkeep's POST /rrc/send handler, which hands it to
# rrc.handle_input(client_id, room, text) and returns whatever comes
# back. So the whole integration point is that one function.
#
# Wrapping it here, at runtime, means barkeep.py and rrc.py stay
# byte-for-byte the files the Stump team already tested and hashed --
# no merge, no manifest churn on files that didn't change, and
# uninstalling is deleting a folder and one line.
#
# The wrapper is deliberately thin and total: it forwards EVERYTHING it
# doesn't claim to the original function, unchanged, including the
# return value. rrc keeps deciding what a chat line means; this only
# gets a look first.
#
# Honest cost of the approach, stated rather than buried: monkey-
# patching is invisible in the source of the file being patched.
# Someone reading rrc.py will not see that handle_input has been
# wrapped. That is mitigated three ways -- activate() prints a boot
# line like every other component here, it refuses to double-wrap,
# and deactivate() puts the original back -- but it remains the real
# trade-off against not touching two core modules. If the Stump team
# would rather have the call sites explicit, the equivalent edit is
# eight lines in barkeep's /rrc/send branch and this file can go away;
# see README.md in this folder.

import rrc

from fservbot import core

_original = None
_installed_as = None


def is_active():
    return _original is not None


def activate():
    """Wraps rrc.handle_input. Returns True if it did, False if the
    plugin was already active.

    Reports its own result, per this project's convention that a
    success message must be printed from where the work happened
    rather than next to the code that scheduled it."""
    global _original, _installed_as
    if _original is not None:
        print("[fservbot] already active -- ignoring second activate()")
        return False

    core.load()
    _original = rrc.handle_input
    rrc.handle_input = _dispatch
    # Remembered so deactivate() can tell whether it's still safe to
    # unwind -- see deactivate()'s comment for why this matters once a
    # second plugin (stumpid) can also be wrapping the same function.
    _installed_as = _dispatch

    mins = core.broadcast_mins()
    print("[fservbot] active: prefix '%s', %d trigger(s), notice %s" % (
        core.prefix(), len(core.active_triggers()),
        ("every %dm" % mins) if mins else "off"))
    if not core.FSERVBOT_OP_PASSWORD:
        # Said out loud at boot because it is the difference between a
        # configurable bot and a read-only one, and a technician who
        # skipped the prompt has no other way to notice.
        print("[fservbot] no operator password set -- dialogue editing is OFF")
    return True


def deactivate():
    """Restores the original handler. Used by tests, and the way out if
    the bot ever needs to be taken out of the path without a reboot.

    Only restores if rrc.handle_input is STILL exactly what activate()
    put there. If another plugin (stumpid) wrapped it again since, this
    module is no longer the outermost layer, and blindly overwriting
    rrc.handle_input with our own _original would silently discard
    whatever wrapped on top of us -- confirmed this actually happens:
    deactivating two stacked plugins in activation order (rather than
    the reverse) corrupted the chain into a self-referential loop and
    blew MicroPython's recursion limit. Refusing with a clear reason is
    the safe failure here; the caller (or a human) can deactivate the
    outer plugin first and retry."""
    global _original, _installed_as
    if _original is None:
        return False
    if rrc.handle_input is not _installed_as:
        print("[fservbot] cannot deactivate cleanly -- something else has "
              "wrapped rrc.handle_input since this plugin activated. "
              "Deactivate that plugin first.")
        return False
    rrc.handle_input = _original
    _original = None
    _installed_as = None
    print("[fservbot] deactivated")
    return True


def _dispatch(client_id, room, text):
    """Stands in for rrc.handle_input. Same signature, same return
    contract: (reply_lines, new_room)."""
    stripped = (text or "").strip()

    # --- the plugin's own /fs* commands ---------------------------------
    if stripped.startswith("/"):
        parts = stripped[1:].split(" ", 1)
        name = parts[0].lower()
        if name in core.COMMANDS:
            arg = parts[1].strip() if len(parts) > 1 else ""
            try:
                replies = core.handle_command(name, arg)
            except Exception as e:
                print("[fservbot] command /%s failed: %s" % (name, e))
                return ["that command hit an error -- it's been logged"], None
            if replies is not None:
                return replies, None

    # --- everything else is rrc's, unchanged ----------------------------
    replies, new_room = _original(client_id, room, text)

    # Append the bot to rrc's own /help rather than editing rrc.py, so
    # someone who types /help actually discovers the thing is here.
    if stripped.startswith("/") and stripped[1:].split(" ", 1)[0].lower() == "help":
        try:
            return list(replies) + [core.help_line()], new_room
        except Exception:
            return replies, new_room

    # --- channel triggers -----------------------------------------------
    # Only plain lines that rrc accepted cleanly. A /command, or a line
    # rrc rejected (dead room, empty after cleaning), never reaches the
    # bot -- replies being non-empty is exactly rrc's way of saying it
    # had something to tell the sender about this line.
    if not stripped.startswith("/") and not replies:
        try:
            user = rrc.get_user(client_id)
            reply = core.maybe_respond(room, user["nick"], stripped)
            if reply:
                rrc.post(room, core.bot_name(), reply)
            else:
                ad = core.maybe_broadcast(room)
                if ad:
                    rrc.post(room, core.bot_name(), ad)
        except Exception as e:
            # The bot failing must never cost someone their message --
            # by this point rrc has already accepted and posted it.
            print("[fservbot] response failed:", e)

    return replies, new_room
