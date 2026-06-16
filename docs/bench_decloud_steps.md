# Bench Directions — De-cloud the ORB (boot-gate MVP)

Goal: ORB boots to Ember with no cloud, talking only to your local server.
Assumes the experiment unit, serial console on `/dev/ttyUSB0`, server box at
`192.168.8.245`. Replace paths/ports/IPs as needed.

Do these in order. After each flash, you can fall back to `orb_full.bin` if anything
goes sideways — you have a full backup and a second unit.

---

## 0. Prereqs (once)
```
pip install esptool h2 --break-system-packages
```
Have on hand: `fw_main.bin` (app dump), `nvs_clean.bin` (clean NVS carved from the
full dump: `dd if=orb_full.bin of=nvs_clean.bin bs=4096 skip=9 count=10`), and the
repo's `tools/` + `server/`.

## 1. Build the patched firmware
```
python3 tools/patch_pin_endpoint.py  fw_main.bin app_1.bin       # keep ENDPOINT_STR from being overwritten
python3 tools/patch_authmode_none.py app_1.bin   app_final.bin  # authmode REQUIRED->NONE: accept ANY cert
```
Both print what they changed and fix the checksum/SHA. (Order matters; authmode second.)

> **NOTE (2026-06-16):** `patch_cert_trust.py` is DISPROVEN on hardware — neutering the
> `esp_crt_bundle` callback left authmode at REQUIRED, so the handshake still enforced
> verification, and the wholesale callback overwrite corrupted the bundle's flag accounting
> (valid certs then failed with mbedtls `-0x9984`). Do NOT use it. The correct, verified fix
> is `patch_authmode_none.py`: it flips the single `mbedtls_ssl_conf_authmode` call site
> (VA `0x4201fa6a`, the ONLY authmode-set in the image) from VERIFY_REQUIRED (2) to
> VERIFY_NONE (0) — one byte, `0c2b`→`0c0b`. Under NONE the handshake skips verification and
> succeeds for any cert; the firmware's post-handshake block already returns success on a
> verify failure (logs it as a *warning* only), so the handshake was the only real gate.

## 2. (Recommended) Prove the cert fix in isolation FIRST
Before trusting the whole pipeline, confirm the patch alone kills the `-0x12288`:
- Flash just the patched app:
  ```
  esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash 0x20000 app_final.bin
  ```
- Temporarily run a throwaway TLS server on the box and point the ORB at it
  (`openssl s_server -accept 9000 -cert cert.pem -key key.pem -www`), or just run
  `server/orb_server.py` (step 4) and skip straight there.
- Boot, watch serial. **Success = the handshake completes, NO
  `esp-x509-crt-bundle: Failed to verify certificate`, no `-0x12288`.** If you still
  see it, stop — the patch didn't take (wrong slot/old image cached).

## 3. Point the ORB at your box
```
python3 tools/nvs_set.py nvs_clean.bin nvs_out.bin ENDPOINT_STR https://192.168.8.245:9000
python3 tools/nvs_parse.py nvs_out.bin        # sanity: ENDPOINT_STR is the new value, REGISTER_STR=1, token intact
esptool.py --chip esp32s3 --port /dev/ttyUSB0 --baud 460800 write_flash 0x9000 nvs_out.bin
```

## 4. Run the local server (on 192.168.8.245)
```
cd server
python3 orb_server.py --port 9000
```
First run generates `cert.pem`/`key.pem`. Leave it running; it logs every stream the
ORB opens.

## 5. Boot the ORB
Power-cycle. Watch BOTH the ORB serial and the server log. Expected:
- ORB serial: `nghttp_new_session ... https://192.168.8.245:9000/connect`, handshake
  OK, then `Got new session: {"connected":...}` → persona load → **Ember**.
- Server log: `POST /connect` → `-> session-start ...`.

If it reaches Ember: the cloud is gone. 🎉

---

## Troubleshooting (the serial log is the source of truth)
- **`-0x12288` / `Failed to verify certificate` (as a hard abort)** → authmode patch not active.
  Re-flash `app_final.bin`; confirm `0x24fa6a` reads `0c0b` in the image you flashed. NOTE: a
  *single* `W ... Failed to verify certificate` warning followed by the connection proceeding is
  EXPECTED under VERIFY_NONE — that's the firmware's non-enforcing post-handshake log, not a failure.
- **`Could not parse URI default/connect`** → `ENDPOINT_STR` is still `default`; the NVS
  write didn't take. Re-flash `nvs_out.bin` to `0x9000`.
- **Connects but hangs on the logo** → it reached `/connect` but the gate needs more than
  the bare session-start. Look at what the server logged the ORB send, and what the ORB
  serial prints after `open downchannel`. Paste both and we extend the server (likely a
  StartSession/persona directive on the downchannel).
- **No connection at all** → wrong box IP, server not running, or firewall on `.245`.
  Confirm `ENDPOINT_STR` host == the box's actual LAN IP.

## What's next after the gate
The server is a skeleton — it only opens the gate. The conversational loop
(`SpeechRecognizer/ExpectSpeech` + `/api/audio`, backed by STT → local LLM → TTS) is the
next build. And the custom character image swap is independent: drop the encoded `.bin` over
`eb_idle_eb1_64.bin` once you're past the gate.
