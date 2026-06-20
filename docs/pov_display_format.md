# POV Display Format

Reverse engineered from ground-truth single-color test files (`Red.bin`, `Green.bin`, `Blue.bin`, `White.bin`) present on the SD-NAND.

---

## Column format

Each column is **272 bits = 34 bytes**:

```
bits[0:1]    = header/sync (always 0b00)
bits[2:271]  = 270 data bits
```

The 270 data bits represent 90 LEDs × 3 channels (R, G, B), **bit-interleaved**:

```
R₀ G₀ B₀ R₁ G₁ B₁ R₂ G₂ B₂ ... R₈₉ G₈₉ B₈₉
```

Extraction:
```python
R = payload[:, 0::3]   # bits 0, 3, 6, 9, ...
G = payload[:, 1::3]   # bits 1, 4, 7, 10, ...
B = payload[:, 2::3]   # bits 2, 5, 8, 11, ...
```

LED slot 0 = blade tip (rim of display), slot 89 = hub (center). Reverse for center-out rendering.

---

## File structure

```
[column_0][column_1]...[column_2015]   = 1 revolution (68,544 bytes)
[revolution_0][revolution_1]...[revolution_N-1]
```

- ~2016 columns per revolution (varies slightly with motor speed)
- ~1500 RPM = ~25 revolutions/second = ~25 fps
- 1 revolution = 1 displayed frame
- Data rate: 34 × 2016 × 25 = 1,713,600 bytes/sec

File size calculation:
```
bytes = 34 × 2016 × N_revolutions
```

Example: 100 revolutions = 6,854,400 bytes = 4.0 seconds.

---

## Color depth

1 bit per channel = 8 possible colors:

| R | G | B | Color |
|---|---|---|-------|
| 0 | 0 | 0 | Black (off) |
| 1 | 0 | 0 | Red |
| 0 | 1 | 0 | Green |
| 0 | 0 | 1 | Blue |
| 1 | 1 | 0 | Yellow |
| 1 | 0 | 1 | Magenta |
| 0 | 1 | 1 | Cyan |
| 1 | 1 | 1 | White |

For gradients and intermediate colors, ordered (Bayer) dithering is required. The encoder in `tools/orb_encode.py` implements a 4×4 Bayer matrix dither in polar space.

---

## Render hardware (HC32F460)

The HC32 renders columns in a hardware loop with minimal CPU involvement:

1. **Hall sensor** fires once per revolution at a fixed physical position
2. **AOS** (Alarm Output System) routes the Hall event to TMR4
3. **TMR4** clocks column data out at a rate set by `speed`/`baud`
4. **DMA** feeds column bytes to the SPI peripheral driving the LEDs
5. **TMR6** measures the Hall period (one value per revolution)

**Correction (2026-06-20):** earlier notes claimed this is "inherently phase-locked, no
software correction needed." On-hardware behaviour disproves that for static content. The
Hall pulse sets the column-clock *phase*, but the DMA frame pointer **free-runs** — it is
not re-anchored to the Hall index each revolution. The columns actually painted per
physical revolution are `C_hw = column_clock_rate * Hall_period`, which equals the authored
2016 only at the exact design RPM (an open-loop motor never holds it). So a plain static
(identical-revolution) file precesses: its anchor walks by `(C_hw - 2016)` columns per
revolution — a steady visible spin. CPR is fixed at 2016 by the size/duration contract
(file size validated as `34*2016*N`), so the cure is content-side counter-rotation (next
section). The `angle` parameter (0–359) is a one-shot static offset — it places a
stationary image but cannot cancel a continuous drift.

---

## Phase locking a STATIC image (counter-rotation)

Because the render free-runs, a static image is held still by baking the cancelling
rotation into the content: author revolution *i* pre-rotated by `deg_per_rev * i` so that
content rotation + hardware precession = net zero. This is what the factory animations do —
a "static" factory logo is stored as a slow rotation the precession cancels on the blade. A
full 360° of counter-rotation returns to the original image, so the natural loop length
`N = round(360 / deg_per_rev)` is **seamless**.

- Encoder: `tools/orb_encode.py logo.png out.bin --lock DEG_PER_REV`
- Calibrate: `tools/orb_lock_calibrate.py` from the observed drift rate (deg/s or
  seconds-per-turn), then bisect sign/magnitude in 2–3 pushes.
- File size scales as `360/deg_per_rev` revolutions × 68,544 B (slower drift → bigger file).
  Trim motor `speed` to shrink the drift, or `--lock-frames` to cap (small per-loop snap).
- Residual jitter (~±6.7°) from RPM wander within a turn is irreducible without per-Hall
  timestamps; the mean drift is fully cancellable.

---

## Phase locking in decoded GIFs

When decoding to a GIF, the "radar sweep" effect is an inherent artifact of the POV format — pixels in each frame were painted sequentially (one column at a time) rather than simultaneously. On the real hardware, persistence of vision makes this invisible. In a GIF it's visible as a faint wipe/sweep.

Inter-frame rotation (each frame starting at a slightly different angle due to the free-run precession above) can be partially corrected by fitting a linear + sinusoidal model to the per-frame rotation measured via FFT cross-correlation, then counter-rotating each frame in pixel space. The linear term is the mean precession (`deg_per_rev`). Residual jitter (~±6.7°) is irreducible from the data alone without Hall sensor timestamps.

---

## Display profile variants

The `baud` field in `Config.txt` selects the display profile:
- `baud:4` → `_64` suffix files (primary set, ~111 animations)
- Other values → `_67` suffix files (subset, alternate profile)

The exact mapping of `baud` values to profile suffixes beyond these two is unconfirmed.
