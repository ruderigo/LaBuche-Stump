# chain.py's settings, interface configs, status, /chain.json and the
# /admin section, for a member Stump and a root Stump.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config, chain, stump_tls
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


check("off by default", chain.settings() is None and chain.status() == {"enabled": False})
s, ct, b = admin_page()
check("no chain section when off", "Stump chain" not in b and "Chaîne de Stumps" not in b)

config.CHAIN_ENABLED = True
config.CHAIN_UPLINK_SSID = "Unit 3B"
config.CHAIN_UPLINK_PASS = ""
config.CHAIN_SUBNET = 5
config.CHAIN_CHANNEL = 11
config.CHAIN_SECRET = "s3cret"
st = chain.settings()
check("member settings", st["uplink_ssid"] == "Unit 3B" and st["subnet"] == 5 and not chain.is_root(st), st)
check("own Wi-Fi on 192.168.5.1", chain.ap_ip(st) == "192.168.5.1" and chain.ap_prefix() == "192.168.5.")
check("HTTPS knows the Wi-Fi moved", stump_tls._ap_prefix() == "192.168.5.")
ifs = chain.interfaces(st, "192.168.4.23", "192.168.4.1", "192.168.5.1")
check("member: hub on own Wi-Fi + uplink to parent",
      [i["type"] for i in ifs] == ["ChainHubInterface", "ChainUplinkInterface"]
      and ifs[0]["listen_ip"] == "192.168.5.1" and ifs[1]["listen_ip"] == "192.168.4.23"
      and ifs[1]["forward_ip"] == "192.168.4.1" and all(i["secret"] == "s3cret" for i in ifs), ifs)
check("member without uplink yet: hub only", len(chain.interfaces(st, None, None, "192.168.5.1")) == 1)
check("member: never a hub on its uplink side", len(chain.interfaces(st, "192.168.4.23", "192.168.4.1", "192.168.5.1",
                                                                   lan_ip="192.168.4.23")) == 2)
s, ct, b = req('/chain.json')
import ujson as json
d = json.loads(b)
check("/chain.json member", ct.startswith("application/json") and d["role"] == "member"
      and d["uplink"]["ssid"] == "Unit 3B" and d["below"] == [] and d["secret"] is True, d)
s, ct, b = admin_page()
check("admin: trying to join", "Trying to join" in b or "rejoindre" in b, b[b.find('chain'):][:300])

config.CHAIN_UPLINK_SSID = ""
config.CHAIN_SECRET = ""
config.CHAIN_SUBNET = 999
st = chain.settings()
check("root, bad subnet falls back to 4", chain.is_root(st) and st["subnet"] == 4)
check("root without a secret: no chain links on the home Wi-Fi",
      len(chain.interfaces(st, None, None, "192.168.4.1", lan_ip="192.168.0.161")) == 1 and chain._state["lan_ip"] is None)
config.CHAIN_SECRET = "s3cret"
st = chain.settings()
ifs = chain.interfaces(st, None, None, "192.168.4.1", lan_ip="192.168.0.161")
check("root with a secret: a second hub on the home Wi-Fi", [(i["name"], i["listen_ip"]) for i in ifs]
      == [("Chain down", "192.168.4.1"), ("Chain LAN", "192.168.0.161")] and ifs[1]["secret"] == "s3cret", ifs)
d = json.loads(req('/chain.json')[2])
check("/chain.json says where it takes links", d["lan_ip"] == "192.168.0.161" and d["ap_ip"] == "192.168.4.1", d)
config.CHAIN_LAN = False
check("CHAIN_LAN = False: own Wi-Fi only", len(chain.interfaces(chain.settings(), None, None, "192.168.4.1",
                                                                lan_ip="192.168.0.161")) == 1)
del config.CHAIN_LAN
config.CHAIN_SECRET = ""
st = chain.settings()
chain.interfaces(st, None, None, "192.168.4.1")
s, ct, b = admin_page()
check("admin: root line and the no-secret warning",
      ("Premier Stump" in b or "First Stump" in b) and "CHAIN_SECRET" in b, b[b.find('chain'):][:400])
print('FAILURES:', FAIL)
