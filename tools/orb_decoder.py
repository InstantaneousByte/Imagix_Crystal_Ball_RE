#!/usr/bin/env python3
"""
Imagix Crystal Ball POV display decoder.
Format reverse-engineered from pcb_testing/{Red,Green,Blue,White}.bin ground truth.

Column layout: 272 bits = 34 bytes
  bits[0:2]   = header/sync (00)
  bits[2:272] = 270 data bits, channel-interleaved R G B R G B ...
                R = data[0::3], G = data[1::3], B = data[2::3]  (90 LEDs, 1 bit/channel)
LED order is tip->hub (reverse for center-out rendering).
~2016-2100 columns per revolution (~1500 RPM).
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
    payload = cols[:, HEADER:HEADER + NLED * 3]          # ncol x 270
    R = payload[:, 0::3]
    G = payload[:, 1::3]
    B = payload[:, 2::3]
    img = np.stack([R, G, B], axis=2).astype(np.uint8) * 255  # ncol x 90 x 3
    return img  # columns x leds x rgb

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
    path = sys.argv[1] if len(sys.argv) > 1 else 'eb_listening_eb1_64.bin'
    cpr  = int(sys.argv[2]) if len(sys.argv) > 2 else 2016
    img = decode_file(path)
    nrev = img.shape[0] // cpr
    print(f'{img.shape[0]} columns, {nrev} revolutions at CPR={cpr}')
    render_polar(img, cpr, nrev // 2).save('decoded_frame.png')
    print('wrote decoded_frame.png')
