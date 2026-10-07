// Codec 2 in the browser, for Stump voice notes. Loads codec2.wasm (Codec 2
// 1.2.0, LGPL-2.1, compiled unmodified) and encodes/decodes whole notes.
// Its source, licence and build script: third_party/codec2/ in the Stump
// project (https://github.com/drowe67/codec2, tag 1.2.0).
// Modes are LXMF's numbers: 3 = 700C, 4 = 1200, 5 = 1300, 6 = 1400,
// 7 = 1600, 8 = 2400, 9 = 3200. Audio is 8 kHz mono 16-bit PCM.
var Codec2 = (function () {
  var ex = null, loading = null;

  // The module only needs these three from its host, for C's stdio; there
  // is nowhere to print to, so writes are accepted and dropped.
  function wasi() {
    return { wasi_snapshot_preview1: {
      fd_close: function () { return 0; },
      fd_seek: function () { return 0; },
      fd_write: function (fd, iovs, n, nwritten) {
        var dv = new DataView(ex.memory.buffer), total = 0;
        for (var i = 0; i < n; i++) total += dv.getUint32(iovs + i * 8 + 4, true);
        dv.setUint32(nwritten, total, true);
        return 0;
      } } };
  }

  function load(bytesOrUrl) {
    if (ex) return Promise.resolve();
    if (loading) return loading;
    var get = (typeof bytesOrUrl === 'string')
      ? fetch(bytesOrUrl).then(function (r) { if (!r.ok) throw new Error('codec2.wasm ' + r.status); return r.arrayBuffer(); })
      : Promise.resolve(bytesOrUrl);
    loading = get.then(function (buf) { return WebAssembly.instantiate(buf, wasi()); })
                 .then(function (r) { ex = r.instance.exports; });
    return loading;
  }

  function frameInfo(mode) {
    return { spf: ex.c2_samples_per_frame(mode), bpf: ex.c2_bytes_per_frame(mode) };
  }

  // pcm: Int16Array at 8 kHz. Returns a Uint8Array of raw Codec 2 frames.
  function encode(mode, pcm) {
    var fi = frameInfo(mode);
    if (!fi.spf) throw new Error('unsupported mode ' + mode);
    var frames = Math.ceil(pcm.length / fi.spf), cap = frames * fi.bpf;
    var pIn = ex.c2_alloc(pcm.length * 2), pOut = ex.c2_alloc(cap);
    new Int16Array(ex.memory.buffer, pIn, pcm.length).set(pcm);
    var n = ex.c2_encode_note(mode, pIn, pcm.length, pOut, cap);
    var out = new Uint8Array(ex.memory.buffer, pOut, Math.max(n, 0)).slice();
    ex.c2_free(pIn); ex.c2_free(pOut);
    return out;
  }

  // bits: Uint8Array of raw frames. Returns an Int16Array at 8 kHz.
  function decode(mode, bits) {
    var fi = frameInfo(mode);
    if (!fi.spf) throw new Error('unsupported mode ' + mode);
    var frames = Math.floor(bits.length / fi.bpf), cap = frames * fi.spf;
    var pIn = ex.c2_alloc(Math.max(bits.length, 1)), pOut = ex.c2_alloc(Math.max(cap * 2, 2));
    new Uint8Array(ex.memory.buffer, pIn, bits.length).set(bits);
    var n = ex.c2_decode_note(mode, pIn, bits.length, pOut, cap);
    var out = new Int16Array(ex.memory.buffer, pOut, Math.max(n, 0)).slice();
    ex.c2_free(pIn); ex.c2_free(pOut);
    return out;
  }

  // Seconds of audio in a note, from its size alone (no decoding).
  function seconds(mode, nbytes) {
    var t = { 3: [4, 40], 4: [6, 40], 5: [7, 40], 6: [7, 40], 7: [8, 40], 8: [6, 20], 9: [8, 20] }[mode];
    return t ? Math.floor(nbytes / t[0]) * t[1] / 1000 : 0;
  }

  return { load: load, encode: encode, decode: decode, seconds: seconds,
           ready: function () { return !!ex; } };
})();
if (typeof module !== 'undefined') module.exports = Codec2;
