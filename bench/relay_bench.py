"""
How fast can this board pass a big file along a chain of Stumps?

Run on a CAM, with the node stopped (mpremote stops it), from the repo:

    mpremote connect PORT run bench/relay_bench.py

Then reboot the board (mpremote connect PORT reset) to start the node again.
Needs no second board and no network: it measures the board's own work.

A file moving between Stumps over Reticulum is cut into packets of up to
500 bytes. Every Stump in the middle of the chain receives each packet,
decides where it goes, and sends it on: it never decrypts it. The two
ends encrypt and decrypt. So this measures, per 483-byte file packet:

  1. RELAY   -- the forwarding work a Stump in the middle does
               (Transport.inbound -> link table -> transmit), quiet and
               with per-packet log lines (logging costs real time);
  2. ENDS    -- link encryption + decryption (AES-128 + HMAC-SHA256),
               what the sending and receiving Stumps do per packet;
  3. SOCKETS -- one UDP send and receive in MicroPython, which every hop
               adds on top of (1) (skipped if this firmware has no
               loopback network).

and prints the ceiling those set, in KB/s and MB per hour. Wi-Fi itself
is measured separately (bench/wifi_bench.py with a laptop).
"""
import time, gc, sys
if "" not in sys.path:
    sys.path.insert(0, "")      # the board's root, where urns/ lives

N = 2000                # packets per measurement
PAYLOAD = 464           # data bytes in a full 500-byte link packet

try:
    from urns.log import LOG_NONE, LOG_NOTICE, set_loglevel
    from urns import const
    from urns.transport import Transport
    from urns.identity import Identity
    from urns.interfaces import Interface
    from urns.crypto.token import Token
except ImportError as e:
    print("Run this from the Stump's board (urns/ must be on it):", e)
    sys.exit()


def kbps(per_packet_us, size=PAYLOAD):
    if per_packet_us <= 0:
        return 0
    return size / 1024 / (per_packet_us / 1e6)


def line(label, per_us):
    kb = kbps(per_us)
    print("  %-34s %7.0f us/packet  %6.0f packets/s  %6.0f KB/s  %6.0f MB/h"
          % (label, per_us, 1e6 / per_us, kb, kb * 3600 / 1024))


class Sink(Interface):
    """An interface that only counts what it would have sent."""
    def __init__(self, name):
        super().__init__(name)
        self.online = True
        self.count = 0

    def process_outgoing(self, raw):
        self.count += 1
        return True

    def __str__(self):
        return self.name


print("=" * 72)
print("Stump relay benchmark -- %d packets of %d bytes each" % (N, PAYLOAD))
print("=" * 72)
gc.collect()
print("free RAM: %d KB" % (gc.mem_free() // 1024))

# ---- 1. RELAY ----------------------------------------------------------
print("\n1. Relay (a Stump in the middle of the chain)")
t0 = time.ticks_ms()
Transport.identity = Identity()
Transport.transport_enabled = True
print("  (identity made in %d ms)" % time.ticks_diff(time.ticks_ms(), t0))
up, down = Sink("wifi-up"), Sink("wifi-down")
link_id = bytes(range(16))
Transport._add_link(link_id, bytes(16), down, 1, up, 1, bytes(range(16, 32)))
flags = (const.HDR_1 << 6) | (const.TRANSPORT_BROADCAST << 4) | (const.DEST_LINK << 2) | const.PKT_DATA
raw = bytes([flags, 0]) + link_id + bytes([const.CTX_RESOURCE]) + bytes(PAYLOAD)

relay = {}
for label, level in (("quiet (the field setting)", LOG_NONE),
                     ("logging every packet (NOTICE)", LOG_NOTICE)):
    set_loglevel(level)
    down.count = 0
    gc.collect()
    n = N if level == LOG_NONE else N // 4
    if level != LOG_NONE:
        print("  (the next %d log lines are part of the test)" % n)
    t0 = time.ticks_us()
    for _ in range(n):
        Transport.inbound(raw, up)
    dt = time.ticks_diff(time.ticks_us(), t0)
    set_loglevel(LOG_NONE)
    if down.count != n:
        print("  !! only %d of %d packets were forwarded" % (down.count, n))
    relay[label] = dt / n
for label, per in relay.items():
    line(label, per)

# ---- 2. ENDS -----------------------------------------------------------
print("\n2. Ends of the chain (link encryption, per packet)")
tok = Token(Token.generate_key())
plain = bytes(PAYLOAD - 64)          # room for IV, padding and HMAC
gc.collect()
t0 = time.ticks_us()
for _ in range(N // 4):
    ct = tok.encrypt(plain)
enc = time.ticks_diff(time.ticks_us(), t0) / (N // 4)
t0 = time.ticks_us()
for _ in range(N // 4):
    tok.decrypt(ct)
dec = time.ticks_diff(time.ticks_us(), t0) / (N // 4)
line("encrypt (sending Stump)", enc)
line("decrypt (receiving Stump)", dec)

# ---- 3. SOCKETS --------------------------------------------------------
print("\n3. UDP send + receive in MicroPython (each hop adds this)")
sock_us = None
try:
    import socket
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(socket.getaddrinfo("127.0.0.1", 47811)[0][-1])
    rx.settimeout(1)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = socket.getaddrinfo("127.0.0.1", 47811)[0][-1]
    got = 0
    t0 = time.ticks_us()
    for _ in range(N // 2):
        tx.sendto(raw, addr)
        try:
            rx.recv(600)
            got += 1
        except OSError:
            pass
    dt = time.ticks_diff(time.ticks_us(), t0)
    rx.close(); tx.close()
    if got:
        sock_us = dt / got
        line("send + receive, 483 bytes", sock_us)
        if got < N // 2:
            print("  (%d of %d arrived)" % (got, N // 2))
    else:
        print("  no datagram came back: this firmware has no loopback; skipped")
except Exception as e:
    print("  skipped (%s)" % e)

# ---- Ceiling -----------------------------------------------------------
print("\nCeiling for one hop in the middle of a chain (CPU only, before Wi-Fi):")
per = relay["quiet (the field setting)"] + (sock_us or 0)
line("relay%s" % (" + sockets" if sock_us else ""), per)
print("\nThe slowest link in the chain sets the speed: compare this with the")
print("Wi-Fi figure from bench/wifi_bench.py, and send both back.")
