# A CAM whose Stump above goes away finds another Stump of the same chain
# in range and joins the strongest that fits (chain.py: find_new_parent),
# never one below itself (a circle), another chain, or a Stump cut off
# from the first one. Fake Wi-Fi: a scan list, and what each network's
# Stump answers on /chain.json.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config, chain
import uasyncio as asyncio
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


config.CHAIN_ENABLED = True
config.CHAIN_UPLINK_SSID = "Stump 4"
config.CHAIN_UPLINK_PASS = ""
config.CHAIN_SUBNET = 6
config.CHAIN_SECRET = "s3cret"
s = chain.settings()
chain.JOIN_S = 1
ME = "192.168.6.1"
chain._state["ap_ip"] = ME
OURS = chain.chain_id(s)
config.CHAIN_SECRET = "other"
THEIRS = chain.chain_id()
config.CHAIN_SECRET = "s3cret"
check("the chain's id: the same for the same secret, another for another, no secret in it",
      OURS == chain.chain_id(s) and OURS != THEIRS and "s3cret" not in OURS and len(OURS) == 12, (OURS, THEIRS))

# What's in range: (ssid, signal dBm, open?) and the Stump behind each.
AIR = {
    "Cafe Free WiFi": (-40, True, "10.0.0.1", None),                                  # not a Stump
    "Stump 7 (below me)": (-45, True, "192.168.7.1",
                           {"enabled": True, "chain": OURS, "path": ["192.168.4.1", "192.168.6.1", "192.168.7.1"]}),
    "Neighbours Stump": (-50, True, "192.168.9.1", {"enabled": True, "chain": THEIRS, "path": ["192.168.9.1"]}),
    "Stump 8 (cut off)": (-55, True, "192.168.8.1", {"enabled": True, "chain": OURS, "path": []}),
    "Stump 5": (-60, True, "192.168.5.1",
                {"enabled": True, "chain": OURS, "path": ["192.168.4.1", "192.168.5.1"]}),
    "Locked home Wi-Fi": (-30, False, "192.168.0.1", None),
    "My own Wi-Fi": (-20, True, ME, None),
}


class STA:
    def __init__(self):
        self.on = None
        self.tried = []

    def scan(self):
        return [(k.encode(), b"\x00" * 6, 11, v[0], 0 if v[1] else 3, False) for k, v in AIR.items()]

    def disconnect(self):
        self.on = None

    def connect(self, ssid, pw=""):
        self.tried.append(ssid)
        self.on = ssid if ssid in AIR else None

    def isconnected(self):
        return self.on is not None

    def ifconfig(self):
        gw = AIR[self.on][2]
        return (gw[:-1] + "50", "255.255.255.0", gw, gw)


class AP:
    def config(self, k):
        return "My own Wi-Fi"


sta = STA()


class Net:
    STA_IF, AP_IF = 0, 1

    @staticmethod
    def WLAN(i):
        return sta if i == 0 else AP()


def get(ip, path):
    for k, v in AIR.items():
        if v[2] == ip:
            return v[3]
    return None

chain._state["known_path"] = ["192.168.4.1", "192.168.6.1"]
r = asyncio.run(chain.find_new_parent(s, net=Net, get=get))
check("joins the strongest Stump of this chain that still reaches the first one",
      r == ("Stump 5", "192.168.5.50", "192.168.5.1"), r)
check("never its own Wi-Fi, never a locked network", "My own Wi-Fi" not in sta.tried
      and "Locked home Wi-Fi" not in sta.tried, sta.tried)
check("skipped: not a Stump, one below itself, another chain, one cut off",
      sta.tried[:4] == ["Cafe Free WiFi", "Stump 7 (below me)", "Neighbours Stump", "Stump 8 (cut off)"], sta.tried)
check("/chain.json says it joined another Stump", chain.status()["fallback"] == "Stump 5")

# Nothing that fits in range: back to trying the one set up; the networks
# that didn't fit aren't tried again for a while.
del AIR["Stump 5"]
sta.tried = []
chain._state["bad"] = {}
r = asyncio.run(chain.find_new_parent(s, net=Net, get=get))
check("nothing fits: None, back to the Stump set up", r is None and sta.tried[-1] == "Stump 4", sta.tried)
sta.tried = []
r = asyncio.run(chain.find_new_parent(s, net=Net, get=get))
check("... and what didn't fit isn't tried again soon", r is None and sta.tried == ["Stump 4"], sta.tried)

# The path: what keeps circles out.
class Up:
    forward_ip = "192.168.5.1"
    listen_ip = "192.168.5.50"
    parent_seen = 1

chain._state["uplink"] = Up()
p = chain.refresh_path(get=lambda ip, path: {"path": ["192.168.4.1", "192.168.5.1"]})
check("path: the Stump above's, then this one", p == ["192.168.4.1", "192.168.5.1", ME] and chain.status()["path"] == p, p)
p = chain.refresh_path(get=lambda ip, path: {"path": []})
check("path: the Stump above cut off -> this one too", p == [], p)
p = chain.refresh_path(get=lambda ip, path: {"path": ["192.168.4.1", ME, "192.168.5.1"]})
check("path: this Stump already in it (a circle) -> cut off", p == [], p)
p = chain.refresh_path(get=lambda ip, path: None)
check("path: no answer -> cut off", p == [], p)
check("lost: Wi-Fi gone", chain._lost(Up(), False, 1000, 0))
u = Up()
u.parent_seen = 1000 - chain.QUIET_S - 1
check("lost: Wi-Fi there but the Stump above silent", chain._lost(u, True, 1000, 0))
u.parent_seen = 990
check("not lost: heard recently", not chain._lost(u, True, 1000, 0))
config.CHAIN_UPLINK_SSID = ""
check("the first Stump: its path is itself", chain.path() == [chain.ap_ip()] and chain.status()["chain"] == OURS)
config.CHAIN_ENABLED = False
print('FAILURES:', FAIL)
