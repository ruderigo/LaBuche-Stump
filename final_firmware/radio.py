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

# What the Heltec V3's SX1262 and RNode firmware accept.
BANDWIDTHS = (7800, 10400, 15600, 20800, 31250, 41700, 62500, 125000, 250000, 500000)
TXP_MIN, TXP_MAX = 0, 22
SF_MIN, SF_MAX = 5, 12
CR_MIN, CR_MAX = 5, 8            # 4/5 .. 4/8
FREQ_MIN, FREQ_MAX = 150000000, 960000000

KEYS = ("frequency", "bandwidth", "txpower", "sf", "cr")


def bridges():
    """The live Heltec Bridge interface(s) on this node, if any."""
    try:
        from urns.transport import Transport
    except ImportError:
        return []
    return [i for i in Transport.interfaces if i.__class__.__name__ == "WiFiSerialInterface"]


def current():
    """The settings the bridge is using right now, or None without one."""
    b = bridges()
    if not b:
        return None
    i = b[0]
    return {"frequency": i.frequency, "bandwidth": i.bandwidth,
            "txpower": i.txpower, "sf": i.sf, "cr": i.cr}


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


def overlay(config):
    """At boot, BEFORE the interfaces are created: writes the saved
    settings into config.py's bridge entry, so the Heltec's very first
    connection already gets them. (Applying them after the bridge had
    connected briefly sent config.py's values -- 7 dBm -- first.)"""
    s = load()
    if not s:
        return False
    names = {"frequency": "frequency", "bandwidth": "bandwidth", "txpower": "txpower",
             "sf": "spreadingfactor", "cr": "codingrate"}
    hit = False
    for entry in config.get("interfaces", []):
        if entry.get("type") == "WiFiSerialInterface":
            for k, v in s.items():
                entry[names[k]] = v
            hit = True
    if hit:
        print("[radio] saved settings in use: %s MHz, %d Hz, %d dBm, SF%d, CR4/%d" % (
            hz_to_mhz(s["frequency"]), s["bandwidth"], s["txpower"], s["sf"], s["cr"]))
    return hit
