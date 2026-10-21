# Project Stump -- layout.py
#
# The order of the navigation buttons (the icon row on every page) and of
# the home page's tiles, and which of them are hidden, set by an admin
# from /admin, and saved on the board's own memory like the theme. Hiding
# only takes a button or tile off the row (its page still answers at its
# address); switching a whole feature off is FEATURES in config.py.
# Home comes first by default. A key the saved order doesn't know (a
# feature switched back on, a new plugin card) goes at the end; one that
# no longer exists is skipped, so an old saved order never breaks a page.

LAYOUT_FILE = "/layout.json"

NAV_DEFAULT = ("home", "chat", "board", "files", "tools", "about")
TILES_DEFAULT = ("chat", "board", "files", "tools", "about")

KINDS = ("nav", "tiles", "nav_hidden", "tiles_hidden")
_saved = {}


def _load():
    global _saved
    try:
        import json
        with open(LAYOUT_FILE) as f:
            d = json.load(f)
        _saved = {k: [str(x) for x in d.get(k, [])] for k in KINDS if isinstance(d.get(k), list)}
    except Exception:
        _saved = {}


_load()


def order(kind, available):
    """`available` (keys, in their default order) in the admin's order."""
    want = _saved.get(kind) or []
    avail = list(available)
    out = [k for k in want if k in avail]
    return out + [k for k in avail if k not in out]


def hidden(kind):
    """The keys an admin hid ("nav" or "tiles")."""
    return set(_saved.get(kind + "_hidden") or [])


def nav(available=NAV_DEFAULT, all=False):
    """The navigation row's keys in order; hidden ones only with all=True."""
    out = order("nav", available)
    return out if all else [k for k in out if k not in hidden("nav")]


def tiles(available, all=False):
    out = order("tiles", available)
    return out if all else [k for k in out if k not in hidden("tiles")]


def is_custom(kind):
    return bool(_saved.get(kind)) or bool(_saved.get(kind + "_hidden"))


def save(kind, keys):
    """Saves an order, or the hidden keys ("nav_hidden", "tiles_hidden");
    None or an empty list: back to the default. True if saved."""
    return save_many({kind: keys})


def save_many(changes):
    """Saves several of KINDS at once, {kind: keys or None}."""
    import json
    new = dict(_saved)
    for kind, keys in changes.items():
        if kind not in KINDS:
            return False
        if keys:
            seen = []
            for k in keys:
                k = str(k)[:120]
                if k and k not in seen:
                    seen.append(k)
            new[kind] = seen[:40]
        else:
            new.pop(kind, None)
    try:
        with open(LAYOUT_FILE, "w") as f:
            json.dump(new, f)
    except OSError:
        return False
    _saved.clear()
    _saved.update(new)
    return True
