# The chain protocol (2, 1.0.0-beta.35), on the board's MicroPython: what
# a Stump says about itself in its beacon (capabilities), what it keeps
# of the others (older ones included), who the hub lets in (a good hello,
# never just a packet), the refusals it shows, a link with nobody on it
# not being a "failure", and /admin's chain section saying all that.
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import config, chain, discovery, time
from urns import umsgpack
from urns.interfaces.chain import ChainHubInterface, HELLO_MAGIC, _hello_tag, PEER_TTL_S
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:400]))
    if not c:
        FAIL.append(n)


# 1. The beacon carries what this Stump is.
import rrc
rrc.NODE_INFO = {"version": "Beta A", "release": "1.0.0-beta.35"}
c = discovery.caps()
check("caps: protocol, release, mode, radio, storage, plugins",
      c["p"] == chain.PROTO == 2 and c["r"] == "1.0.0-beta.35" and c["m"] in ("open", "hybrid", "mandatory")
      and c["rad"] in (True, False) and c["s"][0] in ("sd", "pc", "none") and isinstance(c["pl"], list), c)
data = discovery.beacon_data("Beta A", "LaBuche", b"\x11" * 16)
f = umsgpack.unpackb(data)
check("the beacon: six items, the first five as before", len(f) == 6 and f[0] == "stump" and f[2] == "LaBuche"
      and bytes(f[3]) == b"\x11" * 16 and isinstance(f[4], list) and f[5]["p"] == 2, f)
check("... small enough for LoRa", len(data) < 200, len(data))

# 2. What it keeps of the others: a new one, and one from before beta.35.
discovery._known.clear()
discovery.heard("aa" * 16, "Bravo", ["http://192.168.5.1/"], {"p": 2, "r": "1.0.0-beta.35", "m": "hybrid",
                                                               "rad": True, "s": ["sd", 3000], "pl": ["tavern"]})
discovery.heard("bb" * 16, "Oldie", ["http://192.168.6.1/"])
st = {s["name"]: s for s in discovery.chain_stumps()}
check("a new Stump: its release, protocol, mode, radio, storage and plugins", st["Bravo"]["proto"] == 2
      and st["Bravo"]["release"] == "1.0.0-beta.35" and st["Bravo"]["mode"] == "hybrid" and st["Bravo"]["radio"]
      and st["Bravo"]["storage"] == {"kind": "sd", "free_mb": 3000} and st["Bravo"]["plugins"] == ["tavern"], st["Bravo"])
check("an older Stump (no capabilities): protocol 1, release unknown", st["Oldie"]["proto"] == 1
      and st["Oldie"]["release"] == "" and st["Oldie"]["storage"] is None, st["Oldie"])
discovery.heard("cc" * 16, "Junk", [], {"p": "x", "s": "nope", "pl": 5})
try:
    junk = [s for s in discovery.chain_stumps() if s["name"] == "Junk"]
    ok = len(junk) == 1 and junk[0]["storage"] is None
except Exception as e:
    ok, junk = False, repr(e)
check("capabilities that don't make sense: nothing breaks", ok, junk)

# 3. The hub: who's in.
SECRET = "s3cret"
hub = ChainHubInterface({"listen_ip": "127.0.0.1", "listen_port": 0, "secret": SECRET})
hub._socket = None
check("nobody below: Transport skips it quietly", hub.no_peers)
good = HELLO_MAGIC + _hello_tag(SECRET.encode(), "192.168.5.7", "127.0.0.1")
bad = HELLO_MAGIC + _hello_tag(b"another", "192.168.5.7", "127.0.0.1")
addr = ("192.168.5.7", 4242)
check("a packet from someone unknown: refused", hub.accept(b"reticulum packet", addr) is False)
hub.accept(good, addr)
check("a good hello: in", "192.168.5.7" in hub.peers and not hub.no_peers)
check("... and its packets are taken", hub.accept(b"reticulum packet", addr) is True)
hub.peers["192.168.5.7"][1] -= PEER_TTL_S + 5
check("its hellos stopped: packets aren't taken any more (a packet never keeps a peer in)",
      hub.accept(b"reticulum packet", addr) is False)
hub.accept(good, addr)
check("hello again: in again", hub.accept(b"reticulum packet", addr) is True)
hub.accept(bad, addr)
check("the same address saying hello with another secret: out at once", "192.168.5.7" not in hub.peers
      and hub.accept(b"reticulum packet", addr) is False)
hub.accept(bad, ("192.168.5.9", 4242))
hub.accept(bad, ("192.168.5.9", 4242))
r = {x["ip"]: x for x in hub.refusals()}
check("the refusals are kept: who, how many, when", r["192.168.5.9"]["count"] == 2 and r["192.168.5.7"]["count"] == 1
      and r["192.168.5.9"]["last_s"] < 5, r)
hub.accept(good, addr)
check("a good hello clears its refusal", "192.168.5.7" not in {x["ip"] for x in hub.refusals()})

# 4. Transport doesn't call a link with nobody on it a failure.
from urns.transport import Transport
said = []
import urns.transport as T
_orig = T.log
T.log = lambda msg, lvl=0: said.append(msg)
empty = ChainHubInterface({"listen_ip": "127.0.0.1", "listen_port": 0, "secret": SECRET, "name": "Chain down"})
empty.online = True


class Pk:
    raw = b"x" * 40
    sent = False
    attached_interface = None
    packet_type = 1
    destination_hash = None
    create_receipt = False


saved = Transport.interfaces
Transport.interfaces = [empty]
Transport.outbound(Pk())
Transport.interfaces = saved
T.log = _orig
check("an announce with nobody below: no 'TX failed' warning", not any("TX failed" in m for m in said), said)

# 5. /admin says it.
config.CHAIN_ENABLED = True
config.CHAIN_UPLINK_SSID = ""
config.CHAIN_SECRET = SECRET
chain._state["hubs"] = [hub]
chain._state["hub"] = hub
hub.accept(bad, ("192.168.4.66", 4242))
discovery.heard("dd" * 16, "Newer", [], {"p": 3, "r": "1.0.0-beta.40"})
import admin_web
html = admin_web._render_admin_chain_section("en")
check("/admin: who tried with another secret", "192.168.4.66" in html and "another chain secret" in html, html[-600:])
check("/admin: the other Stumps with their release", "Bravo" in html and "1.0.0-beta.35" in html, html[-600:])
check("/admin: an older one, to update", "Oldie" in html and "update it" in html, html[-600:])
check("/admin: a newer one: update this one", "Newer" in html and "update this one" in html, html[-600:])
d = json.loads(req("/chain.json")[2])
check("/chain.json: proto, stumps, refused", d["proto"] == 2 and any(s["name"] == "Bravo" for s in d["stumps"])
      and any(x["ip"] == "192.168.4.66" for x in d["refused"]), d)
d = json.loads(req("/node.json")[2])
check("/node.json: the chain protocol", d.get("proto") == 2, d)

# 6. A bot's line stays on its Stump (it looked like a person elsewhere,
#    "BarKeep@LaBuche", whom nobody could DM).
import rrc_mesh
rrc.ensure_room("wifi-mesh") if hasattr(rrc, "ensure_room") else None
b = rrc.post("main", "BarKeep", "the bot speaks", bot=True)
p_ = rrc.post("main", "ann", "a person speaks")
check("a bot's line is marked", b and b.get("bot") is True and not p_.get("bot"), (b, p_))
lines = rrc_mesh._stump_lines([b, p_])
check("... and not sent to the other Stumps; a person's is", lines == ["<ann> a person speaks"], lines)
import plugins
plugins._post_use.clear()
m = plugins._post("tavern", "Tavernier", "main", "a plugin speaks")
last = rrc.since("main", 0)[-1]
check("a plugin's line is a bot's too", m and last["nick"] == "Tavernier" and last.get("bot") is True, last)

# 7. Forged beacons: the LXMF address inside must be the beacon's own.
from urns.identity import Identity
from urns.destination import Destination
from urns import const
me = Identity()
victim = Identity()
pub = me.get_public_key()
nh = Identity.full_hash(b"stump.node")[:const.NAME_HASH_LENGTH // 8]


class Pkt:
    def __init__(self, pubkey):
        self.data = pubkey + nh + b"\x00" * 80


class R:
    delivery_destination = None


own_lxmf = Destination.hash(me, "lxmf", "delivery")
victim_lxmf = Destination.hash(victim, "lxmf", "delivery")
rrc_mesh._note_stump_beacon(R(), umsgpack.packb(["stump", "Beta A", "Real", own_lxmf, []]), Pkt(pub))
check("a beacon naming its own LXMF address: a Stump", rrc.is_stump(own_lxmf.hex()))

# A Stump's name resolves from its beacon (discovery._known), without
# waiting for its LXMF announce (router.peers) -- the mesh-xxxx window.
class RP:
    delivery_destination = None
    peers = {}
check("a Stump's name comes from its beacon, not only its LXMF announce",
      rrc_mesh._known_name(RP(), own_lxmf.hex()) == "Real")
rrc_mesh._note_stump_beacon(R(), umsgpack.packb(["stump", "Beta A", "Fake", victim_lxmf, []]), Pkt(pub))
check("a beacon naming someone else's address: ignored (they stay a person)", not rrc.is_stump(victim_lxmf.hex()))

# 8. Profiles cross the chain only with the chain secret's mark.
config.CHAIN_SECRET = "s3cret"
out = rrc_mesh._profiles_out('[{"nick": "ann"}]')
check("profiles sent: marked with the secret", '"t"' in out and rrc_mesh._profiles_in(out) == [{"nick": "ann"}], out)
check("... a plain list (an older Stump, or a forger): refused", rrc_mesh._profiles_in('[{"nick": "eve"}]') is None)
forged = json.dumps({"r": '[{"nick": "eve"}]', "t": "00" * 16})
check("... a wrong mark: refused", rrc_mesh._profiles_in(forged) is None)
tampered = json.loads(out)
tampered["r"] = '[{"nick": "eve"}]'
check("... the records changed under a good mark: refused", rrc_mesh._profiles_in(json.dumps(tampered)) is None)
config.CHAIN_SECRET = ""
check("an open chain (no secret): the plain list, as before", rrc_mesh._profiles_in('[{"nick": "bob"}]') == [{"nick": "bob"}]
      and rrc_mesh._profiles_out("[]") == "[]")
config.CHAIN_SECRET = SECRET

# 9. The Stump above proves the secret in its answer.
from urns.interfaces.chain import ChainUplinkInterface, _ack_tag
up = ChainUplinkInterface({"listen_ip": "192.168.4.23", "listen_port": 0, "forward_ip": "192.168.4.1",
                           "forward_port": 4242, "secret": SECRET})
parent = ("192.168.4.1", 4242)
up.accept(HELLO_MAGIC + b"ACK" + _ack_tag(SECRET.encode(), "192.168.4.23", "192.168.4.1"), parent)
check("a signed answer: the parent is seen, and verified", up.parent_seen and up.parent_verified is True)
up2 = ChainUplinkInterface({"listen_ip": "192.168.4.23", "listen_port": 0, "forward_ip": "192.168.4.1",
                            "forward_port": 4242, "secret": SECRET})
up2.accept(HELLO_MAGIC + b"ACK", parent)
check("a plain answer (an older Stump above): seen, and shown as not verified", up2.parent_seen and up2.parent_verified is False)
up3 = ChainUplinkInterface({"listen_ip": "192.168.4.23", "listen_port": 0, "forward_ip": "192.168.4.1",
                            "forward_port": 4242, "secret": SECRET})
up3.accept(HELLO_MAGIC + b"ACK" + b"x" * 16, parent)
check("an answer with another secret: ignored", not up3.parent_seen)
up.accept(HELLO_MAGIC + b"ACK", parent)
check("verified once: a plain answer after it doesn't undo that", up.parent_verified is True)
print('FAILURES:', FAIL)
