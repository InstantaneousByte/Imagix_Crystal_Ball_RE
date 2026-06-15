# NVS Config Reference (`my-app` namespace)

Live config read from a real device `nvs` partition (`tools/nvs_parse.py`). The partition is
log-structured; values below are the latest *written* (non-erased) entry per key. Secrets
are redacted — the real dump is not in the repo (see `.gitignore`).

**Namespace:** `my-app` (index 1). RF calibration lives in `phy` (untouched here).

## Endpoint / cloud / registration
| Key | Live value | Notes |
|-----|-----------|-------|
| `ENDPOINT_STR` | `default` | API base; URL = `<endpoint>/connect`. Seen historically as `https://chat-buddyos.iviet.com` (prod) and `http://192.168.8.245:9000` (local override). |
| `REGISTER_STR` | `1` | 1 = registered. |
| `PROFILE_STR` | *(UUID, redacted)* | Per-registration profile UUID (v7). |
| `USER_ID_INT` | `<redacted>` | Account user id (matches JWT `sub`). |
| `TOKEN_STR` | *(JWT, redacted)* | Bearer access token; Hasura backend (`x-hasura-*`, role `member`). |

## Identity / firmware
| Key | Live value |
|-----|-----------|
| `DEVICE_TYPE` | `esp_speaker` |
| `DEVICE_ID_STR` | `AIMWLXXXXXXXXXXX` |
| `DEV_NAME_STR` | `IMX-AIMWLXXXXXXXXXXX` |
| `HW_VER_STR` | `1.0` |
| `SW_VER_STR` | `1.16` |
| `NAME_SW_STR` / `NAME_SW_TMP` | `olli_esp_patch_build_1.16_13_May_2026.bin` |
| `TIMEZONE_STR` | `America/Phoenix` |
| `LANGUAGE_STR` | `en-US` |

OTA history seen in the log: `1.0` → `1.09` (`…build_1.09_24_Dec_2025.bin`) → `1.16`.

## Characters / media
| Key | Live value | Notes |
|-----|-----------|-------|
| `CHARACTER_STR` | `Ember` | Active character. |
| `NAME_BOOT` | `bu1` | Boot animation code. |
| `EMBER_CODE` / `EMBER_VER` | `eb1` / `1.0` | |
| `ELLIE_CODE` / `ELLIE_VER` | `1.03` / `el1` | (code/ver appear swapped in storage) |
| `AN_VER_STR` | `1.03` | Active animation-set version. |
| `eb_*_na` / `eb_*_ver` | `ebm/ebr/ebs/ebsd`, `0.0` | Ember per-state media (mu/rd/st/sd) name+version. |
| `el_*_na` / `el_*_ver` | `elm/elr/els/elsd`, `1.0`/`0.0` | Ellie per-state media. |
| `both_code` / `both_ver` | `both` / `0.0` | |
| `order_id` | `0` | |

## Display / behavior
| Key | Live value | Notes |
|-----|-----------|-------|
| `BRIGHTNESS` | `100` | |
| `TOP_BR_INT` / `BOT_BR_INT` / `FAN_BR_INT` | `100` | Top/bottom/fan LED brightness. |
| `OP_POINT` | `medium` | Motor operating point. |
| `SYS_VOL_INT` | `80` | Volume (0–100). |
| `MUTE_BOOL` | `0` | |
| `NIGHT_MODE` | `0` | |
| `NM_START` / `NM_END` | `0` / `3600` | Night-mode window (seconds?). |
| `NM_LED` | `Breath` | Night-mode LED effect. |
| `SILENT_INT` | `0` | |
| `IN_STANDBY` | `0` | |
| `BLT_STATE_BOOL` | `0` | Bluetooth state. |
| `OTA_FG_INT` / `OTA_NEW_STR` | `0` / `` | OTA flag / pending version. |
| `OTA_RB` | `4:30:00` | OTA rollback/schedule time. |
| `FUTURE4_STR` / `FUTURE5_STR` / `FUTURE5_INT` | `default` / `default` / `1` | Reserved. |

Keys still at the seed value `default` (never set by normal operation): `TOKEN_STR` was
`default` pre-registration; `TIMEZONE_STR`, `DEV_NAME_STR`, `CHARACTER_STR`, `PROFILE_STR`,
`NAME_*`, etc. start as `default` and are filled in during provisioning/registration.
