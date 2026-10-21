# Stump on a computer (docs/PC.md): what makes the board's firmware run on
# Windows, macOS or Linux, unchanged.
#
#   - The Stump folder plays the board's flash: the firmware, the plugins,
#     config.py and everything the node saves live there, and a path the
#     firmware opens as "/radio.json" or "/sd/shared/x" is that folder's
#     radio.json or sd/shared/x. Only calls made BY the firmware are
#     redirected (Python's own libraries keep the real filesystem).
#   - Files are UTF-8 with no line-ending translation, as on the board
#     (Windows would otherwise write cp1252 and \r\n).
#   - os.statvfs (missing on Windows) reports a storage quota, so the
#     "card" never fills the computer's own disk: PC_STORAGE_MB.
#   - Small MicroPython extras: time.ticks_*, gc.mem_free, socket.readinto.
#
# Settings, in config.py (all optional):
#   PC_HOTSPOT_IP = "auto"   # the address visitors reach this computer on
#   PC_HTTP_PORT  = 80       # 8080 if 80 is taken
#   PC_STORAGE_MB = 2048     # room for shared files and tools
#   PC_HOTSPOT_NAME = ""     # the Wi-Fi name visitors join, for the About page

import builtins
import os
import sys
import time

ROOT = None
IS_PC = True


# The double-clickable files the Provisioner puts in the Stump folder to
# start and stop it (provisioner.py: _pc_launchers).
if sys.platform == "darwin":
    START_FILE, STOP_FILE = "Start Stump.command", "Stop Stump.command"
elif os.name == "nt":
    START_FILE, STOP_FILE = "Start Stump.bat", "Stop Stump.bat"
else:
    START_FILE, STOP_FILE = "start-stump.sh", "stop-stump.sh"


def power_off():
    """Turns this Stump off (/admin's button, or the Stop file): the radio
    off and told the computer left, then a clean exit -- code 0, which the
    login service (launchd, systemd, the scheduled task) and stump_pc's
    supervisor take as "stopped on purpose", so nothing starts it again
    until it's started (the Start file, or the next login)."""
    try:
        import keepalive
        keepalive.planned()
    except Exception:
        pass
    t = sys.modules.get("urns.transport")
    if t is not None:
        for i in list(t.Transport.interfaces):
            if i.__class__.__name__ in ("RNodeSerialInterface", "RNodeBLEInterface"):
                try:
                    i.close()
                except Exception:
                    pass
    print("[stump] turned off", flush=True)
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)


RESTART_CODE = 3      # stump_pc's supervisor: "start me again now"


def restart():
    """Starts this Stump again (/admin, after changing its settings): the
    radio off, as on the way out, then an exit with RESTART_CODE -- the
    supervisor (stump_pc --service, what the Start file and start-at-login
    run) starts it again at once; the login service would too (not 0)."""
    t = sys.modules.get("urns.transport")
    if t is not None:
        for i in list(t.Transport.interfaces):
            if i.__class__.__name__ in ("RNodeSerialInterface", "RNodeBLEInterface"):
                try:
                    i.close()
                except Exception:
                    pass
    print("[stump] restarting", flush=True)
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(RESTART_CODE)


HEARTBEAT_FILE = ".heartbeat"     # in the Stump folder: touched by the running Stump (keepalive.py)


def heartbeat():
    """The Stump's loop is alive: the supervisor (stump_pc.py --service)
    restarts it when this file goes stale."""
    try:
        with open(os.path.join(ROOT or ".", HEARTBEAT_FILE), "w") as f:
            f.write("%d %d\n" % (int(time.time()), os.getpid()))
    except (OSError, TypeError):
        pass


def setting(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


# ── the hotspot address ─────────────────────────────────────────────

# Addresses the operating systems give their own hotspot.
WELL_KNOWN = ("192.168.137.1",   # Windows Mobile Hotspot
              "192.168.2.1",     # macOS Internet Sharing
              "10.42.0.1")       # Linux NetworkManager hotspot


def local_ips():
    import socket
    ips = []
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    try:
        # The address of the default route (sends nothing).
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def hotspot_ip():
    """The address visitors reach this computer on: PC_HOTSPOT_IP, else the
    hotspot's well-known address if this computer has it, else its own
    address on its network."""
    want = setting("PC_HOTSPOT_IP", "auto")
    if want and want != "auto":
        return want
    ips = local_ips()
    for ip in WELL_KNOWN:
        if ip in ips:
            return ip
    for ip in ips:
        if ip.startswith(("192.168.", "10.", "172.")):
            return ip
    return ips[0] if ips else "127.0.0.1"


# ── the Stump this one joins, when its address moves ────────────────
# On a shared Wi-Fi (home, office) the Stump this computer joins gets its
# address from the router, and it can change when it restarts. chain.py
# notices when it goes quiet and asks find_parent() to look for it on the
# network by its LXMF address; the new address is kept in
# chain_parent.json (in the Stump folder) until config.py names another.

def _parent_file():
    return os.path.join(ROOT or ".", "chain_parent.json")


def _parent_saved():
    import json
    try:
        with open(_parent_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def parent_ip():
    """The address of the Stump this one joins: the one it was last found
    at, while config.py still names the one it was set up with."""
    configured = setting("PC_CHAIN_PARENT_IP", "") or ""
    saved = _parent_saved()
    if saved.get("ip") and saved.get("config_ip") == configured:
        return saved["ip"]
    return configured


def parent_lxmf():
    saved = _parent_saved()
    return saved.get("lxmf") if saved.get("config_ip") == (setting("PC_CHAIN_PARENT_IP", "") or "") else None


def remember_parent(ip, lxmf):
    import json
    data = {"ip": ip, "lxmf": lxmf, "config_ip": setting("PC_CHAIN_PARENT_IP", "") or ""}
    try:
        with open(_parent_file(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        print("[chain] couldn't save the address of the Stump above:", e)


def node_json(ip, timeout=1.5):
    """/node.json of a Stump, straight (never through a proxy), or None."""
    import json
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        port = int(setting("PC_CHAIN_PARENT_HTTP_PORT", 80) or 80)   # 80 on a CAM; tests use others
        with opener.open("http://%s:%d/node.json" % (ip, port), timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def find_parent(lxmf, near, get=None, workers=64):
    """The address on near's /24 where the Stump with this LXMF address
    answers now, or None. A few seconds; run it off the event loop."""
    from concurrent.futures import ThreadPoolExecutor
    get = get or node_json
    if not lxmf or near.count(".") != 3:
        return None
    prefix = near.rsplit(".", 1)[0] + "."

    def ask(ip):
        node = get(ip)
        if not isinstance(node, dict) or node.get("lxmf") != lxmf:
            return None
        # Where it says it takes chain links (a computer answers HTTP on
        # every address it has; its chain is on one of them).
        chain = node.get("chain") or {}
        return ip if ip in (chain.get("lan_ip"), chain.get("ap_ip"), node.get("ap_ip")) else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ip in pool.map(ask, [prefix + str(n) for n in range(1, 255)]):
            if ip:
                return ip
    return None


# ── the Stump folder as the board's filesystem ──────────────────────

def _from_firmware():
    """True when the call comes from code in the Stump folder."""
    f = sys._getframe(2)
    name = f.f_code.co_filename
    return name.startswith(ROOT)


def _map(path):
    if isinstance(path, str) and path.startswith("/") and not path.startswith(ROOT):
        return ROOT + path
    return path


def _wrap1(fn):
    def w(path, *a, **k):
        if _from_firmware():
            path = _map(path)
        return fn(path, *a, **k)
    return w


def _wrap2(fn):
    def w(a, b, *rest, **k):
        if _from_firmware():
            a, b = _map(a), _map(b)
        return fn(a, b, *rest, **k)
    return w


def _dir_size(path):
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for n in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


_real_statvfs = getattr(os, "statvfs", None)     # before install() replaces it


def _disk_free(path):
    """Bytes really free on the disk holding `path`. Never through
    os.statvfs once install() has run -- that's _statvfs below; on macOS
    and Linux shutil.disk_usage went there, which called itself until
    Python gave up, and the firmware read that as "no card" and refused
    every upload (507)."""
    if _real_statvfs is not None:
        st = _real_statvfs(path)
        return st.f_bavail * st.f_frsize
    try:
        import nt                                   # Windows
        return nt._getdiskusage(path)[1]
    except (ImportError, AttributeError):
        pass
    import ctypes                                   # Windows, without nt._getdiskusage
    free = ctypes.c_ulonglong(0)
    if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path), ctypes.byref(free), None, None):
        raise OSError("GetDiskFreeSpaceExW failed for %s" % path)
    return free.value


def _statvfs(path):
    """The board's statvfs() tuple: a quota (PC_STORAGE_MB) for the
    folder, never more than the disk really has free."""
    real = _map(path) if isinstance(path, str) else path
    quota = int(setting("PC_STORAGE_MB", 2048)) * 1024 * 1024
    used = _dir_size(real)
    try:
        disk_free = _disk_free(real)
    except Exception as e:
        print("[stump] couldn't read the disk's free space (%r): counting on the PC_STORAGE_MB quota" % (e,))
        disk_free = quota
    free = max(0, min(quota - used, disk_free))
    total = used + free
    return (1, 1, total, free, free, 0, 0, 0, 0, 255)


def install(root):
    """Sets everything up for the firmware in `root` (the Stump folder)."""
    global ROOT
    ROOT = os.path.abspath(root).replace("\\", "/")
    if os.sep == "\\":
        # Frames report native paths on Windows.
        global _from_firmware

        def _from_firmware():
            f = sys._getframe(2)
            return f.f_code.co_filename.replace("\\", "/").startswith(ROOT)

    real_open = builtins.open

    def stump_open(file, mode="r", *a, **k):
        if _from_firmware():
            file = _map(file)
            if "b" not in mode and not a:
                k.setdefault("encoding", "utf-8")
                k.setdefault("newline", "")
        return real_open(file, mode, *a, **k)
    builtins.open = stump_open

    os.stat = _wrap1(os.stat)
    os.listdir = _wrap1_listdir(os.listdir)
    os.mkdir = _wrap1(os.mkdir)
    os.remove = _wrap1(os.remove)
    os.rmdir = _wrap1(os.rmdir)
    os.rename = _wrap2(os.replace)          # the board's rename replaces, Windows' doesn't
    os.statvfs = _statvfs
    os.mount = lambda *a, **k: None         # the "card" is the sd/ folder
    os.umount = lambda *a, **k: None
    os.sync = getattr(os, "sync", lambda: None)

    for name, fn in (("ticks_ms", lambda: int(time.monotonic() * 1000)),
                     ("ticks_us", lambda: int(time.monotonic() * 1000000)),
                     ("ticks_diff", lambda a, b: a - b),
                     ("ticks_add", lambda a, b: a + b),
                     ("sleep_ms", lambda ms: time.sleep(ms / 1000)),
                     ("sleep_us", lambda us: time.sleep(us / 1000000))):
        if not hasattr(time, name):
            setattr(time, name, fn)

    import gc
    if not hasattr(gc, "mem_free"):
        gc.mem_free = lambda: 1 << 30       # "plenty": the big tables (urns/const.py)
        gc.mem_alloc = lambda: 0
        gc.STUMP_NO_MEM_FREE = True         # ...but not a figure to show (plugins.py)

    import socket
    if not hasattr(socket.socket, "readinto"):
        def readinto(self, buf, n=None):
            try:
                return self.recv_into(buf, n or len(buf))
            except BlockingIOError:
                raise OSError(11, "EAGAIN")      # the board's errno, whatever the OS
        socket.socket.readinto = readinto

    for d in ("sd",):
        try:
            os.makedirs(os.path.join(ROOT, d), exist_ok=True)
        except OSError:
            pass


def _wrap1_listdir(fn):
    def w(path=".", *a, **k):
        if _from_firmware():
            path = _map(path)
        return fn(path, *a, **k)
    return w
