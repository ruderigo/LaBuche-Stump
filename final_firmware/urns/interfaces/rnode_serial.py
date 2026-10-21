# µReticulum RNode over USB serial -- a Heltec (or any RNode) plugged into
# a computer running Stump (docs/PC.md).
#
# The same RNode protocol as WiFiSerialInterface -- real KISS framing and
# the radio set-up commands sent on every connect -- with a USB serial
# port underneath instead of a TCP socket. Only the transport changes, so
# the protocol code (tested on the air with the CAM's Heltec bridge) is
# shared rather than copied: _SerialPort gives the serial port the few
# socket calls WiFiSerialInterface makes (settimeout, sendall, readinto,
# close).
#
# Needs pyserial, so it runs on a computer (CPython), not on the ESP32.
#
# config.py:
#   {"type": "RNodeSerialInterface", "name": "Heltec (USB)", "enabled": True,
#    "port": "/dev/cu.usbserial-0001",     # "COM5" on Windows; "auto" finds it
#    "frequency": 915000000, "bandwidth": 125000, "txpower": 7,
#    "spreadingfactor": 8, "codingrate": 5}

import os
import time
from .wifi_serial import (WiFiSerialInterface, FEND, CMD_DETECT, DETECT_REQ, CMD_FW_VERSION,
                          CMD_PLATFORM, CMD_RADIO_STATE, RADIO_STATE_OFF, CMD_LEAVE)
from ..log import log, LOG_NOTICE, LOG_ERROR, LOG_VERBOSE

HANDSHAKE_S = 3.0     # how long an RNode has to answer "are you an RNode?"

BAUD = 115200
# A restart through the reset line at most this often (s): never a stream
# of resets -- one landing while the RNode saves a setting to its flash
# could spoil what it keeps there.
RESET_EVERY_S = 300.0
BOOT_WAIT_S = 2.5     # opening the port resets most ESP32 boards; let it boot

# USB-serial chips found on RNode boards (Heltec V3: CP2102; others CH340/CH9102,
# or the ESP32-S3's own USB), for "port": "auto".
_KNOWN_VIDS = (0x10C4, 0x1A86, 0x303A, 0x0403)


def find_port():
    """The first serial port that looks like an RNode board, or None."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        if p.vid in _KNOWN_VIDS:
            return p.device
    return None


def describe_port(device):
    """"CP2102 USB to UART Bridge Controller (Silicon Labs)" for a port, so
    the right board can be told apart on a hub; None if unknown."""
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if p.device == device:
                bits = [b for b in (p.product or p.description, p.manufacturer) if b and b != "n/a"]
                return " · ".join(bits) or None
    except Exception:
        pass
    return None


class _SerialPort:
    """A serial port that answers the socket calls WiFiSerialInterface makes."""

    def __init__(self, device):
        import serial
        self._serial = serial
        self.s = serial.Serial(device, BAUD, timeout=0, write_timeout=2,
                               rtscts=False, dsrdtr=False)

    def settimeout(self, t):
        pass                   # reads never block (timeout=0); writes have write_timeout

    def sendall(self, data):
        try:
            self.s.write(data)
        except self._serial.SerialException as e:
            raise OSError(5, str(e))

    def readinto(self, buf):
        try:
            n = min(len(buf), self.s.in_waiting)
            if not n:
                raise OSError(11, "no data")       # EAGAIN, as a non-blocking socket says
            data = self.s.read(n)
        except self._serial.SerialException as e:
            raise OSError(5, str(e))               # unplugged: poll_loop goes offline and retries
        buf[:len(data)] = data
        return len(data)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def hard_reset(device):
    """Restarts the board through its USB-serial chip's reset line (RTS to
    the ESP32's EN), as flashing tools do -- the reset button, whatever the
    firmware is doing: an RNode that counts a Bluetooth device as connected
    ignores everything on its USB, its own reset command included. Opened
    with DTR and RTS off, so opening doesn't glitch it; DTR stays off, so
    it boots normally (not into its bootloader)."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate = device, BAUD
    s.dtr, s.rts = False, False
    s.open()
    try:
        s.rts = True               # EN low: held in reset
        s.dtr = False              # (some drivers only apply RTS with a DTR write)
        time.sleep(0.15)
        s.rts = False              # let go: it boots
        s.dtr = False
        time.sleep(0.05)
    finally:
        s.close()


def port_present(device):
    """Is this serial port still there? (Unplugging a USB board makes its
    port vanish; reads on the open port don't always say so.)"""
    if not device or device == "auto":
        return False
    if os.name != "nt" and device.startswith("/dev/"):
        return os.path.exists(device)
    try:
        from serial.tools import list_ports
        return any(p.device == device for p in list_ports.comports())
    except Exception:
        return True            # can't tell: the probe (below) still will


class RNodeSerialInterface(WiFiSerialInterface):
    # USB doesn't drop an idle link the way the Heltec's Wi-Fi mode does: no
    # keepalive frames needed (or wanted -- nothing should go to the radio
    # that isn't a packet).
    KEEPALIVE_INTERVAL = 10 ** 9
    KIND = "usb"
    # Is the radio still there? The port is looked at every second (an
    # unplugged board's port vanishes), and a quiet radio is asked "are you
    # an RNode?" every PROBE_EVERY s: no answer in PROBE_WAIT s and it's
    # gone (frozen, reset, cable half out).
    PRESENCE_EVERY = 1.0
    PROBE_EVERY = 10.0
    PROBE_WAIT = 4.0
    # Reconnecting: while the port isn't there, looking is cheap, so every
    # LOOK_EVERY s; when it is but the handshake fails, backing off up to
    # MAX_RECONNECT_WAIT (each try holds the node for a few seconds).
    LOOK_EVERY = 2.0
    RECONNECT_WAIT = 2
    MAX_RECONNECT_WAIT = 20

    def __init__(self, config):
        port = config.get("port", "auto")
        self.port_setting = port if port not in (None, "") else "auto"
        if self.port_setting == "auto":
            port = find_port() or "auto"
        self.port = port
        self._last_heard = 0
        self._probe_at = 0
        self._last_presence = 0
        self._last_look = 0
        self._lost_why = None
        self._mute = 0                         # handshakes in a row that got no answer
        self._reset_at = -RESET_EVERY_S        # the last restart through the reset line
        config = dict(config)
        config.setdefault("name", "Heltec (USB)")
        config["target_host"] = port           # WiFiSerialInterface requires one; used in its logs
        super().__init__(config)

    # -- the port underneath (RNodeBLEInterface changes these) --------------

    def _find(self):
        """The port to use now, or None: the one set, or -- if it's gone
        (a board plugged back in can come back under another name) or set
        to "auto" -- the first that looks like an RNode."""
        if self.port_setting != "auto" and port_present(self.port_setting):
            return self.port_setting
        if self.port != "auto" and port_present(self.port):
            return self.port
        return find_port()

    def _present(self):
        return port_present(self.port)

    def _open(self):
        """Opens the radio's port: a socket-like object (_SerialPort)."""
        port = self._find()
        if not port:
            raise OSError(19, "no RNode found on USB (plug the Heltec in)")
        self.port = self.target_host = port
        self.hw["device"] = describe_port(port)
        s = _SerialPort(port)
        time.sleep(BOOT_WAIT_S)
        try:
            s.s.reset_input_buffer()           # the board's boot messages aren't KISS
        except Exception:
            pass
        return s

    def _lost_text(self):
        return "unplugged from %s (it reconnects by itself when it's back)" % self.port

    # -- connecting ----------------------------------------------------------

    def _connect(self):
        s = self._open()
        self._socket = s
        self._in_frame = False
        self._escape = False
        self._command = None
        self._buffer = bytearray()
        # The handshake: an RNode answers DETECT, and says its firmware.
        self.hw["detected"] = False
        self._send_command(CMD_DETECT, bytes([DETECT_REQ]))
        self._send_command(CMD_FW_VERSION, b"\x00")
        self._send_command(CMD_PLATFORM, b"\x00")
        t0 = time.time()
        limit = getattr(self, "HANDSHAKE_WAIT", None) or HANDSHAKE_S
        again = False
        while not self.hw["detected"] and time.time() - t0 < limit:
            if not again and time.time() - t0 > limit / 2:
                again = True                   # asked once more, halfway
                self._send_command(CMD_DETECT, bytes([DETECT_REQ]))
            try:
                n = s.readinto(self._recv_buf)
                for i in range(n):
                    self._process_byte(self._recv_mv[i])
            except OSError as e:
                if e.args and e.args[0] != 11:
                    raise
                time.sleep(0.05)
        if not self.hw["detected"]:
            s.close()
            self._socket = None
            if self.KIND == "usb":
                self._mute += 1
                if self._mute % 2 == 0 and time.time() - self._reset_at >= RESET_EVERY_S:
                    self._reset_at = time.time()
                    # There but deaf -- e.g. it counts a Bluetooth device as
                    # connected (then it ignores USB): the reset button, by wire.
                    try:
                        hard_reset(self.port)
                        log("RNode USB: %s answered nothing twice: restarted it (reset line)" % self.port, LOG_NOTICE)
                    except Exception as e:
                        log("RNode USB: couldn't restart %s: %s" % (self.port, e), LOG_VERBOSE)
            raise OSError(5, "%s answered nothing to the RNode handshake: is the RNode firmware on it "
                             "(rnodeconf --autoinstall), and nothing else using it?" % self.where())
        self.online = True
        self._mute = 0
        self._reconnect_count = 0
        self.hw["lost"] = False
        now = time.time()
        self._last_activity = self._last_heard = self._last_presence = now
        self._probe_at = 0
        self.hw["error"] = None
        self.hw["since"] = now
        log("RNode %s: %s (%s), firmware %s" % (self.KIND.upper(), self.where(), self.hw.get("device") or "?",
                                               self.hw.get("fw") or "?"), LOG_NOTICE)
        self._init_radio()

    def _reconnect(self):
        now = time.time()
        if not self._present() and not self._find():
            # Nothing to connect to: say so, look again soon, no backoff.
            if now - self._last_look >= self.LOOK_EVERY:
                self._last_look = now
                self._open_failed_text()
                self._reconnect_count = max(self._reconnect_count, 1)
            return
        WiFiSerialInterface._reconnect(self)
        if not self.online and self.hw.get("lost"):
            # It worked until it was lost: keep saying why, not "is the
            # RNode firmware on it?" (a failed handshake while it recovers).
            self.hw["error"] = self._lost_why

    def _open_failed_text(self):
        if not self.hw.get("error") or not self.hw.get("lost"):
            self.hw["error"] = "no RNode found on USB (plug the Heltec in)"

    # -- while online: is it still there? -------------------------------------

    def _process_byte(self, byte):
        self._last_heard = time.time()
        WiFiSerialInterface._process_byte(self, byte)

    def _lost(self, why):
        log("RNode %s lost: %s" % (self.KIND.upper(), why), LOG_ERROR)
        self._close_socket()
        self.online = False
        self.hw["detected"] = False
        self.hw["error"] = self._lost_why = why
        self.hw["lost"] = True
        self._reconnect_count = max(self._reconnect_count, 1)   # "error" (red) until it's back
        self._last_reconnect = 0                                # look again right away

    def _health(self):
        now = time.time()
        if now - self._last_presence >= self.PRESENCE_EVERY:
            self._last_presence = now
            if not self._present():
                self._lost(self._lost_text())
                return
        if now < getattr(self, "quiet_until", 0):
            return                 # pairing over Bluetooth: no "still there?" meanwhile
        if self._probe_at and self._last_heard < self._probe_at:
            if now - self._probe_at > self.PROBE_WAIT:
                self._lost("stopped answering on %s (it reconnects by itself)" % self.where())
            return
        if now - self._last_heard > self.PROBE_EVERY:
            try:
                self._probe_at = now
                self._send_command(CMD_DETECT, bytes([DETECT_REQ]))
            except Exception as e:
                self._lost("can't write to %s: %s" % (self.where(), e))

    def retry_now(self):
        WiFiSerialInterface.retry_now(self)
        self._last_look = 0
        self.hw["lost"] = False

    def prime(self):
        pass

    def close(self):
        """On the way out: the radio off, and "the host left", as reference
        RNS does (detach) -- an RNode left on its own otherwise keeps its
        radio going with nothing behind it."""
        if self.online and self._socket:
            try:
                self._send_command(CMD_RADIO_STATE, bytes([RADIO_STATE_OFF]))
                self._send_command(CMD_LEAVE, b"\xff")
            except Exception:
                pass
        WiFiSerialInterface.close(self)

    def where(self):
        return self.port

    def status(self):
        h = WiFiSerialInterface.status(self)
        h.pop("lost", None)
        h["kind"] = self.KIND
        h["where"] = self.where()
        return h

    def __str__(self):
        return self.__class__.__name__ + "[" + self.name + "]"
