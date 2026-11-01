# /admin's USB <-> Bluetooth switch (radio.switch_link) with the real
# interfaces: a simulated RNode on a pseudo-terminal for USB, a stand-in
# for bleak for Bluetooth. Switching closes the old link (radio off, "the
# host left"), brings the new one up (handshake, radio settings) in its
# place, keeps the choice, and a restart applies it over config.py.
# Linux/macOS (pty). Run: python3 tests/pc/radio_link_test.py
import os, sys, pty, time, threading, tempfile, types, tty, asyncio as aio
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))
from urns.log import set_loglevel, LOG_NONE
set_loglevel(LOG_NONE)

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
FAIL = []
HELLO = bytes([FEND, 0x08, 0x73, FEND])


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:400]))
    if not cond:
        FAIL.append(name)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::" + (name + ": " + repr(info)[:1500]).replace("\n", "%0A"))


def kiss(cmd, payload):
    return bytes([FEND, cmd]) + payload + bytes([FEND])

ANSWER = kiss(0x08, b"\x46") + kiss(0x50, b"\x01\x4e") + kiss(0x48, b"\x80")

# USB: a simulated RNode
master, slave = pty.openpty()
tty.setraw(slave)
PORT = os.ttyname(slave)
usb_got = bytearray()
stop = threading.Event()


def usb_rnode():
    os.set_blocking(master, False)
    while not stop.is_set():
        try:
            data = os.read(master, 4096)
            usb_got.extend(data)
            if HELLO in data:
                os.write(master, ANSWER)
            if bytes([FEND, 0x46, 0x02, FEND]) in data:
                os.write(master, kiss(0x62, (482913).to_bytes(4, "big")))     # the code, over USB
        except (BlockingIOError, OSError):
            time.sleep(0.02)

threading.Thread(target=usb_rnode, daemon=True).start()

# Bluetooth: a stand-in for bleak with an RNode behind it
ble_got = bytearray()


class Dev:
    name, address = "RNode 3B87", "AA:BB:CC:DD:EE:01"


class Scanner:
    @staticmethod
    async def find_device_by_filter(f, timeout=5.0):
        await aio.sleep(0.05)
        return Dev()


class Char:
    max_write_without_response_size = 180


class Client:
    def __init__(self, dev, disconnected_callback=None, timeout=10):
        self.services = types.SimpleNamespace(get_service=lambda u: types.SimpleNamespace(get_characteristic=lambda c: Char()))
        self.notify = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def start_notify(self, uuid, cb):
        self.notify = cb

    async def write_gatt_char(self, char, data, response=False):
        ble_got.extend(data)
        if HELLO in bytes(data):
            self.notify(None, bytearray(ANSWER))

bleak = types.ModuleType("bleak")
bleak.BleakScanner, bleak.BleakClient = Scanner, Client
sys.modules["bleak"] = bleak

import radio
import urns.interfaces.rnode_serial as rs
import urns.interfaces.rnode_ble as rb
rs.BOOT_WAIT_S = 0.2
rb.RETRY_S = 0.3
from urns.transport import Transport
radio.LINK_FILE = "/radio_link_test.json"        # (the computer's files map "/" to the Stump folder)
LINK_REAL = os.path.join(ROOT, "final_firmware", "radio_link_test.json")

usb = rs.RNodeSerialInterface({"port": PORT, "frequency": 917112000, "bandwidth": 125000,
                               "txpower": 14, "spreadingfactor": 8, "codingrate": 5})
Transport.interfaces[:] = [usb]
check("on USB to start with", radio.link() == "usb" and usb.online, radio.hw())


async def scenario():
    out = {}
    poll = aio.create_task(usb.poll_loop())          # the Stump reads the USB link all along
    # Pair this computer: pairing mode over USB, a Bluetooth attempt from
    # here, the code shown from USB.
    out["pair"] = radio.pair_computer()
    await aio.sleep(1.5)
    out["probe"] = radio.probe_state()
    out["pin"] = radio.bt_pin()
    out["quiet"] = getattr(usb, "quiet_until", 0) > time.time()
    out["usb_still"] = usb.online
    usb_got.clear()
    out["to_ble"] = radio.switch_link("ble")
    await aio.sleep(rb.RNodeBLEInterface.LOOK_EVERY + 1.5)
    out["ble"] = [i for i in Transport.interfaces if i.__class__.__name__ == "RNodeBLEInterface"]
    out["usb_closed"] = bytes(usb_got)
    out["again"] = radio.switch_link("ble")
    out["to_usb"] = radio.switch_link("usb")
    await aio.sleep(1.5)
    out["usb"] = [i for i in Transport.interfaces if i.__class__.__name__ == "RNodeSerialInterface"]
    return out

r = aio.run(scenario())
check("pair this computer: started over USB", r["pair"] == "done", r["pair"])
check("... the code comes over USB, shown in /admin", r["pin"] == "482913", r["pin"])
check("... the computer reaches it over Bluetooth, and it answers there: paired", r["probe"] == "paired", r["probe"])
check("... USB stays up, its 'still there?' paused while pairing", r["usb_still"] and r["quiet"])
check("... and the attempt makes way when switching", radio._probe["port"] is None)
check("switched to Bluetooth: done, the only radio now", r["to_ble"] == "done" and len(r["ble"]) == 1
      and radio.link() in ("ble", "usb") and len(Transport.interfaces) == 1, (r["to_ble"], Transport.interfaces))
check("the USB link was closed properly: radio off, then leave", kiss(0x06, b"\x00") + kiss(0x0A, b"\xff") in r["usb_closed"],
      r["usb_closed"][-12:])
b = r["ble"][0] if r["ble"] else None
check("Bluetooth up: handshake, the same radio settings", b is not None and b"\x08\x73" in bytes(ble_got)
      and b.frequency == 917112000 and b.txpower == 14, (b and b.status()))
check("asking for the link it's already on: 'same'", r["again"] == "same", r["again"])
check("and back to USB: done, online again", r["to_usb"] == "done" and len(r["usb"]) == 1 and r["usb"][0].online
      and radio.link() == "usb", (r["to_usb"], radio.hw()))
check("the choice is kept, with the USB port", '"usb"' in open(LINK_REAL).read() and PORT in open(LINK_REAL).read())

# At the next start: the kept choice wins over config.py's entry.
open(LINK_REAL, "w").write('{"link": "ble", "target": "RNode 3B87"}')
cfg = {"interfaces": [{"type": "RNodeSerialInterface", "name": "Heltec (USB)", "port": "/dev/x", "frequency": 1}]}
radio.overlay(cfg)
e = cfg["interfaces"][0]
check("restart: config.py's USB entry becomes Bluetooth, settings kept", e["type"] == "RNodeBLEInterface"
      and e["target"] == "RNode 3B87" and e["frequency"] == 1, e)

# No Bluetooth support in this Python yet: installing, try again.
Transport.interfaces[:] = r["usb"]
del sys.modules["bleak"]
radio._bleak["state"] = "installing"
import builtins
real_import = builtins.__import__
builtins.__import__ = lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "bleak" else real_import(n, *a, **k)
res = aio.run(aio.sleep(0, result=radio.switch_link("ble")))
builtins.__import__ = real_import
check("without bleak: says it's being installed, stays on USB", res == "bleak" and radio.link() == "usb", res)
for i in list(Transport.interfaces):
    i.close()
stop.set()
try:
    os.remove(LINK_REAL)
except OSError:
    pass
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
