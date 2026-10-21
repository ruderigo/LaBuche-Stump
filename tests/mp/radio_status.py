# The radio's status for people: /hw.json, the chat poll's "hw" (the
# status dot), the RRC page's profile pop-up and its radio settings
# (/admin/radio.json, /admin's switch /admin/radiochat), /admin's live section,
# /node.json's radio settings; and #lora-mesh as the radio's room on a
# computer with its own Heltec (rrc.LORA_LOCKED).
import sys
exec(open(sys.argv[1] + '/harness.py').read())
import rrc, radio, i18n
import ujson as json
FAIL = []


def check(n, c, i=''):
    print(('PASS ' if c else 'FAIL ') + n + ('' if c else '  ' + repr(i)[:500]))
    if not c:
        FAIL.append(n)


from urns.transport import Transport
saved = list(Transport.interfaces)
del Transport.interfaces[:]

s, ct, b = req('/hw.json')
d = json.loads(b)
check("no radio: state none", d == {"state": "none", "radios": []}, d)


class RNodeSerialInterface:
    """Stands in for the USB interface (its status() is tested in
    tests/pc/rnode_serial_test.py against a simulated RNode)."""
    name = "Heltec (USB)"
    frequency, bandwidth, txpower, sf, cr = 915000000, 125000, 7, 8, 5

    def __init__(self, st):
        self.st = st

    retried = 0
    bt = {"pin": None, "at": 0, "asked": None}

    def retry_now(self):
        self.retried += 1

    def bluetooth(self, action):
        self.bt["asked"] = action
        if action == "pair":
            import time
            self.bt.update(pin="482913", at=time.time())
        return True

    def status(self):
        r = {"name": self.name, "kind": "usb", "where": "/dev/cu.usbserial-0001",
             "device": "CP2102 USB to UART Bridge Controller", "fw": "1.78", "error": None,
             "settings": {}, "radio": {}, "rssi": -92, "snr": -6.5}
        r.update(self.st)
        return r


iface = RNodeSerialInterface({"state": "online"})
Transport.interfaces.append(iface)
d = json.loads(req('/hw.json')[2])
check("a radio online", d["state"] == "online" and d["radios"][0]["where"] == "/dev/cu.usbserial-0001", d)
IP = '192.168.4.50'
i18n.set_lang(IP, 'en')
p = json.loads(req('/rrc/poll?room=main&since=0')[2])
check("the chat poll carries the dot: state, and the port and device for hover", p["hw"]["state"] == "online"
      and "/dev/cu.usbserial-0001" in p["hw"]["title"] and "CP2102" in p["hw"]["title"] and "1.78" in p["hw"]["title"], p["hw"])
iface.st = {"state": "error", "error": "no RNode found on USB (plug the Heltec in)"}
p = json.loads(req('/rrc/poll?room=main&since=0')[2])
check("an error says why on hover", p["hw"]["state"] == "error" and "plug the Heltec in" in p["hw"]["title"], p["hw"])
iface.st = {"state": "connecting"}
check("connecting", json.loads(req('/hw.json')[2])["state"] == "connecting")
n = json.loads(req('/node.json')[2])
check("/node.json gives the radio settings (for a computer joining)", n.get("radio") == {"frequency": 915000000,
      "bandwidth": 125000, "sf": 8, "cr": 5}, n.get("radio"))

s, ct, b = req('/rrc')
check("RRC page: no dot in the navigation; the profile (dot + name) at the foot of the column",
      "id='hw' class='hw-dot" in b and b.find("id='hw'") > b.find("class='nav'") and "id='side'" in b
      and "id='me-btn'" in b and "id='pop'" in b
      and b.find("id='rooms'") < b.find("id='profile'") < b.find("id='chatcol'"), b[:300])

# The radio's settings from the chat's pop-up
iface.st = {"state": "online"}
p = json.loads(req('/rrc/poll?room=main&since=0')[2])
check("a visitor sees the settings, not allowed to change them",
      "settings" in p["hw"] and p["hw"]["edit"] is False, p["hw"])
FORM = b'freq=917.112&bw=125000&sf=8&cr=5&txp=17'
s, ct, b = req('/admin/radio.json', FORM)
check("a visitor can't apply: 403", s.startswith('403') and json.loads(b)["ok"] is False, (s, b))
tok = login()
CK = ('Cookie: stump_admin=' + tok,)
p = json.loads(req('/rrc/poll?room=main&since=0', headers=CK)[2])
check("logged in to /admin: may change them", p["hw"].get("edit") is True, p["hw"])
s, ct, b = req('/admin/radio.json', FORM, headers=CK)
check("an /admin session applies them", s.startswith('200') and json.loads(b)["ok"] is True
      and radio.current()["frequency"] == 917112000 and radio.current()["txpower"] == 17, (s, b, radio.current()))
s, ct, b = req('/admin/radio.json', b'freq=abc&bw=125000&sf=8&cr=5&txp=17', headers=CK)
check("a bad frequency is refused, saying which field", json.loads(b)["ok"] is False and "requency" in json.loads(b)["message"], b)
s, ct, b = admin_page()
check("/admin: the switch for the chat's settings", "action='/admin/radiochat'" in b and "Remove the settings from the chat" in b,
      b[b.find('radiochat') - 200:][:400])
req('/admin/radiochat', b'admin_pass=pw&on=0')
check("switched off: saved", radio.chat_edit() is False)
p = json.loads(req('/rrc/poll?room=main&since=0', headers=CK)[2])
check("switched off: the chat shows the state only", "settings" not in p["hw"] and "edit" not in p["hw"], p["hw"])
s, ct, b = req('/admin/radio.json', FORM, headers=CK)
check("switched off: even an /admin session can't apply from the chat", s.startswith('403'), (s, b))
s, ct, b = admin_page()
check("/admin offers to turn it back on", "Offer the settings in the chat" in b)
req('/admin/radiochat', b'admin_pass=pw&on=1')
check("switched on again", radio.chat_edit() is True)
s, ct, b = admin_page()
check("/admin: a button to reconnect the radio", "action='/admin/radioretry'" in b and "Reconnect the radio" in b)
s, ct, b = req('/admin/radioretry', b'admin_pass=nope')
check("reconnecting needs the password", s.startswith('403') and iface.retried == 0, s)
s, ct, b = req('/admin/radioretry', b'admin_pass=pw')
check("reconnect: the radio is told to connect again", iface.retried == 1 and "handshake starts again" in b,
      b[:300])
# Bluetooth from /admin: buttons, the PIN shown to the admin only.
s, ct, b = admin_page()
check("/admin: Bluetooth on/off/pair in the radio card", "id='bt'" in b and "data-bt='pair'" in b
      and "data-bt='on'" in b and "data-bt='off'" in b)
s, ct, b = req('/admin/bluetooth.json', b'action=pair')
check("a visitor can't touch Bluetooth", s.startswith('403') and iface.bt["asked"] is None, (s, b))
s, ct, b = req('/admin/bt.json')
check("a visitor never sees the PIN", s.startswith('403') and "482913" not in b, (s, b))
tok = login()
CK = ('Cookie: stump_admin=' + tok,)
s, ct, b = req('/admin/bluetooth.json', b'action=pair', headers=CK)
check("the admin: pairing mode", s.startswith('200') and json.loads(b)["ok"] and iface.bt["asked"] == "pair", b)
s, ct, b = req('/admin/bt.json', headers=CK)
check("the admin: the PIN", json.loads(b)["pin"] == "482913", b)
check("the PIN never in /hw.json", "482913" not in req('/hw.json')[2])
s, ct, b = admin_page()
check("/admin: the USB / Bluetooth switch, USB on", "data-link='usb' aria-pressed='true'" in b and "data-link='ble'" in b)
s, ct, b = req('/admin/radiolink.json', b'link=ble')
check("a visitor can't switch the link", s.startswith('403'), (s, b))
s, ct, b = admin_page()
check("/admin: Pair this computer (on USB), forget devices, and what the Heltec answered", "data-bt='computer'" in b
      and "data-bt='unpair'" in b and "id='btheard'" in b)
s, ct, b = req('/admin/radiochat', b'admin_pass=nope&on=0')
check("the switch needs the password", s.startswith('403') and radio.chat_edit() is True, s)

s, ct, b = admin_page()
check("/admin: the radio, live", "id='hwlive'" in b and "/hw.json" in b and ("Radio — live" in b or "Radio — en direct" in b),
      b[b.find('hwlive') - 200:][:300])
del Transport.interfaces[:]
s, ct, b = admin_page()
check("/admin: no live section without a radio", "id='hwlive'" not in b)
Transport.interfaces[:] = saved

# #lora-mesh, the radio's room
W, OWNER, LORA = '192.168.0.20', '127.0.0.1', 'ab' * 16
for c, nick in ((W, 'visitor'), (OWNER, 'owner'), (LORA, '~rod')):
    i18n.set_lang(c, 'en')
    rrc.touch_user(c, nick=nick, room='main')
r, room = rrc.handle_input(W, 'main', '/join lora-mesh')
check("open by default: anyone joins", room == 'lora-mesh', r)
rrc.handle_input(W, 'lora-mesh', '/part')
rrc.LORA_LOCKED[0] = True
r, room = rrc.handle_input(W, 'main', '/join lora-mesh')
check("locked: a web visitor is refused, told why", room is None and "LoRa" in r[0] and "/lora invite" in r[0], r)
r, room = rrc.handle_input(OWNER, 'main', '/join lora-mesh')
check("the computer's owner gets in", room == 'lora-mesh', r)
check("someone on LoRa gets in", rrc.lora_allows(LORA))
r, _ = rrc.handle_input(W, 'main', '/lora invite visitor')
check("only the owner can invite", "only" in r[0], r)
r, _ = rrc.handle_input(OWNER, 'lora-mesh', '/lora invite visitor')
r2, room = rrc.handle_input(W, 'main', '/join lora-mesh')
check("invited: the visitor gets in", room == 'lora-mesh' and "visitor" in r[0], (r, r2))
rooms = rrc.handle_input(W, 'lora-mesh', '/rooms')[0]
check("/rooms marks it", any(x.startswith('#lora-mesh') and '[LoRa]' in x for x in rooms), rooms)
rrc.handle_input(OWNER, 'lora-mesh', '/lora uninvite visitor')
check("uninvited: out of the room", rrc.get_user(W)['room'] == 'main')
p = json.loads(req('/rrc/poll?room=lora-mesh&since=0')[2])
check("a web client not allowed is kept out by the poll", p["room"] != 'lora-mesh', p["room"])
rrc.LORA_LOCKED[0] = False

# Turning the Stump off: only on a computer (stump_host), never on a board.
s, ct, b = admin_page()
check("a board: no turn-off button", "/admin/poweroff" not in b)
s, ct, b = req('/admin/poweroff', b'admin_pass=pw')
check("a board: no turn-off route", s.startswith('404'), s)


class FakeHost:
    IS_PC = True
    ROOT = "/Users/rg/Stump"
    START_FILE = "Start Stump.command"
    off = []

    @staticmethod
    def power_off():
        FakeHost.off.append(1)

sys.modules['stump_host'] = FakeHost
s, ct, b = admin_page()
check("a computer: /admin can turn it off, says how to start it again", "action='/admin/poweroff'" in b
      and "Start Stump.command" in b and "confirm(" in b, b[b.find('poweroff') - 200:][:400])
s, ct, b = req('/admin/poweroff', b'admin_pass=nope')
check("a visitor without the password can't turn it off", s.startswith('403') and not FakeHost.off, s)
s, ct, b = req('/admin/poweroff', b'admin_pass=pw')
check("with the password: it says it's turning off, and where the Start file is", s.startswith('200')
      and "Start Stump.command" in b and "/Users/rg/Stump" in b, b[:300])
del sys.modules['stump_host']
print('FAILURES:', FAIL)
