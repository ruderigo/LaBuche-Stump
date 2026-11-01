# The radio lost (this Stump's Heltec, by any link, or the one of a Stump
# along the chain): the Heltec's own reconnect gets LORA_GRACE_S (2 min)
# with nothing said; past it, whoever writes to LoRa -- in #lora-mesh, or
# a DM to someone on LoRa -- is told, once a minute, and their line waits
# to go out when the radio is back (up to an hour); nobody on LoRa is
# dropped meanwhile. /hw.json says so.
import sys, time
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, rrc_mesh, radio, discovery, i18n
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


STATE = ["online"]
radio.hw = lambda: {"state": STATE[0], "radios": []}
WEB = "192.168.4.77"
rrc.touch_user(WEB, nick="ann", room="lora-mesh")
i18n.set_lang(WEB, "en")
LORA = "cd" * 16
rrc_mesh._peers[LORA] = {"nick": "bob", "room": "lora-mesh", "home_room": "lora-mesh", "last_seen": time.time(),
                         "last_sent_id": 0, "last_dm_id": 0}
rrc.touch_user(LORA, nick="bob", room="lora-mesh")
t0 = time.time()

check("radio online: up", rrc_mesh.lora_tick(t0) == "up")
r, _ = rrc.handle_input(WEB, "lora-mesh", "hello radio")
check("... a line to LoRa: no notice", not any("radio" in x for x in r), r)

STATE[0] = "connecting"
check("radio lost: 'connecting' first (its own reconnect)", rrc_mesh.lora_tick(t0 + 5) == "connecting")
r, _ = rrc.handle_input(WEB, "lora-mesh", "during the grace")
check("... within the grace (2 min): nothing said yet", not any("radio" in x for x in r), r)
check("past 2 min: down", rrc_mesh.lora_tick(t0 + 130) == "down")
rrc_mesh._lora["since"] = time.time() - 130          # the notices use the real clock
r, _ = rrc.handle_input(WEB, "lora-mesh", "while it's down")
check("a line to #lora-mesh: told the radio is down, and that it waits", any("down for 2 min" in x and "kept up to an hour" in x
                                                                            for x in r), r)
r, _ = rrc.handle_input(WEB, "lora-mesh", "again")
check("... once a minute, not on every line", not any("radio" in x for x in r), r)
rrc_mesh._lora["told"].clear()
r, _ = rrc.handle_input(WEB, "lora-mesh", "/msg bob are you there")
check("a DM to someone on LoRa: told too", any("down for" in x for x in r), r)
rrc.touch_user("192.168.4.88", nick="cat", room="main")
rrc_mesh._lora["told"].clear()
r, _ = rrc.handle_input(WEB, "lora-mesh", "/msg cat hi")
check("a DM to someone here on Wi-Fi: not told (it isn't going over the radio)", not any("down for" in x for x in r), r)

# The lines wait: the LoRa person's marks don't move, and they aren't dropped.
check("while it waits, a LoRa person's lines aren't sent", rrc_mesh._lora_waits())
rrc_mesh._peers[LORA]["last_seen"] = time.time() - 4000
rrc_mesh._prune_peers(time.time())
check("... and nobody on LoRa is dropped for being silent", LORA in rrc_mesh._peers)
before = rrc_mesh._peers[LORA]["last_sent_id"]
STATE[0] = "online"
check("the radio back: up", rrc_mesh.lora_tick(t0 + 300) == "up")
rrc_mesh._send_queue[:] = []
rrc_mesh._flush_to_peer(None, LORA, rrc_mesh._peers[LORA])
sent = "\n".join(str(i[2]) for i in rrc_mesh._send_queue)
check("... what waited goes out", "while it's down" in sent and "hello radio" in sent, sent[:300])
check("... the marks catch up", rrc_mesh._peers[LORA]["last_sent_id"] > before)

# Through the chain: no radio here, another Stump's.
STATE[0] = "none"
discovery._known.clear()
discovery.heard("ee" * 16, "RadioStump", [], {"p": 2, "rad": True, "rh": True})
check("no radio here, one along the chain online: up", rrc_mesh.lora_tick(t0 + 400) == "up")
discovery.heard("ee" * 16, "RadioStump", [], {"p": 2, "rad": False, "rh": True})
rrc_mesh.lora_tick(t0 + 410)
check("... its radio down (its beacon says): connecting, then down", rrc_mesh.lora_tick(t0 + 540) == "down")
discovery._known.clear()
check("no radio here nor along the chain: 'none', nothing to wait for", rrc_mesh.lora_tick(t0 + 600) == "none"
      and not rrc_mesh._lora_waits())
r, _ = rrc.handle_input(WEB, "lora-mesh", "no radio anywhere")
check("... and no notice", not any("down for" in x for x in r), r)

# /hw.json
STATE[0] = "connecting"
rrc_mesh.lora_tick(t0 + 700)
s, ct, b = req("/hw.json")
d = json.loads(b)
check("/hw.json: the radio's state here or along the chain", d.get("lora", {}).get("state") in ("connecting", "down")
      and isinstance(d["lora"].get("down_s"), int), d.get("lora"))
print('FAILURES:', FAIL)
