# Asset-Update Protocol — the no-SD animation delivery path

How to make the de-clouded base download animation `.bin`s from our server and sync them to
the blade **itself**, with no SD-card handling. Traced end-to-end from the decomp
(`BaseFirmwareDecomp.txt`); field names resolved directly from the binary's `.rodata` pointer
table (status **[V]** unless marked **[U]**). Emitter implemented in
[`../server/orb_server.py`](../server/orb_server.py) (`--push-anims`).

## TL;DR
The device asks for newer assets on boot; **answer it** with a `media_type:"video"` directive
that points at a zip on our box (plain HTTP). The device downloads the zip, extracts to its own
SD staging, writes `syncing_tracking.info`, and fan-syncs to the blade. Mac → device → fan.

## 1. The device asks (outbound)
On boot the persona task emits a request in the **`FileManager`** namespace. The outbound
name builder `FUN_42023344` maps internal event codes → `{namespace, name}`:

| event | namespace | name |
|------:|-----------|------|
| 0x23 | FileManager | `GetLocalAudios` |
| 0x24 | FileManager | `GetDefaultAssets` |
| 0x25 | FileManager | `UpdateState` |
| 0x1a | (Persona) | `GetContent` |
| 0x1c | Persona | `Update` |
| 0x21 | Persona | `Switch` |
| 0x27 | Persona | `UpdateProfileStatus` |

The "Force get latest anims" boot path corresponds to **`GetDefaultAssets`** (0x24).

## 2. The server answers (inbound) — the directive shape
The inbound header parser `olli_data_get_header` (`0x4202046c`) maps `header.namespace` and
`header.name` strings to enums via two `.rodata` tables (namespace `PTR_DAT_42001b2c`, name
`PTR_DAT_42001b30`; each entry `{enum:u32, str:ptr}`). `olli_background_directive_handle` then
dispatches on those enums. The asset handler `FUN_420320ec` fires for namespace
**`FileManager`** (enum `0x11`) with name:

| name | enum | call | path |
|------|-----:|------|------|
| `UpdateLocalFiles` | 0x32 | `FUN_420320ec(.,0)` | code/files |
| `GetLocalAudios` | 0x34 | `FUN_420320ec(.,0)` | audio |
| **`UpdateDefaultAssets`** | **0x35** | **`FUN_420320ec(.,1)`** | **animations (requires `animations[]`)** |

So the response header **must** be `FileManager` / **`UpdateDefaultAssets`**. (`GetDefaultAssets`
is the device's *outbound request* name and is **not** in the inbound table — sending it back gets
the directive dropped after `data_json_handle [465] background directive`, verified on hardware.)

Inside `FUN_420320ec`, `is_video_type_check_msg` (`0x42031ffc`) then keys on `payload.media_type`
(`"video"`/`"sd"`/`"audio"`) to pick the parser. For `"video"` it also requires a non-empty
`payload.animations[]`.

**Payload schema** (field names verified against the `0x42002db4`–`0x42002e04` `.rodata`
pointer table):

```jsonc
{
  "header": { "namespace": "FileManager", "name": "UpdateDefaultAssets",  // inbound name enum 0x35; GetDefaultAssets is outbound-only
              "messageId": "...", "sessionId": "<device_id>",
              "target": { "deviceIDs": ["<device_id>"] } },
  "payload": {
    "persona": "buddyos_official_ember",
    "character": "Ember",
    "media_type": "video",            // REQUIRED, the routing trigger
    "media_function": "both",         // [U] one of the 6 media-function names
    "version": "2.0",                 // must exceed the NVS version (EMBER_VER=1.0)
    "update_type": "incremental",     // [U] exact value
    "total_files": 1,
    "build_date": "2026-06-19 10:00:00",
    "is_new_version": true, "is_new_character": false, "is_factory_update": false,
    "root_path": "/sdcard/Ember",
    "url": "http://10.0.0.176:9001/persona/anims.zip",   // the zip; plain HTTP (see §4)
    "animations": [
      { "name": "eb_idle_02_eb1_64.bin", "origin_name": "eb_idle_02_eb1_64.bin",
        "size": 9999360, "compatible_versions": ["1.0"],
        "order": 1, "duration": 6000, "is_bootup": false }
    ]
  }
}
```

Notable: there is **no hash/md5/sha/crc field anywhere** in the schema (the full field-pointer
table was dumped — `url`, `size`, `order`, `duration`, `is_bootup`, `compatible_versions`,
`origin_name`, `has_sd`, `has_fan`, `syncing_sd_done`, `syncing_fan_done`, `files`, etc., but no
integrity field), so no checksum is required.

## 3. Download (no SD touching)
Because `payload.url` is present, the handler sets the has-url flag and spawns the **`dwload_file`**
worker thread (`FUN_420045f0(..., PTR_FUN_42003098, ...)`). The worker calls
`download_file_handler` (`0x42029b18`), which does a plain **HTTP GET** of `url` (the
`esp_http_client` here verifies TLS against the Mozilla bundle, so the URL **must be `http://`**),
retries ≤5×/file, and counts completions against `total_files`. On success the zip is run through
`zip_extract` into `/sdcard/<Char>/<code>_<profile>/` (e.g. `/sdcard/Ember/eb1_64/`).
Orchestrated by `olli_persona_start_sync_new_persona` / `workloop_download_file` (`0x42032308`).

## 4. Bridge to the fan
After download, the device writes **`/sdcard/syncing_tracking.info`** (+ `syncing_dependency.info`)
— `write_syncing_tracking_info` (`0x4202ee18`). `olli_read_persona_from_tracking` (`0x42031db4`)
then reads it, parses with the shared manifest parser `parse_new_persona_from_server`
(`0x4202ff68`), **read-then-deletes** the tracking file, and fires
`start_sync_data_to_fan_via_tcp` (`0x4202d7f0`) → the `.bin`s push to the blade over TCP
`172.10.10.1:4800` → HC32 → NAND (see [`fan_tcp_protocol.md`](fan_tcp_protocol.md)).

`parse_new_persona_from_server` is the **same schema** as the on-SD `character.info` /
`syncing_tracking.info` — that is why the manifest fields match those files exactly.

## 5. Server recipe (implemented)
[`orb_server.py`](../server/orb_server.py) `--push-anims <zip>`:
1. builds the per-file list from the zip's `.bin` entries (optional `<zip>.manifest.json` sidecar
   sets per-file `duration`/`is_bootup`/`order`) AND extracts each `.bin`'s **raw uncompressed
   bytes** into memory;
2. on **every** downchannel open, pushes the `media_type:"video"` `UpdateDefaultAssets` directive
   on the held-open downchannel, targeted at the learned id, with each file's `url` pointing at its
   raw `.bin`;
3. serves each raw `.bin` at `http://<ip>:<audio_port>/persona/bin/<name>` (octet-stream).

```
python3 server/orb_server.py --push-anims eb_anims.zip --anim-character Ember --anim-version 2.0
```

**The device does NOT unzip (gate 5, hardware 2026-06-19 `194632`).** `download_file_handler` does a
plain HTTP GET of the per-file `url` and writes the response body **straight** to
`/sdcard/<code>/<name>_<code>.bin`, then compares the byte count to the manifest `size`. Serving a
zip (167 KB compressed) against a `size` of 6.85 MB fails with
`ERROR File size real 6854400, written = 167070` → 3 retries → `persona_force_reboot`. So the `url`
must serve the **raw uncompressed** `.bin` and `size` must equal its real byte length. (The device
shows the `bu_inprogress_67.bin` "UPDATE IN PROGRESS" frame while downloading.)

**Delivery timing (hardware note 2026-06-19):** the device emits `GetLocalAudios`/`sendUpdatePersona`
only **once**, early, during the busy boot — and tears the downchannel down ~6 s later, so a
single push on that request can be missed before the device reads it. The server therefore pushes
`UpdateDefaultAssets` on **every** downchannel open (the device re-opens it every ~12 s when idle)
and stops once the device GETs the zip (`ASSET_SERVED`). Confirm the directive is active by the
startup log line `[anims] ARMED: will push FileManager/UpdateDefaultAssets`.

## 6. Payload schema (hardware-reverse 2026-06-19 — the FIVE gates)
Routing reaches the asset handler only after three gates, each verified on hardware:

1. **Header name** — `FileManager`/`UpdateDefaultAssets` (inbound name enum `0x35` →
   `olli_background_directive_handle [955] "update default"` → `FUN_420320ec(.,1)` video path).
   `GetDefaultAssets` is OUTBOUND-only and is dropped.
2. **Delivery** — push on every downchannel open (see §5), else the one-shot is torn down.
3. **Payload structure** — the video parser `FUN_42030d84` + `parse_new_persona_from_server`
   (`FUN_420302e8`) need a **nested** shape, NOT a flat `animations[]` of bare files:
   - `payload.dependencies` **must be a JSON array** (even `[]`). If absent/not-an-array the parser
     logs `new_persona_not_the_same` and returns before touching animations.
   - `payload.animations[]` — each entry is a **full persona manifest**:
     `{name, persona, character, version, update_type, total_files, build_date, is_new_version,
     is_new_character, is_factory_update, media_type:"video", media_function, files:[…]}`.
   - each `files[]` entry: `{name, url, size, compatible_versions, order, duration, is_bootup}`.
     `name`/`url`/`size`/`order`/`duration` are required; **`url` is per-file** (the zip; the device
     GETs it over plain HTTP and zip-extracts the named file). There is NO top-level `url` in the
     video path (that field is the audio-url branch).
   - `media_function` ∈ **{system, riddle, music, story, sd}** — validated against a 6-entry
     `{str,enum}` table (`PTR_PTR_s_A_42002f24`); table index 0 (`'A'`) is explicitly rejected and
     **`"both"` is NOT valid** (it is a *character* code). The base Ember idle/responding set is the
     **`system`** function (`system_fw_code=eb1`).

   Only when a `files[]` entry needs updating does the parser bump the needs-download flag
   (`state+0x78`); then `FUN_420320ec` spawns the `dwload_file` thread and logs
   `persona parsed dwload`. A flat `animations[]` with one top-level `url` parses without error but
   never sets `+0x78`, so nothing downloads (the 2026-06-19 `190842` symptom).

4. **Persona display name** — after the parser accepts the manifest and spawns the download thread,
   `olli_persona_start_sync_new_persona` calls `persona_compare_local_data` (`FUN_42031270`), which
   matches the manifest **`persona`** field against the firmware character table `PTR_DAT_42002e24`
   = `{OH_UNKNOW, Bootup Animation, Ellie the fairy, Ember the Baby Dragon, both}` (display name →
   code `oh mg/Bootup/Ellie/Ember/both`). Only indices 2–4 (`Ellie the fairy`/`Ember the Baby
   Dragon`/`both`) are valid. The `buddyos_official_*` identifier does **not** match → logs
   `Error persona <id>` → returns 0 (`RET_UNKNOW`) → sync cleans up, no download (the 2026-06-19
   `193354` symptom). So the manifest `persona` MUST be the display name, e.g. **`Ember the Baby
   Dragon`** (not the identifier). On a match it calls `find_character_code_and_version`
   (`FUN_420310f4`), which returns 0 (→ proceed) when
   `/sdcard/<code>/<name>_<version*100 hex>/character.info` does **not** exist — true for any version
   we out-bump, so this passes once the persona name matches.

`parse_new_persona_from_server` is the **same schema** as the on-SD `character.info` /
`syncing_tracking.info` — a persona block plus a `files[]` array — which is why those files match.

### Still trial-confirmable (NVS reflash = clean undo)
- Manifest-level **`name`** value (currently the character `"Ember"`; forms the `<name>` in the
  character.info folder path). RESOLVED: `persona` must be the firmware **display name** (gate 4).
- **Version compare** target key — we out-version all of `system_version (1.0)` /
  `compatible_versions (["1.0"])`, so this should pass regardless.

Watch the firmware logs `persona parsed dwload`, `pending download`, `Done download data %d`,
`Dowload data failed`, and the fan-sync `0x31` upload frames to confirm each stage.
