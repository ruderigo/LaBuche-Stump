#!/bin/sh
# Builds the MicroPython Unix port the tests run on: the same MicroPython
# commit the suite was written against, with SINGLE-precision floats like
# the ESP32-S3 (a test that passes with doubles can still fail on the
# board). Usage: scripts/build_micropython.sh [DIR]   -> DIR/micropython
set -e
DIR="${1:-$HOME/micropython-f32}"
COMMIT=a129b2fba1a3348088c94d0462eef16d20874dea
if [ -x "$DIR/micropython" ]; then echo "$DIR/micropython"; exit 0; fi
SRC="$(mktemp -d)/micropython"
git init -q "$SRC"
git -C "$SRC" remote add origin https://github.com/micropython/micropython
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" checkout -q FETCH_HEAD
make -s -C "$SRC/ports/unix" submodules >/dev/null
make -s -C "$SRC/mpy-cross" -j4 >/dev/null
make -s -C "$SRC/ports/unix" BUILD=build-f32 -j4 \
     CFLAGS_EXTRA='-DMICROPY_FLOAT_IMPL=MICROPY_FLOAT_IMPL_FLOAT' >/dev/null
mkdir -p "$DIR"
cp "$SRC/ports/unix/build-f32/micropython" "$DIR/micropython"
"$DIR/micropython" -c "assert str(1/3) == '0.33333334', 'not single precision'"
echo "$DIR/micropython"
