/*
 * Opus voice notes for the Stump's browser chat, compiled to WebAssembly.
 * Encoder settings are FireFly's (FireFly client quickstart 0.2.10):
 * mono 16 kHz, 8 kbit/s constrained VBR, 60 ms frames, OPUS_APPLICATION_VOIP,
 * OPUS_SIGNAL_VOICE, complexity 10. Decoding is mono at 48 kHz, so RFC 7845's
 * pre-skip and end trimming (both in 48 kHz samples) apply exactly.
 * The Ogg container is written and read in JavaScript (opus.js).
 *
 * libopus 1.5.2 (https://github.com/xiph/opus, BSD-style licence), built
 * unmodified: portable floating-point core, no CPU-specific or DNN code.
 */
#include <stdlib.h>
#include "opus.h"

__attribute__((export_name("op_alloc"))) void *op_alloc(int n) { return malloc(n); }
__attribute__((export_name("op_free"))) void op_free(void *p) { free(p); }

/* Encoder: returns a handle, or 0. */
__attribute__((export_name("op_enc_create")))
OpusEncoder *op_enc_create(void) {
    int err = 0;
    OpusEncoder *e = opus_encoder_create(16000, 1, OPUS_APPLICATION_VOIP, &err);
    if (err != OPUS_OK || !e) return 0;
    opus_encoder_ctl(e, OPUS_SET_BITRATE(8000));
    opus_encoder_ctl(e, OPUS_SET_VBR(1));
    opus_encoder_ctl(e, OPUS_SET_VBR_CONSTRAINT(1));
    opus_encoder_ctl(e, OPUS_SET_COMPLEXITY(10));
    opus_encoder_ctl(e, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));
    return e;
}

/* Encoder lookahead in 16 kHz samples (x3 gives the pre-skip at 48 kHz). */
__attribute__((export_name("op_enc_lookahead")))
int op_enc_lookahead(OpusEncoder *e) {
    opus_int32 la = 0;
    opus_encoder_ctl(e, OPUS_GET_LOOKAHEAD(&la));
    return (int)la;
}

/* One 60 ms frame: 960 samples of 16 kHz PCM in, one packet out. */
__attribute__((export_name("op_enc_frame")))
int op_enc_frame(OpusEncoder *e, const opus_int16 *pcm, unsigned char *out, int cap) {
    return opus_encode(e, pcm, 960, out, cap);
}

__attribute__((export_name("op_enc_destroy")))
void op_enc_destroy(OpusEncoder *e) { opus_encoder_destroy(e); }

/* Decoder: mono at 48 kHz; a stereo stream is mixed down by libopus. */
__attribute__((export_name("op_dec_create")))
OpusDecoder *op_dec_create(void) {
    int err = 0;
    OpusDecoder *d = opus_decoder_create(48000, 1, &err);
    return (err == OPUS_OK) ? d : 0;
}

/* One packet in; up to `cap` samples (120 ms at 48 kHz = 5760) out.
 * Returns the number of samples, or a negative libopus error. */
__attribute__((export_name("op_dec_packet")))
int op_dec_packet(OpusDecoder *d, const unsigned char *pkt, int len, opus_int16 *out, int cap) {
    return opus_decode(d, pkt, len, out, cap, 0);
}

__attribute__((export_name("op_dec_destroy")))
void op_dec_destroy(OpusDecoder *d) { opus_decoder_destroy(d); }
