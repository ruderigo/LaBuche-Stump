# RNodeSerialInterface (a Heltec on USB, for Stump on a computer) against a
# simulated RNode on a pseudo-terminal: the radio set-up it sends on
# connect, a packet out, a packet in, an unplug and replug (also under
# another name), a radio that freezes, /admin's retry, and the radio off
# on close.
# Linux/macOS (pty). Run: python3 tests/pc/rnode_serial_test.py
import os, sys, pty, time, threading
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "pc", "pc_compat"), os.path.join(ROOT, "final_firmware")]
import stump_host
stump_host.install(os.path.join(ROOT, "final_firmware"))
import tty
import urns.interfaces.rnode_serial as rs
rs.BOOT_WAIT_S = 0.2
from urns.log import set_loglevel, LOG_NONE
set_loglevel(LOG_NONE)

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
FAIL = []


def _crash(kind, value, tb):
    import traceback
    text = "".join(traceback.format_exception(kind, value, tb))
    print(text)
    annotate("crashed: " + text[-2500:])
    sys.exit(1)

sys.excepthook = _crash


def annotate(text):
    """On GitHub Actions, a failure shows as an annotation (readable without the log)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + text.replace("%", "%25").replace("\r", "").replace("\n", "%0A"))


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + repr(info)[:300]))
    if not cond:
        FAIL.append(name)
        annotate(name + ": " + repr(info)[:1500])


def frames(raw):
    """KISS frames in raw bytes -> [(command, payload)]."""
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
        if b == FEND:
            body += bytes([FESC, TFEND])
        elif b == FESC:
            body += bytes([FESC, TFESC])
        else:
            body.append(b)
    return bytes([FEND, cmd]) + bytes(body) + bytes([FEND])


master, slave = pty.openpty()
tty.setraw(slave)
port = os.ttyname(slave)
got = bytearray()
stop = threading.Event()


answering = [True]      # the fake RNode answers the handshake (False: a silent board)


def reader():
    os.set_blocking(master, False)
    while not stop.is_set():
        try:
            data = os.read(master, 4096)
            got.extend(data)
            if bytes([FEND, 0x46, 0x02, FEND]) in data:
                os.write(master, kiss(0x62, (482913).to_bytes(4, "big")))     # pairing mode: the PIN
            if answering[0] and bytes([FEND, 0x08, 0x73, FEND]) in data:
                # DETECT -> DETECT_RESP; FW_VERSION -> 1.78; PLATFORM -> ESP32
                os.write(master, kiss(0x08, b"\x46") + kiss(0x50, b"\x01\x4e") + kiss(0x48, b"\x80"))
        except (BlockingIOError, OSError):
            time.sleep(0.02)

threading.Thread(target=reader, daemon=True).start()
iface = rs.RNodeSerialInterface({"port": port, "frequency": 915000000, "bandwidth": 125000,
                                 "txpower": 7, "spreadingfactor": 8, "codingrate": 5})
time.sleep(0.5)
f = [x for x in frames(got) if x[0] not in (0x08, 0x50, 0x48)]
check("the handshake first: DETECT, firmware, platform", [c for c, p in frames(got)][:3] == [0x08, 0x50, 0x48],
      frames(got)[:3])
st = iface.status()
check("the handshake answered: online, its firmware and port in the status", st["state"] == "online"
      and st["detected"] and st["fw"] == "1.78" and st["platform"] == "ESP32" and st["where"] == port
      and st["kind"] == "usb", st)
cmds = [c for c, p in f]
# RNode commands: FREQUENCY 0x01, BANDWIDTH 0x02, TXPOWER 0x03, SF 0x04, CR 0x05, RADIO_STATE 0x06
check("online after connect", iface.online)
import chain
check("connected: this Stump introduces itself on the radio (beacon soon)", chain.take_link_event())
check("radio set up on connect, in order", cmds[:6] == [0x01, 0x02, 0x03, 0x04, 0x05, 0x06], cmds)
check("frequency 915 MHz", f[0][1] == (915000000).to_bytes(4, "big"), f[0])
check("bandwidth 125 kHz", f[1][1] == (125000).to_bytes(4, "big"))
check("power 7, SF8, CR5, radio on", [p for c, p in f[2:6]] == [b"\x07", b"\x08", b"\x05", b"\x01"], f[2:6])

got.clear()
pkt = bytes(range(40)) + bytes([FEND, FESC]) + b"tail"
iface.process_outgoing(pkt)
time.sleep(0.3)
check("a packet goes out as one data frame, escaped", frames(got) == [(0x00, pkt)], frames(got))
check("no keepalive frames on USB", iface.KEEPALIVE_INTERVAL > 10 ** 6)

received = []
iface.process_incoming = lambda data: received.append(bytes(data))
import uasyncio as asyncio


async def run(seconds):
    t = asyncio.create_task(iface.poll_loop())
    await asyncio.sleep(seconds)
    t.cancel()

incoming = bytes(range(60)) + bytes([FEND]) + b"in"
os.write(master, b"boot noise" + kiss(0x23, bytes([157 - 92])) + kiss(0x24, bytes([256 - 26]))
         + kiss(0x00, incoming) + kiss(0x25, bytes([0, 150, 0, 0, 3, 32, 0, 0, 0, 157 - 118]))
         + kiss(0x27, b"\x02\x57") + kiss(0x01, (915000000).to_bytes(4, "big")) + kiss(0x06, b"\x01"))
asyncio.run(run(0.5))
check("a packet comes in (boot noise and other commands ignored)", received == [incoming], received)
st = iface.status()
check("its reports read: RSSI, SNR, channel load, airtime, noise, battery, radio echo",
      st["rssi"] == -92 and st["snr"] == -6.5 and st["channel_load"] == 8.0 and st["airtime"] == 1.5
      and st["noise"] == -118 and st["battery"] == 87 and st["radio"].get("frequency") == 915000000
      and st["radio"].get("on") == 1 and st["last_rx"], st)

# Bluetooth from /admin: on, off, pairing mode -- the PIN comes back, kept
# out of the public status.
got.clear()
ok = iface.bluetooth("on") and iface.bluetooth("off")
time.sleep(0.3)
check("bluetooth on/off: sent", ok
      and frames(got)[-2:] == [(0x46, b"\x01"), (0x46, b"\x00")], frames(got)[-2:])
check("bluetooth: nonsense refused", not iface.bluetooth("explode"))
got.clear()
iface.bluetooth("unpair")
time.sleep(0.3)
check("forget paired devices: sent (CMD_BT_UNPAIR)", frames(got)[-1:] == [(0x70, b"\x01")], frames(got)[-2:])
iface.bluetooth("pair")
asyncio.run(run(0.5))
check("pairing mode: the RNode's PIN, six digits", iface.bt["pin"] == "482913", iface.bt)
check("the PIN never in the public status", "482913" not in repr(iface.status()))

# Unplug, the way macOS does it with a CP2102: reads just go quiet, and
# the port vanishes. Seen every second: offline at once, red, says why.
real_port = iface.port
_present = rs.port_present
plugged = [True]
rs.port_present = lambda d: plugged[0] and d == real_port and _present(d)
plugged[0] = False
asyncio.run(run(1.6))
st = iface.status()
check("unplugged (port gone, reads quiet): offline within a second or two, red, says so",
      not iface.online and st["state"] == "error" and "unplugged" in (st["error"] or ""), st)
asyncio.run(run(2.5))
check("... and stays so while it's gone (no handshake tried)", not iface.online
      and "unplugged" in (iface.status()["error"] or ""), iface.status())
plugged[0] = True
got.clear()
asyncio.run(run(rs.RNodeSerialInterface.LOOK_EVERY + 1.5))
check("plugged back in: reconnects by itself, handshake and radio set-up again", iface.online
      and [c for c, p in frames(got)][:3] == [0x08, 0x50, 0x48]
      and [c for c, p in frames(got) if c not in (0x08, 0x50, 0x48)][:6] == [0x01, 0x02, 0x03, 0x04, 0x05, 0x06],
      frames(got)[:9])

# Plugged back in under another name (macOS: usbserial-0001 -> -0002;
# Windows: another COM): found again ("auto" finding).
plugged[0] = False
asyncio.run(run(1.5))
iface.port_setting = "/dev/stump-test-old-name"
_find_port = rs.find_port
rs.find_port = lambda: real_port
plugged[0] = True
asyncio.run(run(rs.RNodeSerialInterface.LOOK_EVERY + 1.5))
check("back under another name: found and connected", iface.online and iface.port == real_port, iface.status())
rs.find_port = _find_port
iface.port_setting = real_port

# A radio that freezes (plugged in, port there, answers nothing): the
# "are you there?" probe finds it.
iface.PROBE_EVERY, iface.PROBE_WAIT = 0.5, 0.5
answering[0] = False
asyncio.run(run(1.6))
st = iface.status()
check("stops answering: offline, red, says so", not iface.online and st["state"] == "error"
      and "stopped answering" in (st["error"] or ""), st)
answering[0] = True
asyncio.run(run(9))
check("answers again: back online by itself", iface.online, iface.status())
iface.PROBE_EVERY, iface.PROBE_WAIT = 10.0, 4.0

# /admin's "Reconnect the radio": the handshake again, now.
got.clear()
iface.retry_now()
check("retry: offline at once", not iface.online)
asyncio.run(run(1.5))
check("retry: handshake and set-up again, online", iface.online and [c for c, p in frames(got)][:3] == [0x08, 0x50, 0x48],
      frames(got)[:4])

# A board that doesn't answer the handshake (not the RNode firmware): never
# "online", and the status says why.
answering[0] = False
iface.retry_now()
asyncio.run(run(rs.HANDSHAKE_S + 1.5))
st = iface.status()
check("no answer to the handshake: not online, an error that says what to check", not iface.online
      and st["state"] == "error" and "RNode firmware" in (st["error"] or ""), st)
# There but deaf twice in a row (e.g. it counts a Bluetooth device as
# connected, and then ignores USB): restarted through its reset line --
# the reset button, by wire.
resets = []
_hard = rs.hard_reset
rs.hard_reset = lambda dev: resets.append(dev)
iface._mute = 0
iface._reset_at = -10 ** 9            # (an earlier silent stretch may have used it)
for _ in range(2):
    iface.retry_now()
    asyncio.run(run(rs.HANDSHAKE_S + 1.0))
check("deaf twice in a row: restarted through its reset line, once", resets == [iface.port], resets)
for _ in range(2):
    iface.retry_now()
    asyncio.run(run(rs.HANDSHAKE_S + 1.0))
check("deaf twice more right after: no second reset (never a stream of them)", resets == [iface.port], resets)
rs.hard_reset = _hard

# Stopping the Stump: the radio off and "the host left", so the RNode
# doesn't keep transmitting on its own.
answering[0] = True
iface.retry_now()
asyncio.run(run(1.5))
got.clear()
iface.close()
time.sleep(0.3)
check("on close: radio off, then leave", frames(got) == [(0x06, b"\x00"), (0x0A, b"\xff")], frames(got))
rs.port_present = _present
stop.set()
print("FAILURES", FAIL)
sys.exit(1 if FAIL else 0)
