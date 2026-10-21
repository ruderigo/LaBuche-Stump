"""
Which visitor features this node offers, chosen by the technician in the
provisioner and stored as FEATURES in config.py:

    "chat"       the RRC chat -- web (/rrc) and over the mesh (LXMF)
    "billboard"  the bulletin board
    "files"      file sharing: upload, download, the shelf
    "about"      the About page

A feature that's off is gone, not just unlinked: no home tile, no nav
link, no /admin section, and its addresses answer 404. The home page,
/tools and /admin always exist.
"""

ALL = ("chat", "billboard", "files", "about")

try:
    from config import FEATURES as _CONFIGURED
except ImportError:
    _CONFIGURED = ALL

# Address prefixes that belong to each feature. /admin/delete_post is
# checked before /admin/delete, which is a prefix of it.
_ROUTES = (
    ("/admin/delete_post", "billboard"),
    ("/billboard.json", "billboard"),
    ("/files.json", "files"),
    ("/admin/delete", "files"),
    ("/rrc", "chat"),
    ("/billboard", "billboard"),
    ("/post", "billboard"),
    ("/files", "files"),
    ("/upload", "files"),
    ("/download", "files"),
    ("/about", "about"),
)


_memo = [None, None]        # (the list it was worked out from, the features on)


def enabled(name):
    """True if this node offers the feature. Worked out once per value of
    _CONFIGURED (it's asked on every request: re-parsing it each time was
    a measurable share of a request on the board)."""
    src = _CONFIGURED
    if _memo[0] is not src:
        _memo[0], _memo[1] = src, _work_out(src)
    return name in _memo[1]


def _work_out(src):
    """The features on, from FEATURES. An explicitly empty list means none
    (the provisioner warns before writing that). A value with no
    recognisable names at all -- a typo, or not a list -- turns
    everything on, rather than silently leaving a node with nothing."""
    if isinstance(src, str):          # FEATURES = "chat, files" also works
        src = [f for f in src.split(",") if f.strip()]
    try:
        raw = [str(f).strip().lower() for f in src]
    except TypeError:
        return ALL
    if not raw:
        return ()
    chosen = [f for f in raw if f in ALL]
    return tuple(chosen or ALL)


# (prefix, prefix + "/", prefix + "?", name, any address starting with
# it), built once: asked on every request.
_MATCH = tuple((p, p + "/", p + "?", n, p in ("/rrc", "/about")) for p, n in _ROUTES)


def feature_for_path(path):
    """The feature an address belongs to, or None if it's always on."""
    if not path.startswith(("/a", "/b", "/f", "/r", "/p", "/u", "/d")):
        return None                   # (most addresses: none of the routes)
    for prefix, slash, query, name, loose in _MATCH:
        if path.startswith(prefix) and (loose or path == prefix or path.startswith(slash) or path.startswith(query)):
            return name
    return None


def path_blocked(path):
    f = feature_for_path(path)
    return f is not None and not enabled(f)
