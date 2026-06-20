# Fan TCP Protocol

The main board communicates with the blade over plain TCP on `172.10.10.1:4800`. The blade ESP32 acts as a WiFi bridge, forwarding traffic to the HC32 over SPI. The HC32's `cmd 0x08` handler receives file uploads and writes them to the SD-NAND via FatFs.

The connection is **per-sync** — opened when a file transfer begins and torn down (`conn = -1`) when it completes. There is no persistent connection.

---

## Frame format

```
0xAA  [len BE 4B]  [cmd 1B]  [payload]  [checksum]  0xA5
```

- `0xAA` = start of frame
- len = payload length, big-endian 32-bit
- cmd = command byte
- payload = command-specific data
- checksum = single byte XOR or sum (exact algorithm unconfirmed)
- `0xA5` = end of frame

---

## Commands

### Upload file (`cmd = 0x31`)

Initiates a file upload to the blade. Sent by `send_request_upload()` at `0x420296e8`.

Payload:
```
[filesize BE 4B]  [filename, null-terminated]
```

Response codes from blade:
- `0x00` = OK, proceed with data
- `0x82` = duplicate (file already exists with same content)
- `0x80` = busy

After a `0x00` response, the main board streams the file contents. The blade writes them to the SD-NAND as `0:/[filename]`.

### Query video list

Requests the list of files on the blade SD-NAND. The blade responds with one line per file:
```
uart_send_ctr_fan_request_video_list_name_file: index [N] name file [NAME]
```
Approximately 111 files, streamed over ~20 seconds.

### Delete file

Sends a delete command for a named file on the blade SD-NAND.

### Other commands

Additional control frames exist for: power on/off, brightness, speed (motor), angle, state transitions. These are sent via the UART path through the MM32 base MCU rather than TCP.

---

## Upload function chain (main board)

```
fan_sync_binary_file(conn, src_path, dst_name, flag)   @ 0x4202980c
  → get_file_size(src_path)                             @ 0x42015f34
  → send_request_upload(conn, dst_name, filesize)       @ 0x420296e8
  → send_file_data(conn, src_path, flag)                @ 0x4202919c
      → tcp_send_all(conn, buf, len)                    @ 0x42029150

Returns: 1 = success, 2 = duplicate, -1 = send error, -2 = transfer error, -5 = file read error
```

---

## HC32 receive path

The HC32's master loop (`FUN_0000cff4`) dispatches on a command byte from the main board:

| Byte | Action |
|------|--------|
| `0x01` | Reload `0:/Config.txt` |
| `0x03` | Reload `0:/WifiConfig.txt` |
| `0x04` | Reload `0:/displist.txt` |
| `0x05` | Mode switch A |
| `0x06` | Mode switch B |
| `0x07` | Param exchange (param_net/param_dev JSON, 8 bytes in / 9 bytes out) |
| `0x08` | **File receive** → FatFs f_write to SD-NAND |

**HC32 FatFs stack:**

| Function | Role |
|----------|------|
| `FUN_0000c97c` | f_open |
| `FUN_0000c660` | open + read named file |
| `FUN_0000ba64` | f_read / f_write block worker (512-byte sectors) |
| `FUN_0000bfe4` | f_close / f_sync |

---

## Connecting to the fan AP

The blade runs a WiFi AP. Credentials are stored in `WifiConfig.txt` on the blade SD-NAND (redacted here). The main board connects as STA and establishes the TCP connection to `172.10.10.1:4800`.

**ABANDONED** (see [AUDIT.md](AUDIT.md)): a `cmd1` handler that opens its own TCP connection was the original plan, but there is no free flash to place the handler and running the upload from the console task overflows its stack (the stock firmware uses a dedicated sync thread). Use the `syncing_tracking.info` path instead.

## REQUEST_UPLOAD (0x31) — device → fan, verified against fan fw (HC32, 2026-06-19)

Packet built by `send_request_upload` (device `FUN_420296e8`): `0xAA` start, 3-byte length,
len byte, `0x31` cmd, 4-byte big-endian file size (`[6..9]`), filename bytes, `0xA5` end, checksum.
The device requires **filename strlen < 31** (`if (uVar2 < 0x1f)`) or it returns 2 without sending
→ `fan_sync_binary_file` `kret -2`. Filename used = the on-fan dest basename
`<name>_<character>_<verhex>.bin`.

Fan side (`fanblade_hc32.bin`, Cortex-M0+): command dispatch `case 0x31:` → `FUN_000019fc` validates
and replies with a status byte (cmd 0x31, framed `…0x5A`). `FUN_000019fc`:
- verifies the trailing framing magic (else returns `0x86`),
- copies the filename into a 52-byte buffer (so the fan tolerates up to ~50 chars; the device's 31
  is the binding limit), appends CRLF,
- scans existing slot entries for a **duplicate** name → returns `0x82`,
- writes the entry to NAND via `FUN_0000c03c`; `0` = success, `0x81` = NAND write fail,
  `0x85` = invalid slot.

Device-side return mapping (`fan_sync_binary_file` `FUN_4202980c`): fan `0` → "Sending File" then
data; `0x82` → duplicate (return 2); `0x80` → busy (retry); `2`/other → `kret -2`.
