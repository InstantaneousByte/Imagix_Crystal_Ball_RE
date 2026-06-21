#!/usr/bin/env python3
"""
Imagix Crystal Ball POV display decoder.
Format reverse-engineered from pcb_testing/{Red,Green,Blue,White}.bin ground truth.

Column layout: 272 bits = 34 bytes
  bits[0:2]   = header/sync (00)
  bits[2:272] = 270 data bits, channel-interleaved R G B R G B ...
                R = data[0::3], G = data[1::3], B = data[2::3]  (90 LEDs, 1 bit/channel)
LED order is tip->hub (reverse for center-out rendering).

CPR (columns per revolution): the blade paints ~2100 col/rev (the NATIVE hardware
value, measured from bu_bootup_*.bin and eb_pwon_eb1_64.bin). 2016 is only the anim
pipeline's authoring standard. Decoding at the WRONG cpr slices frames across the
true revolution boundary and injects a spurious linear rotation of
(cpr_true - cpr_used)/cpr_used * 360 deg/rev -- this is the "spin" earlier GIF
decodes showed. Decode at the native cpr (or let --auto-cpr find it) and that
spurious walk vanishes; only the authored motion remains.

Usage:
  orb_decoder.py file.bin [--cpr 2100] [--auto-cpr] [--gif out.gif]
"""
import numpy as np
from PIL import Image
import sys

COLBITS = 272
HEADER = 2
NLED = 90

def decode_file(path):
    data = np.frombuffer(open(path, 'rb').read(), dtype=np.uint8)
    bits = np.unpackbits(data)
    ncol = len(bits) // COLBITS
    cols = bits[:ncol * COLBITS].reshape(ncol, COLBITS)
    payload = cols[:, HEADER:HEADER + NLED * 3]
    R = payload[:, 0::3]; G = payload[:, 1::3]; B = payload[:, 2::3]
    img = np.stack([R, G, B], axis=2).astype(np.uint8) * 255   # ncol x 90 x 3
    return img

def _angsig(one):
    """per-column lit-LED count for one revolution (length cpr)."""
    return (one.any(2)).sum(1).astype(float)

def auto_cpr(img, lo=1980, hi=2200):
    """Find columns-per-rev by autocorrelation of the per-column lit signal."""
    lit = (img.any(2)).sum(1).astype(float)
    sig = lit - lit.mean()
    best, bestv = lo, -1e18
    for L in range(lo, hi + 1):
        v = float(np.dot(sig[:-L], sig[L:]) / (len(sig) - L))
        if v > bestv:
            bestv, best = v, L
    # prefer a clean integer-frame divisor within +/-3 of the peak, if one exists
    ncol = img.shape[0]
    for c in range(best - 3, best + 4):
        if c > 0 and ncol % c == 0:
            return c
    return best

def measure_rotation(img, cpr):
    """Per-frame rotation (deg) via angular cross-correlation; returns (per_frame, mean)."""
    nrev = img.shape[0] // cpr
    sigs = [_angsig(img[i*cpr:(i+1)*cpr]) for i in range(nrev)]
    def shift(a, b):
        n = len(a); A = a - a.mean(); B = b - b.mean()
        cc = np.fft.irfft(np.fft.rfft(A) * np.conj(np.fft.rfft(B)), n)
        k = int(np.argmax(cc));  k = k - n if k > n // 2 else k
        return k / cpr * 360.0
    steps = [shift(sigs[i+1], sigs[i]) for i in range(nrev - 1)]
    return steps, (float(np.mean(steps)) if steps else 0.0)

def render_polar(img, cpr, frame, size=600, reverse_leds=True, gain=1.8):
    one = img[frame * cpr:(frame + 1) * cpr]
    if reverse_leds:
        one = one[:, ::-1, :]
    cen = size // 2
    out = np.zeros((size, size, 3), np.uint8)
    for c in range(min(cpr, one.shape[0])):
        ang = 2 * np.pi * c / cpr
        ca, sa = np.cos(ang), np.sin(ang)
        for l in range(NLED):
            rr = (l / (NLED - 1)) * (size // 2 - 5)
            x = int(cen + rr * ca); y = int(cen + rr * sa)
            if 0 <= x < size and 0 <= y < size:
                out[y, x] = np.clip(one[c, l] * gain, 0, 255)
    return Image.fromarray(out)

if __name__ == '__main__':
    a = sys.argv
    path = a[1] if len(a) > 1 and not a[1].startswith('--') else 'eb_listening_eb1_64.bin'
    cpr = None
    if '--cpr' in a:        cpr = int(a[a.index('--cpr') + 1])
    elif len(a) > 2 and not a[2].startswith('--'): cpr = int(a[2])   # positional, back-compat
    gif = a[a.index('--gif') + 1] if '--gif' in a else None

    img = decode_file(path)
    if cpr is None or '--auto-cpr' in a:
        cpr = auto_cpr(img)
        print(f'auto-detected CPR = {cpr}')
    nrev = img.shape[0] // cpr
    print(f'{img.shape[0]} columns, {nrev} revolutions at CPR={cpr}')

    steps, mean = measure_rotation(img, cpr)
    if steps:
        print(f'per-frame rotation: mean {mean:+.2f} deg/frame  '
              f'(spurious linear walk if CPR is wrong; ~0 at native CPR + authored motion)')
        print(f'  range {min(steps):+.1f}..{max(steps):+.1f} deg/frame across {len(steps)} steps')

    render_polar(img, cpr, nrev // 2).save('decoded_frame.png')
    print('wrote decoded_frame.png')
    if gif:
        frames = [render_polar(img, cpr, i) for i in range(nrev)]
        frames[0].save(gif, save_all=True, append_images=frames[1:], duration=40, loop=0)
        print(f'wrote {gif} ({nrev} frames)')
