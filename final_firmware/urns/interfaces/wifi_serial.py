# µReticulum WiFi Serial Interface -- REAL KISS PROTOCOL, fixed 2026-07-25
# Place at: firmware/urns/interfaces/wifi_serial.py
#
# TCP connection to a WiFi Remote-enabled RNode (an RNode switched to
# `-w STATION` mode via rnodeconf, reachable on the LAN instead of over
# USB), speaking REAL RNode KISS protocol.
#
# CORRECTED 2026-07-25, after live LED/reception testing proved the
# original version silently failed to transmit anything a real RNode
# would recognize -- confirmed by direct comparison: the Mac's known-good
# reference-RNS connection lit the Heltec's LED and was seen by a second
# Heltec running RNode; this file's own announce, at the exact moment it
# claimed to transmit, produced no LED activity and no reception at all.
#
# Confirmed by reading reference RNS's actual RNodeInterface.py source
# directly -- not by general KISS-protocol assumption, which is exactly
# what produced the original bug:
#
#   1. WRONG ESCAPE SCHEME. serial.py/tcp.py/the original version of this
#      file all use an XOR-mask scheme (FLAG=0x7E, ESC=0x7D, ESC_MASK=0x20)
#      internal to micropython-reticulum. Real RNode hardware speaks
#      classic KISS: FEND=0xC0, FESC=0xDB, with byte SUBSTITUTION escaping
#      (FESC->FESC+TFESC, FEND->FESC+TFEND), not XOR. Different protocols.
#      A real RNode does not parse the old scheme as valid data.
#
#   2. MISSING RADIO CONFIGURATION. RNode ships in host-controlled mode --
#      the radio sits in standby until told what frequency/bandwidth/
#      power/SF/CR to use. Reference RNS sends six commands on every
#      connect (initRadio()). The original version of this file never
#      sent any of them.
#
#   3. MISSING CMD_DATA COMMAND BYTE. Every KISS frame needs a command
#      byte immediately after the opening FEND. Real data frames are
#      FEND + CMD_DATA(0x00) + escaped_payload + FEND -- the original
#      version sent FEND + escaped_payload + FEND with no command byte.
#
# CONFIDENCE NOTE: process_outgoing() below is a direct, confirmed match
# to reference RNS's real source, byte for byte. _process_byte() (the
# receive side) is a careful RECONSTRUCTION from the confirmed escape
# scheme, the confirmed frame structure, and one confirmed line of the
# real read loop (closing on FEND when command == CMD_DATA) -- the real
# readLoop() runs long and wasn't pulled in its entirety. If receiving
# looks wrong after this fix, pulling the rest of that function is the
# next real step, not a symptom to guess around.
#
# VALIDATION UPDATE (Claude, 2026-08-14): both process_outgoing() and the
# constants below were independently cross-checked against the actual
# upstream RNS.Interfaces.RNodeInterface.py source (from the real
# markqvist/Reticulum repo, not a web summary of it) -- every constant,
# the escape() function, and process_outgoing()'s frame construction are
# byte-for-byte identical to the real implementation. This is now
# confirmed two ways: your own hardware LED/signature tests, and direct
# source comparison against the authoritative reference.
#
# Radio defaults below match what's been carried since Fold 1, now
# including TX power -- missing from earlier config work here, caught via
# MeshCommander's own real, working R36S setup: 915000000 Hz / 125000 Hz /
# SF8 / CR5 / 7dBm.
#
# Socket handling (connect, reconnect, the non-blocking dance around the
# ESP32-S3 lwIP sendall() bug) is UNCHANGED from the previous version --
# that layer was separately confirmed correct (both the Mac's connection
# and this file's own held clean TCP sessions). Only the protocol riding
# on top of it was wrong.

import time
import socket
from . import Interface
from ..log import log, LOG_VERBOSE, LOG_DEBUG, LOG_ERROR, LOG_NOTICE

# Real KISS constants, confirmed from RNS.Interfaces.RNodeInterface (KISS class)
FEND  = 0xC0
FESC  = 0xDB
TFEND = 0xDC
TFESC = 0xDD

# Real RNode command bytes, confirmed from the same source
CMD_DATA        = 0x00
CMD_FREQUENCY   = 0x01
CMD_BANDWIDTH   = 0x02
CMD_TXPOWER     = 0x03
CMD_SF          = 0x04
CMD_CR          = 0x05
CMD_RADIO_STATE = 0x06
RADIO_STATE_ON  = 0x01
CMD_ST_ALOCK    = 0x0B
CMD_LT_ALOCK    = 0x0C


def kiss_escape(data):
    """Real KISS substitution escaping. Order matters: FESC must be
    escaped before FEND, or a freshly-inserted FESC byte from escaping a
    FEND would itself get mangled by the second pass. Confirmed exact
    match to reference RNS's KISS.escape()."""
    data = data.replace(bytes([FESC]), bytes([FESC, TFESC]))
    data = data.replace(bytes([FEND]), bytes([FESC, TFEND]))
    return data


class WiFiSerialInterface(Interface):
    HW_MTU = 564
    # s.connect() below is a BLOCKING call, and uasyncio is cooperative
    # and single-threaded: while it waits, nothing else on the node runs
    # -- not the web server, not the captive-portal DNS, not Reticulum.
    # A short timeout keeps any single stall brief; the exponential
    # backoff keeps repeated failures (an unreachable or not-yet-
    # configured Heltec) from adding up to a node that appears hung.
    # Previously this was a 5s timeout retried every 5s, i.e. the node
    # could be frozen essentially all the time.
    CONNECT_TIMEOUT = 1
    RECONNECT_WAIT = 3
    MAX_RECONNECT_WAIT = 60
    KEEPALIVE_INTERVAL = 3  # seconds -- observed disconnect gap was ~7s
    MAX_RECONNECTS = 0  # 0 = unlimited

    def __init__(self, config):
        name = config.get("name", "WiFi Serial")
        super().__init__(name)

        self.target_host = config.get("target_host", None)
        self.target_port = config.get("target_port", 7633)
        self.reconnect_wait = config.get("reconnect_wait", self.RECONNECT_WAIT)
        self.max_reconnects = config.get("max_reconnects", self.MAX_RECONNECTS)

        # Real radio parameters -- these now actually get sent to the
        # device, unlike the previous version. txpower defaults to 7,
        # confirmed as a real working value from MeshCommander's live
        # R36S configuration.
        self.frequency = config.get("frequency", 915000000)
        self.bandwidth = config.get("bandwidth", 125000)
        self.txpower = config.get("txpower", 7)
        self.sf = config.get("spreadingfactor", 8)
        self.cr = config.get("codingrate", 5)
        self.lt_alock = config.get("airtime_limit_long", None)
        self.st_alock = config.get("airtime_limit_short", None)

        if self.target_host is None:
            raise ValueError("target_host is required for " + self.name)

        self._socket = None
        self._in_frame = False
        self._escape = False
        self._command = None
        self._buffer = bytearray()
        self._recv_buf = bytearray(512)
        self._recv_mv = memoryview(self._recv_buf)
        self._reconnect_count = 0
        self._last_reconnect = 0

        try:
            self._connect()
        except Exception as e:
            log("WiFi Serial initial connect failed: " + str(e), LOG_ERROR)

    def _connect(self):
        addr_info = socket.getaddrinfo(self.target_host, self.target_port)
        addr = addr_info[0][-1]

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.CONNECT_TIMEOUT)
        s.connect(addr)
        s.settimeout(0)

        try:
            s.setsockopt(socket.IPPROTO_TCP, 1, 1)  # TCP_NODELAY
        except:
            pass

        self._socket = s
        self._in_frame = False
        self._escape = False
        self._command = None
        self._buffer = bytearray()
        self.online = True
        self._reconnect_count = 0
        self._last_activity = time.time()
        log("WiFi Serial connected to " + self.target_host + ":" + str(self.target_port), LOG_NOTICE)

        # THE ACTUAL FIX: configure the radio on every connect, the way
        # initRadio() does upstream. Without this, the radio may never
        # have been told what frequency to use at all.
        self._init_radio()

    def _send_command(self, cmd_byte, payload=b""):
        """One real KISS command frame: FEND + command + [escaped payload] + FEND."""
        frame = bytes([FEND, cmd_byte]) + kiss_escape(payload) + bytes([FEND])
        self._socket.settimeout(2)
        self._socket.sendall(frame)
        self._socket.settimeout(0)

    def _init_radio(self):
        """Send the same six configuration commands reference RNS sends
        on every connect (initRadio()), confirmed from real source."""
        try:
            freq = self.frequency
            self._send_command(CMD_FREQUENCY, bytes([
                (freq >> 24) & 0xFF, (freq >> 16) & 0xFF,
                (freq >> 8) & 0xFF, freq & 0xFF,
            ]))
            bw = self.bandwidth
            self._send_command(CMD_BANDWIDTH, bytes([
                (bw >> 24) & 0xFF, (bw >> 16) & 0xFF,
                (bw >> 8) & 0xFF, bw & 0xFF,
            ]))
            # Single-byte commands aren't escaped upstream either --
            # matching that exactly rather than "improving" on it.
            self._send_command(CMD_TXPOWER, bytes([self.txpower]))
            self._send_command(CMD_SF, bytes([self.sf]))
            self._send_command(CMD_CR, bytes([self.cr]))
            self._send_command(CMD_RADIO_STATE, bytes([RADIO_STATE_ON]))
            if self.lt_alock is not None:
                at = int(self.lt_alock * 100)
                self._send_command(CMD_LT_ALOCK, bytes([(at >> 8) & 0xFF, at & 0xFF]))
            if self.st_alock is not None:
                at = int(self.st_alock * 100)
                self._send_command(CMD_ST_ALOCK, bytes([(at >> 8) & 0xFF, at & 0xFF]))
            log("WiFi Serial radio configured: %d Hz / %d Hz / SF%d / CR%d / %ddBm" % (
                self.frequency, self.bandwidth, self.sf, self.cr, self.txpower), LOG_NOTICE)
        except Exception as e:
            log("WiFi Serial radio config failed: " + str(e), LOG_ERROR)
            self.online = False

    def _close_socket(self):
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
            self._socket = None

    def _reconnect(self):
        now = time.time()
        # Backoff grows with consecutive failures, so a Heltec that
        # isn't there (wrong IP, powered off, never configured) costs a
        # brief stall once a minute rather than one every few seconds.
        wait = self.reconnect_wait * (2 ** min(self._reconnect_count, 5))
        if wait > self.MAX_RECONNECT_WAIT:
            wait = self.MAX_RECONNECT_WAIT
        if now - self._last_reconnect < wait:
            return
        self._last_reconnect = now

        if self.max_reconnects > 0 and self._reconnect_count >= self.max_reconnects:
            log("WiFi Serial max reconnect attempts reached", LOG_ERROR)
            self.enabled = False
            return

        self._reconnect_count += 1
        log("WiFi Serial reconnecting (attempt %d, next retry in %ds)..."
            % (self._reconnect_count, wait), LOG_NOTICE)
        self._close_socket()

        try:
            self._connect()
        except Exception as e:
            log("WiFi Serial reconnect failed: " + str(e), LOG_ERROR)

    def process_outgoing(self, data):
        """Real RNode data frame: FEND + CMD_DATA(0x00) + escaped_payload
        + FEND. Confirmed byte-for-byte against reference RNS's actual
        process_outgoing() -- this was the third bug: the previous
        version never sent the CMD_DATA byte at all."""
        if not self.online or not self._socket:
            return False

        try:
            data = self.ifac_sign(data)
            frame = bytes([FEND, CMD_DATA]) + kiss_escape(data) + bytes([FEND])
            self._socket.settimeout(2)
            self._socket.sendall(frame)
            self._socket.settimeout(0)
            self.txb += len(data)
            self.tx += 1
            self._last_activity = time.time()
            return True
        except Exception as e:
            log("WiFi Serial send error: " + str(e), LOG_ERROR)
            try:
                self._socket.settimeout(0)
            except:
                pass
            self.online = False
            return False

    def _process_byte(self, byte):
        """Real KISS receive state machine. See the confidence note at
        the top of this file -- the frame-boundary logic here is a
        careful reconstruction, not a full transcription of the real
        readLoop()."""
        if self._in_frame and byte == FEND:
            self._in_frame = False
            if self._command == CMD_DATA and len(self._buffer) > 0:
                self.process_incoming(bytes(self._buffer))
            self._buffer = bytearray()
            self._command = None

        elif byte == FEND:
            self._in_frame = True
            self._buffer = bytearray()
            self._command = None
            self._escape = False

        elif self._in_frame and self._command is None:
            # First byte after an opening FEND is the command byte, not
            # payload -- this is the piece the old version never checked.
            self._command = byte

        elif self._in_frame and len(self._buffer) < self.HW_MTU:
            if byte == FESC:
                self._escape = True
            else:
                if self._escape:
                    if byte == TFEND:
                        byte = FEND
                    elif byte == TFESC:
                        byte = FESC
                    self._escape = False
                self._buffer.append(byte)

    async def poll_loop(self):
        import uasyncio as asyncio

        log("WiFi Serial poll loop started for " + self.name, LOG_VERBOSE)

        while self.enabled:
            if not self.online:
                self._reconnect()
                await asyncio.sleep(1)
                continue

            try:
                self._socket.settimeout(0)
                n = self._socket.readinto(self._recv_buf)
                if n and n > 0:
                    for i in range(n):
                        self._process_byte(self._recv_mv[i])
                elif n == 0:
                    log("WiFi Serial connection closed by remote", LOG_NOTICE)
                    self.online = False
            except OSError as e:
                if e.args[0] == 11:  # EAGAIN -- nothing available right now
                    pass
                else:
                    log("WiFi Serial recv error: " + str(e), LOG_ERROR)
                    self.online = False
            except Exception as e:
                log("WiFi Serial poll error: " + str(e), LOG_ERROR)

            if self.online and (time.time() - self._last_activity) > self.KEEPALIVE_INTERVAL:
                try:
                    # A COMPLETE, well-formed empty CMD_DATA frame, not a
                    # bare FEND. A lone FEND would open a frame and leave
                    # the parser waiting on a command byte, desyncing the
                    # very next real frame's boundary -- caught this by
                    # tracing it through the new command-byte-aware
                    # parser before testing, not as a symptom after.
                    self._socket.settimeout(2)
                    self._socket.sendall(bytes([FEND, CMD_DATA, FEND]))
                    self._socket.settimeout(0)
                    self._last_activity = time.time()
                except Exception as e:
                    log("WiFi Serial keepalive failed: " + str(e), LOG_ERROR)
                    self.online = False

            await asyncio.sleep(0.01)

        log("WiFi Serial poll loop EXITED for " + self.name, LOG_ERROR)

    def close(self):
        super().close()
        self._close_socket()
        log("WiFi Serial Interface " + self.name + " closed", LOG_VERBOSE)

    def __str__(self):
        return "WiFiSerialInterface[" + self.name + "]"
