# µReticulum Ed25519
# High-level Ed25519 signing and verification
#
# Automatically uses native C module (ed25519_fast) if available
# for the current architecture, otherwise falls back to pure Python.

import os
import sys

_native = None
_native_name = None    # which specific backend loaded -- see _try_native()

# Try to load native C module (Monocypher Ed25519 with SHA-512, RFC 8032 compatible)
def _try_native():
    global _native, _native_name
    mod = None
    name = None
    # 1. IRAM natmod from the filesystem (lib/ed25519_iram.mpy). A dynamically
    #    loaded natmod executes from IRAM; a built-in C module executes from
    #    flash XIP, which on ESP32-S3 measures ~80x slower for this code (the
    #    16KB icache + shared dcache thrash between Monocypher's inner loops
    #    and the PSRAM heap: verify 1461ms XIP vs 17.6ms IRAM). The distinct
    #    module name matters — a VFS .mpy can never shadow a registered
    #    built-in of the same name.
    #    MemoryError (IRAM exhausted) and ValueError (mpy ABI mismatch) fall
    #    through to the built-in, so a bad or missing file only costs speed.
    try:
        import ed25519_iram
        mod = ed25519_iram
        name = "iram"
    except (ImportError, MemoryError, ValueError, OSError):
        pass
    # 2. Built-in / platform C modules (flash XIP on esp32 — slow but present)
    if mod is None:
        try:
            if sys.platform == "esp32":
                import ed25519_fast_xtensawin
                mod = ed25519_fast_xtensawin
                name = "xip"
            elif sys.platform == "rp2":
                import ed25519_fast_armv6m
                mod = ed25519_fast_armv6m
                name = "xip"
            else:
                import ed25519_fast
                mod = ed25519_fast
                name = "xip"
        except ImportError:
            pass
    if mod is None:
        try:
            import ed25519_fast
            mod = ed25519_fast
            name = "xip"
        except ImportError:
            pass
    _native = mod
    _native_name = name if mod is not None else "pure-python"

_try_native()


def have_native():
    return _native is not None


def backend_name():
    """Which crypto backend actually loaded: 'iram' (fast, ~17.6ms/verify
    measured), 'xip' (built-in but flash-executed, ~80x slower, ~1.46s/verify
    measured), or 'pure-python' (no native module at all -- much slower
    again). Exists so this is checkable from outside this module, e.g. by
    a boot-time diagnostic that doesn't want to duplicate the selection
    logic to find out."""
    return _native_name


# Reported at NOTICE, the default log level (VERBOSE is 5, default is 3 --
# the previous version of this line was gated at VERBOSE, so it never
# printed under a normal boot no matter which backend loaded). And it now
# says WHICH backend, not just "a native module loaded": that distinction
# is the whole point, since 'iram' and 'xip' both count as "native" but
# differ by roughly 80x in practice. A node quietly running on the slow
# fallback -- IRAM exhausted, an ABI mismatch, a missing file -- would
# have looked identical to a healthy one in the old log line.
from ..log import log, LOG_NOTICE, LOG_WARNING
if _native_name == "iram":
    log("Ed25519/X25519: fast IRAM crypto active", LOG_NOTICE)
elif _native_name == "xip":
    log("Ed25519/X25519: SLOW crypto path active (native module present but "
        "not the fast IRAM one -- signing/verifying is roughly 80x slower "
        "than it should be; check lib/ed25519_iram.mpy is present and "
        "matches this board's firmware build)", LOG_WARNING)
else:
    log("Ed25519/X25519: NO native crypto module loaded -- running pure "
        "Python, far slower than either native path", LOG_WARNING)


class Ed25519PrivateKey:
    def __init__(self, seed):
        self.seed = seed
        if _native:
            self._pk = _native.publickey(seed)
        else:
            from .pure25519 import ed25519_oop as ed25519
            self.sk = ed25519.SigningKey(seed)

    @classmethod
    def generate(cls):
        return cls.from_private_bytes(os.urandom(32))

    @classmethod
    def from_private_bytes(cls, data):
        return cls(seed=data)

    def private_bytes(self):
        return self.seed

    def public_key(self):
        if _native:
            return Ed25519PublicKey.from_public_bytes(self._pk)
        return Ed25519PublicKey.from_public_bytes(self.sk.vk_s)

    def sign(self, message):
        if _native:
            return _native.sign(message, self.seed)
        return self.sk.sign(message)


class Ed25519PublicKey:
    def __init__(self, data):
        self._data = data
        if not _native:
            from .pure25519 import ed25519_oop as ed25519
            self.vk = ed25519.VerifyingKey(data)

    @classmethod
    def from_public_bytes(cls, data):
        return cls(data)

    def public_bytes(self):
        return self._data

    def verify(self, signature, message):
        if _native:
            if not _native.verify(signature, message, self._data):
                raise Exception("Ed25519 signature verification failed")
        else:
            self.vk.verify(signature, message)
