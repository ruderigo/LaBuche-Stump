# Project Stump -- Captive Portal (DNS redirect)
# Place at: firmware/captive_portal.py
#
# Makes Stump's own AP behave like a normal public WiFi hotspot: a phone
# joining gets an automatic "Sign in to network" prompt instead of
# silently routing to cellular -- which is what broke the very first
# walk-up test (page never loaded on the phone, even though the server
# itself was proven fine from the Mac).
#
# DNS packet construction below is confirmed against a real, working
# reference implementation (p-doyle/Micropython-DNSServer-Captive-Portal,
# read directly, not reconstructed from general DNS-format knowledge --
# same discipline as the KISS protocol fix).
#
# HOW IT WORKS: every DNS query a joined phone makes gets answered with
# Stump's own AP IP, regardless of what domain was actually asked for.
# When the phone's background connectivity check (Android:
# connectivitycheck.gstatic.com/generate_204, expecting a 204; iOS:
# captive.apple.com, expecting specific content) gets Stump's landing
# page back instead of the expected answer, the OS concludes "this
# network has no real internet" and shows the sign-in prompt. Tapping it
# opens a browser straight to whatever Stump serves.
#
# REAL, CONFIRMED LIMITATIONS -- not edge cases, documented consistently
# across every source checked on this:
#   - Only plain HTTP redirects this way. HTTPS can't -- there's no valid
#     TLS certificate to present for a spoofed domain, so an HTTPS-first
#     probe just times out instead of redirecting.
#   - The automatic popup is not guaranteed on every device or OS
#     version. Fallback that still works regardless: a person opening
#     any plain HTTP address manually still lands on Stump's page, since
#     DNS redirects everything the same way either way.

import socket
import gc
import uasyncio as asyncio


class DNSQuery:
    """Parses just enough of a raw DNS query to extract the domain name.
    We don't care what was actually asked -- every query gets the same
    answer, Stump's own IP."""

    def __init__(self, data):
        self.data = data
        self.domain = ""
        opcode = (data[2] >> 3) & 15
        if opcode == 0:  # standard query
            i = 12
            length = data[i]
            while length != 0:
                self.domain += data[i + 1:i + length + 1].decode("utf-8") + "."
                i += length + 1
                length = data[i]

    def response(self, ip):
        """Builds a valid DNS A-record response pointing whatever domain
        was queried at `ip`. Confirmed exact against the reference
        implementation's own working packet layout."""
        if not self.domain:
            return None
        packet = self.data[:2] + b"\x81\x80"                    # ID + standard response flags
        packet += self.data[4:6] * 2 + b"\x00\x00\x00\x00"       # QDCOUNT=ANCOUNT=1, NSCOUNT=ARCOUNT=0
        packet += self.data[12:]                                  # echo the original question section
        packet += b"\xC0\x0C"                                     # name-compression pointer back to it
        packet += b"\x00\x01\x00\x01\x00\x00\x00\x3C\x00\x04"    # TYPE=A, CLASS=IN, TTL=60s, RDLENGTH=4
        packet += bytes(int(o) for o in ip.split("."))            # the 4 IP bytes
        return packet


async def run_dns_server(ap_ip):
    """Background task: answers every DNS query on port 53 with `ap_ip`.
    Meant to be added via asyncio.create_task() alongside example_node.py's
    other background tasks (initial_announce, reannounce_loop, etc.) --
    same event loop, not a separate thread.

    Reports its own success or failure. A caller can't do this: bind()
    happens here, inside the task, long after create_task() returned --
    so a try/except around create_task catches nothing, and any message
    the caller prints is a claim about something that hasn't happened
    yet. The old arrangement announced 'DNS running' and then let the
    task die silently on a bind error, leaving a boot log that actively
    misled anyone trying to work out why the captive portal was dead."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setblocking(False)
        # getaddrinfo(...)[0][-1], NOT a raw ("0.0.0.0", 53) tuple.
        # MicroPython's usocket rejects a plain tuple here with
        # "TypeError: object with buffer protocol required" -- the same
        # requirement that already applies to connect() in
        # wifi_serial.py. This line threw on the very first statement of
        # the task, so the DNS server died instantly on every boot and
        # the captive-portal prompt never appeared. Verified against the
        # real interpreter, both the failure and this fix.
        addr = socket.getaddrinfo("0.0.0.0", 53)[0][-1]
        s.bind(addr)
    except Exception as e:
        print("[dns] FAILED to bind port 53:", e)
        print("[dns] the captive-portal sign-in prompt will not appear.")
        print("[dns] everything else still works; browse to http://%s/ directly." % ap_ip)
        try:
            s.close()
        except Exception:
            pass
        return
    print("[dns] captive portal answering on port 53 ->", ap_ip)
    idle = 0
    try:
        while True:
            handled = False
            try:
                data, addr = s.recvfrom(512)
                q = DNSQuery(data)
                reply = q.response(ap_ip)
                if reply:
                    s.sendto(reply, addr)
                handled = True
            except OSError:
                pass  # nothing waiting right now -- normal, not an error
            except Exception as e:
                # A malformed query shouldn't end the DNS service for
                # everyone else -- phones send some strange packets.
                print("[dns] bad query ignored:", e)

            # Collect after real work, and otherwise only occasionally.
            # This used to run gc.collect() every single pass -- twenty
            # full collections per second, forever, mostly over an idle
            # socket. On an ESP32-S3 with a multi-megabyte PSRAM heap a
            # full sweep is expensive, and uasyncio is cooperative, so
            # that starved the web server and Reticulum along with it.
            idle += 1
            if handled or idle >= 200:      # ~10s when idle
                gc.collect()
                idle = 0
            await asyncio.sleep_ms(50)
    finally:
        s.close()
        print("[dns] captive portal DNS stopped")


# NOTE: the placeholder landing page that used to live here is gone --
# barkeep.py serves the real page on port 80, and this module only needs
# to answer DNS. Two servers on port 80 could never both bind anyway.


DEFAULT_SSID = "LaBuche-Stump.web.app"


def setup_ap(essid=None, include_ip=False):
    """Brings up the AP interface, open (no password). Returns the AP's
    own IP.

    The SSID defaults to DEFAULT_SSID -- a real, publicly hosted page
    that explains what this network is and how to use it, so someone
    scanning nearby WiFi networks can type the name straight into a
    browser (on their own data, before ever joining) and get an answer
    without needing this node to be reachable first. essid overrides
    it with a custom name instead; None or an empty string both fall
    through to the default.

    Was previously "LaBuche " plus the node's own display name (the
    same name used for its mesh identity) -- decoupled on purpose. The
    WiFi hotspot's name and the mesh identity's display name are
    genuinely different concerns; forcing them to share one value meant
    the hosted-page default couldn't exist at all without also renaming
    the node on the mesh, which was never the intent.

    include_ip appends " <ap_ip>" -- defaults to False specifically
    because essid defaults to None (the hosted page): that combination
    is 33 characters, one over WiFi's hard 32-byte SSID limit, and
    truncating a real web address by even one character breaks it as
    something a browser can resolve. Confirmed directly, not just
    reasoned about: an earlier version of this function defaulted
    include_ip to True regardless, and calling setup_ap() with no
    arguments at all -- its own signature inviting exactly that --
    produced "LaBuche-Stump.web. 192.168.4.1" (silently missing "app")
    from a plain, unprovisioned boot. The Provisioner wizard already
    enforces this pairing by construction when a technician runs it
    (the IP question is only ever asked in the custom-name branch);
    this default is what protects everyone else -- a fresh, unflashed
    template, a test script, anything calling this function directly.

    ap_ip is Stump's own AP address -- always 192.168.4.1 in practice
    (MicroPython's ESP32 default, stable across boots). When included,
    it's broadcast because the captive-portal redirect isn't guaranteed
    on every device or OS version, and when it doesn't fire, the
    address in the network name is what someone falls back to.

    The LAN address is deliberately never here, IP suffix on or off. It
    only helps people already on the upstream network, who can be told
    it directly, and it previously consumed nearly the whole 32-byte
    field on its own -- forcing the AP address to be abbreviated and
    the node name clipped, to advertise an address most people reading
    the WiFi list couldn't have used anyway.
    """
    import network
    import time as _time
    ap = network.WLAN(network.AP_IF)
    ap.active(True)

    # Wait for the interface to actually come up before reading its
    # address. active(True) returns before the driver has finished, so
    # reading ifconfig() immediately gives 0.0.0.0 -- which then got
    # baked into the broadcast SSID, so the name advertised the wrong
    # address to everyone looking at the WiFi list.
    ap_ip = "0.0.0.0"
    for _ in range(20):                      # up to ~2s
        try:
            ap_ip = ap.ifconfig()[0]
        except Exception:
            ap_ip = "0.0.0.0"
        if ap_ip and ap_ip != "0.0.0.0":
            break
        _time.sleep(0.1)
    if not ap_ip or ap_ip == "0.0.0.0":
        ap_ip = "192.168.4.1"                # MicroPython's ESP32 default
        print("[ap] interface slow to report an address, assuming", ap_ip)

    # WiFi's SSID limit is a hard 32 BYTES, not a soft guideline.
    # DEFAULT_SSID is 21 characters -- fits alone with room to spare,
    # but DEFAULT_SSID + " " + a dotted-quad IP is 33, one character
    # OVER the limit. That one character matters more here than it
    # would for an ordinary name: clipping "...web.app" to "...web.ap"
    # doesn't just shorten a label, it breaks a real, typeable web
    # address. The Provisioner wizard checks for and warns about this
    # exact combination before it ever reaches here; this function
    # still truncates safely if it happens anyway, rather than crash
    # or silently exceed the hardware limit.
    base = (essid or DEFAULT_SSID).rstrip()
    if include_ip:
        ap_part = " " + ap_ip
        keep = 32 - len(ap_part)
        name = base[:keep].rstrip() if keep > 0 else ""
        full_essid = (name + ap_part)[:32]
    else:
        full_essid = base[:32].rstrip()

    # Set the SSID on its own first. Passing authmode alongside it is
    # rejected on some builds, and because config() is all-or-nothing
    # that failure used to leave the AP with no SSID set at all -- an
    # access point that exists but can't be found or joined, which looks
    # exactly like "the AP doesn't work". Open is the default with no
    # password, so the authmode call is a refinement, not a requirement.
    try:
        ap.config(essid=full_essid)
    except Exception as e:
        print("[ap] could not set SSID:", e)
    try:
        ap.config(authmode=network.AUTH_OPEN)
    except Exception:
        pass  # already open by default when no password is set

    print("[ap] '%s' up on %s (active=%s)" % (full_essid, ap_ip, ap.active()))
    return ap_ip
