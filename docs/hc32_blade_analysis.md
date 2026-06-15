# HC32F460 Blade Firmware Analysis

Firmware: `fanblade_hc32.bin`  
Size: 524,288 bytes (512KB)  
Code occupies ~60KB of the 512KB flash  
Analyzed with Ghidra: 309 functions  

Ghidra setup:
- Language: ARM:LE:32:Cortex (ARM Cortex little-endian, 32-bit)
- `fanblade_hc32.bin` loaded at base `0x00000000`
- SRAM block: `0x1FFF8000`, length `0x30000` (192KB), RWX
- Peripheral block: `0x40000000`, length `0x60000`, RW (no-execute)
- Reset handler: `0x00000294` (vector `0x00000295`, Thumb bit cleared)
- Initial SP: `0x200f0b80`

---

## Master command loop — `FUN_0000cff4`

The blade's main loop dispatches on a command byte set by the main board:

| Byte | Action |
|------|--------|
| `0x01` | Reload `0:/Config.txt` |
| `0x03` | Reload `0:/WifiConfig.txt` |
| `0x04` | Reload `0:/displist.txt` |
| `0x05` | Mode switch → `FUN_00001648(1)` |
| `0x06` | Mode switch → `FUN_00001648(2)` |
| `0x07` | Param exchange — reads 8 bytes in, appends a byte, sends 9 back. This is the `param_net`/`param_dev` JSON handshake with the ESP. |
| `0x08` | **File receive** — block I/O via `FUN_0000ba64`, 512-byte sectors. This is the upload landing path. |
| `0x09` / `0x0a` | Additional command bytes present in dispatcher (partially characterized) |

---

## FatFs stack

| Function | Role |
|----------|------|
| `FUN_0000c97c` | f_open core (path parse + open) |
| `FUN_0000c660` | open + read named file (wraps f_open for config files) |
| `FUN_0000ba64` | f_read / f_write block worker — 512-byte sectors, FAT cluster validation, FRESULT codes |
| `FUN_0000bfe4` | f_close / f_sync — flush + clear handle |
| `FUN_0000cef0` | Handle table lookup (fd → file object) |

Handle table at `DAT_0000be70` / `DAT_0000c038`.

---

## Peripheral map

| Peripheral | Base address | Role |
|------------|-------------|------|
| SDIOC1 | `0x4000F800` | SD-NAND controller (heaviest used, 26 refs) |
| TMR4 | `0x40020000` | POV column render clock |
| AOS | `0x40051000` | Hardware event router (Hall → timer → DMA) |
| DMA1 | `0x40053000` | Pixel DMA feed |
| DMA2 | `0x40053400` | Pixel DMA feed |
| USART1 | `0x4001D000` | ESP config link |
| USART2 | `0x4001D400` | Optical link candidate |
| SPI1 | `0x4001C000` | LED output |
| SPI2 | `0x4001C400` | LED output |
| TMR6 | `0x40021000` | Hall period measurement |
| PORT/GPIO | `0x40053800` | GPIO |
| EFM | `0x40054000` | Embedded flash manager |

---

## Config.txt parsing

On init and on `0x01` command, the blade reads `0:/Config.txt` and parses key:value pairs:

```
play mode:1      → playback mode
brightness:100   → LED brightness (0–100)
angle:229        → POV rotation phase offset (0–359°)
baud:4           → display profile selector (4 = _64 suffix files)
speed:0          → motor speed
```

The `angle` value directly offsets which column is considered "column 0" in the render loop, rotating the displayed image.

---

## Upload receive path

The `0x08` command handler receives file data from the main board via the SPI/UART link and writes it to the SD-NAND using `FUN_0000ba64` (f_write). The destination filename is specified in the transfer header. After writing, the blade updates `0:/displist.txt` if needed.

This is the receiver side of the `fan_sync_binary_file` → TCP upload chain documented in [`fan_tcp_protocol.md`](fan_tcp_protocol.md).
