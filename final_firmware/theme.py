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

try:
    from config import THEME as _CONFIGURED
except ImportError:
    _CONFIGURED = "amber"


def site_theme():
    """The node's theme, validated. "default" is accepted as amber (the
    original look), and anything unrecognised falls back to amber rather
    than leaving pages half-styled."""
    t = (_CONFIGURED or "amber").strip().lower()
    if t == "default":
        return "amber"
    return t if t in THEMES else "amber"


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

# Runs in <head>, before the body renders. The server has already set
# data-theme to the site theme; this only applies a per-browser
# override from /admin, if one exists. Each localStorage access is
# wrapped: private browsing can make it throw outright, and a malformed
# stored value must never take a page down over a cosmetic preference.
STARTUP_SCRIPT = """
(function(){
  try {
    var t = localStorage.getItem('stump_theme');
    if (t === 'custom') {
      var c = JSON.parse(localStorage.getItem('stump_custom_colors') || '{}');
      for (var k in c) document.documentElement.style.setProperty(k, c[k]);
    } else if (t === 'amber' || t === 'phosphor' || t === 'oled' || t === 'paper') {
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
