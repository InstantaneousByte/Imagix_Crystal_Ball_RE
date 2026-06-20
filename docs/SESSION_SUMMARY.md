# Session Summary — 2026-06-20 (InstantaneousByte)

## What was accomplished this session

Starting from a working BENCO local server (gates 1-6 documented from prior sessions), this session
drove the full cloud-free animation pipeline to completion: **a custom image now renders on the
holographic fan blade at idle, pushed entirely from a local server with no NAND access, no cloud,
and no hardware modification.**

---

## The Seven Gates (all cleared)

| Gate | What it checks | Fix |
|------|---------------|-----|
| 1 | HTTP/2 header name `FileManager/UpdateDefaultAssets` | Correct header |
| 2 | Push on every downchannel open | Push in session-start handler |
| 3 | Nested payload schema with `dependencies:[]` | Correct JSON structure |
| 4 | Persona display name "Ember the Baby Dragon" | Correct `persona` field |
| 5 | Raw .bin served (not zip) | Extract and serve per-file |
| 6 | On-fan filename < 31 chars | Auto-shorten in server |
| 7 | `strcmp(manifest.name, "eb1") == 0` for system anims | `name = MEDIA_FUNCTION_FWCODE[media_function]` |

Gate 7 was the key discovery this session: `reload_with_new_persona` (`FUN_4202dc00`) checks
`persona+0x4` (= manifest `name` field) against hardcoded fwcodes via strcmp. For `media_function=system`
it must equal `"eb1"` or the device logs `MEDIA_FUNC_SYSTEM character ERROR` and aborts. The field
`persona+0x4` is populated by `parse_new_persona_from_server` (`FUN_420302e8`) from the manifest
top-level `name` field. Fix: server maps `media_function -> fwcode` via `MEDIA_FUNCTION_FWCODE` dict.

---

## Display pipeline (the new work this session)

After the seven gates, three more layers had to be solved before the frame actually rendered:

### Layer 1 — compatible_versions must be a string (not array)
The device reads `compatible_versions` with a string-getter; sending `["1.0"]` (JSON array) stored
empty → `check_and_mirage_video_bin` skipped the file (`filenode->compatible_version empty`).
Fix: emit a comma-joined string e.g. `"1.0"`.

### Layer 2 — Anim context id (the --anim-as flag)
`reload_animation system` assigns context ids from a hardcoded table (`FUN_4202f4c0`):
- `eb_idle_02` → id 43 (idle)
- `eb_listening` → id 44
- `eb_responding` → id 45
- `eb_low` → id 52, `eb_pwon` → id 53, `eb_bye` → id 61
- `eb_selected` → id 47, `eb_confirm` → id 46, `eb_confirm_04` → id 54

A file with an unknown basename (e.g. `static_imaget`) gets id 0 → `random_chosen_animation_in_current_list
error` → boot frame shown instead. Fix: `--anim-as eb_idle_02` renames the pushed file to the registry
basename so it inherits id 43.

### Layer 3 — compatible_versions must match the push version
The device computes `verhex = int(major)*100 + int(minor)` from `compatible_versions` to locate the
file at `/sdcard/Ember/<fwcode>_<verhex>/<name>_<fwcode>_<verhex>.bin`. If `compatible_versions="1.0"`
but the file downloaded to the `eb1_cd/` folder (version 2.5), `check_compatible_in_sdcard` looks in
`eb1_64/` and fails. Fix: `--anim-compat` flag (defaults to `--anim-version`) sets `compatible_versions`
to match the push version.

---

## Encoder bugs fixed (tools/orb_encode.py)

Found from first on-hardware image:

1. **Radial flip**: encoder had `slot 0 = center` but format doc / hardware says `slot 0 = rim (tip)`.
   Fix: `r = ((NLED-1-slot)/(NLED-1)) * max_r`.

2. **Angular direction**: fan spins clockwise; encoder was sweeping CCW → mirrored output.
   Fix: `sign = -1.0 if cw else 1.0`; default `cw=True`; `--ccw` CLI flag for CCW-spinning units.

---

## Key server flags (orb_server.py --push-anims)

| Flag | Default | Purpose |
|------|---------|---------|
| `--push-anims <zip>` | — | Zip containing the .bin to push |
| `--anim-character` | Ember | Character name |
| `--anim-version` | 2.0 | Must exceed NVS version; bump each run |
| `--anim-media-function` | sd | `system` for idle slot; `riddle`/`music`/`story` also work |
| `--anim-as <basename>` | — | Rename file to registry basename (e.g. `eb_idle_02`) for context id |
| `--anim-compat` | =version | Per-file `compatible_versions`; must match `--anim-version` |

---

## Important device behavior (confirmed on hardware)

- **Fan NAND is never wiped.** `persona_set_current_obj: remove old config` drops only the in-RAM
  root list. Factory content survives all pushes and plays as fallback.
- **1-file system push evicts the other 13 factory anims from the root list** (but not from NAND).
  Listening/responding contexts empty until full set restored.
- **Clean fan sync = no blue LED churn.** The reconnect-ring flicker correlates with sync failures.
- **Fan file count as of this session:** ~116 slots (accumulated test uploads, harmless but should be
  purged eventually via 0x32 delete ops during a fan TCP session).

---

## Next steps

1. **Verify encoder fixes** — encode a known asymmetric test image, push, confirm correct orientation.
   If still mirrored, try `--ccw`.
2. **Push a complete system set** — `eb_idle_02` (custom) + 13 factory originals from SD backup,
   so listening/responding/etc. contexts are populated.
3. **Purge fan junk** — server mode that issues 0x32 deletes for accumulated `static_imaget_*` slots.
4. **STT→LLM→TTS pipeline** — transport works (prior sessions); wire Whisper→Qwen→Chatterbox→opus
   and display Robo-Triy / Koggy on the blade during responses.
5. **Ellie persona** — same pipeline, fwcodes `el1`/`elr`/`elm`/`els`.

---

## Commit log this session

```
3b34e05 encoder: fix radial direction and add CW/CCW flag
e338730 fix idle-anim file lookup: compatible_versions tracks push version
a828d47 system/idle reachable no-NAND; add --anim-as for context-id naming
d94bdfc fwcode-is-name correction + MEDIA_FUNCTION_FWCODE map
78c07ac add MEDIA_FUNCTION_FWCODE map; media_function->name for system/riddle/music/story
67fbb18 fix compatible_versions: emit string not array
d5d91ae gate 7: sd folder fallback for system anims
```
