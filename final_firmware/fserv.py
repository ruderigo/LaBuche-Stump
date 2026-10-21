# Project Stump -- fserv (walk-up file server + credit economy)
# Place at: firmware/fserv.py
#
# LOCAL ONLY -- same R (AP+LAN) as billboard. Nothing here ever touches
# RNS/LXMF; SAF (separate module) is the only thing that rides the mesh,
# and only for small messages, never files.
#
# Neutral mode: anyone browses/uploads/downloads. Identified by IP
# address (barkeep's `identifier`, from the connection's peer) -- an
# earlier version of this comment said MAC address, which the code never
# used. Imperfect (a phone can get a new address, two people behind one
# router share one), but sufficient for a soft reputation economy, not a
# security boundary. Credit tracked per identifier, class-weighted (a
# real upload/download economy, not just a counter).
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
import time

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
# Home-page branding, dropped on the card by whoever keeps the node:
# home.html (or home.<lang>.html), its images, and home.json for where it
# goes. See barkeep._render_chat_page and docs/HOME_BRANDING.md.
HOME_DIR = SD_MOUNT + "/home"
HOME_MAX_BYTES = 32768
HOME_PLACES = ("top", "bottom", "left", "right")
LEDGER_FILE = SD_MOUNT + "/fserv_ledger.json"
AWAITING_FILE = SD_MOUNT + "/fserv_awaiting.json"

# The ceiling storage stays under, as a fraction of the card's total
# capacity -- not "keep N bytes free", which stops making sense the
# moment someone swaps in a card of a different size. 75% leaves real
# headroom on any card: room for the ledger/awaiting JSON files, the
# filesystem's own bookkeeping, and enough slack that eviction (below)
# is never fighting the FAT allocator over the last few blocks.
MAX_SD_RATIO = 0.75
DRAIN_MAX = 2 * 1024 * 1024   # a refused upload: read at most this much of it (drain())

# Uploads, fair to everyone (the second step of hardening for the
# pentest). One visitor -- one address -- can't hog the Stump:
#   - one upload at a time;
#   - UPLOAD_FILES_PER_HOUR files an hour;
#   - UPLOAD_DAY_SHARE of the card's usable room a day (on a free Stump
#     nothing else stops someone pushing everyone's files out);
#   - all uploads together at most UPLOAD_MAX_BPS (bytes/s; 0: no cap),
#     split evenly between those going on: one big upload can't take all
#     of the CAM's Wi-Fi airtime. On a computer, no cap by default.
# A file shared in the last FRESH_S is never pushed out to make room for
# someone else's (507 instead), and a name already taken gets " (2)"
# rather than replacing what someone shared. Each can be set in config.py.
UPLOAD_FILES_PER_HOUR = 30
UPLOAD_DAY_SHARE = 0.25
FRESH_S = 3600
_UPLOAD_BPS_CAM = 300 * 1024


def _cfg(key, default):
    try:
        import config
        v = getattr(config, key, None)
    except ImportError:
        v = None
    return default if v is None else v


def upload_max_bps():
    try:
        import stump_host  # noqa: F401
        default = 0
    except ImportError:
        default = _UPLOAD_BPS_CAM
    return int(_cfg("UPLOAD_MAX_BPS", default))


_up_active = {}       # address -> uploads going on
_up_log = {}          # address -> [[time, bytes]] of the last day's uploads
_UP_MAX_KEYS = 64


def _up_recent(key, now):
    log = [e for e in _up_log.get(key, []) if now - e[0] < 86400]
    if log:
        _up_log[key] = log
    else:
        _up_log.pop(key, None)
    return log


def upload_gate(key, length, now=None):
    """Before an upload from `key` (an address) of `length` bytes is read:
    None if it may go ahead, else (status, code, i18n key, params)."""
    now = time.time() if now is None else now
    if _up_active.get(key, 0) >= 1:
        return ("429 Too Many Requests", "one_at_a_time", "upload_one_at_a_time", {})
    log = _up_recent(key, now)
    if sum(1 for e in log if now - e[0] < 3600) >= int(_cfg("UPLOAD_FILES_PER_HOUR", UPLOAD_FILES_PER_HOUR)):
        return ("429 Too Many Requests", "too_many", "upload_too_many",
                {"n": int(_cfg("UPLOAD_FILES_PER_HOUR", UPLOAD_FILES_PER_HOUR))})
    total, _, _ = get_storage_stats()
    if length > total * MAX_SD_RATIO:
        return None            # bigger than the whole card: "full" (507) says it better
    room = int(total * MAX_SD_RATIO * float(_cfg("UPLOAD_DAY_SHARE", UPLOAD_DAY_SHARE)))
    if total and sum(e[1] for e in log) + length > room:
        return ("429 Too Many Requests", "quota", "upload_quota", {"mb": room // (1024 * 1024)})
    return None


def upload_start(key):
    _up_active[key] = _up_active.get(key, 0) + 1


def upload_end(key, written, now=None):
    n = _up_active.get(key, 0) - 1
    if n > 0:
        _up_active[key] = n
    else:
        _up_active.pop(key, None)
    if written:
        _up_log.setdefault(key, []).append([time.time() if now is None else now, written])
        while len(_up_log) > _UP_MAX_KEYS:
            _up_log.pop(next(iter(_up_log)))


def uploads_now():
    return sum(_up_active.values())


def free_name(directory, fname):
    """fname, or "name (2).ext", "name (3).ext"... if it's taken: an upload
    never replaces a file someone else shared."""
    def taken(n):
        try:
            os.stat(directory + "/" + n)
            return True
        except OSError:
            return False
    if not taken(fname):
        return fname
    dot = fname.rfind(".")
    base, ext = (fname[:dot], fname[dot:]) if dot > 0 else (fname, "")
    for i in range(2, 1000):
        n = "%s (%d)%s" % (base, i, ext)
        if not taken(n):
            return n
    return fname

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
        for d in (SHARED_DIR, TOOLS_DIR, FW_DIR, ABOUT_DIR, HOME_DIR):
            try:
                os.mkdir(d)
            except OSError:
                pass  # already exists
        sd_ok = True
        print("[fserv] SD mounted at", SD_MOUNT)
        sweep_tmp()
        migrate_tools()
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


def sweep_tmp(paths=None):
    """At start-up: the temporary files of saves a power cut interrupted
    (the credit ledger, the billboard). Each save writes <file>.tmp and
    then renames it over <file>, so a <file>.tmp still there is a save
    that never finished: the <file> beside it is the last whole one.
    Only those names -- never anything else that happens to end in .tmp
    (a visitor's upload can). Returns how many were removed."""
    if paths is None:
        paths = [LEDGER_FILE, AWAITING_FILE]
        try:
            import billboard
            paths.append(billboard.storage_file())
        except Exception:
            pass
    n = 0
    for p in paths:
        try:
            os.remove(p + ".tmp")
            n += 1
        except OSError:
            pass
    if n:
        print("[fserv] removed %d unfinished save(s) left by a power cut" % n)
    return n


def _save_json(path, data):
    """Writes data as JSON. Returns True/False rather than letting a
    write failure propagate -- confirmed as a real, reported bug
    without this: an upload's actual bytes could already be safely on
    disk (stream_to_file finished fine) and the credit-ledger rewrite
    right after it is what threw (a card at or near capacity, or any
    other transient I/O hiccup), uncaught, all the way out through
    barkeep.py's own outer exception handler -- which logs the error
    server-side but was never designed to send a response for an
    exception this deep, so the client got nothing back at all: no
    success, no error, just a connection that closed with no HTTP
    response. Paired with the client's fetch() having no .catch() of
    its own (fixed alongside this), that combination left the upload
    UI showing "Sending..." forever with zero indication anything had
    gone wrong -- indistinguishable from the upload having silently
    failed even when the file itself made it onto the card intact.
    Every caller here already treats "couldn't persist this" as
    recoverable rather than fatal to the surrounding request."""
    # Whole or not at all: written to a temporary file, then renamed over
    # the old one (MicroPython's FAT rename replaces, as billboard.py's
    # saves rely on). Writing in place meant a power cut mid-write left a
    # truncated file -- and an unreadable ledger reads as empty, so
    # everyone's credits were gone.
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.rename(tmp, path)
        return True
    except OSError as e:
        print("[fserv] failed to save", path, "--", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


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


# What kind of file, for finding things (the Files page's filters,
# BarKeep, /files.json?cat=). Broader than guess_class(), which sets
# credit costs and stays as it is.
KINDS = (
    ("video", ("mp4", "mkv", "avi", "mov", "webm", "m4v")),
    ("music", ("mp3", "flac", "wav", "ogg", "m4a", "opus", "aac")),
    ("document", ("pdf", "txt", "doc", "docx", "epub", "md", "html", "htm", "odt", "rtf")),
    ("image", ("jpg", "jpeg", "png", "gif", "webp", "avif", "svg", "heic")),
)


def kind(filename):
    """video, music, document, image or other."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for k, exts in KINDS:
        if ext in exts:
            return k
    return "other"


def query_files(words=None, cat=None, sort="newest", limit=5):
    """The shelf, filtered and sorted: [{"name", "size", "mtime", "cat"}].
    words: topic words (folded, see librarian.py); a file matches when its
    folded name holds one of them (or the start of a long one), and files
    matching more words come first. cat: one kind() value. sort: newest,
    oldest, name, small or large. Reads the folder listing and each file's
    size and date, nothing else."""
    import librarian
    rows = []
    for n in _list_files():
        c = kind(n)
        if cat and c != cat:
            continue
        score = 0
        if words:
            folded = librarian.fold(n)
            for w in words:
                if librarian.stem(w) in folded:
                    score += 1
            if not score:
                continue
        try:
            st = os.stat(SHARED_DIR + "/" + n)
            size, mtime = st[6], st[8]
        except OSError:
            size, mtime = 0, 0
        rows.append({"name": n, "size": size, "mtime": mtime, "cat": c, "_s": score})
    # One sort, best matches first, then the order asked for (MicroPython's
    # sort isn't stable, so two passes wouldn't keep the first).
    if sort == "name":
        rows.sort(key=lambda r: (-r["_s"], r["name"].lower()))
    elif sort == "small":
        rows.sort(key=lambda r: (-r["_s"], r["size"]))
    elif sort == "large":
        rows.sort(key=lambda r: (-r["_s"], -r["size"]))
    elif sort == "oldest":
        rows.sort(key=lambda r: (-r["_s"], r["mtime"]))
    else:
        rows.sort(key=lambda r: (-r["_s"], -r["mtime"]))
    for r in rows:
        del r["_s"]
    return rows[:limit] if limit else rows


def credit_balance(identifier):
    if not sd_ok:
        return 0
    if str(identifier).startswith("p:"):         # a registered profile (profiles.py)
        import profiles
        return profiles.balance(identifier[2:])
    return _ledger().get(identifier, 0)


def credit_add(identifier, amount):
    # In free mode there's no ledger to keep. Skipping the write matters
    # beyond tidiness: _save_json rewrites the entire ledger file to the
    # SD card, so a no-op zero write would still mean a full rewrite on
    # every upload AND every download, for no result.
    if not sd_ok or not CREDITS_ENABLED or amount == 0:
        return 0
    if str(identifier).startswith("p:"):
        import profiles
        return profiles.add_credits(identifier[2:], amount)
    ledger = _ledger()
    ledger[identifier] = ledger.get(identifier, 0) + amount
    _save_json(LEDGER_FILE, ledger)
    return ledger[identifier]


def move_credits(src, dst):
    """What `src` has in the ledger goes to `dst` (a visitor's address to
    its browser ID, a visitor's balance into the profile they create).
    Returns the amount moved."""
    if not sd_ok:
        return 0
    ledger = _ledger()
    amount = ledger.pop(src, 0)
    if not amount:
        return 0
    if str(dst).startswith("p:"):
        import profiles
        profiles.add_credits(dst[2:], amount)
    else:
        ledger[dst] = ledger.get(dst, 0) + amount
    _save_json(LEDGER_FILE, ledger)
    return amount


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


async def stream_to_file(reader, dest, length, chunk=16384, max_bps=None):
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
    t0 = time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.time() * 1000)
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
                    cap = upload_max_bps() if max_bps is None else max_bps
                    if cap:
                        # Its share of the cap: reading slower makes the
                        # sender slow down (TCP), and the airtime is shared.
                        share = cap // max(1, uploads_now())
                        due = written * 1000 // max(1, share)
                        try:
                            spent = time.ticks_diff(time.ticks_ms(), t0)
                        except AttributeError:
                            spent = int(time.time() * 1000) - t0
                        if due > spent:
                            await asyncio.sleep(min(2000, due - spent) / 1000)
            if buf:
                f.write(buf)
    except Exception as e:
        _discard_partial(dest)
        print("[fserv] upload failed:", e)
        return False, written
    except BaseException:
        _discard_partial(dest)     # cut off (conn.py's reaper): nothing kept
        raise

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

    At most DRAIN_MAX: a refused upload claiming gigabytes isn't read to
    its end (minutes of the Wi-Fi's airtime) -- the answer goes out and
    the connection closes; a browser may then show a reset instead of the
    message, a price worth paying.
    """
    read = 0
    if length > DRAIN_MAX:
        length = DRAIN_MAX
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


def _is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def list_tools(section=""):
    """The files in tools/ (section "") or in one of its folders."""
    if not sd_ok:
        return []
    d = TOOLS_DIR + ("/" + section if section else "")
    try:
        return sorted(n for n in os.listdir(d) if not _is_dir(d + "/" + n))
    except OSError:
        return []


# The Tools page is sectioned: each tool in its own folder of tools/,
# with a README.md that says what it is (its first "# " line is the
# section's title, the first paragraph under it the summary). Loose files
# in tools/ itself are still offered, under "Other files". A README in
# the visitor's language, README.fr.md / README.es.md, is shown instead
# when there is one.
README = "README.md"


def _is_readme(n):
    return n == README or (n.startswith("README.") and n.endswith(".md"))
README_MAX = 8192            # shown on the page up to this size


def tool_sections(lang=None):
    """[{"dir", "title", "summary", "readme", "readme_file", "files":
    [(name, size)]}], folders in name order, then the loose files (dir "")
    if any. "readme" is the README's text (up to README_MAX), in `lang`
    when the folder has one (README.<lang>.md), or None; the READMEs
    aren't in "files"."""
    if not sd_ok:
        return []
    try:
        names = sorted(os.listdir(TOOLS_DIR))
    except OSError:
        return []
    out = []
    for d in [n for n in names if _is_dir(TOOLS_DIR + "/" + n)] + [""]:
        base = TOOLS_DIR + ("/" + d if d else "")
        files = []
        names_here = list_tools(d)
        for n in names_here:
            if d and _is_readme(n):
                continue
            try:
                files.append((n, os.stat(base + "/" + n)[6]))
            except OSError:
                pass
        rname = None
        if d:
            local = "README.%s.md" % lang if lang else None
            rname = local if local in names_here else (README if README in names_here else None)
        text = _read_readme(base + "/" + rname) if rname else None
        if not files and not text:
            continue
        title, summary = _readme_head(text) if text else (None, "")
        out.append({"dir": d, "title": title or d, "summary": summary,
                    "readme": text, "readme_file": rname, "files": files})
    return out


def _read_readme(path):
    try:
        with open(path, "rb") as f:
            raw = f.read(README_MAX)
        return raw.decode("utf-8", "ignore") if hasattr(raw, "decode") else raw
    except OSError:
        return None


def _readme_head(text):
    """(title, summary): the first "# " line, and the first paragraph of
    plain text after it."""
    title, para, seen_title = None, [], False
    for line in text.split("\n"):
        t = line.strip()
        if title is None and t.startswith("# "):
            title, seen_title = t[2:].strip(), True
            continue
        if not t:
            if para:
                break
            continue
        if t.startswith(("#", "```", "|", "-", "*", ">")) or t[0].isdigit() and t[1:2] == ".":
            if para:
                break
            continue
        if seen_title or title is None:
            para.append(t)
    return title, " ".join(para)


# Cards provisioned before the Tools page had sections keep every tool
# loose in tools/. At mount they're moved into their folders (a rename,
# nothing copied), so the page is tidy and the Provisioner finds them in
# place instead of copying them again at ~1 KB/s.
_MIGRATE = (
    ("provisioner", ("provisioner.py", "LICENSE", "Stump_Beta_A.zip"), ()),
    ("flasher", ("esptool-bundle.js", "esptool-js-LICENSE.txt"), ()),
    ("firefly", (), ("FireFly-",)),
    ("rnfs", ("reticulum-LICENSE.txt",), ("rnfs-", "rnfs_", "lxmf-", "rnspure-")),
)
_MIGRATE_RENAME = {"README.md": ("provisioner", "Stump-README.md"),
                   "rnfs-INSTALL.txt": ("rnfs", README)}


def migrate_tools():
    """Moves the old, loose tools into their folders. Returns how many."""
    try:
        names = [n for n in os.listdir(TOOLS_DIR) if not _is_dir(TOOLS_DIR + "/" + n)]
    except OSError:
        return 0
    moved = 0
    for n in names:
        dest = _MIGRATE_RENAME.get(n)
        if dest is None:
            for d, exact, prefixes in _MIGRATE:
                if n in exact or any(n.startswith(p) for p in prefixes):
                    dest = (d, n)
                    break
        if dest is None:
            continue
        d, new = dest
        try:
            os.mkdir(TOOLS_DIR + "/" + d)
        except OSError:
            pass
        src, dst = TOOLS_DIR + "/" + n, TOOLS_DIR + "/" + d + "/" + new
        try:
            try:
                os.stat(dst)
                os.remove(src)        # the folder already has its own copy
            except OSError:
                os.rename(src, dst)
            moved += 1
        except OSError as e:
            print("[fserv] tools: couldn't move", n, e)
    if moved:
        print("[fserv] tools: moved %d file(s) into their folders" % moved)
    return moved


def _tool_rel_ok(rel):
    parts = rel.split("/")
    return 1 <= len(parts) <= 2 and not any(p in ("", ".", "..") for p in parts)


def delete_tool(rel):
    """Deletes one file from tools/ ("name" or "folder/name"), from
    /admin. Strict: exactly that path, no fallback. True if deleted."""
    if not sd_ok or not _tool_rel_ok(rel):
        return False
    path = TOOLS_DIR + "/" + rel
    if _is_dir(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def delete_tool_section(name):
    """Deletes a whole section, its folder and every file in it (one
    level: sections don't nest). Returns how many files went."""
    if not sd_ok or not name or "/" in name or name in (".", ".."):
        return 0
    d = TOOLS_DIR + "/" + name
    if not _is_dir(d):
        return 0
    n = 0
    try:
        for f in os.listdir(d):
            try:
                os.remove(d + "/" + f)
                n += 1
            except OSError:
                pass
        os.rmdir(d)
    except OSError:
        pass
    return n


def tool_path(rel):
    """The card path for a tools-relative name ("rnfs/x.whl" or "x"), or
    None if it isn't one: at most one folder deep, no "..". A name not
    found in its folder is looked for loose in tools/ (a card the
    migration hasn't reached)."""
    parts = rel.split("/")
    if len(parts) > 2 or any(p in ("", ".", "..") for p in parts):
        return None
    path = TOOLS_DIR + "/" + rel
    if len(parts) == 2:
        try:
            os.stat(path)
        except OSError:
            alt = TOOLS_DIR + "/" + parts[1]
            try:
                os.stat(alt)
                return alt
            except OSError:
                pass
    return path


# The FireFly apps a node can hand out, found in TOOLS_DIR by name so a
# newer file dropped on the card replaces the old one on the page -- no
# firmware change. Big files (the Android APK is ~50 MB) go on the card
# with a card reader: over USB they'd take hours.
APPS = (
    ("android", "FireFly-Android-", ".apk"),
    ("rk3326", "FireFly-RK3326-", ".zip"),
)


def _version_key(v):
    out = []
    for part in v.split("."):
        out.append(int(part) if part.isdigit() else -1)
    return out


def find_apps():
    """{"android": {"file", "version", "size"}, "rk3326": {...}} for the
    apps present -- the highest version of each, compared as numbers
    (0.2.11 beats 0.2.9)."""
    found = {}
    for sec in tool_sections():
        for name, size in sec["files"]:
            for key, prefix, suffix in APPS:
                if name.startswith(prefix) and name.endswith(suffix):
                    version = name[len(prefix):-len(suffix)]
                    if not version:
                        continue
                    best = found.get(key)
                    if best is None or _version_key(version) > _version_key(best["version"]):
                        found[key] = {"file": (sec["dir"] + "/" if sec["dir"] else "") + name,
                                      "name": name, "version": version, "size": size}
    return found


def is_app_file(name):
    name = name.rsplit("/", 1)[-1]
    return any(name.startswith(p) and name.endswith(s) for _, p, s in APPS)


def _home_html_files():
    try:
        return sorted(n for n in os.listdir(HOME_DIR) if n.lower().endswith((".html", ".htm")))
    except OSError:
        return None


def home_branding(lang):
    """What the home page should show from the card's home/ folder: a dict
    {"name", "kind", "html", "place", "height"} or None.

    Which file: home.<lang>.html, else home.html, else -- when the folder
    holds exactly one .html file -- that one, whatever it's called (a
    file dropped in under its own name used to be silently ignored).

    kind "fragment": a piece of HTML, inserted into the page (up to
    HOME_MAX_BYTES). kind "page": a complete document (<!DOCTYPE> or
    <html>) -- shown in a frame instead, because its own styles and
    scripts would otherwise take over the whole Stump page (a full-screen
    canvas page sets overflow:hidden and display:flex on body, for one).

    home.json: {"place": "top"|"bottom"|"left"|"right", "height": px},
    height being the frame's, for a page (default 280)."""
    if not sd_ok:
        return None
    files = _home_html_files()
    if not files:
        return None
    lower = {n.lower(): n for n in files}
    name = lower.get("home." + lang + ".html") or lower.get("home.html")
    if name is None:
        if len(files) != 1:
            return None
        name = files[0]
    path = HOME_DIR + "/" + name
    try:
        size = os.stat(path)[6]
        with open(path, "rb") as f:
            head = f.read(512)
    except OSError as e:
        print("[home] can't read %s: %s" % (name, e))
        return None
    probe = head.lower()
    kind = "page" if (b"<!doctype" in probe or b"<html" in probe) else "fragment"
    html = None
    if kind == "fragment":
        if size > HOME_MAX_BYTES:
            print("[home] %s is %d bytes, over %d: ignored" % (name, size, HOME_MAX_BYTES))
            return None
        try:
            with open(path, "rb") as f:
                html = f.read().decode("utf-8")
        except (OSError, UnicodeError) as e:
            print("[home] can't read %s: %s" % (name, e))
            return None
    cfg = home_config()
    if cfg["hidden"]:
        return None
    return {"name": name, "kind": kind, "html": html, "place": cfg["place"], "height": cfg["height"]}


def home_config():
    """home.json, checked: {"place", "height", "hidden"} with defaults
    (top, 280, False) for anything missing, unknown or broken."""
    cfg = {"place": "top", "height": 280, "hidden": False}
    try:
        import ujson as json
        with open(HOME_DIR + "/home.json") as f:
            raw = json.load(f)
        if raw.get("place") in HOME_PLACES:
            cfg["place"] = raw["place"]
        h = raw.get("height")
        if isinstance(h, int) and 80 <= h <= 1200:
            cfg["height"] = h
        cfg["hidden"] = raw.get("hidden") is True
    except (OSError, ValueError, AttributeError):
        pass
    return cfg


def save_home_config(place, height, hidden):
    """Writes home.json from /admin. Returns None, or a reason it couldn't."""
    if not sd_ok:
        return "no SD card"
    if place not in HOME_PLACES or not (80 <= height <= 1200):
        return "invalid values"
    try:
        os.mkdir(HOME_DIR)
    except OSError:
        pass
    try:
        import ujson as json
        with open(HOME_DIR + "/home.json", "w") as f:
            json.dump({"place": place, "height": height, "hidden": bool(hidden)}, f)
    except OSError as e:
        return str(e)
    return None


def home_status(lang):
    """For /admin: what the node sees in home/, in plain words, in the
    reader's language."""
    import i18n
    if not sd_ok:
        return i18n.t("home_st_no_sd", lang)
    files = _home_html_files()
    if files is None:
        return i18n.t("home_st_no_folder", lang)
    b = home_branding(lang)
    if b is None and home_config()["hidden"] and files:
        return i18n.t("home_st_hidden", lang)
    if b is not None:
        how = (i18n.t("home_st_page", lang, height=b["height"]) if b["kind"] == "page"
               else i18n.t("home_st_section", lang))
        return i18n.t("home_st_in_use", lang, name=b["name"], how=how,
                      place=i18n.t("home_st_place_" + b["place"], lang))
    if not files:
        return i18n.t("home_st_no_html", lang)
    big = [n for n in files if n.lower().startswith("home") and _size(HOME_DIR + "/" + n) > HOME_MAX_BYTES]
    if big:
        return i18n.t("home_st_too_big", lang, name=big[0], kb=HOME_MAX_BYTES // 1024)
    return i18n.t("home_st_several", lang, names=", ".join(files))


def _size(path):
    try:
        return os.stat(path)[6]
    except OSError:
        return 0


def _list_files():
    if not sd_ok:
        return []
    try:
        return sorted(os.listdir(SHARED_DIR))
    except OSError:
        return []


def get_storage_stats():
    """(total_bytes, free_bytes, used_ratio) for the SD card, straight
    from the filesystem via os.statvfs -- not a sum of known file sizes,
    which would miss filesystem overhead and anything not tracked here.
    Confirmed directly against a real statvfs() call that MicroPython's
    tuple follows the standard POSIX layout this uses: s[0]=f_bsize,
    s[2]=f_blocks (total), s[3]=f_bfree (free).

    Returns (0, 0, 1.0) on any failure -- deliberately the FULLEST
    possible reading, not the emptiest. This return value feeds
    make_room_fifo()'s capacity check below; on a genuine statvfs
    failure (no card, unmounted, a transient I/O error), reporting
    "completely full" makes that check refuse new uploads and fall
    through to a clear error, instead of "completely empty", which
    would make it wave every upload through onto a card it couldn't
    actually confirm has room."""
    try:
        s = os.statvfs(SD_MOUNT)
        bsize = s[0]
        total = bsize * s[2]
        free = bsize * s[3]
        used = total - free
        return (total, free, used / total if total > 0 else 1.0)
    except Exception as e:
        print("[fserv] couldn't read the card's size:", repr(e))
        return (0, 0, 1.0)


def make_room_fifo(incoming_length):
    """Evicts the OLDEST files in SHARED_DIR (by mtime), one at a time,
    until incoming_length fits under MAX_SD_RATIO of total card
    capacity -- or returns False if it can't be made to fit even after
    removing every shared file.

    Only ever touches SHARED_DIR. TOOLS_DIR, FW_DIR, and ABOUT_DIR are
    never even listed here, let alone eligible for removal -- there is
    no code path in this function that can reach them, not just a
    check that happens to exclude them. A technician's tools, firmware
    images, and the About page's own gallery must never be silently
    deleted just because visitors filled the shared folder.

    Never raises: a listing or stat failure on one file is skipped
    (that file simply can't be evicted this pass) rather than aborting
    the whole eviction attempt over one bad entry.
    """
    total, free, _ = get_storage_stats()
    if total == 0:
        return False   # no card / couldn't read it -- get_storage_stats() already logged nothing, nothing safe to promise here
    used = total - free
    max_allowed = int(total * MAX_SD_RATIO)
    if used + incoming_length <= max_allowed:
        return True

    try:
        names = os.listdir(SHARED_DIR)
    except OSError:
        return False

    entries = []
    for name in names:
        path = SHARED_DIR + "/" + name
        try:
            st = os.stat(path)
            entries.append((st[8], st[6], path))   # (mtime, size, path)
        except OSError:
            continue
    entries.sort(key=lambda e: e[0])   # oldest mtime first

    # Shared in the last FRESH_S: not pushed out for someone else's upload
    # (one visitor flooding the card can't wipe what was just shared). A
    # time in the future -- the board's clock started again from 2000
    # after a power cut, with no network time -- doesn't count as fresh,
    # or nothing could ever make room.
    now = time.time()
    fresh = int(_cfg("FRESH_S", FRESH_S))
    entries = [e for e in entries if not (0 <= now - e[0] < fresh)]

    for mtime, size, path in entries:
        try:
            os.remove(path)
        except OSError:
            continue
        used -= size
        if used + incoming_length <= max_allowed:
            return True

    return False


def delete_file(filename):
    """Removes one file from SHARED_DIR by name. Safe to call with a
    name that doesn't exist or already isn't there -- returns False
    rather than raising, since the caller (an admin delete action)
    wants a clean yes/no, not an exception to catch."""
    try:
        os.remove(SHARED_DIR + "/" + filename)
        return True
    except OSError:
        return False



# NOTE: no HTTP server here either -- same reason as billboard.py.
# barkeep.py owns port 80 and calls this module's storage/credit
# functions (mount_sd, stream_to_file, drain, credit_cost, credit_add,
# credit_balance, guess_class, _list_files, _awaiting, _save_json,
# get_storage_stats, make_room_fifo, delete_file) directly. Keeping a
# second copy of the upload/download handler here meant fixes landed
# in one file and rotted in the other.
