# Fast Ed25519/X25519 for Stump on a computer (docs/PC.md): the native
# module urns/crypto looks for, built on the 'cryptography' package
# instead of the board's C code. Same five functions, same results
# (Ed25519 is deterministic: RFC 8032).
BACKEND = "host"

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def publickey(seed):
    return Ed25519PrivateKey.from_private_bytes(bytes(seed)).public_key().public_bytes(*_RAW)


def sign(message, seed):
    return Ed25519PrivateKey.from_private_bytes(bytes(seed)).sign(bytes(message))


def verify(signature, message, public_key):
    try:
        Ed25519PublicKey.from_public_bytes(bytes(public_key)).verify(bytes(signature), bytes(message))
        return True
    except (InvalidSignature, ValueError):
        return False


def x25519_publickey(private):
    return X25519PrivateKey.from_private_bytes(bytes(private)).public_key().public_bytes(*_RAW)


def x25519(private, peer_public):
    return X25519PrivateKey.from_private_bytes(bytes(private)).exchange(
        X25519PublicKey.from_public_bytes(bytes(peer_public)))
