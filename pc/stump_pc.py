#!/usr/bin/env python3
# Project Stump -- stump_pc.py: a Stump on a computer (docs/PC.md).
#
# The computer plays the CAM's role: same pages, chat, plugins, admin and
# mesh, with the Heltec on USB (or over Wi-Fi), and it can chain with CAM
# Stumps. This folder plays the board's flash: the firmware, the plugins
# and config.py sit next to this file, as they do on the board, and the
# Provisioner keeps them up to date (python3 provisioner.py --pc).
#
# Run:  python3 stump_pc.py        (Windows: py stump_pc.py)
# Stop: Ctrl+C
#
#   --service   what start-at-login runs: the log goes to stump.log in this
#               folder (rotated at 5 MB), and the node is started again if
#               it ever stops on an error (not after Ctrl+C).

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_PYTHON = (3, 8)


def _fail(msg):
    print("\n[stump] " + msg + "\n")
    sys.exit(1)


def main():
    if sys.version_info < MIN_PYTHON:
        _fail("Stump needs Python %d.%d or newer (this is %s)." % (MIN_PYTHON + (sys.version.split()[0],)))
    if not os.path.exists(os.path.join(HERE, "example_node.py")):
        _fail("The Stump firmware isn't in this folder. Set it up with:\n"
              "  python3 provisioner.py --pc")
    if not os.path.exists(os.path.join(HERE, "config.py")):
        _fail("No config.py in this folder. Set it up with:\n  python3 provisioner.py --pc")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        _fail("Stump needs the 'cryptography' package:\n  python3 -m pip install cryptography pyserial")

    # The log has symbols (✓ ✗ ✎ →): never let printing them stop the node
    # (Windows prints to a pipe or file in its legacy code page otherwise).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    os.chdir(HERE)
    sys.path[:0] = [os.path.join(HERE, "pc_compat"), HERE]
    import stump_host
    stump_host.install(HERE)
    print("[stump] on a computer: %s, Python %s" % (sys.platform, sys.version.split()[0]))
    print("[stump] folder: " + HERE)
    print("[stump] visitors reach it at http://%s/" % stump_host.hotspot_ip())
    try:
        import example_node  # noqa: F401  -- the node itself, exactly as on the board
    except KeyboardInterrupt:
        raise
    except BaseException as e:
        # Kept for /admin's next start (keepalive.py): what made it stop.
        try:
            import keepalive
            keepalive.note_crash("%s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        raise


LOG = os.path.join(HERE, "stump.log")
LOG_MAX = 5 * 1024 * 1024


def _open_log():
    try:
        if os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass
    return open(LOG, "a", buffering=1, encoding="utf-8", errors="replace")


def _close_radios():
    """The Heltec on USB or Bluetooth, told on the way out: radio off, the
    host left (an RNode left alone keeps its radio on otherwise)."""
    t = sys.modules.get("urns.transport")
    if t is None:
        return
    for i in list(t.Transport.interfaces):
        if i.__class__.__name__ in ("RNodeSerialInterface", "RNodeBLEInterface"):
            try:
                i.close()
            except Exception:
                pass


def _stop_on_sigterm():
    """launchctl unload, systemctl stop, a scheduled task ending: SIGTERM,
    handled like Ctrl-C so the radio is switched off first."""
    import signal

    stopping = []

    def stop(signum, frame):
        # Once is enough: a second stop while it's already stopping would
        # interrupt the clean-up itself (asyncio cancelling its tasks).
        if stopping:
            return
        # Raised inside a finalizer (__del__, a weakref callback), the
        # KeyboardInterrupt is only printed ("Exception ignored") and the
        # Stump keeps running: try again a moment later instead.
        if frame is not None and (frame.f_code.co_name in ("__del__", "_remove", "_callback", "remove",
                                                          "_atomic_removal")
                                  or frame.f_code.co_filename.endswith(("weakref.py", "_weakrefset.py"))):
            import os
            import threading
            threading.Timer(0.05, os.kill, (os.getpid(), signum)).start()
            return
        stopping.append(1)
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGTERM, stop)
    except (ValueError, AttributeError, OSError):
        pass


def _watchdog_s():
    """WATCHDOG_S from config.py (60 by default; 0: off), read without
    running config.py in the supervisor."""
    import re
    try:
        with open(os.path.join(HERE, "config.py"), encoding="utf-8") as f:
            m = re.search(r"(?m)^WATCHDOG_S\s*=\s*(\d+)", f.read())
        return int(m.group(1)) if m else 60
    except (OSError, ValueError):
        return 60


def _mark_watchdog():
    """Tell the next start it was the watchdog (keepalive.begin)."""
    import json
    path = os.path.join(HERE, "boot_state.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d["wd"] = True
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def _wait_watched(child, t0, log):
    """child.wait(), but if the Stump's heartbeat (keepalive.py) goes stale
    -- its loop stopped -- it's ended, and "watchdog" returned so it's
    started again. Before its first heartbeat the start is left alone."""
    import time
    hb = os.path.join(HERE, ".heartbeat")
    limit = _watchdog_s()
    while True:
        try:
            return child.wait(timeout=2)
        except Exception:
            pass
        if limit <= 0:
            continue
        try:
            beat = os.path.getmtime(hb)
        except OSError:
            continue
        if beat < t0:
            continue                 # not started beating yet (or an old file)
        if time.time() - beat > limit:
            log.write("[stump] it stopped responding (no heartbeat for %d s): restarting it\n" % limit)
            log.flush()
            _mark_watchdog()
            child.kill()
            try:
                child.wait(timeout=10)
            except Exception:
                pass
            return "watchdog"


def service():
    """Runs the node in a child process, logging to stump.log, and starts
    it again if it stops on an error (with a growing pause, up to a
    minute, so a broken setup doesn't spin)."""
    import subprocess
    import time
    log = _open_log()
    wait = 2
    while True:
        log.write("\n[stump] starting %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        t0 = time.time()
        child = None
        try:
            child = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__)],
                                     cwd=HERE, stdout=log, stderr=subprocess.STDOUT)
            code = _wait_watched(child, t0, log)
        except KeyboardInterrupt:
            if child is not None and child.poll() is None:
                child.terminate()          # it switches the radio off, then stops
                try:
                    child.wait(timeout=10)
                except Exception:
                    child.kill()
            log.write("[stump] stopped\n")
            return
        if code == 0:
            log.write("[stump] stopped\n")
            return
        if code == 3:                      # stump_host.RESTART_CODE: asked for (new settings)
            log.write("[stump] restarting (asked for)\n")
            log.flush()
            continue
        if code == "watchdog":
            log.flush()
            continue
        if time.time() - t0 > 300:
            wait = 2
        log.write("[stump] stopped with code %s; starting again in %d s\n" % (code, wait))
        log.flush()
        time.sleep(wait)
        wait = min(wait * 2, 60)
        log.close()
        log = _open_log()


if __name__ == "__main__":
    _stop_on_sigterm()
    if "--service" in sys.argv:
        service()
    else:
        if sys.stdout is None:          # pythonw.exe: no console to print to
            sys.stdout = sys.stderr = _open_log()
        try:
            main()
        except KeyboardInterrupt:
            pass
        finally:
            _close_radios()
