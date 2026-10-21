# Project Stump -- fservbot activation
# Place at: final_firmware/fservbot/install.py
#
# Registers with the plugin API (plugins.py, docs/PLUGINS.md); nothing
# to add to example_node.py, the loader finds this folder by itself.
#
#   /fsadd /fsdel /fslist /fsset /fsshow /fshelp
#                       the operator's commands. Slash only (trigger=
#                       False): several carry the operator password, and
#                       a "!" line is posted to the room for all to read.
#   on_message          the channel triggers (!fserv, !list, ...)
#   every 30 s          the periodic "what I am" notice, now a real
#                       timer -- still only in a room where someone has
#                       spoken since the last notice, so an empty room is
#                       never advertised to (or a LoRa mesh woken up).
#
# Before the plugin API this wrapped rrc.handle_input; the wrapper's job
# (seeing chat lines, appending to /help) is now the registry's.

import time
import rrc

from fservbot import core

VERSION = "1.1"
STUMP_API = 1

# The room with the most recent human line, and when -- for the notice.
_activity = {"room": None, "at": 0}


def setup(stump):
    core.STORAGE_FILE = stump.storage_file("fservbot.json", legacy=core.STORAGE_FILE)
    core.load()
    for name in core.COMMANDS:
        stump.command(name, _command(name), trigger=False, limit=False, mesh_lines=0,
                      help=core.help_line if name == "fshelp" else None)
    stump.on_message(_on_line)
    stump.every(30, _notice)
    mins = core.broadcast_mins()
    print("[fservbot] active: prefix '%s', %d trigger(s), notice %s" % (
        core.prefix(), len(core.active_triggers()),
        ("every %dm" % mins) if mins else "off"))
    if not core.FSERVBOT_OP_PASSWORD:
        # Said out loud at boot because it is the difference between a
        # configurable bot and a read-only one, and a technician who
        # skipped the prompt has no other way to notice.
        print("[fservbot] no operator password set -- dialogue editing is OFF")


def _command(name):
    def run(ctx, arg):
        replies = core.handle_command(name, arg)
        if replies is None:
            import i18n
            return [rrc.tok_unknown(name, i18n.t("unknown_command", ctx.lang, cmd=name))]
        return replies
    return run


def _on_line(ctx):
    """A plain line rrc accepted and posted: answer a trigger. Posted as
    the bot's own name, straight into the room, as before."""
    _activity["room"], _activity["at"] = ctx.room, time.time()
    reply = core.maybe_respond(ctx.room, ctx.nick, ctx.text.strip())
    if reply:
        rrc.post(ctx.room, core.bot_name(), reply)
        _activity["at"] = 0      # the bot just spoke here: no notice on top


def _notice():
    room, at = _activity["room"], _activity["at"]
    if not room or not at:
        return
    ad = core.maybe_broadcast(room)
    if ad:
        rrc.post(room, core.bot_name(), ad)
        _activity["at"] = 0      # wait for someone to speak again
