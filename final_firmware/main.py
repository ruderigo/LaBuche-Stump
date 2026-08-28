# Project Stump -- main.py
# MicroPython auto-runs this file after every boot, no USB/computer
# needed. This is what actually makes "power on and it works" real.
#
# Startup failures come in two kinds and they need opposite responses:
#
#   TRANSIENT -- WiFi not up yet, a router still rebooting after the
#   power cut that hit both devices at once. Retrying fixes these, and
#   an unattended node in the field should recover on its own.
#
#   PERSISTENT -- a missing module, a bad upload, a mixed install where
#   some files came from one build and some from another. Retrying can
#   NEVER fix these, and resetting forever actively hides them: the
#   board looks alive, WiFi even comes up briefly each cycle, but
#   nothing ever finishes booting. From outside that presents as
#   "connection refused" with no explanation anywhere -- which is
#   exactly how a mixed install cost real debugging time.
#
# So: retry a few times for the transient case, then stop and stay
# stopped with the error on screen for the persistent one. A board
# sitting at a REPL showing "no module named 'rrc'" can be fixed in
# seconds. A board silently rebooting every ten seconds cannot.

import time
import machine

MAX_BOOT_ATTEMPTS = 3
COUNTER_FILE = "boot_fail_count"


def _read_count():
    try:
        with open(COUNTER_FILE) as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def _write_count(n):
    try:
        with open(COUNTER_FILE, "w") as f:
            f.write(str(n))
    except Exception:
        pass  # read-only or full filesystem -- not worth failing the boot over


def _clear_count():
    try:
        import os
        os.remove(COUNTER_FILE)
    except Exception:
        pass


try:
    import example_node
    # Reaching here means startup got far enough to be considered good,
    # so the failure counter shouldn't carry over to a later, unrelated
    # problem.
    _clear_count()

except Exception as e:
    attempts = _read_count() + 1
    _write_count(attempts)

    print()
    print("=" * 52)
    print("STARTUP FAILED (attempt %d of %d)" % (attempts, MAX_BOOT_ATTEMPTS))
    print("=" * 52)
    print("  %s: %s" % (type(e).__name__, e))
    print()

    # A missing module is never transient and is nearly always a partial
    # or mixed upload, so name that directly rather than making someone
    # infer it from a bare ImportError.
    if isinstance(e, ImportError):
        print("  A module is missing. That means the upload is incomplete, or")
        print("  mixes files from two different builds -- retrying cannot fix")
        print("  it. Re-upload a complete, single-build firmware folder:")
        print("    python3 provisioner.py --upload-app <port>")
        print()

    if attempts >= MAX_BOOT_ATTEMPTS:
        print("  Giving up on automatic restart -- the board is staying")
        print("  powered and idle so this message remains readable.")
        print("  Nothing else is running: no web server, no mesh.")
        print()
        print("  Fix the cause, then reset the board (or power-cycle it).")
        print("=" * 52)
        _clear_count()   # so the next boot after a fix starts clean
    else:
        print("  Retrying in 10 seconds...")
        print("=" * 52)
        time.sleep(10)
        machine.reset()
