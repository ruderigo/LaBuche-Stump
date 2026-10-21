r"""
Can this CAM serve HTTPS, and how hard does it work at it?

    1. mpremote connect PORT run bench/tls_probe.py
       It opens the CAM's own Wi-Fi if it isn't up already ("StumpBench",
       no password, or the Stump's own while it runs), then serves HTTPS
       on port 8443 for two minutes, printing each connection: how long
       the secure handshake took and the free memory after it.
    2. From a laptop joined to that Wi-Fi (docs/HTTPS_SETUP.md, step 0):
         curl -sk -o /dev/null -w "handshake %{time_appconnect}s\n" https://192.168.4.1:8443/
       then a burst and a crowd:
         for i in $(seq 10); do curl -sk -o /dev/null -w "%{time_appconnect}s\n" https://192.168.4.1:8443/; done
         seq 6 | xargs -P6 -I{} curl -sk -o /dev/null -w "parallel %{time_appconnect}s\n" https://192.168.4.1:8443/
    3. It ends with a summary and RESULT: ... Reboot the CAM afterwards
       (mpremote connect PORT reset).

Uses the Stump's installed certificate (/tls, see --install-cert) when
there is one, else a throwaway certificate for "stump-probe.invalid"
built in below (that's why curl needs -k). It's public and good for
nothing else: it only proves the board can do TLS and times it.

What the numbers mean: since the chat stopped moving everything to HTTPS
(only the voice recorder uses it now, a few connections per note), even
slow handshakes are fine for voice notes. Under ~0.3 s and steady free
memory: the board copes with several people recording at once. Over
~1 s, or memory falling with each connection: voice notes still work,
one or two people at a time.
"""
import time, gc, sys
if "" not in sys.path:
    sys.path.insert(0, "")
import network
import uasyncio as asyncio

PORT = 8443
RUN_S = 120

PROBE_CERT = """-----BEGIN CERTIFICATE-----
MIIBTTCB9aADAgECAgEBMAoGCCqGSM49BAMCMB4xHDAaBgNVBAMME3N0dW1wLXBy
b2JlLmludmFsaWQwIBcNMjYwMTAxMDAwMDAwWhgPMjA1NTEyMjUwMDAwMDBaMB4x
HDAaBgNVBAMME3N0dW1wLXByb2JlLmludmFsaWQwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAARakxmKSwJi7Y0RAJQ63l38+hvDHmolu7q1dgOzllh4KlB1SXNCrJ5n
tIO4qcTdqV4yA+XACM26MwY6BSeEXBKQoyIwIDAeBgNVHREEFzAVghNzdHVtcC1w
cm9iZS5pbnZhbGlkMAoGCCqGSM49BAMCA0cAMEQCIAQH9evImz5TF9fM2c/52Zy9
BGhkCOQddXkF1cQI1F/9AiArgW1tj5gnGjS9Y2+vncbGmU1PuohEbJw/7+yRQIt2
hg==
-----END CERTIFICATE-----"""
PROBE_KEY = """-----BEGIN EC PRIVATE KEY-----
MHcCAQEEIG0RIcsoQUAsL+rrz/zECLdwC8xJuwUKfgMNsD6RuxozoAoGCCqGSM49
AwEHoUQDQgAEWpMZiksCYu2NEQCUOt5d/Pobwx5qJbu6tXYDs5ZYeCpQdUlzQqye
Z7SDuKnE3aleMgPlwAjNujMGOgUnhFwSkA==
-----END EC PRIVATE KEY-----"""


def certificate():
    """(cert, key, where from): the Stump's own when installed."""
    try:
        with open("/tls/fullchain.pem", "rb") as f:
            cert = f.read()
        with open("/tls/privkey.pem", "rb") as f:
            key = f.read()
        return cert, key, "the Stump's installed certificate"
    except OSError:
        return PROBE_CERT.encode(), PROBE_KEY.encode(), "the probe's throwaway certificate"


def wifi():
    ap = network.WLAN(network.AP_IF)
    if not ap.active() or ap.ifconfig()[0] == "0.0.0.0":
        ap.active(True)
        try:
            ap.config(essid="StumpBench", authmode=network.AUTH_OPEN)
        except Exception:
            pass
    return ap.ifconfig()[0]


results = []      # (handshake_ms, free_bytes)


async def handle(reader, writer):
    t0 = time.ticks_ms()
    try:
        await reader.readline()                 # the handshake happens here
        hs = time.ticks_diff(time.ticks_ms(), t0)
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
        body = b"stump tls probe ok\n"
        await writer.awrite(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: "
                            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
        gc.collect()
        free = gc.mem_free()
        results.append((hs, free))
        print("connection %2d: handshake %4d ms, free memory %d KB" % (len(results), hs, free // 1024))
    except Exception as e:
        print("connection failed:", e)
    finally:
        try:
            await writer.aclose()
        except Exception:
            pass


async def main():
    try:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    except (ImportError, AttributeError) as e:
        print("RESULT: this MicroPython can't serve TLS (%s): the firmware needs updating" % e)
        return
    cert, key, where = certificate()
    try:
        ctx.load_cert_chain(cert, key)
    except Exception as e:
        print("RESULT: the certificate or key was refused (%s)" % e)
        return
    ip = wifi()
    gc.collect()
    print("Using %s. Free memory before: %d KB" % (where, gc.mem_free() // 1024))
    try:
        await asyncio.start_server(handle, "0.0.0.0", PORT, ssl=ctx)
    except Exception as e:
        print("RESULT: couldn't serve TLS on port %d (%s)" % (PORT, e))
        return
    print("Serving https://%s:%d/ for %d s -- run the curl lines now." % (ip, PORT, RUN_S))
    await asyncio.sleep(RUN_S)
    if not results:
        print("RESULT: HTTPS works on this board (server up), but nothing connected: "
              "run the curl lines from a laptop on its Wi-Fi")
        return
    hs = sorted(r[0] for r in results)
    frees = [r[1] for r in results]
    print("\n%d connections. Handshake: fastest %d ms, median %d ms, slowest %d ms."
          % (len(hs), hs[0], hs[len(hs) // 2], hs[-1]))
    print("Free memory: first %d KB, last %d KB, lowest %d KB."
          % (frees[0] // 1024, frees[-1] // 1024, min(frees) // 1024))
    print("RESULT: HTTPS works on this board")


asyncio.run(main())
