# The /admin pages: rendered, and their forms handled. Split out of
# barkeep.py, which loads this the first time someone opens /admin -- most
# boots never do, so these ~1,500 lines aren't in the CAM's memory then.
# barkeep's own helpers are reached as B.<name> (barkeep, imported when a
# page is served: by then it's loaded, so there's no import loop).

import billboard
import features
import fserv
import guard
import i18n
import keepalive
import layout
import plugins
import profiles
import propagation
import radio
import rrc
import settings as stump_settings
import stump_tls as tls
import theme
import uasyncio as asyncio
import ujson as json

import webtext

import barkeep as B


def _admin_logout_form(lang, tok):
    return ("<form method='POST' action='/admin/logout' class='logout' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(tok) + "'>"
            "<button type='submit'>" + i18n.t("admin_logout", lang) + "</button></form>")


def _render_admin_login_page(lang, error=False, wait=0):
    """The ONLY way to reach file deletion now -- no link, no nav tile,
    no button anywhere in the visible UI points here. Reachable only by
    typing the address directly, which is the point: the drawer this
    replaced sat on /files where every visitor saw it exist, even
    collapsed, and a wrong password there had to be re-typed once per
    file. This page asks for the password exactly once regardless of
    how many files get selected next.
    """
    msg = i18n.t("admin_locked", lang, s=wait) if wait else i18n.t("admin_wrong_password", lang)
    err = "<p class='sub' style='color:var(--ember-bright);'>" + msg + "</p>" if error else ""
    return (
        "<h1>" + i18n.t("admin_header", lang) + "</h1>"
        + err +
        "<form method='POST' action='/admin' class='row'>"
        "<input type='password' name='admin_pass' placeholder='"
        + i18n.t("files_admin_password", lang) + "' autofocus>"
        "<button type='submit'>" + i18n.t("admin_login_button", lang) + "</button>"
        "</form>"
    )


def _render_admin_file_list_page(lang, names, pw):
    """Shown only after a correct password. The password travels
    forward as a hidden field on the SAME simple, stateless pattern
    this whole project already uses everywhere else (no sessions, no
    cookies) -- re-validated for real by /admin/delete when the form
    comes back, never trusted just because it arrived in a hidden
    field. One password entry covers selecting as many files as
    wanted; the batch submits together as a single delete.

    Also carries theme and logo customization -- unrelated to file
    deletion, but gated behind the same login rather than a second
    password prompt, since both are "things only an admin should
    change" and asking twice would be the exact kind of friction the
    /admin redesign was built to remove. Shown regardless of whether
    there are any files to delete, unlike the checkbox list below.
    """
    if not names:
        file_section = "<p class='sub'>" + i18n.t("files_none_yet", lang) + "</p>"
    else:
        items = "".join(
            "<li><label><input type='checkbox' name='f' value='" + billboard._esc(n) + "'> "
            + billboard._esc(n) + "</label></li>"
            for n in names
        )
        file_section = (
            "<form method='POST' action='/admin/delete' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<div class='panel'><ul class='files'>" + items + "</ul></div>"
            "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
            "</form>"
        )

    # Sections for features this node doesn't offer are left out (their
    # delete routes answer 404 anyway); theme and logo settings always show.
    files_part = ("<h2 class='sub'>" + i18n.t("admin_files_header", lang) + "</h2>"
                  "<p class='sub'>" + i18n.t("admin_select_intro", lang) + "</p>" + file_section
                  if features.enabled("files") else "")
    board_part = _render_admin_billboard_section(lang, pw) if features.enabled("billboard") else ""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    radio_cur = radio.current()
    hw = radio.hw()
    import chain
    ch = chain.status()
    on_air = (
        _card("radio", "\U0001f4fb", _render_admin_radio_section(lang, pw),
              _radio_peek(hw, radio_cur, lang))
        + _card("chain", "\U0001f517", _render_admin_chain_section(lang), _chain_peek(ch, lang))
        + _card("pn", "\U0001f4ec", _render_admin_pn_section(lang, pw), ""))
    content = (
        _card("files", "\U0001f4c1", files_part, T("admin_peek_files", n=len(names)))
        + _card("board", "\U0001f4cc", board_part, "")
        + _card("home", "\U0001f3e0", _render_admin_home_section(lang, pw), "")
        + _card("tools", "\U0001f9f0", _render_admin_tools_section(lang, pw), ""))
    look = _card("look", "\U0001f3a8", _render_admin_site_theme_section(lang, pw)
                 + _render_admin_layout_section(lang, pw) + _render_admin_settings_section(lang),
                 _theme_label(theme.admin_choice(), lang) if theme.admin_choice() else "")
    system = (
        _card("setup", "\u2699\ufe0f", _render_admin_setup_section(lang, pw), _setup_peek(lang))
        + _card("people", "\U0001f465", _render_admin_profiles_section(lang, pw),
                T("admin_profiles_peek", n=len(profiles.all_profiles())))
        + _card("plugins", "\U0001f9e9", _render_admin_plugins_section(lang, pw), _plugins_peek(lang))
        + _card("https", "\U0001f512", _render_admin_tls_line(lang), "")
        + _card("power", "\u23fb", _render_admin_power_section(lang, pw), ""))
    return (
        "<div class='admin-wide'>"
        "<div class='admin-head'><h1>" + T("admin_header") + "</h1>"
        + _admin_logout_form(lang, pw) + "</div>"
        + B._release_line()
        + _render_admin_health(lang, pw)
        + _admin_tiles(lang, hw, radio_cur, ch, len(names))
        + _group(T("admin_group_air"), on_air)
        + _group(T("admin_group_content"), content)
        + _group(T("admin_group_look"), look)
        + _group(T("admin_group_system"), system)
        + _admin_logout_form(lang, pw)
        + "</div><script>" + B._ADMIN_CARDS_SCRIPT + "</script>"
    )


def _card(cid, icon, body, peek="", open_=False):
    if not body:
        return ""
    title = ""
    if body.startswith("<h2 class='sub'>"):
        end = body.find("</h2>")
        title, body = body[16:end], body[end + 5:]
    return ("<details class='card' id='c-" + cid + "'" + (" open" if open_ else "") + "><summary>"
            "<span class='ic' aria-hidden='true'>" + icon + "</span><span class='tx'><span class='ct'>" + title + "</span>"
            + ("<span class='pk'>" + peek + "</span>" if peek else "")
            + "</span></summary><div class='cb'>" + body + "</div></details>")


def _group(label, cards):
    return ("<h2 class='grp'>" + label + "</h2>" + cards) if cards else ""


def _radio_peek(hw, cur, lang):
    if hw["state"] == "none":
        return i18n.t("admin_peek_noradio", lang)
    txt = i18n.t("rrc_hw_" + hw["state"], lang)
    if cur:
        txt += " · " + radio.hz_to_mhz(cur["frequency"]) + " MHz"
    return "<i class='dot " + hw["state"] + "'></i>" + txt


def _chain_peek(ch, lang):
    if not ch.get("enabled"):
        return ""
    n = len(ch.get("below") or [])
    role = i18n.t("admin_peek_root" if ch["role"] == "root" else "admin_peek_member", lang)
    return role + " · " + i18n.t("admin_peek_below", lang, n=n)


def _plugins_peek(lang):
    try:
        items = plugins.status()
        on = sum(1 for p in items if p.get("state") == "on")
        return i18n.t("admin_peek_plugins", lang, n=on, total=len(items))
    except Exception:
        return ""


def _admin_tiles(lang, hw, cur, ch, nfiles):
    """The Stump at a glance: radio, chain, people in the chat, files --
    each opens its card below."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    tiles = []
    if hw["state"] != "none":
        tiles.append(("radio", "<i class='dot big " + hw["state"] + "'></i>", T("rrc_hw_" + hw["state"]),
                      (radio.hz_to_mhz(cur["frequency"]) + " MHz") if cur else T("admin_tile_radio")))
    else:
        tiles.append(("radio", "\U0001f4fb", T("admin_peek_noradio"), T("admin_tile_radio")))
    if ch.get("enabled"):
        tiles.append(("chain", "\U0001f517", T("admin_peek_root" if ch["role"] == "root" else "admin_peek_member"),
                      T("admin_peek_below", n=len(ch.get("below") or []))))
    try:
        rrc._prune_users()
        people = len(rrc._users)
    except Exception:
        people = 0
    tiles.append(("", "\U0001f465", str(people), T("admin_tile_people")))
    if features.enabled("files"):
        tiles.append(("files", "\U0001f4c1", str(nfiles), T("admin_tile_files")))
    out = []
    for cid, icon, big, small in tiles:
        inner = ("<span class='ti'>" + icon + "</span><b>" + big + "</b><small>" + small + "</small>")
        out.append(("<a class='atile' href='#c-" + cid + "'>" + inner + "</a>") if cid
                   else "<div class='atile'>" + inner + "</div>")
    return "<div class='atiles'>" + "".join(out) + "</div>"


def _theme_label(name, lang):
    """A theme's name for people (plain text: escape it for HTML)."""
    if name in theme.EXTRA:
        return theme.label(name, lang)
    return i18n.t("admin_theme_" + name, lang)


def _render_admin_site_theme_section(lang, pw):
    """The theme everyone gets, chosen here by an admin (theme.py saves it
    on the board's own memory): every built-in and pack theme, and "as
    set up", which goes back to config.py's THEME."""
    current = theme.admin_choice()
    opts = ["<label class='opt'><input type='radio' name='theme' value=''"
            + (" checked" if current is None else "") + "> "
            + billboard._esc(i18n.t("admin_site_theme_setup", lang,
                                    name=_theme_label(theme.configured_theme(), lang)))
            + "</label>"]
    for n in theme.THEMES:
        opts.append("<label class='opt'><input type='radio' name='theme' value='" + B._attr_esc(n) + "'"
                    + (" checked" if current == n else "") + "> " + billboard._esc(_theme_label(n, lang)) + "</label>")
    return (
        "<h2 class='sub'>" + i18n.t("admin_site_theme_header", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("admin_site_theme_intro", lang) + "</p>"
        "<form method='POST' action='/admin/theme' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'>" + "".join(opts) + "</div>"
        "<button type='submit'>" + i18n.t("admin_site_theme_save", lang) + "</button>"
        "</form>"
    )


def _nav_items(lang):
    """[(key, label, icon)] for the navigation row, in layout order:
    the destinations this node offers."""
    out = []
    for k in layout.nav(all=True):
        if k not in B._DESTINATIONS:
            continue
        feat = B._NAV_FEATURE.get(k)
        if feat is not None and not features.enabled(feat):
            continue
        href, icon, label = B._DESTINATIONS[k]
        out.append((k, i18n.t(label, lang), icon))
    return out


def _render_admin_layout_section(lang, pw):
    """The order of the navigation buttons and of the home page's tiles:
    drag them (or use the arrows, on a phone), then save. layout.py."""
    hide, show = i18n.t("admin_layout_hide", lang), i18n.t("admin_layout_show", lang)

    def lst(field, items):
        off = layout.hidden(field)
        return ("<ol class='reorder' data-field='" + field + "'>"
                + "".join("<li draggable='true' data-key='" + B._attr_esc(k) + "'"
                          + (" class='off'" if k in off else "") + ">"
                          "<span class='grip' aria-hidden='true'>⠿</span>" + icon
                          + "<span class='rname'>" + billboard._esc(name) + "</span>"
                          "<button type='button' class='vis'>" + (show if k in off else hide) + "</button>"
                          "<button type='button' class='up' aria-label='↑'>▲</button>"
                          "<button type='button' class='dn' aria-label='↓'>▼</button></li>"
                          for k, name, icon in items)
                + "</ol>")
    tiles = []
    icons = {k: icon for k, f, h, icon, l, sub in B._HOME_TILES}
    for k, name, html in B.home_tile_items(lang, all=True):
        tiles.append((k, name, icons.get(k, B._ICON_PLUGIN)))
    return (
        "<h2 class='sub'>" + i18n.t("admin_layout_header", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("admin_layout_intro", lang) + "</p>"
        "<form method='POST' action='/admin/layout' autocomplete='off' id='layout-form'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<input type='hidden' name='nav' value=''><input type='hidden' name='tiles' value=''>"
        "<input type='hidden' name='nav_hidden' value=''><input type='hidden' name='tiles_hidden' value=''>"
        "<div class='panel'><b>" + i18n.t("admin_layout_nav", lang) + "</b>" + lst("nav", _nav_items(lang)) + "</div>"
        "<div class='panel'><b>" + i18n.t("admin_layout_tiles", lang) + "</b>" + lst("tiles", tiles) + "</div>"
        "<div class='row'><button type='submit'>" + i18n.t("admin_layout_save", lang) + "</button>"
        "<button type='submit' name='reset' value='1'>" + i18n.t("admin_layout_reset", lang) + "</button></div>"
        "</form><script>" + B._LAYOUT_SCRIPT.replace("I18N_HIDE", json.dumps(hide)).replace("I18N_SHOW", json.dumps(show))
        + "</script>"
    )


def _render_admin_tools_section(lang, pw):
    """The Tools page's files, by section, each with a box to tick: a
    whole section, or single files. Deleted from the SD card."""
    if not fserv.sd_ok:
        return ""
    out = "<h2 class='sub'>" + i18n.t("admin_tools_header", lang) + "</h2>"
    secs = fserv.tool_sections(lang)
    if not secs:
        return out + "<p class='sub'>" + i18n.t("admin_tools_none", lang) + "</p>"
    rows = []
    for sec in secs:
        d = sec["dir"]
        prefix = d + "/" if d else ""
        if d:
            rows.append("<li><label><input type='checkbox' name='d' value='" + B._attr_esc(d) + "'> <b>"
                        + billboard._esc(sec["title"]) + "</b> — "
                        + i18n.t("admin_tools_whole", lang, n=len(sec["files"])) + "</label></li>")
        else:
            rows.append("<li><b>" + i18n.t("tools_other_h2", lang) + "</b></li>")
        for n, size in sec["files"]:
            rows.append("<li><label>&nbsp;&nbsp;&nbsp;<input type='checkbox' name='f' value='"
                        + B._attr_esc(prefix + n) + "'> " + billboard._esc(n)
                        + "</label><small>" + B._human_size(size) + "</small></li>")
    return out + (
        "<p class='sub'>" + i18n.t("admin_tools_intro", lang) + "</p>"
        "<form method='POST' action='/admin/tools' autocomplete='off' onsubmit=\"return confirm("
        + B._attr_esc(json.dumps(i18n.t("admin_tools_confirm", lang))) + ")\">"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'><ul class='files'>" + "".join(rows) + "</ul></div>"
        "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
        "</form>"
    )


def _render_admin_tls_line(lang):
    """HTTPS status: on/off and why, and how long the certificate has
    left -- every node falls back to plain HTTP the day it expires."""
    import time
    st = tls.status()
    head = "<h2 class='sub'>HTTPS</h2>"
    if not st["active"]:
        why = i18n.t("admin_tls_why_" + (st.get("code") or "failed"), lang, detail=st.get("detail") or st["reason"])
        return head + "<p class='sub'>" + i18n.t("admin_tls_off", lang, reason=billboard._esc(why)) + "</p>"
    try:
        import propagation
        now = propagation.unix_now()
    except Exception:
        import time as _time
        now = int(_time.time())
    # The node's clock may be unset (off-grid, no NTP): then "days left"
    # would be nonsense, so show the expiry date and skip the count.
    clock_ok = now >= 1735689600
    days = tls.days_left(now) if clock_ok else None
    try:
        import propagation as _pn
        y, mo, d = time.gmtime(int(st["not_after"]) - _pn.EPOCH_OFFSET)[:3]
        until = "%04d-%02d-%02d" % (y, mo, d)
    except Exception:
        until = "?"
    line = i18n.t("admin_tls_on", lang, host=billboard._esc(st["host"] or "?"), until=until,
                  days=days if days is not None else "?")
    if days is not None and days < 21:
        line = "<b>" + line + " — " + i18n.t("admin_tls_renew", lang) + "</b>"
    return head + "<p class='sub'>" + line + "</p>"


def _render_admin_chain_section(lang):
    """A chain of Stumps over Wi-Fi (chain.py): this Stump's place in it.
    Shown only on a Stump in a chain."""
    import chain
    st = chain.status()
    if not st["enabled"]:
        return ""
    e = billboard._esc
    lines = []
    if st["role"] == "root":
        lines.append(i18n.t("admin_chain_root", lang, ip=st["ap_ip"],
                            channel=(" · Wi-Fi %d" % st["channel"]) if st["channel"] else ""))
    else:
        up = st["uplink"]
        if up["ip"]:
            heard = (i18n.t("admin_chain_heard", lang, s=up["parent_heard_s"])
                     if up["parent_heard_s"] is not None else "")
            lines.append(i18n.t("admin_chain_member", lang, ssid=e(up["ssid"]), ip=up["ip"],
                                parent=up["parent"], heard=heard))
            if up.get("parent_verified") is False:
                lines.append("<b>" + i18n.t("admin_chain_parent_plain", lang) + "</b>")
        else:
            lines.append(i18n.t("admin_chain_trying", lang, ssid=e(up["ssid"])))
    lines.append(i18n.t("admin_chain_below", lang, list=", ".join(st["below"])) if st["below"]
                 else i18n.t("admin_chain_none_below", lang))
    if not st["secret"]:
        lines.append("<b>" + i18n.t("admin_chain_nosecret", lang) + "</b>")
    for r in st.get("refused", []):
        lines.append("<b>" + i18n.t("admin_chain_refused", lang, ip=e(r["ip"]), n=r["count"],
                                    ago=_ago(r["last_s"], lang)) + "</b>")
    # The other Stumps, and whether they speak the same chain protocol.
    mine = st.get("proto") or chain.PROTO
    rows = []
    for s in st.get("stumps", [])[:12]:
        rel = e(s["release"]) if s["release"] else i18n.t("admin_chain_older", lang)
        row = e(s["name"] or s["lxmf"][:8]) + " &middot; " + rel
        if s["proto"] < mine:
            row += " &middot; <b>" + i18n.t("admin_chain_update_it", lang) + "</b>"
        elif s["proto"] > mine:
            row += " &middot; <b>" + i18n.t("admin_chain_update_me", lang) + "</b>"
        rows.append(row)
    if rows:
        lines.append(i18n.t("admin_chain_stumps", lang) + "<br>" + "<br>".join(rows))
    return ("<h2 class='sub'>" + i18n.t("admin_chain_header", lang) + "</h2>"
            "<p class='sub'>" + "<br>".join(lines) + "</p>")


def _ago(s, lang):
    return i18n.t("admin_chain_ago_min", lang, n=s // 60) if s >= 60 else i18n.t("admin_chain_ago_s", lang, n=s)


def _render_admin_plugins_section(lang, pw):
    """Plugins (plugins.py): what's installed, running or off, the RAM
    each took to start, the last error, and an on/off switch saved
    across restarts. Also says when the last boot went into safe mode."""
    items = plugins.status()
    out = "<h2 class='sub'>" + i18n.t("admin_plugins_header", lang) + "</h2>"
    safe = plugins.safe_mode()
    if safe["culprit"]:
        out += "<p class='sub'><b>" + i18n.t("admin_plugins_culprit", lang, name=billboard._esc(safe["culprit"])) + "</b></p>"
    elif safe["on"]:
        out += "<p class='sub'><b>" + i18n.t("admin_plugins_safe", lang) + "</b></p>"
    if not items:
        return out + "<p class='sub'>" + i18n.t("plugin_none", lang) + "</p>"
    hid = "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
    rows = []
    for p in items:
        on = p["state"] == "on"
        meta = []
        if p["version"]:
            meta.append("v" + billboard._esc(str(p["version"])))
        if p["mem"] is not None:
            meta.append(B._human_size(p["mem"]) + " RAM")
        line = ("<div><span class='pname'>" + billboard._esc(p["name"]) + "</span> "
                "<span class='pstate'>— " + i18n.t("admin_plugins_state_" + p["state"].replace("-", "_"), lang)
                + "</span>"
                + ("<span class='pmeta'>" + " · ".join(meta) + "</span>" if meta else "")
                + ("<span class='pcmds'>" + " ".join("/" + c for c in p["commands"]) + "</span>"
                   if p["commands"] else "")
                + ("<span class='perr'>" + billboard._esc(p["error"]) + "</span>" if p["error"] else "")
                + "</div>")
        button = ""
        if not (on and p["name"] in plugins.PROTECTED):
            button = ("<form method='POST' action='/admin/plugins' autocomplete='off'>" + hid +
                      "<input type='hidden' name='name' value='" + B._attr_esc(p["name"]) + "'>"
                      "<input type='hidden' name='action' value='" + ("off" if on else "on") + "'>"
                      "<button type='submit'>" + i18n.t("admin_plugins_off" if on else "admin_plugins_on", lang)
                      + "</button></form>")
        rows.append("<li>" + line + button + "</li>")
    return out + "<div class='panel'><ul class='plist'>" + "".join(rows) + "</ul></div>"


def _render_admin_home_section(lang, pw):
    """The home page's own section (docs/HOME_BRANDING.md): what the node
    sees in the card's home/ folder, and where to put it -- above, under,
    or beside the buttons -- its frame height, and whether to show it.
    Saved to home/home.json, so the panel and the file always agree."""
    cfg = fserv.home_config()
    out = ("<h2 class='sub'>" + i18n.t("admin_home_header", lang) + "</h2>"
           "<p class='sub'>" + billboard._esc(fserv.home_status(lang)) + "</p>")
    if not fserv.sd_ok:
        return out
    radios = "".join(
        "<label class='opt'><input type='radio' name='place' value='" + v + "'"
        + (" checked" if cfg["place"] == v else "") + "> " + i18n.t(k, lang) + "</label>"
        for v, k in (("top", "admin_home_top"), ("bottom", "admin_home_bottom"),
                     ("left", "admin_home_left"), ("right", "admin_home_right")))
    return out + (
        "<form method='POST' action='/admin/home' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<fieldset class='opts'><legend>" + i18n.t("admin_home_place", lang) + "</legend>" + radios + "</fieldset>"
        "<label>" + i18n.t("admin_home_height", lang) + "<br>"
        "<input name='height' type='number' min='80' max='1200' value='" + str(cfg["height"]) + "'></label>"
        "<small>" + i18n.t("admin_home_height_note", lang) + "</small>"
        "<label class='opt'><input type='checkbox' name='show' value='1'" + ("" if cfg["hidden"] else " checked") + "> "
        + i18n.t("admin_home_show", lang) + "</label>"
        "<button type='submit'>" + i18n.t("admin_home_save", lang) + "</button></form>"
    )


def _render_admin_pn_section(lang, pw):
    """LXMF propagation node (propagation.py): switch, status, and the two
    single-phone test tools."""
    st = propagation.status()
    hid = "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
    head = ("<h2 class='sub'>" + i18n.t("admin_pn_header", lang) + "</h2>"
            "<p class='sub'>" + i18n.t("admin_pn_intro", lang) + "</p>")
    toggle = ("<form method='POST' action='/admin/propagation' autocomplete='off'>" + hid +
              "<input type='hidden' name='action' value='" + ("off" if st["enabled"] else "on") + "'>"
              "<button type='submit'>" + i18n.t("admin_pn_disable" if st["enabled"] else "admin_pn_enable", lang)
              + "</button></form>")
    if not st["enabled"]:
        return head + "<p class='sub'>" + i18n.t("admin_pn_off_state", lang) + "</p>" + toggle
    s = st["stats"]
    body = ("<p class='sub'>" + i18n.t("admin_pn_on_state", lang) + " <code>" + (st["address"] or "") + "</code><br>"
            + i18n.t("admin_pn_counts", lang, stored=st["stored"], checking=st["checking"],
                     valid=s["valid"], invalid=s["invalid"], served=s["served"]) + "</p>")
    inbox = propagation.mailbox_inbox()
    mailbox = ("<p class='sub'>" + i18n.t("admin_pn_mailbox", lang) + " <code>" + (st["mailbox"] or "") + "</code></p>"
               + ("<ul>" + "".join("<li>" + billboard._esc(m["text"]) + " <small>&mdash; " + m["from"][:8]
                                   + "…</small></li>" for m in reversed(inbox)) + "</ul>"
                  if inbox else "<p class='sub'><small>" + i18n.t("admin_pn_mailbox_none", lang) + "</small></p>"))
    leave = ("<form method='POST' action='/admin/propagation' class='post-form' autocomplete='off'>" + hid +
             "<input type='hidden' name='action' value='leave'>"
             "<label>" + i18n.t("admin_pn_leave_to", lang) + "<br><input name='to' maxlength='32' "
             "placeholder='53d91d2f…'></label>"
             "<label>" + i18n.t("admin_pn_leave_text", lang) + "<br><input name='text' maxlength='200'></label>"
             "<button type='submit'>" + i18n.t("admin_pn_leave_button", lang) + "</button></form>")
    return head + body + toggle + mailbox + leave


def _render_admin_radio_live(lang, heading=True):
    """The radio, live: whether the RNode answered, its firmware, the
    settings it echoed back, signal of the last packet, noise floor,
    channel load, battery -- refreshed every 2 s from /hw.json."""
    if not radio.bridges():
        return ""
    labels = {k: i18n.t("admin_hw_" + k, lang) for k in (
        "title", "none", "where", "error", "set", "echo", "on", "noise", "load", "airtime",
        "battery", "temp", "counts", "last", "since")}
    labels.update({k: i18n.t("rrc_hw_" + k, lang) for k in ("online", "connecting", "error")})
    return (("<h2 class='sub'>" + labels["title"] + "</h2>" if heading else "")
            + "<style>.hwt{border-collapse:collapse;margin:6px 0 14px;font-size:.85rem}"
            ".hwt caption{text-align:left;font-weight:bold;padding:4px 0}"
            ".hwt th{text-align:left;color:var(--muted);font-weight:normal;padding:2px 12px 2px 0;white-space:nowrap}"
            ".hwt td{padding:2px 0}.hw-dot{display:inline-block;width:10px;height:10px;border-radius:50%;"
            "background:#f2efe8;border:1px solid var(--border)}.hw-dot.online{background:#3fb950}"
            ".hw-dot.connecting{background:#f0a030}.hw-dot.error{background:#e5534b}</style>"
            "<div id='hwlive'><p class='sub'>…</p></div>"
            + B._HW_LIVE_SCRIPT.replace("HW_LABELS", json.dumps(labels)))


def _host():
    """The computer this Stump runs on (pc_compat's stump_host), or None on
    a board."""
    try:
        import stump_host
        return stump_host if getattr(stump_host, "IS_PC", False) else None
    except ImportError:
        return None


def _render_admin_power_section(lang, pw):
    """On a computer: turn this Stump off (it runs in the background, from
    the login service). Started again from the Start file in its folder,
    or at the next login."""
    h = _host()
    restart = ("<p class='sub'>" + i18n.t("admin_restart_intro", lang) + "</p>" + _render_admin_restart_form(lang, pw))
    if h is None:
        return "<h2 class='sub'>" + i18n.t("admin_power_header_cam", lang) + "</h2>" + restart
    where = billboard._esc(getattr(h, "START_FILE", "Start Stump"))
    return ("<h2 class='sub'>" + i18n.t("admin_power_header", lang) + "</h2>" + restart +
            "<form method='POST' action='/admin/poweroff' class='post-form' onsubmit=\"return confirm("
            + billboard._esc(json.dumps(i18n.t("admin_power_confirm", lang))) + ")\">"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<p class='sub'>" + i18n.t("admin_power_intro", lang, start=where) + "</p>"
            "<button type='submit'>" + i18n.t("admin_power_off", lang) + "</button></form>")


def _setup_peek(lang):
    s = stump_settings.current()
    bits = [billboard._esc(s["NODE_NAME"]), i18n.t("admin_setup_" + stump_settings.economy(s), lang).split(" — ")[0]]
    if stump_settings.has_access_modes():
        bits.append(i18n.t("admin_setup_" + s["AUTH_MODE"], lang).split(" — ")[0])
    return " · ".join(bits)


def _render_admin_setup_section(lang, pw, notice=""):
    """The Provisioner's answers, changed in place (settings.py): name and
    bot, the file-sharing economy, who may enter, the features. Folded
    groups, one Save; what waits for a restart says so, with the button."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    S = stump_settings
    s = S.current()
    esc = billboard._esc
    eco = S.economy(s)

    def radio_row(name, value, checked, label):
        return ("<label class='opt'><input type='radio' name='" + name + "' value='" + value + "'"
                + (" checked" if checked else "") + "> " + label + "</label>")

    names = ("<details class='inner'><summary>" + T("admin_setup_names") + " <span class='pk'>" + esc(s["NODE_NAME"])
             + " · " + esc(s["BOT_NAME"]) + "</span></summary>"
             "<label>" + T("admin_setup_node") + "<br><input name='node_name' maxlength='" + str(S.NAME_MAX)
             + "' required value='" + esc(s["NODE_NAME"]) + "'></label>"
             "<p class='sub'><small>" + T("admin_setup_node_note") + "</small></p>"
             "<label>" + T("admin_setup_bot") + "<br><input name='bot_name' maxlength='" + str(S.BOT_MAX)
             + "' required value='" + esc(s["BOT_NAME"]) + "'></label>"
             "<label>" + T("admin_setup_greeting", n=S.GREETING_MAX) + "<br><textarea name='greeting' rows='3' maxlength='"
             + str(S.GREETING_MAX) + "'>" + esc(s["MESH_GREETING"]) + "</textarea></label></details>")
    w = s["CREDIT_WEIGHTS"]
    files = ("<details class='inner'><summary>" + T("admin_setup_files") + " <span class='pk'>"
             + T("admin_setup_" + eco).split(" — ")[0] + "</span></summary>"
             + radio_row("economy", "free", eco == "free", T("admin_setup_free"))
             + radio_row("economy", "standard", eco == "standard", T("admin_setup_standard"))
             + radio_row("economy", "custom", eco == "custom", T("admin_setup_custom"))
             + "<div class='weights' id='setupw'><p class='sub'><small>" + T("admin_setup_weights") + "</small></p>"
             + "".join("<label>" + T("admin_setup_w_" + k) + "<br><input name='w_" + k + "' type='number' min='0' max='"
                       + str(S.WEIGHT_MAX) + "' value='" + str(w.get(k, 1)) + "'></label>" for k in S.WEIGHT_CLASSES)
             + "</div></details>")
    access = ""
    if S.has_access_modes():
        access = ("<details class='inner'><summary>" + T("admin_setup_access") + " <span class='pk'>"
                  + T("admin_setup_" + s["AUTH_MODE"]).split(" — ")[0] + "</span></summary>"
                  + "".join(radio_row("auth_mode", m, s["AUTH_MODE"] == m, T("admin_setup_" + m)) for m in S.MODES)
                  + "</details>")
    on = s["FEATURES"]
    feats = ("<details class='inner'><summary>" + T("admin_setup_features") + " <span class='pk'>"
             + (" · ".join(T(B._FEATURE_LABELS[f]) for f in on) or T("admin_setup_f_none")) + "</span></summary>"
             + "".join("<label class='opt'><input type='checkbox' name='f_" + f + "' value='1'"
                       + (" checked" if f in on else "") + "> " + T(B._FEATURE_LABELS[f]) + "</label>" for f in S.FEATURES)
             + "<label class='opt'><input type='checkbox' name='librarian' value='1'"
             + (" checked" if s["LIBRARIAN"] else "") + "> " + T("admin_setup_librarian", bot=esc(s["BOT_NAME"])) + "</label>"
             + "</details>")
    store = ""
    st = S.storage()
    if st is not None:
        # A computer's "card" (the sd/ folder): its size, what's in it, the disk.
        share = int(st["mb"] * fserv.MAX_SD_RATIO) * S.MB
        store = ("<details class='inner'><summary>" + T("admin_setup_storage") + " <span class='pk'>"
                 + B._human_size(st["mb"] * S.MB) + "</span></summary>"
                 "<label>" + T("admin_setup_storage_mb") + "<br><input name='storage_mb' type='number' min='"
                 + str(S.STORAGE_MIN_MB) + "' max='" + str(st["max_mb"]) + "' step='256' value='" + str(st["mb"]) + "'></label>"
                 "<p class='sub'><small>" + T("admin_setup_storage_note", share=B._human_size(share),
                                              used=B._human_size(st["used"]), free=B._human_size(st["disk_free"]),
                                              pct=int(fserv.MAX_SD_RATIO * 100)) + "</small></p>"
                 "</details>")
    comp = ""
    aw = S.awake()
    if aw is not None:
        # This computer: kept awake while the Stump runs (caffeinate, ...).
        state = ("admin_setup_awake_on" if aw["how"] else "admin_setup_awake_failed" if aw["on"]
                 else "admin_setup_awake_off")
        comp = ("<details class='inner'><summary>" + T("admin_setup_computer") + " <span class='pk'>"
                + T(state) + "</span></summary>"
                "<input type='hidden' name='computer' value='1'>"
                "<label class='opt'><input type='checkbox' name='keep_awake' value='1'"
                + (" checked" if aw["on"] else "") + (" disabled" if not aw["possible"] and not aw["on"] else "")
                + "> " + T("admin_setup_keep_awake") + "</label>"
                "<p class='sub'><small>" + T("admin_setup_keep_awake_note")
                + ("" if aw["possible"] else " " + T("admin_setup_awake_none")) + "</small></p>"
                "</details>")
    return ("<h2 class='sub'>" + T("admin_setup_header") + "</h2>" + notice
            + "<p class='sub'>" + T("admin_setup_intro") + "</p>"
            "<form method='POST' action='/admin/setup' class='post-form setup' autocomplete='off'>"
            "<input type='hidden' name='admin_pass' value='" + esc(pw) + "'>"
            + names + files + store + comp + access + feats
            + "<button type='submit'>" + T("admin_setup_save") + "</button></form>"
            "<script>(function(){var w=document.getElementById('setupw');function f(){"
            "var c=document.querySelector(\"input[name=economy]:checked\");var on=c&&c.value==='custom';"
            "w.style.opacity=on?'':'.5';w.querySelectorAll('input').forEach(function(i){i.disabled=!on;});}"
            "document.querySelectorAll('input[name=economy]').forEach(function(r){r.onchange=f;});f();})();</script>")


def _render_admin_profiles_section(lang, pw):
    """The registered profiles (profiles.py), shared by the chain: each
    one's balance; a new password (there's no email to reset it by), or
    removing it (on every Stump of the chain)."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    esc = billboard._esc
    rows = []
    for r in profiles.all_profiles():
        n = r["nick"]
        rows.append("<details class='inner'><summary>" + esc(n) + " <span class='pk'>"
                    + T("profile_balance", n=profiles.balance(n)) + "</span></summary>"
                    "<form method='POST' action='/admin/profiles' class='post-form setup' autocomplete='off'>"
                    "<input type='hidden' name='admin_pass' value='" + esc(pw) + "'>"
                    "<input type='hidden' name='nick' value='" + esc(n) + "'>"
                    "<label>" + T("admin_profiles_newpw", n=profiles.PW_MIN) + "<br><input type='text' name='pw' minlength='"
                    + str(profiles.PW_MIN) + "' autocomplete='off'></label>"
                    "<button type='submit' name='action' value='reset'>" + T("admin_profiles_reset") + "</button> "
                    "<button type='submit' name='action' value='delete' onclick=\"return confirm("
                    + esc(json.dumps(T("admin_profiles_delete_confirm", nick=n))) + ")\">"
                    + T("admin_profiles_delete") + "</button></form></details>")
    return ("<h2 class='sub'>" + T("admin_profiles_header") + "</h2>"
            "<p class='sub'>" + T("admin_profiles_intro") + "</p>"
            + ("".join(rows) if rows else "<p class='sub'>" + T("admin_profiles_none") + "</p>"))


def _render_admin_restart_form(lang, pw):
    return ("<form method='POST' action='/admin/restart' class='post-form' onsubmit=\"return confirm("
            + billboard._esc(json.dumps(i18n.t("admin_setup_restart_confirm", lang))) + ")\">"
            "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
            "<button type='submit'>" + i18n.t("admin_setup_restart", lang) + "</button></form>")


async def _restart_soon():
    """After the answer has gone out: start the Stump again (from a rescue
    start: a normal start, which is what restarting it is asked for)."""
    import uasyncio as asyncio
    await asyncio.sleep(1)
    if keepalive.mode() == "rescue":
        keepalive.try_normal()          # (restarts it)
        return
    stump_settings.restart()


def _render_admin_health(lang, pw):
    """At the top of /admin: a rescue start (what failed, when a normal
    start is tried again, the button to try it now), or a note that the
    watchdog restarted the Stump. Nothing when all is well."""
    T = lambda k, **kw: i18n.t(k, lang, **kw)
    st = keepalive.status()
    esc = billboard._esc
    if st["mode"] == "rescue":
        return ("<div class='panel rescue'><h2 class='sub'>" + T("admin_rescue_header") + "</h2>"
                "<p class='sub'>" + T("admin_rescue_text", n=st["failed_starts"],
                                      why=esc(str(st["last_failure"] or "?")),
                                      min=(st["retry_in_s"] or 0) // 60 + 1) + "</p>"
                + _render_admin_restart_form(lang, pw).replace(
                    T("admin_setup_restart") + "</button>", T("admin_rescue_try") + "</button>")
                + "</div>")
    if st["cause"] == "watchdog":
        return "<p class='sub'><b>" + T("admin_watchdog_note") + "</b></p>"
    return ""


async def _power_off_soon():
    """After the answer has gone out: turn the Stump off."""
    import uasyncio as asyncio
    await asyncio.sleep(1)
    _host().power_off()


def _render_admin_bt(lang, pw=""):
    """How the Heltec is reached, folded ("Transport: USB"): the USB /
    Bluetooth switch, "Reconnect the radio", and its Bluetooth from /admin,
    no terminal: on, off, and pairing mode with the PIN shown here (polled
    from /admin/bt.json, never in the public status)."""
    T = lambda k: i18n.t(k, lang)
    btn = lambda a, k: "<button type='button' data-bt='" + a + "'>" + T(k) + "</button>"
    now = radio.link()
    seg = ""
    if now:
        # A Heltec on this computer: USB or Bluetooth, switched live.
        seg = ("<div class='seg' role='group' aria-label='" + T("admin_link_title") + "'>"
               + "".join("<button type='button' data-link='" + k + "' aria-pressed='" + ("true" if now == k else "false")
                         + "'" + (" class='on'" if now == k else "") + ">" + lbl + "</button>"
                         for k, lbl in (("usb", "USB"), ("ble", "Bluetooth")))
               + "</div><p class='sub'>" + T("admin_link_" + now) + "</p>")
    how = {"usb": "USB", "ble": "Bluetooth"}.get(now) or "Wi-Fi"
    retry = ("<form method='POST' action='/admin/radioretry' class='post-form inline'>"
             "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
             "<p class='sub'>" + T("admin_radio_retry_intro") + "</p>"
             "<button type='submit'>" + T("admin_radio_retry") + "</button></form>")
    return ("<details class='inner' id='bt'" + (" open" if now == "ble" else "") + "><summary>" + T("admin_transport")
            + " <span class='pk'>" + how + "</span></summary>"
            + seg + retry +
            "<p class='sub'>" + T("admin_bt_intro") + "</p>"
            "<div class='btrow'>" + (btn("computer", "admin_bt_computer") if now == "usb" else "")
            + (btn("pair", "admin_bt_pair") if now != "ble" else "") + btn("on", "admin_bt_on") + btn("off", "admin_bt_off")
            + btn("unpair", "admin_bt_unpair")
            + "</div>" + ("<p class='sub'>" + T("admin_bt_ble_note") + "</p>" if now == "ble" else "")
            + "<p class='sub' id='btmsg' aria-live='polite'></p><div id='btpin' hidden></div>"
            "<p class='sub' id='btprobe'></p>"
            "<details class='tips'><summary>" + T("admin_bt_help") + "</summary><p class='sub'>" + T("admin_bt_tips")
            + "</p><p class='sub'><small>" + T("admin_bt_heard") + " <code id='btheard'>\u2014</code></small></p></details></details>"
            "<script>" + B._BT_SCRIPT.replace("BT_LABELS", json.dumps({
                "pairing": T("admin_bt_pairing"), "pin": T("admin_bt_pin"), "gone": T("admin_bt_gone"),
                "switching": T("admin_link_switching"), "computer": T("admin_bt_computer_go"),
                "searching": T("admin_bt_probe_searching"), "connected": T("admin_bt_probe_connected"),
                "paired": T("admin_bt_probe_paired"), "left": T("admin_bt_left"),
                "unpair": T("admin_bt_unpair_confirm")})) + "</script>")


def _render_admin_radio_section(lang, pw):
    """The Heltec Bridge's radio settings (radio.py). Shows what the
    bridge is using right now; applying sends them to the Heltec at once
    if it's connected, and saves them so they survive a reboot."""
    cur = radio.current()
    head = "<h2 class='sub'>" + i18n.t("admin_radio_header", lang) + "</h2>"
    if cur is None:
        return head + "<p class='sub'>" + i18n.t("admin_radio_none", lang) + "</p>"

    def opts(values, selected, label=str):
        return "".join("<option value='%d'%s>%s</option>" % (v, " selected" if v == selected else "", label(v))
                       for v in values)

    def field(label_key, control):
        return "<label>" + i18n.t(label_key, lang) + "<br>" + control + "</label>"

    settings = (
        "<p class='sub'>" + i18n.t("admin_radio_intro", lang) + "</p>"
        "<form method='POST' action='/admin/radio' class='post-form' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        + field("radio_txp", "<input name='txp' type='number' min='%d' max='%d' value='%d'>"
                % (radio.TXP_MIN, radio.TXP_MAX, cur["txpower"]))
        + field("radio_freq", "<input name='freq' inputmode='decimal' value='" + radio.hz_to_mhz(cur["frequency"]) + "'>")
        + field("radio_bw", "<select name='bw'>" + opts(radio.BANDWIDTHS, cur["bandwidth"],
                lambda v: ("%g" % (v / 1000)) + " kHz") + "</select>")
        + field("radio_sf", "<select name='sf'>" + opts(range(radio.SF_MIN, radio.SF_MAX + 1), cur["sf"]) + "</select>")
        + field("radio_cr", "<select name='cr'>" + opts(range(radio.CR_MIN, radio.CR_MAX + 1), cur["cr"],
                lambda v: "4/%d" % v) + "</select>")
        + "<button type='submit'>" + i18n.t("admin_radio_save", lang) + "</button>"
        "</form>")
    return (
        head
        + _render_admin_radio_live(lang, heading=False) +
        "<details class='inner'><summary>" + i18n.t("admin_radio_settings", lang) + " <span class='pk'>"
        + radio.hz_to_mhz(cur["frequency"]) + " MHz · " + ("%g" % (cur["bandwidth"] / 1000)) + " kHz · SF"
        + str(cur["sf"]) + " · CR4/" + str(cur["cr"]) + " · " + str(cur["txpower"]) + " dBm</span></summary>"
        + settings + "</details>"
        + _render_admin_bt(lang, pw) +
        "<form method='POST' action='/admin/radiochat' class='post-form'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<input type='hidden' name='on' value='" + ("0" if radio.chat_edit() else "1") + "'>"
        "<p class='sub'>" + i18n.t("admin_radio_chat_on" if radio.chat_edit() else "admin_radio_chat_off", lang) + "</p>"
        "<button type='submit'>" + i18n.t("admin_radio_chat_turn_off" if radio.chat_edit() else "admin_radio_chat_turn_on", lang)
        + "</button></form>"
    )


def _render_admin_billboard_section(lang, pw):
    """Moderation for the walk-up bulletin board -- added on request,
    specifically for removing something inappropriate rather than
    waiting up to 72 hours for BILLBOARD_TTL_SECONDS to age it out on
    its own. Same password-gated pattern as file deletion above: one
    hidden field carries the already-validated password forward,
    checked again for real by /admin/delete_post rather than trusted
    just because it arrived with the form.

    Each post is identified by its own timestamp (see
    billboard.delete_entry's own docstring for why that's a reasonable
    identifier here without adding a separate ID field to the storage
    format), carried as the checkbox's value -- HTML-escaped for safe
    embedding, not URL-encoded, the same fix that corrected the file
    checkboxes after a real double-encoding bug there.

    Calls billboard._prune_and_write() first, with no new entry, purely
    to guarantee every entry has a REAL, stored timestamp before any
    checkbox is built. Without this, a post that predates the
    auto-purge feature and hasn't been through a prune pass yet (the
    board's very first admin visit after upgrading, before anyone has
    posted since) reads back from _read_entries() with ts=None --
    which rendered as a checkbox value='None', and deleting it always
    silently failed: delete_entry() can only ever match a stored line
    with a real, three-field timestamp, and an old, never-pruned entry
    is still stored as its original two-field line with no timestamp
    at all. Confirmed directly: that exact sequence reproduced "0
    deleted" with the entry still present, matching a real report.
    Pruning here first means _read_entries() right after always sees
    the same freshly-stamped timestamp that gets written to disk, so
    the two can never disagree.
    """
    billboard._prune_and_write()
    entries = billboard._read_entries()
    if not entries:
        return (
            "<h2 class='sub'>" + i18n.t("admin_billboard_header", lang) + "</h2>"
            "<p class='sub'>" + i18n.t("billboard_nothing_yet", lang) + "</p>"
        )
    items = "".join(
        "<li><label><input type='checkbox' name='ts' value='" + billboard._esc(pid) + "'> "
        + "<span>" + billboard._esc(title) + " <small>&mdash; " + billboard._esc(sig) + "</small>"
        # Full body, not collapsed: a moderator needs to see exactly what
        # they're about to remove, which is often in the body, not the title.
        + ("<span class='post-body'>" + billboard._esc(body) + "</span>" if body else "")
        + "</span></label></li>"
        for sig, title, body, ts, pid in reversed(entries)
    )
    return (
        "<h2 class='sub'>" + i18n.t("admin_billboard_header", lang) + "</h2>"
        "<form method='POST' action='/admin/delete_post' autocomplete='off'>"
        "<input type='hidden' name='admin_pass' value='" + billboard._esc(pw) + "'>"
        "<div class='panel'><ul class='files'>" + items + "</ul></div>"
        "<button type='submit'>" + i18n.t("admin_delete_selected", lang) + "</button>"
        "</form>"
    )


def _render_admin_settings_section(lang):
    """Theme presets, a custom color palette, and SVG logo controls --
    all of it client-side, persisted in the ADMIN'S OWN BROWSER via
    localStorage, not written to the server or the SD card anywhere.
    Worth being direct about since it's easy to assume otherwise for a
    'branding' feature: setting a theme or logo here changes what this
    one browser sees on its own next visit. It does not change what
    any other visitor sees, and there is currently no way to make a
    theme or logo choice apply site-wide to everyone -- that would be
    server-side storage, which this deliberately isn't.
    """
    return (
        "<h2 class='sub'>" + i18n.t("admin_theme_header", lang) + "</h2>"
        "<p class='sub'>" + i18n.t("admin_theme_browser_intro", lang) + "</p>"
        "<div class='panel'>"
        "<div class='row'>"
        "<button type='button' onclick=\"stumpSetTheme('site')\">" + i18n.t("admin_theme_site", lang)
        + " (" + (billboard._esc(theme.label(theme.site_theme(), lang)) if theme.site_theme() in theme.EXTRA
                  else i18n.t("admin_theme_" + theme.site_theme(), lang)) + ")</button>"
        "<button type='button' onclick=\"stumpSetTheme('amber')\">" + i18n.t("admin_theme_amber", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('phosphor')\">" + i18n.t("admin_theme_phosphor", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('oled')\">" + i18n.t("admin_theme_oled", lang) + "</button>"
        "<button type='button' onclick=\"stumpSetTheme('paper')\">" + i18n.t("admin_theme_paper", lang) + "</button>"
        + "".join("<button type='button' onclick=\"stumpSetTheme('" + n + "')\">"
                  + billboard._esc(theme.label(n, lang)) + "</button>" for n in sorted(theme.EXTRA)) +
        "</div>"
        "<p class='sub'>" + i18n.t("admin_theme_custom_intro", lang) + "</p>"
        "<div class='row'>"
        "<label>" + i18n.t("admin_color_bg", lang) + " <input type='color' id='stump-c-bg' value='#1b1512'></label>"
        "<label>" + i18n.t("admin_color_panel", lang) + " <input type='color' id='stump-c-panel' value='#2a2119'></label>"
        "<label>" + i18n.t("admin_color_text", lang) + " <input type='color' id='stump-c-text' value='#ecdfc8'></label>"
        "<label>" + i18n.t("admin_color_ember", lang) + " <input type='color' id='stump-c-ember' value='#d97a3a'></label>"
        "<label>" + i18n.t("admin_color_border", lang) + " <input type='color' id='stump-c-border' value='#493c2e'></label>"
        "</div>"
        "<div class='row'>"
        "<button type='button' onclick='stumpSaveCustomPalette()'>" + i18n.t("admin_save_palette", lang) + "</button>"
        "<button type='button' onclick='stumpResetPalette()'>" + i18n.t("admin_reset_palette", lang) + "</button>"
        "</div>"
        "</div>"

        "<h2 class='sub'>" + i18n.t("admin_logo_header", lang) + "</h2>"
        "<div class='panel'>"
        "<textarea id='stump-svg-input' rows='6' placeholder='<svg ...>...</svg>' "
        "style='width:100%;font-family:ui-monospace,monospace;font-size:.8rem;'></textarea>"
        "<div class='row'>"
        "<button type='button' onclick='stumpSaveSvgLogo()'>" + i18n.t("admin_save_logo", lang) + "</button>"
        "<button type='button' onclick='stumpResetLogo()'>" + i18n.t("admin_reset_logo", lang) + "</button>"
        "<button type='button' onclick='stumpHideLogo()'>" + i18n.t("admin_hide_logo", lang) + "</button>"
        "</div>"
        "</div>"

        "<script>" + B._ADMIN_SETTINGS_SCRIPT.replace(
            "I18N_LOGO_RESET_NOTICE",
            json.dumps(i18n.t("admin_logo_reset_notice", lang)).replace("</", "<\\/")
        ).replace("STUMP_SITE_THEME", json.dumps(theme.site_theme())
        ).replace("STUMP_SITE_STAMP", json.dumps(theme.site_stamp())) + "</script>"
    )


async def _r_post_admin_radiolink_json(reader, writer, method, path, headers, ip, identifier, secure):
    # USB or Bluetooth for the Heltec on this computer, switched live.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    lang = i18n.get_lang(identifier)
    if not B._admin_or_local(identifier, headers):
        await B._send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t("admin_bt_denied", lang)}),
                    "application/json")
    else:
        kind = webtext.text(body).split("link=", 1)[-1].split("&")[0] if b"link=" in body else ""
        r = radio.switch_link(kind)
        await B._send(writer, "200 OK", json.dumps({"ok": r in ("done", "same"), "message": i18n.t(
            "admin_link_r_" + r, lang)}), "application/json")


async def _r_get_admin_bt_json(reader, writer, method, path, headers, ip, identifier, secure):
    # The pairing PIN, while it's fresh: an /admin session only.
    if not B._admin_or_local(identifier, headers):
        await B._send(writer, "403 Forbidden", "{}", "application/json")
    else:
        await B._send(writer, "200 OK", json.dumps({"pin": radio.bt_pin(), "probe": radio.probe_state(),
                                                  "heard": radio.bt_heard()}), "application/json")


async def _r_get_admin(reader, writer, method, path, headers, ip, identifier, secure):
    # No link anywhere points here on purpose -- see
    # _render_admin_login_page's own docstring. With a live
    # session (the cookie set at login), the admin page itself.
    lang = i18n.get_lang(identifier)
    tok = B._cookie(headers, B.ADMIN_COOKIE)
    if B._session_ok(tok):
        names = fserv._list_files() if fserv.sd_ok else []
        await B._send(writer, "200 OK", B._page(_render_admin_file_list_page(lang, names, tok)))
    else:
        await B._send(writer, "200 OK", B._page(_render_admin_login_page(lang)))


async def _r_post_admin_home(reader, writer, method, path, headers, ip, identifier, secure):
    # Before the generic POST /admin (login) branch, like the others.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        try:
            height = int(fields.get("height", "280"))
        except ValueError:
            height = -1
        # An unticked checkbox sends nothing: no "show" means hide.
        why = fserv.save_home_config(fields.get("place", ""), height, fields.get("show") != "1")
        msg = i18n.t("admin_home_saved", lang) if why is None else i18n.t("admin_home_failed", lang, why=why)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_propagation(reader, writer, method, path, headers, ip, identifier, secure):
    # Before the generic POST /admin (login) branch, like /admin/radio.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        action = fields.get("action", "")
        if action == "on":
            propagation.save_setting(True)
            if propagation.enable():
                propagation.announce()
                msg = i18n.t("admin_pn_enabled", lang)
            else:
                msg = i18n.t("admin_pn_no_sd", lang)
        elif action == "off":
            propagation.save_setting(False)
            propagation.disable()
            msg = i18n.t("admin_pn_disabled", lang)
        elif action == "leave":
            err = propagation.leave_message(fields.get("to", ""), fields.get("text", "").strip() or "test")
            msg = i18n.t({None: "admin_pn_left", "off": "admin_pn_off_state", "address": "admin_pn_bad_address",
                          "unknown": "admin_pn_unknown"}.get(err, "admin_pn_unknown"), lang)
        else:
            msg = ""
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>" if msg else ""
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_profiles(reader, writer, method, path, headers, ip, identifier, secure):
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    lang = i18n.get_lang(identifier)
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        n = fields.get("nick", "")
        if fields.get("action") == "delete":
            msg = i18n.t("admin_profiles_deleted" if profiles.delete(n) else "admin_profiles_missing", lang, nick=n)
        elif profiles.valid_password(fields.get("pw", "")) and profiles.set_password(
                n, fields.get("pw", ""), digested=await profiles.prepare(fields.get("pw", ""))):
            msg = i18n.t("admin_profiles_was_reset", lang, nick=n)
        else:
            msg = i18n.t("profile_err_password", lang, n=profiles.PW_MIN)
        names = fserv._list_files() if fserv.sd_ok else []
        await B._send(writer, "200 OK", B._page("<p class='sub'><b>" + billboard._esc(msg) + "</b></p>"
                                            + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_setup(reader, writer, method, path, headers, ip, identifier, secure):
    # The Provisioner's answers changed from /admin (settings.py),
    # and the restart that applies the rest.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    lang = i18n.get_lang(identifier)
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    elif path.startswith("/admin/restart"):
        await B._send(writer, "200 OK", B._page(
            "<h1>" + i18n.t("admin_setup_restart", lang) + "</h1><p class='sub'>"
            + i18n.t("admin_setup_restarting", lang) + "</p>"
            "<script>setTimeout(function(){location.href='/admin';},20000);</script>"))
        import uasyncio as asyncio
        asyncio.create_task(_restart_soon())
    else:
        changes, bad = stump_settings.parse_form(fields)
        later = []
        if bad:
            label = {"node_name": "admin_setup_node", "bot_name": "admin_setup_bot",
                     "greeting": "admin_setup_names", "economy": "admin_setup_files",
                     "auth_mode": "admin_setup_access",
                     "storage_mb": "admin_setup_storage"}.get(bad) or "admin_setup_w_" + bad[2:]
            msg = i18n.t("admin_setup_invalid", lang, field=i18n.t(label, lang).split(" (")[0])
        else:
            before = stump_settings.current()
            saved, later = stump_settings.apply(changes)
            if not saved:
                msg = i18n.t("admin_setup_not_saved", lang)
            elif all(before.get(k) == v for k, v in changes.items()):
                msg = i18n.t("admin_setup_unchanged", lang)
            elif later:
                msg = None
            else:
                msg = i18n.t("admin_setup_saved", lang)
        labels = {"NODE_NAME": "admin_setup_node", "BOT_NAME": "admin_setup_bot",
                  "MESH_GREETING": "admin_setup_names", "FEATURES": "admin_setup_features",
                  "CREDIT_WEIGHTS": "admin_setup_files", "AUTH_MODE": "admin_setup_access",
                  "PC_STORAGE_MB": "admin_setup_storage", "PC_KEEP_AWAKE": "admin_setup_computer"}
        what = ", ".join(i18n.t(labels.get(k, k), lang).split(" (")[0] for k in later)
        notice = "<p class='sub'><b>" + billboard._esc(msg or i18n.t("admin_setup_later", lang, what=what)) + "</b></p>"
        names = fserv._list_files() if fserv.sd_ok else []
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_poweroff(reader, writer, method, path, headers, ip, identifier, secure):
    # Turn this Stump off (a computer): /admin's button with the
    # password, or the Stop file on the computer itself (local).
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    h = _host()
    lang = i18n.get_lang(identifier)
    if h is None:
        await B._send(writer, "404 Not Found", "Not a computer Stump", "text/plain")
    elif not (ip in B._LOCAL or B._admin_ok(fields.get("admin_pass", ""), identifier)):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        folder = billboard._esc(str(getattr(h, "ROOT", "") or ""))
        await B._send(writer, "200 OK", B._page(
            "<h1>" + i18n.t("admin_power_bye_title", lang) + "</h1><p class='sub'>"
            + i18n.t("admin_power_bye", lang, start=billboard._esc(h.START_FILE), folder=folder) + "</p>"))
        import uasyncio as asyncio
        asyncio.create_task(_power_off_soon())


async def _r_post_admin_radioretry(reader, writer, method, path, headers, ip, identifier, secure):
    # /admin's "Reconnect the radio": the handshake again, now.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        msg = i18n.t("admin_radio_retrying" if radio.retry() else "admin_radio_none", lang)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_radiochat(reader, writer, method, path, headers, ip, identifier, secure):
    # /admin's switch for the radio settings in the chat's pop-up.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        on = fields.get("on") == "1"
        ok = radio.set_chat_edit(on)
        msg = i18n.t(("admin_radio_chat_on" if on else "admin_radio_chat_off") if ok else "admin_radio_not_saved", lang)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_bluetooth_json(reader, writer, method, path, headers, ip, identifier, secure):
    # The Heltec's Bluetooth from /admin: on, off, pairing mode.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    lang = i18n.get_lang(identifier)
    if not B._admin_or_local(identifier, headers):
        await B._send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t("admin_bt_denied", lang)}),
                    "application/json")
    else:
        action = billboard._url_decode(webtext.text(body).split("action=", 1)[-1].split("&")[0]) if b"action=" in body else ""
        if action == "computer":
            r = radio.pair_computer()
            ok, key = r == "done", {"done": "admin_bt_computer_go", "usb": "admin_bt_computer_usb",
                                    "bleak": "admin_link_r_bleak"}.get(r, "admin_bt_offline")
        else:
            ok = action in ("on", "off", "pair", "unpair") and radio.bluetooth(action) > 0
            key = {"on": "admin_bt_done_on", "off": "admin_bt_done_off", "pair": "admin_bt_pairing",
                   "unpair": "admin_bt_done_unpair"}.get(action)
            key = key if ok else "admin_bt_offline"
        await B._send(writer, "200 OK", json.dumps({"ok": ok, "message": i18n.t(key, lang)}), "application/json")


async def _r_post_admin_radio_json(reader, writer, method, path, headers, ip, identifier, secure):
    # The radio's settings from the chat's pop-up (rrc_ui): the
    # person on the computer, or an /admin session. JSON answer.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    lang = i18n.get_lang(identifier)
    if not B._radio_editor(identifier, headers):
        await B._send(writer, "403 Forbidden", json.dumps({"ok": False, "message": i18n.t(
            "rrc_radio_owner_only" if radio.chat_edit() else "admin_radio_chat_off", lang)}),
                    "application/json")
    else:
        fields = {}
        for kv in webtext.text(body).split("&"):
            parts = kv.split("=", 1)
            if len(parts) == 2:
                fields[parts[0]] = billboard._url_decode(parts[1])
        settings, bad = radio.parse_form(fields)
        if bad:
            label = {"frequency": "radio_freq", "bandwidth": "radio_bw", "txpower": "radio_txp",
                     "sf": "radio_sf", "cr": "radio_cr"}[bad]
            ok, msg = False, i18n.t("admin_radio_invalid", lang, field=i18n.t(label, lang))
        else:
            result = radio.apply(settings)
            ok = radio.save(settings)
            msg = i18n.t("admin_radio_not_saved" if not ok else {"sent": "admin_radio_sent",
                         "pending": "admin_radio_pending"}.get(result, "admin_radio_none"), lang)
        await B._send(writer, "200 OK", json.dumps({"ok": ok, "message": msg}), "application/json")


async def _r_post_admin_radio(reader, writer, method, path, headers, ip, identifier, secure):
    # Must come before the generic POST /admin (login) branch
    # below, which catches every other /admin POST.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        settings, bad = radio.parse_form(fields)
        if bad:
            label = {"frequency": "radio_freq", "bandwidth": "radio_bw", "txpower": "radio_txp",
                     "sf": "radio_sf", "cr": "radio_cr"}[bad]
            msg = i18n.t("admin_radio_invalid", lang, field=i18n.t(label, lang))
        else:
            result = radio.apply(settings)
            saved = radio.save(settings)
            if not saved:
                msg = i18n.t("admin_radio_not_saved", lang)
            elif result == "sent":
                msg = i18n.t("admin_radio_sent", lang)
            elif result == "pending":
                msg = i18n.t("admin_radio_pending", lang)
            else:
                msg = i18n.t("admin_radio_none", lang)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_layout(reader, writer, method, path, headers, ip, identifier, secure):
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        if fields.get("reset"):
            ok = layout.save_many({k: None for k in layout.KINDS})
        else:
            nav_ok = set(B._DESTINATIONS)
            tile_ok = set(k for k, n, h in B.home_tile_items(lang, all=True))

            def keys(name, allowed):
                return [k for k in fields.get(name, "").split(",") if k in allowed]
            changes = {"nav_hidden": keys("nav_hidden", nav_ok),
                       "tiles_hidden": keys("tiles_hidden", tile_ok)}
            if keys("nav", nav_ok):
                changes["nav"] = keys("nav", nav_ok)
            if keys("tiles", tile_ok):
                changes["tiles"] = keys("tiles", tile_ok)
            ok = layout.save_many(changes)
        msg = i18n.t("admin_layout_saved" if ok else "admin_layout_not_saved", lang)
        names = fserv._list_files() if fserv.sd_ok else []
        await B._send(writer, "200 OK", B._page("<p class='sub'>" + billboard._esc(msg) + "</p>"
                                            + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_theme(reader, writer, method, path, headers, ip, identifier, secure):
    # The theme for everyone, and deleting tools: like the other
    # /admin forms, the password comes back as a hidden field and
    # is checked again here.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields, ticked, sections = {}, [], []
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) != 2:
            continue
        val = billboard._url_decode(parts[1])
        if parts[0] == "f":
            ticked.append(val)
        elif parts[0] == "d":
            sections.append(val)
        else:
            fields[parts[0]] = val
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        extra = ""
        if path.startswith("/admin/theme"):
            choice = fields.get("theme", "").strip().lower() or None
            if choice is not None and choice not in theme.THEMES:
                msg = i18n.t("admin_site_theme_not_saved", lang)
            elif theme.set_site_theme(choice):
                msg = i18n.t("admin_site_theme_saved", lang,
                             name=_theme_label(theme.site_theme(), lang))
                # This browser may have its own theme from the
                # buttons below, which would hide the change:
                # drop it, and show the new one now.
                extra = ("<script>try{localStorage.removeItem('stump_theme');}catch(e){}"
                         "document.documentElement.removeAttribute('style');"
                         "document.documentElement.setAttribute('data-theme',"
                         + json.dumps(theme.site_theme()) + ");</script>")
            else:
                msg = i18n.t("admin_site_theme_not_saved", lang)
        else:
            gone = 0
            for d in sections:
                gone += fserv.delete_tool_section(d)
            for rel in ticked:
                if rel.split("/", 1)[0] in sections:
                    continue           # went with its section
                if fserv.delete_tool(rel):
                    gone += 1
            msg = i18n.t("admin_deleted_notice", lang, n=gone)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>" + extra
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_plugins(reader, writer, method, path, headers, ip, identifier, secure):
    # Before the generic POST /admin (login) branch, like the others.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    fields = {}
    for kv in webtext.text(body).split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            fields[parts[0]] = billboard._url_decode(parts[1])
    pw = fields.get("admin_pass", "")
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        lang = i18n.get_lang(identifier)
        msg = plugins.admin_chat([fields.get("action", ""), fields.get("name", "")], lang)[0]
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + billboard._esc(msg) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_logout(reader, writer, method, path, headers, ip, identifier, secure):
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    for kv in webtext.text(body).split("&"):
        if kv.startswith("admin_pass="):
            B._end_session(billboard._url_decode(kv[11:]))
    B._end_session(B._cookie(headers, B.ADMIN_COOKIE))
    await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /admin\r\n"
                        "Set-Cookie: " + B.ADMIN_COOKIE + "=; Path=/admin; Max-Age=0; HttpOnly; SameSite=Strict\r\n"
                        "Cache-Control: no-store\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


async def _r_post_admin(reader, writer, method, path, headers, ip, identifier, secure):
    # The login. A right password starts a session and redirects
    # to GET /admin (see SESSION_IDLE_S above), so this POST --
    # with the password in it -- never sits in the history.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    pw = ""
    for kv in webtext.text(body).split("&"):
        if kv.startswith("admin_pass="):
            pw = billboard._url_decode(kv[11:])
    lang = i18n.get_lang(identifier)
    if not B._check_admin_pw(pw, identifier):
        await B._send(writer, "403 Forbidden", B._page(_render_admin_login_page(
            lang, error=True, wait=guard.locked(identifier))))
    else:
        tok = B._new_session()
        await writer.awrite("HTTP/1.1 303 See Other\r\nLocation: /admin\r\n"
                            "Set-Cookie: " + B.ADMIN_COOKIE + "=" + tok
                            + "; Path=/admin; HttpOnly; SameSite=Strict\r\n"
                            "Cache-Control: no-store\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


async def _r_post_admin_delete_post(reader, writer, method, path, headers, ip, identifier, secure):
    # Must come BEFORE the /admin/delete branch below --
    # "/admin/delete_post".startswith("/admin/delete") is True,
    # so without this ordering the broader check would catch
    # these requests first and try to delete files named after
    # timestamps that don't exist, silently doing nothing.
    # Confirmed directly before writing this rather than
    # assumed: a startswith-based router means more specific
    # paths always have to come first.
    #
    # Same re-validation discipline as file deletion: the
    # password arrived as a hidden field, convenient but not
    # trustworthy on its own, so it's checked here exactly as
    # strictly as everywhere else that accepts one. Timestamps
    # arrive the same repeated-field way filenames do for a
    # batch file delete -- one ts=... occurrence per checked
    # post, collected into a list rather than only keeping the
    # last match.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    pw = ""
    timestamps = []
    for kv in webtext.text(body).split("&"):
        if kv.startswith("ts="):
            timestamps.append(billboard._url_decode(kv[3:]))
        elif kv.startswith("admin_pass="):
            pw = billboard._url_decode(kv[11:])
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        deleted_titles = []
        for ts_str in timestamps:
            removed = billboard.delete_entry(ts_str) if ts_str else False
            if removed:
                deleted_titles.append(removed if removed is not True else "?")
        # Name exactly what was removed, not just how many -- so a
        # moderator can see at a glance that it matches what they
        # ticked.
        lang = i18n.get_lang(identifier)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + i18n.t("admin_post_deleted_notice", lang, n=len(deleted_titles))
        if deleted_titles:
            notice += " " + ", ".join("« " + billboard._esc(t) + " »" for t in deleted_titles)
        notice += "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def _r_post_admin_delete(reader, writer, method, path, headers, ip, identifier, secure):
    # Re-validates the password for real -- it arrived as a
    # hidden field on the page above, which is convenient, not
    # trustworthy: anyone could POST here directly with a
    # forged or empty one, so this checks it exactly as
    # strictly as the login step did, not just checks it was
    # present. Every checked box arrives as a repeated f=...
    # field with the SAME name, one occurrence per file --
    # collected into a list here rather than the earlier
    # single-file parsing pattern that only ever kept the last
    # match, since a batch is the whole point of this route.
    length = int(headers.get("content-length", "0"))
    body = await B._read_small_body(reader, length)
    pw = ""
    fnames = []
    for kv in webtext.text(body).split("&"):
        if kv.startswith("f="):
            fnames.append(B._clean_filename(billboard._url_decode(kv[2:])))
        elif kv.startswith("admin_pass="):
            pw = billboard._url_decode(kv[11:])
    if not B._admin_ok(pw, identifier):
        await B._send(writer, "403 Forbidden", "Invalid admin password", "text/plain")
    else:
        deleted_count = 0
        for fname in fnames:
            if fname and fserv.delete_file(fname):
                deleted_count += 1
        # Re-renders the file list directly instead of
        # redirecting to /admin -- a 303 there lands on the
        # bare login form (that's what GET /admin always shows),
        # giving zero visible confirmation that anything
        # happened. A real deletion succeeding and then bouncing
        # back to an empty password prompt reads exactly like
        # "the delete doesn't work", confirmed directly against
        # a real test report. The password is already validated
        # at this point in the handler, so there's no reason to
        # make the admin type it a second time just to see the
        # file is actually gone.
        lang = i18n.get_lang(identifier)
        names = fserv._list_files() if fserv.sd_ok else []
        notice = "<p class='sub'>" + i18n.t("admin_deleted_notice", lang, n=deleted_count) + "</p>"
        await B._send(writer, "200 OK", B._page(notice + _render_admin_file_list_page(lang, names, pw)))


async def route(reader, writer, method, path, headers, ip, identifier, secure):
    """Every GET and POST under /admin, in the order barkeep matched them."""
    if method == "POST" and path.startswith("/admin/radiolink.json"):
        await _r_post_admin_radiolink_json(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "GET" and path.startswith("/admin/bt.json"):
        await _r_get_admin_bt_json(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "GET" and path.startswith("/admin"):
        await _r_get_admin(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/home"):
        await _r_post_admin_home(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/propagation"):
        await _r_post_admin_propagation(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/profiles"):
        await _r_post_admin_profiles(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and (path.startswith("/admin/setup") or path.startswith("/admin/restart")):
        await _r_post_admin_setup(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/poweroff"):
        await _r_post_admin_poweroff(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/radioretry"):
        await _r_post_admin_radioretry(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/radiochat"):
        await _r_post_admin_radiochat(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/bluetooth.json"):
        await _r_post_admin_bluetooth_json(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/radio.json"):
        await _r_post_admin_radio_json(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/radio"):
        await _r_post_admin_radio(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/layout"):
        await _r_post_admin_layout(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and (path.startswith("/admin/theme") or path.startswith("/admin/tools")):
        await _r_post_admin_theme(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/plugins"):
        await _r_post_admin_plugins(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/logout"):
        await _r_post_admin_logout(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin") and not path.startswith("/admin/delete"):
        await _r_post_admin(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/delete_post"):
        await _r_post_admin_delete_post(reader, writer, method, path, headers, ip, identifier, secure)
    elif method == "POST" and path.startswith("/admin/delete"):
        await _r_post_admin_delete(reader, writer, method, path, headers, ip, identifier, secure)
    else:
        return False
    return True
