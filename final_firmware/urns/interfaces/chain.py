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
PEER_TTL_S = 75       # a child whose last good hello is older than this is dropped
REFUSED_KEEP_S = 600  # refused hellos are remembered this long (for /chain.json)
REFUSED_MAX = 16


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


def _same(a, b):
    """Tags compared in a time that doesn't tell how much was right."""
    a, b = bytes(a), bytes(b)
    if len(a) != len(b):
        return False
    r = 0
    for i in range(len(a)):
        r |= a[i] ^ b[i]
    return r == 0


def _ack_tag(secret, child_ip, hub_ip):
    """The Stump above's answer proves it knows the secret too (since
    1.0.0-beta.37): the answer to a hello used to be plain, so anything
    answering from the parent's address looked like the parent."""
    if not secret:
        return b""
    from ..crypto.hmac import new as hmac_new
    return hmac_new(secret, (hub_ip + "<" + child_ip).encode()).digest()[:16]


class ChainUplinkInterface(UDPInterface):
    """Unicast to the parent Stump (its access point address)."""

    def __init__(self, config):
        secret = config.get("secret") or ""
        self._secret = secret.encode() if isinstance(secret, str) else secret
        config.setdefault("name", "Chain uplink")
        config.setdefault("bitrate", 2000000)
        super().__init__(config)
        self.parent_seen = 0       # last time the parent answered a hello
        # The parent's answer proved the chain secret (None: no answer yet,
        # or no secret; False: a plain answer, an older Stump above).
        self.parent_verified = None

    def accept(self, data, addr):
        # Only the parent, whose address we send to, talks on this link.
        if _ip_of(addr) != self.forward_ip:
            return False
        if data[:len(HELLO_MAGIC)] == HELLO_MAGIC:
            tail = bytes(data[len(HELLO_MAGIC):])
            if self._secret:
                want = b"ACK" + _ack_tag(self._secret, self.listen_ip, self.forward_ip)
                if _same(tail, want):
                    self.parent_verified = True
                elif tail == b"ACK" and self.parent_verified is not True:
                    self.parent_verified = False     # an older Stump above: taken, shown
                else:
                    log("Chain: an answer from " + self.forward_ip + " with another secret: ignored", LOG_WARNING)
                    return False
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
        self.peers = {}            # ip -> [addr, last good hello (or first packet, open chain)]
        self.refused = {}          # ip -> [refused hellos, last time] (a wrong chain secret)

    @property
    def no_peers(self):
        """Nobody below this Stump: Transport skips it quietly (sending to
        no one isn't a failure -- it used to log "TX failed" on every
        announce of a Stump at the end of a chain)."""
        return not self.peers

    def _refuse(self, ip):
        r = self.refused.get(ip)
        now = time.time()
        if r is None:
            log("Chain hello from " + ip + " refused (wrong chain secret)", LOG_WARNING)
            if len(self.refused) >= REFUSED_MAX:
                del self.refused[min(self.refused, key=lambda k: self.refused[k][1])]
            self.refused[ip] = [1, now]
        else:
            r[0] += 1
            r[1] = now
        # A Stump that was in, now saying hello with another secret: out at once.
        if ip in self.peers:
            log("Chain: " + ip + " below this one now has another secret: dropped", LOG_NOTICE)
            del self.peers[ip]

    def refusals(self):
        """[{"ip", "count", "last_s"}] in the last REFUSED_KEEP_S, newest first."""
        now = time.time()
        for ip in [k for k, v in self.refused.items() if now - v[1] > REFUSED_KEEP_S]:
            del self.refused[ip]
        return [{"ip": ip, "count": v[0], "last_s": int(now - v[1])}
                for ip, v in sorted(self.refused.items(), key=lambda kv: -kv[1][1])]

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
            if not _same(tag, _hello_tag(self._secret, ip, self.listen_ip)):
                self._refuse(ip)
                return False
            self.refused.pop(ip, None)
            self._learn(ip)
            try:
                # The answer goes back where the hello came from (a PC's
                # hello script uses its own port; Reticulum is on the chain port).
                self._socket.sendto(HELLO_MAGIC + b"ACK" + _ack_tag(self._secret, ip, self.listen_ip), addr)
                self._socket.settimeout(0)
            except Exception:
                pass
            return False           # a hello isn't a Reticulum packet
        peer = self.peers.get(ip)
        if peer is not None:
            # With a secret, only a good hello keeps a peer in: a packet
            # doesn't (an address taken over after a Stump left, or a Stump
            # whose secret changed, would otherwise stay in as long as it
            # kept sending). It's dropped when its hellos stop (PEER_TTL_S).
            if self._secret and time.time() - peer[1] > PEER_TTL_S:
                return False
            if not self._secret:
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
