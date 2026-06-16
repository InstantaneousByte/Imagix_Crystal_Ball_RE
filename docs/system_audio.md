# Embedded system audio (the 26 baked-in sounds)

All of the orb's system / UI / status sounds are **compiled into the app image**
(DROM segment), not read from the SD card. This is why swapping `narrator/bootup.ogg`
on the card does nothing — the boot chime (and every other system sound) is played
from firmware.

## Mechanism

1. A play request carries a name (e.g. `bootup.ogg`) or a full path
   (e.g. `/sdcard/Ember/en-US/local_voice/foo.ogg`).
2. **Bare names** are routed to a "raw/binary" pipeline; **full `/sdcard/...` paths**
   are routed to the separate SDCARD file player.
3. The raw pipeline calls a filename->id matcher (`FUN_4201256c`). It uses a
   **substring (`strstr`) test**, so any name that merely *contains* a known clip
   name matches — including `/sdcard/narrator/bootup.ogg`, which is why even the
   on-card copy is hijacked to the embedded blob.
4. `play_binary` (`FUN_420124d4`) reads the blob from a fixed table and streams it:
   ```c
   if (id < 0x1a) { start=table[id].start; end=table[id].end;
                    write(start, end-start); return OK; }   // embedded only
   return -1;                                                // no SD fallback
   ```
   The matcher's "no match" sentinel is `0x1a` (26) -> `play_binary` returns error.
   **There is no SD fallback for system sounds.**

## The table

Base file offset **`0x2238f8`**; entries are `{u32 id, u32 start_va, u32 end_va}`,
12 bytes each, ids 0..25. DROM file->VA = `+0x3c1d0000`; slot size = `end - start`.

| id | clip | slot (bytes) | file offset |
|----|------|-------------:|-------------|
| 0  | character_selection_narrator_voice | 314990 | 0x03cf48 |
| 1  | connected_narrator_voice | 43207 | 0x089dba |
| 2  | ap_on | 46310 | 0x09ccec |
| 3  | cheer_full | 56919 | 0x0a81d6 |
| 4  | poweron | 23553 | 0x0c326f |
| 5  | bootup | 53818 | 0x0b6031 |
| 6  | wifi_connected_done | 17544 | 0x0c8e74 |
| 7  | ap_off | 34403 | 0x094685 |
| 8  | cancel_factory_reset | 14719 | 0x0cd300 |
| 9  | confirm_factory_reset | 64368 | 0x0d0c83 |
| 10 | setup_device | 381498 | 0x0e07f7 |
| 11 | not_ready | 57937 | 0x13da35 |
| 12 | insert_sdcard | 13327 | 0x14bc8a |
| 13 | change_character_ember | 57533 | 0x161d9c |
| 14 | change_character_ellie | 38781 | 0x15861b |
| 15 | connect_inet_error | 66202 | 0x16fe5d |
| 16 | connect_server_error | 64770 | 0x1800fb |
| 17 | loss_wifi | 35996 | 0x18fe01 |
| 18 | error_wifi | 62141 | 0x1a0453 |
| 19 | setup_with_wifi | 19083 | 0x153b8c |
| 20 | setup_with_ble | 19179 | 0x14f09d |
| 21 | energy_up | 25685 | 0x198aa1 |
| 22 | silent | 5461 | 0x19eefa |
| 23 | wake_up | 13089 | 0x1af714 |
| 24 | confirm_standby | 9115 | 0x1b2a39 |
| 25 | update_in_progress | 19495 | 0x1b4dd8 |

Total embedded audio: ~1523 KB.

## Replacing a sound: `tools/patch_system_audio.py`

```
python3 tools/patch_system_audio.py app_final.bin app_audio.bin \
    --set bootup=creepy_run.ogg --set poweron=air_horn.ogg
esptool.py --chip esp32s3 write_flash 0x20000 app_audio.bin
```
The tool splices the blob, zero-pads the slot, rewrites the end-pointer so
`play_binary: remaining N` reports the new size, and re-fixes the image
checksum + SHA256 (round-trip verified byte-identical). Run it on the
already-patched app so de-cloud patches survive. `--list` prints the manifest.

**Constraint:** replacement must be `<=` the target slot (blobs are packed
contiguously). Encode small: `ffmpeg -i in.wav -ac 1 -ar 48000 -c:a libvorbis -b:a 32k out.ogg`.

**Repurpose the big unused slots:** a de-clouded single-persona unit likely never
triggers id 0 (`character_selection_narrator_voice`, ~315 KB) or id 10
(`setup_device`, ~381 KB). Point a long custom clip at one of those for headroom:
`--set setup_device=my_long_track.ogg`.

## Why "just read them all from SD" is NOT viable by patching

`play_binary` is embedded-only and the bare-name routing is upstream, so making
system sounds load from SD would require the boot/system code to emit full
`/sdcard/...` paths instead of bare names. That means injecting path-building code,
which hits the same wall as the cmd1 experiment (no free DROM/IRAM segment space,
console-task stack overflow). The per-slot splice above is the practical route;
deleting embedded blobs does NOT reclaim usable space (fixed image layout, absolute
pointers) — it only zeroes dead weight.
