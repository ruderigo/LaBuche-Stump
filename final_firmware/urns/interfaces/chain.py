# µReticulum chain interfaces: Stumps linked to each other over Wi-Fi.
#
# A chain is a tree of Stumps: each one's Wi-Fi station joins the access
# point of the Stump before it (its "uplink"), and serves its own access
# point to the next ones (its "downlinks"), on its own subnet. Over those
# links they speak Reticulum in UDP, so announces, messages and file
# transfers cross the chain without touching LoRa. (See docs/CHAIN.md.)
#
#   ChainUplinkInterface   on the station side: unicast to the parent
#                          Stump's access point address, and a hello every
#                          HELLO_S seconds so the parent knows it's there.
#   ChainHubInterface      on the access-point side: learns its children
#                          from their hellos (Stumps) or their first packet
#                          (a PC running Reticulum), and sends each packet to
#                          every one heard recently (one Reticulum segment).
#
# With a chain secret (config CHAIN_SECRET), hellos carry an HMAC: only a
# Stump (or a PC running scripts/chain_hello.py) that knows the secret is
# taken in, and nothing is sent to -- or accepted from -- anyone else on
# the Wi-Fi. Reticulum packets are already signed and encrypted end to
# end; what the secret adds is who may sit on the chain. (Reticulum's own
# per-packet access codes, IFAC, would also do it, but cost an Ed25519
# signature, an HKDF and a byte-by-byte unmask per packet per hop: tens
# of milliseconds on a CAM, a hundredfold slowdown.)

import socket
import time
from .udp import UDPInterface, _create_socket, _resolve_addr
from ..log import log, LOG_NOTICE, LOG_WARNING, LOG_DEBUG

HELLO_MAGIC = b"STUMPCHAIN1"
HELLO_S = 20          # a child says hello this often
PEER_TTL_S = 75       # a child not heard from for this long is dropped


def _ip_of(addr):
    """The sender's address as text: a tuple on the ESP32, a raw sockaddr
    (family, port, 4 address bytes) on the Unix port the tests run on."""
    if isinstance(addr, tuple):
        return addr[0]
    try:
        return "%d.%d.%d.%d" % tuple(addr[4:8])
    except Exception:
        return None


def _link_event():
    try:
        import chain
        chain.link_event()
    except Exception:
        pass


def _hello_tag(secret, child_ip, hub_ip):
    if not secret:
        return b""
    from ..crypto.hmac import new as hmac_new
    return hmac_new(secret, (child_ip + ">" + hub_ip).encode()).digest()[:16]


class ChainUplinkInterface(UDPInterface):
    """Unicast to the parent Stump (its access point address)."""

    def __init__(self, config):
        secret = config.get("secret") or ""
        self._secret = secret.encode() if isinstance(secret, str) else secret
        config.setdefault("name", "Chain uplink")
        config.setdefault("bitrate", 2000000)
        super().__init__(config)
        self.parent_seen = 0       # last time the parent answered a hello

    def accept(self, data, addr):
        # Only the parent, whose address we send to, talks on this link.
        if _ip_of(addr) != self.forward_ip:
            return False
        if data[:len(HELLO_MAGIC)] == HELLO_MAGIC:
            if not self.parent_seen:
                _link_event()                  # first answer: introduce ourselves
            self.parent_seen = time.time()     # the parent's answer to a hello
            return False
        return True

    def hello(self):
        if not self._socket:
            return
        try:
            tag = _hello_tag(self._secret, self.listen_ip, self.forward_ip)
            self._socket.sendto(HELLO_MAGIC + tag, self._forward_addr)
            self._socket.settimeout(0)
        except Exception as e:
            log("Chain hello failed: " + str(e), LOG_DEBUG)

    async def poll_loop(self):
        import uasyncio as asyncio

        async def hellos():
            while self.online:
                self.hello()
                await asyncio.sleep(HELLO_S)

        asyncio.create_task(hellos())
        await super().poll_loop()


class ChainHubInterface(UDPInterface):
    """The access-point side: every child Stump heard recently."""

    def __init__(self, config):
        secret = config.get("secret") or ""
        self._secret = secret.encode() if isinstance(secret, str) else secret
        config.setdefault("name", "Chain hub")
        config.setdefault("bitrate", 2000000)
        # Bound to this Stump's own access-point address; forward_ip is
        # unused (each child is sent to directly).
        config.setdefault("forward_ip", config.get("listen_ip", "0.0.0.0"))
        super().__init__(config)
        self.peers = {}            # ip -> [addr, last_seen]

    def _learn(self, ip):
        """A peer is addressed at its IP on the chain port (whatever port it
        sent from), so a PC running Reticulum with a plain UDPInterface --
        listen and send on the chain port -- is reachable the same way as a
        child Stump."""
        if ip not in self.peers:
            log("Chain: " + ip + " joined below this one", LOG_NOTICE)
            _link_event()
            self.peers[ip] = [_resolve_addr(ip, self.listen_port), time.time()]
        else:
            self.peers[ip][1] = time.time()
        return self.peers[ip]

    def accept(self, data, addr):
        ip = _ip_of(addr)
        if data[:len(HELLO_MAGIC)] == HELLO_MAGIC:
            if ip is None:
                return False
            tag = data[len(HELLO_MAGIC):]
            if tag != _hello_tag(self._secret, ip, self.listen_ip):
                log("Chain hello from " + ip + " refused (wrong chain secret)", LOG_WARNING)
                return False
            self._learn(ip)
            try:
                # The answer goes back where the hello came from (a PC's
                # hello script uses its own port; Reticulum is on the chain port).
                self._socket.sendto(HELLO_MAGIC + b"ACK", addr)
                self._socket.settimeout(0)
            except Exception:
                pass
            return False           # a hello isn't a Reticulum packet
        peer = self.peers.get(ip)
        if peer is not None:
            peer[1] = time.time()
            return True
        if ip is None:
            return False
        # Someone new sending Reticulum straight away, with no hello (a PC
        # with a plain UDPInterface): welcome in an open chain only. With
        # a chain secret, a PC runs scripts/chain_hello.py.
        if self._secret:
            return False
        self._learn(ip)
        return True

    def process_outgoing(self, data):
        if not self.online or not self._socket or not self.peers:
            return False
        now = time.time()
        sent = False
        for ip in list(self.peers):
            addr, seen = self.peers[ip]
            if now - seen > PEER_TTL_S:
                log("Chain: " + ip + " below this one went quiet", LOG_NOTICE)
                del self.peers[ip]
                continue
            try:
                self._socket.sendto(data, addr)
                self._socket.settimeout(0)
                sent = True
            except Exception as e:
                log("Chain send to " + ip + " failed: " + str(e), LOG_DEBUG)
        if sent:
            self.txb += len(data)
            self.tx += 1
            self._last_activity = now
            if self._first_tx_time == 0:
                self._first_tx_time = now
        return sent
