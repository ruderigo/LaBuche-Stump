#!/usr/bin/env python3
"""Laptop side of bench/wifi_bench.py.   python3 bench/wifi_bench_host.py CAM_IP"""
import socket, sys, time

if len(sys.argv) != 2:
    sys.exit(__doc__)
CAM = sys.argv[1]
TCP_BYTES = 2 * 1024 * 1024
UDP_COUNT = 2000
PKT = bytes(483)


def line(sock):
    buf = b""
    while not buf.endswith(b"\n"):
        b = sock.recv(1)
        if not b:
            raise SystemExit("the CAM closed the connection")
        buf += b
    return buf.decode().split()


def report(label, nbytes, ms, extra=""):
    kbs = nbytes / 1024 / max(ms, 1) * 1000
    print("  %-30s %7.0f KB/s  %6.0f MB/hour%s" % (label, kbs, kbs * 3600 / 1024, extra))


c = socket.create_connection((CAM, 5201), timeout=60)
print("Connected to %s\n" % CAM)

c.sendall(b"UP %d\n" % TCP_BYTES)
block = bytes(16384)
for _ in range(TCP_BYTES // len(block)):
    c.sendall(block)
_, got, ms = line(c)
report("TCP laptop -> CAM", int(got), int(ms))

c.sendall(b"DOWN %d\n" % TCP_BYTES)
got, t0 = 0, time.time()
while got < TCP_BYTES:
    b = c.recv(65536)
    if not b:
        break
    got += len(b)
report("TCP CAM -> laptop", got, (time.time() - t0) * 1000)

u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
c.sendall(b"UDPIN\n")
line(c)                                         # GO
for i in range(UDP_COUNT):
    u.sendto(PKT, (CAM, 5202))
    if i % 20 == 19:
        time.sleep(0.002)                       # don't simply flood the CAM's buffers
_, got, ms = line(c)
report("UDP laptop -> CAM (483 B)", int(got) * 483, int(ms),
       "  %d/%d arrived" % (int(got), UDP_COUNT))

r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
r.bind(("0.0.0.0", 0))
r.settimeout(2)
c.sendall(b"UDPOUT %d %d\n" % (UDP_COUNT, r.getsockname()[1]))
got, first, last = 0, None, None
while True:
    try:
        r.recv(600)
    except socket.timeout:
        break
    now = time.time()
    first = first or now
    last = now
    got += 1
_, sent, ms = line(c)
report("UDP CAM -> laptop (483 B)", got * 483, ((last or 0) - (first or 0)) * 1000,
       "  %d/%d arrived" % (got, int(sent)))
c.sendall(b"BYE\n")
print("\nSend these back with relay_bench.py's numbers and where the laptop was"
      "\n(same room / one wall / one floor).")
