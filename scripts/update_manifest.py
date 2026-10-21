#!/usr/bin/env python3
"""Rewrites EXPECTED_FILE_HASHES in provisioner.py from final_firmware/
(the core files, and each built-in plugin's plugin.json, which carries
that plugin's own file hashes). Run after changing any firmware file;
tests/run.py fails until you do."""
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
(ROOT / "provisioner.py").write_text(new)
print("%d files; %s" % (len(files), "changed" if new != src else "already current"))
