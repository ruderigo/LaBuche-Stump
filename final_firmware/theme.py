"""
Site-wide look, shared by every page (barkeep.py and rrc_ui.py).

The node's theme is chosen by the technician in the provisioner and
stored as THEME in config.py. The server writes it straight into each
page's <html data-theme='...'>, so it applies on first paint, with or
without JavaScript. A browser can still override it from /admin; that
choice lives in that browser's localStorage only and is applied by
STARTUP_SCRIPT on top.

Every colour on every page comes from the variables below. The chat
page used to hard-code the amber palette, so it ignored themes
entirely -- including the per-browser choice from /admin.
"""

THEMES = ("amber", "phosphor", "oled", "paper")
BUILTIN_THEMES = THEMES

# Themes added by packs (docs/PACKS.md): the Provisioner writes them to
# themes.json on the board's flash, {name: {"label", "colors"}}. Checked
# hard -- a name is lowercase letters, digits and "-", a colour variable
# is one of the names below and a colour is #rgb or #rrggbb -- because
# they are written straight into every page's CSS.
THEMES_FILE = "themes.json"
COLOR_VARS = ("--bg", "--panel", "--ember", "--ember-bright", "--text", "--muted", "--border",
              "--panel-2", "--line", "--dim", "--action", "--dm", "--dm-nick", "--err")
EXTRA = {}   # name -> {"label": str|dict, "colors": {var: colour}}


def _ok_name(n):
    return isinstance(n, str) and 0 < len(n) <= 20 and all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in n)


def _ok_colour(c):
    return isinstance(c, str) and len(c) in (4, 7) and c[0] == "#" and \
        all(ch in "0123456789abcdefABCDEF" for ch in c[1:])


def _load_extra():
    try:
        import ujson as json
    except ImportError:
        import json
    try:
        with open(THEMES_FILE) as f:
            data = json.loads(f.read())
    except Exception:
        return
    if not isinstance(data, dict):
        return
    for name, t in data.items():
        if not _ok_name(name) or name in BUILTIN_THEMES or not isinstance(t, dict):
            continue
        colors = t.get("colors")
        if not isinstance(colors, dict):
            continue
        clean = {k: v for k, v in colors.items() if k in COLOR_VARS and _ok_colour(v)}
        if clean:
            EXTRA[name] = {"label": t.get("label") or name, "colors": clean}


_load_extra()
THEMES = BUILTIN_THEMES + tuple(sorted(EXTRA))


def label(name, lang=None):
    """A pack theme's display name, in the visitor's language if given."""
    lab = EXTRA.get(name, {}).get("label", name)
    if isinstance(lab, dict):
        return lab.get(lang) or lab.get("en") or next(iter(lab.values()), name)
    return str(lab)

try:
    from config import THEME as _CONFIGURED
except ImportError:
    _CONFIGURED = "amber"


# The theme an admin chose for everyone from /admin, saved on the board's
# own memory (so it works without an SD card, and an update that keeps
# config.py keeps it too). It wins over config.py's THEME until the admin
# goes back to "as set up", which deletes the file.
SITE_THEME_FILE = "/site_theme.json"


def _load_admin_file():
    try:
        import json
        with open(SITE_THEME_FILE) as f:
            d = json.load(f)
        t = d.get("theme")
        return (t if t in THEMES else None), int(d.get("stamp", 1))
    except Exception:
        return None, 0


def _load_admin_choice():
    return _load_admin_file()[0]


# _STAMP counts the admin's "for everyone" changes. A browser's own pick
# (the per-browser buttons in /admin) is saved with the stamp it was made
# under; once the admin changes the theme for everyone, the stamp moves
# on and every browser drops its older pick (STARTUP_SCRIPT), so "for
# everyone" means everyone -- it used to lose, forever, to any browser
# that had once clicked a per-browser button.
_ADMIN_CHOICE, _STAMP = _load_admin_file()


def _valid(t):
    t = (t or "amber").strip().lower()
    if t == "default":
        return "amber"
    return t if t in THEMES else "amber"


def configured_theme():
    """config.py's THEME (what the Provisioner set up), validated."""
    return _valid(_CONFIGURED)


def admin_choice():
    """The theme an admin chose for everyone, or None."""
    return _ADMIN_CHOICE


def set_site_theme(name):
    """Saves an admin's choice for everyone (None: back to config.py's).
    Returns True if it was saved."""
    global _ADMIN_CHOICE, _STAMP
    import json
    if name is not None and name not in THEMES:
        return False
    try:
        with open(SITE_THEME_FILE, "w") as f:
            json.dump({"theme": name, "stamp": _STAMP + 1}, f)
    except OSError:
        return False
    _ADMIN_CHOICE, _STAMP = name, _STAMP + 1
    return True


def site_stamp():
    return str(_STAMP)


def startup_script():
    """STARTUP_SCRIPT with the current stamp, for each page's <head>."""
    return STARTUP_SCRIPT.replace("STUMP_SITE_STAMP", "'" + str(_STAMP) + "'")


def site_theme():
    """The node's theme, validated: an admin's choice from /admin, else
    config.py's. "default" is accepted as amber (the original look), and
    anything unrecognised falls back to amber rather than leaving pages
    half-styled."""
    return _ADMIN_CHOICE or _valid(_CONFIGURED)


def html_open():
    return "<html data-theme='" + site_theme() + "'>"


# Core variables (used by every page):
#   --bg --panel --text --muted --border --ember --ember-bright
# Extra shades (mostly the chat page):
#   --panel-2   sidebars, bars, tiles -- between --bg and --panel
#   --line      row dividers and hover
#   --dim       system notices, section headers
#   --action    /me lines
#   --dm        private message text   --dm-nick  private message nick
#   --err       error text
# Text on an accent (--ember) background uses --bg in every theme.
CSS = """
:root,[data-theme='amber']{
  --bg:#1b1512; --panel:#2a2119; --ember:#d97a3a; --ember-bright:#f0a050;
  --text:#ecdfc8; --muted:#9c8d76; --border:#493c2e;
  --panel-2:#221b15; --line:#2f271e; --dim:#7d715f; --action:#c8b48f;
  --dm:#c8a2c8; --dm-nick:#d8b4d8; --err:#e07a5a;
}
[data-theme='phosphor']{
  --bg:#0d140e; --panel:#142217; --ember:#33ff66; --ember-bright:#5cff85;
  --text:#d0f0d6; --muted:#5c8f68; --border:#1f3b25;
  --panel-2:#101b12; --line:#1a2d1e; --dim:#4f8a5c; --action:#a8d8b0;
  --dm:#8fd6c8; --dm-nick:#b0e8dc; --err:#ff7a66;
}
[data-theme='oled']{
  --bg:#000000; --panel:#121212; --ember:#4da6ff; --ember-bright:#80bfff;
  --text:#f0f0f0; --muted:#8a8a8a; --border:#2a2a2a;
  --panel-2:#0a0a0a; --line:#1c1c1c; --dim:#7a7a7a; --action:#c8c8c8;
  --dm:#c8a2e8; --dm-nick:#dcbcf5; --err:#ff6b6b;
}
[data-theme='paper']{
  --bg:#f5f2eb; --panel:#e8e3d5; --ember:#a84814; --ember-bright:#c25b1f;
  --text:#2c2825; --muted:#6b6355; --border:#d0c8b6;
  --panel-2:#eee9dd; --line:#ddd6c4; --dim:#6f6657; --action:#5e513b;
  --dm:#7a3f8c; --dm-nick:#5e2f70; --err:#b3261e;
}
"""

# Scroll bars out of sight until used: the thumb shows while something
# scrolls (and for a moment after: STARTUP_SCRIPT adds .scrolling), and
# while the pointer is on it in browsers that style it (Safari, older
# Chrome). Phones already draw overlay bars of their own.
CSS += """
*{scrollbar-width:thin; scrollbar-color:transparent transparent;}
.scrolling{scrollbar-color:var(--dim) transparent;}
::-webkit-scrollbar{width:8px; height:8px; background:transparent;}
::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:transparent;}
::-webkit-scrollbar-thumb{background:transparent; border-radius:4px;}
.scrolling::-webkit-scrollbar-thumb,::-webkit-scrollbar-thumb:hover{background:var(--dim);}
"""

# Pack themes start from amber (any variable they leave out) and override.
CSS += "".join("[data-theme='%s']{%s}\n" % (n, " ".join("%s:%s;" % kv for kv in sorted(t["colors"].items())))
               for n, t in sorted(EXTRA.items()))


# Runs in <head>, before the body renders. The server has already set
# data-theme to the site theme; this only applies a per-browser
# override from /admin, if one exists. Each localStorage access is
# wrapped: private browsing can make it throw outright, and a malformed
# stored value must never take a page down over a cosmetic preference.
STARTUP_SCRIPT = """
(function(){
  // Show a scroll bar only while its box scrolls (see .scrolling in CSS).
  document.addEventListener('scroll', function(e){
    var el = (e.target === document) ? document.documentElement : e.target;
    if (!el || !el.classList) return;
    el.classList.add('scrolling');
    clearTimeout(el._stumpScroll);
    el._stumpScroll = setTimeout(function(){ el.classList.remove('scrolling'); }, 900);
  }, true);
})();
(function(){
  try {
    var t = localStorage.getItem('stump_theme');
    if (t && localStorage.getItem('stump_theme_stamp') !== STUMP_SITE_STAMP) {
      // Picked before the admin last set the theme for everyone: drop it.
      localStorage.removeItem('stump_theme');
      localStorage.removeItem('stump_theme_stamp');
      t = null;
    }
    if (t === 'custom') {
      var c = JSON.parse(localStorage.getItem('stump_custom_colors') || '{}');
      for (var k in c) document.documentElement.style.setProperty(k, c[k]);
    } else if (STUMP_THEME_NAMES.indexOf(t) >= 0) {
      document.documentElement.setAttribute('data-theme', t);
    }
  } catch (e) {}
  try {
    var customSvg = localStorage.getItem('stump_custom_svg');
    var hidden = localStorage.getItem('stump_logo_hidden') === '1';
    if (customSvg || hidden) {
      window.addEventListener('DOMContentLoaded', function(){
        var el = document.getElementById('site-logo');
        if (!el) return;
        if (customSvg) el.innerHTML = customSvg;
        if (hidden) el.style.display = 'none';
      });
    }
  } catch (e) {}
})();
"""

# The names a browser may pick from /admin (built-in and pack themes).
STARTUP_SCRIPT = STARTUP_SCRIPT.replace(
    "STUMP_THEME_NAMES", "[" + ",".join("'" + n + "'" for n in THEMES) + "]")
