# Imagix Crystal Ball — Reverse Engineering Notes

> **STATUS / CORRECTIONS (2026-06-15):** A full audit of this repo against the binary is in
> [`docs/AUDIT.md`](docs/AUDIT.md). Addresses, the segment map, and the `syncing_tracking.info`
> schema are **verified**; the **firmware-injection (cmd1 splice) approach is abandoned** — there
> is no free space in the code segment and the handler overflows the console task. The data-driven
> `syncing_tracking.info` path is the way forward. Treat the "Firmware patching" section as historical.

Reverse engineering of the Imagix Crystal Ball, a POV holographic fan display toy running a white-label "BuddyOS" platform by OLLI Technology (iviet.com, Vietnam). The device ships with two AI persona characters (Ember the dragon, Ellie the fairy) rendered as spinning LED animations synchronized to cloud-driven conversation.

This repo documents the hardware architecture, firmware internals, display format, and content pipeline well enough to load custom animation content onto the device.

---

## Animation sample

Ember's power-on animation, decoded from the blade SD-NAND using `tools/orb_decoder.py`:

![Ember power-on fireball animation](assets/eb_pwon.gif)

*120 frames · 4.8 seconds · decoded from `eb_pwon_eb1_64.bin` · 1-bit RGB POV format*

---

## Hardware

Four separate processors communicate over UART, SPI, and TCP:

| Chip | Role | Interface |
|------|------|-----------|
| ESP32-S3 (main board) | Cloud, audio, console, persona logic | WiFi, UART to base |
| MM32G0001A6T (motor board) | Motor PWM, UART relay | USART1/2, 3-pin JST to main board |
| ESP32-U4WDH (blade) | WiFi bridge, content relay | SPI to HC32 |
| HC32F460JEUA (blade) | POV render, eMMC/SD-NAND owner | SDIOC1, Hall, DMA |

The main board runs IDF 5.5.0 (project `olli_esp_sdk`, built Apr 2026). The blade HC32 owns a FORESEE FLSD032G SD-NAND (4GB, standard microSD interface) containing all animation `.bin` files, plus `Config.txt`, `WifiConfig.txt`, and `displist.txt`.

Tools and software used: [`docs/tools.md`](docs/tools.md)

Full hardware notes: [`docs/hardware_overview.md`](docs/hardware_overview.md)

Dumping guide (how to get the firmware yourself): [`docs/dumping_guide.md`](docs/dumping_guide.md)

---

## Display format

Each animation file is a raw binary stream of 34-byte columns (272 bits each):

```
[2 header bits (00)][270 data bits]
```

270 data bits = 90 LEDs × 3 channels (RGB), bit-interleaved: R0 G0 B0 R1 G1 B1 ...

- ~2016 columns per revolution
- ~1500 RPM (~25 rev/sec)
- 1 bit per channel (8 colors); dithering required for gradients
- File size = 34 × 2016 × N revolutions

The HC32 streams columns to the LEDs via DMA, clocked by TMR4, phase-locked to the Hall sensor via the AOS hardware event router. No software involvement in the render loop.

Full format spec: [`docs/pov_display_format.md`](docs/pov_display_format.md)

Decoder/encoder: [`tools/orb_decoder.py`](tools/orb_decoder.py), [`tools/orb_encode.py`](tools/orb_encode.py)

---

## Content pipeline

Animation files live permanently on the blade's SD-NAND. The main board's `/sdcard` is a transit area — files are downloaded from the cloud, pushed to the fan over TCP, then deleted locally.

**Fan TCP protocol** (`172.10.10.1:4800`, plain TCP):
```
0xAA [len BE 4B] [cmd 1B] [payload] [checksum] 0xA5
```
The upload command (`0x31`) sends a file to the blade by name. The HC32 receives it via its `cmd 0x08` handler and writes it to the SD-NAND using FatFs.

**Triggering a sync from the SD card:**

Drop a JSON file at `/sdcard/syncing_tracking.info`. The firmware reads it on a sync trigger (gated by `ctx+0x80`), pushes the described files to the fan, then deletes it (so it does not persist at rest — expected). The JSON **field names are verified** real strings; the **exact field routing** that selects local-SD vs cloud download is **not yet validated** — see [`docs/AUDIT.md`](docs/AUDIT.md).

**Consumer gate verified (2026-06-15):** `start_sync_data_to_fan_via_tcp` (`0x4202d7f0`) pushes a file iff `has_sd != 0 && has_fan == 0`, then sets `has_fan = 1`. To re-push a replaced animation, make its `has_fan` read 0 (`syncing_fan_done: false` at the `character.info` level).

Ground-truth `character.info` schema (from 3 real SD files): [`docs/character_info_format.md`](docs/character_info_format.md). See also [`docs/syncing_tracking_format.md`](docs/syncing_tracking_format.md) and [`examples/`](examples/) (`character_info_eb1_64_REAL.json` is verified; the old `character_info_template.json` is superseded).

## Surviving cloud shutdown (de-clouding)

The cloud API base is an NVS string, **`ENDPOINT_STR`** (default `"default"`); URLs are `<ENDPOINT_STR>/connect`. `configCheckFistTime` is **seed-if-absent**, so a custom `ENDPOINT_STR` **persists across reboot with no firmware patch** — point the orb at a self-hosted server and reimplement the `/connect` channel. Full analysis: [`docs/decloud_endpoint.md`](docs/decloud_endpoint.md).

---

## Firmware patching (ABANDONED — see [`docs/AUDIT.md`](docs/AUDIT.md))

> The cmd1 inject-and-splice approach does **not** work on this firmware and **broke boot audio** when flashed. Two hard walls: (1) the code segment has **zero free space** (no `0xFF`/`0x00` run ≥64 B), so the "cmd2 stub region" at `0x42043c40` is **live code**, not padding; (2) `fan_sync_binary_file` from the console task **overflows its stack** — the stock firmware runs it from a dedicated sync thread. The code in [`cmd1_stub/`](cmd1_stub/) is correct (addresses, sockaddr, relocation verified) but has nowhere safe to live. Kept for reference.

---

## Firmware addresses (main board, `olli_esp_patch_build_1.16`, Apr 2026)

| Function | Address |
|----------|---------|
| `fan_sync_binary_file(conn, src, dst, flag)` | `0x4202980c` |
| `start_sync_data_to_fan_via_tcp(ctx)` | `0x4202d7f0` |
| `write_syncing_tracking_info(ctx, int)` | `0x4202ee18` |
| `configCheckFistTime` (config seed-if-absent — NOT endpoint writer) | `0x42009f28` |
| `cmd1` stub entry | `0x42043808` |
| `cmd2` handler (NOT free space) | `0x42043c40` |
| fan sync context global | `DAT_42002bb0` |

Full function registry: [`docs/function_registry.md`](docs/function_registry.md)

---

## Tools

| Tool | Purpose |
|------|---------|
| `orb_decoder.py` | Decode `.bin` animation to polar GIF |
| `orb_encode.py` | Encode image/GIF to `.bin` animation |
| `find_padding.py` | Find free space in firmware for patch placement |
| `splice_patch.py` | Splice compiled patch bytes into firmware image |
| `extract_cmd1.py` | Extract `cmd1_handler` + `fan_connect` from IDF ELF |
| `cmd1_stub/` | IDF 5.5 project that compiles the cmd1 handler |

---

## Status

- [x] All four chips dumped
- [x] SD-NAND backed up (full image)
- [x] Main board firmware fully analyzed (Ghidra, 9946 functions)
- [x] HC32 blade firmware analyzed (309 functions)
- [x] POV display format reverse engineered and verified
- [x] Fan TCP upload protocol decoded (sender + receiver)
- [x] `syncing_tracking.info` + `character.info` schemas verified from 3 real SD files; fan-push consumer gate verified (`has_sd && !has_fan`)
- [x] Repo audited against the binary (2026-06-15) — see [docs/AUDIT.md](docs/AUDIT.md)
- [x] `cmd1` patch code corrected & verified, but **injection approach abandoned** (no free flash; console-task stack overflow)
- [x] Partition table read (`pt.bin`, pure OTA, 16 MB)
- [ ] `syncing_tracking.info` field routing validated (local vs cloud) — next step
- [x] Endpoint mechanism solved: `ENDPOINT_STR` NVS string, seed-if-absent, no patch needed to repoint — see [docs/decloud_endpoint.md](docs/decloud_endpoint.md)
- [ ] `<endpoint>/connect` HTTP/2 protocol + NVS namespace documented (pending capture)
- [ ] Custom animation content loaded and displayed

---

## Notes

- No TLS pinning on the main board. Uses Mozilla CA bundle.
- Fan TCP protocol is unauthenticated plain TCP.
- SWD on HC32 was unprotected at time of dumping.
- Console accessible at 3 Mbaud on the main board's UART port.
- `displist.txt` on the blade SD-NAND is a plain newline-separated filename list.
- The `_64` and `_67` file suffixes appear to be display profile variants selected by `baud:N` in `Config.txt`.

---

## Methodology note

A significant portion of this analysis was carried out with the assistance of a large language model (Claude, by Anthropic), used as an interactive analysis partner throughout. The LLM assisted with decompilation interpretation, binary format reverse engineering, call graph analysis, Python tooling, and firmware patch design. All findings were verified against the actual binary data. The hardware work — soldering, dumping, console access, physical disassembly — was done by the human researcher.

See [`docs/tools.md`](docs/tools.md) for more detail.
