"""
Radio settings for the Heltec Bridge, changeable from /admin.

The CAM configures the Heltec's radio itself on every connection
(frequency, bandwidth, TX power, spreading factor, coding rate -- see
urns/interfaces/wifi_serial.py, _init_radio). Those values used to come
only from config.py's bridge entry, which set none of them, so the
built-in defaults always won, 7 dBm included; and an rnodeconf change on
the Heltec was simply overwritten at the next reconnect.

Now: settings saved here (SETTINGS_FILE, on internal flash so they work
without an SD card) override config.py, are written into the bridge's
config at boot (before it first connects), and when changed from /admin are sent to the Heltec at once if
it's connected, or at its next connection otherwise.
"""

import ujson as json

SETTINGS_FILE = "/radio.json"
# The Heltec bridge: over Wi-Fi (the CAM), or on USB or Bluetooth (Stump on
# a computer).
BRIDGE_TYPES = ("WiFiSerialInterface", "RNodeSerialInterface", "RNodeBLEInterface")

# What the Heltec V3's SX1262 and RNode firmware accept.
BANDWIDTHS = (7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000)
TXP_MIN, TXP_MAX = 0, 22
SF_MIN, SF_MAX = 5, 12
CR_MIN, CR_MAX = 5, 8            # 4/5 .. 4/8
FREQ_MIN, FREQ_MAX = 150000000, 960000000

KEYS = ("frequency", "bandwidth", "txpower", "sf", "cr")

# Whether the chat's profile pop-up offers the radio settings (the Radio
# row opens into an editor), switched from /admin. On by default; the
# editor there only works for the person on the computer or an /admin
# session either way (barkeep._radio_editor).
CHAT_FILE = "/radio_chat.json"


def bridges():
    """The live Heltec Bridge interface(s) on this node, if any."""
    try:
        from urns.transport import Transport
    except ImportError:
        return []
    return [i for i in Transport.interfaces if i.__class__.__name__ in BRIDGE_TYPES]


def retry():
    """Every radio connects again now, handshake and set-up from scratch
    (/admin's "Reconnect the radio"). How many were told."""
    n = 0
    for i in bridges():
        if hasattr(i, "retry_now"):
            i.retry_now()
            n += 1
    return n


BT_PIN_S = 180        # a pairing PIN is shown this long after the RNode gives it


def bluetooth(action):
    """Bluetooth on/off/pair on every connected radio. How many took it."""
    n = 0
    for i in bridges():
        if hasattr(i, "bluetooth") and i.bluetooth(action):
            n += 1
    return n


_probe = {"port": None, "until": 0}
PROBE_S = 60                  # the Heltec's pairing window is 35 s


def pair_computer():
    """Pairs this computer with the Heltec while it's still on USB: pairing
    mode over USB, and a Bluetooth connection attempt from this computer
    (which makes the computer ask for the code); the Heltec sends the code
    over USB, so /admin shows it. Then switch_link("ble"). Returns "done",
    "usb" (not on USB), "bleak" (Bluetooth support installing), "offline"."""
    import time
    if link() != "usb":
        return "usb"
    if not ble_ready():
        return "bleak"
    if bluetooth("pair") < 1:
        return "offline"
    for i in bridges():
        # While this computer pairs over Bluetooth the Heltec may stop
        # answering on USB: don't take that for a lost radio (reopening the
        # port restarts it, which would end the pairing).
        i.quiet_until = time.time() + PROBE_S
    _probe_stop()
    from urns.interfaces.rnode_ble import _BLEPort
    p = _BLEPort("auto")
    p.probe = True                # it asks the Heltec itself: an answer = paired
    _probe["port"] = p
    _probe["until"] = time.time() + PROBE_S
    return "done"


def _probe_stop():
    p = _probe["port"]
    if p is not None:
        try:
            p.stop()
            p.drop()
        except Exception:
            pass
    _probe["port"] = None


def probe_state(now=None):
    """The pairing attempt from this computer: None, "searching",
    "connected", or an error line."""
    import time
    now = time.time() if now is None else now
    p = _probe["port"]
    if p is None:
        return None
    if now > _probe["until"]:
        _probe_stop()
        return None
    if p.paired:
        return "paired"
    if p.connected:
        return "connected"
    return p.error or "searching"


def bt_heard():
    """The last few things the Heltec said that aren't status reports."""
    out = []
    for i in bridges():
        out += list((getattr(i, "bt", None) or {}).get("heard", []))
    return out[-8:]


def bt_pin(now=None):
    """The pairing PIN the radio gave (recent), or None. Admin only."""
    import time
    now = time.time() if now is None else now
    for i in bridges():
        b = getattr(i, "bt", None)
        if b and b.get("pin") and now - b.get("at", 0) < BT_PIN_S:
            return b["pin"]
    return None


def current():
    """The settings the bridge is using right now, or None without one."""
    b = bridges()
    if not b:
        return None
    i = b[0]
    return {"frequency": i.frequency, "bandwidth": i.bandwidth,
            "txpower": i.txpower, "sf": i.sf, "cr": i.cr}


def hw():
    """The radios' state, for /hw.json, the chat's status dot and /admin:
    {"state": "none" | "online" | "connecting" | "error", "radios": [...]}.
    "none": this Stump has no radio of its own (nothing to show)."""
    out = []
    for i in bridges():
        try:
            out.append(i.status())
        except Exception as e:
            out.append({"state": "error", "name": getattr(i, "name", "?"), "error": str(e)})
    states = [r.get("state") for r in out]
    if not out:
        state = "none"
    elif "error" in states:
        state = "error"
    elif "connecting" in states:
        state = "connecting"
    else:
        state = "online"
    return {"state": state, "radios": out}


def hw_line(r):
    """One radio in one line, for the status dot's hover text."""
    bits = [r.get("name") or "radio", r.get("where") or ""]
    if r.get("device"):
        bits.append(r["device"])
    if r.get("fw"):
        bits.append("RNode " + r["fw"])
    if r.get("state") != "online" and r.get("error"):
        bits.append(r["error"])
    return " · ".join(b for b in bits if b)


def chat_edit():
    """True unless /admin switched the chat's radio settings off."""
    try:
        with open(CHAT_FILE) as f:
            return json.load(f).get("on", True) is not False
    except (OSError, ValueError, AttributeError):
        return True


def set_chat_edit(on):
    try:
        with open(CHAT_FILE, "w") as f:
            json.dump({"on": bool(on)}, f)
        return True
    except OSError as e:
        print("[radio] could not save the chat setting:", e)
        return False


def load():
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for k in KEYS:
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, int) and _valid(k, v):
            out[k] = v
    return out


def _valid(key, v):
    if key == "frequency":
        return FREQ_MIN <= v <= FREQ_MAX
    if key == "bandwidth":
        return v in BANDWIDTHS
    if key == "txpower":
        return TXP_MIN <= v <= TXP_MAX
    if key == "sf":
        return SF_MIN <= v <= SF_MAX
    if key == "cr":
        return CR_MIN <= v <= CR_MAX
    return False


def mhz_to_hz(text):
    """ "915.125" -> 915125000, parsed as text, never through a float:
    this board's MicroPython uses single-precision floats, where 915.1
    MHz would come out as 915099976 Hz. Returns None if it isn't a
    plain decimal number with at most six decimal places."""
    text = (text or "").strip().replace(",", ".")
    if text.count(".") > 1:
        return None
    parts = text.split(".", 1)
    whole, frac = parts[0], (parts[1] if len(parts) > 1 else "")
    if not whole.isdigit() or (frac and not frac.isdigit()) or len(frac) > 6:
        return None
    return int(whole) * 1000000 + int((frac + "000000")[:6])


def hz_to_mhz(hz):
    whole, frac = divmod(int(hz), 1000000)
    frac = ("%06d" % frac).rstrip("0")
    return str(whole) + ("." + frac if frac else ".0")


def parse_form(fields):
    """fields: the submitted strings. Returns (settings, None) or
    (None, name of the first bad field)."""
    s = {}
    hz = mhz_to_hz(fields.get("freq", ""))
    if hz is None or not _valid("frequency", hz):
        return None, "frequency"
    s["frequency"] = hz
    for key, name in (("bandwidth", "bw"), ("txpower", "txp"), ("sf", "sf"), ("cr", "cr")):
        raw = fields.get(name, "").strip()
        if not raw.lstrip("-").isdigit() or not _valid(key, int(raw)):
            return None, key
        s[key] = int(raw)
    return s, None


def save(settings):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
        return True
    except OSError as e:
        print("[radio] could not save settings:", e)
        return False


def apply(settings):
    """Puts the settings on every bridge interface. Returns "sent" if at
    least one was connected and got them now, "pending" if they'll go
    out at the next connection, "none" if there's no bridge at all."""
    b = bridges()
    if not b:
        return "none"
    sent = False
    for i in b:
        if "frequency" in settings: i.frequency = settings["frequency"]
        if "bandwidth" in settings: i.bandwidth = settings["bandwidth"]
        if "txpower" in settings: i.txpower = settings["txpower"]
        if "sf" in settings: i.sf = settings["sf"]
        if "cr" in settings: i.cr = settings["cr"]
        if getattr(i, "online", False) and getattr(i, "_socket", None) is not None:
            i._init_radio()
            sent = sent or getattr(i, "online", False)
    return "sent" if sent else "pending"


# ── USB or Bluetooth (a Stump on a computer) ─────────────────────────
# /admin switches the Heltec's link live; the choice is kept here and
# applied at boot over config.py's entry.
LINK_FILE = "/radio_link.json"
_LINKS = {"usb": "RNodeSerialInterface", "ble": "RNodeBLEInterface"}
_bleak = {"state": None}          # None, "installing", "failed"


def link():
    """"usb", "ble", or None (no Heltec on this computer)."""
    for i in bridges():
        for k, v in _LINKS.items():
            if i.__class__.__name__ == v:
                return k
    return None


def _link_saved():
    try:
        with open(LINK_FILE) as f:
            d = json.load(f)
        return d if d.get("link") in _LINKS else None
    except (OSError, ValueError, AttributeError):
        return None


def _link_overlay(config):
    d = _link_saved()
    if not d:
        return False
    for entry in config.get("interfaces", []):
        if entry.get("type") in _LINKS.values() and entry.get("type") != _LINKS[d["link"]]:
            entry["type"] = _LINKS[d["link"]]
            entry["name"] = "Heltec (Bluetooth)" if d["link"] == "ble" else "Heltec (USB)"
            if d["link"] == "ble":
                entry["target"] = d.get("target") or "auto"
            else:
                entry["port"] = d.get("port") or "auto"
            print("[radio] the Heltec over %s (chosen in /admin)" % ("Bluetooth" if d["link"] == "ble" else "USB"))
            return True
    return False


def ble_ready():
    """Can this computer's Python do Bluetooth (bleak)? If not, starts
    installing it (once, in the background: it needs the internet)."""
    try:
        import bleak  # noqa: F401
        return True
    except ImportError:
        pass
    if _bleak["state"] is None:
        _bleak["state"] = "installing"
        try:
            import sys, subprocess, _thread

            def go():
                r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "bleak"],
                                   capture_output=True)
                _bleak["state"] = None if r.returncode == 0 else "failed"
            _thread.start_new_thread(go, ())
        except Exception:
            _bleak["state"] = "failed"
    return False


def switch_link(kind, target="auto"):
    """Moves the Heltec to USB or Bluetooth now: the old link told the
    radio is off and closed, the new one set up in its place (handshake,
    radio settings), and the choice kept for the next start. Returns
    "done", "same", "none" (no Heltec on a computer here), "bleak"
    (Bluetooth support still installing) or "failed"."""
    if kind not in _LINKS:
        return "failed"
    now = link()
    if now is None:
        return "none"
    if now == kind:
        return "same"
    if kind == "ble" and not ble_ready():
        return "bleak"
    _probe_stop()                 # the pairing attempt's connection makes way
    try:
        import uasyncio as asyncio
        from urns.transport import Transport
        old = [i for i in bridges() if i.__class__.__name__ in _LINKS.values()][0]
        cfg = {"frequency": old.frequency, "bandwidth": old.bandwidth, "txpower": old.txpower,
               "spreadingfactor": old.sf, "codingrate": old.cr}
        saved = _link_saved() or {}
        # The USB port it had (set by name, or found): back to it later.
        port = getattr(old, "port_setting", None) if now == "usb" else saved.get("port")
        if now == "usb" and port == "auto" and getattr(old, "port", "auto") not in ("auto", None):
            port = old.port
        if kind == "ble":
            from urns.interfaces.rnode_ble import RNodeBLEInterface as Cls
            cfg.update(name="Heltec (Bluetooth)", target=target or "auto")
        else:
            from urns.interfaces.rnode_serial import RNodeSerialInterface as Cls
            cfg.update(name="Heltec (USB)", port=port or "auto")
        try:
            old.close()                  # radio off, "the host left"
        except Exception:
            pass
        new = Cls(cfg)
        new.setup_ifac(cfg)
        new.gravity = getattr(old, "gravity", 0)
        idx = Transport.interfaces.index(old) if old in Transport.interfaces else len(Transport.interfaces)
        Transport.deregister_interface(old)
        Transport.interfaces.insert(idx, new)
        asyncio.create_task(new.poll_loop())
        print("[radio] switched to %s" % ("Bluetooth" if kind == "ble" else "USB"))
    except Exception as e:
        print("[radio] couldn't switch:", e)
        return "failed"
    try:
        with open(LINK_FILE, "w") as f:
            json.dump({"link": kind, "target": target or "auto", "port": port or "auto"}, f)
    except OSError as e:
        print("[radio] couldn't keep the choice:", e)
    return "done"


def overlay(config):
    """At boot, BEFORE the interfaces are created: writes the saved
    settings into config.py's bridge entry, so the Heltec's very first
    connection already gets them. (Applying them after the bridge had
    connected briefly sent config.py's values -- 7 dBm -- first.) Also the
    USB/Bluetooth choice made in /admin."""
    _link_overlay(config)
    s = load()
    if not s:
        return False
    names = {"frequency": "frequency", "bandwidth": "bandwidth", "txpower": "txpower",
             "sf": "spreadingfactor", "cr": "codingrate"}
    hit = False
    for entry in config.get("interfaces", []):
        if entry.get("type") in BRIDGE_TYPES:
            for k, v in s.items():
                entry[names[k]] = v
            hit = True
    if hit:
        print("[radio] saved settings in use: %s MHz, %d Hz, %d dBm, SF%d, CR4/%d" % (
            hz_to_mhz(s["frequency"]), s["bandwidth"], s["txpower"], s["sf"], s["cr"]))
    return hit
