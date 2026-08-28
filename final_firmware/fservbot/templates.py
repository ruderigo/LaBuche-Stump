# Project Stump -- fservbot base template pack
# Place at: final_firmware/fservbot/templates.py
#
# The starter set every mIRC fserv script shipped with: say what you
# are, list what you answer to, show the shelf, report status, and
# advertise yourself into the channel every so often.
#
# Kept in its own file so an operator can rewrite the node's voice by
# editing ONE file, without going near the dialogue engine. Everything
# here is a plain function returning plain text.
#
# PLAIN TEXT, NOT HTML. These land in an RRC message, which
# rrc_ui.render() escapes with esc() exactly like a stranger's chat
# line. Returning "<a href=...>" would display the tag literally. That
# escaping is the point -- an operator-supplied string replayed to
# every future visitor goes through the same escape-at-point-of-use
# path as any other untrusted text. Quote a path as text and let the
# shelf page carry the real links.

import fserv

# How many filenames !list will name before it stops and points
# elsewhere. A room with MAX_MESSAGE_LEN=400 truncates anything longer
# anyway, so this keeps the reply inside one readable line rather than
# letting rrc._clean() chop it mid-filename.
LIST_LIMIT = 12


def about(ctx):
    """What this thing is. The reply to !fserv, and the line the
    periodic broadcast puts in the channel."""
    p = ctx["prefix"]
    return ("%s -- fserv bot on this Stump. %shelp for commands, "
            "%slist for what's on the shelf." % (ctx["bot_name"], p, p))


def help_(ctx):
    names = sorted(ctx["triggers"])
    if not names:
        return "no triggers configured right now."
    return "I answer to: " + ", ".join(names)


def list_(ctx):
    if not fserv.sd_ok:
        return "shelf's empty -- no card in the slot."
    names = fserv._list_files()
    if not names:
        return "nothing on the shelf yet. Bring something, if you've got it."
    shown = names[:LIST_LIMIT]
    parts = []
    for n in shown:
        cls = fserv.guess_class(n)
        if fserv.CREDITS_ENABLED:
            parts.append("%s (%s, %d)" % (n, cls, fserv.credit_cost(n)))
        else:
            parts.append("%s (%s)" % (n, cls))
    line = "on the shelf: " + ", ".join(parts)
    if len(names) > LIST_LIMIT:
        line += "  ...and %d more on the main page" % (len(names) - LIST_LIMIT)
    return line


def info(ctx):
    mode = "credits on" if fserv.CREDITS_ENABLED else "free mode"
    card = "SD mounted" if fserv.sd_ok else "no SD card"
    return "%s -- %s, %s. Files: open the main page, or /download?f=<name>." % (
        ctx["bot_name"], mode, card)


def rules(ctx):
    return ("Take what you need, leave what you can. Nothing here is "
            "backed up and the chat forgets itself on reboot.")


# Trigger suffix (the prefix is prepended at runtime, so changing
# FSERVBOT_TRIGGER_PREFIX moves all of these at once) -> handler.
#
# Several names share a handler on purpose: an fserv that answers
# !list but not !files is an fserv that looks broken to half the
# people who try it.
BASE_TEMPLATES = {
    "fserv": about,
    "about": about,
    "help": help_,
    "list": list_,
    "files": list_,
    "info": info,
    "rules": rules,
}

# What the periodic broadcast says. Same text as !fserv by design --
# the ad and the answer should not disagree about what this node is.
BROADCAST = about
