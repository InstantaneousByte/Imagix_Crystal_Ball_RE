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
1. builds `animations[]` from the zip's `.bin` entries (optional `<zip>.manifest.json` sidecar
   sets per-file `duration`/`is_bootup`/`order`);
2. when the device sends `FileManager/GetDefaultAssets` (or `GetLocalAudios`), pushes the
   `media_type:"video"` directive on the held-open downchannel, targeted at the learned id;
3. serves the zip at `http://<ip>:<audio_port>/persona/anims.zip` over plain HTTP.

```
python3 server/orb_server.py --push-anims eb_anims.zip --anim-character Ember --anim-version 2.0
```

**Delivery timing (hardware note 2026-06-19):** the device emits `GetLocalAudios`/`sendUpdatePersona`
only **once**, early, during the busy boot — and tears the downchannel down ~6 s later, so a
single push on that request can be missed before the device reads it. The server therefore pushes
`UpdateDefaultAssets` on **every** downchannel open (the device re-opens it every ~12 s as a
keepalive, when it's idle and reliably reads directives) and stops once the device GETs the zip
(`ASSET_SERVED`). Confirm the directive is active by the startup log line
`[anims] ARMED: will push FileManager/UpdateDefaultAssets`.

## 6. Open items (trial-confirmable; NVS reflash = clean undo)
- **Header `name`** — RESOLVED 2026-06-19: `FileManager`/`UpdateDefaultAssets` (inbound name enum
  `0x35`). Confirmed on hardware that `GetDefaultAssets` is dropped (outbound-only name).
- **`update_type` / `media_function`** exact accepted values.
- **Version compare** — which NVS key (`AN_VER_STR` / `EMBER_VER` / per-mode `eb_*_ver`) the
  `version`/`compatible_versions` is checked against. We out-version all of them.
- **`url` granularity** — single top-level zip (assumed, matches the audio `eb_us_15.zip`
  pattern) vs per-file. If per-file, the field would live on each `animations[]` entry.

Watch for the firmware logs `url_audio %s`, `pending download`, `Done download data %d`,
`Dowload data failed`, and the fan-sync `0x31` upload frames to confirm each stage.
