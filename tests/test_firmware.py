"""Checks on final_firmware/ that need no board.

- Every firmware .py compiles with mpy-cross, MicroPython's own compiler, at
  the version the provisioner flashes (v1.28): CPython accepts syntax the
  board rejects, and the board only says so after a flash and a reboot.
- The provisioner's EXPECTED_FILE_HASHES lists every file and matches it,
  because the provisioner refuses to upload a folder that doesn't.
- Every walk-up string has French, English and Spanish with the same
  {placeholders}, and every key the code asks for exists (t() shows a
  missing key as the raw key name).
"""
import hashlib
import re
import shutil
import string
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FW = ROOT / "final_firmware"
FIRMWARE_PY = sorted(p for p in FW.rglob("*.py")
                     if "third_party" not in p.parts and "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", FIRMWARE_PY, ids=lambda p: str(p.relative_to(FW)))
def test_compiles_with_mpy_cross(path, tmp_path):
    mpy_cross = shutil.which("mpy-cross")
    if not mpy_cross:
        pytest.skip("mpy-cross not installed (pip install 'mpy-cross==1.28.*')")
    r = subprocess.run([mpy_cross, "-o", str(tmp_path / "out.mpy"), str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout


def _on_disk():
    return {str(f.relative_to(FW)): hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            for f in sorted(FW.rglob("*")) if f.is_file() and "__pycache__" not in f.parts}


def test_manifest_lists_every_file():
    import provisioner
    on_disk = _on_disk()
    assert sorted(set(on_disk) - set(provisioner.EXPECTED_FILE_HASHES)) == [], \
        "new files missing from EXPECTED_FILE_HASHES in provisioner.py"
    assert sorted(set(provisioner.EXPECTED_FILE_HASHES) - set(on_disk)) == [], \
        "EXPECTED_FILE_HASHES names files that no longer exist"


def test_manifest_hashes_match():
    """The provisioner's own pre-upload check passes on this folder."""
    import provisioner
    stale = provisioner._check_local_files_current(FW)
    assert stale == [], (
        "provisioner.py has stale hashes for %s; regenerate EXPECTED_FILE_HASHES "
        "(the command is in the comment above it)" % stale)


LANGS = {"fr", "en", "es"}


def _strings():
    import i18n
    return i18n.STRINGS


def _placeholders(text):
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name})


def test_every_string_has_all_three_languages():
    incomplete = {k: sorted(LANGS - set(v)) for k, v in _strings().items() if set(v) != LANGS}
    assert incomplete == {}


def test_placeholders_match_across_languages():
    mismatched = {k: {lang: _placeholders(v[lang]) for lang in v}
                  for k, v in _strings().items()
                  if len({tuple(_placeholders(v[lang])) for lang in v}) > 1}
    assert mismatched == {}


def test_every_string_formats():
    """t() calls .format() on every string, so {{ }} escaping must be valid."""
    for key, v in _strings().items():
        for lang, text in v.items():
            names = _placeholders(text)
            text.format(**{n: "x" for n in names})  # raises on a malformed string


def test_every_key_the_code_uses_exists():
    strings = _strings()
    lookup = re.compile(r"""\bt\(\s*["']([a-z0-9_]+)["']\s*([,)+])""")
    missing = {}
    for path in FIRMWARE_PY:
        for m in lookup.finditer(path.read_text(encoding="utf-8")):
            key, after = m.groups()
            if after == "+":  # built at runtime, e.g. "admin_theme_" + theme name
                if not any(k.startswith(key) for k in strings):
                    missing.setdefault(key + "*", []).append(path.name)
            elif key not in strings:
                missing.setdefault(key, []).append(path.name)
    assert missing == {}
