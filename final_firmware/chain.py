# Project Stump -- chain.py
#
# Stumps linked to each other over Wi-Fi, so big transfers between them
# never touch LoRa (docs/CHAIN.md). Each Stump in a chain:
#
#   - serves its own Wi-Fi on its own subnet, 192.168.<CHAIN_SUBNET>.1
#     (every Stump in a chain needs a different number);
#   - except the first one (the root), joins the Wi-Fi of the Stump before
#     it as a station (CHAIN_UPLINK_SSID) -- the "uplink";
#   - speaks Reticulum to its neighbours in UDP on both: up through the
#     uplink, down to whoever joined its own Wi-Fi (other Stumps, and PCs
#     running Reticulum), with transport on so it relays for them.
#
# The ESP32 can't route ordinary IP between its two Wi-Fi sides, but it
# can relay Reticulum -- which is all the chain needs.
#
# config.py:
#   CHAIN_ENABLED     = True
#   CHAIN_UPLINK_SSID = ""      # the neighbour Stump's Wi-Fi; "" = this is the root
#   CHAIN_UPLINK_PASS = ""
#   CHAIN_SUBNET      = 4       # this Stump's Wi-Fi is 192.168.<n>.1 (4..254, unique per Stump)
#   CHAIN_CHANNEL     = 11      # root only: the Wi-Fi channel the whole chain uses
#   CHAIN_SECRET      = ""      # the same on every Stump of the chain (strongly advised)
#   CHAIN_PORT        = 4242
#   CHAIN_LAN         = True    # root only, with a secret: also take chain links on
#                               # the Wi-Fi it joined (WIFI_SSID), so a computer on
#                               # the same home network joins without switching Wi-Fi

import time

DEFAULT_SUBNET = 4
DEFAULT_PORT = 4242

_state = {"sta_ip": None, "parent_ip": None, "ap_ip": None, "uplink": None, "hub": None,
          "hubs": [], "lan_ip": None, "new_link": False,
          # The Stumps from the first one down to this one (their own Wi-Fi
          # addresses), learned from the Stump above; [] while cut off.
          # Another Stump looking for a new Stump to join reads it here
          # (/chain.json) so it never joins one below itself (a circle).
          "path": None, "path_at": 0,
          # Joined instead of CHAIN_UPLINK_SSID after it went away (or None).
          "fallback": None, "lost_since": None, "bad": {}, "last_look": 0}


def chain_id(s=None):
    """Which chain this is, without giving its secret away: the same on
    every Stump with the same CHAIN_SECRET (in /chain.json)."""
    s = s or settings()
    if not s:
        return None
    import hashlib
    try:
        import ubinascii as binascii
    except ImportError:
        import binascii
    return binascii.hexlify(hashlib.sha256(b"stump-chain:" + s["secret"].encode()).digest()[:6]).decode()


def link_event():
    """A Stump joined below this one, or the one above answered for the
    first time: this Stump announces itself again soon (example_node's
    beacon_loop), so the newcomer knows it's a Stump without waiting for
    the half-hourly beacon -- #wifi-mesh depends on it."""
    _state["new_link"] = True


# Introductions over the radio, for Stumps with no Wi-Fi between them (a
# computer off Wi-Fi with its Heltec, a CAM out of range): the radio
# coming up, or a Stump heard for the first time, makes this Stump
# announce itself soon (the same as a new chain link) -- not more often
# than these, to spare the airtime.
RADIO_UP_EVERY = 600
NEW_STUMP_EVERY = 60
_intro = {"radio": -10 ** 9, "stump": -10 ** 9}


def radio_up(now=None):
    """The radio just connected (handshake done, set up): introduce this
    Stump on it."""
    now = time.time() if now is None else now
    if now - _intro["radio"] >= RADIO_UP_EVERY:
        _intro["radio"] = now
        link_event()
        return True
    return False


def stump_heard(now=None):
    """A Stump this one didn't know just introduced itself: answer, so it
    learns this one too (over the radio there's no chain to tell it)."""
    now = time.time() if now is None else now
    if now - _intro["stump"] >= NEW_STUMP_EVERY:
        _intro["stump"] = now
        link_event()
        return True
    return False


def take_link_event():
    if _state["new_link"]:
        _state["new_link"] = False
        return True
    return False


def settings():
    """The chain settings, or None when this Stump isn't in a chain."""
    try:
        import config as c
    except ImportError:
        return None
    if not getattr(c, "CHAIN_ENABLED", False):
        return None
    try:
        subnet = int(getattr(c, "CHAIN_SUBNET", DEFAULT_SUBNET))
    except (TypeError, ValueError):
        subnet = DEFAULT_SUBNET
    if not 1 <= subnet <= 254:
        subnet = DEFAULT_SUBNET
    try:
        channel = int(getattr(c, "CHAIN_CHANNEL", 0) or 0)
    except (TypeError, ValueError):
        channel = 0
    return {
        "uplink_ssid": (getattr(c, "CHAIN_UPLINK_SSID", "") or "").strip(),
        "uplink_pass": getattr(c, "CHAIN_UPLINK_PASS", "") or "",
        "subnet": subnet,
        "channel": channel if 1 <= channel <= 13 else 0,
        "secret": getattr(c, "CHAIN_SECRET", "") or "",
        "port": int(getattr(c, "CHAIN_PORT", DEFAULT_PORT) or DEFAULT_PORT),
        "lan": bool(getattr(c, "CHAIN_LAN", True)),
    }


def ap_ip(s=None):
    """This Stump's own Wi-Fi address: 192.168.<subnet>.1 -- or, for a
    Stump on a computer (docs/PC.md), its hotspot's real address."""
    try:
        import stump_host
        return stump_host.hotspot_ip()
    except ImportError:
        pass
    s = s or settings()
    return "192.168.%d.1" % (s["subnet"] if s else DEFAULT_SUBNET)


def ap_prefix():
    """'192.168.<subnet>.' -- who's on this Stump's own Wi-Fi."""
    return ap_ip()[:-1]


def is_root(s=None):
    s = s or settings()
    return bool(s) and not s["uplink_ssid"]


def connect_uplink(s, timeout=20):
    """Joins the parent Stump's Wi-Fi as a station. Returns (sta_ip,
    parent_ip) or (None, None). Reconnects by itself afterwards."""
    import network
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    try:
        sta.config(reconnects=-1)          # keep trying if the parent reboots
    except Exception:
        pass
    if not sta.isconnected():
        print("[chain] joining %r ..." % s["uplink_ssid"])
        sta.connect(s["uplink_ssid"], s["uplink_pass"])
        t0 = time.time()
        while not sta.isconnected():
            if time.time() - t0 > timeout:
                print("[chain] couldn't join %r yet; will keep trying" % s["uplink_ssid"])
                return None, None
            time.sleep(0.5)
    try:
        sta.config(pm=0)                   # no power save: it costs throughput
    except Exception:
        pass
    ip, mask, gw, dns = sta.ifconfig()
    _state["sta_ip"], _state["parent_ip"] = ip, gw
    try:
        rssi = sta.status("rssi")
    except Exception:
        rssi = "?"
    print("[chain] uplink: %s via %s (signal %s dBm)" % (ip, gw, rssi))
    return ip, gw


def interfaces(s, sta_ip, parent_ip, own_ap_ip, lan_ip=None):
    """The Reticulum interface configs for this Stump's place in the chain.
    lan_ip: the first Stump's address on the Wi-Fi it joined (WIFI_SSID),
    where it also takes chain links when it has a chain secret -- a
    computer on that same network (home, office) then joins the chain
    without leaving it. Never without a secret: anyone on that network
    could sit on the chain."""
    out = []
    # A Stump on a computer with one Wi-Fi radio joins its parent and serves
    # visitors on that same network: no separate side below it.
    if own_ap_ip != sta_ip:
        out.append({"type": "ChainHubInterface", "name": "Chain down", "enabled": True,
                    "listen_ip": own_ap_ip, "listen_port": s["port"], "secret": s["secret"],
                    "gravity": 10})
    if s["uplink_ssid"] and sta_ip and parent_ip:
        out.append({"type": "ChainUplinkInterface", "name": "Chain up", "enabled": True,
                    "listen_ip": sta_ip, "listen_port": s["port"],
                    "forward_ip": parent_ip, "forward_port": s["port"],
                    "secret": s["secret"], "gravity": 10})
    _state["lan_ip"] = None
    if is_root(s) and lan_ip and lan_ip != own_ap_ip and s.get("lan", True):
        if s["secret"]:
            out.append({"type": "ChainHubInterface", "name": "Chain LAN", "enabled": True,
                        "listen_ip": lan_ip, "listen_port": s["port"], "secret": s["secret"],
                        "gravity": 10})
            _state["lan_ip"] = lan_ip
        else:
            print("[chain] not taking chain links on %s: that needs a CHAIN_SECRET" % lan_ip)
    _state["ap_ip"] = own_ap_ip
    return out


def attach(rns):
    """Called after Reticulum set up its interfaces: remembers the chain's."""
    _state["hubs"] = []
    for i in rns.interfaces:
        n = i.__class__.__name__
        if n == "ChainHubInterface":
            _state["hubs"].append(i)
            if _state["hub"] is None or getattr(i, "listen_ip", None) == _state["ap_ip"]:
                _state["hub"] = i
        elif n == "ChainUplinkInterface":
            _state["uplink"] = i


def _start_uplink(rns, s, ip, gw):
    """Creates the uplink interface once the parent's Wi-Fi is joined (at
    boot, or later if the parent came up after this Stump)."""
    import uasyncio as asyncio
    from urns.interfaces.chain import ChainUplinkInterface
    from urns.transport import Transport
    cfg = interfaces(s, ip, gw, _state["ap_ip"] or ap_ip(s))[-1]
    iface = ChainUplinkInterface(cfg)
    iface.setup_ifac(cfg)
    iface.gravity = cfg["gravity"]
    rns.interfaces.append(iface)
    Transport.register_interface(iface)
    _state["uplink"] = iface
    asyncio.create_task(iface.poll_loop())
    print("[chain] uplink to %s is up" % gw)


QUIET_S = 90            # the Stump above not heard for this long: look for it


def _follow_parent(up, host, now, started):
    """A Stump on a computer joins its parent on a shared Wi-Fi, where the
    parent's address can change when it restarts (the router's DHCP). This
    remembers the parent's LXMF address while it answers, and when it goes
    quiet, looks for it on the network in a thread (stump_host.find_parent);
    the address it's found at is saved, and the watch loop moves the uplink
    there (network.WLAN.ifconfig reads it)."""
    import _thread
    f = _state.setdefault("follow", {"lxmf": host.parent_lxmf(), "busy": False, "last": 0, "asked": 0})
    heard = up.parent_seen and now - up.parent_seen < host.setting("CHAIN_QUIET_S", QUIET_S)
    if heard and not f["lxmf"] and not f["busy"] and now - f["asked"] > 60:
        f["asked"], f["busy"] = now, True

        def learn(ip=up.forward_ip):
            try:
                node = host.node_json(ip)
                if isinstance(node, dict) and node.get("lxmf"):
                    f["lxmf"] = node["lxmf"]
                    host.remember_parent(ip, node["lxmf"])
            except Exception as e:
                print("[chain] couldn't ask the Stump above who it is:", e)
            f["busy"] = False
        _thread.start_new_thread(learn, ())
    quiet = (now - up.parent_seen if up.parent_seen else now - started) > host.setting("CHAIN_QUIET_S", QUIET_S)
    if quiet and f["lxmf"] and not f["busy"] and now - f["last"] > 2 * host.setting("CHAIN_QUIET_S", QUIET_S):
        f["last"], f["busy"] = now, True
        print("[chain] the Stump above (%s) is quiet: looking for it on the network..." % up.forward_ip)

        def look(near=up.forward_ip, lxmf=f["lxmf"]):
            try:
                ip = host.find_parent(lxmf, near)
                if ip and ip != near:
                    print("[chain] found it at %s: moving the uplink there" % ip)
                    host.remember_parent(ip, lxmf)
                elif not ip:
                    print("[chain] not found on the network yet; trying again soon")
            except Exception as e:
                print("[chain] looking for the Stump above failed:", e)
            f["busy"] = False
        _thread.start_new_thread(look, ())


async def watch_loop(rns):
    """Brings the uplink up when the parent's Wi-Fi is joined, and moves it
    if its address changes (the parent rebooted and handed out a new one).
    On a computer, also finds the parent again when its address moved."""
    import uasyncio as asyncio
    import network
    s = settings()
    if not s or not s["uplink_ssid"]:
        return
    try:
        import stump_host
    except ImportError:
        stump_host = None
    started = time.time()
    while True:
        await asyncio.sleep(15)
        up = _state["uplink"]
        now = time.time()
        if stump_host is not None and up is not None:
            try:
                _follow_parent(up, stump_host, now, started)
            except Exception as e:
                print("[chain] follow:", e)
        try:
            sta = network.WLAN(network.STA_IF)
            sta_up = sta.isconnected()
            if stump_host is None:
                # A CAM: keep the path fresh, and find another Stump to
                # join when the one above is gone.
                if sta_up and up is not None and up.parent_seen and now - _state["path_at"] > PATH_EVERY:
                    p = refresh_path()
                    if p:
                        _state["known_path"] = p
                if _lost(up, sta_up, now, max(started, _state.get("moved_at", 0))):
                    if _state["lost_since"] is None:
                        _state["lost_since"] = now
                        _state["path"] = []             # cut off: nobody joins this one meanwhile
                        _state["path_at"] = 0           # asked again as soon as it's back
                    if now - _state["lost_since"] > LOST_S and now - _state["last_look"] > RETRY_S:
                        _state["last_look"] = now
                        print("[chain] the Stump above is gone; looking for another")
                        if await find_new_parent(s):
                            _state["lost_since"] = None
                            _state["path_at"] = 0
                        sta_up = sta.isconnected()
                else:
                    _state["lost_since"] = None
            if not sta_up:
                continue
            ip, mask, gw, dns = sta.ifconfig()
            _state["sta_ip"], _state["parent_ip"] = ip, gw
            if up is None:
                _start_uplink(rns, s, ip, gw)
                continue
            if ip != up.listen_ip or gw != up.forward_ip:
                print("[chain] uplink address changed: %s via %s" % (ip, gw))
                up.listen_ip = ip
                up.forward_ip = gw
                # Another Stump (or the same at a new address): heard
                # afresh -- its first answer re-introduces this Stump
                # (link event), and it gets time to answer.
                up.parent_seen = 0
                _state["moved_at"] = time.time()
                _state["path_at"] = 0
                from urns.interfaces.udp import _resolve_addr
                up._forward_addr = _resolve_addr(gw, up.forward_port)
                up._recreate_socket()
        except Exception as e:
            print("[chain] watch:", e)


# ── Finding another Stump to join ───────────────────────────────────
# A CAM joins the Stump set in CHAIN_UPLINK_SSID. If that one goes away
# (off, moved, out of range) for LOST_S, it looks for another Stump of the
# same chain in range and joins the strongest: open Wi-Fi networks, tried
# by signal, each asked for its /chain.json -- the same chain (chain_id),
# still reaching the first Stump (a path), and not one of this Stump's own
# below it (this Stump in its path). At the next restart it tries
# CHAIN_UPLINK_SSID first again.

LOST_S = 60          # the Stump above gone this long: look for another
PATH_EVERY = 120     # how often the path is asked of the Stump above
MAX_TRIES = 6        # open networks tried per look (each takes a few seconds)
JOIN_S = 10
RETRY_S = 180        # nothing found: look again after this
BAD_S = 1800         # a network that wasn't a Stump of this chain: skipped this long


def _http_json(ip, path, timeout=2):
    """GET http://ip/path -> the JSON, or None. Small and blocking (a
    couple of seconds at most)."""
    import socket
    import ujson
    s = None
    try:
        addr = socket.getaddrinfo(ip, 80)[0][-1]
        s = socket.socket()
        s.settimeout(timeout)
        s.connect(addr)
        s.send(("GET %s HTTP/1.0\r\nHost: %s\r\n\r\n" % (path, ip)).encode())
        data = b""
        while len(data) < 8192:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
        return ujson.loads(body)
    except Exception:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def path():
    """[first Stump, ..., this one] (Wi-Fi addresses), or [] when cut off
    from the first Stump."""
    s = settings()
    if not s:
        return []
    if is_root(s):
        return [ap_ip(s)]
    return list(_state["path"] or [])


def refresh_path(get=None):
    """Asks the Stump above for its path; this Stump's is that plus itself."""
    get = get or _http_json
    up = _state["uplink"]
    if up is None:
        _state["path"] = []
        return []
    d = get(up.forward_ip, "/chain.json")
    p = d.get("path") if isinstance(d, dict) else None
    me = _state["ap_ip"] or ap_ip()
    _state["path"] = (list(p) + [me]) if p and me not in p else []
    _state["path_at"] = time.time()
    return _state["path"]


def _good_parent(d, s, me, root):
    if not isinstance(d, dict) or not d.get("enabled"):
        return False, "not a Stump in a chain"
    if d.get("chain") != chain_id(s):
        return False, "another chain"
    p = d.get("path") or []
    if not p:
        return False, "cut off from the first Stump itself"
    if me in p:
        return False, "one of this Stump's own, below it"
    if root and p[0] != root:
        return False, "reaches another first Stump"
    return True, ""


async def find_new_parent(s, net=None, get=None, own_ssid=None):
    """Looks for another Stump of this chain in range and joins the
    strongest that fits. Returns (ssid, sta_ip, parent_ip) or None (then
    it goes back to trying CHAIN_UPLINK_SSID)."""
    import uasyncio as asyncio
    if net is None:
        import network as net
    get = get or _http_json
    sta = net.WLAN(net.STA_IF)
    me = _state["ap_ip"] or ap_ip(s)
    known = _state.get("known_path") or []
    root = known[0] if known else None
    if own_ssid is None:
        try:
            own_ssid = net.WLAN(net.AP_IF).config("essid")
        except Exception:
            own_ssid = ""
    now = time.time()
    try:
        found = sta.scan()
    except Exception as e:
        print("[chain] couldn't scan:", e)
        return None
    seen = {}
    for n in found:
        ssid = n[0].decode("utf-8", "replace") if isinstance(n[0], bytes) else str(n[0])
        if not ssid or ssid == own_ssid or n[4] != 0:      # Stumps' Wi-Fi is open
            continue
        if now - _state["bad"].get(ssid, -BAD_S) < BAD_S:
            continue
        if ssid not in seen or n[3] > seen[ssid]:
            seen[ssid] = n[3]
    order = sorted(seen, key=lambda k: -seen[k])[:MAX_TRIES]
    if order:
        print("[chain] looking for another Stump of this chain: %s" % ", ".join(
            "%s (%d dBm)" % (k, seen[k]) for k in order))
    for ssid in order:
        try:
            sta.disconnect()
        except Exception:
            pass
        sta.connect(ssid)
        t0 = time.time()
        while not sta.isconnected() and time.time() - t0 < JOIN_S:
            await asyncio.sleep(0.5)
        if not sta.isconnected():
            _state["bad"][ssid] = time.time()
            continue
        ip, mask, gw, dns = sta.ifconfig()
        ok, why = _good_parent(get(gw, "/chain.json"), s, me, root)
        if ok:
            print("[chain] joining %r (%d dBm) instead of %r" % (ssid, seen[ssid], s["uplink_ssid"]))
            _state["fallback"] = ssid
            return ssid, ip, gw
        print("[chain] %r: %s" % (ssid, why))
        _state["bad"][ssid] = time.time()
    try:
        sta.disconnect()
        sta.connect(s["uplink_ssid"], s["uplink_pass"])
    except Exception:
        pass
    if order:
        print("[chain] no other Stump of this chain in range; trying %r again" % s["uplink_ssid"])
    return None


def _lost(up, sta_up, now, started):
    """Is the Stump above gone? Its Wi-Fi gone, or there but the Stump
    silent."""
    if not sta_up:
        return True
    if up is None:
        return now - started > LOST_S
    seen = up.parent_seen or started
    return now - seen > QUIET_S


def status():
    """For /admin and /chain.json."""
    s = settings()
    if not s:
        return {"enabled": False}
    now = time.time()
    up, hub = _state["uplink"], _state["hub"]
    return {
        "enabled": True,
        "role": "root" if is_root(s) else "member",
        "ap_ip": ap_ip(s),
        "channel": s["channel"] or None,
        "secret": bool(s["secret"]),
        "uplink": None if is_root(s) else {
            "ssid": s["uplink_ssid"],
            "ip": up.listen_ip if up else None,
            "parent": up.forward_ip if up else None,
            "parent_heard_s": int(now - up.parent_seen) if up and up.parent_seen else None,
        },
        "lan_ip": _state["lan_ip"],
        "chain": chain_id(s),
        "path": path(),
        "fallback": _state["fallback"],
        "below": sorted(set(ip for h in (_state["hubs"] or ([hub] if hub else [])) for ip in h.peers)),
    }
