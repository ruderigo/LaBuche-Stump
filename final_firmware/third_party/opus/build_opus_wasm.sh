#!/bin/sh
# Rebuilds web/opus.wasm: libopus 1.5.2 (https://github.com/xiph/opus, tag
# v1.5.2, BSD-style licence -- see COPYING), unmodified, plus stump_opus.c.
# Portable floating-point core only: no CPU-specific or DNN (DRED/OSCE) code.
# Needs git, clang, wasm-ld and wasi-libc
# (Ubuntu: apt install clang lld wasi-libc libclang-rt-18-dev-wasm32).
set -e
cd "$(dirname "$0")"
HERE="$(pwd)"
[ -d opus-src ] || git clone -q --depth 1 --branch v1.5.2 https://github.com/xiph/opus opus-src
cd opus-src
list() { sed -e ':a' -e '/\\$/N; s/\\\n//; ta' "$1" | sed -n "s/^$2 *= *//p"; }
FILES="$(list celt_sources.mk CELT_SOURCES) $(list silk_sources.mk SILK_SOURCES) \
$(list silk_sources.mk SILK_SOURCES_FLOAT) $(list opus_sources.mk OPUS_SOURCES_FLOAT) \
src/opus.c src/opus_decoder.c src/opus_encoder.c src/extensions.c src/repacketizer.c"
clang --target=wasm32-wasi --sysroot=/usr -I/usr/include/wasm32-wasi -L/usr/lib/wasm32-wasi -O2 -DNDEBUG \
  -DOPUS_BUILD -DUSE_ALLOCA -DHAVE_LRINTF -DHAVE_LRINT -DPACKAGE_VERSION='"1.5.2"' \
  -Iinclude -Icelt -Isilk -Isilk/float -Isrc \
  -nostartfiles -Wl,--no-entry -Wl,--export-dynamic -Wl,--strip-all \
  -Wl,--initial-memory=4194304 -Wl,--max-memory=33554432 \
  -o "$HERE/../../web/opus.wasm" "$HERE/stump_opus.c" $FILES -lm
echo "built ../../web/opus.wasm"
