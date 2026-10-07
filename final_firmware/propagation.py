"""
LXMF propagation node: store-and-forward for offline messages.

When enabled, this node announces itself as an LXMF 1.2 propagation node
(aspect lxmf.propagation, on the node's own identity). Phones (FireFly,
Sideband) can pick it as their propagation node: they upload messages for
recipients who are offline, and recipients collect them later with a sync.
The node can't read them -- they're encrypted for the recipient.

Protocol: upstream LXMF 1.2.0 (LXMRouter.propagation_packet /
propagation_resource_concluded / message_get_request, LXStamper.
validate_pn_stamp), matched field for field so upstream clients accept it.

One deliberate difference from upstream. Every stored message carries a
proof-of-work stamp the node must check: a 256 KB work block built in
1000 rounds, then one hash. Measured on the CAM: ~30 s per message. The
CAM runs everything in one loop, so upstream's "check, then confirm
receipt" would freeze the web pages, chat and Heltec bridge for half a
minute per upload -- and a phone waits for a confirmation far less than
that, so it would resend. Here receipt is confirmed at once, the upload is
parked on the SD card, and a background task checks stamps a couple of
rounds at a time (~60 ms per step). A message is stored -- and offered to
its recipient -- only once its stamp checks out; a bad stamp is dropped.

Phase A scope: clients only. Peering with other propagation nodes is
Phase B; until then /offer answers ERROR_NO_ACCESS, which makes an
upstream node that tries to auto-peer back off cleanly instead of retrying.
"""

import os
import sys
import time
import ujson as json
try:
    import hashlib
except ImportError:
    import uhashlib as hashlib

from urns import umsgpack
from urns.identity import Identity
from urns.destination import Destination
from urns.crypto.hkdf import hkdf

# ---- protocol constants (upstream LXMF 1.2.0) ----
STAMP_SIZE = 32                    # LXStamper.STAMP_SIZE
DEST_LEN = 16                      # LXMessage.DESTINATION_LENGTH
LXMF_OVERHEAD = 112                # LXMessage.LXMF_OVERHEAD
WORKBLOCK_ROUNDS = 1000            # LXStamper.WORKBLOCK_EXPAND_ROUNDS_PN
PEERING_COST = 18                  # LXMRouter.PEERING_COST (announced; peering is Phase B)
MESSAGE_GET_PATH = "/get"          # LXMPeer.MESSAGE_GET_PATH
OFFER_REQUEST_PATH = "/offer"      # LXMPeer.OFFER_REQUEST_PATH
ERROR_NO_IDENTITY = 0xf0
ERROR_NO_ACCESS = 0xf1
PN_META_VERSION, PN_META_NAME, PN_META_IMPL_NAME = 0x00, 0x01, 0xFE

# ---- this node's limits ----
# The CAM's stack moves at most 16 KB per transfer (urns MAX_RESOURCE_SIZE),
# so that's the advertised per-transfer limit, with room for packing.
TRANSFER_LIMIT_KB = 15
SYNC_LIMIT_KB = 15
# One /get reply must fit one 16 KB transfer AND hold any single message the
# node accepts (up to TRANSFER_LIMIT_KB). It used to be 14,000 bytes, so a
# message between ~14 and 15 KB -- a 15 s Opus voice note, FireFly's default,
# is ~13.4-14.2 KB plus its envelope -- was stored but could never be
# collected: skipped on every sync until it expired.
RESPONSE_BYTES_MAX = 16000
MESSAGE_EXPIRY = 30 * 24 * 3600    # upstream MESSAGE_EXPIRY
MAX_PER_RECIPIENT = 100
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_PENDING = 50                   # uploads waiting for their stamp check
ROUNDS_PER_STEP = 2                # ~60 ms of work per step on the CAM

try:
    from config import PROPAGATION_STAMP_COST as STAMP_COST
except ImportError:
    STAMP_COST = 16                # upstream PROPAGATION_COST
STAMP_FLEX = 3                     # upstream PROPAGATION_COST_FLEX

STORE_DIR = "/sd/lxmf_pn"
PENDING_DIR = STORE_DIR + "/pending"
SETTINGS_FILE = "/propagation.json"
MAILBOX_IDENTITY_FILE = "/pn_mailbox_identity"

# ESP32 MicroPython counts time from 2000; LXMF timebases are Unix time.
# Same correction urns applies when it stamps a message.
EPOCH_OFFSET = 946684800 if sys.platform == "esp32" else 0

_router = None
_dest = None
_node_name = "Stump"
_enabled = False
_entries = {}        # transient_id -> [recipient, path, received, size, local]
_pending = []        # paths in PENDING_DIR, oldest first
_workblock = None    # one 256 KB buffer, reused for every check
_mailbox = None      # test mailbox: [Destination(IN), identity]
_mailbox_inbox = []  # newest last: {"text", "from", "at"}
stats = {"received": 0, "valid": 0, "invalid": 0, "dropped": 0, "served": 0, "local": 0}


def unix_now():
    return int(time.time()) + EPOCH_OFFSET


# ---- settings (switch in /admin, default from config.py) ----

def configured_on():
    try:
        with open(SETTINGS_FILE) as f:
            return bool(json.load(f).get("enabled"))
    except (OSError, ValueError):
        pass
    try:
        from config import PROPAGATION_NODE
        return bool(PROPAGATION_NODE)
    except ImportError:
        return False


def save_setting(on):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"enabled": bool(on)}, f)
        return True
    except OSError as e:
        print("[pn] could not save setting:", e)
        return False


# ---- announce ----

def app_data():
    """Upstream LXMRouter.get_propagation_node_app_data(), field for field;
    upstream clients reject anything that doesn't validate exactly
    (LXMF.pn_announce_data_is_valid)."""
    meta = {PN_META_VERSION: "stump-pn-1", PN_META_NAME: _node_name.encode("utf-8"),
            PN_META_IMPL_NAME: "stump"}
    return umsgpack.packb([False, unix_now(), _enabled, TRANSFER_LIMIT_KB, SYNC_LIMIT_KB,
                           [STAMP_COST, STAMP_FLEX, PEERING_COST], meta])


def announce():
    if _dest is not None:
        _dest.announce()
    if _mailbox is not None:
        _mailbox[0].announce()


# ---- setup ----

def attach(router, node_name):
    """Called at every boot, on or off, so /admin can switch the node on
    later without a restart."""
    global _router, _node_name
    _router = router
    _node_name = node_name


def enable(router=None, node_name=None):
    """Creates the propagation destination and loads what's stored. Safe to
    call again (e.g. when switched on from /admin)."""
    global _router, _dest, _node_name, _enabled
    if router is not None:
        _router = router
    if node_name is not None:
        _node_name = node_name
    if _router is None:
        return False
    if not _storage_ready():
        print("[pn] no SD card: propagation node not started")
        return False
    if _dest is None:
        _dest = Destination(_router.delivery_identity, Destination.IN, Destination.SINGLE,
                            "lxmf", "propagation")
        _dest.set_link_established_callback(_on_link)
        _dest.register_request_handler(MESSAGE_GET_PATH, _get_request, allow=Destination.ALLOW_ALL)
        _dest.register_request_handler(OFFER_REQUEST_PATH, _offer_request, allow=Destination.ALLOW_ALL)
        _dest.set_default_app_data(app_data)   # fresh timebase on every announce
        _setup_mailbox(_node_name)
        _load_store()
    _enabled = True
    print("[pn] propagation node on: %s, %d stored, %d waiting for stamp checks"
          % (_dest.hexhash, len(_entries), len(_pending)))
    return True


def disable():
    """Stops accepting uploads and announces the node as off. Stored
    messages stay and can still be collected, so nothing is lost."""
    global _enabled
    _enabled = False


def hexhash():
    return _dest.hexhash if _dest is not None else None


def _storage_ready():
    try:
        import fserv
        if not fserv.sd_ok:
            return False
    except ImportError:
        pass
    for d in (STORE_DIR, PENDING_DIR):
        try:
            os.mkdir(d)
        except OSError:
            pass
    try:
        os.listdir(PENDING_DIR)
        return True
    except OSError:
        return False


def _load_store():
    _entries.clear()
    del _pending[:]
    floor = _clock_floor()
    now = unix_now()
    for name in os.listdir(STORE_DIR):
        if name == "pending":
            continue
        path = STORE_DIR + "/" + name
        try:
            parts = name.split("_")
            tid = bytes.fromhex(parts[0])
            received = int(parts[1]) if len(parts) > 1 else now
            local = name.endswith("_local")
            with open(path, "rb") as f:
                recipient = f.read(DEST_LEN)
            size = os.stat(path)[6]
        except (OSError, ValueError, IndexError):
            continue
        if floor is not None and received < floor and now >= floor:
            received = now   # stored while the clock was unset: age unknown, count from now
        _entries[tid] = [recipient, path, received, size, local]
    for name in sorted(os.listdir(PENDING_DIR)):
        _pending.append(PENDING_DIR + "/" + name)
    _expire()


def _clock_floor():
    """Earliest Unix time a set clock can show (2025-01-01). A message
    received while the clock was unset gets an unknown age instead of
    looking decades old -- the same trap the billboard fell into."""
    return 1735689600


# ---- uploads ----

def _on_link(link):
    link.set_packet_callback(_on_packet)
    link.resource_concluded_callback = _on_resource


def _on_packet(plaintext, packet):
    # urns confirms receipt right after this returns -- deliberately before
    # the stamp check (see the module docstring).
    try:
        data = umsgpack.unpackb(plaintext)
        for transient_data in data[1]:
            _park(transient_data)
    except Exception as e:
        print("[pn] bad upload packet:", e)


def _on_resource(resource):
    try:
        from urns.resource import COMPLETE
        if resource.status != COMPLETE:
            return
        data = umsgpack.unpackb(resource.data)
        if not (isinstance(data, list) and len(data) == 2 and isinstance(data[1], list)):
            return
        # Upstream: without a validated peering key (Phase B), a transfer
        # may carry one message only.
        if len(data[1]) > 1:
            print("[pn] multi-message upload without peering, ignored")
            resource.link.teardown()
            return
        for transient_data in data[1]:
            _park(transient_data)
    except Exception as e:
        print("[pn] bad upload resource:", e)


def _park(transient_data):
    stats["received"] += 1
    if not _enabled or not isinstance(transient_data, (bytes, bytearray)):
        stats["dropped"] += 1
        return
    if len(transient_data) <= LXMF_OVERHEAD + STAMP_SIZE:
        stats["dropped"] += 1
        return
    if len(transient_data) - STAMP_SIZE > TRANSFER_LIMIT_KB * 1000:
        # Over the per-transfer limit this node announces: upstream clients
        # don't send these; anything that does is refused, never parked.
        stats["dropped"] += 1
        return
    tid = Identity.full_hash(bytes(transient_data[:-STAMP_SIZE]))
    name = tid.hex()
    if tid in _entries or any(p.endswith(name) for p in _pending):
        return  # already have it
    if len(_pending) >= MAX_PENDING:
        stats["dropped"] += 1
        print("[pn] check queue full, upload dropped")
        return
    path = PENDING_DIR + "/" + ("%010d" % unix_now()) + "_" + name
    try:
        with open(path, "wb") as f:
            f.write(transient_data)
    except OSError as e:
        stats["dropped"] += 1
        print("[pn] could not park upload:", e)
        return
    _pending.append(path)


# ---- background stamp checks ----

async def checker_loop():
    """Checks parked uploads one at a time, a couple of work-block rounds
    per step, yielding to the rest of the node between steps."""
    import uasyncio as asyncio
    while True:
        if not _pending:
            await asyncio.sleep(2)
            continue
        path = _pending[0]
        try:
            with open(path, "rb") as f:
                transient_data = f.read()
        except OSError:
            _pending.pop(0)
            continue
        try:
            valid = await _check_stamp(transient_data)
        except Exception as e:
            # One bad check must never stop the checker for good -- an
            # exception here used to kill the task, so nothing after it was
            # ever checked. Drop this upload and carry on.
            print("[pn] stamp check failed:", e)
            valid = False
        if _pending and _pending[0] == path:
            _pending.pop(0)
        try:
            if valid:
                stats["valid"] += 1
                _accept(transient_data)
            else:
                stats["invalid"] += 1
                print("[pn] invalid stamp, message dropped")
        except Exception as e:
            print("[pn] could not store checked message:", e)
        try:
            os.remove(path)
        except OSError:
            pass


def _min_cost():
    return max(0, STAMP_COST - STAMP_FLEX)


async def _check_stamp(transient_data):
    """upstream LXStamper.validate_pn_stamp(): work block from the message's
    id, then full_hash(workblock + stamp) must be at or below the target."""
    import uasyncio as asyncio
    global _workblock
    lxm, stamp = transient_data[:-STAMP_SIZE], transient_data[-STAMP_SIZE:]
    tid = Identity.full_hash(bytes(lxm))
    if _workblock is None:
        _workblock = bytearray(WORKBLOCK_ROUNDS * 256)
    wb = _workblock
    for n in range(WORKBLOCK_ROUNDS):
        wb[n * 256:(n + 1) * 256] = hkdf(length=256, derive_from=tid,
                                         salt=Identity.full_hash(tid + umsgpack.packb(n)),
                                         context=None)
        if n % ROUNDS_PER_STEP == ROUNDS_PER_STEP - 1:
            await asyncio.sleep(0)
    # SHA-256 over work block + stamp, fed in place: building that 256 KB
    # string first copied the block twice per check, and the second check
    # already hit MemoryError on a fragmented heap.
    h = hashlib.sha256()
    h.update(wb)
    h.update(bytes(stamp))
    result = h.digest()
    target = 1 << (256 - _min_cost())
    return int.from_bytes(result, "big") <= target


def _accept(transient_data):
    """A message whose stamp checked out: deliver it here if it's for this
    node (or its test mailbox), else store it for its recipient."""
    lxm, stamp = bytes(transient_data[:-STAMP_SIZE]), bytes(transient_data[-STAMP_SIZE:])
    tid = Identity.full_hash(lxm)
    recipient = lxm[:DEST_LEN]
    if _router is not None and _router.delivery_destination is not None \
            and recipient == _router.delivery_destination.hash:
        _deliver_here(lxm, _router.delivery_destination, to_mailbox=False)
        return
    if _mailbox is not None and recipient == _mailbox[0].hash:
        _deliver_here(lxm, _mailbox[0], to_mailbox=True)
        return
    _store(tid, recipient, lxm + stamp, local=False)


def _store(tid, recipient, data, local):
    now = unix_now()
    path = STORE_DIR + "/" + tid.hex() + "_" + str(now) + ("_local" if local else "")
    with open(path, "wb") as f:
        f.write(data)
    _entries[tid] = [recipient, path, now, len(data), local]
    _enforce_limits(recipient)


def _deliver_here(lxm, destination, to_mailbox):
    """A propagated message addressed to this node itself: decrypt it and
    hand it to the chat bridge, as upstream delivers locally."""
    from urns.lxmf import LXMessage
    plain = destination.decrypt(lxm[DEST_LEN:])
    if plain is None:
        return
    message = LXMessage.unpack_from_bytes(lxm[:DEST_LEN] + plain)
    stats["local"] += 1
    if to_mailbox:
        _mailbox_inbox.append({"text": message.content_as_string() or "",
                               "from": message.source_hash.hex(), "at": unix_now()})
        del _mailbox_inbox[:-10]
        return
    cb = getattr(_router, "_delivery_callback", None)
    if cb:
        # Marked, so the chat can tell it came the slow way (rrc_mesh drops
        # stale commands that arrive like this).
        message.via_propagation = True
        cb(message)


# ---- limits and expiry ----

def _remove(tid):
    entry = _entries.pop(tid, None)
    if entry is not None:
        try:
            os.remove(entry[1])
        except OSError:
            pass


def _enforce_limits(recipient):
    mine = sorted((e[2], t) for t, e in _entries.items() if e[0] == recipient)
    for _, t in mine[:-MAX_PER_RECIPIENT] if len(mine) > MAX_PER_RECIPIENT else []:
        _remove(t)
    total = sum(e[3] for e in _entries.values())
    if total > MAX_TOTAL_BYTES:
        for _, t in sorted((e[2], t) for t, e in _entries.items()):
            if total <= MAX_TOTAL_BYTES:
                break
            total -= _entries[t][3]
            _remove(t)


def _expire():
    now = unix_now()
    if now < _clock_floor():
        return   # clock not set: ages can't be measured
    for t in [t for t, e in _entries.items() if now - e[2] > MESSAGE_EXPIRY]:
        _remove(t)


# ---- requests ----

def _offer_request(path=None, data=None, request_id=None, link_id=None,
                   remote_identity=None, requested_at=None):
    # Peering is Phase B. ERROR_NO_ACCESS makes an upstream peer un-peer
    # cleanly (LXMPeer.offer_response) instead of retrying.
    return ERROR_NO_ACCESS


def _get_request(path=None, data=None, request_id=None, link_id=None,
                 remote_identity=None, requested_at=None):
    """upstream LXMRouter.message_get_request(): [None, None] lists the
    identified client's message ids, smallest first; [wants, haves, limit]
    deletes what it has and returns what it wants, stamps stripped."""
    if remote_identity is None:
        return ERROR_NO_IDENTITY
    try:
        recipient = Destination.hash(remote_identity, "lxmf", "delivery")
        if data is None or not isinstance(data, list) or len(data) < 2:
            return None
        if data[0] is None and data[1] is None:
            mine = [(e[3], t) for t, e in _entries.items() if e[0] == recipient]
            mine.sort()
            return [t for _, t in mine]
        for t in (data[1] or []):
            if t in _entries and _entries[t][0] == recipient:
                _remove(t)
        out = []
        limit = RESPONSE_BYTES_MAX
        if len(data) >= 3 and data[2] is not None:
            try:
                limit = min(limit, int(float(data[2]) * 1000))
            except (TypeError, ValueError):
                pass
        size = 24
        for t in (data[0] or []):
            e = _entries.get(t)
            if e is None or e[0] != recipient:
                continue
            try:
                with open(e[1], "rb") as f:
                    stamped = f.read()
            except OSError:
                continue
            if size + len(stamped) + 16 > limit:
                continue
            out.append(stamped[:-STAMP_SIZE])
            size += len(stamped) + 16
        stats["served"] += len(out)
        return out
    except Exception as e:
        print("[pn] /get failed:", e)
        return None


# ---- the single-phone test tools ----

def _setup_mailbox(node_name):
    """A test recipient that's never online: send it a message through this
    node and /admin shows it arriving. Its identity lives on the node, so
    the node can read what reaches it. It refuses links and never confirms
    direct delivery, so a phone has to go through the propagation node."""
    global _mailbox
    ident = None
    try:
        os.stat(MAILBOX_IDENTITY_FILE)        # first boot: no file yet, not an error
        ident = Identity.from_file(MAILBOX_IDENTITY_FILE)
    except OSError:
        pass
    if ident is None:
        ident = Identity()
        try:
            ident.to_file(MAILBOX_IDENTITY_FILE)
        except Exception:
            pass
    d = Destination(ident, Destination.IN, Destination.SINGLE, "lxmf", "delivery")
    d.accepts_links(False)
    d.set_default_app_data(umsgpack.packb([(node_name + " test mailbox").encode("utf-8"), None, []]))
    _mailbox = [d, ident]


def mailbox_hexhash():
    return _mailbox[0].hexhash if _mailbox is not None else None


def mailbox_inbox():
    return list(_mailbox_inbox)


def leave_message(recipient_hex, text):
    """Stores a message from this node for an LXMF address, as if someone
    had uploaded it while they were offline: they collect it with a sync.
    Returns None on success, or a reason string."""
    if not _enabled or _router is None:
        return "off"
    try:
        recipient = bytes.fromhex(recipient_hex.strip().lower())
    except ValueError:
        return "address"
    if len(recipient) != DEST_LEN:
        return "address"
    ident = Identity.recall(recipient)
    if ident is None:
        return "unknown"   # never heard its announce: can't encrypt to it
    from urns.lxmf import LXMessage
    to = Destination(ident, Destination.OUT, Destination.SINGLE, "lxmf", "delivery")
    m = LXMessage(destination=to, source=_router.delivery_destination, content=text,
                  title="", fields={})
    m.pack()
    lxm = m.packed[:DEST_LEN] + to.encrypt(m.packed[DEST_LEN:])
    tid = Identity.full_hash(lxm)
    # Made here, not uploaded, so it carries no stamp (generating one would
    # take this board hours). The placeholder is stripped before serving,
    # and Phase B won't forward unstamped local messages to peers.
    _store(tid, recipient, lxm + bytes(STAMP_SIZE), local=True)
    return None


def status():
    return {"enabled": _enabled, "address": hexhash(), "stored": len(_entries),
            "checking": len(_pending), "bytes": sum(e[3] for e in _entries.values()),
            "stats": dict(stats), "mailbox": mailbox_hexhash(), "cost": STAMP_COST}
