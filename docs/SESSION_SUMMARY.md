# Session Summary — 2026-06-20 (InstantaneousByte)

## What was accomplished this session

The custom-frame pipeline (gates 1–7 + the three display layers) was already landing a
static image on the blade at idle. The remaining defect: **the static image rotated
clockwise instead of holding still.** This session diagnosed the cause and shipped the fix.

---

## Root cause — the render free-runs (the old "hard phase-lock" claim was wrong)

Ground truth re-established from the factory color bins (`Red/Green/Blue/White.bin`):

- Each is exactly **1,713,600 B = 50,400 columns = 25 rev × 2016 col/rev** (1.00 s @ 25 fps).
  **CPR = 2016 is measured, not assumed.** The device also validates file size as
  `34 * 2016 * N` and slices frames on that boundary (`asset_update_protocol.md`), so CPR
  is **not** a free parameter.
- The color bins are 60 %-brightness *dithered* solid fills (period-5 spatial dither); they
  pin CPR and the bit format but carry no angular feature, so they can't show drift.

The HC32 render path **free-runs**: the Hall pulse (AOS→TMR4) sets the column-clock *phase*,
but the DMA frame pointer is **not** re-anchored to the Hall index each revolution. Columns
painted per physical rev `C_hw = column_clock_rate * Hall_period` equals 2016 only at the
exact design RPM, which an open-loop motor never holds. So a plain identical-revolution file
precesses by `(C_hw - 2016)` columns/rev -> the observed clockwise spin.

Evidence this beats the prior "inherently phase-locked" claim:
1. A genuinely static (identical-rev) file **cannot** drift under a true per-Hall frame
   reset — yet it does.
2. The format doc's own GIF note: frame start-angle varies *with motor speed* (free-run
   signature).
3. The factory animations have rotation baked into their content — the pipeline's
   counter-rotation compensation, forced by the fixed-2016 contract.

The HC32 render ISR (TMR4/AOS/DMA) is **not** in `ghidra_export_hc32.txt` (it is
interrupt/DMA code Ghidra left as raw bytes), so the lock behaviour was established from
on-hardware behaviour + the factory bins, not from decompiled register writes. The
recurring `0x7d0`/2000 in the export is an SDIO busy-wait timeout `(HCLK/8000)*2000`, not a
column count.

---

## The fix — counter-rotation encode (`tools/orb_encode.py --lock`)

Hold a static image still by baking the cancelling rotation into the content: revolution
*i* is the source pre-rotated by `deg_per_rev * i`, so content rotation + hardware
precession = 0. A full 360 deg of counter-rotation returns to the original image, so the
loop `N = round(360 / |deg_per_rev|)` is **seamless** (validated: at 5 deg/rev -> 72 frames,
measured 28 cols = 5.00 deg/rev per rev, last->first = one clean 5 deg step).

New code:
- `encode_locked()` + CLI `--lock DEG_PER_REV` and `--lock-frames N` (cap, accepts a small
  per-loop snap). Reuses the radial/CW fixes from the prior session.
- `encode_sweep()` + CLI `--lock-sweep DMIN DMAX [--sweep-frames N]` — a calibration CHIRP:
  the baked rate ramps linearly across the file, so on one push the image drifts, freezes
  for a beat, then reverses. The freeze time maps directly to the `--lock` value
  (`lock = DMIN + (DMAX-DMIN)*t_freeze/T`) — sign and magnitude in a single push, no rev/s
  assumption, no blade instrumentation.
- `tools/orb_lock_calibrate.py` — converts an observed drift (deg/s or seconds-per-turn +
  direction, with optional measured `--rps`) into the `--lock` value, loop length, and push
  `duration`. Use it to read off an estimate from the drift you're already seeing (0 extra
  pushes); the sweep is the robust alternative when the RPM is uncertain.

`deg_per_rev` is **per-unit** (depends on this blade's running RPM) and **could not be
measured from any uploaded file** — the color bins are uniform and `eb_pwon` is a real
animation. It must be calibrated on hardware.

File-size note: slower drift -> larger seamless file (`360/delta` revolutions × 68,544 B;
5 deg/rev ~ 4.9 MB, 1 deg/rev ~ 25 MB). The blade SD-NAND holds 116+ multi-MB slots so it
has room; trim motor `speed` to shrink delta, or cap with `--lock-frames`, if needed.

---

## Calibration / push recipe (cloud-free, no NAND)

```bash
# 1. Measure the unlocked drift: push the plain static image, time one full apparent
#    turn (T sec) and note direction, then:
python3 tools/orb_lock_calibrate.py --turn-seconds T --dir cw     # -> --lock value + duration

# 2. Encode locked:
python3 tools/orb_encode.py logo.png locked.bin --lock <value>

# 3. Stage with the printed duration so a whole turn plays before re-pick.
#    NOTE: stage_anims.sh is a SHELL script (run with bash, not python3), and the in-zip
#    entry MUST end in .bin or --push-anims rejects it. Do NOT pass `-n eb_idle_02` (that
#    strips the extension); the source basename keeps .bin, and --anim-as does the relabel.
bash tools/stage_anims.sh -o locked.zip -d <duration_ms> locked.bin

# 4. Push (bump version each run):
python3 server/orb_server.py --push-anims locked.zip --anim-character Ember \
    --anim-version 2.7 --anim-media-function system --anim-as eb_idle_02

# 5. Observe: still -> done. Same-direction drift -> raise |--lock|. Opposite -> flip sign.
```

---

## Next steps

1. **Calibrate `deg_per_rev` on hardware** and converge the lock (2-3 pushes).
2. **Push a complete system set** (locked idle + 13 factory originals from SD backup) so
   listening/responding contexts are populated.
3. **Purge fan junk** — 0x32 deletes for accumulated test slots.
4. **STT->LLM->TTS pipeline** — wire Whisper->Qwen->Chatterbox->opus, display the mascot frames.
5. **Verify the render ISR** if a fuller HC32 dump becomes available (confirm C_hw vs RPM,
   and whether `speed`/`baud` can null delta directly).

---

## Files changed this session

```
tools/orb_encode.py          +encode_locked() / --lock / --lock-frames,
                             +encode_sweep() / --lock-sweep (calibration chirp), docstring
tools/orb_lock_calibrate.py  NEW — drift -> --lock value + loop length + duration
docs/pov_display_format.md   corrected hard-lock claim; counter-rotation section
docs/SESSION_SUMMARY.md      this file
```
