# Project Stump -- fservbot core (dialogue engine + settings + admin)
# Place at: final_firmware/fservbot/core.py
#
# The dialogue table and everything that edits it. Pure logic: binds
# nothing, starts no task, and is called only from install.py's wrapper
# around rrc.handle_input. Same shape as billboard.py and fserv.py --
# storage, state, and text, with the HTTP layer somewhere else.
#
# TWO SETUP SURFACES, ONE BACKEND. A technician can preset everything
# through the provisioner (which writes config.py), and a user in the
# RRC channel who knows the operator password can change the same
# things live with /fs* commands. Both read and write the state below,
# so neither can produce a node the other can't understand.
#
# WHAT IS CONFIG AND WHAT IS CONTENT. The password itself is only
# settable from config.py -- i.e. by a technician with the provisioner
# or a serial connection. It is deliberately NOT changeable from chat:
# a password that can be rotated by whoever currently holds it is a
# door that locks the operator out of their own node, and the README's
# "Air-gapped administration" section is explicit that settings belong
# behind a physical connection. Dialogues, the trigger prefix, and the
# broadcast interval are content-level, so they are editable from the
# channel, and persist to SD alongside the billboard.

import time
import ujson as json

import rrc
import billboard

from fservbot import templates

try:
    from config import BOT_NAME
except ImportError:
    BOT_NAME = "BarKeep"

# Every setting below is optional in config.py. A node whose config
# predates this plugin still boots and still runs the bot on defaults
# -- config is an override, not a hard dependency, the same way
# fserv.py already treats CREDIT_WEIGHTS.
try:
    from config import FSERVBOT_OP_PASSWORD
except ImportError:
    FSERVBOT_OP_PASSWORD = ""

try:
    from config import FSERVBOT_TRIGGER_PREFIX
except ImportError:
    FSERVBOT_TRIGGER_PREFIX = "!"

try:
    from config import FSERVBOT_BROADCAST_MINS
except ImportError:
    FSERVBOT_BROADCAST_MINS = 0        # 0 = broadcasting off

# Bounded everywhere, same reasoning as rrc.py: this runs unsupervised
# on a board with finite RAM, so nothing that an operator can append to
# is allowed to grow without a ceiling.
MAX_DIALOGUES = 40
MAX_TRIGGER_LEN = 24
MAX_RESPONSE_LEN = 300
MIN_BROADCAST_MINS = 5             # below this it's channel spam, not an ad

STORAGE_FILE = "/sd/fservbot.json"

# ---------------------------------------------------------------------
# State
# ---------------------------------------------------------------------

_custom = {}            # trigger -> response, operator-added
_disabled = []          # base triggers switched off (list, not set: JSON-native)
_opts = {}              # runtime overrides for prefix / broadcast
_loaded = False
_last_broadcast = [0]   # list so it's mutable from a function


def prefix():
    return _opts.get("prefix") or FSERVBOT_TRIGGER_PREFIX


def broadcast_mins():
    v = _opts.get("broadcast_mins")
    return FSERVBOT_BROADCAST_MINS if v is None else v


def bot_name():
    return BOT_NAME


# ---------------------------------------------------------------------
# Trigger normalisation
# ---------------------------------------------------------------------

def _clean_lower(text, limit):
    """rrc._clean strips control characters and truncates. Reused rather
    than reimplemented -- this project already shares small private
    helpers across modules (billboard._esc, billboard._extract_ip)."""
    return rrc._clean(text, limit).lower()


def _bare(raw):
    """Reduces a trigger to its BARE NAME -- no prefix, lowercased,
    control characters gone.

    Triggers are stored bare and the prefix is prepended only when
    matching or displaying. Storing them prefixed instead meant that
    changing the prefix orphaned every custom dialogue ('!wifi' kept
    its old '!' while the bot had moved to '@') and quietly re-enabled
    every base template that had been switched off, because the
    disabled list no longer matched the new keys. Bare storage makes a
    prefix change atomic: one setting moves everything, and there is no
    second copy of the prefix anywhere to fall out of sync.

    Leading punctuation is stripped rather than rejected, so '!list',
    '@list' and 'list' all name the same trigger -- including entries
    written by an older build, or by hand, under a different prefix."""
    t = _clean_lower(raw, MAX_TRIGGER_LEN + 2)
    while t and not (t[0].isalpha() or t[0].isdigit()):
        t = t[1:]
    return t[:MAX_TRIGGER_LEN]


def _norm_incoming(text):
    """Bare name of a trigger someone SAID, or "" if it wasn't one.

    Requires the prefix to actually be present -- without that check
    the word 'help' in ordinary conversation would set the bot off.
    Matches the FIRST WORD only, so '!list please' works: matching the
    whole line meant any word after the trigger silently killed it,
    which reads as a broken bot rather than a typo. mIRC fserv scripts
    matched the first word too."""
    first = text.split(" ", 1)[0]
    t = _clean_lower(first, MAX_TRIGGER_LEN + 2)
    p = prefix()
    if not t.startswith(p):
        return ""
    return _bare(t)


# ---------------------------------------------------------------------
# Persistence -- SD when present, memory otherwise
# ---------------------------------------------------------------------

def load():
    """Reads saved state. Safe to call repeatedly; only the first call
    does work."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not billboard._sd_available():
        return
    try:
        with open(STORAGE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return              # absent or unparseable -- run on base templates
    # Shape-check everything below. A file that PARSES but isn't the
    # expected shape (a bare list, a null, non-string values) would
    # otherwise throw AttributeError/TypeError straight past the except
    # above -- at import, during boot, sending main.py into its reset
    # loop over a scrambled file on a card anyone can pull out. Bad
    # entries are skipped individually; they are never fatal.
    if not isinstance(data, dict):
        return
    custom = data.get("custom")
    if isinstance(custom, dict):
        for k, v in custom.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            trig = _bare(k)
            resp = rrc._clean(v, MAX_RESPONSE_LEN)
            if trig and resp and len(_custom) < MAX_DIALOGUES:
                _custom[trig] = resp
    disabled = data.get("disabled")
    if isinstance(disabled, list):
        for k in disabled:
            if isinstance(k, str):
                t = _bare(k)
                if t and t not in _disabled:
                    _disabled.append(t)
    opts = data.get("opts")
    if isinstance(opts, dict):
        p = opts.get("prefix")
        if isinstance(p, str) and len(p) == 1 and ord(p) > 32:
            _opts["prefix"] = p
        b = opts.get("broadcast_mins")
        if isinstance(b, int) and (b == 0 or b >= MIN_BROADCAST_MINS):
            _opts["broadcast_mins"] = b


def _save():
    """No card, no save. The bot keeps working from memory for the rest
    of this boot and says so -- same degrade-gracefully behaviour as
    fserv's ledger and the billboard's flash fallback."""
    if not billboard._sd_available():
        return False
    try:
        with open(STORAGE_FILE, "w") as f:
            json.dump({"custom": _custom, "disabled": _disabled,
                       "opts": _opts}, f)
        return True
    except OSError:
        return False


def _persist_note(ok):
    return "" if ok else "  (no SD card -- this lasts until reboot)"


# ---------------------------------------------------------------------
# Dialogue lookup
# ---------------------------------------------------------------------

def active_triggers():
    """Display form: bare names with the current prefix applied."""
    p = prefix()
    out = []
    for t in _custom:
        out.append(p + t)
    for t in templates.BASE_TEMPLATES:
        if t not in _disabled and t not in _custom:
            out.append(p + t)
    return out


def _ctx():
    """Everything a template is allowed to know. Passed in rather than
    imported by templates.py so the template pack stays a file of plain
    text functions that can be rewritten without touching this engine."""
    return {"bot_name": BOT_NAME, "prefix": prefix(),
            "triggers": active_triggers()}


def maybe_respond(room, nick, text):
    """Returns a reply for a plain chat line, or None.

    Only called for lines that were NOT slash-commands: this is the bot
    reacting to channel conversation, separate from rrc.py's own
    /command parser."""
    load()
    trig = _norm_incoming(text)
    if not trig:
        return None
    if trig in _custom:
        return _custom[trig]
    if trig in templates.BASE_TEMPLATES and trig not in _disabled:
        try:
            return templates.BASE_TEMPLATES[trig](_ctx())
        except Exception as e:
            # A broken template must not take down the chat handler it
            # was called from. Report and stay quiet.
            print("[fservbot] template '%s' failed: %s" % (trig, e))
            return None
    return None


def maybe_broadcast(room):
    """The classic fserv channel ad: says what this node is, at most
    once every broadcast_mins.

    Driven by chat activity rather than by a timer task on purpose.
    Adding a background task would mean another coroutine in a
    cooperative single-threaded loop that already treats any stall as a
    node-wide outage -- and an empty room does not need advertising to.
    Someone talking is exactly when an ad is worth showing, which is
    also how the mIRC scripts behaved."""
    load()
    mins = broadcast_mins()
    if not mins:
        return None
    now = time.time()
    if _last_broadcast[0] and (now - _last_broadcast[0]) < mins * 60:
        return None
    _last_broadcast[0] = now
    try:
        return templates.BROADCAST(_ctx())
    except Exception as e:
        print("[fservbot] broadcast template failed:", e)
        return None


def help_line():
    """One line appended to rrc's own /help output, so the bot is
    discoverable without editing rrc.py."""
    p = prefix()
    return "%sfserv %shelp     the fserv bot  (%sfshelp to configure it)" % (p, p, "/")


# ---------------------------------------------------------------------
# Admin commands -- the RRC surface
# ---------------------------------------------------------------------

COMMANDS = ("fsadd", "fsdel", "fslist", "fsset", "fsshow", "fshelp")


def _auth(pw):
    """Blank configured password means editing is OFF -- fail closed. A
    blank supplied password can never match a blank configured one, so
    a node that was never set up cannot be reconfigured by a passer-by
    who simply omits the argument."""
    return bool(FSERVBOT_OP_PASSWORD) and pw == FSERVBOT_OP_PASSWORD


_DENIED = ["wrong password -- or none set yet, in which case editing is "
           "off until a technician sets FSERVBOT_OP_PASSWORD."]


def handle_command(cmd, arg):
    """Returns reply lines shown ONLY to whoever typed the command.

    Never posted to the room: a mistyped password would otherwise land
    in the channel transcript, where MAX_MESSAGES_PER_ROOM keeps it
    readable to everyone for the next 60 messages."""
    load()

    if cmd == "fshelp":
        p = prefix()
        return [
            "fserv bot -- channel triggers start with '%s' (%sfserv, %shelp, %slist)" % (p, p, p, p),
            "/fsadd <pw> <trigger> :: <response>   add or replace a dialogue",
            "/fsdel <pw> <trigger>                 remove one (base or custom)",
            "/fslist                               show active triggers",
            "/fsshow <trigger>                     show what one replies",
            "/fsset <pw> prefix <char>             change the trigger character",
            "/fsset <pw> broadcast <mins|off>      periodic 'what I am' notice",
            "the password is set by a technician in config.py, not from here.",
        ]

    if cmd == "fslist":
        act = sorted(active_triggers())
        if not act:
            return ["no triggers active"]
        p = prefix()
        off = sorted(p + t for t in _disabled if t in templates.BASE_TEMPLATES)
        lines = ["active: " + ", ".join(act)]
        if off:
            lines.append("switched off: " + ", ".join(off))
        return lines

    if cmd == "fsshow":
        trig = _bare(arg)
        if not trig:
            return ["usage: /fsshow <trigger>"]
        shown = prefix() + trig
        if trig in _custom:
            return ["%s -> %s" % (shown, _custom[trig])]
        if trig in templates.BASE_TEMPLATES and trig not in _disabled:
            try:
                return ["%s -> %s  (base template)" % (shown, templates.BASE_TEMPLATES[trig](_ctx()))]
            except Exception as e:
                return ["%s is a base template and it errored: %s" % (shown, e)]
        return ["nothing answers to %s" % shown]

    if cmd == "fsadd":
        pw, _, rest = arg.partition(" ")
        if not _auth(pw):
            return _DENIED
        if "::" not in rest:
            return ["usage: /fsadd <pw> <trigger> :: <response>"]
        raw_t, _, raw_r = rest.partition("::")
        trig = _bare(raw_t)
        resp = rrc._clean(raw_r, MAX_RESPONSE_LEN)
        if not trig or not resp:
            return ["need both a trigger and a response"]
        if trig not in _custom and len(_custom) >= MAX_DIALOGUES:
            return ["dialogue limit reached (%d) -- remove one first" % MAX_DIALOGUES]
        _custom[trig] = resp
        # Re-adding un-hides a base trigger that had been switched off,
        # so /fsadd is always "make this answer", never a no-op that
        # leaves the operator wondering why nothing happened.
        while trig in _disabled:
            _disabled.remove(trig)
        ok = _save()
        return ["saved: %s%s -> %s%s" % (prefix(), trig, resp, _persist_note(ok))]

    if cmd == "fsdel":
        pw, _, rest = arg.partition(" ")
        if not _auth(pw):
            return _DENIED
        trig = _bare(rest.strip())
        if not trig:
            return ["usage: /fsdel <pw> <trigger>"]
        removed = False
        if trig in _custom:
            del _custom[trig]
            removed = True
        if trig in templates.BASE_TEMPLATES and trig not in _disabled:
            # Base templates are code, so they are switched off rather
            # than deleted -- and can be switched back on later with
            # /fsadd, or by clearing the saved file.
            _disabled.append(trig)
            removed = True
        if not removed:
            return ["nothing answers to %s%s" % (prefix(), trig)]
        ok = _save()
        return ["removed: %s%s%s" % (prefix(), trig, _persist_note(ok))]

    if cmd == "fsset":
        pw, _, rest = arg.partition(" ")
        if not _auth(pw):
            return _DENIED
        key, _, val = rest.strip().partition(" ")
        key = key.lower()
        val = val.strip()

        if key == "prefix":
            ch = rrc._clean(val, 1)
            if not ch or ch == "/":
                # '/' would collide with rrc's own command parser: every
                # trigger would be swallowed as a chat command before
                # the bot ever saw it.
                return ["pick a single character that isn't '/'  e.g. ! or @ or ~"]
            _opts["prefix"] = ch
            ok = _save()
            return ["trigger prefix is now '%s' -- try %sfserv%s" % (ch, ch, _persist_note(ok))]

        if key == "broadcast":
            if val.lower() in ("off", "0", "none"):
                _opts["broadcast_mins"] = 0
                ok = _save()
                return ["periodic notice off" + _persist_note(ok)]
            try:
                mins = int(val)
            except ValueError:
                return ["usage: /fsset <pw> broadcast <minutes|off>"]
            if mins < MIN_BROADCAST_MINS:
                return ["minimum is %d minutes -- anything faster is channel spam"
                        % MIN_BROADCAST_MINS]
            _opts["broadcast_mins"] = mins
            _last_broadcast[0] = 0      # let the next line advertise
            ok = _save()
            return ["periodic notice every %d minutes%s" % (mins, _persist_note(ok))]

        return ["settable: prefix, broadcast   (see /fshelp)"]

    return None


def reset():
    """Clears runtime state. Tests, and the honest answer to 'how do I
    start over' -- delete STORAGE_FILE and reboot."""
    _custom.clear()
    del _disabled[:]
    _opts.clear()
    _last_broadcast[0] = 0
    global _loaded
    _loaded = False
