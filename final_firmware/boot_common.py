# Project Stump -- boot_common.py
#
# The retry-vs-give-up logic main.py needs, extracted so a second board
# role can reuse it instead of duplicating main.py with one import line
# changed. Same reasoning as node_common.py's own extraction: two
# near-identical copies of boot logic drift the moment one gets a real
# fix and the other doesn't, and nobody notices until the un-fixed one
# fails in the field.
#
# THIS FILE HAS NO MODULE-LEVEL SIDE EFFECTS -- importing it does
# nothing by itself. boot_with_retry() is the only thing that acts,
# and only when a caller's own main.py actually invokes it.

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


def boot_with_retry(module_name):
    """Imports module_name, retrying transient failures a few times and
    stopping cleanly (not endlessly rebooting) on a persistent one --
    see main.py's own header comment for the full transient-vs-persistent
    reasoning this preserves unchanged. Never returns on success (the
    imported module owns the board from here); on a persistent failure
    it prints the reason and returns, leaving the board idle at a REPL
    rather than power-cycling forever."""
    try:
        __import__(module_name)
        # Reaching here means startup got far enough to be considered
        # good, so the failure counter shouldn't carry over to a later,
        # unrelated problem.
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

        # A missing module is never transient and is nearly always a
        # partial or mixed upload, so name that directly rather than
        # making someone infer it from a bare ImportError.
        if isinstance(e, ImportError):
            print("  A module is missing. That means the upload is incomplete, or")
            print("  mixes files from two different builds -- retrying cannot fix")
            print("  it. Re-upload a complete, single-build firmware folder:")
            print("    python3 provisioner.py --upload-app <port>")
            print()

        if attempts >= MAX_BOOT_ATTEMPTS:
            print("  Giving up on automatic restart -- the board is staying")
            print("  powered and idle so this message remains readable.")
            print("  Nothing else is running.")
            print()
            print("  Fix the cause, then reset the board (or power-cycle it).")
            print("=" * 52)
            _clear_count()   # so the next boot after a fix starts clean
        else:
            print("  Retrying in 10 seconds...")
            print("=" * 52)
            time.sleep(10)
            machine.reset()
