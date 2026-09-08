"""
µReticulum Example: LXMF Messaging Node
========================================
Compatible with MeshChat, Sideband, NomadNet over UDP on the same LAN.

Project Stump build: this file is the COMPLETE, already-integrated
version -- captive_portal, barkeep, and fserv are wired in below. No
patch scripts needed; this file can be dropped onto the device as-is.
"""

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

import gc
gc.collect()

# ---- Peripherals ----
import peripherals.adc_reader as adc_reader
adc_reader.init_battery(CONFIG)

active_peripherals = [adc_reader]

gc.collect()

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
    gc.collect()


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


async def send_echo_reply(router, source_hash, content):
    """Send echo reply as async task (crypto takes ~7s, must not block poll loop)."""
    import uasyncio as asyncio
    await asyncio.sleep(0)
    try:
        reply_content = "Echo: " + str(content)
        msg = router.send_message(source_hash, reply_content)
        if msg:
            if DEBUG >= 1:
                print("[Echo] Replied to " + source_hash.hex()[:8])
        else:
            if DEBUG >= 1:
                print("[Echo] Cannot reply to " + source_hash.hex()[:8] + " (unknown identity)")
    except Exception as e:
        from urns.log import log, LOG_ERROR
        log("Echo reply error: " + str(e), LOG_ERROR)
    gc.collect()


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
                raise RuntimeError("WiFi connection timed out")
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


def setup_node(rns, node_name):
    """Stump's own node bring-up: the shared identity/router mechanics
    from node_common.py, plus Stump's specific inbound-message and
    announce handling passed in as callbacks -- not hardcoded into a
    shared function other firmware also uses. See node_common.py's own
    docstring for why this split exists.
    """
    from node_common import setup_node as _shared_setup_node

    def on_message(message):
        import uasyncio as asyncio

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
        try:
            import rrc_mesh
            rrc_mesh.on_message(router, message)
        except Exception as e:
            print("[rrc_mesh] inbound failed:", e)
        gc.collect()

    def on_announce(destination_hash, display_name):
        if DEBUG >= 1:
            print("[Peer] " + (display_name or "?") + " [" + destination_hash.hex()[:8] + "]")

    dest, router = _shared_setup_node(rns, node_name, on_message=on_message, on_announce=on_announce)
    return dest, router


def load_plugins():
    """Activate every plugin folder present on the board.

    A plugin is any directory shipping an install.py with an activate().
    Discovering them rather than naming them means dropping a folder in
    and re-running the wizard is the whole install -- no edit here, no
    new hash on this file, for the second plugin or the twentieth.

    Deliberately forgiving: a plugin that fails to import or activate
    logs and is skipped. An add-on must never be able to stop the node
    booting, and the alternative -- a boot loop with no web server -- is
    exactly the failure that is hardest to diagnose in the field.
    """
    import os
    activated = []
    try:
        entries = os.listdir()
    except Exception:
        return activated
    for name in sorted(entries):
        if name.startswith(".") or name in ("urns", "lib", "peripherals"):
            continue
        try:
            os.stat(name + "/install.py")
        except OSError:
            continue  # not a plugin folder
        try:
            mod = __import__(name + ".install", None, None, ("activate",))
            if mod.activate():
                activated.append(name)
        except Exception as e:
            print("[plugin] %s did not activate: %s" % (name, e))
    if activated:
        print("[plugin] active:", ", ".join(activated))
    return activated


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
    print("Project Stump -- %s" % STUMP_VERSION)
    print("=" * 46)

    ip = None
    if needs_wifi(CONFIG):
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
    ap_ip = captive_portal.setup_ap(SSID_NAME, include_ip=SSID_INCLUDE_IP)
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
    load_plugins()
    fserv.mount_sd()
    # Move any flash-stored billboard onto the card now that it's
    # mounted, so posts written before a card was fitted aren't
    # stranded on flash and replaced by an empty SD-backed board.
    import billboard as _bb
    _bb.migrate_to_sd()
    gc.collect()

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

    dest, router = setup_node(rns, NODE_NAME)
    gc.collect()

    rns.setup_interfaces()
    gc.collect()

    if DEBUG >= 1:
        print("LXMF address:", dest.hexhash)
        print("Free memory:", gc.mem_free(), "bytes")
        print("Running... (Ctrl+C to stop)")

    async def initial_announce():
        await asyncio.sleep(0.5)
        try:
            router.announce()
            if DEBUG >= 1:
                print("Announced as:", NODE_NAME)
        except Exception as e:
            if DEBUG >= 2:
                print("Initial announce error:", e)
        gc.collect()

    async def reannounce_loop():
        while True:
            await asyncio.sleep(REANNOUNCE_INTERVAL)
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
                if DEBUG >= 2:
                    print("[Re-announced]")
            except Exception as e:
                if DEBUG >= 2:
                    print("Re-announce error:", e)
            gc.collect()

    _original_run = rns.run

    async def run_with_reannounce():
        asyncio.create_task(initial_announce())
        asyncio.create_task(reannounce_loop())
        asyncio.create_task(serial_input_loop(router))
        asyncio.create_task(barkeep.run_barkeep_server())
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
        import rrc_mesh
        asyncio.create_task(rrc_mesh.poll_loop(router))
        await _original_run()

    try:
        asyncio.run(run_with_reannounce())
    except KeyboardInterrupt:
        rns.shutdown()
        if DEBUG >= 1:
            print("Shutdown complete")


main()
