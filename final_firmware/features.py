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
    ("/admin/delete", "files"),
    ("/rrc", "chat"),
    ("/billboard", "billboard"),
    ("/post", "billboard"),
    ("/files", "files"),
    ("/upload", "files"),
    ("/download", "files"),
    ("/about", "about"),
)


def enabled(name):
    """True if this node offers the feature. An explicitly empty list
    means none (the provisioner warns before writing that). A value
    with no recognisable names at all -- a typo, or not a list -- turns
    everything on, rather than silently leaving a node with nothing."""
    src = _CONFIGURED
    if isinstance(src, str):          # FEATURES = "chat, files" also works
        src = [f for f in src.split(",") if f.strip()]
    try:
        raw = [str(f).strip().lower() for f in src]
    except TypeError:
        return name in ALL
    if not raw:
        return False
    chosen = [f for f in raw if f in ALL]
    return name in (chosen or ALL)


def feature_for_path(path):
    """The feature an address belongs to, or None if it's always on."""
    for prefix, name in _ROUTES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return name
        if prefix in ("/rrc", "/about") and path.startswith(prefix):
            return name
    return None


def path_blocked(path):
    f = feature_for_path(path)
    return f is not None and not enabled(f)
