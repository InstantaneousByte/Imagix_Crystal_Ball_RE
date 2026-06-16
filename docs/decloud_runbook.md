# De-cloud Runbook — VERIFIED on hardware 2026-06-16

This is the canonical, reproduce-from-scratch record of getting the ORB onto a local
server with **no cloud and no trusted certificate, permanently**. Everything here ran on
the real unit; the firmware milestone (TLS handshake + downchannel open against a
self-signed local server) is **confirmed from serial**. See "What success looks like".

If you just want the short bench checklist, see `bench_decloud_steps.md`. This doc is the
full story: how to derive every binary from a flash dump, how each patch works, and the
exact flash sequence used.

---

## TL;DR (what actually worked)

1. Built a patched app `app_final.bin` from the clean app dump using two tiny patchers:
   - `patch_pin_endpoint.py` — NOP `set_endpoint` so registration can't reset the endpoint.
   - `patch_authmode_none.py` — flip mbedTLS `authmode` REQUIRED→NONE (one byte) so the
     handshake accepts **any** server cert. This is the cert fix; no trusted cert ever needed.
2. Set `ENDPOINT_STR = https://<box-ip>:9000` in a clean NVS image (`nvs_set.py`).
3. Flashed **`app_final.bin` → `0x20000`**, then **`nvs_out.bin` → `0x9000`** with esptool,
   back to back.
4. Ran `server/orb_server.py` (self-signed) on the box. The ORB connected over TLS and
   opened the downchannel — cloud gone.

> The firmware patches are the whole reason a self-signed local server works. The remaining
> boot-to-Ember step is **server-side only** (downchannel framing — see last section); no
> further firmware change is required.

---

## 0. Prereqs

```
pip install esptool h2 --break-system-packages
```
- USB-UART on the ESP32-S3 (`/dev/ttyUSB0`), 3 Mbaud console for watching boot.
- To enter download mode for read/write: ground GPIO0 (TP35) on reset, as during the dump.
- Secure boot is OFF and flash encryption is OFF on this unit (the dump is plaintext), so
  unsigned images flash and boot fine.

---

## 1. Full backup FIRST (source of truth)

Never patch without a known-good full image to fall back to.

```
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 read_flash 0x0 0x1000000 orb_full.bin
```

> 🔒 `orb_full.bin` (and anything carved from its `nvs`) contains a **live JWT** in
> `TOKEN_STR` plus the account email/ids. Treat it as secret — it is `.gitignore`d and must
> never be committed or shared.

---

## 2. Derive the working binaries from the dump

Partition map (from `pt.bin`, see `flash_layout.md`):

| Name | Offset | Size | Page math (4 KB pages) |
|------|--------|------|------------------------|
| `nvs`   | `0x09000` | 40 KB | skip 9, count 10 |
| `ota_0` | `0x20000` | 5 MB  | skip 32 (image proper ≈4.04 MB) |

**App image** (`fw_main.bin`) — the running app lives in `ota_0`. Carve the same span we
analyzed (0x420000 bytes), or read it straight off the chip:

```
# from the full dump:
dd if=orb_full.bin of=fw_main.bin bs=4096 skip=32 count=1056
# --- or read it directly:
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 read_flash 0x20000 0x420000 fw_main.bin

md5sum fw_main.bin    # must be: c281edec65ca43a4108fcae7fb70cbc4
```
The patchers parse the image header to find the real image end (checksum @ `0x40bd2f`,
SHA256 @ `0x40bd30`), so the partial-vs-full-slot size doesn't matter — just match the MD5.

**Clean NVS** (`nvs_clean.bin`) — carve the 40 KB `nvs` partition:

```
dd if=orb_full.bin of=nvs_clean.bin bs=4096 skip=9 count=10
# -> 40960 bytes. Keep this private (holds the JWT).
```

---

## 3. Build the patched app (`app_final.bin`)

Two patchers, run in order. Each verifies its target bytes, applies the change, and re-fixes
the 1-byte XOR checksum + appended SHA256 so the image is valid for any boot/OTA path.

```
python3 tools/patch_pin_endpoint.py  fw_main.bin app_1.bin       # pin the endpoint
python3 tools/patch_authmode_none.py app_1.bin   app_final.bin   # accept any cert (REQUIRED->NONE)

md5sum app_final.bin   # 0361b98696998ef1e83414aa0421892b
```

What each one does (both verified against `olli_esp_patch_build_1.16`):

- **`patch_pin_endpoint.py`** — `RegisterNotifySuccess` (`FUN_42005ec4`) calls `set_endpoint`
  (`FUN_4200aaa8`) on every successful registration, writing the server-supplied endpoint over
  `ENDPOINT_STR`. NOP that call at **VA `0x42005f06` / file `0x235f06`** (`25 ba 04` → `f0 20 00`).
  Token / user_id / `REGISTER_STR=1` are still written, so the boot gate stays satisfied, but
  `ENDPOINT_STR` is now yours and never clobbered.
- **`patch_authmode_none.py`** — `mbedtls_ssl_conf_authmode` (`FUN_421b5aa0`, `conf->authmode`
  at +0x28) has **exactly one caller in the whole image**, in `open_ssl_connection`
  (`FUN_4201f8f0`) at **VA `0x4201fa6a` / file `0x24fa6a`**: `movi.n a11, 0x2`
  (`MBEDTLS_SSL_VERIFY_REQUIRED`). Flip the immediate 2→0 (`0c 2b` → `0c 0b`, one byte) to
  `VERIFY_NONE`. Under NONE the handshake skips cert verification and succeeds for any cert;
  the firmware's post-handshake block is already non-enforcing (logs a verify failure as a
  *warning* and returns success), so the handshake was the only real gate.

> **DO NOT use `patch_cert_trust.py`** — it is flagged DISPROVEN. Neutering the
> `esp_crt_bundle` verify callback left authmode at REQUIRED and corrupted the bundle's flag
> accounting; even a valid cert then failed (`-0x9984`). The authmode flip is the correct fix.

Variant: `patch_authmode_none.py fw_main.bin app_authmode_only.bin`
(`md5 86c5ed4ed700b0afe8a30e134c5d8d4d`) — cert fix only, endpoint writer left intact, if you
prefer to drive the endpoint from your server's registration response instead of pinning it.

Sanity check the built image (optional but cheap):
```
python3 - <<'PY'
d=open('app_final.bin','rb').read()
assert d[0x24fa6a:0x24fa6a+2].hex()=='0c0b', 'authmode patch missing'
assert d[0x235f06:0x235f06+3].hex()=='f02000', 'pin-endpoint patch missing'
print('app_final.bin: both patches present')
PY
```

---

## 4. Point the ORB at your box (NVS)

The firmware builds every request URL as `<ENDPOINT_STR>/connect`. Set it offline on the
clean NVS, then sanity-check that the JWT and registration state survived the edit (a mangled
`TOKEN_STR` throws the unit into a re-registration loop):

```
python3 tools/nvs_set.py nvs_clean.bin nvs_out.bin ENDPOINT_STR https://192.168.8.245:9000
python3 tools/nvs_parse.py nvs_out.bin
#   confirm:  ENDPOINT_STR = https://192.168.8.245:9000
#             TOKEN_STR    = <~900-char JWT, intact>
#             REGISTER_STR = 1 ,  PROFILE_STR = <uuid> ,  USER_ID_INT intact
#             [phy] cal_data / cal_mac / cal_version present (WiFi RF cal)
```
Replace `192.168.8.245` with the box's actual LAN IP. Using the IP directly means no DNS
redirect is needed on this path, and under VERIFY_NONE the IP-vs-cert-hostname mismatch is
ignored too — the self-signed cert needs no SAN gymnastics.

---

## 5. Flash (the exact sequence used)

App to `ota_0`, then NVS to `nvs`, back to back:

```
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash 0x20000 app_final.bin
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash 0x9000  nvs_out.bin
```
(Equivalently in one shot: `write_flash 0x20000 app_final.bin 0x9000 nvs_out.bin`.)

To revert to bone-stock at any time: `write_flash 0x20000 fw_main.bin` and
`write_flash 0x9000 nvs_clean.bin`.

---

## 6. Run the server, boot, and what success looks like

```
cd server && python3 orb_server.py --port 9000     # first run self-signs cert.pem/key.pem
```
Power-cycle the ORB and watch serial. **Verified-good milestone (2026-06-16):**

```
W (...) nghttp_new_session: [871] new nghttp session, uri: [https://192.168.8.245:9000/connect]
W (...) enable_tcp_keepalive: [721] set keepalive for TCP connection
W (...) open_downchannel:    [810] done open downchannel
W (...) delayed_server_ping_task: ... registered=1, downchannel_created=1      <-- flipped from 0,0
```
- **No `-0x12288`** and **no hard `Failed to verify certificate` abort.** A single
  `W ... Failed to verify certificate` *warning* followed by the connection proceeding is
  EXPECTED under VERIFY_NONE — that's the non-enforcing post-handshake log, not a failure.
- Server log shows `=== ORB connected ===` and `GET /connect`.

That is the cloud being gone: the ORB now talks only to your box, over TLS, with a cert it
would never otherwise trust.

---

## 7. Last gate to Ember — downchannel framing (server-side, no reflash)

After the handshake, the boot gate (`data_json_handle: [400] Got new session`) only fires
when the server's reply is **delimiter-framed**. The firmware buffers the downchannel and
`strstr`s for literal markers (`FUN_42020050` → `FUN_4201ff70`), passing `data_json_handle`
only the slice between them:

```
$START_JSON{"connected":<unix_ms>,"session_id":"<DEVICE_ID>"}$END_JSON
```

A bare-JSON reply is silently dropped (no `Got new session`, no fallback log) — this was the
live hang on 2026-06-16. `orb_server.py` now wraps every downchannel message via
`frame_json()`. With that in place the gate fires → logo dismiss → local persona load →
**Ember**. No firmware change needed for this; it is purely the server.

---

## Gotchas / safety

- **Keep `orb_full.bin`** as the golden backup before any write. Re-flash it to recover.
- **Never commit secrets:** `orb_full.bin`, `nvs_clean.bin`, `nvs_out.bin`, `nvs_readback.bin`
  carry the live JWT + account ids. All are `.gitignore`d.
- **Do not apply a cloud OTA during this work** — it could flip eFuses (secure boot / flash
  encryption) and/or revert these patches. Moot once fully de-clouded.
- **Device identity lives in NVS, not the app.** `DEVICE_ID_STR`, `TOKEN_STR`, SIP creds, BLE
  key — bench-flashing a bare ESP32-S3 won't be a *registered* device unless you also clone NVS.
- The bootloader **skips image validation on power-on** here (a raw splice booted), but the
  patchers fix checksum + SHA anyway so the output is valid for any path.

## Verified artifact hashes

| File | MD5 | Notes |
|------|-----|-------|
| `fw_main.bin` | `c281edec65ca43a4108fcae7fb70cbc4` | clean app dump from `ota_0` |
| `app_final.bin` | `0361b98696998ef1e83414aa0421892b` | pin-endpoint + authmode=NONE (flashed) |
| `app_authmode_only.bin` | `86c5ed4ed700b0afe8a30e134c5d8d4d` | authmode=NONE only |
