#!/bin/sh
# Rebuilds web/codec2.wasm from these sources (Codec 2 1.2.0, LGPL-2.1,
# https://github.com/drowe67/codec2 tag 1.2.0, unmodified) plus
# stump_codec2.c. Needs clang, wasm-ld and wasi-libc
# (Ubuntu: apt install clang lld wasi-libc libclang-rt-18-dev-wasm32).
# generated/ holds the codebook tables Codec 2's own build generates.
set -e
cd "$(dirname "$0")"
clang --target=wasm32-wasi --sysroot=/usr -I/usr/include/wasm32-wasi -L/usr/lib/wasm32-wasi -O2 -DNDEBUG \
  -Isrc -Igenerated -nostartfiles -Wl,--no-entry -Wl,--export-dynamic -Wl,--strip-all \
  -Wl,--initial-memory=4194304 -Wl,--max-memory=33554432 \
  -o ../../web/codec2.wasm stump_codec2.c src/*.c generated/*.c -lm
echo "built ../../web/codec2.wasm"
