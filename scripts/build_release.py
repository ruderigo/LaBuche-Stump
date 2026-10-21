#!/usr/bin/env python3
"""Builds a Stump release's files, reproducibly.

    python3 scripts/build_release.py 1.0.0-beta.1 dist/

Writes to dist/:

  Stump-<version>.zip         everything a technician needs, in one folder:
                              provisioner.py, final_firmware/, packs/, pc/, bench/,
                              README, CHANGELOG, LICENSE, and what checks
                              a release (docs/RELEASE.md, the signing key,
                              scripts/sign_release.py)
  provisioner.py              the same Provisioner, on its own
  <pack>-pack-<v>.zip         each pack in packs/, for packs/ next to the
                              Provisioner (or --install-pack)
  MANIFEST.json               every file inside every zip: size and SHA-256
  SHA256SUMS                  the release files' SHA-256 (sha256sum -c)

Reproducible: files sorted, timestamps fixed (SOURCE_DATE_EPOCH, else the
last commit's time), permissions normalised. The same commit gives
byte-identical zips, so anyone can rebuild a release and compare hashes.
"""
import hashlib, json, os, subprocess, sys, time, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"__pycache__", ".git", ".DS_Store"}
SKIP_FILES = {".DS_Store", "plugins_state.json", "plugins_loading", "boot_fail_count", "themes.json"}


def epoch():
    if os.environ.get("SOURCE_DATE_EPOCH"):
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        return int(subprocess.check_output(["git", "-C", str(ROOT), "log", "-1", "--format=%ct"]).strip())
    except Exception:
        return 1735689600   # 2025-01-01


def files_under(base):
    out = []
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or p.suffix == ".pyc":
            continue
        if p.is_file():
            out.append(p)
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def write_zip(dest, entries, stamp, manifest):
    """entries: [(source_path, name_in_zip)]"""
    dt = time.gmtime(max(stamp, 315532800))[:6]
    listing = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for src, name in sorted(entries, key=lambda e: e[1]):
            info = zipfile.ZipInfo(name, date_time=dt)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if os.access(src, os.X_OK) and src.suffix in (".sh", ".py") else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.create_system = 3
            data = Path(src).read_bytes()
            zf.writestr(info, data)
            listing.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest[dest.name] = listing


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    version, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    stamp = epoch()
    manifest = {}
    top = "Stump-%s/" % version

    # With the signature checker and the public key, so a release can be
    # checked offline from the folder itself (docs/RELEASE.md).
    entries = [(ROOT / n, top + n) for n in ("provisioner.py", "README.md", "CHANGELOG.md", "LICENSE",
                                             "RELEASE_SIGNING_KEY.txt", "docs/RELEASE.md",
                                             "scripts/sign_release.py")
               if (ROOT / n).is_file()]
    # pc/: Stump on a computer (provisioner.py --pc, docs/PC.md).
    # bench/: measurements run on a CAM (tls_probe.py: docs/HTTPS_SETUP.md step 0).
    for base in ("final_firmware", "packs", "pc", "bench"):
        entries += [(p, top + p.relative_to(ROOT).as_posix()) for p in files_under(ROOT / base) if p.suffix != ".zip"]
    write_zip(out / ("Stump-%s.zip" % version), entries, stamp, manifest)

    (out / "provisioner.py").write_bytes((ROOT / "provisioner.py").read_bytes())

    for pack in sorted((ROOT / "packs").iterdir()):
        spec_file = pack / "pack.json"
        if not spec_file.is_file():
            continue
        spec = json.loads(spec_file.read_text())
        name = "%s-pack-%s.zip" % (spec["name"], spec["version"])
        write_zip(out / name, [(p, pack.name + "/" + p.relative_to(pack).as_posix()) for p in files_under(pack)],
                  stamp, manifest)

    (out / "MANIFEST.json").write_text(json.dumps({"version": version, "source_date_epoch": stamp,
                                                   "files": manifest}, indent=1, sort_keys=True) + "\n")
    names = sorted(p.name for p in out.iterdir() if p.is_file() and p.name not in ("SHA256SUMS",)
                   and not p.name.startswith("SHA256SUMS."))
    (out / "SHA256SUMS").write_text("".join("%s  %s\n" % (sha256(out / n), n) for n in names))
    print((out / "SHA256SUMS").read_text(), end="")


if __name__ == "__main__":
    main()
