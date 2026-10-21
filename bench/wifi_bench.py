"""
Wi-Fi speed between this CAM and a laptop, and the channels in use around it.

    1. mpremote connect PORT run bench/wifi_bench.py
       It joins the Wi-Fi in config.py (WIFI_SSID), or -- if that fails or
       USE_AP is True below -- opens its own network "StumpBench" (no
       password). It prints its address and waits.
    2. On a laptop on the same network:
       python3 bench/wifi_bench_host.py <that address>
    3. Reboot the CAM afterwards (mpremote connect PORT reset).

Measures TCP both ways (a file over HTTP is TCP), and 483-byte UDP
packets both ways (what a Reticulum link between Stumps would carry),
counting losses. Run it where a Stump will sit, and again a wall or a
floor away: that's the number that decides the chain.
"""
import time, gc, sys
if "" not in sys.path:
    sys.path.insert(0, "")
import network, socket

USE_AP = False          # True: skip config.py's Wi-Fi, open "StumpBench"
PORT_TCP, PORT_UDP = 5201, 5202
CHUNK = 4096


def scan_channels(wlan):
    try:
        nets = wlan.scan()
    except Exception as e:
        print("  scan failed:", e)
        return
    by = {}
    for n in nets:
        ch, rssi = n[2], n[3]
        c = by.setdefault(ch, [0, -100])
        c[0] += 1
        c[1] = max(c[1], rssi)
    print("  %d networks heard. Channel: count (strongest)" % len(nets))
    for ch in sorted(by):
        print("   %2d: %2d  (%d dBm)%s" % (ch, by[ch][0], by[ch][1],
              "   <- 1, 6, 11 don't overlap" if ch in (1, 6, 11) else ""))
    quiet = min((1, 6, 11), key=lambda c: (by.get(c, [0])[0], by.get(c, [0, -100])[1]))
    print("  Quietest of 1/6/11 here: %d" % quiet)


def connect():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    print("Channels around this board:")
    scan_channels(sta)
    if not USE_AP:
        try:
            from config import WIFI_SSID, WIFI_PASS
            print("\nJoining %r ..." % WIFI_SSID)
            sta.connect(WIFI_SSID, WIFI_PASS)
            for _ in range(40):
                if sta.isconnected():
                    break
                time.sleep(0.5)
            if sta.isconnected():
                try:
                    rssi = sta.status("rssi")
                except Exception:
                    rssi = "?"
                try:
                    ch = sta.config("channel")
                except Exception:
                    ch = "?"
                print("Joined: signal %s dBm, channel %s" % (rssi, ch))
                return sta.ifconfig()[0]
            print("Couldn't join; opening StumpBench instead.")
        except ImportError:
            print("No WIFI_SSID in config.py; opening StumpBench.")
    sta.active(False)
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="StumpBench")
    try:
        ap.config(authmode=network.AUTH_OPEN)
    except Exception:
        pass
    time.sleep(1)
    print("Opened Wi-Fi 'StumpBench': join it from the laptop.")
    return ap.ifconfig()[0]


def readline(c):
    buf = b""
    while not buf.endswith(b"\n"):
        b = c.recv(1)
        if not b:
            return None
        buf += b
    return buf.decode().strip()


def serve(ip):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(socket.getaddrinfo("0.0.0.0", PORT_TCP)[0][-1])
    srv.listen(1)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(socket.getaddrinfo("0.0.0.0", PORT_UDP)[0][-1])
    print("\nReady. On the laptop:  python3 bench/wifi_bench_host.py %s\n" % ip)
    c, peer = srv.accept()
    peer_ip = peer[0] if isinstance(peer, tuple) else "127.0.0.1"   # (Unix port: raw sockaddr)
    print("Laptop connected from", peer_ip)
    buf = bytearray(CHUNK)
    while True:
        cmd = readline(c)
        if not cmd or cmd == "BYE":
            break
        parts = cmd.split()
        if parts[0] == "UP":                    # laptop -> CAM, TCP
            want, got = int(parts[1]), 0
            t0 = time.ticks_ms()
            while got < want:
                n = c.readinto(buf) if hasattr(c, "readinto") else len(c.recv(CHUNK))
                if not n:
                    break
                got += n
            ms = max(1, time.ticks_diff(time.ticks_ms(), t0))
            c.send(("OK %d %d\n" % (got, ms)).encode())
            print("TCP laptop -> CAM: %d KB in %d ms = %d KB/s" % (got // 1024, ms, got * 1000 // 1024 // ms))
        elif parts[0] == "DOWN":                # CAM -> laptop, TCP
            want, sent = int(parts[1]), 0
            block = bytes(CHUNK)
            while sent < want:
                sent += c.write(block[:min(CHUNK, want - sent)])
        elif parts[0] == "UDPIN":               # laptop -> CAM, UDP
            udp.settimeout(2)
            got, first, last = 0, None, None
            c.send(b"GO\n")
            while True:
                try:
                    udp.recv(600)
                except OSError:
                    break
                now = time.ticks_ms()
                first = first if first is not None else now
                last = now
                got += 1
            ms = time.ticks_diff(last, first) if got > 1 else 0
            c.send(("OK %d %d\n" % (got, ms)).encode())
        elif parts[0] == "UDPOUT":              # CAM -> laptop, UDP
            count, port = int(parts[1]), int(parts[2])
            dest = socket.getaddrinfo(peer_ip, port)[0][-1]
            pkt = bytes(483)
            t0 = time.ticks_ms()
            sent = 0
            for _ in range(count):
                try:
                    udp.sendto(pkt, dest)
                    sent += 1
                except OSError:
                    time.sleep_ms(2)            # out of buffers: back off
            ms = max(1, time.ticks_diff(time.ticks_ms(), t0))
            c.send(("OK %d %d\n" % (sent, ms)).encode())
    c.close(); srv.close(); udp.close()
    print("Done. Reboot the board: mpremote connect PORT reset")


gc.collect()
serve(connect())
