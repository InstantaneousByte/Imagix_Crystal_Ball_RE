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
