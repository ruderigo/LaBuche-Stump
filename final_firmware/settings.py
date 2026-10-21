# The Provisioner's answers, changed from /admin -- no reflash, nothing
# minted again: this Stump's identity (its keys), the chat and billboard
# history, the files and credits, the radio settings all stay as they are.
#
# What can change here: the name, the bot's name and its first-contact
# auto-reply, the file-sharing economy (free, standard, custom weights),
# who may enter (stumpid's mode: open / hybrid / mandatory), which
# features are offered, the librarian's firefly bubble, and on a computer
# the size of its "card" (PC_STORAGE_MB). Each is written back into config.py -- the same
# line the Provisioner wrote, so an update keeps it -- and applied at once
# where the running Stump allows; the rest after a restart (restart()).
#
# Wi-Fi, the chain and the radio have their own cards (or the Provisioner):
# a wrong value there can cut this Stump off, so they aren't here.

import os

CONFIG_FILE = "/config.py"

FEATURES = ("chat", "billboard", "files", "about")
MODES = ("open", "hybrid", "mandatory")
WEIGHT_CLASSES = ("video", "music", "document", "other")
STANDARD_WEIGHTS = {"video": 3, "music": 2, "document": 1, "other": 1}
NAME_MAX = 20            # the Wi-Fi name keeps 12 characters for "-192.168.x.1"
BOT_MAX = 24
GREETING_MAX = 200
WEIGHT_MAX = 99

# Applied to the running Stump at once; the others need a restart.
LIVE = ("CREDITS_ENABLED", "CREDIT_WEIGHTS", "AUTH_MODE", "FEATURES", "LIBRARIAN", "PC_STORAGE_MB")

# A computer's "card": the sd/ folder, PC_STORAGE_MB in size (never more
# than the disk has free). Uploads fill it to fserv.MAX_SD_RATIO, then the
# oldest shared files make room.
STORAGE_DEFAULT_MB = 2048
STORAGE_MIN_MB = 256
MB = 1024 * 1024


def on_computer():
    try:
        import stump_host  # noqa: F401
        return True
    except ImportError:
        return False


def storage():
    """A computer's card: {"mb": its size, "used": bytes in sd/, "disk_free":
    bytes free on the disk, "max_mb": the most it can be}. None on the CAM
    (its card is the one in the slot)."""
    try:
        import stump_host
        sd = stump_host._map("/sd")
        used = stump_host._dir_size(sd)
    except Exception:
        return None                      # (not a computer, or one this can't measure: no Storage group)
    free = 0
    for where in (sd, stump_host.ROOT):          # (no sd/ folder yet: the Stump folder's disk)
        try:
            free = stump_host._disk_free(where)
            break
        except Exception:
            pass
    try:
        mb = int(stump_host.setting("PC_STORAGE_MB", STORAGE_DEFAULT_MB))
    except (TypeError, ValueError):
        mb = STORAGE_DEFAULT_MB
    return {"mb": mb, "used": used, "disk_free": free,
            "max_mb": max(STORAGE_MIN_MB, mb, (used + free) // MB)}


def librarian_on():
    """The librarian's firefly bubble (bottom-right corner): shown?"""
    c = _config()
    return bool(getattr(c, "LIBRARIAN", True)) if c is not None else True


def _config():
    try:
        import config
        return config
    except ImportError:
        return None


def has_access_modes():
    """Is stumpid (who may enter) on this Stump?"""
    try:
        import stumpid.core  # noqa: F401
        return True
    except ImportError:
        return False


def current():
    """What this Stump runs with now."""
    c = _config()
    g = lambda k, d: getattr(c, k, d) if c is not None else d
    try:
        import fserv
        credits, weights = fserv.CREDITS_ENABLED, dict(fserv.CLASS_WEIGHTS)
    except Exception:
        credits, weights = bool(g("CREDITS_ENABLED", False)), dict(g("CREDIT_WEIGHTS", STANDARD_WEIGHTS))
    for k in WEIGHT_CLASSES:
        weights.setdefault(k, STANDARD_WEIGHTS[k])
    mode = g("AUTH_MODE", "open")
    try:
        import stumpid.core as sc
        mode = sc.AUTH_MODE
    except Exception:
        pass
    try:
        import features
        feats = [f for f in FEATURES if features.enabled(f)]
    except Exception:
        feats = list(g("FEATURES", FEATURES))
    out = {"NODE_NAME": g("NODE_NAME", "") or "", "BOT_NAME": g("BOT_NAME", "") or "",
            "MESH_GREETING": g("MESH_GREETING", "") or "", "CREDITS_ENABLED": bool(credits),
            "CREDIT_WEIGHTS": weights, "AUTH_MODE": mode if mode in MODES else "open", "FEATURES": feats,
            "LIBRARIAN": librarian_on()}
    out.update(_storage_now())          # (no {**d} on the board's MicroPython)
    return out


def _storage_now():
    if not on_computer():
        return {}
    try:
        import stump_host
        return {"PC_STORAGE_MB": int(stump_host.setting("PC_STORAGE_MB", STORAGE_DEFAULT_MB))}
    except Exception:
        return {"PC_STORAGE_MB": STORAGE_DEFAULT_MB}


def economy(s=None):
    """"free", "standard" or "custom"."""
    s = s or current()
    if not s["CREDITS_ENABLED"]:
        return "free"
    return "standard" if all(s["CREDIT_WEIGHTS"].get(k) == v for k, v in STANDARD_WEIGHTS.items()) else "custom"


def parse_form(fields):
    """The /admin form -> ({KEY: value}, None), or (None, field name) for
    the first value that isn't acceptable."""
    out = {}
    name = (fields.get("node_name") or "").strip()
    if not name or len(name) > NAME_MAX or any(ord(ch) < 32 for ch in name):
        return None, "node_name"
    out["NODE_NAME"] = name
    bot = (fields.get("bot_name") or "").strip()
    if not bot or len(bot) > BOT_MAX or any(ord(ch) < 32 for ch in bot):
        return None, "bot_name"
    out["BOT_NAME"] = bot
    greeting = (fields.get("greeting") or "").replace("\r", " ").replace("\n", " ").strip()
    if len(greeting) > GREETING_MAX:
        return None, "greeting"
    out["MESH_GREETING"] = greeting
    eco = fields.get("economy")
    if eco == "free":
        out["CREDITS_ENABLED"] = False
        out["CREDIT_WEIGHTS"] = current()["CREDIT_WEIGHTS"]
    elif eco == "standard":
        out["CREDITS_ENABLED"] = True
        out["CREDIT_WEIGHTS"] = dict(STANDARD_WEIGHTS)
    elif eco == "custom":
        w = {}
        for k in WEIGHT_CLASSES:
            try:
                v = int(fields.get("w_" + k, ""))
            except ValueError:
                return None, "w_" + k
            if v < 0 or v > WEIGHT_MAX:
                return None, "w_" + k
            w[k] = v
        out["CREDITS_ENABLED"] = True
        out["CREDIT_WEIGHTS"] = w
    else:
        return None, "economy"
    if has_access_modes():
        mode = fields.get("auth_mode")
        if mode not in MODES:
            return None, "auth_mode"
        out["AUTH_MODE"] = mode
    out["FEATURES"] = [f for f in FEATURES if fields.get("f_" + f)]
    out["LIBRARIAN"] = bool(fields.get("librarian"))
    st = storage() if "storage_mb" in fields else None
    if st is not None:
        try:
            mb = int(str(fields.get("storage_mb")).strip())
        except ValueError:
            return None, "storage_mb"
        if mb < STORAGE_MIN_MB or mb > st["max_mb"]:
            return None, "storage_mb"
        out["PC_STORAGE_MB"] = mb
    return out, None


# -- writing config.py ------------------------------------------------------

def _value_end(lines, i):
    """The index after the last line of the assignment starting at line i
    (a dict or list may run over several lines: brackets counted, strings
    skipped)."""
    depth, quote, j = 0, None, i
    while j < len(lines):
        line = lines[j]
        start = line.index("=") + 1 if j == i else 0
        k = start
        while k < len(line):
            ch = line[k]
            if quote:
                if ch == "\\":
                    k += 1
                elif ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "#":
                break
            elif ch in "[{(":
                depth += 1
            elif ch in "]})":
                depth -= 1
            k += 1
        j += 1
        if depth <= 0 and quote is None:
            return j
    return j


def _is_assignment(line, key):
    if not line.startswith(key):
        return False
    rest = line[len(key):].lstrip(" \t")
    return rest.startswith("=") and not rest.startswith("==")


def write_config(changes, path=None):
    """Writes each KEY = value into config.py in place of its line (or at
    the end if it isn't there), the rest of the file untouched; whole or
    not at all (a temporary file, then a rename). True if written."""
    path = path or CONFIG_FILE
    try:
        with open(path) as f:
            lines = f.read().split("\n")
    except OSError:
        return False
    for key, value in changes.items():
        new = "%s = %r" % (key, value)
        for i, line in enumerate(lines):
            if _is_assignment(line, key):
                lines[i:_value_end(lines, i)] = [new]
                break
        else:
            if lines and lines[-1] == "":
                lines.insert(len(lines) - 1, new)
            else:
                lines.append(new)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write("\n".join(lines))
        os.rename(tmp, path)          # replaces, on the board's FAT too
        return True
    except OSError as e:
        print("[settings] couldn't write config.py:", e)
        return False


def apply(changes):
    """Saves the changes to config.py, applies what the running Stump can
    take now. Returns (saved, [keys that wait for a restart])."""
    before = current()
    changed = {k: v for k, v in changes.items() if before.get(k) != v}
    if not changed:
        return True, []
    saved = write_config(changed)
    c = _config()
    for k, v in changed.items():
        if c is not None:
            try:
                setattr(c, k, v)
            except Exception:
                pass
    later = [k for k in changed if k not in LIVE]
    if "CREDITS_ENABLED" in changed or "CREDIT_WEIGHTS" in changed:
        try:
            import fserv
            fserv.CREDITS_ENABLED = changes["CREDITS_ENABLED"]
            fserv.CLASS_WEIGHTS = dict(changes["CREDIT_WEIGHTS"])
        except Exception:
            later.append("CREDIT_WEIGHTS")
    if "AUTH_MODE" in changed:
        try:
            import stumpid.core as sc
            sc.set_mode(changed["AUTH_MODE"])
            import stumpid.install as si
            si.enforce_mode()          # out of the rooms they may no longer be in
        except Exception:
            later.append("AUTH_MODE")
    if "FEATURES" in changed:
        try:
            import features
            features._CONFIGURED = list(changed["FEATURES"])
        except Exception:
            pass
        # The pages follow at once; chat over the mesh starts (or stops)
        # with the Stump.
        if ("chat" in before["FEATURES"]) != ("chat" in changed["FEATURES"]):
            later.append("FEATURES")
    out = []
    for k in later:
        if k not in out:
            out.append(k)
    return saved, out


def restart():
    """Starts this Stump again: the board resets; on a computer, the
    process ends with RESTART_CODE and its supervisor starts it again."""
    try:
        import keepalive
        keepalive.planned()          # not a failed start
    except Exception:
        pass
    try:
        import stump_host
        stump_host.restart()
        return
    except ImportError:
        pass
    import machine
    machine.reset()
