# NVS Key Map (`my-app` namespace)

The `nvs` partition (`0x009000`, 40 KB — see [`flash_layout.md`](flash_layout.md)) holds the
device's persistent config in NVS namespace **`my-app`** (plus the standard `phy` calibration
namespace). Dumped and parsed from a full 16 MB flash image; **status [V] verified** against the
known-good factory NVS (`nvs_factory.bin`).

> Identity/credential values are **redacted** here (chat-only): real `DEVICE_ID`, `PROFILE`,
> `TOKEN`, `USER_ID`. Device id shown as `AIMWLXXXXXXXXXXX`.

## How the partition is laid out
Standard ESP-IDF NVS: 4 KB pages, each = 32 B page header + 32 B entry-state bitmap + 126 × 32 B
entries. An entry is `ns(1) type(1) span(1) chunk(1) crc32(4) key(16) data(8)`; variable-length
strings/blobs store `size` in the data field and spill into the next `span-1` entries. All values
below are stored as **strings** even when they're logically ints/bools (the firmware keeps a
`_INT`/`_BOOL`/`_STR` suffix convention but the on-disk type is `str`).

## Keys

### Identity / registration (redacted)
| Key | Value | Notes |
|-----|-------|-------|
| `DEVICE_ID_STR` | `AIMWLXXXXXXXXXXX` | the real id; appears in every event/meta header |
| `DEV_NAME_STR` | `IMX-AIMWLXXXXXXXXXXX` | BLE/advertised name |
| `PROFILE_STR` | `<profile-uuid>` | user profile uuid |
| `TOKEN_STR` | `<redacted>` | auth token |
| `USER_ID_INT` | `<redacted>` | account user id |
| `REGISTER_STR` | `1` | registered flag (**factory `0`**) |
| `order_id` | `0` | |

### Server endpoint / cloud
| Key | Value | Notes |
|-----|-------|-------|
| `ENDPOINT_STR` | `https://10.0.0.176:9000` | **the de-cloud lever** — factory: `https://chat-buddyos-us.iviet.com` |

### Persona + animation versioning — **the update-gate levers**
The device's `Force get latest anims` / `contextBootUpSendUpdateAnims` boot path compares these
NVS versions against what the server offers (`version: latest`). Offer a higher version → it
fetches. These are the keys to drive (or to leave alone).

| Key | Value | Notes |
|-----|-------|-------|
| `CHARACTER_STR` | `Ember` | active persona (**factory `default`**) |
| `AN_VER_STR` | `1.0` | active animation set version (**factory `1.03`** — tracks the active character) |
| `EMBER_CODE` | `eb1` | Ember fw code |
| `EMBER_VER` | `1.0` | Ember (system) anim version |
| `ELLIE_VER` | `el1` | Ellie fw code (note: `_VER`/`_CODE` are **swapped** vs Ember's convention) |
| `ELLIE_CODE` | `1.03` | Ellie (system) anim version |
| `eb_mu_na` / `eb_mu_ver` | `ebm` / `0.0` | Ember music mode (0.0 = not installed) |
| `eb_rd_na` / `eb_rd_ver` | `ebr` / `0.0` | Ember riddle mode |
| `eb_st_na` / `eb_st_ver` | `ebs` / `0.0` | Ember story mode |
| `eb_sd_na` / `eb_sd_ver` | `ebsd` / `0.0` | Ember "sd" mode |
| `el_mu_na` / `el_mu_ver` | `elm` / `1.0` | Ellie music mode |
| `el_rd_na` / `el_rd_ver` | `elr` / `1.0` | Ellie riddle mode |
| `el_st_na` / `el_st_ver` | `els` / `1.0` | Ellie story mode |
| `el_sd_na` / `el_sd_ver` | `elsd` / `0.0` | Ellie "sd" mode |
| `both_code` / `both_ver` | `both` / `0.0` | shared/both-character content |
| `NAME_BOOT` | `bu1` | boot-animation set code |

### OTA / firmware
| Key | Value | Notes |
|-----|-------|-------|
| `SW_VER_STR` | `1.16` | running sw version |
| `NAME_SW_STR` / `NAME_SW_TMP` | `olli_esp_patch_build_1.16_13_May_2026.bin` | running/pending image name |
| `OTA_NEW_STR` | `` (empty) | pending OTA image name |
| `OTA_FG_INT` | `0` | OTA-in-progress flag |
| `OTA_RB` | `4:30:00` | scheduled reboot time for OTA |
| `HW_VER_STR` | `1.0` | hardware revision |

### Boot modes / misc state
| Key | Value | Notes |
|-----|-------|-------|
| `IN_STANDBY` | `0` | standby flag (boot log: `Standby value`) |
| `SILENT_INT` | `0` | silent boot (set by crash reset-reason) |
| `NIGHT_MODE` / `NM_START` / `NM_END` / `NM_LED` | `0` / `0` / `3600` / `Breath` | night-mode window + LED effect |
| `MUTE_BOOL` / `BLT_STATE_BOOL` | `0` / `0` | mute, bluetooth |
| `LANGUAGE_STR` | `en-US` | |
| `TIMEZONE_STR` | `America/Phoenix` | |
| `SYS_VOL_INT` | `80` | volume |
| `TOP_BR_INT` / `BOT_BR_INT` / `FAN_BR_INT` / `BRIGHTNESS` | `100` each | LED + fan brightness |
| `OP_POINT` | `medium` | wakeword sensitivity |
| `DEVICE_TYPE` | `esp_speaker` | |
| `FUTURE4_STR` / `FUTURE5_STR` / `FUTURE5_INT` | `default` / `default` / `1` | reserved |

## Forensics: the `cm2err` corruption (2026-06-19)
Running the hidden console `cmd2` crashed the device (`Guru Meditation Error:
InstrFetchProhibited`, `PC=0x00060023` — it jumped into an empty handler slot and executed
garbage). The device then **boot-looped into `Force get latest anims` and never loaded the
persona** (Ember), sitting on the boot logo while it begged the server for an animation update
the de-cloud server didn't answer.

Diffing the corrupted full dump against known-good:
- **App image (`ota_0`) intact.** The only `ota_0` deltas are the appended **SHA-256** at the
  image tail (`~0x42bd50`) plus two build-metadata bytes — i.e. the reference image is simply a
  *different build*, not corruption. `cmd2` wrote nothing to flash; it was a pure runtime crash.
- **`spiffs` deltas** are filesystem page-counter churn + the secondary wifi config; not relevant.
- **The wedge was entirely in `nvs`** — which is why restoring the SD card did nothing and an NVS
  reflash fixed it instantly. The factory-vs-running diff blends normal de-cloud/Ember state with
  the fault, so the exact trigger byte isn't isolable without a pre-incident dump of this unit, but
  it lived in the persona/anim-update state above (`AN_VER_STR` / `CHARACTER_STR` / the per-mode
  `*_ver` keys, several of which read `0.0`). A whole-partition factory NVS reflash cleared it.

**Console `cmd1`–`cmd5` are off-limits going forward** — `cmd2` is a guaranteed crash and the rest
are uncharacterized. The NVS keys above are the safe, deterministic lever instead.

## Why this matters for custom content
The `Force get latest anims` path is the **no-SD delivery hook**: the device *asks* the server for
newer animations on boot. Answer that request (a `FileManager` directive, `media_type:"video"`,
files served over plain HTTP from the Mac, `version` above the NVS value here) and it downloads +
syncs to the fan on its own — no SD handling, no soldering. See
[`fan_tcp_protocol.md`](fan_tcp_protocol.md) and the persona-update trace in the project notes.
