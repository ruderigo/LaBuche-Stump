#!/usr/bin/env python3
"""Signs and checks a release's SHA256SUMS with a Reticulum identity
(Ed25519) -- the same kind of key every Stump, FireFly and Sideband
already uses, so a release can be checked with nothing but Reticulum,
offline, even on a handheld.

    pip install rns        (or rnspure)

    python3 scripts/sign_release.py keygen release.identity
        Makes the release identity, once. Keep the file secret; put its
        base64 in the repository secret STUMP_RELEASE_IDENTITY, and commit
        the public key it prints as RELEASE_SIGNING_KEY.txt.

    python3 scripts/sign_release.py sign release.identity dist/SHA256SUMS
        Writes dist/SHA256SUMS.rnsig (hex Ed25519 signature).

    python3 scripts/sign_release.py verify SHA256SUMS SHA256SUMS.rnsig RELEASE_SIGNING_KEY.txt
        Checks it. Exit status 0 only if the signature is good.
"""
import sys
from pathlib import Path


def _rns():
    try:
        import RNS
        return RNS
    except ImportError:
        sys.exit("Reticulum isn't installed: pip install rns   (or rnspure)")


def _pubkey(arg):
    p = Path(arg)
    text = p.read_text() if p.is_file() else arg
    for line in text.splitlines():
        line = line.strip()
        if len(line) == 128 and all(c in "0123456789abcdef" for c in line.lower()):
            return bytes.fromhex(line)
    sys.exit("no 128-hex-digit public key in %s" % arg)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    RNS = _rns()
    if cmd == "keygen":
        out = Path(sys.argv[2])
        if out.exists():
            sys.exit("%s already exists -- not overwriting a key" % out)
        ident = RNS.Identity()
        ident.to_file(str(out))
        import base64
        print("Identity file:   %s  (secret: keep it safe)" % out)
        print("Identity hash:   %s" % ident.hexhash)
        print("Public key:      %s" % ident.get_public_key().hex())
        print("Secret value for STUMP_RELEASE_IDENTITY:")
        print(base64.b64encode(out.read_bytes()).decode())
    elif cmd == "sign" and len(sys.argv) == 4:
        ident = RNS.Identity.from_file(sys.argv[2])
        if ident is None:
            sys.exit("couldn't load the identity in %s" % sys.argv[2])
        data = Path(sys.argv[3]).read_bytes()
        sig = ident.sign(data)
        Path(sys.argv[3] + ".rnsig").write_text(sig.hex() + "\n")
        print("signed %s with %s" % (sys.argv[3], ident.hexhash))
        print("public key: %s" % ident.get_public_key().hex())
    elif cmd == "verify" and len(sys.argv) == 5:
        data = Path(sys.argv[2]).read_bytes()
        sig = bytes.fromhex(Path(sys.argv[3]).read_text().strip())
        check = RNS.Identity(create_keys=False)
        check.load_public_key(_pubkey(sys.argv[4]))
        if check.validate(sig, data):
            print("GOOD signature on %s by %s" % (sys.argv[2], check.hexhash))
        else:
            sys.exit("BAD signature on %s" % sys.argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
