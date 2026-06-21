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
  python3 orb_encode.py logo.png   output.bin --lock 2.0      # STATIC, phase-locked
  python3 orb_encode.py logo.png   sweep.bin  --lock-sweep -8 8  # calibration chirp

Phase-lock (static images):
  PREFERRED -- author at the blade's NATIVE columns-per-rev so the image is boundary-
  aligned and just sits still, like the factory bootup logo (no drift, no tearing):
    python3 orb_encode.py logo.png static.bin --cpr 2100 --seconds 4
  The blade paints ~2100 col/rev, NOT the 2016 the anim pipeline authors at (measured
  from bu_bootup_*.bin). Start at 2100; nudge +/-1-2 if a faint creep remains.

  Orientation: a locked image lands rotated by a fixed Hall-vs-column-0 offset. Correct
  it once with --rotate DEG (deg CCW; flip sign if it goes the wrong way), then reuse:
    python3 orb_encode.py logo.png static.bin --cpr 2100 --rotate 41 --seconds 4

  FALLBACK (must stay at 2016) -- bake counter-rotation to cancel the 2016-vs-2100 walk.
  This stops the drift but adds a sweeping tear (= the per-frame step), so prefer --cpr.
    --lock DEG_PER_REV    cancel a measured precession (tears ~DEG_PER_REV)
  Calibrate DEG_PER_REV with tools/orb_lock_calibrate.py / --lock-sweep.

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

def cartesian_to_polar_column(frame, col_idx, cw=True):
    """
    Sample one angular column from a square frame.
    Returns (90,3) uint8 array matching the hardware slot layout:
      slot 0 = blade tip (rim/outer edge), slot 89 = hub (center).
      This matches the format doc ground-truth: the HC32 emits tip first.

    cw=True  (default): columns sweep clockwise as the fan physically does.
      col 0 = Hall sensor trigger position; subsequent columns advance CW.
      Use cw=False to reverse the sweep direction if your fan runs CCW.
    """
    h, w, _ = frame.shape
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    max_r = min(cx, cy)
    # CW in screen coords (y-down): angle decreases with col_idx so the sweep
    # goes right->down->left->up when viewed from the front.
    sign = -1.0 if cw else 1.0
    ang = sign * 2 * np.pi * col_idx / CPR
    ca, sa = np.cos(ang), np.sin(ang)
    out = np.zeros((NLED, 3), dtype=np.uint8)
    for slot in range(NLED):
        # slot 0 = rim (max r), slot 89 = hub (r=0) -- matches format doc.
        r = ((NLED - 1 - slot) / (NLED - 1)) * max_r
        x = int(round(cx + r * ca))
        y = int(round(cy + r * sa))
        if 0 <= x < w and 0 <= y < h:
            out[slot] = frame[y, x]
    return out

def dither_frame_to_polar(frame, cw=True):
    """
    Convert a square RGB frame into (CPR, 90, 3) of 1-bit-per-channel values,
    using ordered (Bayer) dithering per channel so gradients survive.
    Returns boolean-ish array (0/1) shape (CPR, NLED, 3).
    """
    # First sample all columns into a polar buffer (CPR x NLED x 3)
    polar = np.zeros((CPR, NLED, 3), dtype=np.float32)
    for c in range(CPR):
        polar[c] = cartesian_to_polar_column(frame, c, cw=cw)
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

def _square(frame):
    if frame.shape[0] != frame.shape[1]:
        s = min(frame.shape[0], frame.shape[1])
        frame = frame[:s, :s]
    return frame

def _seam_deg(n, d):
    """Per-loop discontinuity (deg) if the file loops after n frames at d deg/rev:
    how far n*d lands from a whole number of turns."""
    s = (n * d) % 360.0
    return min(s, 360.0 - s)

def _auto_loop_len(d, tol=2.5, hi=500):
    """Pick the SMALLEST frame count whose total counter-rotation lands within `tol`
    of a whole number of turns -> a seamless-enough loop at the smallest file size.

    The naive N = round(360/|d|) only closes cleanly when |d| divides 360; for an
    arbitrary measured d (e.g. 15.36) it leaves a (360 - N*|d|) snap each loop. This
    walks N up to the first value whose seam is under tol (e.g. 15.36 -> N=47, seam
    ~1.9 deg, vs N=23's ~6.7 deg), well under the ~+/-6.7 deg RPM-jitter floor so the
    residual is invisible. Falls back to the global-min seam if none is under tol
    within `hi` frames. For small d this may return 1 (precession already below tol,
    so plain static is fine and no counter-rotation is needed)."""
    best_n, best_s = 1, _seam_deg(1, d)
    for n in range(1, hi + 1):
        s = _seam_deg(n, d)
        if s < best_s:
            best_n, best_s = n, s
        if s <= tol:
            return n, s
    return best_n, best_s

def encode_locked(base, out_path, deg_per_rev, cw=True, max_frames=None):
    """
    Encode a STATIC image phase-locked via COUNTER-ROTATION at CPR=2016.

    NOTE (preferred fix): the blade actually paints ~2100 columns/rev, not 2016 (read
    off bu_bootup_*.bin, which is authored at 2100 and is dead-static/tear-free). 2016 is
    only the animation pipeline's authoring standard. The clean fix for a static image is
    to author at the NATIVE CPR (orb_encode.py --cpr 2100): identical revs, boundary-
    aligned to the physical revolution -> no drift AND no tearing, exactly like the
    factory bootup. Use that for static content.

    This function is the fallback for when you must stay at CPR=2016 (e.g. matching the
    anim pipeline). The render free-runs (Hall sets column-clock phase, the DMA frame
    pointer is not re-anchored), so a plain 2016 file precesses by (C_hw - 2016) ~ 84
    columns/rev (~15 deg). Counter-rotation cancels the drift but, because the 2016 frame
    boundary cuts across the ~2100-column physical rev, it introduces a sweeping tear of
    magnitude deg_per_rev. Prefer --cpr for anything static.

    A full 360 deg of counter-rotation returns to the original image. For a d that does
    not divide 360 evenly, _auto_loop_len picks the smallest frame count whose total
    rotation lands within ~2.5 deg of a whole number of turns (e.g. 15.36 deg/rev -> 47
    frames, seam ~1.9 deg) so the per-loop snap stays under the RPM-jitter floor. Pass
    max_frames to force an exact loop length instead.

    deg_per_rev : signed degrees to rotate the source per revolution. Magnitude =
                  measured precession (deg/rev). Sign picks the cancel direction;
                  if the blade image still drifts the SAME way, flip the sign.
                  Calibrate with tools/orb_lock_calibrate.py.
    Returns (out_path, duration_ms) -- feed duration_ms to the push sidecar so the
    device plays a whole turn before re-picking (no mid-loop snap).
    """
    if deg_per_rev == 0:
        raise ValueError('deg_per_rev must be non-zero (0 = no lock = plain static)')
    base = _square(base)
    if max_frames is not None:
        n = int(max_frames)               # explicit override
        seam = _seam_deg(n, deg_per_rev)
    else:
        n, seam = _auto_loop_len(deg_per_rev)
    dur_ms = int(round(n / FPS * 1000.0))
    sz = n * CPR * BYTES_COL
    print(f'Phase-lock encode: {deg_per_rev:+.4f} deg/rev -> {n} frames '
          f'({n/FPS:.2f}s, {sz} bytes, ~{sz/1e6:.1f} MB), per-loop seam ~{seam:.2f} deg')
    if seam > 5.0:
        print(f'  NOTE: seam ~{seam:.1f} deg is on the large side; try a different '
              f'--lock-frames for a tighter loop. Set push duration={dur_ms} ms.')
    else:
        print(f'  set push duration to {dur_ms} ms')
    pil_base = Image.fromarray(base)
    with open(out_path, 'wb') as fout:
        for i in range(n):
            # rotate from the ORIGINAL each frame (no cumulative interpolation blur)
            frame = np.asarray(
                pil_base.rotate(deg_per_rev * i, resample=Image.BILINEAR,
                                expand=False))
            bits = dither_frame_to_polar(frame, cw=cw)
            buf = bytearray()
            for c in range(CPR):
                buf += encode_column(bits[c])
            fout.write(buf)
            if i % 25 == 0:
                print(f'  frame {i}/{n}')
    print(f'Done. {out_path}  {os.path.getsize(out_path)} bytes  '
          f'({os.path.getsize(out_path)/BYTES_SEC:.3f}s)  duration={dur_ms}ms')
    return out_path, dur_ms

def encode(frames, out_path, seconds=None, cw=True):
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
            bits = dither_frame_to_polar(frame, cw=cw)
            buf = bytearray()
            for c in range(CPR):
                buf += encode_column(bits[c])
            fout.write(buf)
            if fi % 10 == 0:
                print(f'  frame {fi}/{target_revs}')
    sz = os.path.getsize(out_path)
    print(f'Done. {out_path}  {sz} bytes  ({sz/BYTES_SEC:.3f}s)')
    return out_path

def encode_sweep(base, out_path, deg_min, deg_max, frames=200, cw=True):
    """
    CALIBRATION CHIRP. Encode a static image whose baked counter-rotation RATE ramps
    linearly from deg_min to deg_max (deg/rev) across the file. Push it once and watch:
    the image drifts, decelerates, freezes for a beat, then reverses. The freeze is the
    frame where the baked rate equals this blade's true precession, so

        lock = deg_min + (deg_max - deg_min) * (t_freeze / T_total)

    is the value to feed orb_encode.py --lock. One push gives sign AND magnitude with no
    rev/s assumption. If it never freezes (drifts monotonically the whole pass), the true
    precession is outside [deg_min, deg_max] -- widen the range.

    Make the range bracket your expected drift; default +/-8 deg/rev covers an unlocked
    image that takes down to ~1.8 s per apparent turn (at 25 rev/s).
    """
    if deg_max <= deg_min:
        raise ValueError('deg_max must be > deg_min')
    base = _square(base)
    n = int(frames)
    T = n / FPS
    sz = n * CPR * BYTES_COL
    print(f'Sweep encode: {deg_min:+.2f} -> {deg_max:+.2f} deg/rev over {n} frames '
          f'({T:.2f}s, {sz} bytes, ~{sz/1e6:.1f} MB)')
    print(f'  resolution: {(deg_max-deg_min)/(n-1):.3f} deg/rev per frame ({1000/FPS:.0f} ms)')
    pil_base = Image.fromarray(base)
    theta = 0.0   # cumulative content rotation
    with open(out_path, 'wb') as fout:
        for i in range(n):
            g = deg_min + (deg_max - deg_min) * i / (n - 1)   # rate at this frame
            frame = np.asarray(
                pil_base.rotate(theta, resample=Image.BILINEAR, expand=False))
            bits = dither_frame_to_polar(frame, cw=cw)
            buf = bytearray()
            for c in range(CPR):
                buf += encode_column(bits[c])
            fout.write(buf)
            theta += g          # advance so the LOCAL slope at frame i is g(i)
            if i % 25 == 0:
                print(f'  frame {i}/{n}  (rate {g:+.2f} deg/rev)')
    dur_ms = int(round(T * 1000))
    print(f'Done. {out_path}  {os.path.getsize(out_path)} bytes  duration={dur_ms}ms')
    print('Read-off table (playback time -> lock value at that instant):')
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        t = frac * T
        d = deg_min + (deg_max - deg_min) * frac
        print(f'    t={t:5.2f}s  ->  --lock {d:+.2f}')
    print(f'Push it once at duration {dur_ms} ms, note t_freeze, then:')
    print(f'    lock = {deg_min:+.2f} + {deg_max-deg_min:.2f} * (t_freeze / {T:.2f})')
    return out_path, dur_ms

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    inp, outp = sys.argv[1], sys.argv[2]
    seconds = None
    cw = True   # default: fan spins clockwise (matches physical hardware)
    lock = None
    lock_frames = None
    sweep = None
    sweep_frames = 200
    if '--seconds' in sys.argv:
        seconds = float(sys.argv[sys.argv.index('--seconds')+1])
    if '--ccw' in sys.argv:
        cw = False
        print('Note: encoding for CCW fan rotation (column order reversed)')
    if '--cpr' in sys.argv:
        # Author at the blade's NATIVE columns-per-rev instead of the 2016 anim standard.
        # The hardware paints ~2100 col/rev (read off bu_bootup_*.bin); content authored
        # at that value is boundary-aligned to the physical revolution -> a plain static
        # image holds still with NO drift and NO tearing, exactly like the factory bootup
        # logo. This is the correct fix for static content; --lock is the 2016-bound fallback.
        CPR = int(sys.argv[sys.argv.index('--cpr')+1])
        BYTES_SEC = BYTES_COL * CPR * FPS
        print(f'Authoring at CPR={CPR} columns/rev (native hardware, not the 2016 standard)')
    if '--lock' in sys.argv:
        lock = float(sys.argv[sys.argv.index('--lock')+1])
    if '--lock-frames' in sys.argv:
        lock_frames = int(sys.argv[sys.argv.index('--lock-frames')+1])
    if '--sweep-frames' in sys.argv:
        sweep_frames = int(sys.argv[sys.argv.index('--sweep-frames')+1])
    if '--lock-sweep' in sys.argv:
        si = sys.argv.index('--lock-sweep')
        sweep = (float(sys.argv[si+1]), float(sys.argv[si+2]))
    frames = load_frames(inp)
    print(f'Loaded {len(frames)} source frame(s)')
    if '--rotate' in sys.argv:
        # one-time orientation offset correction (deg CCW). The blade's Hall index does
        # not line up with the encoder's column 0, so a locked image lands rotated by a
        # fixed amount; pre-rotate the source to cancel it. Flip the sign if it goes the
        # wrong way. Measure the offset once, then reuse it forever (it's a constant).
        rot = float(sys.argv[sys.argv.index('--rotate')+1])
        frames = [np.asarray(Image.fromarray(_square(f)).rotate(
                    rot, resample=Image.BILINEAR, expand=False)) for f in frames]
        print(f'Applied source rotation {rot:+.1f} deg CCW (orientation offset)')
    if sweep is not None:
        # calibration chirp: ramp the baked rate to find the lock value in one push
        encode_sweep(frames[0], outp, sweep[0], sweep[1],
                     frames=sweep_frames, cw=cw)
    elif lock is not None:
        # static phase-lock: counter-rotation loop from the first frame
        encode_locked(frames[0], outp, lock, cw=cw, max_frames=lock_frames)
    else:
        encode(frames, outp, seconds, cw=cw)
