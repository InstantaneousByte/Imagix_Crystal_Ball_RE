#!/usr/bin/env python3
"""
Imagix Crystal Ball — animation ENCODER (image/GIF -> .bin)

Reverses the decoded display format:
  - 90 radial LED slots (slot 0 = hub/center, slot 89 = tip/rim)
  - 2016 angular columns per revolution
  - 25 rev/sec, 1 revolution = 1 displayed frame
  - per column: 272 bits = 34 bytes = [2 header bits = 00][270 data bits]
  - 270 data bits = 90 * (R,G,B) bit-interleaved: R=bit0,3,6.. G=1,4,7.. B=2,5,8..
  - 1 bit per channel (8 colors); dithering fakes gradients
  - data rate: 1,713,600 bytes/sec  (34 * 2016 * 25)

Usage:
  python3 orb_encode.py input.gif  output.bin                 # use GIF's own frames
  python3 orb_encode.py frames_dir output.bin                 # PNG sequence
  python3 orb_encode.py input.gif  output.bin --seconds 4     # force duration/looping

Output is padded/truncated to an exact whole number of revolutions so the
firmware's size<->duration math stays consistent.
"""
import sys, os, glob
import numpy as np
from PIL import Image

CPR        = 2016          # columns per revolution
NLED       = 90            # radial slots
COLBITS    = 272           # bits per column (2 header + 270 data)
HEADER     = 2
BYTES_COL  = COLBITS // 8   # 34
FPS        = 25
BYTES_SEC  = BYTES_COL * CPR * FPS   # 1,713,600

def load_frames(path):
    """Return list of RGB numpy arrays."""
    frames = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, '*.png')) +
                       glob.glob(os.path.join(path, '*.jpg')))
        for f in files:
            frames.append(np.asarray(Image.open(f).convert('RGB')))
    else:
        im = Image.open(path)
        try:
            n = im.n_frames
        except Exception:
            n = 1
        for i in range(n):
            im.seek(i)
            frames.append(np.asarray(im.convert('RGB')))
    return frames

def cartesian_to_polar_column(frame, col_idx):
    """
    Sample one angular column from a square frame.
    Returns (90,3) uint8 array: index 0 = center, 89 = rim.
    Angle 0 points up; rotation matches decoder (we FLIP_TOP_BOTTOM in decode,
    so here we pre-account by sampling angle straight, decoder-side handles flip).
    """
    h, w, _ = frame.shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    max_r = min(cx, cy)
    ang = 2 * np.pi * col_idx / CPR
    ca, sa = np.cos(ang), np.sin(ang)
    out = np.zeros((NLED, 3), dtype=np.uint8)
    for slot in range(NLED):
        r = (slot / (NLED - 1)) * max_r
        x = int(round(cx + r * ca))
        y = int(round(cy + r * sa))
        if 0 <= x < w and 0 <= y < h:
            out[slot] = frame[y, x]
    return out

def dither_frame_to_polar(frame):
    """
    Convert a square RGB frame into (CPR, 90, 3) of 1-bit-per-channel values,
    using ordered (Bayer) dithering per channel so gradients survive.
    Returns boolean-ish array (0/1) shape (CPR, NLED, 3).
    """
    # First sample all columns into a polar buffer (CPR x NLED x 3)
    polar = np.zeros((CPR, NLED, 3), dtype=np.float32)
    for c in range(CPR):
        polar[c] = cartesian_to_polar_column(frame, c)
    # Ordered dither: compare each channel value (0..255) to a threshold pattern
    # 4x4 Bayer matrix scaled to 0..255
    bayer = np.array([
        [ 0, 8, 2,10],
        [12, 4,14, 6],
        [ 3,11, 1, 9],
        [15, 7,13, 5]], dtype=np.float32)
    bayer = (bayer + 0.5) / 16.0 * 255.0
    th = np.zeros((CPR, NLED), dtype=np.float32)
    for c in range(CPR):
        for s in range(NLED):
            th[c, s] = bayer[c % 4, s % 4]
    bits = np.zeros((CPR, NLED, 3), dtype=np.uint8)
    for ch in range(3):
        bits[:, :, ch] = (polar[:, :, ch] > th).astype(np.uint8)
    return bits

def encode_column(col_bits):
    """col_bits: (90,3) of 0/1. Return 34 bytes."""
    # interleave R,G,B -> 270 data bits
    data = np.zeros(270, dtype=np.uint8)
    data[0::3] = col_bits[:, 0]   # R
    data[1::3] = col_bits[:, 1]   # G
    data[2::3] = col_bits[:, 2]   # B
    full = np.zeros(COLBITS, dtype=np.uint8)
    full[HEADER:HEADER+270] = data   # first 2 bits = 0 header
    return np.packbits(full).tobytes()   # 34 bytes

def encode(frames, out_path, seconds=None):
    # Determine number of revolutions (frames). If seconds given, loop/trim.
    if seconds is not None:
        target_revs = int(round(seconds * FPS))
    else:
        target_revs = len(frames)
    # loop or trim source frames to target_revs
    src = [frames[i % len(frames)] for i in range(target_revs)]
    print(f'Encoding {target_revs} revolutions ({target_revs/FPS:.2f}s), '
          f'{target_revs*CPR} columns, {target_revs*CPR*BYTES_COL} bytes')
    with open(out_path, 'wb') as fout:
        for fi, frame in enumerate(src):
            # square the frame if needed
            if frame.shape[0] != frame.shape[1]:
                s = min(frame.shape[0], frame.shape[1])
                frame = frame[:s, :s]
            bits = dither_frame_to_polar(frame)
            buf = bytearray()
            for c in range(CPR):
                buf += encode_column(bits[c])
            fout.write(buf)
            if fi % 10 == 0:
                print(f'  frame {fi}/{target_revs}')
    sz = os.path.getsize(out_path)
    print(f'Done. {out_path}  {sz} bytes  ({sz/BYTES_SEC:.3f}s)')
    return out_path

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    inp, outp = sys.argv[1], sys.argv[2]
    seconds = None
    if '--seconds' in sys.argv:
        seconds = float(sys.argv[sys.argv.index('--seconds')+1])
    frames = load_frames(inp)
    print(f'Loaded {len(frames)} source frame(s)')
    encode(frames, outp, seconds)
