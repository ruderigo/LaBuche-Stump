# The ESP32's hardware AES (ucryptolib), on a computer: the 'cryptography'
# package (docs/PC.md). urns uses AES-CBC (mode 2); ECB (1) for completeness.
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class aes:
    def __init__(self, key, mode, iv=None):
        m = modes.ECB() if mode == 1 else modes.CBC(bytes(iv))
        c = Cipher(algorithms.AES(bytes(key)), m)
        self._enc, self._dec = c.encryptor(), c.decryptor()

    def encrypt(self, data, out=None):
        return self._enc.update(bytes(data))

    def decrypt(self, data, out=None):
        return self._dec.update(bytes(data))
