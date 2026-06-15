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

The HC32 renders columns in a hardware loop with no CPU involvement:

1. **Hall sensor** fires once per revolution at a fixed physical position
2. **AOS** (Alarm Output System) routes the Hall event directly to TMR4
3. **TMR4** resets its counter and clocks column data at the correct rate
4. **DMA** feeds pre-loaded column bytes to the SPI peripheral driving the LEDs

This makes the display inherently phase-locked to the physical blade position — no software timing correction is needed. The `angle` parameter in `Config.txt` (0–359) adjusts the rotational phase offset of the rendered image.

---

## Phase locking in decoded GIFs

When decoding to a GIF, the "radar sweep" effect is an inherent artifact of the POV format — pixels in each frame were painted sequentially (one column at a time) rather than simultaneously. On the real hardware, persistence of vision makes this invisible. In a GIF it's visible as a faint wipe/sweep.

Inter-frame rotation (each frame starting at a slightly different angle due to motor speed variation) can be partially corrected by fitting a linear + sinusoidal model to the per-frame rotation measured via FFT cross-correlation, then counter-rotating each frame in pixel space. Residual jitter (~±6.7°) is irreducible from the data alone without Hall sensor timestamps.

---

## Display profile variants

The `baud` field in `Config.txt` selects the display profile:
- `baud:4` → `_64` suffix files (primary set, ~111 animations)
- Other values → `_67` suffix files (subset, alternate profile)

The exact mapping of `baud` values to profile suffixes beyond these two is unconfirmed.
