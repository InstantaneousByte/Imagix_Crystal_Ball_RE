# Repo Audit — claims checked against `fw_main.bin` + decomp (2026-06-15)

Method: every address checked for a real `=== Function ===` start in the decomp; the
segment map parsed from the live image header; schema field names and path strings
grepped as raw strings in the image; the connection model and stub regions read from
the disassembly. Verdicts: **[V]** verified against the binary, **[U]** unverified
(plausible but not checkable from the ESP32 image, or self-flagged unvalidated), **[W]**
wrong or dangerous.

## Summary

The **address-level facts are solid**: the 6-segment map is exact, and all 24 spot-checked
function addresses are real function starts. The **`syncing_tracking.info` JSON schema is
grounded** — all 15 field names are real strings in the image and `cJSON` is present. The
**damage was concentrated in one place**: the claim that `0x42043c40` is a free/disposable
"cmd2 stub." It is live code, and overwriting it caused the boot-audio loss. A secondary
correction: the fan connection is **per-sync**, not persistent (this also corrects a verbal
claim I made mid-session).

---

## docs/esp32s3_firmware_analysis.md

| Claim | Verdict |
|---|---|
| Segment map (6 segs, VAs/lengths/offsets) | **[V]** parsed from header, all exact |
| Entry `0x4037a56c`, IDF 5.5.0, image @ flash `0x20000`, seg3 fully packed | **[V]** |
| All fan/persona/config/lwip function addresses | **[V]** all are real function starts |
| Fan ctx `DAT_42002bb0`, socket at `*(ctx+0x44)`, set to `0xffffffff` after sync (per-sync) | **[V]** confirmed at decomp 139605–139606, 142877 |
| "Patch placement uses the cmd1+cmd2 **stub** region" / "233 bytes → cmd2 stub space at `0x42043c40`" | **[W] DANGEROUS** — `0x42043c40` is live code (`cmd2`=`FUN_42043c40` + ~12 unnamed functions after it). This is the brick. |
| cmd1 patch splice block (offset 136, CALL8) | **[W]** superseded; placement is invalid regardless |
| "DNS-blocking cloud keeps device at boot screen; console+fan TCP available before" | **[U]** plausible, not validated |

## docs/function_registry.md

| Claim | Verdict |
|---|---|
| All main-board addresses (fan TCP, sync orchestration, persona, FS, display, config) | **[V]** every spot-checked address is a real function |
| `fan_sync_binary_file(conn,src,dst,flag)`, returns 1=ok/2=dup/<0=err | **[V]** body + stock caller confirm signature & codes |
| lwip block (`0x420b8588` etc., fopen `0x421a7684`, fclose unconfirmed) | **[V]** matches this+prior session |
| `cmd2/3/4` labeled "**Dev stub**" (`0x42043c40/c2c/c18`) | **[W]** addresses right, but they're **real handlers** (cmd2→`FUN_42005cbc`, cmd3/4→`FUN_4200f1e4(±1)`), not disposable |
| `cmd5` cycles model 1↔2; `fan` dispatcher | **[V]** matches disasm |
| HC32 blade `FUN_0000xxxx` table | **[U]** from the separate HC32 dump, not checkable in the ESP32 image |

## docs/syncing_tracking_format.md  +  examples/*.json

| Claim | Verdict |
|---|---|
| JSON field names (`is_factory_update`, `animation_array`, `origin_name`, `has_sd`, `has_fan`, `root_path`, `is_new_character`, `total_files`, `media_type`, …) | **[V]** all 15 are real strings; `cJSON` present; `RET_SDCARD` present |
| Path format `/sdcard/%s/%s_%02x` | **[V]** `/sdcard/%s` present |
| File is read then **deleted** (transient) | **[V]** consistent — it's why the file isn't on the card at rest |
| "Polls on **each loop iteration**" | **[U]** imprecise — read on a trigger gated by `ctx+0x80`, in the sync/message path; not literally every loop |
| Exact field **routing** (which values pick `RET_SDCARD_EXISTING`/local vs cloud; `origin_name` vs `name`; `has_sd`/`has_fan` effects) | **[U]** self-flagged unvalidated. **This is the one thing to nail down** — see below |

## docs/fan_tcp_protocol.md

| Claim | Verdict |
|---|---|
| TCP `…:4800`; upload chain `fan_sync_binary_file→get_file_size→send_request_upload→send_file_data→tcp_send_all` | **[V]** addresses + `4800` confirmed |
| Return codes 1/2/-1/-2/-5 | **[V]** match body |
| **"connection is per-sync … no persistent connection"** | **[V]** correct (corrects my mid-session misstatement) |
| Frame format `0xAA [len BE][cmd][payload][cksum] 0xA5`, cmd `0x31` upload, resp `0x00/0x82/0x80` | **[U]** checksum self-flagged unconfirmed; frame bytes not re-derived here |
| HC32 cmd bytes `0x01–0x08`, FatFs stack | **[U]** HC32-side, not in this image |

## docs/pov_display_format.md  +  tools/orb_decoder.py / orb_encode.py

| Claim | Verdict |
|---|---|
| 34 B/column (272 bits = 2 hdr + 90×3), 2016 col/rev, 68,544 B/rev, ~25 fps | **[U]** arithmetic self-consistent; derived from `Red/Green/Blue/White.bin` I haven't seen — **verify by uploading one test bin** |
| Bit-interleaved RGB, 1 bpp, Bayer dither for gradients | **[U]** same — testable against a real file |
| HC32 Hall→AOS→TMR4→DMA render path | **[U]** HC32-side |

## docs/hardware_overview.md  +  docs/hc32_blade_analysis.md

| Claim | Verdict |
|---|---|
| Chip IDs (ESP32-S3 / MM32G0001 / ESP32-U4WDH / HC32F460), version strings, board topology | **[U]** your hardware findings; not in the ESP32 image |
| `Config.txt`/`WifiConfig.txt`/`displist.txt` contents | **[U]** blade-side — correctly **absent** from the main-board image; verify from your blade SD/HC32 dump |

## tools/find_padding.py

| Claim | Verdict |
|---|---|
| Scans seg3 for trailing `0xFF`, reports free padding | **[V] correct tool** — and it would have reported **0 bytes free**, "need a different approach." It did **not** produce the `0x42043c40` placement; that was a manual override of its honest result. |

## docs/dumping_guide.md, docs/tools.md, README.md, cmd1_stub/*

| Claim | Verdict |
|---|---|
| Dumping procedures, tool usage | **[U]** procedural |
| Anything describing the cmd1 **inject-and-splice** approach + cmd2 placement | **[W]** describes the abandoned approach; inherits the placement error |

## docs/ADDRESS_CORRECTIONS.md

| Claim | Verdict |
|---|---|
| lwip address corrections, sockaddr layout, literal relocation, `(PC+3)` l32r math, J-redirect, rodata-default caveat | **[V]** all verified — but they fix an approach that's blocked by no-free-space; accurate, about a dead path |

---

## What this means going forward

The injection approach is dead (no free flash; wrong task). But the **data-driven path is well-grounded**:
the `syncing_tracking.info` schema is real, and the firmware reads-then-deletes the file — i.e. you
*drop* the manifest and it gets consumed. The single open question is the **field routing**: which values
make `FUN_420317d4` (`check_persona_sdcard_existing`) / `FUN_42032308` return `RET_SDCARD_EXISTING` so the
firmware syncs **local SD files** to the fan instead of attempting a (dead) cloud download. That's readable
from those two functions — a concrete, grounded next step — or confirmable with a MITM capture of one real
cloud sync. The `Ember/eb1_64/Original/eb_idle_eb1_64.bin.og` backup on your card suggests a file-replace
workflow may already be in reach; reading `character.info` is the next data point.

---

## cm2err forensics (2026-06-19): `cmd2` console = crash; the wedge was NVS

| Claim | Verdict |
|---|---|
| Hidden console `cmd2` → `Guru Meditation InstrFetchProhibited` (`PC=0x00060023`); jumps into an empty handler slot | **[V]** captured on UART |
| `cmd2` performs **no flash write** — pure runtime crash | **[V]** the `ota_0` deltas are explained: the 3-byte change at file `0x235f06` is the **endpoint-pin patch** (`set_endpoint` call NOP, `25 ba 04`→`f0 20 00`, see function_registry `0x42005f06`) applied to the running unit, and the 32-byte image-tail change is the resulting recomputed SHA-256. Factory is unpatched. Not corruption, not a `cmd2` write |
| `spiffs` diffs (8.5 KB) are page-counter churn + secondary wifi config | **[V]** repetitive `+0xfc` 2-byte deltas at every 4 KB page = SPIFFS bookkeeping, not content |
| The persistent wedge (`Force get latest anims`, never loads persona) lived in **`nvs`** | **[V]** SD restore had no effect; a factory NVS reflash fixed it instantly |
| Exact trigger byte | **[U]** factory-vs-running NVS diff blends normal de-cloud/Ember state with the fault; not isolable without a pre-incident dump of this unit. Localized to the persona/anim-update state (`AN_VER_STR`/`CHARACTER_STR`/per-mode `*_ver`, several `0.0`) — see [`nvs_keys.md`](nvs_keys.md) |

**Console `cmd1`–`cmd5` are abandoned for good** — `cmd2` is a guaranteed crash, the rest are
uncharacterized, and the NVS key map ([`nvs_keys.md`](nvs_keys.md)) is the deterministic lever now.
