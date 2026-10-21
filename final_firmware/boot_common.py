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
    """Imports module_name -- the node, which owns the board from then on.

    If it stops on an error (while starting, or weeks later), the error is
    kept for /admin (keepalive.note_crash) and the board starts again in 10
    seconds. A field Stump has no one to read a console: it must never sit
    idle. Failed starts are counted by keepalive.py; RESCUE_AFTER in a row
    give a rescue start (no radio, no chain, no plugins: the Wi-Fi, the
    pages and /admin), which tries a normal start again after a while.

    The one exception is a missing module: an incomplete or mixed upload,
    which no restart can fix -- the board stays idle at the REPL with the
    message readable, as before."""
    try:
        __import__(module_name)
        _clear_count()
    except KeyboardInterrupt:
        # Ctrl+C that got past the node's own handler (a second one, mid
        # clean-up): the watchdog must still be disarmed before the REPL.
        try:
            import keepalive
            keepalive.console_stop()
        except Exception:
            pass
        raise
    except Exception as e:
        print()
        print("=" * 52)
        print("THE STUMP STOPPED ON AN ERROR")
        print("=" * 52)
        print("  %s: %s" % (type(e).__name__, e))
        print()
        if isinstance(e, ImportError):
            attempts = _read_count() + 1
            _write_count(attempts)
            # A missing module is never transient and is nearly always a
            # partial or mixed upload, so name that directly rather than
            # making someone infer it from a bare ImportError.
            print("  A module is missing. That means the upload is incomplete, or")
            print("  mixes files from two different builds -- retrying cannot fix")
            print("  it. Re-upload a complete, single-build firmware folder:")
            print("    python3 provisioner.py --upload-app <port>")
            print()
            if attempts >= MAX_BOOT_ATTEMPTS:
                print("  The board is staying idle so this message remains readable.")
                print("  Fix the upload, then reset the board.")
                print("=" * 52)
                _clear_count()
                return
        try:
            import keepalive
            keepalive.note_crash("%s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        print("  Starting again in 10 seconds (after %d failed starts in a row," % 3)
        print("  a rescue start: the Wi-Fi, the pages and /admin only).")
        print("=" * 52)
        time.sleep(10)
        machine.reset()
