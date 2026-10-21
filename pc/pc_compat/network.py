# The board's Wi-Fi (network.WLAN), on a computer (docs/PC.md).
#
# The computer's operating system owns Wi-Fi: its own hotspot (Windows
# Mobile Hotspot, macOS Internet Sharing) or a router the computer joined.
# So this stand-in only answers questions -- the hotspot's address and
# name -- and politely ignores requests to change them.
import stump_host

STA_IF = 0
AP_IF = 1
AUTH_OPEN = 0
STAT_GOT_IP = 1010


class WLAN:
    def __init__(self, interface=STA_IF):
        self._if = interface

    def active(self, on=None):
        return True

    def isconnected(self):
        return True

    def connect(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass

    def ifconfig(self, new=None):
        if new is not None:
            return None                    # the OS sets addresses, not Stump
        ip = stump_host.hotspot_ip()
        gw = stump_host.parent_ip() if self._if == STA_IF else ""
        return (ip, "255.255.255.0", gw or ip, ip)

    def config(self, *args, **kwargs):
        if args:
            key = args[0]
            if key == "essid":
                import config as c
                return (stump_host.setting("PC_HOTSPOT_NAME", "")
                        or getattr(c, "SSID_NAME", None) or "Stump")
            if key == "mac":
                import uuid
                return uuid.getnode().to_bytes(6, "big")
            return None
        return None                        # settings are the OS's

    def status(self, key=None):
        if key == "rssi":
            return 0
        return STAT_GOT_IP

    def scan(self):
        return []
