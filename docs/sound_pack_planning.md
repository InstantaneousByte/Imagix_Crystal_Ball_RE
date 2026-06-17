# Sound-pack planning sheet

Length budgets are **approximate** and assume mono OGG Vorbis. Leave ~5% margin for
container overhead. Rule of thumb: seconds = slot_bytes / (kbps x 125).
Encode: `ffmpeg -i src.wav -ac 1 -ar 48000 -c:a libvorbis -b:a 32k out.ogg`, then check
`ls -l` against the slot. `-b:a 32k` is the sweet spot; drop to `24k` for the tight ones.

| ✓ | id | clip | slot bytes | ~sec @32k | ~sec @24k | use |
|---|----|------|-----------:|----------:|----------:|-----|
| ☐ | 0 | `character_selection_narrator_voice` | 314,990 **BIG** | 75s | 100s | persona pick (UNUSED - repurpose!) |
| ☐ | 1 | `connected_narrator_voice` | 43,207 | 10s | 14s | connected voice |
| ☐ | 2 | `ap_on` | 46,310 | 11s | 15s | AP mode on |
| ☐ | 3 | `cheer_full` | 56,919 | 14s | 18s | celebration SFX |
| ☐ | 4 | `poweron` | 23,553 | 6s | 7s | boot power-on |
| ☐ | 5 | `bootup` | 53,818 | 13s | 17s | boot logo chime |
| ☐ | 6 | `wifi_connected_done` | 17,544 | 4s | 6s | wifi connected ok |
| ☐ | 7 | `ap_off` | 34,403 | 8s | 11s | AP mode off |
| ☐ | 8 | `cancel_factory_reset` | 14,719 | 3s | 5s | cancel reset |
| ☐ | 9 | `confirm_factory_reset` | 64,368 | 15s | 20s | confirm reset |
| ☐ | 10 | `setup_device` | 381,498 **BIG** | 91s | 121s | device setup (UNUSED - repurpose!) |
| ☐ | 11 | `not_ready` | 57,937 | 14s | 18s | not ready yet |
| ☐ | 12 | `insert_sdcard` | 13,327 | 3s | 4s | no SD card |
| ☐ | 13 | `change_character_ember` | 57,533 | 14s | 18s | switch to Ember |
| ☐ | 14 | `change_character_ellie` | 38,781 | 9s | 12s | switch to Ellie |
| ☐ | 15 | `connect_inet_error` | 66,202 | 16s | 21s | no internet |
| ☐ | 16 | `connect_server_error` | 64,770 | 15s | 21s | server unreachable |
| ☐ | 17 | `loss_wifi` | 35,996 | 9s | 11s | lost wifi |
| ☐ | 18 | `error_wifi` | 62,141 | 15s | 20s | wifi error |
| ☐ | 19 | `setup_with_wifi` | 19,083 | 5s | 6s | setup via wifi |
| ☐ | 20 | `setup_with_ble` | 19,179 | 5s | 6s | setup via BLE |
| ☐ | 21 | `energy_up` | 25,685 | 6s | 8s | power-up SFX |
| ☐ | 22 | `silent` | 5,461 | 1s | 2s | silence pad |
| ☐ | 23 | `wake_up` | 13,089 | 3s | 4s | wake acknowledge |
| ☐ | 24 | `confirm_standby` | 9,115 | 2s | 3s | go to standby |
| ☐ | 25 | `update_in_progress` | 19,495 | 5s | 6s | updating... |

Total across all 26 slots: ~1523 KB.

## Quick guidance

- **Tight (<15 KB):** `insert_sdcard` (~3s), `confirm_standby` (~2s), `silent` (~1s),
  `wake_up` (~3s), `cancel_factory_reset` (~3s). Keep these to a short word/sting,
  or use `-b:a 24k`.
- **Comfortable (35-66 KB, ~9-16s):** most error/status/persona-switch clips. Plenty of
  room for a spoken line in your character's voice.
- **Boot (id 4/5):** `poweron` ~6s, `bootup` ~12s at 32k. These MUST fit their own slot
  (the boot sequence plays them by id - can't be moved elsewhere).
- **BIG / repurpose (id 0 ~315 KB, id 10 ~381 KB):** never triggered de-clouded. Aim your
  longest pieces here - ~75s (id 0) / ~90s at 32k (id 10). Point them with
  `--set setup_device=long_track.ogg` etc.

Apply the whole pack in one shot:

```
python3 tools/patch_system_audio.py app_final.bin app_audio.bin \
    --set bootup=bootup.ogg --set poweron=poweron.ogg \
    --set connect_inet_error=oops.ogg --set energy_up=yeah.ogg   # ...etc
esptool.py --chip esp32s3 write_flash 0x20000 app_audio.bin
```
