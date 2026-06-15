# De-clouding: API Endpoint Mechanism & Survival After Server Shutdown

**Goal:** keep the orb usable (and point it at a self-hosted LLM backend) after Imagix's
cloud goes dark. **Headline result: this needs no firmware rewrite, and most likely no
code patch at all** — the API endpoint is configuration *data* in NVS, not a baked-in code
literal.

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

So once `ENDPOINT_STR` holds a real URL, the seeder takes the **keep** branch every boot.
**A custom `ENDPOINT_STR` survives reboots with zero firmware modification.**

> **Correction to prior handoff:** the inherited note "NOP the `nvs_set_str` in
> `configCheckFistTime` at flash `0x2506f8`" is both **unnecessary** and **mislocated**.
> `FUN_42009f28` is the generic seed-if-absent config init, and `0x2506f8` maps into its
> *literal pool* (~VA `0x420006f8`), not an instruction. No NOP is required to make a
> custom endpoint stick.

### Transport

- mbedTLS with the **stock Mozilla CA bundle**; `mbedtls_ssl_set_hostname` used for SNI.
- **No certificate pinning** observed. A local HTTPS server with a cert chaining to a CA in
  the bundle will be accepted; whether plain `http://` endpoints are accepted is **[?]** —
  test, or terminate TLS at the local box.

---

## The plan (easiest → most invasive)

### 1. Repoint `ENDPOINT_STR` to your own server — no firmware change
This is the primary path and it doubles as the local-LLM hook.

**Setting the key:**
- **Console (preferred, if exposed):** the firmware registers an `nvs` console token and a
  `GET` token alongside the literal key names. On the serial console run `help` to confirm;
  if it's the IDF `nvs` console component the flow is roughly
  `nvs_namespace <ns>` then `nvs_set ENDPOINT_STR str -v "https://orb.local/"`.
  *(Namespace name still [?] — find it next.)*
- **Offline NVS edit (always works, data-only, reversible):** dump the `nvs` partition,
  edit/insert `ENDPOINT_STR` with esp-idf's `nvs_partition_gen.py` / NVS tooling, reflash
  **only** the nvs partition. No code touched.

**Server side:** stand up a box answering `<endpoint>/connect` (HTTP/2 downchannel,
`olli-session-id` header) and bridge it to your local LLM/TTS stack. Protocol details TBD —
a MITM capture of a still-live unit (or the `olli_h2_*` functions) will nail the framing.

### 2. (Only if needed) skip registration
If the downchannel won't open until a `REGISTER_STR` flag is set, either have your local
server complete the registration handshake, or set `REGISTER_STR` in NVS the same way.

### 3. (Fallback) boot without any cloud
If you only want the orb to *not brick* (animation player, no conversation): NOP/flip the
boot gate `FUN_4202d050` (the cloud-registration-complete check). In-place, no free flash.
Loses AI conversation; keeps the device alive.

---

## Open items to verify next
- [ ] Exact NVS **namespace** for `ENDPOINT_STR`, and whether the `nvs`/`GET` console
      commands can write it directly.
- [ ] The `<endpoint>/connect` **HTTP/2 protocol** (framing, auth, session lifecycle) —
      from `olli_h2_task`/`olli_h2_send_*` or a live capture.
- [ ] Whether registration (`REGISTER_STR`) must succeed before the downchannel opens.
- [ ] TLS: does a local cert chaining to the Mozilla bundle suffice; is `http://` accepted.
