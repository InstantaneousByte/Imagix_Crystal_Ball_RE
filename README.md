# Imagix Crystal Ball — Reverse Engineering Notes

> **STATUS (2026-06-17): DE-CLOUDED — DONE, end-to-end.** The orb boots to its character (Ember)
> talking only to a local server on the LAN — **no cloud, no trusted certificate** — and the whole
> thing reproduces from a stock dump with **one command** (`tools/build_orb.py`). **Built and
> verified for main-board sw `[1.16]` only** (see [Version lock](#version-lock)). Full writeup:
> [`docs/decloud_runbook.md`](docs/decloud_runbook.md); what's next in
> [`docs/roadmap.md`](docs/roadmap.md); repo audited against the binary in
> [`docs/AUDIT.md`](docs/AUDIT.md). (Bulk *code injection* — the cmd1 splice — was a dead end [no
> free flash, console-task stack overflow]; the de-cloud uses byte-level *patching*, which works.)
>
> **UPDATE (2026-06-18): CONVERSATION LAYER — voice turn works end-to-end on hardware.** The local
> server now drives a full turn (wake → capture → endpoint → reply → the orb fetches & plays our
> audio → re-listens), so the cloud AI is fully replaceable. Only the AI itself
> (STT→LLM→TTS) remains to drop in; the wire contract is nailed down and reproduced. Details:
> ["WORKING local reply path" in `docs/observed_protocol.md`](docs/observed_protocol.md) and
> [roadmap item 5](docs/roadmap.md). Notably this needed **no mitmproxy capture** — it all came out
> of the decomp.
>
> **UPDATE (2026-06-20): CUSTOM ANIMATION RENDERING — DONE.** A custom image now renders on the
> holographic fan blade at idle, pushed entirely from the local server with **no NAND access, no
> cloud, and no hardware modification**. Seven firmware gates cleared; the full encode→push pipeline
> works end-to-end: `tools/orb_encode.py` converts any PNG/GIF to the POV `.bin` format, and
> `server/orb_server.py --push-anims` delivers it to the idle slot over the local H2 channel.
> Details: [`docs/anim_display_hook.md`](docs/anim_display_hook.md) and
> [`docs/SESSION_SUMMARY.md`](docs/SESSION_SUMMARY.md).

Reverse engineering of the Imagix Crystal Ball, a POV holographic fan display toy running a white-label "BuddyOS" platform by OLLI Technology (iviet.com, Vietnam). The device ships with two AI persona characters (Ember the dragon, Ellie the fairy) rendered as spinning LED animations synchronized to cloud-driven conversation.

This repo documents the hardware architecture, firmware internals, display format, and content pipeline well enough to load custom content onto the device — and to run it **fully cloud-free** off a local server (see [De-clouding](#de-clouding--done-2026-06-16) below).

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

## De-clouding — DONE (verified end-to-end 2026-06-17)

The orb boots to its character (Ember) talking **only to a local server on your LAN** — no
internet, no cloud account, **no trusted certificate** — and it reproduces from a stock flash
dump with a single build command. **This is built and verified for main-board sw `[1.16]`
(`olli_esp_patch_build_1.16`, Apr 2026); see [Version lock](#version-lock) below.**

### Reproduce in one command

Prereqs: `pip install esptool h2 cryptography` (optional `psutil` for cleaner LAN-IP detection).
Take a full-flash backup first ([`docs/dumping_guide.md`](docs/dumping_guide.md)).

```
python3 tools/build_orb.py \
  --fw-in fw_main.bin \
  --nvs-in your_nvs_backup.bin \
  --endpoint https://<box-ip>:9000 \
  --character Ember \
  --ssid "<your 2.4GHz SSID>" --password "<your password>" \
  -o orb_build
```

(`fw_main.bin` = your stock app dump from `ota_0`; `your_nvs_backup.bin` = your device's NVS, which
preserves the per-board RF calibration, JWT, and identity; `<box-ip>` = the LAN address of the
machine running the server.)

`build_orb.py` patches the app (accept-any-cert + endpoint pin), writes `ENDPOINT_STR` /
`CHARACTER_STR` / `REGISTER_STR=1` into a copy of your NVS, builds `/wifi.txt` into a fresh SPIFFS
image, runs a **preflight self-check** (re-reads the built artifacts and confirms both patches,
`REGISTER_STR=1`, and the endpoint), and prints the exact ordered `esptool write_flash` line
(app `0x20000`, NVS `0x9000`, wifi `0xA20000`). Flash it, run the server on the box
(`python3 server/orb_server.py` — auto-detects the LAN IP and generates its own self-signed cert,
no openssl needed), and power-cycle. The orb joins WiFi, dials `https://<box-ip>:9000/connect`,
the server returns the framed session-start, and the orb comes up on your character off the LAN.

### How it works — four pieces, all verified on hardware

1. **Accept any cert (1 byte).** The downchannel is always TLS and verification was enforced
   (`authmode = VERIFY_REQUIRED`), rejecting a self-signed local cert at the handshake.
   `mbedtls_ssl_conf_authmode` (`0x421b5aa0`) has **exactly one caller**; flipping its argument
   REQUIRED(2)→NONE(0) — one byte at `0x4201fa6a` (`0c2b`→`0c0b`, `tools/patch_authmode_none.py`)
   — accepts any cert forever. (The earlier `patch_cert_trust.py` callback-neutering attempt was
   disproven on hardware and is kept only flagged-for-reference.)
2. **Pin the endpoint (3-byte NOP).** Registration (`RegisterNotifySuccess` `0x42005ec4`) rewrote
   `ENDPOINT_STR` on every successful boot; a NOP at `0x42005f06` (`tools/patch_pin_endpoint.py`)
   stops it so `ENDPOINT_STR = https://<box-ip>:9000` stays put.
3. **WiFi + registration (SPIFFS + NVS).** WiFi is **not** in NVS — it's a single plaintext file
   `/wifi.txt` in the `spiffs` partition (`0xA20000`), whose content begins **directly at `SSID=`**
   (records `SSID=…\nPASSWORD=…\nFAVOURITE=…\n\n`; the `FAVOURITE=true` net auto-connects). The
   file body has **no header/prefix** — an early build wrongly prepended a 5-byte `01 00 00 00 7c`
   that was actually a misread SPIFFS page header, which made the parser miss the `SSID=` key and
   fail to connect on *any* network; fixed. Separately, `REGISTER_STR=1` is **mandatory**: a
   from-factory orb (`REGISTER_STR=0`) sits in BLE Setup and never dials `/connect`. `build_orb.py`
   handles both. (See [`docs/wifi_provisioning.md`](docs/wifi_provisioning.md).)
4. **Speak the protocol.** The downchannel is **delimiter-framed** — every message must be wrapped
   `$START_JSON{…}$END_JSON` (the firmware `strstr`s for the markers; bare JSON is dropped).
   `server/orb_server.py` answers `/connect` with the framed session-start that flips the boot gate
   (`data_json_handle: Got new session`) → logo dismiss → your character.

### <a name="version-lock"></a>Version lock — sw `[1.16]` only

The two firmware byte-patches use **fixed offsets specific to this exact build**
(`olli_esp_patch_build_1.16`). They are **fail-safe**: each patcher verifies the expected bytes at
its offset and aborts on mismatch, so running them against a different firmware version won't brick
anything — it just refuses. **A different sw version needs the two patch offsets re-derived** (find
the sole `mbedtls_ssl_conf_authmode` caller and the `RegisterNotifySuccess` `set_endpoint` call
site). Everything else carries across versions unchanged: the NVS keys (`ENDPOINT_STR`,
`CHARACTER_STR`, `REGISTER_STR`), the `/wifi.txt` format, the `$START_JSON…$END_JSON` framing, the
local server, and the whole method. OTA is the only thing that could move the offsets — and the
endpoint pin + local server prevent OTA, so a working de-cloud stays put.

Deep dives: reproduce-from-scratch [`docs/decloud_runbook.md`](docs/decloud_runbook.md) · endpoint
internals [`docs/decloud_endpoint.md`](docs/decloud_endpoint.md) · captured protocol + framing
[`docs/observed_protocol.md`](docs/observed_protocol.md) · WiFi/registration
[`docs/wifi_provisioning.md`](docs/wifi_provisioning.md) · what's next
[`docs/roadmap.md`](docs/roadmap.md).

---

## Firmware addresses (main board, `olli_esp_patch_build_1.16`, Apr 2026)

| Function | Address |
|----------|---------|
| `fan_sync_binary_file(conn, src, dst, flag)` | `0x4202980c` |
| `start_sync_data_to_fan_via_tcp(ctx)` | `0x4202d7f0` |
| `write_syncing_tracking_info(ctx, int)` | `0x4202ee18` |
| `configCheckFistTime` (config seed-if-absent — NOT endpoint writer) | `0x42009f28` |
| `RegisterNotifySuccess` (real endpoint writer — NOP'd to pin) | `0x42005ec4` |
| `set_endpoint` call site (NOP target `25ba04`→`f02000`) | `0x42005f06` |
| `mbedtls_ssl_conf_authmode` (sole authmode setter; `conf+0x28`) | `0x421b5aa0` |
| authmode call site (`movi.n a11,2`→`0`, `0c2b`→`0c0b`) | `0x4201fa6a` |
| `open_ssl_connection` (TLS setup; post-handshake is non-enforcing) | `0x4201f8f0` |
| `data_json_handle` (downchannel parser; `$START_JSON`/`$END_JSON`) | `0x42020a1c` |
| `olli_data_check_target_device` (sessionId/deviceIDs gate; fail → `[426] is_approved`) | `0x4201d280` |
| `olli_background_directive_handle` (downchannel directive dispatch; `[465]`, `[883]`/`[888]`) | `0x4202046c` |
| `on_recv_data_chunk` (nghttp2 data cb; `finish`=6B on recognize stream → `[624] Text Finish`) | `0x420211c8` |
| `olli_user_directive_handle` (in-band directive dispatch; `[432]`, "Has Url") | `0x4202007c` |
| `speech_recognizer_start_capture` (sets the "interacting" guard, struct byte `+0x5d`) | (sets `+0x5d=1`) |
| `esp_x509_crt_bundle` attach (audio-fetch TLS verify — the `-0x2700` source) | `0x420c8f68` |
| fan sync context global | `DAT_42002bb0` |

Full function registry: [`docs/function_registry.md`](docs/function_registry.md)

---

## Tools

| Tool | Purpose |
|------|---------|
| `build_orb.py` | **One-command de-cloud build** — patches app, writes NVS (endpoint / character / `REGISTER_STR=1`), builds `/wifi.txt` SPIFFS, preflight-checks, prints the flash line |
| `patch_authmode_none.py` | Flip mbedTLS `authmode` REQUIRED→NONE (1 byte) — accept any TLS cert |
| `patch_pin_endpoint.py` | NOP the registration endpoint-writer so `ENDPOINT_STR` stays put |
| `nvs_set.py` | Set an NVS string key (e.g. `ENDPOINT_STR`, `REGISTER_STR`) in a dump, preserving other keys + RF calibration |
| `nvs_parse.py` | Parse an NVS dump (live config + key history; redacts secrets) |
| `make_wifi_spiffs.py` | Build a `/wifi.txt` SPIFFS image to set WiFi with no app/cloud |
| `patch_system_audio.py` | Splice replacement system/UI sounds into the firmware DROM |
| `orb_decoder.py` / `orb_encode.py` | Decode/encode `.bin` POV animations |

Local server: `server/orb_server.py` (HTTP/2 + TLS, answers `/connect` with the framed
session-start; pure-Python self-signed cert).

*Abandoned code-injection research (`find_padding.py`, `splice_patch.py`, `extract_cmd1.py`,
`cmd1_stub/`) is retained for reference only — injection hit no free flash + a console-task stack
overflow. The de-cloud uses byte-patching instead.*

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
- [x] Partition table read (`pt.bin`, pure OTA, 16 MB) — map + free-flash analysis in [docs/flash_layout.md](docs/flash_layout.md)
- [ ] `syncing_tracking.info` field routing validated (local vs cloud) — next step
- [x] Endpoint mechanism solved: `ENDPOINT_STR` NVS string. NVS dump proved registration (`RegisterNotifySuccess` `0x42005ec4`) overwrites it; pin via 3-byte NOP at `0x42005f06` (`tools/patch_pin_endpoint.py`) — see [docs/decloud_endpoint.md](docs/decloud_endpoint.md)
- [x] `<endpoint>/connect` HTTP/2 protocol documented — incl. the `$START_JSON…$END_JSON` downchannel framing ([docs/observed_protocol.md](docs/observed_protocol.md))
- [x] **Fully de-clouded, end-to-end (2026-06-17)** — 1-byte `authmode=NONE` + endpoint pin + WiFi `/wifi.txt` + `REGISTER_STR=1` + framed local `/connect`; boots to **Ember off the LAN**, no cloud, no trusted cert. Whole build is one command (`tools/build_orb.py`) ([docs/decloud_runbook.md](docs/decloud_runbook.md))
- [x] **WiFi provisioning without the app** — creds in `/wifi.txt` in SPIFFS (body begins at `SSID=`, no prefix); `REGISTER_STR=1` required or the orb sits in BLE Setup; both handled by `build_orb.py` ([docs/wifi_provisioning.md](docs/wifi_provisioning.md))
- [x] **Conversation turn works end-to-end on hardware (2026-06-18)** — wake → capture → server VAD endpoint → `finish` token (clears the "interacting" guard) → ExpectSpeech on the held-open downchannel (auto-targeted at the real device id) → orb fetches & plays our audio over **plain HTTP** → re-listens. Decomp-derived, no mitmproxy needed ([docs/observed_protocol.md](docs/observed_protocol.md), [roadmap item 5](docs/roadmap.md))
- [ ] Swap the placeholder beep for the real STT → local LLM → TTS → ogg/opus pipeline (only the AI remains; transport is done)
- [x] **Custom animation rendering on the blade (2026-06-20)** — custom PNG/GIF encodes to `.bin` via `tools/orb_encode.py`, pushed via `server/orb_server.py --push-anims` with no NAND access, no cloud, no hardware mod. Seven firmware gates cleared; encoder bugs fixed (radial direction + CW/CCW sweep). The idle slot (`eb_idle_02`, context id 43) now shows our frame. Full pipeline in [`docs/anim_display_hook.md`](docs/anim_display_hook.md)

---

## Notes

- TLS uses the Mozilla CA bundle with no pinning — but verification was **enforced** (`VERIFY_REQUIRED`), so a self-signed/MITM cert was rejected at the handshake until we patched `authmode` to `NONE` (1 byte at `0x4201fa6a`). Any cert is now accepted.
- **Two separate TLS clients.** The `authmode=NONE` patch covers only the **control channel** (nghttp2 `/connect` + event streams). The **audio fetch** is a different client — ESP-ADF `http_stream`/`esp_http_client` — which still attaches the Mozilla bundle (`esp_x509_crt_bundle`, `0x420c8f68`) and verifies, so an `https://` TTS URL fails with `mbedtls -0x2700`. The local server serves `/api/audio` over **plain HTTP** to sidestep it (or patch this second cert site the same way `authmode` was patched). See [`docs/observed_protocol.md`](docs/observed_protocol.md).
- Fan TCP protocol is unauthenticated plain TCP.
- SWD on HC32 was unprotected at time of dumping.
- Console accessible at 3 Mbaud on the main board's UART port.
- `displist.txt` on the blade SD-NAND is a plain newline-separated filename list.
- The `_64` and `_67` file suffixes appear to be display profile variants selected by `baud:N` in `Config.txt`.

---

## Credits & third-party components

- **[Silero VAD](https://github.com/snakers4/silero-vad)** — voice-activity detector used by
  `server/orb_server.py` to endpoint the user's speech (the orb has no end-of-speech VAD of its own,
  so the server owns it). © Silero Team, licensed under the **MIT License**. The model
  (`silero_vad.onnx`) is **not vendored** in this repo — it is fetched separately at setup and is
  gitignored; run it via `onnxruntime`. We use the model as-is; all credit for it goes to the Silero
  Team. See the upstream repo for its license and model card.

Other runtime dependencies (`h2`, `onnxruntime`, `numpy`, `cryptography`, `esptool`, and `ffmpeg`
for opus encoding) are used under their respective open-source licenses.

---

## Methodology note

A significant portion of this analysis was carried out with the assistance of a large language model (Claude, by Anthropic), used as an interactive analysis partner throughout. The LLM assisted with decompilation interpretation, binary format reverse engineering, call graph analysis, Python tooling, and firmware patch design. All findings were verified against the actual binary data. The hardware work — soldering, dumping, console access, physical disassembly — was done by the human researcher.

See [`docs/tools.md`](docs/tools.md) for more detail.
