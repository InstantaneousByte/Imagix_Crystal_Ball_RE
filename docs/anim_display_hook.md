# Animation Display Hook — making a synced file actually render

A file synced to the blade's NAND (gates 1–7) is **stored but not displayed**. The device
plays animations by *playlist name* (`Anim_man: starting play with name [eb_idle_02_eb1_64.bin]`);
the blade renders whatever NAND slot currently answers to that name. Getting our frame on screen
means getting our content onto a slot the playlist already plays.

## The integration step: `check_and_mirage_video_bin` (`FUN_4202d3f8`)
Runs in the sync finalize. "Mirage" = the display/aliasing pass. For each just-synced file node it
branches on `compatible_versions`:

```c
if (filenode.compatible_versions == empty)      // field +0x1c == 0
    log("filenode->compatible_version empty %s");  // <-- SKIP, file never aliased
else
    ... tokenize compatible_versions on ',' ...
    ... FUN_4202f400 builds the playlist name per token ...
    ... find that name in the fan video list, change_name_vid (0x34) to alias ...
```

**Bug found 2026-06-19:** the server sent `compatible_versions` as a JSON **array** `["1.0"]`, but
the device reads it with a string-getter (the on-SD `syncing_tracking.info` stored it as the string
`""`, and the mirage parses each comma-token as `"%d.%d"`). An array yields empty → the mirage
skipped every push we ever made. **`compatible_versions` MUST be a comma-separated STRING** (e.g.
`"1.0"` or `"1.0,2.0"`). Server now emits a string (`build_animations_manifest`).

## Playlist naming — `FUN_4202f400`
Builds the canonical fan filename from `printf("%s_%s_%02x.bin", base, fwcode, ver)`:
- `eb_idle_02` + `eb1` + (1.0×100 = 0x64) → **`eb_idle_02_eb1_64.bin`**.
- `param_4 != 0` (factory) uses the shorter `"%s_%02x.bin"` form.

The `fwcode` arg is the persona's `+0x4` field. Two distinct naming schemes exist on the blade:
- **Factory/base anims:** `<base>_<fwcode>_<verhex>.bin` — fwcode `eb1`/`ebr`/`ebm`/`ebs` (the
  playlist names the device plays).
- **Downloaded custom files:** `<name>_<Character>_<verhex>.bin` — uses the **character** string
  (`Ember`), e.g. `static_imaget.bin_Ember_c9.bin`.

**RESOLVED 2026-06-19:** `+0x4` is the manifest **`name`** field (parser `puVar15[1] =
FUN_4202f380(name)`), fully server-settable. Sending `name="Ember"` (the character) made the mirage
build `<base>_Ember_<ver>.bin` (matches no `eb1` playlist slot) AND made gate 7 fail. Sending
`name="eb1"` makes the mirage build real `<base>_eb1_<ver>.bin` names AND clears gate 7's strcmp.
**No NAND/hardware step is needed to reach the system/idle set — it was a wrong field value, not a
wall.** The server maps `media_function -> name` fwcode via `MEDIA_FUNCTION_FWCODE`.

## Fan control opcodes (device → blade, `FUN_4202c8a8(conn, payload, OP, x)`)
| OP | sender | action (HC32 handler) |
|----|--------|------------------------|
| `0x31` | `send_request_upload` (`FUN_420296e8`) | upload a file to NAND (`[size BE4][name]`). 0x00 ok / 0x82 dup / 0x80 busy. |
| `0x32` | `tcp_send_ctr_fan_request_delete` | delete a slot. |
| `0x34` | `tcp_send_ctr_fan_change_name_vid` (`FUN_4202cc0c`) | **rename slot**: payload `[cat][len1][name1][len2][name2]`; HC32 finds slot[cat] holding `name1`, renames to `name2` (content unchanged). NAK 2 = name1 mismatch, 3 = bad slot. |
| `0x35` | apply path / `check_and_mirage` | commit/select active video (sends the active node ptr); fired once per apply when a current video exists. |
| `0x36` | several | no-payload query/commit; returns the live file count (the `Total file N` log). |

## Two ways onto the screen
1. **Feed the mirage** (use the built-in path): non-empty `compatible_versions` + a base-anim
   basename, and overcome the fwcode mismatch (e.g. by matching the `<...>_Ember_<ver>.bin` scheme
   the download path actually creates, or finding where `+0x4` can be set to `eb1`).
2. **Drive the blade directly** (bypass the mirage): we now know the opcodes — after a sync, issue
   our own `0x32` delete of a playlist slot then `0x34` rename of our uploaded file onto that
   playlist name, so the device plays our content under an id it already shows. The catch: the
   device owns the fan TCP socket during sync, so we'd inject via the device, not as a 3rd party.

## Update 2026-06-19 (trial 211145): mirage dead-ends, playlist category is the real lever

The `compatible_versions` string fix worked — the mirage engaged instead of skipping:
```
check_and_mirage_video_bin [static_imaget.bin_Ember_ca.bin] origin[static_imaget.bin] cmp [1.0]
check_and_mirage_video_bin with name [static_imaget.bin_Ember_64.bin]
```
That built name **confirms the fwcode wall**: `FUN_4202f400` used `Ember` (the character) where the
playlist uses `eb1`, and `_64` (compatible_versions 1.0) where our file is `_ca` (v2.2). It searched
the fan for a file that doesn't exist, matched nothing, renamed nothing. The
`change name from [eb_selected_64.bin] to [eb_selected_eb1_64.bin] ... failed` line is the device's
hardcoded special-case (not our file) failing because no `eb_selected_64.bin` exists. **Conclusion:
the mirage can only build `_Ember_` names and only renames files INTO our naming scheme (target =
our long name), never out to an `eb1` playlist name. It cannot display a manifest-pushed file.**

**The real reason our file never plays:** `Anim_man reload_animation` (the playlist builder) loads
only the **system / music / riddle / story** categories, each scanning
`/sdcard/<character>/<fwcode>_<verhex>/character.info` (`eb1_*`/`ebm_*`/`ebr_*`/`ebs_*`). **There is
no `reload_animation sd`** — an `sd` file syncs, registers, and sits in the one bucket the playlist
never reads. We chose the single category that passes gate 7 *and* is unplayable.

**The opening:** gate 7's reload skips the fwcode strcmp for `riddle`/`music`/`story` too (only
`system` strcmps), so those three pass gate 7 **and** are playlist categories. To land a file where
`reload_animation` finds it, the download folder's `<name>` must be the function fwcode (so the
manifest top-level `name` = `ebm`/`ebr`/`ebs`, not the character). Server now maps this automatically
(`MEDIA_FUNCTION_FWCODE`): `--anim-media-function music` → folder `/sdcard/Ember/ebm_<ver>/`.

Caveat: `music`/`riddle`/`story` anims play in their *context* (music playback / riddle game /
story), not the always-on idle (which is `system`/`eb1`, gate-7-blocked). So loading our file into
one of these proves the playlist path (`Anim_man: adding id NN name [...]`); making it *visible* may
need triggering that context. Next experiment: `--anim-media-function music --anim-version 2.0`,
watch for our file in the `reload_animation music` add-list after the apply-reboot.

Fallback if the playlist path resists: NAND-level Trojan (rewrite an `eb1` slot's content on the
blade directly — we have the HC32 fw + POV format), or MITM the device↔blade `4800` TCP. Both
bypass the device's category logic entirely.

## BREAKTHROUGH 2026-06-20 (trial 002136): system/idle reachable from the server, no NAND

`--anim-media-function system` with `name="eb1"` (the corrected fwcode = manifest name field):
- **Gate 7 PASSED** — no `MEDIA_FUNC_SYSTEM character ERROR`. The mirage built `static_imaget.bin_eb1_64.bin` (eb1 scheme), and `fan_sync_set_ember_character_code [eb1][1]` registered it as **function 1 (system)**.
- The update **persisted**: after reboot, `system_version = [2.4]`, `check_character_animation_exist_in_sdcard [Ember][eb1][2.4]`, `CMD_SYNC_CHARACTER`.
- **Our file entered the system rotation**: `reload_animation system → adding id 0 name [static_imaget.bin_eb1_cc.bin]`.

**Proven: the system/idle category is reachable purely from the server — no NAND/hardware step.**

Two remaining issues, both server-fixable:
1. **Whole-set replacement.** A 1-file system update logs `persona_set_current_obj: remove old config` and drops the other 13 anims (`reload_animation done 1`). The factory set is evicted (restore from SD backup, or push a complete set).
2. **id 0 = no context.** Anim context ids come from a hardcoded registry (`FUN_4202f4c0` calls in `reload_animation system`): each known **basename** → an id (`eb_idle_02`→43 idle, `eb_responding`→45, `eb_listening`→44, `eb_low`→52, `eb_pwon`→53, `eb_bye`→61, `eb_selected`→47, `eb_confirm`→46, `eb_confirm_04`→54). `static_imaget` matches nothing → id 0 → `random_chosen_animation_in_current_list error 1 to 0` → falls back to the boot frame (audio still plays; that's a separate path).

**Recipe to take the idle slot:** name the pushed file `eb_idle_02` so the on-fan name is
`eb_idle_02_eb1_<verhex>.bin` and the registry assigns it id 43. New flag `--anim-as eb_idle_02`:
```
python3 server/orb_server.py --push-anims static_test.zip --anim-character Ember \
    --anim-version 2.5 --anim-media-function system --anim-as eb_idle_02
```
Caveat: a 1-file `system` push still evicts the other anims (issue 1), so the device will have only
an idle anim; listening/responding contexts will be empty until we push a full set (our frame as
`eb_idle_02` + the 13 originals from the SD backup). For a first *visible* result, idle-only is enough.

## trial 004124: id 43 achieved; remaining blocker = compatible_versions vs file folder

`--anim-as eb_idle_02` worked: `reload_animation system -> adding id 43 name [eb_idle_02_eb1_cd.bin]`
(idle context, not id 0). But the frame still didn't render. Root cause: the file downloads to the
**push-version** folder (`/sdcard/Ember/eb1_cd/` for 2.5) but the per-file `compatible_versions` was
still the default **1.0**, so the device computed verhex `64` and looked in the wrong place:
```
check_compatible_in_sdcard: node [eb_idle_02] compatition version [1.0]
check_compatible_in_sdcard: this file not exist [/sdcard/Ember/eb1_64/eb_idle_02_eb1_64.bin]
random_chosen_animation_in_current_list error 1 to 0   <- 1 candidate, 0 valid
```
Same root cause broke the mirage alias: `change name from [eb_idle_02_eb1_64.bin] to
[eb_idle_02_eb1_cd.bin] ... failed` (the `_64` file isn't where it looked either).

Two confirmations from this run:
- **The fan is never wiped.** `persona_set_current_obj: remove old config` drops only the device's
  in-RAM root list; the fan keeps all 116 slots incl. the factory set. When our anim failed
  validation the device fell back to a fan-resident default and the **factory idle played** — i.e.
  factory content survives every push.
- **Clean sync = no LED churn.** This run logged `sd[1] fan[1] fail[0] ref[1]` (file truly landed on
  the fan); the downchannel didn't thrash, so the blue reconnect-ring flicker was absent.

**Fix:** new `--anim-compat` flag, defaulting to `--anim-version`, sets per-file
`compatible_versions` so the lookup folder matches where the file downloaded. Next test:
```
python3 server/orb_server.py --push-anims static_test.zip --anim-character Ember \
    --anim-version 2.6 --anim-media-function system --anim-as eb_idle_02
```
Expect `check_compatible_in_sdcard: checking path [/sdcard/Ember/eb1_ce]` (correct folder), NO
"this file not exist", NO `random_chosen_animation` error -> the idle picker plays our frame.

## trial 005534: CUSTOM FRAME RENDERING ON BLADE — PIPELINE COMPLETE

**This is the milestone run.** All seven gates cleared, compatible_versions matched push version,
`--anim-as eb_idle_02` gave id 43. Key log lines confirming success:

```
Anim_man: adding id 43 name [eb_idle_02_eb1_ce.bin] to root list
Anim_man: starting play with name force 1 [eb_idle_02_eb1_ce.bin] duration 4000
on_evt_queue_cb: update user state ["state": "warm_up_standby"]    <- held 3+ min, no errors
```

Custom frame rendered on the holographic fan blade. Server-only, no NAND, no cloud, no hardware mod.

**Two rendering bugs identified from first on-hardware image:**

### Bug 1 — Radial flip (encoder)
Format doc (ground-truthed from hardware solid-color test files) says `slot 0 = blade tip (rim)`.
Encoder had `slot 0 = r=0 (center)` — opposite. Content drawn near the rim appeared near the hub.
Fix: `r = ((NLED-1-slot) / (NLED-1)) * max_r` in `cartesian_to_polar_column`.

### Bug 2 — Angular direction (encoder)
Fan physically rotates clockwise. Encoder was sweeping CCW (positive angle = CCW in screen-space
y-down coords), so all asymmetric content was horizontally mirrored on the blade. Text/logos would
appear backwards.
Fix: `sign = -1.0 if cw else 1.0` before the angle calculation; default `cw=True`. CLI flag `--ccw`
available if a fan unit runs CCW.

Both fixes committed to `tools/orb_encode.py`. The `--ccw` flag allows per-unit override.

---

## COMPLETE WORKING COMMAND SEQUENCE (cloud-free custom idle)

```bash
# 1. Encode your image (PNG or GIF) -> .bin
python3 tools/orb_encode.py my_image.png custom_idle.bin --seconds 4

# 2. Stage it in a zip (name inside zip doesn't matter)
zip static_test.zip custom_idle.bin

# 3. Push — bump version each run so the device sees it as new
python3 server/orb_server.py \
    --push-anims static_test.zip \
    --anim-character Ember \
    --anim-version 2.7 \
    --anim-media-function system \
    --anim-as eb_idle_02

# Repeat with --anim-version 2.8, 2.9, etc. for subsequent pushes.
# The device checks version > current; compatible_versions auto-matches push version.
```

**What the device does:**
1. Downloads `custom_idle.bin` as `eb_idle_02` from local server
2. Saves to `/sdcard/Ember/eb1_<verhex>/eb_idle_02_eb1_<verhex>.bin`
3. Mirage pass aliases it into the playlist
4. `reload_animation system` picks it up with **id 43** (idle context)
5. Idle picker selects it; blade renders your frame at idle

**Caveats:**
- 1-file system push **evicts the other 13 factory anims** from the in-RAM list (not from fan NAND).
  Factory content still plays as fallback when our anim fails validation. To restore the full set:
  push your custom frame as `eb_idle_02` plus the 13 originals from SD backup in one batch, OR
  restore `/sdcard/Ember/eb1_64/` from backup and reboot with `system_version` downgraded.
- The fan is never wiped. All 116+ accumulated test slots remain on NAND until a purge-fan sweep
  issues 0x32 (delete) ops during a fan TCP session.
- Listening/responding contexts (id 44/45) are empty in a 1-file push — the device may error
  if triggered to speak before the full set is restored.
