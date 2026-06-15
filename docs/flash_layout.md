# Flash Layout — Free Flash vs. Injectable Space

A recurring point of confusion: the firmware notes say there is **"no free space"** for code
injection, yet the chip has **megabytes of unused flash**. Both are true — they're different
things. This doc keeps them straight.

## Partition table (`pt.bin`, 16 MB flash, pure OTA)

| Name | Type | Offset | Size | Notes |
|------|------|--------|------|-------|
| `nvs` | data | `0x009000` | 40 KB | config keys (namespace `my-app`) |
| `otadata` | data | `0x013000` | 8 KB | which OTA slot boots |
| `ota_0` | app | `0x020000` | **5 MB** | app slot A (the dumped image lives here) |
| `ota_1` | app | `0x520000` | **5 MB** | app slot B — **spare** |
| `spiffs` | data | `0xa20000` | 5 MB | filesystem |

The app image proper occupies `0x020000`–`~0x42bd50` (≈4.04 MB), so even within `ota_0`
there is ≈0.95 MB of slack (and it isn't clean `0xFF` — stale bytes from a prior image
remain). On top of that, the entire 5 MB `ota_1` slot is unused while the device boots from
`ota_0`. So: **lots of free flash.**

## The two kinds of "space"

### 1. Free space inside the mapped code segment — NONE
What an in-place splice needs: a run of unused bytes **inside seg3** (the IROM that is
memory-mapped and executing), big enough to hold a function that existing code can reach with
a near call. A full scan finds **zero `0xFF`/`0x00` runs ≥64 B in seg3** (re-verified
2026-06-15). seg3 is fully packed. This is the wall that killed the cmd1 injection — the
"cmd2 stub region" at `0x42043c40` is live code, not padding.

### 2. Free flash in the partition — PLENTY, but not executable as-is
The `ota_0` tail, all of `ota_1`, and `spiffs` are dormant flash. The running image does
**not map them into the instruction address space** — only seg3's bytes are mapped as code.
Dropping bytes there does nothing until they are mapped.

## Using the free flash for code (the heavier path)

To execute from the free flash you restructure the image rather than patch it in place:

- **Add a new IROM segment** to the image header pointing at the free flash, then hook
  existing code to call into it.
- **64 KB alignment** is mandatory: ESP32-S3's flash MMU page size is 64 KB, so an IROM/DROM
  segment must be 64 KB-aligned in **both** flash offset and virtual address.
- Fix the **1-byte XOR checksum** and the **appended SHA256** afterward (see
  `tools/patch_pin_endpoint.py` for how those are located — at the image end, not the dump
  end). On this device the bootloader skips validation on power-on, but a clean image is still
  worth producing.
- This is real and viable for *substantial* added code (e.g. a local-control shim). It was
  overkill for the tiny cmd1 handler, and it would not have fixed cmd1's *other* blocker —
  running `fan_sync_binary_file` from the console task overflowed that task's stack.

## Bottom line

- In-place overwrite injection → blocked (seg3 is packed).
- Append-a-segment injection → possible (plenty of partition room), but it's image
  restructuring with 64 KB alignment + integrity fixup, not a quick patch.
- The de-cloud and content goals need neither — they're a 3-byte NOP
  (`tools/patch_pin_endpoint.py`) and SD/`character.info` edits respectively.
