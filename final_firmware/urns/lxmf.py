# µReticulum LXMF - Lightweight Extensible Message Format
# Wire-compatible with reference LXMF for MeshChat/Sideband interop
# Supports opportunistic (single-packet) and direct (link) message delivery

try:
    from keepalive import doing as _doing
except ImportError:
    def _doing(label):
        pass
from urns.mem import collect as _gc_collect
import time, sys
from . import umsgpack
from . import ztext
from .identity import Identity
from .destination import Destination
from .packet import Packet
from .transport import Transport
from .log import log, LOG_VERBOSE, LOG_DEBUG, LOG_ERROR, LOG_NOTICE, LOG_WARNING, sl
from .crypto.hashes import sha256

APP_NAME = "lxmf"

# Stamps and tickets (upstream LXMF: LXStamper.py, LXMessage.py,
# LXMRouter.py). A recipient can require a stamp on every message sent to
# it: proof of work over a 768 KB block made from the message's id
# ("cost" n: its SHA-256 must start with n zero bits), or, from a sender
# it gave a ticket to, sha256(ticket + message id)[:16], which costs one
# hash. Sideband requires stamps only if its user turns that on (cost
# 1-32), and gives a ticket to the contacts it marks trusted.
TICKET_LENGTH = 16
STAMP_SIZE = 32
WORKBLOCK_EXPAND_ROUNDS = 3000
TICKETS_MAX = 64            # tickets kept (one per contact)
COSTS_MAX = 128             # stamp costs kept (one per announced contact)
# Names learned from announces, and the ids of messages taken in (so one
# sent again isn't delivered twice): both grew with every new address
# and every message, checked or not, until the board had no memory left.
# The oldest go first. A board keeps fewer, like urns/const.py's tables.
PEERS_MAX = 160 if sys.platform == "esp32" else 768
DELIVERED_MAX = 256 if sys.platform == "esp32" else 4096
# Off unless the admin turns it on (/admin -> Stump settings -> Announcing,
# config.LXMF_STAMPS): off, no ticket is kept or used, no stamp made,
# exactly as before 1.0.0-beta.53.
STAMPS = [False]
ROUTER = [None]             # the node's router (for /admin's status line)

# Packed text (urns/ztext.py): a message's content written shorter, to and
# from those whose LXMF announce says they read it -- a fifth item after
# LXMF's own three and FireFly's app tag, {"z": 1}. Every other app gets,
# and sends, plain text as before. config.LXMF_PACKED_TEXT = False turns it
# off: nothing said in the announce, nothing packed, nothing unpacked.
PACKED_TEXT = [True]
PACKS_MAX = 128             # peers remembered as reading packed text


def reads_packed(app_data):
    """True when an LXMF announce says its sender reads packed text:
    [name, cost, functions, tag, {"z": version}]."""
    try:
        b0 = app_data[0]
        if (0x90 <= b0 <= 0x9f) or b0 == 0xdc:
            d = umsgpack.unpackb(app_data)
            if isinstance(d, list) and len(d) > 4 and isinstance(d[4], dict):
                z = d[4].get("z")
                return isinstance(z, int) and not isinstance(z, bool) and z >= 1
    except Exception:
        pass
    return False


def stamp_cost_from_app_data(app_data):
    """The stamp cost an LXMF announce asks for, or None (upstream
    LXMF.stamp_cost_from_app_data: [name, cost, ...] since LXMF 0.5)."""
    if not app_data:
        return None
    try:
        b0 = app_data[0]
        if (0x90 <= b0 <= 0x9f) or b0 == 0xdc:
            d = umsgpack.unpackb(app_data)
            if isinstance(d, list) and len(d) > 1:
                c = d[1]
                if isinstance(c, int) and not isinstance(c, bool) and 0 < c <= 254:
                    return c
    except Exception:
        pass
    return None


def _first_four(packed):
    """A packed payload's first four items as a four-item array, byte
    for byte as received."""
    b0 = packed[0]
    if 0x90 <= b0 <= 0x9f:
        start = 1
    elif b0 == 0xdc:
        start = 3
    else:
        raise ValueError("not an LXMF payload")
    end = start
    for _ in range(4):
        _, end = umsgpack._unpack(packed, end)
    return b"\x94" + bytes(packed[start:end])


def ticket_stamp(ticket, message_id):
    """The stamp a ticket gives a message: one hash, whatever the cost."""
    return sha256(bytes(ticket) + bytes(message_id))[:TICKET_LENGTH]


def _ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.monotonic() * 1000)


def _since(t0):
    try:
        return time.ticks_diff(time.ticks_ms(), t0)
    except AttributeError:
        return int(time.monotonic() * 1000) - t0


def _wall():
    """Unix time (tickets carry Unix expiry times)."""
    return time.time() + (946684800 if sys.platform == "esp32" else 0)


class StampJob:
    """Makes one proof-of-work stamp, a slice at a time (step()), so the
    node keeps answering while it works: the work block (3000 rounds of
    HKDF), then random stamps until one's hash is under the target."""

    def __init__(self, message_id, cost, rounds=WORKBLOCK_EXPAND_ROUNDS):
        from .crypto.hkdf import hkdf
        self._hkdf = hkdf
        self.mid = bytes(message_id)
        self.cost = cost
        self.rounds = rounds
        self.target = 1 << (256 - cost)
        self.wb = None
        self.n = 0
        self.tries = 0
        self.stamp = None

    def step(self, budget_ms=40):
        """Works for about budget_ms; True once the stamp is found."""
        import os
        try:
            import uhashlib as hl
        except ImportError:
            import hashlib as hl
        t0 = _ms()
        if self.wb is None:
            self.wb = bytearray(self.rounds * 256)
        while self.n < self.rounds:
            n = self.n
            self.wb[n * 256:(n + 1) * 256] = self._hkdf(
                length=256, derive_from=self.mid,
                salt=sha256(self.mid + umsgpack.packb(n)), context=None)
            self.n += 1
            if _since(t0) >= budget_ms:
                return False
        while True:
            s = os.urandom(STAMP_SIZE)
            h = hl.sha256()
            h.update(self.wb)           # fed in place: no 768 KB copy per try
            h.update(s)
            self.tries += 1
            if int.from_bytes(h.digest(), "big") <= self.target:
                self.stamp = s
                self.wb = None
                return True
            if _since(t0) >= budget_ms:
                return False

# Standard LXMF fields
FIELD_EMBEDDED_LXMS    = 0x01
FIELD_TELEMETRY        = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE  = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE            = 0x06
FIELD_AUDIO            = 0x07
FIELD_THREAD           = 0x08
FIELD_COMMANDS         = 0x09
FIELD_RESULTS          = 0x0A
FIELD_GROUP            = 0x0B
FIELD_TICKET           = 0x0C


class LXMessage:
    """LXMF message - wire-compatible with reference implementation"""

    # States
    GENERATING  = 0x00
    OUTBOUND    = 0x01
    SENDING     = 0x02
    SENT        = 0x04
    DELIVERED   = 0x08
    FAILED      = 0xFF

    # Delivery methods
    UNKNOWN       = 0x00
    OPPORTUNISTIC = 0x01
    DIRECT        = 0x02
    PROPAGATED    = 0x03

    # Representation
    PACKET   = 0x01
    RESOURCE = 0x02

    # Verification
    SOURCE_UNKNOWN    = 0x01
    SIGNATURE_INVALID = 0x02

    # Sizes
    DESTINATION_LENGTH = Identity.TRUNCATED_HASHLENGTH // 8   # 16
    SIGNATURE_LENGTH   = Identity.SIGLENGTH // 8              # 64
    TIMESTAMP_SIZE     = 8
    STRUCT_OVERHEAD    = 8
    LXMF_OVERHEAD      = 2 * DESTINATION_LENGTH + SIGNATURE_LENGTH + TIMESTAMP_SIZE + STRUCT_OVERHEAD  # 112

    # Max content that fits in a single encrypted packet (opportunistic)
    ENCRYPTED_PACKET_MAX_CONTENT = Packet.ENCRYPTED_MDU + TIMESTAMP_SIZE - LXMF_OVERHEAD + DESTINATION_LENGTH

    def __init__(self, destination=None, source=None, content=b"", title=b"",
                 fields=None, desired_method=None,
                 destination_hash=None, source_hash=None):

        self._destination = destination
        self._source = source
        self.destination_hash = destination_hash or (destination.hash if destination else None)
        self.source_hash = source_hash or (source.hash if source else None)

        if isinstance(title, str):
            title = title.encode("utf-8")
        if isinstance(content, str):
            content = content.encode("utf-8")

        self.title = title
        self.content = content
        self.fields = fields if fields is not None else {}
        self.stamp = None              # a proof-of-work stamp, made before pack()
        self.outbound_ticket = None    # the recipient's ticket: the stamp is one hash

        self.timestamp = None
        self.signature = None
        self.hash = None
        self.message_id = None
        self.packed = None
        self.state = LXMessage.GENERATING
        self.method = LXMessage.UNKNOWN
        self.desired_method = desired_method

        self.incoming = False
        self.signature_validated = False
        self.unverified_reason = None
        self.transport_encrypted = False
        self.transport_encryption = None

        self.rssi = None
        self.snr = None
        self.q = None

        self._delivery_callback = None
        self._failed_callback = None

    @property
    def destination(self):
        return self._destination

    @destination.setter
    def destination(self, d):
        if self._destination is None:
            self._destination = d
        else:
            raise ValueError("Cannot reassign destination")

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, s):
        if self._source is None:
            self._source = s
        else:
            raise ValueError("Cannot reassign source")

    def content_as_string(self):
        try:
            return self.content.decode("utf-8")
        except Exception:
            return None

    def title_as_string(self):
        try:
            return self.title.decode("utf-8")
        except Exception:
            return None

    def register_delivery_callback(self, callback):
        self._delivery_callback = callback

    def register_failed_callback(self, callback):
        self._failed_callback = callback

    def _hash_payload(self):
        """[timestamp, title, content, fields] and the bytes hashed for the
        message id, set once (a stamp is made from the id before pack())."""
        if getattr(self, "_hashed", None) is None:
            self._set_timestamp()
            payload = [self.timestamp, self.title, self.content, self.fields]
            hashed_part = self._destination.hash + self._source.hash + umsgpack.packb(payload)
            self.hash = sha256(hashed_part)
            self.message_id = self.hash
            self._hashed = (payload, hashed_part)
        return self._hashed

    def _set_timestamp(self):
        if self.timestamp is None:
            platform = sys.platform
            # https://docs.micropython.org/en/latest/library/time.html
            if platform == "esp32":
                # Micropython on ESP32 uses epoch time of 2000-01-01 so for Unix time need to add 946,684,800 seconds
                self.timestamp = 946684800 + time.time()
            else:
                self.timestamp = time.time()

    def pack(self):
        """Pack message into wire format"""
        if self.packed:
            raise ValueError("Message already packed")

        payload, hashed_part = self._hash_payload()

        # Sign: hash(dest + source + payload) + message_hash
        signed_part = hashed_part + self.hash
        self.signature = self._source.sign(signed_part)
        try:
            _gc_collect()
        except:
            pass
        self.signature_validated = True

        # The stamp goes after the four fields, outside what's hashed and
        # signed (upstream LXMessage.pack): from a ticket, or made already.
        stamp = None
        if self.outbound_ticket is not None:
            stamp = ticket_stamp(self.outbound_ticket, self.hash)
        elif self.stamp is not None:
            stamp = self.stamp
        if stamp is not None:
            payload = payload + [stamp]

        # Assemble packed message
        packed_payload = umsgpack.packb(payload)
        self.packed = b""
        self.packed += self._destination.hash
        self.packed += self._source.hash
        self.packed += self.signature
        self.packed += packed_payload

        # Determine delivery method
        content_size = len(packed_payload) - self.TIMESTAMP_SIZE - self.STRUCT_OVERHEAD

        if self.desired_method is None:
            self.desired_method = LXMessage.OPPORTUNISTIC

        if self.desired_method == LXMessage.OPPORTUNISTIC:
            if content_size > self.ENCRYPTED_PACKET_MAX_CONTENT:
                log("Message too large for opportunistic, using DIRECT", LOG_DEBUG)
                self.desired_method = LXMessage.DIRECT

        self.method = self.desired_method

    def send(self, attached_interface=None):
        """Send the message via the chosen method. attached_interface: an
        opportunistic message goes out on that interface only (the radio,
        for #lora-mesh between Stumps -- rrc_mesh)."""
        if not self.packed:
            self.pack()

        if self.method == LXMessage.OPPORTUNISTIC:
            # For opportunistic, we send to the destination directly
            # The packet data excludes the destination hash (inferred from packet header)
            data = self.packed[self.DESTINATION_LENGTH:]
            pkt = Packet(self._destination, data, attached_interface=attached_interface)
            pkt.send()
            self.state = LXMessage.SENT
            self.transport_encrypted = True
            self.transport_encryption = "Curve25519"
            log("Sent opportunistic LXMF message to " + self.destination_hash.hex()[:8], LOG_NOTICE)

            if self._delivery_callback:
                try:
                    self._delivery_callback(self)
                except Exception as e:
                    log("Delivery callback error: " + str(e), LOG_ERROR)

        elif self.method == LXMessage.DIRECT:
            # DIRECT delivery handled by LXMRouter._send_direct()
            pass
        else:
            log("Unsupported delivery method: " + str(self.method), LOG_ERROR)
            self.state = LXMessage.FAILED

    @staticmethod
    def unpack_from_bytes(lxmf_bytes):
        """Unpack LXMF message from wire format bytes"""
        DL = LXMessage.DESTINATION_LENGTH
        SL = LXMessage.SIGNATURE_LENGTH

        destination_hash = lxmf_bytes[:DL]
        source_hash = lxmf_bytes[DL:2 * DL]
        signature = lxmf_bytes[2 * DL:2 * DL + SL]
        packed_payload = lxmf_bytes[2 * DL + SL:]

        unpacked_payload = umsgpack.unpackb(packed_payload)
        # What a radio sends isn't always an LXMF payload: anything that
        # isn't [timestamp, title, content, fields, (stamp)] is refused here,
        # as a parsing error, rather than tripping later code (fuzzing found
        # AttributeErrors from a payload of the wrong shape).
        if (not isinstance(unpacked_payload, list) or len(unpacked_payload) < 4
                or not isinstance(unpacked_payload[0], (int, float))
                or not isinstance(unpacked_payload[1], (bytes, str))
                or not isinstance(unpacked_payload[2], (bytes, str))
                or not isinstance(unpacked_payload[3], (dict, type(None)))):
            raise ValueError("not an LXMF payload")

        # A stamp, if any, is a fifth item, outside what's hashed and
        # signed: the first four, as their sender wrote them. Taken from
        # the bytes received, not packed again -- a board's floats are 32
        # bits, so a timestamp read and written back is not the sender's
        # eight bytes, and every stamped message failed its signature
        # there (never on a computer, nor in the tests against upstream).
        if len(unpacked_payload) > 4:
            packed_payload = _first_four(packed_payload)
            unpacked_payload = unpacked_payload[:4]

        # Compute message hash
        hashed_part = destination_hash + source_hash + packed_payload
        message_hash = sha256(hashed_part)
        signed_part = hashed_part + message_hash

        timestamp = unpacked_payload[0]
        title_bytes = unpacked_payload[1]
        content_bytes = unpacked_payload[2]
        fields = unpacked_payload[3]

        # Try to find source identity for signature validation
        source_identity = Identity.recall(source_hash)

        # Build source destination if identity known
        source_dest = None
        if source_identity:
            source_dest = Destination(source_identity, Destination.OUT,
                                       Destination.SINGLE, APP_NAME, "delivery")

        # Build destination object if identity known
        dest_identity = Identity.recall(destination_hash)
        dest_obj = None
        if dest_identity:
            dest_obj = Destination(dest_identity, Destination.OUT,
                                    Destination.SINGLE, APP_NAME, "delivery")

        message = LXMessage(
            destination=dest_obj,
            source=source_dest,
            destination_hash=destination_hash,
            source_hash=source_hash,
        )

        message.hash = message_hash
        message.message_id = message_hash
        message.signature = signature
        message.incoming = True
        message.timestamp = timestamp
        message.title = title_bytes if isinstance(title_bytes, bytes) else title_bytes.encode("utf-8") if title_bytes else b""
        message.content = content_bytes if isinstance(content_bytes, bytes) else content_bytes.encode("utf-8") if content_bytes else b""
        message.fields = fields if isinstance(fields, dict) else {}
        message.packed = lxmf_bytes

        # Validate signature
        if source_identity:
            # Always checked, even without the native Ed25519 module (a
            # pure-Python verify costs ~2 s on the ESP32, rare on a LoRa
            # mesh). Skipping it used to mark the message VERIFIED, on the
            # theory that the encryption authenticates the sender -- it
            # doesn't: anyone can encrypt to this node and claim any
            # source hash. Only the signature proves who sent it.
            try:
                if source_identity.validate(signature, signed_part):
                    message.signature_validated = True
                else:
                    message.signature_validated = False
                    message.unverified_reason = LXMessage.SIGNATURE_INVALID
            except Exception as e:
                message.signature_validated = False
                message.unverified_reason = LXMessage.SIGNATURE_INVALID
                if sl(LOG_DEBUG):
                    log("Signature validation error: " + str(e), LOG_DEBUG)
        else:
            message.signature_validated = False
            message.unverified_reason = LXMessage.SOURCE_UNKNOWN

        # Bootstrap the clock from a trusted, signature-validated sender.
        # No-op unless time sync is enabled and the clock is still unset.
        if message.signature_validated:
            try:
                from .transport import Transport
                Transport.sync_clock_from(timestamp, source_hash)
            except Exception:
                pass

        return message

    def __str__(self):
        if self.hash:
            return "<LXMessage " + self.hash.hex()[:8] + ">"
        return "<LXMessage>"


class LXMRouter:
    """Simplified LXMF router for µReticulum
    
    Handles:
    - Registering delivery identity (makes node visible in MeshChat)
    - Receiving opportunistic LXMF messages
    - Sending opportunistic LXMF messages
    - Peer announce tracking
    """


    def __init__(self, identity=None, storagepath=None):
        self.identity = identity
        self.storagepath = storagepath

        self.delivery_destination = None
        self.delivery_identity = None
        self.display_name = None
        self.stamp_cost = None      # what this node's announce asks for (None: no stamps)

        self._delivery_callback = None
        self._announce_callback = None
        self._progress_callback = None

        self.peers = {}  # hash -> {name, timestamp}, at most PEERS_MAX
        self.delivered_ids = {}  # message_hash -> timestamp (dedup), at most DELIVERED_MAX
        self._cleaned_at = 0

        self.MESSAGE_EXPIRY = 30 * 24 * 60 * 60  # 30 days

        # Stamps (see the top of this file).
        self.stamp_costs = {}       # destination hash -> the cost its announce asks
        self.tickets = {}           # destination hash -> [expires, ticket] it gave us
        self.TICKETS_FILE = None    # where tickets are kept across restarts (the node sets it)
        # The highest cost this node makes stamps for by proof of work. A
        # CAM can't (the work block alone is ~90 s and 768 KB): 0, tickets
        # only. A computer: ~1 s at 8, ~13 s at 12, minutes at 16.
        self.stamp_max_cost = 0 if sys.platform == "esp32" else 20
        self.unstamped = {}         # destination hash -> cost: sent without the stamp it asks
        self._stamp_queue = []      # [message, destination, interface] waiting for a stamp
        self._stamp_job = None
        self.stamps_made = 0
        self.packs = {}             # destination hash -> 1: its announce says it reads packed text
        ROUTER[0] = self

    def register_delivery_identity(self, identity, display_name=None, stamp_cost=None):
        """Register identity for receiving LXMF messages.
        Creates the lxmf.delivery destination and sets up callbacks."""
        self.delivery_identity = identity
        self.display_name = display_name
        self.stamp_cost = stamp_cost

        self.delivery_destination = Destination(
            identity, Destination.IN, Destination.SINGLE,
            APP_NAME, "delivery"
        )

        # Set the packet callback for incoming opportunistic messages
        self.delivery_destination.set_packet_callback(self._delivery_packet)

        # Set link established callback for incoming link-based delivery (Resources)
        self.delivery_destination.set_link_established_callback(self._on_link_established)

        # Set announce handler so Transport forwards peer announces to us
        self.delivery_destination._announce_handler = self._announce_handler

        # Set app_data for announces
        if display_name is not None:
            # set_default_app_data so plain Destination.announce() calls (path
            # responses, post-clock-sync re-announces) also carry the LXMF
            # app_data — announce() falls back to default_app_data.
            self.delivery_destination.set_default_app_data(self._get_announce_app_data())

        log("LXMF delivery registered: " + self.delivery_destination.hexhash, LOG_NOTICE)
        return self.delivery_destination

    def register_delivery_callback(self, callback):
        """Register callback for incoming messages: callback(lxmessage)"""
        self._delivery_callback = callback

    def register_announce_callback(self, callback):
        """Register callback for peer announces: callback(destination_hash, display_name)"""
        self._announce_callback = callback

    def register_progress_callback(self, callback):
        """Register callback for incoming resource transfer progress:
        callback(resource) — reference RNS Resource progress_callback
        signature; read resource.get_progress() or received_count/
        total_parts on it."""
        self._progress_callback = callback

    def announce(self):
        """Announce our LXMF delivery destination"""
        if self.delivery_destination:
            app_data = self._get_announce_app_data()
            self.delivery_destination.announce(app_data=app_data)
            log("LXMF announced as: " + (self.display_name or "unnamed"), LOG_NOTICE)

    def _get_announce_app_data(self):
        """Build announce app_data: msgpack([display_name_bytes, stamp_cost,
        supported_functionality]) — matches upstream LXMF; the empty
        functionality list tells peers we don't support LXMF compression.
        Then, where packed text is on: no app tag (that's a Stump app's),
        and {"z": 1}. Upstream LXMF reads the first three items only."""
        dn = None
        if self.display_name:
            dn = self.display_name.encode("utf-8") if isinstance(self.display_name, str) else self.display_name
        data = [dn, self.stamp_cost, []]
        if PACKED_TEXT[0] and ztext.available():
            data += [None, {"z": 1}]
        return umsgpack.packb(data)

    def send_message(self, destination_hash, content, title="",
                     fields=None, desired_method=None, via=None):
        """Send an LXMF message to a destination hash.

        Resilient delivery chain:
          1. reuse a link we previously opened to the peer (DIRECT), else
          2. send opportunistically if a path is known (auto-upgrades to
             DIRECT via a fresh OutgoingLink when the payload is too big), else
          3. request a path and defer the send until the route is learned.

        Returns the LXMessage when sent/started, True when queued pending a path,
        or None on hard failure (reachable but identity still unknown).
        """
        _doing("sending an LXMF message")

        if via is not None:
            return self._send_via(destination_hash, content, title, fields, via)

        have_link = self._find_active_link_for(destination_hash) is not None

        # No route yet: solicit a path and defer the send. The retry re-enters
        # this method once the path (and the peer's identity, carried in the
        # path-response announce) is known.
        if not have_link and not Transport.has_path(destination_hash):
            if sl(LOG_VERBOSE):
                log("No path to " + destination_hash.hex()[:8] + ", requesting path", LOG_VERBOSE)
            Transport.ensure_path(
                destination_hash,
                on_found=lambda: self.send_message(
                    destination_hash, content, title, fields, desired_method),
                on_timeout=lambda: log(
                    "LXMF send to " + destination_hash.hex()[:8]
                    + " failed: no path", LOG_ERROR),
            )
            return True   # queued

        # Reachable — we need the peer's keys to encrypt.
        dest_identity = Identity.recall(destination_hash)
        if dest_identity is None:
            log("Cannot send LXMF: unknown identity for " + destination_hash.hex()[:8], LOG_ERROR)
            return None

        dest = Destination(dest_identity, Destination.OUT,
                           Destination.SINGLE, APP_NAME, "delivery")

        source = Destination(self.delivery_identity, Destination.OUT,
                             Destination.SINGLE, APP_NAME, "delivery")

        # Prefer an already-open link when the caller hasn't forced a method:
        # avoids a redundant path request when we can answer over the link.
        if have_link and desired_method is None:
            desired_method = LXMessage.DIRECT

        msg = LXMessage(
            destination=dest,
            source=source,
            content=self._packed(destination_hash, content),
            title=title,
            fields=fields,
            desired_method=desired_method or LXMessage.OPPORTUNISTIC,
        )

        if self._stamp_first(msg, destination_hash, dest, None):
            return msg
        self._dispatch(msg, dest, None)
        return msg

    def _dispatch(self, msg, dest, interface):
        """Packs and sends a message (its stamp, if any, already made)."""
        msg.pack()
        if interface is not None:
            if msg.method != LXMessage.OPPORTUNISTIC:
                log("LXMF message too big for one packet on " + str(interface), LOG_ERROR)
                return None
            msg.send(attached_interface=interface)
        elif msg.method == LXMessage.OPPORTUNISTIC:
            msg.send()
        elif msg.method == LXMessage.DIRECT:
            self._send_direct(msg, dest)
        return msg

    # ---- packed text (urns/ztext.py) ----

    def packs_for(self, destination_hash):
        """True when text to this destination goes packed."""
        return PACKED_TEXT[0] and bytes(destination_hash) in self.packs

    def packed_size(self, destination_hash, text):
        """The bytes `text` takes as a message's content to this destination."""
        data = text.encode("utf-8") if isinstance(text, str) else text
        return len(ztext.pack(data)) if self.packs_for(destination_hash) else len(data)

    def _packed(self, destination_hash, content):
        if not self.packs_for(destination_hash):
            return content
        data = content.encode("utf-8") if isinstance(content, str) else content
        out = ztext.pack(data)
        if len(out) < len(data) and sl(LOG_VERBOSE):
            log("LXMF text packed for " + bytes(destination_hash).hex()[:8] + ": " + str(len(data))
                + " -> " + str(len(out)) + "B", LOG_VERBOSE)
        return out

    def _unpack(self, message):
        """A packed content, read: the message then holds the text itself
        (its id and signature are those of what was sent, as they must be).
        One that can't be read stays as it came, as any binary content."""
        c = message.content
        if PACKED_TEXT[0] and c and c[0] >= ztext.PLAIN:
            text = ztext.unpack(c)
            if text is not None:
                message.content = text

    # ---- stamps and tickets ----

    def _ticket_for(self, destination_hash):
        t = self.tickets.get(destination_hash)
        if t is None:
            return None
        if _wall() >= t[0]:
            del self.tickets[destination_hash]
            return None
        return t[1]

    def _stamp_first(self, msg, destination_hash, dest, interface):
        """Prepares the stamp a recipient asks for. False: send now (no
        stamp needed, or a ticket's, made in pack()). True: queued, sent
        by stamp_loop() once its proof of work is done."""
        if not STAMPS[0]:
            return False
        ticket = self._ticket_for(destination_hash)
        if ticket is not None:
            msg.outbound_ticket = ticket
            return False
        cost = self.stamp_costs.get(destination_hash)
        if not cost:
            return False
        if cost > self.stamp_max_cost:
            # Sent anyway: an app that doesn't enforce stamps takes it.
            if self.unstamped.get(destination_hash) != cost:
                log("LXMF " + destination_hash.hex()[:8] + " asks for stamps (cost " + str(cost)
                    + "), too costly here: sent without one. It arrives if that app gives this node"
                    " a ticket (Sideband: mark it trusted)", LOG_WARNING)
            self.unstamped[destination_hash] = cost
            return False
        msg._hash_payload()
        self._stamp_queue.append([msg, dest, interface])
        log("LXMF " + destination_hash.hex()[:8] + " asks for stamps (cost " + str(cost)
            + "): making one", LOG_NOTICE)
        return True

    async def stamp_loop(self, budget_ms=40):
        """Makes queued stamps one at a time, in slices, then sends each
        message. Started by the node (example_node)."""
        try:
            import uasyncio as asyncio
        except ImportError:
            import asyncio
        while True:
            if not self._stamp_queue:
                await asyncio.sleep(1)
                continue
            msg, dest, iface = self._stamp_queue[0]
            try:
                cost = (self.stamp_costs.get(msg.destination_hash) or 0) if STAMPS[0] else 0
                ticket = self._ticket_for(msg.destination_hash) if STAMPS[0] else None
                if ticket is not None or not cost:
                    # A ticket arrived meanwhile, or it stopped asking.
                    msg.outbound_ticket = ticket
                else:
                    if self._stamp_job is None or self._stamp_job.mid != msg.message_id:
                        self._stamp_job = StampJob(msg.message_id, cost)
                    if not self._stamp_job.step(budget_ms):
                        await asyncio.sleep(0)
                        continue
                    msg.stamp = self._stamp_job.stamp
                    self.stamps_made += 1
                    log("LXMF stamp made for " + msg.destination_hash.hex()[:8] + " (cost "
                        + str(cost) + ", " + str(self._stamp_job.tries) + " tries)", LOG_NOTICE)
                self._stamp_job = None
                self._stamp_queue.pop(0)
                self._dispatch(msg, dest, iface)
            except Exception as e:
                log("LXMF stamp error: " + str(e), LOG_ERROR)
                self._stamp_job = None
                if self._stamp_queue and self._stamp_queue[0][0] is msg:
                    self._stamp_queue.pop(0)
            await asyncio.sleep(0)

    def _take_ticket(self, message):
        """Keeps the ticket a signed message carries (FIELD_TICKET:
        [expires, 16 bytes]): our replies to its sender then cost a hash."""
        try:
            if not STAMPS[0] or not message.signature_validated:
                return
            entry = (message.fields or {}).get(FIELD_TICKET)
            if not (isinstance(entry, list) and len(entry) > 1):
                return
            expires, ticket = entry[0], entry[1]
            if not isinstance(expires, (int, float)) or _wall() >= expires:
                return
            if not isinstance(ticket, bytes) or len(ticket) != TICKET_LENGTH:
                return
            src = message.source_hash
            old = self.tickets.get(src)
            if old is not None and old[1] == ticket:
                return
            if old is None and len(self.tickets) >= TICKETS_MAX:
                self.tickets.pop(min(self.tickets, key=lambda k: self.tickets[k][0]))
            self.tickets[src] = [expires, ticket]
            self.unstamped.pop(src, None)
            log("LXMF ticket from " + src.hex()[:8] + ": replies to it carry its stamp", LOG_NOTICE)
            self._save_tickets()
        except Exception as e:
            log("LXMF ticket error: " + str(e), LOG_ERROR)

    def _save_tickets(self):
        if not self.TICKETS_FILE:
            return
        try:
            tmp = self.TICKETS_FILE + ".tmp"
            with open(tmp, "wb") as f:
                f.write(umsgpack.packb(self.tickets))
            import os
            os.rename(tmp, self.TICKETS_FILE)
        except Exception as e:
            log("LXMF could not save tickets: " + str(e), LOG_ERROR)

    def load_tickets(self, path):
        """Where tickets are kept; loads those still valid."""
        self.TICKETS_FILE = path
        try:
            with open(path, "rb") as f:
                d = umsgpack.unpackb(f.read())
        except Exception:
            return
        now = _wall()
        if isinstance(d, dict):
            for k, v in d.items():
                if (isinstance(k, bytes) and isinstance(v, list) and len(v) == 2
                        and isinstance(v[0], (int, float)) and v[0] > now
                        and isinstance(v[1], bytes) and len(v[1]) == TICKET_LENGTH):
                    self.tickets[k] = v

    def _send_via(self, destination_hash, content, title, fields, interface):
        """One packet on one interface (the radio), whatever links or paths
        exist elsewhere: an open link over the Wi-Fi chain would otherwise
        carry it, and the radio would never transmit. Only what fits in a
        single packet (the caller cuts it up); returns the LXMessage, or
        None (too big, or the peer's keys not known yet)."""
        dest_identity = Identity.recall(destination_hash)
        if dest_identity is None:
            log("Cannot send LXMF on " + str(interface) + ": unknown identity for "
                + destination_hash.hex()[:8], LOG_ERROR)
            return None
        dest = Destination(dest_identity, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")
        source = Destination(self.delivery_identity, Destination.OUT, Destination.SINGLE, APP_NAME, "delivery")
        msg = LXMessage(destination=dest, source=source, content=self._packed(destination_hash, content),
                        title=title, fields=fields, desired_method=LXMessage.OPPORTUNISTIC)
        if self._stamp_first(msg, destination_hash, dest, interface):
            return msg
        return self._dispatch(msg, dest, interface)

    def _find_active_link_for(self, destination_hash):
        """Return an ACTIVE OutgoingLink we previously opened to this LXMF
        peer, or None. Only initiator links qualify: reference LXMF wires
        delivery callbacks on links it ACCEPTS at its delivery destination,
        not on links it OPENED — a message sent back over the peer's own
        link transfers and proves at the Resource layer but is silently
        discarded by the peer's app (observed with MeshChat image replies)."""
        from .link import OutgoingLink
        for link in Transport.active_links:
            if link.status != 0x01:  # ACTIVE
                continue
            if isinstance(link, OutgoingLink) and link.destination.hash == destination_hash:
                return link
        return None

    def _send_direct(self, message, destination):
        """Send LXMF message via DIRECT link delivery (link + Resource).

        Reuses an OutgoingLink we already hold to the peer, else opens a
        fresh one (path availability is guaranteed by the has_path/
        ensure_path gate in send_message). Never sends over a peer-initiated
        link — see _find_active_link_for.
        """
        from .link import OutgoingLink
        from . import const

        def deliver_on(link, owns_link):
            packed = message.packed
            # Single link packet capacity: ~415B after Token encryption
            if len(packed) <= 415:
                link.send(packed, const.CTX_NONE)
                message.state = LXMessage.SENT
                if sl(LOG_VERBOSE):
                    log("LXMF DIRECT sent as packet: " + str(len(packed)) + "B", LOG_VERBOSE)
                if owns_link:
                    link.teardown()
            else:
                from .resource import Resource
                # Chain with any existing resource-concluded callback (e.g. the
                # router's incoming-delivery handler on a peer-initiated link)
                # so future incoming resources still fire their handler.
                previous_cb = link.resource_concluded_callback
                def on_resource_done(r):
                    if getattr(r, "is_initiator", False):
                        self._direct_resource_concluded(r, message, link, owns_link)
                    elif previous_cb is not None:
                        previous_cb(r)
                link.resource_concluded_callback = on_resource_done
                Resource(link, packed, is_response=False)
                if sl(LOG_VERBOSE):
                    log("LXMF DIRECT sending as resource: " + str(len(packed)) + "B", LOG_VERBOSE)

        existing = self._find_active_link_for(message.destination_hash)
        if existing is not None:
            log("LXMF DIRECT reusing active link " + existing.link_id.hex()[:8] +
                " to " + message.destination_hash.hex()[:8], LOG_NOTICE)
            deliver_on(existing, owns_link=False)
            return

        def on_established(link):
            deliver_on(link, owns_link=True)

        def on_closed(link):
            if message.state < LXMessage.SENT:
                if self._retry_direct(message, destination):
                    return
                message.state = LXMessage.FAILED
                log("LXMF DIRECT link closed before delivery", LOG_ERROR)

        OutgoingLink(destination, established_callback=on_established, closed_callback=on_closed)
        log("LXMF DIRECT delivery initiated to " + message.destination_hash.hex()[:8], LOG_NOTICE)

    def _retry_direct(self, message, destination):
        """Bounded re-attempt of a DIRECT delivery whose link died before the
        message got through, or whose resource transfer failed. On a congested
        half-duplex hop the establishment packets (LR, proof, LRRTT) can be
        lost to the relay's own transmissions; reference LXMF retries failed
        outbounds, and without this a single lost packet permanently fails the
        delivery. Counts attempts on the message: 3 total."""
        attempts = getattr(message, "direct_attempts", 0) + 1
        if attempts >= 3:
            return False
        message.direct_attempts = attempts
        log("LXMF DIRECT retry " + str(attempts + 1) + "/3 to "
            + message.destination_hash.hex()[:8], LOG_NOTICE)
        try:
            message.state = LXMessage.SENDING
            self._send_direct(message, destination)
            return True
        except Exception as e:
            log("LXMF DIRECT retry error: " + str(e), LOG_ERROR)
            return False

    def _direct_resource_concluded(self, resource, message, link, owns_link=True):
        """Called when outgoing DIRECT Resource transfer completes."""
        from .resource import COMPLETE
        if resource.status == COMPLETE:
            message.state = LXMessage.DELIVERED
            log("LXMF DIRECT delivered: " + message.destination_hash.hex()[:8], LOG_NOTICE)
        else:
            # Retry on a FRESH link: an unanswered advertisement usually means
            # the peer's side of this link never became established. Mark the
            # message FAILED before teardown so the link's closed-callback
            # doesn't also schedule a retry, and grab the destination before
            # _close() nulls it; _retry_direct resets the state on re-send.
            destination = link.destination
            message.state = LXMessage.FAILED
            if owns_link:
                link.teardown()
            if destination is not None and self._retry_direct(message, destination):
                return
            log("LXMF DIRECT resource failed", LOG_ERROR)
            return
        # Only teardown links we opened ourselves — if we reused a peer-
        # initiated link, tearing it down would close their channel.
        if owns_link:
            link.teardown()

    def _on_link_established(self, link):
        """Called when a link is established to our delivery destination."""
        link.set_packet_callback(self._link_packet_received)
        link.resource_concluded_callback = self._handle_resource_concluded
        if self._progress_callback:
            link.resource_started_callback = self._on_resource_started
        if sl(LOG_DEBUG):
            log("LXMF link established: " + link.link_id.hex()[:8], LOG_DEBUG)

    def _on_resource_started(self, resource):
        """Wire the router's progress callback into a just-accepted resource."""
        resource.progress_callback = self._progress_callback

    def _link_packet_received(self, plaintext, packet):
        """Handle single-packet LXMF message received on a link (CTX_NONE)."""
        try:
            # DIRECT delivery sends full packed: dest_hash + source_hash + sig + payload
            # (unlike OPPORTUNISTIC which strips dest_hash)
            if sl(LOG_DEBUG):
                log("LXMF link packet: " + str(len(plaintext)) + "B", LOG_DEBUG)
            message = LXMessage.unpack_from_bytes(plaintext)
            message.receiving_interface = getattr(packet, "receiving_interface", None)   # which link it came in on (rrc_mesh: #mesh)
            self._take_in(message, "link ")
        except Exception as e:
            log("LXMF link packet error: " + str(e), LOG_ERROR)

    def _handle_resource_concluded(self, resource):
        """Called when a Resource transfer completes on a link."""
        from .resource import COMPLETE
        if resource.status != COMPLETE:
            log("LXMF resource not complete, ignoring", LOG_DEBUG)
            return

        try:
            data = resource.data
            if data is None or len(data) < 2 * LXMessage.DESTINATION_LENGTH + LXMessage.SIGNATURE_LENGTH:
                log("LXMF resource data too short", LOG_DEBUG)
                return

            # Check if this is a response (request_id set) — not LXMF delivery
            if resource.request_id is not None:
                log("LXMF resource is a response, not a delivery", LOG_DEBUG)
                return

            # DIRECT delivery sends full packed: dest_hash + source_hash + sig + payload
            if sl(LOG_DEBUG):
                log("LXMF resource unpacking: " + str(len(data)) + "B", LOG_DEBUG)
            message = LXMessage.unpack_from_bytes(data)
            message.receiving_interface = getattr(getattr(resource, "link", None), "attached_interface", None)   # which link it came in on (rrc_mesh: #mesh)
            self._take_in(message, "resource ")
        except Exception as e:
            log("LXMF resource delivery error: " + str(e), LOG_ERROR)

    def _delivery_packet(self, data, packet):
        """Handle incoming opportunistic LXMF packet"""
        try:
            if sl(LOG_DEBUG):
                log("LXMF delivery_packet: " + str(len(data)) + "B", LOG_DEBUG)
            # For opportunistic delivery, prepend destination hash
            # (it's inferred from the packet destination, not included in payload)
            message = LXMessage.unpack_from_bytes(self.delivery_destination.hash + data)
            message.receiving_interface = getattr(packet, "receiving_interface", None)   # which link it came in on (rrc_mesh: #mesh)
            self._take_in(message, "", packet)
        except Exception as e:
            log("LXMF delivery packet error: " + str(e), LOG_ERROR)

    def _take_in(self, message, how, packet=None):
        """A message just unpacked, however it came (`how`: "" for a packet
        of its own, "link " or "resource " over a link): dropped when its
        signature is wrong, delivered once. `packet`: the opportunistic
        packet to prove (a link proves its own packets and resources)."""
        message.transport_encrypted = True
        message.transport_encryption = "Curve25519"
        if message.unverified_reason != LXMessage.SIGNATURE_INVALID:
            self._unpack(message)

        if message.signature_validated:
            log("LXMF " + how + "message from " + message.source_hash.hex()[:8] +
                ": " + (message.content_as_string() or "(binary)"), LOG_NOTICE)
        else:
            reason = "unknown source" if message.unverified_reason == LXMessage.SOURCE_UNKNOWN else "invalid signature"
            log("LXMF unverified " + how + "message (" + reason + ") from " +
                message.source_hash.hex()[:8], LOG_NOTICE)
            if message.unverified_reason == LXMessage.SIGNATURE_INVALID:
                # Forged or corrupted: someone else's address on it.
                log("LXMF message dropped: its signature doesn't match its sender", LOG_WARNING)
                return

        if message.hash in self.delivered_ids:
            if packet is None:
                if sl(LOG_DEBUG):
                    log("LXMF duplicate ignored: " + message.hash.hex()[:8], LOG_DEBUG)
                return
            # A copy of a packet already taken in is the sender trying
            # again because our proof didn't reach it: prove it again
            # (else it keeps resending, then shows "not sent"), but
            # don't deliver it twice.
            log("LXMF duplicate from " + message.source_hash.hex()[:8] + ": proved again, not delivered twice", LOG_NOTICE)
            try:
                packet.prove()
            except Exception:
                pass
            return
        self._remember(message.hash)

        if packet is not None:
            # Send delivery proof (so sender knows we received it)
            try:
                _gc_collect()
            except:
                pass
            packet.prove()

        self._take_ticket(message)
        if self._delivery_callback:
            try:
                self._delivery_callback(message)
            except Exception as e:
                log("LXMF delivery callback error: " + str(e), LOG_ERROR)

    def _announce_handler(self, destination_hash, app_data, packet):
        """Called by Transport when any announce is received"""
        self.handle_announce(destination_hash, app_data)

    def handle_announce(self, destination_hash, app_data):
        """Process an LXMF delivery announce from a peer.
        Call this from your announce handler."""
        display_name = None
        if app_data:
            display_name = self._parse_display_name(app_data)

        if destination_hash not in self.peers and len(self.peers) >= PEERS_MAX:
            self.peers.pop(min(self.peers, key=lambda k: self.peers[k]["timestamp"]))
        self.peers[destination_hash] = {
            "name": display_name,
            "timestamp": time.time(),
        }
        if app_data and reads_packed(app_data):
            if destination_hash not in self.packs and len(self.packs) >= PACKS_MAX:
                self.packs.pop(next(iter(self.packs)))
            self.packs[bytes(destination_hash)] = 1
        else:
            self.packs.pop(destination_hash, None)
        cost = stamp_cost_from_app_data(app_data)
        if cost:
            if destination_hash not in self.stamp_costs and len(self.stamp_costs) >= COSTS_MAX:
                self.stamp_costs.pop(next(iter(self.stamp_costs)))
            self.stamp_costs[destination_hash] = cost
        else:
            self.stamp_costs.pop(destination_hash, None)
            self.unstamped.pop(destination_hash, None)

        log("LXMF peer: " + (display_name or "?") +
            " [" + destination_hash.hex()[:8] + "]", LOG_VERBOSE)

        if self._announce_callback:
            try:
                self._announce_callback(destination_hash, display_name)
            except Exception as e:
                log("Announce callback error: " + str(e), LOG_ERROR)

    @staticmethod
    def _parse_display_name(app_data):
        """Parse display name from LXMF announce app_data"""
        try:
            if not app_data or len(app_data) == 0:
                return None

            # Version 0.5.0+ format: msgpack [name_bytes, stamp_cost]
            first = app_data[0]
            if (0x90 <= first <= 0x9f) or first == 0xdc:
                peer_data = umsgpack.unpackb(app_data)
                if isinstance(peer_data, list) and len(peer_data) >= 1:
                    dn = peer_data[0]
                    if dn is None or dn is False:
                        return None
                    if isinstance(dn, bytes):
                        return dn.decode("utf-8")
                    return str(dn)

            # Legacy format: raw string
            return app_data.decode("utf-8")

        except Exception:
            return None

    def _remember(self, message_hash):
        """Notes a message as taken in. Expired ids are cleared at most
        once a minute (it was a pass over all of them at every message);
        at DELIVERED_MAX the oldest goes."""
        now = time.time()
        ids = self.delivered_ids
        if now - self._cleaned_at > 60:
            self._cleaned_at = now
            for h in [h for h, ts in ids.items() if now - ts > self.MESSAGE_EXPIRY]:
                del ids[h]
        if len(ids) >= DELIVERED_MAX:
            ids.pop(min(ids, key=lambda k: ids[k]))
        ids[message_hash] = now

    @staticmethod
    def display_name_from_app_data(app_data):
        """Public helper to parse display name from app_data"""
        return LXMRouter._parse_display_name(app_data)
