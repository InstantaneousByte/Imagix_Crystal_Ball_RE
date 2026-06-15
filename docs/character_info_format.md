# character.info Format (ground truth)

`character.info` is the **per-character manifest** that lives in each character folder
on the SD-NAND, e.g. `/sdcard/Ember/eb1_64/character.info`. Unlike
`syncing_tracking.info` (transient, written-then-deleted), `character.info` **persists
at rest** and describes one character's animation set plus its sync state.

**Status: [V] Verified** against three real SD-card files:

| File | name | persona | version | root_path | files |
|------|------|---------|---------|-----------|-------|
| `…450295_character.info` | `eb1` | `Ember the Baby Dragon` | `1.0`  | `/sdcard/Ember/eb1_64` | 4 |
| `…465360_character.info` | `el1` | `Ellie the fairy`       | `1.0`  | `/sdcard/Ellie/el1_64` | 4 |
| `…470479_character.info` | `el1` | `Ellie the fairy`       | `1.03` | `/sdcard/Ellie/el1_67` | 9 |

> ⚠️ This **supersedes** the schema sketch in `syncing_tracking_format.md` and the old
> `examples/character_info_template.json`, both of which conflated `character.info` with
> `syncing_tracking.info`. The earlier doc's `animation_array`, `persona:"buddyos_…"`,
> seconds-based `duration`, and per-file `has_sd`/`has_fan` are **wrong for this file** —
> see "Corrections" at the bottom.

---

## Top-level fields

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `name` | string | **yes** | Short family code, e.g. `"eb1"`, `"el1"`. NOT the display name. |
| `persona` | string | no* | **Display name**, e.g. `"Ember the Baby Dragon"`. (*Parser tolerates absence; defaults to `"null"`.) |
| `version` | string | **yes** | e.g. `"1.0"`, `"1.03"`. |
| `root_path` | string | **yes** | Explicit absolute path, e.g. `"/sdcard/Ember/eb1_64"`. Given literally — not built from a format string. |
| `character` | string | **yes** | Character code, e.g. `"Ellie"`. **Empty string `""` is accepted** (present-but-empty); a *missing* key fails the parse. |
| `total_files` | int | **yes** | Count of entries in `files[]`. |
| `build_date` | string | **yes** | ISO-8601, e.g. `"2025-08-25T11:11:00Z"`. |
| `is_factory_update` | bool | no | `false` in all observed files. → persona struct `+0x49`. |
| `syncing_sd_done` | bool | no | SD copy present/ready. `true` in all observed files. → struct `+0x44`. |
| `syncing_fan_done` | bool | no | Fan copy pushed. Present (`true`) only in `el1_67`. → struct `+0x45`. |
| `media_function` | int | no | `1`, present only in `el1_67`. → struct `[0xf]`. |
| `files` | array | **yes** | Per-file array (**this is the real key — not `animation_array`**). |

**Parser:** `olli_video_parse_data_from_sdcard` (decomp ~147348). The hard-required set —
parse returns NULL/`error 1` if any are absent — is **`name`, `version`, `build_date`,
`total_files`, `root_path`, `character`**. It also reads `update_type`, `is_new_version`,
`is_new_character` into the struct, though those were not present in the observed
`character.info` files (they belong to the `syncing_tracking.info` side).

---

## Per-file fields (`files[]`)

| Field | Type | Req? | Notes |
|-------|------|------|-------|
| `name` | string | yes | Animation filename, e.g. `"eb_idle_eb1_64.bin"`. |
| `origin_name` | string | no | Base/role name, e.g. `"el_idle"`. Present only in the `1.03` format. NOT a destination filename. |
| `size` | int | yes | File size in **bytes**. Always a multiple of 34 (see POV note). |
| `order` | int | yes | Display/selection order index. |
| `duration` | int | yes | **Milliseconds** (e.g. `4000` = 4.0 s), not seconds. |
| `is_bootup` | int | yes | `0` for all observed entries. |

---

## Sync state machine (how a file gets pushed to the fan)

Two layers of flags, both confirmed in the binary:

- **character.info (top level):** `syncing_sd_done`, `syncing_fan_done`.
- **syncing_tracking.info (per file):** `has_sd`, `has_fan` — written by the serializer
  (decomp ~143920) from persona-struct per-file bytes `+0xc` (has_sd) and `+0x31` (has_fan).

**Consumer gate — [V] confirmed** in `start_sync_data_to_fan_via_tcp` (`0x4202d7f0`):

```
for each queued file:
    if (has_sd == 0) || (has_fan != 0) || (retries > 4):  skip
    else:
        rc = fan_sync_binary_file(fan_conn, src_path, dst_name, 1)
        if (rc > 0):  has_fan = 1        # mark done, persisted on write-back
```

So a file is pushed to the fan **iff `has_sd != 0 && has_fan == 0`**, and `has_fan` is set
to 1 on success. Making a target file's `has_fan` read as 0 is exactly what forces a
re-push. At the `character.info` level that is `syncing_fan_done: false`.

---

## POV size/timing — [V] validated by these files

Every `size` is divisible by **34** (one POV column = 34 bytes), confirming
`pov_display_format.md`. Clean examples:

| File | size (bytes) | ÷ 68,544 (bytes/rev) | duration | implied fps |
|------|-------------:|---------------------:|---------:|------------:|
| `eb_idle_eb1_64.bin` | 6,854,400 | 100 revs exactly | 4000 ms | 25 |
| `el_idle_el1_64.bin` | 3,427,200 | 50 revs exactly | 2000 ms | 25 |

Non-idle files are whole numbers of *columns* (size ÷ 34 ∈ ℤ) but not always whole
*revolutions*; revolution count is approximate (motor speed varies), matching the doc.

---

## Recipe: replace a character's idle animation with a custom one

Grounded in the consumer gate above; the only empirical unknown is how the top-level
`syncing_fan_done` propagates to per-file `has_fan` during a parse→serialize cycle, so
treat the first run as a test with the `.og` backup ready.

1. Encode your animation to a valid POV `.bin` — size a multiple of **34**, ideally a
   whole number of **68,544-byte** revolutions; see `tools/orb_encode.py`.
2. Back up the original (already present on the card as
   `Ember/eb1_64/Original/eb_idle_eb1_64.bin.og`).
3. Replace `/sdcard/Ember/eb1_64/eb_idle_eb1_64.bin` with your file.
4. In `/sdcard/Ember/eb1_64/character.info`: set that entry's `size` to the new byte
   count, and set top-level `syncing_fan_done: false` (and keep `syncing_sd_done: true`).
5. Reboot, watch the serial log for the sync, restore from `.og` if needed. **Only the
   fan is written — no flash/brick risk.**

---

## Corrections vs. the earlier sketch

| Earlier doc said | Reality |
|------------------|---------|
| array key `animation_array` | **`files`** (serializer also emits `"files"`) |
| `persona: "buddyos_my_character"` | display name, e.g. `"Ember the Baby Dragon"` |
| `duration` in seconds (`4`) | **milliseconds** (`4000`) |
| per-file `has_sd`/`has_fan` in character.info | not here — those live in `syncing_tracking.info`; character.info uses top-level `syncing_sd_done`/`syncing_fan_done` |
| `origin_name` = fan destination filename | base/role name (`"el_idle"`), only in `1.03`+ |
| `root_path` built from `/sdcard/%s/%02x` | given **explicitly** in the file |
