# ESP32-S3 Main Board Firmware Analysis

Firmware: `olli_esp_patch_build_1.16_13_May_2026.bin`  
Size: 4,242,768 bytes  
IDF: 5.5.0  
Project: `olli_esp_sdk`  
Built: Apr 7 2026  
Entry: `0x4037a56c`  
Flash chip: 16MB  
Image at flash offset: `0x20000`  
Secure boot: none (`secure_version = 0`)  

Analyzed with Ghidra. 9946 functions found after loading the correct segment map (the critical fix — seg3 IROM was missing from naive binary loads).

---

## Segment map

| Seg | Load VA | Length | File offset | Description |
|-----|---------|--------|-------------|-------------|
| 0 | `0x3c1d0020` | `0x223808` | `0x20` | DROM (rodata, strings) |
| 1 | `0x3fc9bf00` | `0x8a80` | `0x223828` | DRAM |
| 2 | `0x40378000` | `0x3d60` | `0x22c0a8` | IRAM |
| 3 | `0x42000020` | `0x1cbbe0` | `0x230020` | **IROM (main code)** |
| 4 | `0x4037bd60` | `0x100f0` | `0x22fe08` | IRAM |
| 5 | `0x600fe000` | `0x20` | `0x22fef8` | RTC |

Seg3 is fully packed (0 bytes of trailing 0xFF) — a full scan finds **zero `0xFF`/`0x00` runs ≥64 B anywhere in the code segment**, i.e. **no free space** for a spliced blob. The earlier plan to use the "cmd1+cmd2 stub region" at `0x42043c40` was **WRONG**: that region is live code, and flashing over it broke boot audio. See [AUDIT.md](AUDIT.md).

> **"No free space" means no room *inside the mapped code segment* — not that the chip is full.** There is ≈0.95 MB of slack at the tail of `ota_0` and a whole spare 5 MB `ota_1` slot, but that flash is **not mapped as executable** by the running image. Using it for code means *appending an IROM segment* (64 KB-aligned, + checksum/SHA256 fixup), i.e. image restructuring — not an in-place splice. See [flash_layout.md](flash_layout.md).

---

## Key function addresses

### Fan TCP / upload

| Address | Name | Signature |
|---------|------|-----------|
| `0x4202980c` | `fan_sync_binary_file` | `int(conn, src_path, dst_name, flag)` |
| `0x420296e8` | `send_request_upload` | `int(conn, filename, filesize)` |
| `0x4202919c` | `send_file_data` | `int(conn, path, flag)` |
| `0x42029150` | `tcp_send_all` | `int(conn, buf, len)` |
| `0x4202cbc4` | `tcp_send_ctr_fan_delete` | `void(conn, filename)` |
| `0x4202cc0c` | `tcp_send_ctr_fan_change_name` | `void(conn, ...)` |
| `0x4202cdc4` | `tcp_send_ctr_fan_query_list` | `void(conn, buf, len)` |

Fan sync context: `DAT_42002bb0` → pointer to struct. `*(int*)(*DAT_42002bb0 + 0x44)` = live TCP socket fd. Set to `0xffffffff` after each sync completes (connection is per-sync, not persistent).

### Persona / animation management

| Address | Name | Notes |
|---------|------|-------|
| `0x42032308` | `olli_persona_start_sync_new_persona` | Main persona sync initiator |
| `0x4202ee18` | `write_syncing_tracking_info` | Writes `/sdcard/syncing_tracking.info` |
| `0x4202ed40` | `clear_sync_tracking_files` | Deletes both tracking files |
| `0x4202dc00` | `reload_with_new_persona` | Reads tracking file, drives sync |
| `0x4202d7f0` | `start_sync_data_to_fan_via_tcp` | Iterates file list, calls fan_sync_binary_file |
| `0x4202dfb0` | `check_queue_msg_control` | Main message dispatcher (~1179 lines) |
| `0x420317d4` | `check_persona_sdcard_existing` | Returns 2=local files exist, 1=needs download |
| `0x4202f03c` | `add_new_persona_amins_update_fan` | Adds persona to fan update queue |

### Configuration

| Address | Name | Notes |
|---------|------|-------|
| `0x42009f28` | `configCheckFistTime` | Table-driven first-boot NVS init. Writes "default" as cloud endpoint on first boot. Table at `0x3fca4c20`. |
| `0x42015f18` | `file_exists` | `int(path)` |
| `0x42015ef4` | `file_delete` | `void(path)` |
| `0x42015f34` | `get_file_size` | `int(path)` |

### Console command stubs

| Address | Name | Status |
|---------|------|--------|
| `0x42043808` | `cmd1` | real ~20-byte handler; injection patch ABANDONED (see AUDIT.md) |
| `0x42043c40` | `cmd2` | **real handler** → `FUN_42005cbc(0)` — NOT free space |
| `0x42043c2c` | `cmd3` | **real handler** → `FUN_4200f1e4(+1)` — NOT free space |
| `0x42043c18` | `cmd4` | **real handler** → `FUN_4200f1e4(-1)` — NOT free space |
| `0x420438a4` | `cmd5` | Debug: cycles model number 1↔2 |
| `0x420438f0` | `fan` | Dispatcher: `query_video_list` + `delete` |

### lwip / libc — CORRECTED 2026-06-14 (see [ADDRESS_CORRECTIONS.md](ADDRESS_CORRECTIONS.md))

| Function | OLLI address |
|----------|-------------|
| `lwip_socket` | `0x420b8588` |
| `lwip_connect` | `0x420b8174` |
| `lwip_setsockopt` | `0x420b8bf0` |
| `lwip_send` | `0x420b84f4` |
| `lwip_close` | `0x420c900c` |
| `fopen` | `0x421a7684` |
| `fclose` | *(unconfirmed — `0x421a785c` is `fseek`, not `fclose`)* |

---

## SD card paths

- Mount point: `/sdcard` (FATFS/SDMMC)
- Secondary: `/spiffs` (wifi.txt)
- Persona content root: `/sdcard/%s/%s_%02x` (character/name/version_hex)
- PCB test audio: `/sdcard/pcb_testing/`
- Wake word models: `/sdcard/ww_model/heyember.bin`, `/sdcard/ww_model/hiellie.bin`
- Sync tracking: `/sdcard/syncing_tracking.info`, `/sdcard/syncing_dependency.info`

---

## Boot sequence

```
WiFi → cloud registration (olli_h2_task, HTTP/2) → fan link → check bu_on in displist → home screen
```

The boot screen blocks until the cloud registration completes. `FUN_4202d050` checks for `bu_on` in the fan's file list to determine when to advance to the home screen. DNS-blocking the cloud endpoint keeps the device at the boot screen indefinitely — however the serial console and fan TCP are available before this point.

---

## cmd1 patch (ABANDONED — see [AUDIT.md](AUDIT.md))

> Does not work on this firmware: no free flash for the blob, and the handler overflows the console task. Boot audio was lost when flashed. Code is correct; placement is impossible. Kept for reference.

See [`cmd1_stub/`](../cmd1_stub/) for the complete IDF 5.5 project.

Patch placement (invalid):
- Blob (fan_connect + cmd1_handler, 233 bytes) → cmd2 stub space at `0x42043c40` (file offset `0x273c40`)
- 3-byte jump at cmd1 entry `0x42043808` (file offset `0x273808`) → `0x42043cc8` (cmd1_handler in blob)

```bash
python3 splice_patch.py fw_main.bin cmd1_patch.bin \
    0x273c40 0x273808 0x42043c40 136
esptool.py write_flash 0x20000 fw_patched.bin
```
