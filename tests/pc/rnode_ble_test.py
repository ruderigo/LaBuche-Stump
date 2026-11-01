# RNodeBLEInterface (a Heltec over Bluetooth, for Stump on a computer)
# against a simulated RNode behind a stand-in for bleak: found by name,
# the handshake and radio set-up over the Bluetooth serial service, writes
# cut to the link's size, a packet in, the link dropping (red at once,
# back by itself), no Heltec around (says what to check), and the radio
# off on close. Run: python3 tests/pc/rnode_ble_test.py
import os, sys, time, types, asyncio as aio
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
sys.path.append(ROOT)              # (the Provisioner, for its name check)
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))
from urns.log import set_loglevel, LOG_NONE
set_loglevel(LOG_NONE)

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
FAIL = []


def annotate(text):
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:400]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


def frames(raw):
    out, cur, esc, inside = [], None, False, False
    for b in raw:
        if b == FEND:
            if inside and cur:
                out.append((cur[0], bytes(cur[1:])))
            cur, inside = bytearray(), True
        elif inside:
            if b == FESC:
                esc = True
            else:
                if esc:
                    b = FEND if b == TFEND else FESC if b == TFESC else b
                    esc = False
                cur.append(b)
    return out


def kiss(cmd, payload):
    body = bytearray()
    for b in payload:
        body += bytes([FESC, TFEND]) if b == FEND else bytes([FESC, TFESC]) if b == FESC else bytes([b])
    return bytes([FEND, cmd]) + bytes(body) + bytes([FEND])


# ---- a stand-in for bleak, with an RNode behind it -------------------------
UART = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
radio = {"around": True, "answering": True, "mute": 0, "connects": 0, "written": bytearray(), "writes": [], "client": None, "seen": b""}
DETECT = bytes([FEND, 0x08, 0x73, FEND])


class Dev:
    name, address = "RNode 3B87", "AA:BB:CC:DD:EE:01"


class Adv:
    local_name = "RNode 3B87"
    service_uuids = [UART]


class Scanner:
    @staticmethod
    async def find_device_by_filter(f, timeout=5.0):
        await aio.sleep(0.05)
        return Dev() if radio["around"] and f(Dev(), Adv()) else None


class Char:
    max_write_without_response_size = 20
    properties = ["write"]                  # the RNode's RX: writes with a reply only


class Service:
    def get_characteristic(self, u):
        return Char()


class Services:
    def get_service(self, u):
        return Service()


class Client:
    def __init__(self, dev, disconnected_callback=None, timeout=10):
        self.cb = disconnected_callback
        self.services = Services()
        self.notify = None
        self.up = False
        self.first = None
        self.first_before_notify = False

    async def __aenter__(self):
        self.up = True
        radio["client"] = self
        radio["connects"] += 1
        self.mute = radio["mute"] > 0          # a subscription lost to the first pairing
        radio["mute"] = max(0, radio["mute"] - 1)
        return self

    async def __aexit__(self, *a):
        self.up = False

    async def start_notify(self, uuid, cb):
        self.notify = cb

    async def write_gatt_char(self, char, data, response=False):
        if self.first is None:
            self.first, self.first_before_notify = bytes(data), self.notify is None
        if self.notify is None:
            return
        radio.setdefault("replies", []).append(response)
        radio["writes"].append(len(data))
        radio["written"].extend(data)
        # Answers each "are you an RNode?" once, however it was cut up.
        seen = radio["seen"] + bytes(data)
        if radio["answering"] and DETECT in seen and not self.mute:
            self.notify(None, bytearray(kiss(0x08, b"\x46") + kiss(0x50, b"\x01\x4e") + kiss(0x48, b"\x80")))
            seen = seen[seen.rindex(DETECT) + len(DETECT):]
        radio["seen"] = seen[-8:]

    def drop(self):
        self.up = False
        self.cb(self)


bleak = types.ModuleType("bleak")
bleak.BleakScanner, bleak.BleakClient = Scanner, Client
sys.modules["bleak"] = bleak

import urns.interfaces.rnode_ble as rb
rb.RETRY_S = 0.3
import uasyncio as asyncio

iface = rb.RNodeBLEInterface({"target": "RNode 3B87", "frequency": 917112000, "bandwidth": 125000,
                              "txpower": 14, "spreadingfactor": 8, "codingrate": 5})
received = []
iface.process_incoming = lambda data: received.append(bytes(data))


async def run(seconds):
    t = asyncio.create_task(iface.poll_loop())
    await asyncio.sleep(seconds)
    t.cancel()

asyncio.run(run(rb.RNodeBLEInterface.LOOK_EVERY + 1.5))
st = iface.status()
check("found by name over Bluetooth, handshake answered: online", iface.online and st["state"] == "online"
      and st["kind"] == "ble" and st["fw"] == "1.78" and "RNode 3B87" in st["where"], st)
f = [x for x in frames(radio["written"]) if x[0] not in (0x08, 0x50, 0x48)]
check("radio set up over Bluetooth, the mesh's frequency", [c for c, p in f][:6] == [1, 2, 3, 4, 5, 6]
      and f[0][1] == (917112000).to_bytes(4, "big") and f[2][1] == b"\x0e", f[:6])

radio["written"].clear()
radio["writes"].clear()
pkt = bytes(range(100)) + bytes([FEND])
iface.process_outgoing(pkt)
asyncio.run(run(0.5))
check("written WITH a reply (what makes the computer encrypt the link)", radio["replies"] and all(radio["replies"]),
      radio["replies"][:5])
check("a packet goes out whole, cut to the link's size", frames(radio["written"]) == [(0, pkt)]
      and max(radio["writes"]) <= 20 and len(radio["writes"]) > 1, (frames(radio["written"]), radio["writes"]))
incoming = bytes(range(50)) + b"in"
radio["client"].notify(None, bytearray(kiss(0x00, incoming)))
asyncio.run(run(0.4))
check("a packet comes in", received == [incoming], received)

# The link drops (out of range, Heltec off): red at once, and it comes
# back by itself.
radio["around"] = False
radio["client"].drop()
asyncio.run(run(1.6))
st = iface.status()
check("link dropped: offline, red, says so", not iface.online and st["state"] == "error" and st["error"], st)
asyncio.run(run(1.5))
st = iface.status()
check("no Heltec around: says to check it's on, Bluetooth on, paired", not iface.online
      and "paired" in (st["error"] or ""), st)
radio["around"] = True
radio["written"].clear()
asyncio.run(run(2.5))
check("back in range: reconnects by itself, handshake and set-up again", iface.online
      and [c for c, p in frames(radio["written"])][:3] == [0x08, 0x50, 0x48], (iface.status(), frames(radio["written"])[:4]))

# Just paired: connected, but nothing comes back (the subscription made
# before the bond was dropped): the link is started over, then it works.
rb.RNodeBLEInterface.HANDSHAKE_WAIT = 1.5
radio["mute"] = 1
before = radio["connects"]
iface._ble.drop()                       # a fresh connection -- the muted one
iface.retry_now()
asyncio.run(run(16))
check("connected but silent after pairing: the Bluetooth link is started over, then online",
      iface.online and radio["connects"] >= before + 2, (iface.status().get("error"), radio["connects"] - before))
rb.RNodeBLEInterface.HANDSHAKE_WAIT = rb.HANDSHAKE_BLE_S

# /admin's retry works the same way.
iface.retry_now()
asyncio.run(run(1.5))
check("retry: online again", iface.online)

# Stopping the Stump: radio off, then leave.
radio["written"].clear()
iface.close()
time.sleep(0.5)
check("on close: radio off, then leave", frames(radio["written"]) == [(0x06, b"\x00"), (0x0A, b"\xff")],
      frames(radio["written"]))

# Starting on Bluetooth with the Heltec also on USB (charging): restarted
# once through the cable first (the RNode's reset command); after
# KICK_AFTER silent handshakes in a row, a whole fresh start (what
# switching USB -> Bluetooth did by hand): this computer's Bluetooth link
# started over from nothing, the Heltec restarted too when on the cable.
import urns.interfaces.rnode_serial as rs
kicks = []


class FakeUSB:
    def __init__(self, port):
        self.port = port
        self.got = bytearray()

    def sendall(self, data):
        self.got.extend(data)

    def close(self):
        kicks.append((self.port, bytes(self.got)))


def fake_reset(port):
    kicks.append((port, "reset line"))

real_find, real_port, real_boot, real_reset = rs.find_port, rs._SerialPort, rs.BOOT_WAIT_S, rs.hard_reset
rs.find_port, rs._SerialPort, rs.BOOT_WAIT_S = (lambda: "/dev/cu.usbserial-0001"), FakeUSB, 0.05
rs.hard_reset = fake_reset
real_every = rs.RESET_EVERY_S
rs.RESET_EVERY_S = 0               # (the limit is checked on its own below)
rb.KICK_BOOT_S = 0.1
radio["around"], radio["answering"] = True, True
iface2 = rb.RNodeBLEInterface({"target": "RNode 3B87"})
check("start-up on Bluetooth, the Heltec on USB too: restarted once through its reset line",
      kicks == [("/dev/cu.usbserial-0001", "reset line")], kicks)
async def run2(i, s):
    t = asyncio.create_task(i.poll_loop())
    await asyncio.sleep(s)
    t.cancel()
asyncio.run(run2(iface2, 3))
check("... then online over Bluetooth, no more restarts", iface2.online and len(kicks) == 1, (iface2.status().get("error"), kicks))
check("encrypted before subscribing: a lone frame end written with a reply first",
      radio["client"].first == b"\xc0" and radio["client"].first_before_notify, radio["client"].first)
rb.RNodeBLEInterface.HANDSHAKE_WAIT = 1.0
radio["answering"] = False
first_port = iface2._ble
iface2.retry_now()
asyncio.run(run2(iface2, 14))
check("silent over Bluetooth twice in a row: a fresh start -- restarted through the cable",
      len(kicks) >= 2, kicks)
check("... and this computer's Bluetooth started over from nothing (a new link, the old one ended)",
      iface2._ble is not first_port and not first_port._thread.is_alive())
radio["answering"] = True
asyncio.run(run2(iface2, 6))
check("... then online again", iface2.online, iface2.status().get("error"))
iface2.close()

# No USB cable, already paired: the same fresh start on this computer's
# side (that part never needed the cable), no restart.
rs.find_port = lambda: None
kicks.clear()
iface3 = rb.RNodeBLEInterface({"target": "RNode 3B87"})
check("no USB cable: nothing to restart at start-up", kicks == [])
asyncio.run(run2(iface3, 3))
check("... online over Bluetooth", iface3.online, iface3.status().get("error"))
radio["answering"] = False
first_port = iface3._ble
iface3.retry_now()
asyncio.run(run2(iface3, 14))
check("no cable, silent twice: Bluetooth started over from nothing all the same", iface3._ble is not first_port
      and kicks == [], kicks)
radio["answering"] = True
asyncio.run(run2(iface3, 6))
check("... then online again, no cable needed", iface3.online, iface3.status().get("error"))

# Not seen at all for a while, on the cable (holding on to a link that's
# gone, not advertising): a fresh start, then found.
rs.find_port = lambda: "/dev/cu.usbserial-0001"
iface3.close()
radio["around"] = False
iface4 = rb.RNodeBLEInterface({"target": "RNode 3B87"})
kicks.clear()
asyncio.run(run2(iface4, 4))
iface4._ble.misses = rb.MISS_KICK
radio["around"] = True
asyncio.run(run2(iface4, 5))
check("not seen a few scans in a row, on the cable: restarted, then found", kicks and iface4.online,
      (kicks, iface4.status().get("error")))

# Stopping: the Bluetooth link ends properly (the Heltec sees the computer
# leave and advertises again for the next start).
c = radio["client"]
iface4.close()
check("on close: disconnected properly, not left hanging", not c.up and not iface4._ble._thread.is_alive())
# Never a stream of resets: a second one within RESET_EVERY_S is skipped.
rs.RESET_EVERY_S = 300
kicks.clear()
iface6 = rb.RNodeBLEInterface({"target": "RNode 3B87"})
check("never a stream of resets: the start-up one done...", len(kicks) == 1, kicks)
check("... another one right after: skipped", iface6._usb_kick() is False and len(kicks) == 1, kicks)
iface6.close()
rs.RESET_EVERY_S = 0
# A USB chip whose reset line can't be set: the RNode's reset command.
def no_line(port):
    raise OSError("not supported")
rs.hard_reset = no_line
kicks.clear()
iface5 = rb.RNodeBLEInterface({"target": "RNode 3B87"})
check("no reset line: the RNode's reset command instead", kicks == [("/dev/cu.usbserial-0001", bytes([0xC0, 0x55, 0xF8, 0xC0]))], kicks)
iface5.close()
rs.find_port, rs._SerialPort, rs.BOOT_WAIT_S, rs.hard_reset = real_find, real_port, real_boot, real_reset
rs.RESET_EVERY_S = real_every

# The Heltec forgot this computer while the computer kept its pairing
# (macOS: "Peer removed pairing information", CBError 14). Connected as
# usual: says what to do. Pairing this computer: the old pairing here is
# removed once, then it pairs afresh.
radio["stale"] = True
real_aenter = Client.__aenter__


async def stale_aenter(self):
    if radio.get("stale"):
        raise Exception('failed to connect: Error Domain=CBErrorDomain Code=14 "Peer removed pairing information"')
    return await real_aenter(self)

Client.__aenter__ = stale_aenter
forgot = []
rb.forget_on_this_mac = lambda name: forgot.append(name) or radio.update(stale=False) or True
p = rb._BLEPort("auto")
time.sleep(1.0)
check("the Heltec forgot this computer: says so, and what to do (Pair this computer; or forget it in System Settings)",
      p.stale and "forgot this computer" in (p.error or "") and "Forget This Device" in p.error and not forgot, p.error)
p.stop(2)
p = rb._BLEPort("auto")
p.probe = True
t0 = time.time()
while time.time() - t0 < 4 and not p.paired:
    time.sleep(0.1)
check("pairing this computer: the old pairing here removed once, then paired", forgot == ["RNode 3B87"] and p.paired
      and not p.stale, (forgot, p.error))
p.stop(2)
Client.__aenter__ = real_aenter
check("finding the old pairing's address in the Mac's Bluetooth report", rb._find_address(
    {"SPBluetoothDataType": [{"device_not_connected": [{"Rod's phone": {"device_address": "11:22"}},
                                                      {"RNode EA69": {"device_address": "AA:BB:CC:DD:EE:69"}}]}]},
    "RNode EA69") == "AA:BB:CC:DD:EE:69")

# The name as typed in the Provisioner: "EA69" (what the Heltec's screen
# shows) finds "RNode EA69" -- it found nothing before, so every start
# needed the USB -> Bluetooth switch (which looks for any RNode).
class D2:
    name, address = "RNode EA69", "AA:BB:CC:DD:EE:69"
for typed in ("EA69", "ea69", "RNode EA69", "rnode-ea69", "AA:BB:CC:DD:EE:69", "auto"):
    check("the name typed as %r finds RNode EA69" % typed, rb._matches(typed, D2(), Adv()))
for typed in ("3B87", "RNode 3B87", "69"):
    check("... and %r doesn't" % typed, not rb._matches(typed, D2(), Adv()))
import provisioner as PV
check("the Provisioner keeps what was typed as the Heltec advertises it",
      [PV._ble_name(x) for x in ("EA69", "rnode ea69", "", "auto")] == ["RNode EA69", "RNode EA69", "auto", "auto"])

# No bleak in that Python: says how to get it.
del sys.modules["bleak"]
import builtins
real_import = builtins.__import__
builtins.__import__ = lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "bleak" else real_import(n, *a, **k)
p = rb._BLEPort("auto")
time.sleep(0.3)
builtins.__import__ = real_import
check("without bleak: says the Provisioner installs it", "bleak" in (p.error or "") and not p.connected, p.error)
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
