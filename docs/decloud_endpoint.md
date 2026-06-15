# De-clouding: API Endpoint Mechanism & Survival After Server Shutdown

**Goal:** keep the orb usable (and point it at a self-hosted LLM backend) after Imagix's
cloud goes dark. **Headline result: no firmware rewrite is needed.** The endpoint is
configuration *data* in NVS (`ENDPOINT_STR`), so you set it to your own server — but cloud
registration actively overwrites it, so pinning it takes either one 3-byte in-place NOP or
having your server return the right value. Details below.

---

## How the endpoint actually works — [V] verified

- The cloud API base is an **NVS string** under the key **`ENDPOINT_STR`**.
- Every request URL is built as **`<ENDPOINT_STR>/connect`** (the HTTP/2 downchannel; see
  strings `%s/connect`, `olli-session-id`, `olli_h2_task`, `olli_h2_restart_waiting_register`).
- Its factory **default value is the literal string `"default"`** (so a virgin device's
  URL is the nonsense `default/connect`). The cloud **registration** handshake is what is
  supposed to overwrite `ENDPOINT_STR` with the real server URL.
- **Brick mechanism:** dead cloud → registration never completes → `ENDPOINT_STR` stays
  `"default"` → URL invalid → boot stalls waiting on `olli_h2_restart_waiting_register`.

### Config plumbing (functions)

| Address | Role | Notes |
|---------|------|-------|
| `0x4200ac64` | config **schema registrar** | Builds in-RAM `{key, default, type}` descriptor table (`DAT_420006ac`). `ENDPOINT_STR` is type 6; default ptr = `PTR_s_default`. Other keys: `DEVICE_TYPE/ID`, `TOKEN_STR`, `TIMEZONE_STR`, `SYS_VOL_INT`, `MUTE_BOOL`, `BLT_STATE_BOOL`, `LANGUAGE_STR`, `AN_VER_STR`, `DEV_NAME_STR`, `REGISTER_STR`, `CHARACTER_STR`, `OTA_FG_INT`, `OTA_NEW_STR`. |
| `0x42009f28` | `configCheckFistTime` — config **seeder** | **Seed-if-absent**, see below. NOT a cloud-endpoint writer. |
| `0x42009c7c` | `getString` (NVS read) | Reads key into a `std::string`; on miss returns the caller's default. Logs `nvs_get_str_fail` (line `0x27d`). |
| `0x42009bc0` | `putString` (NVS write) | Logs `nvs_set_str_fail` (line `0x165`). |
| `0x42009c6c` | `put` (NVS set wrapper) | Used by the seeder; logs `Put data failed size %d %d`. |

### Why a custom endpoint persists — [V] verified

`configCheckFistTime` (`FUN_42009f28`, decomp ~54484–54568) loops the descriptor table and
for each key:

```
val = getString(key, default="NULL")        # sentinel
if (len(val)==4 && val=="NULL"):             # key ABSENT in NVS
    put(key, schema_default)                 # seed it  (ENDPOINT_STR <- "default")
else:                                         # key PRESENT
    keep stored value                         # <-- NOT overwritten
```

So once `ENDPOINT_STR` holds a real URL, the seeder takes the **keep** branch every boot —
`configCheckFistTime` itself will **not** clobber it.

> **Correction to prior handoff:** the inherited note "NOP the `nvs_set_str` in
> `configCheckFistTime` at flash `0x2506f8`" is **mislocated** — `FUN_42009f28` is the
> generic seed-if-absent config init, and `0x2506f8` maps into its *literal pool*
> (~VA `0x420006f8`), not an instruction.

### ⚠️ But registration DOES overwrite it — this is the real clobber [V]

A device NVS readback proved a custom `ENDPOINT_STR` does **not** survive in practice: a
hand-set `http://192.168.8.245:9000` was later reset to `default`. `configCheckFistTime`
isn't the culprit — **registration is**. The endpoint has exactly one typed writer,
`set_endpoint` = `FUN_4200aaa8` (`set_config(6, …)`), and that has exactly one caller:

**`RegisterNotifySuccess` = `FUN_42005ec4`** runs on every *successful* cloud registration
and commits the server's response into NVS:

```
set_config(0x84-field);                 // FUN_4200aa98
set_token(   resp +0xe4 );               // FUN_4200aab8  (index 2)
set_endpoint(resp +0xb4 );               // FUN_4200aaa8  (index 6)  <-- overwrites ENDPOINT_STR
set_config_int(3, user_id);              // FUN_4200a474
set_config_int(0xc, 1);                  // REGISTER_STR = 1
log "RegisterNotifySuccess finish"
```

So whatever endpoint the server hands back in its registration response (`+0xb4`) is written
over `ENDPOINT_STR` — and when that value is `"default"`, a user-set URL is wiped. This
fires on every registration, which is why it "kept coming back."

### Fix: pin the endpoint

Two ways, both grounded:

1. **Firmware NOP (definitive):** NOP the `set_endpoint` call so registration stops touching
   the endpoint. Token / user_id / `REGISTER_STR=1` are still set, so the boot gate is
   satisfied; `ENDPOINT_STR` becomes purely user-controlled.
   - Instruction: `call ... 0x4200aaa8` at **VA `0x42005f06`** / **file offset `0x235f06`**
     in the app image. Bytes `25 ba 04` → 3-byte NOP `F0 20 00`.
   - **Boot validation is skipped on power-on** on this device (a raw splice that fixed
     neither the checksum nor the SHA256 was observed to boot), so a direct esptool/JTAG
     flash of just the NOP boots fine. Secure boot is OFF (no signing); flash encryption is
     OFF (readable dump). Image integrity (1-byte XOR checksum + appended SHA256) only matters
     if the image goes through a validated OTA path — and note both live at the **end of the
     image** (checksum `0x40bd2f`, SHA256 `0x40bd30`), *not* the end of the 4 MB partition dump.
   - `tools/patch_pin_endpoint.py` parses the image, NOPs the call, and fixes both checksum
     and hash so the output is a fully valid image regardless (verified: checksum + SHA256
     both pass post-patch). Flash to the active OTA slot (`fw_main.bin` came from `0x20000` →
     typically `ota_0`).
2. **Control the response (no patch):** once your local server implements the `/connect`
   registration, have its `RegisterNotifySuccess` response carry *your* endpoint in `+0xb4` —
   then it persists by design. Until then, the NOP lets you pin it immediately.

### Transport

- mbedTLS with `mbedtls_ssl_set_hostname` used for SNI.
- **TLS server-cert verification is NOT enforced.** In `open_ssl_connection` (`FUN_4201f8f0`)
  the handshake calls `mbedtls_ssl_get_verify_result`; on failure it logs a *warning*
  ("Failed to verify certificate") and **returns success anyway** (both the verify-pass and
  verify-fail paths `return 0`). No pinning. Consequence: **any cert is accepted** — a
  transparent MITM (e.g. mitmproxy with its default CA) works with **no firmware patch**, and
  a local `http://` endpoint needs no cert either. (Earlier notes claiming REQUIRED-mode
  verification / a needed CA patch were wrong — inferred from cert error strings, disproven by
  the control flow.)
- For protocol capture: DNS-redirect the cloud host to a mitmproxy box; the ORB will log the
  verify warning on serial and proceed, so the exchange is captured in plaintext.

---

## Confirmed from a real NVS dump (2026-06-15)

A readback of the device's `nvs` partition (10×4 KB pages, ESP-IDF v2 format) confirms the
model end-to-end:

- **Namespace: `my-app`** (index 1). All config keys above live here. (`phy` holds RF cal.)
- **`ENDPOINT_STR` write history** (by page seq; the partition is log-structured so old
  values remain until GC):

  | seq | state | value |
  |-----|-------|-------|
  | 0 | erased | `default` (initial seed) |
  | 2 | erased | `https://chat-buddyos.iviet.com` ← **production cloud**, written at registration |
  | 4 | erased | `http://192.168.8.245:9000` ← **local override** (LAN, plain HTTP, custom port) |
  | 7 | **live** | `default` (current) |

- **The override path is real, not theoretical** — a plain-`http://` LAN URL was written to
  `ENDPOINT_STR`, so the key accepts arbitrary URLs including non-TLS ones. Combined with the
  absence of cert pinning, a local **`http://`** server is a viable target (no cert needed).
- **Production endpoint to reimplement:** `https://chat-buddyos.iviet.com` → `…/connect`.
- **Registration writes a tuple:** `REGISTER_STR` (0/1), `PROFILE_STR` (UUID), `USER_ID_INT`,
  and `TOKEN_STR` (a **JWT**; backend is Hasura — claims `x-hasura-*`, role `member`). The
  `/connect` channel is JWT-authenticated; a local server controls its own auth.
- **`"default"` resolution is still unconfirmed:** the current live `ENDPOINT_STR` is
  `"default"` while `REGISTER_STR=1`, yet `chat-buddyos.iviet.com` is **not** a plaintext
  literal in `fw_main.bin`. So `"default"` either maps to an assembled/obfuscated host, is
  supplied during BLE provisioning, or the registration state is stale. Resolve by reading
  the URL-builder (where `ENDPOINT_STR` is read and `%s/connect` is formatted).

> 🔒 **The NVS dump contains a live credential** (`TOKEN_STR` JWT embedding the account
> email + user id). Treat `nvs_readback.bin` as secret — do **not** commit it to the repo or
> share it. It is intentionally excluded via `.gitignore`.

## The plan (easiest → most invasive)

### 1. Repoint `ENDPOINT_STR` to your own server, AND stop registration overwriting it
Setting the key is necessary but not sufficient — registration (`RegisterNotifySuccess`)
rewrites it back from the server response (this is the confirmed clobber above). So:

**Set the key** (either):
- **Console (if exposed):** the firmware registers an `nvs` token + a `GET` token. Run `help`
  on the serial console; if it's the IDF `nvs` component the flow is `nvs_namespace my-app`
  then `nvs_set ENDPOINT_STR str -v "http://<lan-ip>:<port>"`. (Namespace **confirmed**:
  `my-app`.)
- **Offline NVS edit:** rewrite `ENDPOINT_STR` in the `nvs` partition (`tools/nvs_parse.py`
  reads it; esp-idf `nvs_partition_gen.py` writes), reflash only the nvs partition.

**Stop the overwrite** (either):
- **NOP the writer (definitive):** `tools/patch_pin_endpoint.py` — NOPs `set_endpoint` at file
  offset `0x235f06` and rehashes. After this, the NVS value is authoritative forever.
- **Or control the response:** your local server returns your endpoint in the registration
  response (`+0xb4`), so it persists with no patch.

**Server side:** answer `<endpoint>/connect` (HTTP/2 downchannel, `olli-session-id` header,
JWT auth — you control it) and bridge to your local LLM/TTS. Protocol framing TBD — from the
`olli_h2_*` functions or a MITM capture.

### 2. (Fallback) boot without any cloud
If you only want the orb to *not brick* (animation player, no conversation): NOP/flip the
boot gate `FUN_4202d050`. In-place, no free flash. Loses AI conversation; keeps it alive.

---

## Open items to verify next
- [x] NVS **namespace** = `my-app` (confirmed from dump).
- [x] TLS: no pinning; plain `http://` LAN endpoint accepted (override was written in NVS).
- [x] Production host known: `https://chat-buddyos.iviet.com`.
- [x] **Endpoint overwrite found:** `RegisterNotifySuccess` (`FUN_42005ec4`) → `set_endpoint`
      (`FUN_4200aaa8`) at VA `0x42005f06`. Fix = NOP (`tools/patch_pin_endpoint.py`) or
      server-controlled response.
- [ ] How **`"default"`** resolves to a host (URL-builder path; host not a plaintext literal).
- [ ] The `<endpoint>/connect` **HTTP/2 protocol** (framing, JWT/session lifecycle) — from
      `olli_h2_task`/`olli_h2_send_*` or a live capture.
