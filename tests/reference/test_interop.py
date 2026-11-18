"""µReticulum (final_firmware/urns) against the reference Reticulum and LXMF.

Run by .github/workflows/tests.yml twice (job "reference"): against the rns/lxmf
FireFly for Android pins, and the newest release.

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
def verify_like_the_board():
    """urns now checks every incoming LXMF signature (lxmf.py: unpack_from_bytes),
    on the board and here alike, so there's nothing to switch on."""


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


# ── Stamps and tickets (1.0.0-beta.53, opt-in) ─────────────────────────
# What a Sideband user who requires stamps checks on every message from
# the Stump: LXMessage.validate_stamp(cost, tickets).

from urns import lxmf as ulxmf


def _pair():
    sender, receiver = UIdentity(), UIdentity()
    src = _urns_dest(sender, UDestination.IN)
    dst = _urns_dest(receiver, UDestination.OUT)
    RNS.Identity.remember(os.urandom(32), src.hash, sender.get_public_key())
    return src, dst


@pytest.mark.parametrize("cost", [None, 8, 16])
def test_stamp_cost_read_from_a_reference_announce(cost):
    app_data = RNS.vendor.umsgpack.packb([b"Rod", cost, []])
    assert ulxmf.stamp_cost_from_app_data(app_data) == LXMF.stamp_cost_from_app_data(app_data)
    assert ulxmf.stamp_cost_from_app_data(b"Rod") is None          # the old announce format


def test_ticket_stamp_validates_in_reference():
    src, dst = _pair()
    ticket = os.urandom(ulxmf.TICKET_LENGTH)
    m = ULXMessage(dst, src, "réponse de la Bûche")
    m.outbound_ticket = ticket
    m.pack()
    got = LXMF.LXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated and got.hash == m.hash
    assert got.validate_stamp(24, tickets=[ticket])
    assert not got.validate_stamp(24, tickets=[os.urandom(16)])


def test_proof_of_work_stamp_validates_in_reference():
    src, dst = _pair()
    m = ULXMessage(dst, src, "stamped")
    m._hash_payload()
    job = ulxmf.StampJob(m.message_id, 8)
    while not job.step(1000):
        pass
    m.stamp = job.stamp
    m.pack()
    got = LXMF.LXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated and got.hash == m.hash
    assert got.validate_stamp(8)
    assert not got.validate_stamp(40)


def test_no_stamp_without_a_cost_or_a_ticket():
    src, dst = _pair()
    m = ULXMessage(dst, src, "plain")
    m.pack()
    got = LXMF.LXMessage.unpack_from_bytes(m.packed)
    assert got.stamp is None


def _router():
    r = ULXMRouter(identity=UIdentity())
    r.register_delivery_identity(r.identity, display_name="Stump")
    return r


def _ticketed_message(ticket, expires):
    sender, receiver = RNS.Identity(), RNS.Identity()
    src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    dst = RNS.Destination(receiver, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    m = LXMF.LXMessage(dst, src, "hi", fields={LXMF.FIELD_TICKET: [expires, ticket]})
    m.pack()
    UIdentity.remember(os.urandom(32), src.hash, sender.get_public_key())
    return ULXMessage.unpack_from_bytes(m.packed)


def test_ticket_from_a_reference_message_kept_only_when_switched_on(tmp_path):
    import time
    ticket = os.urandom(16)
    got = _ticketed_message(ticket, time.time() + 3600)
    r = _router()
    ulxmf.STAMPS[0] = False
    r._take_ticket(got)
    assert r.tickets == {}
    ulxmf.STAMPS[0] = True
    try:
        r.TICKETS_FILE = str(tmp_path / "t.bin")
        r._take_ticket(got)
        assert r._ticket_for(got.source_hash) == ticket
        r2 = _router()
        r2.load_tickets(r.TICKETS_FILE)
        assert r2._ticket_for(got.source_hash) == ticket            # kept across a restart
        old = _ticketed_message(os.urandom(16), time.time() - 1)
        r._take_ticket(old)
        assert old.source_hash not in r.tickets                     # expired: refused
    finally:
        ulxmf.STAMPS[0] = False


def test_what_the_router_does_per_recipient():
    r = _router()
    src, dst = _pair()
    h = dst.hash
    m = ULXMessage(dst, src, "x")
    ulxmf.STAMPS[0] = False
    r.stamp_costs[h] = 8
    assert r._stamp_first(m, h, dst, None) is False and m.outbound_ticket is None   # off: as before
    ulxmf.STAMPS[0] = True
    try:
        r.stamp_max_cost = 20
        assert r._stamp_first(m, h, dst, None) is True and r._stamp_queue[0][0] is m  # queued for a stamp
        r._stamp_queue.clear()
        r.stamp_max_cost = 0                                                           # a CAM
        m2 = ULXMessage(dst, src, "y")
        assert r._stamp_first(m2, h, dst, None) is False and r.unstamped[h] == 8
        r.tickets[h] = [9e12, os.urandom(16)]
        m3 = ULXMessage(dst, src, "z")
        assert r._stamp_first(m3, h, dst, None) is False and m3.outbound_ticket == r.tickets[h][1]
    finally:
        ulxmf.STAMPS[0] = False


def test_a_stump_app_tag_in_an_announce_is_ignored_by_reference_lxmf():
    """The fourth item FireFly is asked to add ("stump:firefly/0.6"):
    upstream LXMF still reads the name, the stamp cost and compression."""
    app_data = RNS.vendor.umsgpack.packb([b"Rod", 8, [], "stump:firefly/0.6"])
    assert LXMF.display_name_from_app_data(app_data) == "Rod"
    assert LXMF.stamp_cost_from_app_data(app_data) == 8
    import rrc
    assert rrc.app_tag_in(app_data)


# ---- packed text (urns/ztext.py) -------------------------------------------

def test_the_packed_text_flag_in_an_announce_is_ignored_by_reference_lxmf():
    """A Stump's own LXMF announce, with its fifth item {"z": 1}: upstream
    LXMF reads the name, the stamp cost and compression as it always did."""
    from urns import lxmf as ulxmf_mod
    r = _router()
    app_data = r._get_announce_app_data()
    assert RNS.vendor.umsgpack.unpackb(app_data) == [b"Stump", None, [], None, {"z": 1}]
    assert LXMF.display_name_from_app_data(app_data) == "Stump"
    assert LXMF.stamp_cost_from_app_data(app_data) is None
    assert LXMF.compression_support_from_app_data(app_data) is False
    assert ulxmf_mod.reads_packed(app_data)
    ref = RNS.vendor.umsgpack.packb([b"Rod", 8, [LXMF.SF_COMPRESSION], "stump:firefly-android/0.6.3.b.74", {"z": 1}])
    assert ulxmf_mod.reads_packed(ref) and ulxmf.stamp_cost_from_app_data(ref) == 8
    assert not ulxmf_mod.reads_packed(RNS.vendor.umsgpack.packb([b"Rod", 8, [LXMF.SF_COMPRESSION]]))


def _inflate_by_the_book(content):
    """Packed text read with nothing but zlib and the format's constants:
    what any other LXMF app would write to read it."""
    import zlib
    from urns import ztext
    assert content[0] == 0xFF
    n = len(ztext._DICT)
    stored = bytes((0, n & 255, n >> 8, ~n & 255, (~n >> 8) & 255)) + ztext._DICT
    d = zlib.decompressobj(-15)
    out = d.decompress(stored + ztext._HEAD + content[1:])
    assert d.eof and not d.unused_data
    return out[n:]


def test_a_packed_urns_message_reads_in_reference_lxmf():
    """urns packs for a peer that says "z"; reference LXMF carries the
    content as bytes, signature and id intact, and plain zlib reads it."""
    r = _router()
    src, dst = _pair()
    r.handle_announce(dst.hash, RNS.vendor.umsgpack.packb([b"Rod", None, [], "stump:firefly-android/0.6.3.b.74", {"z": 1}]))
    text = "<Rod@LaBuche> on se rejoint au camp à 18h, apporte les piles et de l'eau\n<alice> ok I'm on my way"
    content = r._packed(dst.hash, text)
    assert content[0] == 0xFF and len(content) < 0.7 * len(text.encode())
    m = ULXMessage(dst, src, content, title="#lora-mesh")
    m.pack()
    got = LXMF.LXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated and got.hash == m.hash
    assert got.content == content
    assert _inflate_by_the_book(got.content).decode() == text


def test_a_packed_reference_message_reads_in_urns():
    """The other way: FireFly (reference LXMF, zlib) to a Stump."""
    from urns import ztext
    sender, receiver = RNS.Identity(), RNS.Identity()
    src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    dst = RNS.Destination(receiver, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    text = "/msg alice est-ce que quelqu'un a des nouvelles de la tempête? on a perdu le courant depuis ce matin"
    packed = ztext.pack(text.encode())
    assert packed[0] == 0xFF
    m = LXMF.LXMessage(dst, src, packed, desired_method=LXMF.LXMessage.OPPORTUNISTIC)
    m.pack()
    assert m.method == LXMF.LXMessage.OPPORTUNISTIC
    UIdentity.remember(os.urandom(32), src.hash, sender.get_public_key())
    got = ULXMessage.unpack_from_bytes(m.packed)
    assert got.signature_validated and got.hash == m.hash
    _router()._unpack(got)
    assert got.content_as_string() == text


def test_more_text_fits_one_packet_packed():
    """Reference LXMF's own rule for one opportunistic packet (295 bytes of
    content): a batch too long for it plain fits packed."""
    from urns import ztext
    sender, receiver = RNS.Identity(), RNS.Identity()
    src = RNS.Destination(sender, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    dst = RNS.Destination(receiver, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
    batch = "\n".join([
        "<sam> Anyone know if the bridge on route 117 is open again? Heard it was closed after the storm last week.",
        "<lee> still closed as of this morning, take the detour through Val-d'Or",
        "<sam> thanks, that adds an hour but ok",
        "<lee> no problem, drive safe and call me when you get back to the station",
        "<kim> the weather is turning, wind picking up from the west and it looks like rain before dark",
    ])
    assert len(batch.encode()) > LXMF.LXMessage.ENCRYPTED_PACKET_MAX_CONTENT
    plain = LXMF.LXMessage(dst, src, batch, title="#lora-mesh", desired_method=LXMF.LXMessage.OPPORTUNISTIC)
    plain.pack()
    assert plain.method == LXMF.LXMessage.DIRECT          # a link, then the message: several packets
    packed = LXMF.LXMessage(dst, src, ztext.pack(batch.encode()), title="#lora-mesh",
                            desired_method=LXMF.LXMessage.OPPORTUNISTIC)
    packed.pack()
    assert packed.method == LXMF.LXMessage.OPPORTUNISTIC  # one packet


# ── Announce stamps (1.0.0-beta.86) ───────────────────────────────────
# Reticulum 1.6.1 puts a proof-of-work stamp on announces, and a node can
# enforce it. A Stump with ANNOUNCE_STAMP on adds its own: it must pass
# 1.6.1's own check under enforcement, its app data reading as before.

def _ref_announce(packet_raw):
    p = RNS.Packet(None, packet_raw)
    assert p.unpack()
    return p


def _stamped_urns_announce(app_data, cost):
    from urns import dstamp
    ident = UIdentity()
    d = UDestination(ident, UDestination.IN, UDestination.SINGLE, "nomadnetwork", "node")
    saved = (dstamp.COST, dstamp.ENABLED[0], dict(dstamp._stamps), dict(dstamp._wanted))
    dstamp.COST, dstamp.ENABLED[0] = cost, True
    try:
        dstamp.want(d)
        while not dstamp.step(200):
            pass
        pkt = d.announce(app_data=app_data, send=False)
        pkt.pack()
        return d, pkt.raw
    finally:
        dstamp.COST, dstamp.ENABLED[0] = saved[0], saved[1]
        dstamp._stamps.clear(); dstamp._stamps.update(saved[2])
        dstamp._wanted.clear(); dstamp._wanted.update(saved[3])


def _unstamped_urns_announce(app_data):
    d = UDestination(UIdentity(), UDestination.IN, UDestination.SINGLE, "nomadnetwork", "node")
    pkt = d.announce(app_data=app_data, send=False)
    pkt.pack()
    return d, pkt.raw


@pytest.mark.skipif(not hasattr(RNS.Transport, "STAMPS_ENFORCED"), reason="Reticulum before 1.6.1 has no announce stamps")
def test_a_stamped_urns_announce_passes_reference_stamp_enforcement():
    keep = (RNS.Transport.STAMPS_ENFORCED, RNS.Transport.DESTINATION_COST)
    # A light cost so the test is quick; the format and the check are 1.6.1's own.
    RNS.Transport.STAMPS_ENFORCED, RNS.Transport.DESTINATION_COST = True, 10
    try:
        d, raw = _stamped_urns_announce(b"Aleph node", 12)
        assert RNS.Identity.validate_announce(_ref_announce(raw))
        assert RNS.Identity.known_destinations[d.hash][3] == b"Aleph node"      # the stamp left out, as 1.6.1 does
        u, raw = _unstamped_urns_announce(b"Bet node")
        assert not RNS.Identity.validate_announce(_ref_announce(raw))
        RNS.Transport.STAMPS_ENFORCED = False
        u, raw = _unstamped_urns_announce(b"Bet node")        # (a new one: 1.6.1 remembers the one it refused)
        assert RNS.Identity.validate_announce(_ref_announce(raw))       # not enforced: as before
        assert RNS.Identity.known_destinations[u.hash][3] == b"Bet node"
    finally:
        RNS.Transport.STAMPS_ENFORCED, RNS.Transport.DESTINATION_COST = keep


def test_a_stamp_at_the_stumps_cost_meets_reference_defaults():
    """COST 25 is 1.6.1's DESTINATION_VALUE: above the cost a node
    enforcing stamps asks by default (23.5)."""
    from urns import dstamp
    assert dstamp.COST == 25 and dstamp.EXT_LEN == 18
    if hasattr(RNS.Transport, "DESTINATION_VALUE"):
        assert dstamp.COST >= RNS.Transport.DESTINATION_VALUE >= RNS.Transport.DESTINATION_COST
