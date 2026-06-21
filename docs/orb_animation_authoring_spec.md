# ORB Animation Authoring Spec
### Brief for artists creating animations for the Imagix Crystal Ball POV display

This device is a **persistence-of-vision (POV) holographic fan**: a single blade of
**90 RGB LEDs** spins fast, painting a circular image one radial line at a time. One full
revolution = one frame. Everything below comes from reverse-engineering the hardware, so
treating it as hard constraints (not suggestions) is what keeps art from turning to mush.

Mental model: **you are drawing glowing neon on a black spinning disc.** Only lit pixels
show; black is invisible (LED off).

---

## 1. Canvas & resolution (what to deliver)

| Spec | Value |
|------|-------|
| Frame shape | **Square**, 1:1 |
| Deliver at | **1024 × 1024 px** ideal · 512 × 512 px minimum |
| Safe area | **Centered circle only** — corners are NOT displayed (it's a disc) |
| Background | Pure black `#000000` (= LEDs off = invisible). Transparent is OK, treated as off |
| Effective display resolution | ~**90 px radial** (center→rim) × ~**565 px angular** at the rim |

- Author at high res; the encoder samples down to the blade's polar grid. Detail **finer
  than ~90 radial / ~565 angular is lost** — design bold, not fussy.
- **The hub is low-resolution.** Angular detail collapses toward the center (all lines
  converge there). Keep important detail in the **mid-to-outer radius**; tiny things near
  the center smear or vanish.
- **Orientation:** draw "up = up." There's a fixed mounting offset that we correct at
  encode time — **do not pre-rotate** frames.
- Thin radial lines (1 px) can flicker; **2–3 px minimum** for stable strokes.

---

## 2. Color — the hard one

The display is **1 bit per channel**. That means **8 possible colors, full stop:**

| Color | Hex | | Color | Hex |
|-------|-----|-|-------|-----|
| Black (off) | `#000000` | | Yellow | `#FFFF00` |
| Red | `#FF0000` | | Cyan | `#00FFFF` |
| Green | `#00FF00` | | Magenta | `#FF00FF` |
| Blue | `#0000FF` | | White | `#FFFFFF` |

- **Anything not on this list is approximated by dithering** (a grainy stipple of the 8
  colors). Gradients, soft shading, and anti-aliased edges become visible noise.
- **For crispest results:** flat fills in these 8 colors, hard edges, high contrast.
- Dithering can be a deliberate retro look (it suits the aesthetic) — just author *knowing*
  it happens, rather than being surprised by it.
- Mid-tones and pastels read poorly; saturated and bright read best.

---

## 3. Motion & frame counts

- **~25 frames per second** — one painted frame per revolution (~1500 RPM).
- **Frame count = duration (seconds) × 25.** A 4-second loop = ~100 frames.
- **Loops must be seamless:** the last frame has to flow back into the first with no jump
  (applies to idle, listening, thinking, talk, responding).
- Fast motion strobes at 25 fps — keep movement smooth/continuous rather than snappy.
- Factory references (measured): power-on ≈ **116 frames** (~4.6 s); boot logo ≈ **122
  frames** (~4.9 s).

**Delivery format:** zero-padded PNG sequence preferred — `eb_idle_0001.png … eb_idle_0100.png`
(lossless, exact frames). Animated GIF acceptable as a fallback.

---

## 4. State hierarchy — what to make, how many, how long

The device's assistant has a set of states. **Each numbered variant is a separate
animation the device picks from at RANDOM** when that state fires — so to theme a state
cleanly you must fill *every* variant (or leftovers from another set bleed in). You may
also add MORE variants than listed; the count isn't fixed.

Frame counts below are **recommended targets** (director's call), not hard requirements.

### Tier 1 — conversational loop (the must-haves)

| State | Variants | Length (frames / sec) | Type | Notes |
|-------|:---:|:---:|------|-------|
| idle | 4 | 50–100 / 2–4 s | **loop** | Resting; the most-seen state. Make these distinct |
| wake | 1 | 25–40 / 1–1.5 s | one-shot | Triggered on wake word — a "perk up" |
| vad | 1 | 15–25 / ~1 s | short | Voice detected; quick acknowledgement |
| listening | 4 | 50–75 / 2–3 s | **loop** | While the user is speaking |
| thinking | 2 | 50–75 / 2–3 s | **loop** | Processing |
| meaning | 1 | ~25 / ~1 s | one-shot | Parsing what was said |
| responding | 7 | 50–100 / 2–4 s | **loop** | Plays while it talks — most screen time, most variety |
| talk | 1 | 25–50 / 1–2 s | **loop** | Mouth-move / speaking loop |
| confirm | 4 | 25–50 / 1–2 s | one-shot | Acknowledgement |

### Tier 2 — personality / polish

| State | Variants | Length (frames / sec) | Type | Notes |
|-------|:---:|:---:|------|-------|
| happy | 1 | 40–75 / 1.5–3 s | one-shot | Positive reaction |
| error | 1 | 40–60 / ~2 s | one-shot | Something went wrong |
| low | 1 | ~40 / ~1.5 s | one-shot | Low battery |
| bye | 1 | 50–75 / 2–3 s | one-shot | Goodbye |
| selected | 1 | 25–40 / 1–1.5 s | one-shot | When chosen |
| start | 1 | 25–50 / 1–2 s | one-shot | Startup |
| pwon | 3 | 75–116 / 3–4.6 s | one-shot | Power-on intro |
| pwoff | 3 | 50–75 / 2–3 s | one-shot | Power-off outro |

### System (shared, `bu_` set — optional, lowest priority)

| State | Variants | Length | Type | Notes |
|-------|:---:|:---:|------|-------|
| bootup | 1 | ~100–125 / 4–5 s | one-shot | Boot splash / logo |
| on | 1 | 25–50 / 1–2 s | one-shot | Power-on |
| connecting | 1 | ~50 / ~2 s | **loop** | Network bring-up (loops until connected) |
| connected | 1 | ~25 / ~1 s | one-shot | Connection success |
| inprogress / updone | 1 ea | ~25–50 | mixed | Update progress / done |

---

## 5. Delivery checklist

- [ ] Square frames, **1024 × 1024** (or 512² min), all content inside the centered circle
- [ ] Pure black background; design as glowing shapes (black = off = invisible)
- [ ] Palette limited toward the **8 displayable colors**; flat fills, hard edges, high contrast
- [ ] **Seamless** first→last frame on every looping state
- [ ] Important detail kept in the mid-to-outer radius (hub is low-res)
- [ ] "Up = up" orientation; no pre-rotation
- [ ] PNG sequences, zero-padded, named by state + variant (`eb_responding_03_0001.png …`)
- [ ] Frame counts per the tables above; every variant of a themed state filled

---

## 6. Technical reference (pipeline-side, artists can ignore)

- Polar geometry: **2100 columns/revolution × 90 LEDs/column**, 34 bytes/column,
  2 sync bits + 270 data bits (90 × RGB, 1 bit/channel, MSB-first).
- 1 frame per revolution; ~25 rev/s ⇒ ~25 fps. Frame = 2100 × 34 = 71,400 B; ~1.79 MB/s.
- Encoder (`orb_encode.py`) samples the square source into the 2100 × 90 polar grid,
  dithers to 1-bit RGB, and applies the fixed orientation offset via `--cpr 2100 --rotate <deg>`.
- Author resolution only needs to exceed the effective grid (90 radial / ~565 angular);
  1024² gives clean anti-aliased downsampling headroom.
