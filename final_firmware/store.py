# Saving and reading the Stump's small JSON files (settings, identities,
# the ledger, profiles...), one way everywhere.
#
# Whole or not at all: written to <file>.tmp, then renamed over the old
# file. Most saves used to write in place, so a power cut or a reset in
# the middle left a truncated file -- and a file that doesn't parse reads
# as "nothing saved": the identity table, the layout, the radio settings
# quietly back to their defaults. The rename replaces the old file on the
# board's filesystems (FAT, LittleFS) and on a computer (pc_compat makes
# os.rename replace on Windows too); where it doesn't, the old file is
# removed first, which still never leaves a half-written one.
#
# A save never raises: it returns False and says why in the log (a full
# card isn't a reason to fail the request that saved).

import os

try:
    import ujson as json
except ImportError:
    import json


def save_json(path, data, who="store"):
    """Saves data as JSON at path. True if it was saved."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        try:
            os.rename(tmp, path)
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass
            os.rename(tmp, path)
        return True
    except Exception as e:
        print("[%s] couldn't save %s: %s" % (who, path, e))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def load_json(path, default=None):
    """What save_json() saved at path, or default (nothing saved yet, or
    a file that doesn't parse)."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default
