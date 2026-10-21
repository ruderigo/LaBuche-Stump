#!/usr/bin/env python3
"""Lets a PC join a chain of Stumps that has a chain secret (docs/CHAIN.md).

A Stump in a chain only talks Reticulum with whoever on its Wi-Fi has said
hello with the chain secret. Stumps do it themselves; a PC running
Reticulum (rnfs, Sideband, MeshChat, NomadNet...) runs this alongside it:

    python3 chain_hello.py 192.168.5.1 <chain secret>

where 192.168.5.1 is the Stump whose Wi-Fi the PC is on. Leave it running.
Then give the PC's Reticulum a plain UDP interface to that Stump, in
~/.reticulum/config:

    [[Stump chain]]
      type = UDPInterface
      enabled = yes
      listen_ip = 0.0.0.0
      listen_port = 4242
      forward_ip = 192.168.5.1
      forward_port = 4242

Python 3 standard library only; Windows, macOS, Linux.
"""
import hashlib, hmac, socket, sys, time

HELLO_MAGIC = b"STUMPCHAIN1"
EVERY_S = 20


def my_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 4242))
        return s.getsockname()[0]
    finally:
        s.close()


def tag(secret, my_ip, hub_ip):
    if not secret:
        return b""
    return hmac.new(secret.encode(), (my_ip + ">" + hub_ip).encode(), hashlib.sha256).digest()[:16]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    hub = args[0]
    secret = args[1] if len(args) > 1 else ""
    port = 4242
    bind = None
    for a in sys.argv[1:]:
        if a.startswith("--bind="):          # tests: send from this address
            bind = a.split("=", 1)[1]
    once = "--once" in sys.argv
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if bind:
        s.bind((bind, 0))
    s.settimeout(3)
    answered = None
    while True:
        me = bind or my_ip_towards(hub)
        s.sendto(HELLO_MAGIC + tag(secret, me, hub), (hub, port))
        try:
            data, _ = s.recvfrom(64)
            ok = data.startswith(HELLO_MAGIC)
        except socket.timeout:
            ok = False
        if ok != answered:
            print(time.strftime("%H:%M:%S"), ("in the chain at %s as %s" % (hub, me)) if ok
                  else "no answer from %s -- right Stump, right secret?" % hub, flush=True)
            answered = ok
        if once:
            sys.exit(0 if ok else 1)
        time.sleep(EVERY_S)


if __name__ == "__main__":
    main()
