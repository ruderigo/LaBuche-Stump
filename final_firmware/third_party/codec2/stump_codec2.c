/*
 * Whole-note Codec 2 encode/decode for the Stump's browser chat, compiled
 * to WebAssembly. Modes are LXMF's numbers (LXMF.AM_CODEC2_*), mapped to
 * Codec 2 1.2.0 modes, as in FireFly's firefly_codec2.c.
 *
 * Codec 2 is LGPL-2.1 (https://github.com/drowe67/codec2, tag 1.2.0);
 * this wrapper is built and linked against it unmodified.
 */
#include <stdlib.h>
#include "codec2.h"

static int c2mode(int lxmf_mode) {
    switch (lxmf_mode) {
        case 3: return CODEC2_MODE_700C;
        case 4: return CODEC2_MODE_1200;
        case 5: return CODEC2_MODE_1300;
        case 6: return CODEC2_MODE_1400;
        case 7: return CODEC2_MODE_1600;
        case 8: return CODEC2_MODE_2400;
        case 9: return CODEC2_MODE_3200;
        default: return -1;
    }
}

__attribute__((export_name("c2_alloc"))) void *c2_alloc(int n) { return malloc(n); }
__attribute__((export_name("c2_free"))) void c2_free(void *p) { free(p); }

/* Samples per frame for an LXMF mode, or 0 if unsupported. */
__attribute__((export_name("c2_samples_per_frame")))
int c2_samples_per_frame(int lxmf_mode) {
    int m = c2mode(lxmf_mode);
    if (m < 0) return 0;
    struct CODEC2 *c = codec2_create(m);
    if (!c) return 0;
    int n = codec2_samples_per_frame(c);
    codec2_destroy(c);
    return n;
}

/* Bytes per frame for an LXMF mode, or 0 if unsupported. */
__attribute__((export_name("c2_bytes_per_frame")))
int c2_bytes_per_frame(int lxmf_mode) {
    int m = c2mode(lxmf_mode);
    if (m < 0) return 0;
    struct CODEC2 *c = codec2_create(m);
    if (!c) return 0;
    int n = codec2_bytes_per_frame(c);
    codec2_destroy(c);
    return n;
}

/* Encodes whole frames of 8 kHz 16-bit PCM; a final partial frame is
 * padded with silence. Returns the number of bytes written, or -1. */
__attribute__((export_name("c2_encode_note")))
int c2_encode_note(int lxmf_mode, const short *pcm, int nsamples, unsigned char *out, int out_cap) {
    int m = c2mode(lxmf_mode);
    if (m < 0) return -1;
    struct CODEC2 *c = codec2_create(m);
    if (!c) return -1;
    int spf = codec2_samples_per_frame(c), bpf = codec2_bytes_per_frame(c);
    short *frame = (short *)malloc(sizeof(short) * spf);
    int written = 0;
    for (int i = 0; i < nsamples; i += spf) {
        if (written + bpf > out_cap) break;
        for (int k = 0; k < spf; k++) frame[k] = (i + k < nsamples) ? pcm[i + k] : 0;
        codec2_encode(c, out + written, frame);
        written += bpf;
    }
    free(frame);
    codec2_destroy(c);
    return written;
}

/* Decodes floor(nbytes / bytes-per-frame) whole frames to 8 kHz PCM;
 * trailing bytes that don't make a frame are ignored. Returns samples. */
__attribute__((export_name("c2_decode_note")))
int c2_decode_note(int lxmf_mode, const unsigned char *bits, int nbytes, short *out, int out_cap) {
    int m = c2mode(lxmf_mode);
    if (m < 0) return -1;
    struct CODEC2 *c = codec2_create(m);
    if (!c) return -1;
    int spf = codec2_samples_per_frame(c), bpf = codec2_bytes_per_frame(c);
    int frames = nbytes / bpf, produced = 0;
    for (int f = 0; f < frames; f++) {
        if (produced + spf > out_cap) break;
        codec2_decode(c, out + produced, bits + f * bpf);
        produced += spf;
    }
    codec2_destroy(c);
    return produced;
}
