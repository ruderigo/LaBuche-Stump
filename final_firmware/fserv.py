# Project Stump -- fserv (walk-up file server + credit economy)
# Place at: firmware/fserv.py
#
# LOCAL ONLY -- same R (AP+LAN) as billboard. Nothing here ever touches
# RNS/LXMF; SAF (separate module) is the only thing that rides the mesh,
# and only for small messages, never files.
#
# Neutral mode: anyone browses/uploads/downloads. Identified by MAC
# address (imperfect -- phones randomize it per-network -- but sufficient
# for a soft reputation economy, not a security boundary). Credit tracked
# per identifier, class-weighted (a real upload/download economy, not
# just a counter).
#
# Regulated mode, same mechanism: a slot gets marked "awaiting hash X" by
# whoever has permission (e.g. a practitioner), while THEY are physically
# at Stump. The matching person later provides that same hash -- still
# just a local lookup key typed into a web form, never sent anywhere over
# RNS -- to unlock upload into that specific slot. One code path handles
# both modes; regulated is just neutral-mode-plus-a-slot-check.
#
# STORAGE: SD card, not internal flash -- real file sizes need real
# storage. Pins confirmed from Freenove's own official SDMMC tutorial
# (CMD=38, CLK=39, D0=40, 1-bit bus). machine.SDCard()'s parameter names
# (sck/cmd/data) confirmed against MicroPython's own official docs after
# the first guess (mosi/miso, SPI-mode names) failed on real hardware
# with "invalid config: SDMMC slot with SPI pin arguments".
#
# UPLOAD uses a raw POST body with the filename in a header, not
# multipart form-data -- deliberately simpler than parsing MIME
# boundaries on a memory-constrained chip. A few lines of JS on the page
# handle this with fetch(), no multipart parser needed on either end.

# FUTURE STORAGE EXPANSION, not built yet -- noted here so it isn't lost.
# The SD card above is this build's storage, not storage's only possible
# shape: ESP32-S3's SDMMC runs through the GPIO matrix, not fixed silicon
# pins, so nothing here is hard-wired to one device. Two paths considered
# for extra capacity: a repurposed old Android phone (Termux running a
# simple HTTP file server, fserv proxying to it over the LAN -- just
# another HTTP request, straightforward, and genuine e-waste reuse in the
# same spirit as STUMP.pdf's circular-economy phase) versus a dedicated
# NAS-style module (usually means mounting SMB/NFS, which MicroPython
# doesn't support well -- a much heavier lift for the same result). The
# phone-proxy path is the one worth building toward first.

import uasyncio as asyncio
import ujson as json
import os

import billboard

SD_MOUNT = "/sd"
SHARED_DIR = SD_MOUNT + "/shared"
# Technician tools live apart from user uploads. Separate directory so
# they are never credit-charged, never listed among the things people
# brought, and not casually overwritten by an upload that happens to
# share a filename. The node carries the tool needed to reprovision it.
TOOLS_DIR = SD_MOUNT + "/tools"
# Flashable images and the catalog that describes them. Kept apart from
# both user uploads and tools: adding hardware support should be
# "drop a .bin here and add a catalog entry", with no risk of a user
# upload shadowing a firmware image or vice versa.
FW_DIR = SD_MOUNT + "/fw"
# About-page assets (the hardware gallery photos), kept apart from
# both user uploads and technician tools for the same reason those are
# already separated from each other: a marketing photo has no business
# competing with -- or being mistaken for -- something a visitor
# actually uploaded, and it should never be credit-charged or listed
# on the community Files page.
ABOUT_DIR = SD_MOUNT + "/about"
LEDGER_FILE = SD_MOUNT + "/fserv_ledger.json"
AWAITING_FILE = SD_MOUNT + "/fserv_awaiting.json"

# Credit weight per file class -- now operator-configurable via config.py
# rather than edited here. The fallbacks below keep this module working
# standalone (and keep an older config.py without these settings from
# breaking the node), so config is an override, not a hard dependency.
try:
    from config import CREDIT_WEIGHTS as CLASS_WEIGHTS
except ImportError:
    CLASS_WEIGHTS = {"video": 3, "music": 2, "document": 1, "other": 1}

try:
    from config import CREDITS_ENABLED
except ImportError:
    CREDITS_ENABLED = True


def credit_cost(filename):
    """What this file is worth -- 0 everywhere when the economy is off.
    Centralised so free mode is a single switch rather than a condition
    every call site has to remember to check."""
    if not CREDITS_ENABLED:
        return 0
    return CLASS_WEIGHTS.get(guess_class(filename), 1)

sd_ok = False


def mount_sd():
    global sd_ok
    try:
        from machine import SDCard
        # Confirmed against MicroPython's own official docs (consistent
        # across v1.21-1.27): SD/MMC mode uses sck (for CLK), cmd, and a
        # data TUPLE -- not the SPI-style mosi/miso names originally
        # guessed here, and plain pin numbers, not Pin() objects.
        sd = SDCard(slot=1, width=1, sck=39, cmd=38, data=(40,))
        os.mount(sd, SD_MOUNT)
        for d in (SHARED_DIR, TOOLS_DIR, FW_DIR, ABOUT_DIR):
            try:
                os.mkdir(d)
            except OSError:
                pass  # already exists
        sd_ok = True
        print("[fserv] SD mounted at", SD_MOUNT)
    except Exception as e:
        print("[fserv] SD mount FAILED:", e)
        sd_ok = False
    return sd_ok


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def _ledger():
    return _load_json(LEDGER_FILE, {})


def _awaiting():
    return _load_json(AWAITING_FILE, {})


def guess_class(filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp4", "mkv", "avi", "mov"):
        return "video"
    if ext in ("mp3", "flac", "wav", "ogg", "m4a"):
        return "music"
    if ext in ("pdf", "txt", "doc", "docx"):
        return "document"
    return "other"


def credit_balance(identifier):
    if not sd_ok:
        return 0
    return _ledger().get(identifier, 0)


def credit_add(identifier, amount):
    # In free mode there's no ledger to keep. Skipping the write matters
    # beyond tidiness: _save_json rewrites the entire ledger file to the
    # SD card, so a no-op zero write would still mean a full rewrite on
    # every upload AND every download, for no result.
    if not sd_ok or not CREDITS_ENABLED or amount == 0:
        return 0
    ledger = _ledger()
    ledger[identifier] = ledger.get(identifier, 0) + amount
    _save_json(LEDGER_FILE, ledger)
    return ledger[identifier]


def mark_awaiting(hash_hex, note=""):
    """Called (locally, by whoever has permission) to open a regulated
    slot for a specific hash. hash_hex is just a lookup key here --
    nothing about this touches RNS."""
    if not sd_ok:
        return False
    table = _awaiting()
    table[hash_hex] = {"note": note, "fulfilled": False}
    _save_json(AWAITING_FILE, table)
    return True


async def stream_to_file(reader, dest, length, chunk=16384):
    """
    Streams `length` bytes from an open socket reader straight into a
    file on the SD card, `chunk` bytes at a time -- the upload never
    exists in RAM as a whole. This is what removes the memory ceiling
    on uploads: previously the entire body was read into a single
    bytearray before being written, so a multi-megabyte audio file or
    image would either fail outright or fragment the heap badly enough
    to destabilise the node, even with 8MB PSRAM.

    Chunk size is a throughput decision, not a safety one. At 2KB a
    10MB upload is over 5000 cycles of read/write/await, and every
    await yields to the DNS poller and the bridge loop before control
    returns -- so per-cycle overhead, not the data, set the transfer
    time. Small SD writes are individually inefficient too. 16KB is
    nowhere near large enough to strain the heap (RRC's worst case is
    591KB by comparison) and cuts the cycle count eightfold.

    A dropped connection mid-upload leaves a truncated file, which would
    otherwise sit in the shared folder looking like a real one and be
    served to the next person as a corrupt download -- so a short read
    deletes it rather than keeping it.

    Returns (ok, bytes_written).
    """
    written = 0
    try:
        with open(dest, "wb") as f:
            # Accumulate into a buffer and flush in large blocks rather
            # than writing every socket read straight to the card.
            # reader.read(n) returns as soon as ANY data is available --
            # usually a single ~1.4KB TCP segment -- so the number of
            # reads is set by the network, not by what we ask for, and
            # asking for more doesn't reduce them. What we can control
            # is the write count: buffering turns ~7000 small writes
            # into ~640 large ones on a 10MB upload, and SD cards are
            # substantially more efficient with bigger blocks.
            buf = bytearray()
            while written < length:
                want = length - written
                if want > chunk:
                    want = chunk
                data = await reader.read(want)
                if not data:
                    break  # client went away mid-upload
                buf.extend(data)
                written += len(data)
                if len(buf) >= chunk:
                    f.write(buf)
                    buf = bytearray()
            if buf:
                f.write(buf)
    except Exception as e:
        _discard_partial(dest)
        print("[fserv] upload failed:", e)
        return False, written

    if written < length:
        _discard_partial(dest)
        return False, written
    return True, written


def _discard_partial(path):
    """Removes a half-written upload. Best-effort: if it can't be
    removed there's nothing more useful to do than carry on."""
    try:
        os.remove(path)
    except Exception:
        pass


async def drain(reader, length, chunk=2048):
    """
    Reads and throws away `length` bytes. Used when an upload is going
    to be REJECTED (no filename, no SD card): the body still has to come
    off the socket for the client to reliably read the error response,
    but there's no reason to hold any of it -- and no reason to let a
    rejected upload exhaust memory, which is exactly what buffering it
    first would do.
    """
    read = 0
    while read < length:
        want = length - read
        if want > chunk:
            want = chunk
        buf = await reader.read(want)
        if not buf:
            break
        read += len(buf)
    return read


def _esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def list_fw():
    """Firmware images and catalog available for the kiosk flasher."""
    if not sd_ok:
        return []
    try:
        return sorted(os.listdir(FW_DIR))
    except OSError:
        return []


def list_tools():
    """Technician tools available for download from this node."""
    if not sd_ok:
        return []
    try:
        return sorted(os.listdir(TOOLS_DIR))
    except OSError:
        return []


def _list_files():
    if not sd_ok:
        return []
    try:
        return sorted(os.listdir(SHARED_DIR))
    except OSError:
        return []



# NOTE: no HTTP server here either -- same reason as billboard.py.
# barkeep.py owns port 80 and calls this module's storage/credit
# functions (mount_sd, stream_to_file, drain, credit_cost, credit_add,
# credit_balance, guess_class, _list_files, _awaiting, _save_json)
# directly. Keeping a second copy of the upload/download handler here
# meant fixes landed in one file and rotted in the other.
