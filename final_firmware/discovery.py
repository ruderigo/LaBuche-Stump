# Finding Stumps on a Wi-Fi without knowing their address.
#
# A Stump on a home or office Wi-Fi gets its address from the router, and
# the router can change it (a computer Stump went .172 -> .175 -> .176 in a
# week). An app that remembered the address then talks to nothing. So
# every Stump -- the CAM and a computer alike -- answers a tiny question on
# the local network, and says where it is in its announce:
#
#   1. "Who's a Stump here?": a UDP datagram to port PORT (4244), sent as a
#      broadcast (255.255.255.255, or the subnet's) or straight to one
#      address. Its first bytes are QUERY ("STUMP?"); anything after them
#      is ignored (room for a version). Every Stump that hears it answers
#      the sender, unicast, with one JSON object (card()):
#        {"stump": 1, "name": "macOS", "lxmf": "<32 hex>",
#         "release": "1.0.0-beta.21", "http": ["http://192.168.0.176/"],
#         "here": "http://192.168.0.176/"}
#      "lxmf" is the Stump's identity (its LXMF delivery address): it
#      never changes, so an app keeps a Stump by it, and finds its current
#      address again by asking. "http" lists every address it serves its
#      pages on; "here" is the one on the asker's network (or "").
#   2. Its announce: the "stump.node" beacon's data gains a fifth item, the
#      same "http" list, and the beacon goes out again soon after the
#      address changes (watch_loop).
#   3. GET /stumps.json (barkeep): this Stump's card and the other Stumps
#      it knows, each with the addresses its own beacon gave.
#
# Nothing to install, nothing to set on the router; a Wi-Fi that keeps its
# devices from seeing each other (some guest networks) blocks this, as it
# blocks everything else local.

import time

try:
    import ujson as json
except ImportError:
    import json

PORT = 4244
QUERY = b"STUMP?"
WATCH_S = 30            # how often the addresses are looked at
MAX_ANSWERS_PER_S = 5   # a flood of questions doesn't keep the Stump busy

WEB_PORT = 80           # set by barkeep once its server is up (8080 on a busy computer)
_state = {"urls": [], "sock": None, "answers": [], "error": None, "answered": 0}


def addresses():
    """This Stump's IPv4 addresses visitors can reach, now: on a computer,
    its hotspot address and its addresses on the networks it's on; on the
    CAM, its own access point and the Wi-Fi it joined."""
    out = []

    def add(ip):
        if ip and ip != "0.0.0.0" and not ip.startswith("127.") and ip not in out:
            out.append(ip)
    try:
        import stump_host
    except ImportError:
        stump_host = None
    if stump_host is not None:
        # No name lookups here: resolving the computer's own hostname can
        # hang for many seconds (macOS), and this runs every WATCH_S and on
        # every question. The hotspot address set in config.py, the
        # well-known hotspot addresses this computer holds, and the address
        # of its default route.
        want = stump_host.setting("PC_HOTSPOT_IP", "auto")
        if want and want != "auto":
            add(want)
        for ip in getattr(stump_host, "WELL_KNOWN", ()):
            if _is_mine(ip):
                add(ip)
        add(_route_ip())
        return out
    try:
        import network
        for itf in (network.STA_IF, network.AP_IF):
            w = network.WLAN(itf)
            if w.active() and (itf == network.AP_IF or w.isconnected()):
                add(w.ifconfig()[0])
    except Exception:
        pass
    return out


def _is_mine(ip):
    """Is this address one of this computer's? (Binding to it only works if
    so -- no name lookup.)"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((ip, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _route_ip():
    """The address of this computer's default route (sends nothing)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def urls(ips=None):
    port = "" if WEB_PORT in (80, None) else ":%d" % WEB_PORT
    return ["http://%s%s/" % (ip, port) for ip in (addresses() if ips is None else ips)]


def _same_net(a, b):
    """Same /24: on a home Wi-Fi or a Stump's own, close enough."""
    return a.split(".")[:3] == b.split(".")[:3]


def card(asker_ip=None):
    """What this Stump says about itself (see the top of this file)."""
    try:
        import rrc
        node = rrc.NODE or {}
        info = rrc.NODE_INFO or {}
    except ImportError:
        node, info = {}, {}
    try:
        from config import NODE_NAME as name
    except ImportError:
        name = node.get("name") or "Stump"
    ips = addresses()
    here = ""
    if asker_ip:
        for ip in ips:
            if _same_net(ip, asker_ip):
                here = urls([ip])[0]
                break
    return {"stump": 1, "name": node.get("name") or name, "lxmf": node.get("lxmf") or "",
            "release": info.get("release") or "", "http": urls(ips), "here": here}


def answer(data, asker_ip):
    """The reply to one datagram, or None (not a question, or too many)."""
    if not bytes(data[:len(QUERY)]) == QUERY:
        return None
    now = time.time()
    _state["answers"] = [t for t in _state["answers"] if now - t < 1]
    if len(_state["answers"]) >= MAX_ANSWERS_PER_S:
        return None
    _state["answers"].append(now)
    _state["answered"] += 1
    return json.dumps(card(asker_ip)).encode()


def _open():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except Exception:
        pass
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    s.bind(socket.getaddrinfo("0.0.0.0", PORT)[0][-1])
    s.setblocking(False)
    return s


def _ip_of(addr):
    if isinstance(addr, tuple):
        return addr[0]
    try:
        return "%d.%d.%d.%d" % tuple(addr[4:8])       # MicroPython's raw sockaddr
    except Exception:
        return None


async def loop():
    """Answers "who's a Stump here?" for as long as the Stump runs."""
    import uasyncio as asyncio
    try:
        s = _state["sock"] = _open()
        print("[discovery] answering on UDP port %d" % PORT)
    except Exception as e:
        _state["error"] = str(e)
        print("[discovery] not answering on UDP port %d: %s" % (PORT, e))
        return
    while True:
        try:
            data, addr = s.recvfrom(256)
        except OSError:
            await asyncio.sleep(0.2)
            continue
        except Exception:
            await asyncio.sleep(0.5)
            continue
        try:
            reply = answer(data, _ip_of(addr))
            if reply:
                s.sendto(reply, addr)
        except Exception as e:
            print("[discovery] couldn't answer:", e)
        await asyncio.sleep(0)


def changed():
    """True once each time the addresses differ from last time looked."""
    now = urls()
    if now != _state["urls"]:
        first = not _state["urls"]
        _state["urls"] = now
        return not first
    return False


async def watch_loop():
    """The address changed (the router gave a new one): say so soon -- the
    beacon (with the new "http") and the LXMF announce go out again."""
    import uasyncio as asyncio
    changed()
    while True:
        await asyncio.sleep(WATCH_S)
        try:
            if changed():
                print("[discovery] this Stump's address is now %s" % ", ".join(_state["urls"]))
                import chain
                chain.link_event()
        except Exception:
            pass


def beacon_data(version, node_name, lxmf_hash):
    """The "stump.node" beacon's announce data: ["stump", version, name,
    <16-byte LXMF hash>, [web addresses]] (msgpack)."""
    from urns import umsgpack
    try:
        http = urls()
    except Exception:
        http = []
    return umsgpack.packb(["stump", version, node_name, bytes(lxmf_hash), http])


# Other Stumps' addresses, from their beacons: LXMF hex -> {"name", "http", "at"}.
_known = {}
KNOWN_MAX = 64


def heard(lxmf_hex, name, http):
    if not lxmf_hex:
        return
    _known[lxmf_hex] = {"name": name or "", "http": [str(u) for u in (http or [])][:6], "at": time.time()}
    if len(_known) > KNOWN_MAX:
        oldest = sorted(_known.items(), key=lambda kv: kv[1]["at"])[0][0]
        del _known[oldest]


def directory():
    """GET /stumps.json: this Stump, then the others it heard (newest first)."""
    others = sorted(_known.items(), key=lambda kv: -kv[1]["at"])
    return {"self": card(), "stumps": [{"name": v["name"], "lxmf": k, "http": v["http"],
                                         "heard_s": int(time.time() - v["at"])} for k, v in others]}
