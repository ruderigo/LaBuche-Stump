# µReticulum HMAC
# Based on Reticulum's HMAC.py (from Python 3.10.4)
# Modified to NOT require .copy() on hash objects (MicroPython uhashlib lacks it)

_trans_5C = bytes((x ^ 0x5C) for x in range(256))
_trans_36 = bytes((x ^ 0x36) for x in range(256))

digest_size = None

_PADS = {}


def _xor_pad(key, byte):
    """bytes(b ^ byte for b in key), computed as one integer XOR."""
    n = len(key)
    mask = _PADS.get((byte, n))
    if mask is None:
        mask = _PADS[(byte, n)] = int.from_bytes(bytes((byte,)) * n, "big")
    return (int.from_bytes(key, "big") ^ mask).to_bytes(n, "big")


class HMAC:
    blocksize = 64

    def __init__(self, key, msg=None, digestmod=None):
        if not isinstance(key, (bytes, bytearray)):
            raise TypeError("key: expected bytes or bytearray")

        if digestmod is None:
            from uhashlib import sha256
            digestmod = sha256

        self._digest_cons = digestmod

        inner = self._digest_cons()
        self.digest_size = inner.digest_size if hasattr(inner, 'digest_size') else 32

        if hasattr(inner, 'block_size'):
            blocksize = inner.block_size
            if blocksize < 16:
                blocksize = self.blocksize
        else:
            blocksize = self.blocksize

        self.block_size = blocksize

        if len(key) > blocksize:
            key = self._digest_cons(key).digest()

        key = key + b'\x00' * (blocksize - len(key))

        # Store padded keys for reconstruction (avoids .copy() requirement).
        # XOR as one big integer, in C: the byte-by-byte generator this
        # replaces cost ~1.3 ms per HMAC on a CAM.
        self._outer_key_pad = _xor_pad(key, 0x5C)
        self._inner_key_pad = _xor_pad(key, 0x36)

        # Build inner hash
        self._inner = self._digest_cons()
        self._inner.update(self._inner_key_pad)
        if msg is not None:
            self._inner.update(msg)

    def update(self, msg):
        self._inner.update(msg)

    def digest(self):
        # Recreate outer hash from scratch (no .copy() needed)
        outer = self._digest_cons()
        outer.update(self._outer_key_pad)
        outer.update(self._inner.digest())
        return outer.digest()

    def hexdigest(self):
        import binascii
        return str(binascii.hexlify(self.digest()), 'ascii')


def new(key, msg=None, digestmod=None):
    return HMAC(key, msg, digestmod)


def digest(key, msg, digest_func):
    if digest_func is None:
        from uhashlib import sha256
        digest_func = sha256

    digest_cons = digest_func

    inner = digest_cons()
    outer = digest_cons()
    blocksize = getattr(inner, 'block_size', 64)
    if len(key) > blocksize:
        key = digest_cons(key).digest()

    key = key + b'\x00' * (blocksize - len(key))
    inner.update(_xor_pad(key, 0x36))
    outer.update(_xor_pad(key, 0x5C))
    inner.update(msg)
    outer.update(inner.digest())
    return outer.digest()
