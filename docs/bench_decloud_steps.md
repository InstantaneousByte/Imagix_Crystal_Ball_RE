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
python3 tools/patch_pin_endpoint.py fw_main.bin app_1.bin       # keep ENDPOINT_STR from being overwritten
python3 tools/patch_cert_trust.py   app_1.bin   app_final.bin   # accept any TLS cert
```
Both print what they changed and fix the checksum/SHA. (Order matters; cert patch second.)

## 2. (Recommended) Prove the cert patch in isolation FIRST
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
- **`-0x12288` / `Failed to verify certificate`** → cert patch not active. Re-flash
  `app_final.bin`; confirm `0x2f8d73` reads `0c0209251df0` in the image you flashed.
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
