# Function Registry

All addresses are virtual (IROM `0x42000020` base) for the main board ESP32-S3 firmware, or flash `0x0` base for the HC32 blade firmware. Status: [V]=Verified from decompilation, [I]=Inferred from context, [?]=Uncertain.

---

## Main board (ESP32-S3)

### Fan TCP protocol [V]

| Address | Name | Signature | Notes |
|---------|------|-----------|-------|
| `0x4202980c` | `fan_sync_binary_file` | `int(conn, src, dst, flag)` | Top-level upload. Returns 1=ok, 2=dup, <0=err |
| `0x420296e8` | `send_request_upload` | `int(conn, filename, filesize)` | Sends 0x31 upload-request frame |
| `0x4202919c` | `send_file_data` | `int(conn, path, flag)` | Reads file, streams to socket |
| `0x42029150` | `tcp_send_all` | `int(conn, buf, len)` | Full-buffer write, chunked |
| `0x4202cbc4` | `tcp_send_ctr_fan_delete` | `void(conn, filename)` | Delete file from fan |
| `0x4202cc0c` | `tcp_send_ctr_fan_change_name` | `void(conn, ...)` | Rename file on fan |
| `0x4202cdc4` | `tcp_send_ctr_fan_query_list` | `void(conn, buf, len)` | Request file list |
| `0x4202cd44` | `tcp_send_ctr_fan_power_on` | `void(conn, ...)` | Fan power on |
| `0x4202cd84` | `tcp_send_ctr_fan_power_off` | `void(conn, ...)` | Fan power off |

### Fan sync orchestration [V]

| Address | Name | Notes |
|---------|------|-------|
| `0x4202d7f0` | `start_sync_data_to_fan_via_tcp` | Iterates file list, calls fan_sync_binary_file per entry |
| `0x4202dfb0` | `check_queue_msg_control` | Main message dispatcher, ~1179 lines. Type 0x04 = sync needed |
| `0x4202dc00` | `reload_with_new_persona` | Reads syncing_tracking.info, drives sync |
| `0x4202d3f8` | `fan_sync_connection_manager` | Manages connect/disconnect for fan TCP socket |
| `0x4202cf34` | `fan_sync_start_task` | Starts sync FreeRTOS task |
| `0x4202d050` | `fan_sync_post_complete` / `check_swap_bu_on` | Post-boot completion gate — checks for bu_on in fan list |

### Persona / animation [V]

| Address | Name | Notes |
|---------|------|-------|
| `0x42032308` | `olli_persona_start_sync_new_persona` | Checks sdcard existing (ret=2) → local path; else cloud |
| `0x4202ee18` | `write_syncing_tracking_info` | Serializes persona_ctx to JSON → `/sdcard/syncing_tracking.info` |
| `0x4202ed40` | `clear_sync_tracking_files` | Deletes syncing_tracking.info + syncing_dependency.info |
| `0x4202f03c` | `add_new_persona_amins_update_fan` | Adds persona animations to fan update queue |
| `0x420317d4` | `check_persona_sdcard_existing` | Returns 2=RET_SDCARD_EXISTING, 1=needs download |
| `0x42031c6c` | `check_compatible_in_sdcard` | Version compatibility check for SD-resident content |

### Server asset-update pipeline (inbound) [V] — added 2026-06-19 (see [asset_update_protocol.md](asset_update_protocol.md))

The chain that lets the server push anims with **no SD handling**: directive → download → tracking → fan-sync.

| Address | Name | Notes |
|---------|------|-------|
| `0x420320ec` | `olli_persona_server_event_handle` | Master inbound asset dispatcher. Calls the classifier, routes type 1→`FUN_42030d84` (and spawns the `dwload_file` thread if `payload.url` present), 2→`FUN_42030b40`, 3→`FUN_4202a344` |
| `0x42031ffc` | `is_video_type_check_msg` | Classifier. Keys on `payload.media_type`: `"video"`→1, `"sd"`→2, `"audio"`→3. For video also requires non-empty `payload.animations[]` |
| `0x42030d84` | `olli_assert_parse_data_from_server` | Session/video directive parse (type 1). Reads `payload.{url, animations[], dependencies[]}`; writes `syncing_tracking.info` + `syncing_dependency.info` |
| `0x42030b40` | `olli_assert_parse_data_session_directive` | Session directive parse (type 2, `media_type:"sd"`). Reads `payload.url` + `animations[]` |
| `0x4202ff68` | `parse_new_persona_from_server` | Shared persona-**manifest** parser (also parses on-SD `character.info` / `syncing_tracking.info`). Requires `media_type=="video"`. Fields: persona/character/version/update_type/total_files/build_date/is_new_*/is_factory_update/media_function + files[name,origin_name,size,compatible_versions,order,duration,is_bootup,has_sd,has_fan]. **No url field** (manifest only) |
| `0x42029b18` | `download_file_handler` | HTTP GET of the url at `param+0x18` → file. Plain HTTP (client verifies TLS). Logs `HTTP request with url`, `Done download data` |
| `0x42027154` | `Anim_man` (read character.info) | Builds `/sdcard/<Char>/<code>_<profile>/character.info`, reads it, parses via `FUN_4202ff68` |
| `0x42031db4` | `olli_read_persona_from_tracking` | Reads `/sdcard/syncing_tracking.info`, parses via `FUN_4202ff68`, **read-then-deletes**, triggers fan sync |
| `0x42032b30` | `check_character_animation_exist` | Removes old config, parses manifest, calls `FUN_42032308` |
| `0x4202a344` | `parse_persona_audio` (`local_sound`) | Audio asset parser (the `GetLocalAudios` path). Fields: persona/build_date/character/media_type + files[name,language,version] |

### Outbound directive name map [V]

`FUN_42023344` maps internal event codes → `{namespace, name}` for messages the device **sends**:

| event | namespace / name | event | namespace / name |
|------:|------------------|------:|------------------|
| 0x23 | FileManager / `GetLocalAudios` | 0x1c | Persona / `Update` |
| 0x24 | FileManager / `GetDefaultAssets` | 0x21 | Persona / `Switch` |
| 0x25 | FileManager / `UpdateState` | 0x22 | UserEvent / `Event` |
| 0x1a | (Persona) / `GetContent` | 0x26 | ErrorReport / `UpdateError` |
| 0x1d | ClientInformation / `UpdateDeviceInfo` | 0x27 | Persona / `UpdateProfileStatus` |


### File system [V]

| Address | Name | Notes |
|---------|------|-------|
| `0x42015f18` | `file_exists` | Returns nonzero if file exists |
| `0x42015ef4` | `file_delete` | Deletes a file |
| `0x42015f34` | `get_file_size` | Returns file size in bytes, 0 on fail |

### Display / POV control [V]

| Address | Name | Notes |
|---------|------|-------|
| `0x420289f8` | `fan_set_angle` | Sets POV rotation phase offset |
| `0x42028990` | `fan_set_brightness` | Sets LED brightness |
| `0x4202834c` | `fan_query_video_list` | Request file list from fan |
| `0x42028c10` | `fan_delete_file` | Delete file from fan |
| `0x42028924` | `fan_set_speed_high` | Motor speed high |
| `0x42028900` | `fan_set_speed_low` | Motor speed low |

### Configuration / NVS [V] — corrected 2026-06-15 (see [decloud_endpoint.md](decloud_endpoint.md))

| Address | Name | Notes |
|---------|------|-------|
| `0x4200ac64` | `config_schema_register` | Builds in-RAM `{key, default, type}` descriptor table (`DAT_420006ac`). Registers `ENDPOINT_STR` (type 6, default `"default"`), `TOKEN_STR`, `REGISTER_STR`, `CHARACTER_STR`, `LANGUAGE_STR`, etc. |
| `0x42009f28` | `configCheckFistTime` | Config **seed-if-absent** loop. Per key: `getString(key,"NULL")`; if result=="NULL" (absent) → `put(key, schema_default)`; else **keep stored value**. NOT a cloud-endpoint writer. A custom `ENDPOINT_STR` therefore persists across reboot with no patch. |
| `0x42009c7c` | `getString` | NVS read into `std::string`; returns caller default on miss. Logs `nvs_get_str_fail` (ln `0x27d`). |
| `0x42009bc0` | `putString` | NVS write. Logs `nvs_set_str_fail` (ln `0x165`). |
| `0x42009c6c` | `put` | NVS set wrapper used by the seeder; logs `Put data failed size %d %d`. |
| `0x42005ec4` | `RegisterNotifySuccess` | Runs on successful cloud registration; commits server response to NVS: `set_token(+0xe4)`, **`set_endpoint(+0xb4)`**, `user_id`, `REGISTER_STR=1`. **This is what overwrites `ENDPOINT_STR`.** |
| `0x4200aaa8` | `set_endpoint` | `set_config(6, val)` wrapper; sole typed endpoint writer; sole caller is `RegisterNotifySuccess`. |
| `0x42005f06` | *(patch site)* | The `set_endpoint` call inside `RegisterNotifySuccess`. NOP (`25 ba 04`→`f0 20 00`, file off `0x235f06`) to pin `ENDPOINT_STR`. See `tools/patch_pin_endpoint.py`. |

> **Endpoint:** the cloud API base is the NVS string `ENDPOINT_STR` (default `"default"`);
> request URLs are `<ENDPOINT_STR>/connect`. Dead cloud → key stays `"default"` → boot
> stalls. Repoint by writing `ENDPOINT_STR` (no firmware change). The prior "NOP
> `nvs_set_str` at `0x2506f8`" note was **wrong/mislocated** — `0x2506f8` maps into this
> function's literal pool. **However** the endpoint IS overwritten elsewhere — by
> `RegisterNotifySuccess` (`0x42005ec4`) on every registration. Pin it by NOPing the
> `set_endpoint` call at `0x42005f06` (`tools/patch_pin_endpoint.py`) or by controlling the
> registration response.

### Console commands [V]

| Address | Registered as | Notes |
|---------|--------------|-------|
| `0x42043808` | `cmd1` | real handler; injection patch ABANDONED (see AUDIT.md) |
| `0x42043c40` | `cmd2` | real handler → `FUN_42005cbc(0)` — NOT free space |
| `0x42043c2c` | `cmd3` | real handler → `FUN_4200f1e4(+1)` — NOT free space |
| `0x42043c18` | `cmd4` | real handler → `FUN_4200f1e4(-1)` — NOT free space |
| `0x420438a4` | `cmd5` | Cycles model number 1↔2 |
| `0x420438f0` | `fan` | Dispatcher: query_video_list + delete |

### lwip / libc — CORRECTED 2026-06-14 (see [ADDRESS_CORRECTIONS.md](ADDRESS_CORRECTIONS.md))

> The old entries were guessed in the `0x421a8xxx` page; the real lwip BSD layer is in
> `0x420b8xxx`. Re-resolved from real call-signatures. `lwip_socket`/`0x421a8ddc` was a
> mislabel (a string fn), and `0x421a785c` was `fseek`, not `fclose`.

| Function | OLLI address | Note |
|----------|-------------|------|
| `lwip_socket`     | `0x420b8588` | `socket(2,1,0)`; slot `PTR_FUN_420029d0` |
| `lwip_connect`    | `0x420b8174` | tail-calls `netconn_connect`; validates `sin_family`@off1 |
| `lwip_setsockopt` | `0x420b8bf0` | `setsockopt(fd,6,1,…)`; slot `PTR_FUN_42002004` |
| `lwip_send`       | `0x420b84f4` | confirmed |
| `lwip_close`      | `0x420c900c` | confirmed; slot `PTR_FUN_42000024` |
| `lwip_bind`       | `0x420b8030` | httpd bind |
| `lwip_listen`     | `0x420b821c` | httpd listen |
| `lwip_accept`     | `0x420b7ecc` | httpd accept |
| `fopen`           | `0x421a7684` | `fopen(path,mode)`; slot `PTR_FUN_42000394` |
| `fclose`          | *(unconfirmed)* | slot `PTR_FUN_420013dc` was mislabeled — it holds `0x421a785c`, which is **`fseek`** |
| `inet_pton`       | *(unused / old `0x421a8538` was wrong)* | build sockaddr directly: `sin_addr=0x010A0AAC` |

---

## HC32 blade (HC32F460)

### Master loop [V]

| Address | Name | Notes |
|---------|------|-------|
| `FUN_0000cff4` | Master command loop | Init + infinite dispatch on command byte |

### FatFs [V]

| Address | Name | Notes |
|---------|------|-------|
| `FUN_0000c97c` | f_open core | Path parse + open |
| `FUN_0000c660` | open+read file | Used for Config/displist/WifiConfig |
| `FUN_0000ba64` | f_read/f_write block worker | 512B sectors, returns FRESULT |
| `FUN_0000bfe4` | f_close/f_sync | Flush + clear handle |
| `FUN_0000cef0` | handle table lookup | fd → file object |
