#!/usr/bin/env python3
"""Rewrites EXPECTED_FILE_HASHES in provisioner.py from final_firmware/
(the core files, and each built-in plugin's plugin.json, which carries
that plugin's own file hashes). Run after changing any firmware file;
tests/run.py fails until you do.

A file that leaves the manifest, and isn't shipped anywhere else in the
firmware, goes into RETIRED_FILES: updates remove it from the Stumps that
have it (1.0.0-beta.74). One that comes back leaves that list."""
import hashlib, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import provisioner

fw = ROOT / "final_firmware"
files = provisioner.manifest_candidates(fw)
body = "".join('    "%s": "%s",\n' % (f, hashlib.sha256((fw / f).read_bytes()).hexdigest()[:16]) for f in files)
src = (ROOT / "provisioner.py").read_text()
new = re.sub(r"EXPECTED_FILE_HASHES = \{.*?\n\}", lambda m: "EXPECTED_FILE_HASHES = {\n" + body + "}", src, flags=re.S)

shipped = set(files) | set(provisioner._board_files(fw))
left = [f for f in provisioner.EXPECTED_FILE_HASHES
        if f not in shipped and provisioner._belongs_on_board(f)]
retired = sorted((set(provisioner.RETIRED_FILES) | set(left)) - shipped)
block = "RETIRED_FILES = (\n" + "".join('    "%s",\n' % f for f in retired) + ")"
new = re.sub(r"RETIRED_FILES = \(\n.*?\n\)", lambda m: block, new, count=1, flags=re.S)

(ROOT / "provisioner.py").write_text(new)
print("%d files; %s" % (len(files), "changed" if new != src else "already current"))
for f in left:
    print("  retired: %s (updates remove it from the Stumps that have it)" % f)
