# A LoRa client stays in its room while it's still here, at no airtime of
# its own (1.0.0-beta.79, asked by the FireFly team): the proof its app
# sends back for a line this Stump pushed it, and an announce of its own,
# count as "still here"; one that went quiet anyway and comes back within
# an hour returns to the room it was in, not to #lora-mesh.
import sys, time
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, rrc_mesh, i18n, mods
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


rrc.LORA_NONE[0] = False                   # a Stump with a radio
rrc.create_room("bavard")
FF = "cd" * 16
rrc_mesh._get_peer(FF, "bob")
p = rrc_mesh._peers[FF]
check("(a FireFly first lands in #lora-mesh)", p["room"] == rrc.MESH_ROOM and p["home_room"] == rrc.MESH_ROOM, p)
p["room"] = "bavard"                       # it went to #bavard (/join)
rrc.touch_user(FF, nick=p["nick"], room="bavard")


# -- a delivery proof counts ---------------------------------------------------
class Receipt:
    cb = None

    def set_delivery_callback(self, fn):
        self.cb = fn

    def set_timeout(self, t):                  # (beta.80: a route that doesn't deliver)
        pass

    def set_timeout_callback(self, fn):
        pass


class Pkt:
    def __init__(self):
        self.receipt = Receipt()


class Msg:
    def __init__(self):
        self.packet = Pkt()


SENT = []


class Router:
    def send_message(self, dest, text, title="", fields=None, via=None):
        m = Msg()
        SENT.append(m)
        return m


p["last_seen"] = time.time() - 290         # quiet for almost PEER_TIMEOUT
rrc_mesh._send_item((Router(), bytes.fromhex(FF), "a line of #bavard", "#bavard"))
check("a line pushed to it: its proof watched", SENT and SENT[-1].packet.receipt.cb is not None)
SENT[-1].packet.receipt.cb(SENT[-1].packet.receipt)
check("... its proof back: still here (its time refreshed)", time.time() - p["last_seen"] < 5, time.time() - p["last_seen"])
rrc_mesh._prune_peers(time.time() + 200)
check("... so not dropped 5 minutes after its last message", FF in rrc_mesh._peers)
SENT.clear()
rrc_mesh._send_item((Router(), bytes.fromhex("ab" * 16), "to someone not in a room", ""))
check("to someone not in a room: nothing watched", SENT and SENT[-1].packet.receipt.cb is None)

# -- an announce of its own counts -----------------------------------------------
real = (rrc_mesh._note_stump_beacon, rrc_mesh._is_person)
rrc_mesh._note_stump_beacon = lambda r, a, pk: False
rrc_mesh._is_person = lambda r, d, pk: True


class AnnRouter:
    def _parse_display_name(self, data):
        return "bob"


p["last_seen"] = time.time() - 290
rrc_mesh.on_announce(AnnRouter(), bytes.fromhex(FF), b"\x91\xa3bob", None)
check("its announce: still here", time.time() - p["last_seen"] < 5)
ST = "ef" * 16
rrc_mesh._peers[ST] = {"nick": "Gearn", "room": rrc.MESH_ROOM, "home_room": rrc.MESH_ROOM, "stump": True,
                       "last_seen": time.time() - 100, "last_sent_id": 0, "last_dm_id": 0}
rrc_mesh._still_here(ST)
check("... (a Stump's presence is its beacon's business: untouched)", time.time() - rrc_mesh._peers[ST]["last_seen"] > 90)
del rrc_mesh._peers[ST]
rrc_mesh._note_stump_beacon, rrc_mesh._is_person = real

# -- gone quiet anyway: back to the room it was in ---------------------------------
p["last_seen"] = time.time() - 400
rrc_mesh._prune_peers(time.time())
check("quiet past PEER_TIMEOUT (no proof, no announce): it leaves, as before", FF not in rrc_mesh._peers)
check("... the room it was in remembered", rrc_mesh._was.get(FF, ("",))[0] == "bavard", rrc_mesh._was)
back = rrc_mesh._get_peer(FF, "bob")
check("it comes back within the hour: in #bavard, not #lora-mesh", back["room"] == "bavard" and back["home_room"] == rrc.MESH_ROOM, back)
check("... its join said in #bavard", any("bob" in (m.get("body") or "") for m in rrc.since("bavard", 0)[-2:]), rrc.since("bavard", 0)[-2:])
check("... and /part still takes it home, to #lora-mesh", rrc_mesh._handle(FF, back, "/part")[1] == rrc.MESH_ROOM)
# Back before its name is heard again (beta.81): its join under the name
# it had, so its app sees "✓ ~bob" and switches to that room.
back["room"] = "bavard"
back["last_seen"] = time.time() - 400
rrc_mesh._prune_peers(time.time())
rrc_mesh._reach.pop(FF, None)
nameless = rrc_mesh._get_peer(FF, None)
check("back with no name heard yet: its join in #bavard under the name it had (its app switches on it)",
      nameless["room"] == "bavard" and nameless["nick"] == "bob"
      and any(("~bob" in (m.get("body") or "")) for m in rrc.since("bavard", 0)[-2:]), (nameless, rrc.since("bavard", 0)[-2:]))
back = nameless

back["room"] = "bavard"
back["last_seen"] = time.time() - 400
rrc_mesh._prune_peers(time.time())
real_jr = mods.join_refusal
mods.join_refusal = lambda h, room, lang: "banned from #bavard" if room == "bavard" else ""
again = rrc_mesh._get_peer(FF, "bob")
mods.join_refusal = real_jr
check("banned from it meanwhile: not put back there (#lora-mesh)", again["room"] == rrc.MESH_ROOM, again)

again["room"] = "bavard"
again["last_seen"] = time.time() - 400
rrc_mesh._prune_peers(time.time())
rrc_mesh._was[FF] = ("bavard", time.time() - rrc_mesh.BACK_S - 1)
late = rrc_mesh._get_peer(FF, "bob")
check("back after more than an hour: #lora-mesh, as a newcomer", late["room"] == rrc.MESH_ROOM, late)

late["last_seen"] = time.time() - 400
rrc_mesh._prune_peers(time.time())
check("quiet in #lora-mesh itself: nothing to remember", FF not in rrc_mesh._was)

print("FAILURES", FAIL)
