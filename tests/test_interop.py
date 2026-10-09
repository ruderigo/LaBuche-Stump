"""µReticulum (final_firmware/urns) against the reference Reticulum and LXMF.

A Stump node only works if what urns puts on the air is exactly what Sideband,
NomadNet and FireFly (all running the official rns/lxmf packages) expect, and
the other way round. These tests run urns under CPython (tests/shims stands in
for MicroPython's hardware modules) next to the real libraries and check that
both sides agree on keys, addresses, signatures, encryption and LXMF messages.
"""
import os

import pytest

import RNS
import LXMF
import urns
from urns import Identity as UIdentity, Destination as UDestination
from urns.lxmf import LXMessage as ULXMessage, LXMRouter as ULXMRouter


@pytest.fixture
def verify_like_the_board(monkeypatch):
    """On the board the native Ed25519 module is loaded, so incoming LXMF
    signatures are checked. Here urns runs its pure-Python fallback, where it
    skips the check by design (lxmf.py: verify_signatures = have_native())."""
    monkeypatch.setattr(ULXMRouter, "verify_signatures", True)


@pytest.fixture(scope="module")
def keypair():
    """One private key, loaded on both sides."""
    ref = RNS.Identity()
    mine = UIdentity(create_keys=False)
    mine.load_private_key(ref.get_private_key())
    return ref, mine


def test_same_private_key_gives_same_public_key_and_hash(keypair):
    ref, mine = keypair
    assert mine.get_public_key() == ref.get_public_key()
    assert mine.hash == ref.hash


def test_new_urns_identity_loads_in_reference():
    mine = UIdentity()
    ref = RNS.Identity.from_bytes(mine.get_private_key())
    assert ref.get_public_key() == mine.get_public_key()


@pytest.mark.parametrize("aspects", [("lxmf", "delivery"), ("lxmf", "propagation"), ("rrc", "hub")])
def test_destination_hashes_match(keypair, aspects):
    ref, mine = keypair
    app, *rest = aspects
    assert UDestination.hash(mine, app, *rest) == RNS.Destination.hash(ref, app, *rest)


def test_urns_signature_verifies_in_reference(keypair):
    ref, mine = keypair
    msg = os.urandom(300)
    assert ref.validate(mine.sign(msg), msg)


def test_reference_signature_verifies_in_urns(keypair):
    ref, mine = keypair
    msg = os.urandom(300)
    assert mine.validate(ref.sign(msg), msg)
    assert not mine.validate(ref.sign(msg), msg + b"x")


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 400])
def test_reference_encrypts_urns_decrypts(keypair, size):
    ref, mine = keypair
    sender_view = RNS.Identity(create_keys=False)
    sender_view.load_public_key(ref.get_public_key())
    plain = os.urandom(size)
    assert mine.decrypt(sender_view.encrypt(plain)) == plain


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 400])
def test_urns_encrypts_reference_decrypts(keypair, size):
    ref, mine = keypair
    sender_view = UIdentity(create_keys=False)
    sender_view.load_public_key(mine.get_public_key())
    plain = os.urandom(size)
    assert ref.decrypt(sender_view.encrypt(plain)) == plain


def _urns_dest(identity, direction):
    return UDestination(identity, direction, UDestination.SINGLE, "lxmf", "delivery")


def test_lxmf_message_from_urns_validates_in_reference_lxmf():
    """A Stump node's LXMF message, as Sideband/FireFly would receive it."""
    sender, receiver = UIdentity(), UIdentity()
    src = _urns_dest(sender, UDestination.IN)
    dst = _urns_dest(receiver, UDestination.OUT)
    m = ULXMessage(dst, src, "Bonjour de la Bûche ✓", "titre", fields={0x01: b"x"})
    m.pack()

    # The reference side knows the sender's public key, as after an announce.
    RNS.Identity.remember(os.urandom(32), src.hash, sender.get_public_key())
    got = LXMF.LXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated, got.unverified_reason
    assert got.content_as_string() == "Bonjour de la Bûche ✓"
    assert got.title_as_string() == "titre"
    assert got.hash == m.hash
    assert got.source_hash == src.hash and got.destination_hash == dst.hash


def test_lxmf_message_from_reference_validates_in_urns(verify_like_the_board):
    """A message from Sideband/FireFly, as the Stump node receives it."""
    sender, receiver = RNS.Identity(), RNS.Identity()
    # OUT so no running Reticulum instance is needed; it still signs with the full key.
    src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    dst = RNS.Destination(receiver, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    m = LXMF.LXMessage(dst, src, "Hola desde FireFly ✓", "título")
    m.pack()

    UIdentity.remember(os.urandom(32), src.hash, sender.get_public_key())
    got = ULXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated, got.unverified_reason
    assert got.content_as_string() == "Hola desde FireFly ✓"
    assert got.title_as_string() == "título"
    assert got.hash == m.hash


def test_tampered_lxmf_message_is_not_validated_by_urns(verify_like_the_board):
    sender, receiver = RNS.Identity(), RNS.Identity()
    # OUT so no running Reticulum instance is needed; it still signs with the full key.
    src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    dst = RNS.Destination(receiver, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    m = LXMF.LXMessage(dst, src, "original")
    m.pack()
    UIdentity.remember(os.urandom(32), src.hash, sender.get_public_key())
    tampered = m.packed.replace(b"original", b"0riginal")
    got = ULXMessage.unpack_from_bytes(tampered)
    assert not got.signature_validated
