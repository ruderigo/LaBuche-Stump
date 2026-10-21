"""CPython stand-in for MicroPython's ucryptolib.aes (tests only).
Only the mode urns uses: CBC (2), no padding, one-shot encrypt/decrypt."""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MODE_ECB, MODE_CBC = 1, 2


class aes:
    def __init__(self, key, mode, iv=None):
        if mode != MODE_CBC:
            raise ValueError("only CBC is emulated")
        self._key, self._iv = bytes(key), bytes(iv)

    def encrypt(self, data):
        e = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        return e.update(bytes(data)) + e.finalize()

    def decrypt(self, data):
        d = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
        return d.update(bytes(data)) + d.finalize()
