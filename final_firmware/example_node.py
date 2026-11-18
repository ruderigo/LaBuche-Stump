"""
µReticulum Example: LXMF Messaging Node
========================================
Compatible with MeshChat, Sideband, NomadNet over UDP on the same LAN.

Project Stump build: this file is the COMPLETE, already-integrated
version -- captive_portal, barkeep, and fserv are wired in below. No
patch scripts needed; this file can be dropped onto the device as-is.
"""

from urns.mem import collect as _gc_collect
from config import WIFI_SSID, WIFI_PASS, NODE_NAME, DEBUG, CONFIG
try:
    from config import SSID_INCLUDE_IP
except ImportError:
    # Matches captive_portal.setup_ap()'s own default (also False, for
    # the identical reason: paired with SSID_NAME defaulting to None,
    # True here produces a truncated, non-resolving hosted-page URL.
    # An old config.py missing this setting entirely must land on the
    # same safe default a fresh, unprovisioned one does -- not silently
    # revert to the broken pairing this whole fix exists to prevent.
    SSID_INCLUDE_IP = False
try:
    from config import SSID_NAME
except ImportError:
    SSID_NAME = None   # matches captive_portal.setup_ap()'s own default
try:
    from config import REANNOUNCE_INTERVAL
except ImportError:
    REANNOUNCE_INTERVAL = 120

# Release tag, printed first thing at boot. The single most useful line
# in a field bug report: it says exactly which build is on the board,
# which no amount of reading the log afterwards can tell you.
STUMP_VERSION = "Beta A"
# The exact release (a git tag "v" + this, built and signed by
# .github/workflows/release.yml, which refuses a tag that doesn't match).
STUMP_RELEASE = "1.0.0-beta.85"

# Node-type beacon: a second destination on this node's own identity,
# aspect "stump.node", so clients can tell a Stump from a person without
# guessing from display names. The LXMF delivery announce is left exactly
# as upstream LXMF defines it -- adding fields there would risk every
# other client's parser (Sideband, MeshChat, the Android app). Announced
# at boot and every STUMP_ANNOUNCE_INTERVAL seconds; path requests for it
# are answered automatically, like any local destination.
try:
    from config import STUMP_ANNOUNCE_INTERVAL
except ImportError:
    STUMP_ANNOUNCE_INTERVAL = 1800

import gc
_gc_collect()
try:
    from urns import mem as _mem
    _mem.report("at boot")
    if _mem.headroom(2):
        _mem.report("with room to work in")
except Exception as _e:
    print("[mem] no headroom added:", _e)

# ---- Peripherals ----
import peripherals.adc_reader as adc_reader
adc_reader.init_battery(CONFIG)

active_peripherals = [adc_reader]

_gc_collect()

# ---- Echo reply ----
ECHO_REPLY = False   # superseded by rrc_mesh; kept so the old path is explicit


async def _send_msg(router, dest_hash, body):
    """Send LXMF message as async task (crypto is slow)."""
    import uasyncio as asyncio
    await asyncio.sleep(0)
    try:
        msg = router.send_message(dest_hash, body)
        if msg:
            print("[Sent] -> " + dest_hash.hex()[:8])
        else:
            print("[Error] Unknown identity: " + dest_hash.hex()[:8])
    except Exception as e:
        print("[Error] Send failed: " + str(e))
    _gc_collect()


async def serial_input_loop(router):
    """Poll stdin for /msg <hash> <body> commands.

    Entirely optional: this is the USB debugging console. A deployed
    node runs headless with nothing attached to stdin, and polling an
    invalid descriptor raises EBADF -- confirmed by simulating a
    headless boot, where the task died with "Task exception wasn't
    retrieved". A dying task is noise at best and a risk to the event
    loop at worst, so a stdin that isn't usable simply retires this
    feature instead of failing loudly forever.
    """
    import sys
    import select
    import uasyncio as asyncio
    from urns.identity import Identity

    try:
        poller = select.poll()
        poller.register(sys.stdin, select.POLLIN)
    except Exception as e:
        print("[console] serial command input unavailable (%s) -- continuing without it." % e)
        return
    buf = ""

    consecutive_errors = 0
    while True:
        try:
            ready = poller.poll(0)
            consecutive_errors = 0
        except Exception as e:
            # stdin went away mid-run (terminal detached). Retire the
            # console rather than spinning on an error every 50ms.
            consecutive_errors += 1
            if consecutive_errors > 3:
                print("[console] serial input closed (%s) -- console retired." % e)
                return
            await asyncio.sleep(1)
            continue
        if ready:
            ch = sys.stdin.read(1)
            if ch in ("\n", "\r"):
                line = buf.strip()
                buf = ""
                if line.startswith("/msg "):
                    parts = line[5:].split(" ", 1)
                    if len(parts) < 2 or len(parts[0]) < 8:
                        print("Usage: /msg <hex_hash> <message>")
                        continue
                    prefix = parts[0].lower()
                    body = parts[1]
                    match = None
                    for dh in Identity.known_destinations:
                        if dh.hex().startswith(prefix):
                            match = dh
                            break
                    if match:
                        asyncio.create_task(_send_msg(router, match, body))
                    else:
                        print("[Error] No known destination: " + prefix)
                elif line:
                    print("Unknown command. Use: /msg <hash> <message>")
            else:
                buf += ch
        await asyncio.sleep(0.05)


def _wifi_why(network, wlan):
    """Why the Wi-Fi wasn't joined, as the chip says (said on the USB:
    Arborist reads it after a setup, 1.0.0-beta.60)."""
    try:
        st = wlan.status()
    except Exception:
        return ""
    for name, why in (("STAT_WRONG_PASSWORD", "the password is wrong"),
                      ("STAT_NO_AP_FOUND", "the network wasn't found (this board only sees 2.4 GHz Wi-Fi)"),
                      ("STAT_ASSOC_FAIL", "the router refused it"),
                      ("STAT_HANDSHAKE_TIMEOUT", "no answer to the password (is it right?)"),
                      ("STAT_BEACON_TIMEOUT", "the network went out of reach")):
        if st == getattr(network, name, None):
            return ": " + why
    return ""


def connect_wifi(ssid, password, timeout=15):
    import sys
    import network
    import time

    platform = sys.platform  # "esp32" or "rp2"

    if platform == "esp32":
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            ap.active(False)
            if DEBUG >= 2:
                print("AP_IF deactivated")

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        if DEBUG >= 1:
            print("Connecting to WiFi:", ssid)
        wlan.connect(ssid, password)
        start = time.time()
        while not wlan.isconnected():
            if time.time() - start > timeout:
                raise RuntimeError("WiFi connection timed out" + _wifi_why(network, wlan))
            time.sleep(0.5)

    if platform == "esp32":
        wlan.config(pm=0)
    elif platform == "rp2":
        wlan.config(pm=0xa11140)

    ip = wlan.ifconfig()[0]
    if DEBUG >= 1:
        print("Connected! IP:", ip, "(" + platform + ")")

    try:
        import ntptime
        ntptime.settime()
        if DEBUG >= 1:
            print("NTP synced")
    except Exception as e:
        if DEBUG >= 1:
            print("NTP sync failed:", e)

    return ip


def stump_beacon_data(lxmf_dest, node_name):
    """The beacon's announce data, made fresh each time: its fifth item is
    the list of addresses the Stump's pages are on now (discovery.py), so
    an app has the current one even after the router changed it."""
    import discovery
    return discovery.beacon_data(STUMP_VERSION, node_name, bytes(lxmf_dest.hash))


def make_stump_beacon(identity, lxmf_dest, node_name):
    """The "stump.node" destination. Its announce data is a msgpack list:
        ["stump", STUMP_VERSION, node_name, <16-byte LXMF delivery hash>,
         [<web addresses, "http://192.168.0.176/">]]
    (the fifth item since 1.0.0-beta.21; readers take the first four as before)
    Same identity as the LXMF destination, so a client can also match
    the two announces by identity hash."""
    from urns.destination import Destination
    beacon = Destination(identity, Destination.IN, Destination.SINGLE, "stump", "node")
    beacon.set_default_app_data(stump_beacon_data(lxmf_dest, node_name))
    return beacon


def setup_node(rns, node_name):
    """Stump's own node bring-up: the shared identity/router mechanics
    from node_common.py, plus Stump's specific inbound-message and
    announce handling passed in as callbacks -- not hardcoded into a
    shared function other firmware also uses. See node_common.py's own
    docstring for why this split exists.
    """
    from node_common import setup_node as _shared_setup_node

    def on_message(message):
        sender = message.source_hash.hex()[:8]
        content = message.content_as_string() or "(binary)"

        results = []
        for p in active_peripherals:
            result = p.process(content)
            if result:
                results.append(result)
        if results:
            content = "\n".join(results)

        if DEBUG >= 1:
            from node_common import peer_name
            name = peer_name(router, message.source_hash)
            print()
            print("<" + name + "/" + sender + "> " + content)

        # The bridge owns replies now: an inbound message becomes a line
        # in an RRC room, and anything owed back to the sender goes out
        # through its own send queue. The old blanket echo would fight
        # with that -- every mesh message would get both a room post and
        # a parroted copy of itself.
        # Chat turned off in the provisioner (features.py) turns the mesh
        # side of it off too: nothing is posted to rooms or answered.
        try:
            import features
            chat_on = features.enabled("chat")
        except ImportError:
            chat_on = True
        if chat_on:
            try:
                import rrc_mesh
                rrc_mesh.on_message(router, message)
            except Exception as e:
                print("[rrc_mesh] inbound failed:", e)
        _gc_collect()

    def on_announce(destination_hash, display_name):
        if DEBUG >= 1:
            print("[Peer] " + (display_name or "?") + " [" + destination_hash.hex()[:8] + "]")

    dest, router = _shared_setup_node(rns, node_name, on_message=on_message, on_announce=on_announce)
    return dest, router


def load_plugins():
    """Start every plugin folder present on the board -- plugins.py does
    the work (docs/PLUGINS.md): setup(stump) for plugins written for the
    plugin API, activate() exactly as before for the ones that aren't,
    each guarded so a broken plugin is logged and skipped, never a
    reason the node fails to boot. It also keeps the safe-mode marker: a
    plugin that resets or hangs the board while starting is switched off
    at the next boot."""
    try:
        import plugins
        return plugins.load_all()
    except Exception as e:
        print("[plugin] loader failed:", e)
        return []


def needs_wifi(config):
    """Check if any UDP, TCP, or WiFi-bridge interface is enabled in config."""
    for iface in config.get("interfaces", []):
        if iface.get("enabled", False) and iface.get("type", "") in (
            "UDPInterface", "TCPClientInterface", "WiFiSerialInterface",
        ):
            return True
    return False


def main():
    """Run on MicroPython (ESP32-S3, RP2040, etc.)"""
    import uasyncio as asyncio

    print("=" * 46)
    print("Project Stump -- %s (%s)" % (STUMP_VERSION, STUMP_RELEASE))
    print("=" * 46)

    # Normal start, or a rescue start after failed ones (keepalive.py): no
    # radio, no chain, no plugins -- its Wi-Fi, the pages and /admin.
    import keepalive
    RESCUE = keepalive.begin() == "rescue"

    ip = None
    # A chain of Stumps over Wi-Fi (chain.py, docs/CHAIN.md): a member
    # Stump's station joins the Stump before it instead of WIFI_SSID.
    import chain
    CHAIN = None if RESCUE else chain.settings()
    chain_sta_ip = chain_parent_ip = None
    if CHAIN and chain.over_lan(CHAIN):
        # A member over the home Wi-Fi (CHAIN_PARENT_IP): it joins WIFI_SSID
        # like a Stump on its own (its home address, the clock, internet)
        # and links to the first Stump there, as a computer Stump does.
        try:
            import network
            network.WLAN(network.STA_IF).active(True)
            network.WLAN(network.STA_IF).config(reconnects=-1)   # the chip keeps trying by itself
        except Exception:
            pass
        try:
            ip = connect_wifi(WIFI_SSID, WIFI_PASS)
            chain_sta_ip, chain_parent_ip = chain.note_lan_uplink(CHAIN, ip)
        except Exception as e:
            print("[chain] the router (%s) isn't joined yet (%s): it keeps trying; nothing else is joined"
                  % (WIFI_SSID, e))
    elif CHAIN and CHAIN["uplink_ssid"]:
        try:
            chain_sta_ip, chain_parent_ip = chain.connect_uplink(CHAIN)
        except Exception as e:
            print("[chain] uplink not joined (" + str(e) + "); will keep trying")
    elif needs_wifi(CONFIG) or (CHAIN and CHAIN["lan"] and CHAIN["secret"] and WIFI_SSID):
        # (the first Stump of a chain also takes chain links there: chain.py)
        # Degrade, don't die. An unreachable upstream network (router
        # down, wrong password, or simply no WiFi where this is
        # deployed) used to raise straight out of main(), which main.py
        # catches by resetting -- an infinite boot loop that never
        # reached the AP bring-up below, so the entire local walk-up
        # side (Billboard, BarKeep, fserv, captive portal) never
        # started either, despite none of it needing upstream WiFi.
        # The Heltec bridge does need the LAN, but WiFiSerialInterface
        # already swallows its own initial connect failure and retries
        # in its poll loop, so it reconnects by itself if the network
        # turns up later. Local services shouldn't wait on that.
        try:
            ip = connect_wifi(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            print("WiFi unavailable (" + str(e) + ")")
            print("Continuing without it -- local AP, billboard, files and chat all")
            print("still work. The Heltec bridge will keep retrying in the background.")

    # ---- Project Stump integration (captive portal + BarKeep + fserv) ----
    import captive_portal
    # The AP is named after this node, not a hardcoded "Stump" -- the
    # whole point of asking for a node name in the wizard is that it
    # shows up where people actually look, which is the Wi-Fi list.
    # The LAN address is still printed by connect_wifi() above, so a
    # technician on the serial console can read it -- it just isn't
    # broadcast in the network name any more.
    ap_ip = captive_portal.setup_ap(
        SSID_NAME, include_ip=SSID_INCLUDE_IP,
        ap_ip=chain.ap_ip(CHAIN) if (CHAIN or not RESCUE) else None,     # its number, in a chain or not (rescue: the default)
        # The root sets the chain's channel; a member's access point
        # follows its uplink's channel whatever it's told.
        channel=CHAIN["channel"] if CHAIN and chain.is_root(CHAIN) else None)
    import barkeep
    import fserv
    # After rrc/barkeep exist (plugins may wrap them) and before the
    # server starts taking requests. Every plugin folder present gets
    # activated here, individually guarded -- a broken or missing
    # plugin is logged and skipped, never a reason the node fails to
    # boot. (This used to also have a hand-written fservbot-specific
    # activation block directly below; that was leftover from before
    # this generic loader existed and just activated it a second time,
    # caught only by fservbot's own re-entry guard. Removed.)
    # The card first: plugins read their saved state while they start
    # (fservbot's dialogues and stumpid's identities live on it). With
    # the plugins first, fservbot looked before /sd existed, found
    # nothing, and ran on its defaults until the next reboot -- every
    # reboot.
    fserv.mount_sd()
    if not RESCUE:
        load_plugins()
    # Move any flash-stored billboard onto the card now that it's
    # mounted, so posts written before a card was fitted aren't
    # stranded on flash and replaced by an empty SD-backed board.
    import billboard as _bb
    _bb.migrate_to_sd()
    _gc_collect()

    from urns import Reticulum
    from urns.log import LOG_NONE, LOG_NOTICE, LOG_DEBUG

    # Announce rebroadcast throttling. Real and already enforced inside
    # urns/transport.py -- but the two values that govern it were
    # MicroPython const(), unreachable from config.py, so no deployment
    # could tune them without editing urns/const.py directly and
    # reflashing. const() only inlines bare-name references WITHIN the
    # file that defines them; accessed cross-module as an attribute
    # (which is how transport.py reads them: `from . import const`,
    # then `const.ANNOUNCE_RATE_WINDOW` at call time) it's an ordinary,
    # mutable module attribute -- confirmed directly, not assumed.
    # Overriding here, before Reticulum() brings up Transport's own
    # maintenance loop, means every later read sees the configured
    # value. Left untouched (no override) when config.py doesn't
    # specify them, so an older config.py keeps today's exact defaults.
    try:
        from config import ANNOUNCE_RATE_MAX, ANNOUNCE_RATE_WINDOW
        import urns.const as _const
        _const.ANNOUNCE_RATE_MAX = ANNOUNCE_RATE_MAX
        _const.ANNOUNCE_RATE_WINDOW = ANNOUNCE_RATE_WINDOW
    except ImportError:
        pass

    log_map = {0: LOG_NONE, 1: LOG_NONE, 2: LOG_DEBUG}
    rns = Reticulum(loglevel=log_map.get(DEBUG, LOG_NOTICE))
    rns.config = CONFIG
    if RESCUE:
        rns.config = dict(CONFIG)
        rns.config["interfaces"] = []        # the radio may be what fails

    dest, router = setup_node(rns, NODE_NAME)
    # Before any interface is up: announces heard in the first seconds
    # (another Stump's beacon, on a chain) would otherwise be missed until
    # its next re-announce, many minutes later.
    try:
        import rrc_mesh
        rrc_mesh.install_announce_handler(router)
    except Exception as e:
        print("[rrc_mesh] announce handler not installed:", e)
    # Reported in /rrc/poll as "node", so a client can tell the Wi-Fi
    # session and the LXMF address are the same node.
    try:
        import rrc
        import webtext
        # "key": the identity's public key (X25519 then Ed25519, 64 bytes),
        # base64url: /address's QR code carries it, so an app can check it
        # against the address and reach this Stump without an announce.
        rrc.NODE = {"name": NODE_NAME, "lxmf": dest.hexhash, "key": webtext.b64url(rns.identity.get_public_key())}
        rrc.NODE_IDENTITY[0] = rns.identity          # signs its half of /hello
        rrc.NODE_INFO = {"version": STUMP_VERSION, "release": STUMP_RELEASE}
    except Exception:
        pass
    try:
        import plugins
        plugins.network_ready(rns.identity)      # plugins' own destinations (API 2)
    except Exception as e:
        print("[plugin] network not ready for plugins:", e)
    _gc_collect()

    # Radio settings saved from /admin override config.py's bridge entry,
    # written in before the interfaces exist so the Heltec's very first
    # connection already gets them (see radio.py).
    try:
        import radio
        radio.overlay(rns.config)
    except Exception as e:
        print("[radio] saved settings not applied:", e)
    if CHAIN:
        # Chain links, and transport on: a Stump in a chain relays for the
        # Stumps and PCs on either side of it.
        rns.config["interfaces"] = list(rns.config.get("interfaces", [])) + \
            chain.interfaces(CHAIN, chain_sta_ip, chain_parent_ip, ap_ip, lan_ip=ip)
        rns.config["enable_transport"] = True
        print("[chain] %s on %s%s%s" % ("root" if chain.is_root(CHAIN) else "member", ap_ip,
                                        ", channel %d" % CHAIN["channel"] if CHAIN["channel"] and chain.is_root(CHAIN) else "",
                                        " and on %s (the Wi-Fi it joined)" % chain._state["lan_ip"]
                                        if chain._state["lan_ip"] else ""))
    # A computer Stump also finds, and is found by, Reticulum apps on its
    # networks with no setup (NomadNet, Sideband, MeshChat: their default
    # AutoInterface). /admin -> This computer switches it off. A board
    # can't (IPv4-only sockets).
    try:
        import settings as _settings
        import config as _cfg
        if not RESCUE and _settings.on_computer() and getattr(_cfg, "LOCAL_DISCOVERY", True):
            rns.config["interfaces"] = list(rns.config.get("interfaces", [])) + \
                [{"type": "AutoInterface", "name": "Local network", "enabled": True}]
    except Exception as e:
        print("[auto] not added:", e)
    rns.setup_interfaces()
    # Who may be in #lora-mesh follows the access mode, like every room
    # (stumpid: open, hybrid, mandatory). LORA_MESH_LOCKED = True in
    # config.py still keeps it to LoRa users, this computer and whom they
    # invite (/lora invite) -- off unless asked for.
    try:
        import rrc as _rrc
        import config as _cfg
        locked = bool(getattr(_cfg, "LORA_MESH_LOCKED", False))
        _rrc.LORA_LOCKED[0] = locked
        if locked:
            print("[rrc] #lora-mesh is the radio's room: LoRa users, this computer, and invitees")
    except Exception as e:
        print("[rrc] #lora-mesh lock not set:", e)
    if CHAIN:
        chain.attach(rns)
        import rrc as _rrc
        _rrc.open_chain_room()     # #wifi-mesh: the other Stumps of the chain
    _gc_collect()

    if DEBUG >= 1:
        print("LXMF address:", dest.hexhash)
        print("Free memory:", gc.mem_free(), "bytes")
        print("Running... (Ctrl+C to stop)")

    import announcer          # the interval (/admin, live) and "Announce now"

    async def initial_announce():
        await asyncio.sleep(0.5)
        try:
            router.announce()
            announcer.done()
            if DEBUG >= 1:
                print("Announced as:", NODE_NAME)
        except Exception as e:
            if DEBUG >= 2:
                print("Initial announce error:", e)
        _gc_collect()

    try:
        beacon = make_stump_beacon(rns.identity, dest, NODE_NAME)
        if DEBUG >= 1:
            print("Stump beacon:", beacon.hexhash, "(stump.node)")
    except Exception as e:
        beacon = None
        print("Stump beacon not created:", e)

    # LXMF propagation node (store-and-forward), when switched on in the
    # provisioner or /admin (see propagation.py).
    try:
        import propagation
        propagation.attach(router, NODE_NAME)
        if propagation.configured_on():
            propagation.enable()
    except Exception as e:
        print("[pn] not started:", e)

    async def pn_announce_loop():
        # Its own slow cycle, offset from the LXMF announce and the Stump
        # beacon so the signing stalls never coincide.
        await asyncio.sleep(10)
        while True:
            try:
                if propagation._enabled:
                    propagation.announce()
            except Exception as e:
                print("[pn] announce error:", e)
            _gc_collect()
            await asyncio.sleep(STUMP_ANNOUNCE_INTERVAL)

    async def beacon_loop():
        # Announce signing freezes the event loop (see reannounce_loop
        # below), so this runs on its own slow cycle, started 5 s after
        # boot -- offset from the 2-minute LXMF announce so the two
        # stalls never land on top of each other.
        await asyncio.sleep(5)
        while True:
            try:
                from urns.interfaces.wifi_serial import prime_all_bridges
                prime_all_bridges()
            except Exception:
                pass
            try:
                beacon.announce(app_data=stump_beacon_data(dest, NODE_NAME))
                if DEBUG >= 2:
                    print("[Stump beacon announced]")
            except Exception as e:
                if DEBUG >= 2:
                    print("Beacon announce error:", e)
            _gc_collect()
            # Until the next beacon -- or sooner, when a new chain link
            # comes up: the Stump at the other end has to hear this
            # beacon (and the LXMF announce) to put us in #wifi-mesh.
            waited = 0
            while waited < STUMP_ANNOUNCE_INTERVAL:
                await asyncio.sleep(5)
                waited += 5
                try:
                    import chain as _chain
                    if _chain.take_link_event() or _chain.take_owed():
                        await asyncio.sleep(3)
                        try:
                            router.announce()
                        except Exception:
                            pass
                        await asyncio.sleep(2)
                        break
                except ImportError:
                    pass

    async def reannounce_loop():
        while True:
            # Every announcer.every() seconds (set live from /admin), or at
            # once when /admin asks ("Announce now"), counted a second at a
            # time -- no wall clock needed.
            waited, manual = 0, False
            while waited < announcer.every():
                await asyncio.sleep(1)
                announcer.tick()
                waited += 1
                if announcer.take():
                    manual = True
                    break
            # router.announce() is fully synchronous -- confirmed zero
            # yield points anywhere in its call chain -- so this freezes
            # the entire event loop, including the Heltec bridge's own
            # keepalive and receive servicing, for however long the
            # signing takes. Diagnosed against a field report: a steady
            # ~2-minute cadence of the bridge dropping and re-sending its
            # full radio config matched this loop's interval exactly,
            # independent of any chat activity -- confirming this
            # specific call as a standing, unconditional cause.
            #
            # Priming right before it can't make the freeze shorter --
            # nothing on this side of a synchronous crypto call can do
            # that -- but it resets the bridge's inactivity clock to zero
            # right as the freeze begins, rather than the freeze starting
            # partway through whatever idle time had already elapsed.
            # That's the difference between the freeze eating into the
            # Heltec's own disconnect tolerance and eating past it.
            try:
                from urns.interfaces.wifi_serial import prime_all_bridges
                prime_all_bridges()
            except Exception:
                pass  # never let a diagnostic aid block the actual announce
            try:
                router.announce()
                announcer.done()
                if manual:
                    print("[announce] on demand (/admin): LXMF address", dest.hexhash)
                elif DEBUG >= 2:
                    print("[Re-announced]")
            except Exception as e:
                if DEBUG >= 2:
                    print("Re-announce error:", e)
            if manual and beacon is not None:
                # The Stump beacon too, so the other Stumps learn its name
                # at once (its own cycle is half an hour).
                await asyncio.sleep(2)
                try:
                    beacon.announce(app_data=stump_beacon_data(dest, NODE_NAME))
                except Exception as e:
                    print("[announce] beacon:", e)
            _gc_collect()

    _original_run = rns.run

    async def run_with_reannounce():
        asyncio.create_task(keepalive.loop())
        # LXMF stamps (opt-in, /admin): tickets kept on the board, the
        # stamp maker idle unless a contact asks for one.
        try:
            from urns import lxmf as _lx
            import config as _cfg
            _lx.STAMPS[0] = bool(getattr(_cfg, "LXMF_STAMPS", False))
            router.load_tickets("lxmf_tickets.bin")
            asyncio.create_task(router.stamp_loop())
        except Exception as e:
            print("[stamps] not started:", e)
        # Announce stamps (Reticulum 1.6.1; opt-in, /admin; 1.0.0-beta.86):
        # the ones kept, and the maker, idle unless it's on and one's missing.
        try:
            from urns import dstamp as _ds
            from urns.identity import Identity as _Id
            import config as _cfg
            import sys as _sys
            _ds.load(_Id.storagepath)
            _ds.set_enabled(getattr(_cfg, "ANNOUNCE_STAMP", False))

            def _stamped():
                import announcer
                announcer.request()          # announced again soon, stamped (NOW_GAP apart)
            _ds.ON_FOUND[0] = _stamped
            asyncio.create_task(_ds.loop(budget_ms=30 if _sys.platform == "esp32" else 100))
        except Exception as e:
            print("[stamps] announce stamps not started:", e)
        asyncio.create_task(initial_announce())
        asyncio.create_task(reannounce_loop())
        if beacon is not None:
            asyncio.create_task(beacon_loop())
        try:
            import propagation
            # Always started: /admin can switch the node on at runtime, and
            # both idle cheaply while it's off or has nothing to check.
            asyncio.create_task(propagation.checker_loop())
            asyncio.create_task(pn_announce_loop())
        except Exception as e:
            print("[pn] tasks not started:", e)
        asyncio.create_task(serial_input_loop(router))
        try:
            # "Who's a Stump here?" on the local network, and the beacon
            # again when the router gives this Stump a new address.
            import discovery
            asyncio.create_task(discovery.loop())
            asyncio.create_task(discovery.watch_loop())
        except Exception as e:
            print("[discovery] not started:", e)
        asyncio.create_task(barkeep.run_barkeep_server())
        if CHAIN:
            asyncio.create_task(chain.watch_loop(rns))
        try:
            import plugins
            # Plugin timers, and the minute after which this boot counts
            # as good (clears the safe-mode marker).
            asyncio.create_task(plugins.scheduler_loop())
            asyncio.create_task(plugins.stable_loop())
        except Exception as e:
            print("[plugin] tasks not started:", e)
        # The DNS redirect is what actually makes the captive portal
        # work -- setup_ap() above only brings the AP interface up.
        # Without this task running, a phone joining "Stump" gets no
        # "Sign in to network" prompt and silently falls back to
        # cellular.
        #
        # No try/except here on purpose: create_task only SCHEDULES the
        # coroutine, so nothing it can raise happens yet, and any
        # success message printed here would be a claim about a bind
        # that hasn't been attempted. Both tasks report their own real
        # status ([dns] / [web] lines) once they've actually tried.
        if ap_ip:
            asyncio.create_task(captive_portal.run_dns_server(ap_ip))
        # Drains the bridge's outbound queue and pushes new room lines
        # to subscribed mesh peers.
        try:
            import features
            chat_on = features.enabled("chat")
        except ImportError:
            chat_on = True
        if chat_on:
            import rrc_mesh
            asyncio.create_task(rrc_mesh.poll_loop(router))
        elif DEBUG >= 1:
            print("[features] chat is off: mesh chat bridge not started")
        await _original_run()

    try:
        asyncio.run(run_with_reannounce())
    except KeyboardInterrupt:
        # Stopped on purpose (Ctrl+C, the provisioner): not a crash, so
        # the next boot shouldn't start in plugin safe mode -- nor count
        # as a failed start, nor be reset by the watchdog mid-upload.
        keepalive.console_stop()
        try:
            import plugins
            plugins.clear_marker()
        except Exception:
            pass
        rns.shutdown()
        if DEBUG >= 1:
            print("Shutdown complete")


main()
