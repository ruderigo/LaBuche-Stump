// Opus voice notes in the browser, for the Stump's chat. Loads opus.wasm
// (libopus 1.5.2, BSD-style licence, compiled unmodified) and wraps its
// packets in Ogg Opus files exactly as RFC 7845 defines them -- what FireFly
// sends and plays as LXMF audio mode 16 (FireFly client quickstart 0.2.10).
// libopus licence and build script: third_party/opus/ in the Stump project.
//
//   OpusHead page, OpusTags page, then ~1 s of 60 ms packets per page; the
//   pre-skip from the encoder's lookahead; the last page's granule position
//   trims the padding; end-of-stream flag on the last page.
// Encoding: mono 16 kHz, 8 kbit/s constrained VBR, VOIP, complexity 10.
// Decoding: mono at 48 kHz (a stereo file is mixed down by libopus).
var Opus = (function () {
  var ex = null, loading = null;

  function load(bytesOrUrl) {
    if (ex) return Promise.resolve();
    if (loading) return loading;
    var get = (typeof bytesOrUrl === 'string')
      ? fetch(bytesOrUrl).then(function (r) { if (!r.ok) throw new Error('opus.wasm ' + r.status); return r.arrayBuffer(); })
      : Promise.resolve(bytesOrUrl);
    loading = get.then(function (buf) { return WebAssembly.instantiate(buf, {}); })
                 .then(function (r) { ex = r.instance.exports; });
    return loading;
  }

  // ---- Ogg CRC32: polynomial 0x04C11DB7, no reflection, initial value 0 ----
  var CRC = (function () {
    var t = new Uint32Array(256);
    for (var i = 0; i < 256; i++) {
      var r = i << 24;
      for (var k = 0; k < 8; k++) r = (r & 0x80000000) ? ((r << 1) ^ 0x04C11DB7) : (r << 1);
      t[i] = r >>> 0;
    }
    return t;
  })();
  function crc32(b) {
    var c = 0;
    for (var i = 0; i < b.length; i++) c = ((c << 8) ^ CRC[((c >>> 24) ^ b[i]) & 0xff]) >>> 0;
    return c >>> 0;
  }

  function page(packets, granule, serial, seq, flags) {
    var lacing = [], size = 0, i, j;
    packets.forEach(function (p) {
      for (j = p.length; j >= 255; j -= 255) lacing.push(255);
      lacing.push(j);
      size += p.length;
    });
    var out = new Uint8Array(27 + lacing.length + size), dv = new DataView(out.buffer);
    out.set([0x4f, 0x67, 0x67, 0x53, 0, flags]);
    // granule position: 64-bit little-endian (fits easily in 2^53)
    dv.setUint32(6, granule % 4294967296, true);
    dv.setUint32(10, Math.floor(granule / 4294967296), true);
    dv.setUint32(14, serial, true);
    dv.setUint32(18, seq, true);
    out[26] = lacing.length;
    out.set(lacing, 27);
    var at = 27 + lacing.length;
    for (i = 0; i < packets.length; i++) { out.set(packets[i], at); at += packets[i].length; }
    dv.setUint32(22, crc32(out), true);
    return out;
  }

  // pcm: Int16Array, mono 16 kHz. Returns a Uint8Array Ogg Opus file.
  function encode(pcm) {
    var e = ex.op_enc_create();
    if (!e) throw new Error('opus encoder');
    var la = ex.op_enc_lookahead(e), preSkip = la * 3;
    var frames = Math.ceil((pcm.length + la) / 960);
    var pIn = ex.op_alloc(960 * 2), pOut = ex.op_alloc(1500), packets = [], f, i;
    for (f = 0; f < frames; f++) {
      var frame = new Int16Array(ex.memory.buffer, pIn, 960);
      for (i = 0; i < 960; i++) { var s = f * 960 + i; frame[i] = s < pcm.length ? pcm[s] : 0; }
      var n = ex.op_enc_frame(e, pIn, pOut, 1500);
      if (n < 0) { ex.op_free(pIn); ex.op_free(pOut); ex.op_enc_destroy(e); throw new Error('opus encode ' + n); }
      packets.push(new Uint8Array(ex.memory.buffer, pOut, n).slice());
    }
    ex.op_free(pIn); ex.op_free(pOut); ex.op_enc_destroy(e);

    var serial = (Math.random() * 4294967296) >>> 0, seq = 0, pages = [];
    var head = new Uint8Array(19), hv = new DataView(head.buffer);
    head.set([0x4f, 0x70, 0x75, 0x73, 0x48, 0x65, 0x61, 0x64, 1, 1]);   // "OpusHead", version 1, 1 channel
    hv.setUint16(10, preSkip, true); hv.setUint32(12, 16000, true);      // pre-skip; input rate
    hv.setInt16(16, 0, true); head[18] = 0;                              // output gain; mapping family 0
    pages.push(page([head], 0, serial, seq++, 0x02));                    // beginning of stream
    var vendor = 'Stump (libopus 1.5.2)', tags = new Uint8Array(8 + 4 + vendor.length + 4), tv = new DataView(tags.buffer);
    tags.set([0x4f, 0x70, 0x75, 0x73, 0x54, 0x61, 0x67, 0x73]);         // "OpusTags"
    tv.setUint32(8, vendor.length, true);
    for (i = 0; i < vendor.length; i++) tags[12 + i] = vendor.charCodeAt(i);
    tv.setUint32(12 + vendor.length, 0, true);                           // no comments
    pages.push(page([tags], 0, serial, seq++, 0));
    var end = preSkip + pcm.length * 3;                                  // true end, at 48 kHz
    for (f = 0; f < packets.length; f += 16) {
      var group = packets.slice(f, f + 16), last = f + 16 >= packets.length;
      var granule = last ? end : (f + group.length) * 2880;
      pages.push(page(group, granule, serial, seq++, last ? 0x04 : 0));
    }
    var total = 0; pages.forEach(function (p) { total += p.length; });
    var file = new Uint8Array(total), at = 0;
    pages.forEach(function (p) { file.set(p, at); at += p.length; });
    return file;
  }

  // Pages of the first logical stream -> {preSkip, lastGranule, packets}, or null.
  function parse(data) {
    var i = 0, serial = null, packets = [], cur = [], head = null, last = null;
    while (i < data.length) {
      if (i + 27 > data.length || data[i] !== 0x4f || data[i + 1] !== 0x67 || data[i + 2] !== 0x67 || data[i + 3] !== 0x53) return null;
      var nseg = data[i + 26], body = i + 27 + nseg, size = 0, k;
      if (body > data.length) return null;
      for (k = 0; k < nseg; k++) size += data[i + 27 + k];
      if (body + size > data.length) return null;
      var dv = new DataView(data.buffer, data.byteOffset + i, 27);
      var sn = dv.getUint32(14, true);
      if (serial === null) serial = sn;
      if (sn === serial) {
        var lo = dv.getUint32(6, true), hi = dv.getUint32(10, true);
        if (!(lo === 0xffffffff && hi === 0xffffffff) && head !== null) last = hi * 4294967296 + lo;
        var at = body;
        for (k = 0; k < nseg; k++) {
          var len = data[i + 27 + k];
          cur.push(data.subarray(at, at + len)); at += len;
          if (len < 255) {                                   // a packet ends here
            var n = 0, p, q = 0;
            cur.forEach(function (c) { n += c.length; });
            p = new Uint8Array(n); cur.forEach(function (c) { p.set(c, q); q += c.length; });
            cur = [];
            if (head === null) {
              if (p.length < 19 || String.fromCharCode.apply(null, p.subarray(0, 8)) !== 'OpusHead') return null;
              if ((p[9] !== 1 && p[9] !== 2) || p[18] !== 0) return null;
              head = { preSkip: p[10] | (p[11] << 8) };
            } else packets.push(p);
          }
        }
      }
      i = body + size;
    }
    if (head === null || last === null || last < head.preSkip) return null;
    if (packets.length && String.fromCharCode.apply(null, packets[0].subarray(0, 8)) === 'OpusTags') packets.shift();
    return { preSkip: head.preSkip, lastGranule: last, packets: packets };
  }

  // Length in whole ms from the bytes alone: (last granule - pre-skip) / 48.
  function durationMs(data) {
    var p = parse(data);
    return p ? Math.floor((p.lastGranule - p.preSkip) / 48) : null;
  }

  // data: Uint8Array Ogg Opus file. Returns an Int16Array, mono 48 kHz.
  function decode(data) {
    var p = parse(data);
    if (!p) throw new Error('not an Ogg Opus file');
    var d = ex.op_dec_create();
    if (!d) throw new Error('opus decoder');
    var cap = 5760, pOut = ex.op_alloc(cap * 2), chunks = [], total = 0;
    p.packets.forEach(function (pkt) {
      var pIn = ex.op_alloc(Math.max(pkt.length, 1));
      new Uint8Array(ex.memory.buffer, pIn, pkt.length).set(pkt);
      var n = ex.op_dec_packet(d, pIn, pkt.length, pOut, cap);
      ex.op_free(pIn);
      if (n > 0) { chunks.push(new Int16Array(ex.memory.buffer, pOut, n).slice()); total += n; }
    });
    ex.op_free(pOut); ex.op_dec_destroy(d);
    var all = new Int16Array(total), at = 0;
    chunks.forEach(function (c) { all.set(c, at); at += c.length; });
    var keep = Math.max(0, Math.min(all.length - p.preSkip, p.lastGranule - p.preSkip));
    return all.subarray(p.preSkip, p.preSkip + keep);
  }

  return { load: load, encode: encode, decode: decode, durationMs: durationMs,
           ready: function () { return !!ex; } };
})();
if (typeof module !== 'undefined') module.exports = Opus;
