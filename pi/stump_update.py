#!/usr/bin/env python3
"""Stump OS updater (1.0.0-beta.87): a Raspberry Pi set up from the image
brought up to the newest release in place, nothing lost.

Run by stump-update.service (as the stump user) when /admin asks: the
.update-request file in the Stump folder (a systemd path unit watches it).

  1. The newest release on GitHub, betas included (the testers' line).
  2. Its Stump-<version>.zip, SHA256SUMS and SHA256SUMS.rnsig downloaded.
  3. SHA256SUMS checked against its signature with the release key of the
     source already on the Pi -- never one that came with the download --
     and the zip against SHA256SUMS. Anything that doesn't check: nothing
     changes.
  4. The zip unpacked beside the old source, and its Provisioner run on
     the Stump folder as an update (--pc FOLDER --answers): firmware
     replaced; settings, identity, packs, rooms and files kept. The
     running Stump restarts on the new files by itself (.updated).
  5. The new source takes the old one's place, for the next update.

What happened goes in update-status.json, which /admin shows.

    python3 stump_update.py STUMP_FOLDER [--api URL] [--src DIR]
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = "ruderigo/SandBox_Stump_On"
API = "https://api.github.com/repos/%s/releases?per_page=15" % REPO
HERE = Path(__file__).resolve().parent            # <src>/pi
REQUEST = ".update-request"
STATUS = "update-status.json"


def _version_key(v):
    """1.0.0-beta.87 -> sortable; a release without -beta after its betas."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$", v or "")
    if not m:
        return None
    a, b, c, beta = m.groups()
    return (int(a), int(b), int(c), int(beta) if beta is not None else 10 ** 6)


def current_release(folder):
    try:
        text = (Path(folder) / "example_node.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'^STUMP_RELEASE\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else None


def _status(folder, state, **kw):
    d = {"state": state, "at": int(time.time())}
    d.update(kw)
    try:
        tmp = Path(folder) / (STATUS + ".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        os.replace(str(tmp), str(Path(folder) / STATUS))
    except OSError:
        pass
    print("[stump-update] %s %s" % (state, json.dumps(kw)))


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "stump-os-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def newest_release(api=API):
    """(version, {asset name: download url}) of the newest release that
    carries what an update needs, or (None, None)."""
    best = (None, None)
    for rel in json.loads(_get(api).decode("utf-8")):
        if rel.get("draft"):
            continue
        v = (rel.get("tag_name") or "").lstrip("v")
        k = _version_key(v)
        if k is None:
            continue
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets") or []}
        if not all(n in assets for n in ("Stump-%s.zip" % v, "SHA256SUMS", "SHA256SUMS.rnsig")):
            continue
        if best[0] is None or k > _version_key(best[0]):
            best = (v, assets)
    return best


def release_key(src):
    """The release signing key of the source already on this Pi."""
    text = (Path(src) / "RELEASE_SIGNING_KEY.txt").read_text()
    for line in text.splitlines():
        line = line.strip().lower()
        if len(line) == 128 and all(c in "0123456789abcdef" for c in line):
            return bytes.fromhex(line)
    raise ValueError("no release key")


def signature_ok(data, sig_hex, public_key):
    """A Reticulum identity's signature: Ed25519 with the second half of its
    64-byte public key (the first half is its X25519 key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(public_key[32:]).verify(bytes.fromhex(sig_hex.strip()), data)
        return True
    except (InvalidSignature, ValueError):
        return False


def listed_hash(sums_text, name):
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def update(folder, api=API, src=None, python=None):
    """Brings the Stump in `folder` up to the newest release. Returns the
    state written to update-status.json."""
    folder = Path(folder)
    src = Path(src or HERE.parent)
    try:
        (folder / REQUEST).unlink()
    except OSError:
        pass
    _status(folder, "checking")
    have = current_release(folder)
    try:
        v, assets = newest_release(api)
    except Exception as e:
        return _fail(folder, "couldn't reach GitHub: %s" % e)
    if v is None:
        return _fail(folder, "no release found")
    if have and _version_key(have) and _version_key(v) <= _version_key(have):
        _status(folder, "current", release=have)
        return "current"
    tmp = Path(tempfile.mkdtemp(prefix="stump-update-"))
    try:
        name = "Stump-%s.zip" % v
        try:
            for n in (name, "SHA256SUMS", "SHA256SUMS.rnsig"):
                (tmp / n).write_bytes(_get(assets[n], timeout=600))
        except Exception as e:
            return _fail(folder, "download failed: %s" % e)
        sums = (tmp / "SHA256SUMS").read_bytes()
        try:
            key = release_key(src)
        except (OSError, ValueError) as e:
            return _fail(folder, "no release key on this Pi: %s" % e)
        if not signature_ok(sums, (tmp / "SHA256SUMS.rnsig").read_text(), key):
            return _fail(folder, "the release's signature doesn't check")
        want = listed_hash(sums.decode("utf-8", "replace"), name)
        got = hashlib.sha256((tmp / name).read_bytes()).hexdigest()
        if not want or want != got:
            return _fail(folder, "the download doesn't match the signed list")
        new = src.parent / (src.name + ".new")
        shutil.rmtree(new, ignore_errors=True)
        with zipfile.ZipFile(tmp / name) as z:
            for m in z.namelist():
                p = Path(m)
                if p.is_absolute() or ".." in p.parts:
                    return _fail(folder, "unsafe path in the zip")
            z.extractall(tmp / "x")
        tops = [d for d in (tmp / "x").iterdir() if d.is_dir()]
        if len(tops) != 1 or not (tops[0] / "provisioner.py").is_file():
            return _fail(folder, "the zip isn't a Stump release")
        shutil.move(str(tops[0]), str(new))
        answers = new / "pi" / "update-answers.json"
        if not answers.is_file():
            answers = HERE / "update-answers.json"
        r = subprocess.run([python or sys.executable, str(new / "provisioner.py"), "--pc", str(folder),
                            "--answers", str(answers)],
                           cwd=str(new), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            shutil.rmtree(new, ignore_errors=True)
            return _fail(folder, "the Provisioner's update failed: " + (r.stdout + r.stderr).strip()[-300:])
        old = src.parent / (src.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if src.exists():
            os.replace(str(src), str(old))
        os.replace(str(new), str(src))
        shutil.rmtree(old, ignore_errors=True)
        _status(folder, "updated", release=v, previous=have or "")
        return "updated"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fail(folder, why):
    _status(folder, "failed", why=why)
    return "failed"


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    api, src = API, None
    if "--api" in argv:
        api = argv[argv.index("--api") + 1]
    if "--src" in argv:
        src = argv[argv.index("--src") + 1]
    state = update(argv[1], api=api, src=src, python=os.environ.get("STUMP_PROVISIONER_PYTHON"))
    sys.exit(0 if state in ("updated", "current") else 1)


if __name__ == "__main__":
    main(sys.argv)
