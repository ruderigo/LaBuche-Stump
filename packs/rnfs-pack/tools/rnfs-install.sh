#!/bin/sh
# Installs rnfs from the files next to this script -- no internet needed.
# macOS and Linux, Python 3.11 or newer. Run:  sh rnfs-install.sh
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
WHEELS="rnspure-1.5.7-py3-none-any.whl lxmf-1.2.0-py3-none-any.whl rnfs-0.1.1-py3-none-any.whl"
for w in $WHEELS; do
  [ -f "$w" ] || { echo "Missing $w -- download it from the Stump's Tools page into this folder."; exit 1; }
done
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null || {
  echo "rnfs needs Python 3.11 or newer ($PY is older, or missing)."; exit 1; }
VENV="$HOME/.rnfs-venv"
if "$PY" -m venv "$VENV" 2>/dev/null; then
  "$VENV/bin/python" -m pip install --quiet --no-index --no-deps $WHEELS
  echo "rnfs is installed: $VENV/bin/rnfs"
  echo "Add it to your PATH, e.g.:  ln -s $VENV/bin/rnfs ~/.local/bin/rnfs"
else
  echo "(Python's venv module isn't available: installing for this user instead.)"
  "$PY" -m pip install --quiet --user --no-index --no-deps --break-system-packages $WHEELS 2>/dev/null \
    || "$PY" -m pip install --quiet --user --no-index --no-deps $WHEELS
  echo "rnfs is installed (~/.local/bin/rnfs)."
fi
echo "Start it with:  rnfs daemon     then, in another window:  rnfs get <cid> -o <file>"
