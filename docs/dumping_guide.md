# Dumping the Firmware

Everything in this repo was derived from firmware dumps taken directly from the hardware. If you want to reproduce the analysis or verify the function addresses against your own unit, here's how to get the same files.

> **Note on enclosure damage:** Accessing the UART pads on the main board and the SWD pads on the blade both require opening the device, and doing so is destructive to the enclosure. The plastic clips and adhesive do not survive disassembly cleanly. Plan accordingly — 3D printed replacement enclosure parts may be made available in the future.

---

## What you need

**For the ESP32 boards (main board + blade ESP):**
- USB-to-Serial UART adapter (3.3V logic — e.g. FTDI FT232, CH340, CP2102)
- esptool.py (`pip install esptool`)
- Fine-tipped probes or soldering equipment

**For the ARM chips (HC32F460 + MM32G0001):**
- ST-Link V2 (or clone) with SWD support
- OpenOCD (any recent version)
- Fine-tipped probes or soldering equipment

**For the SD-NAND:**
- Fine-tipped probes or soldering equipment for the blade PCB pads
- A microSD breakout board or adapter
- Linux system with `dd`

---

## Main board ESP32-S3

No secure boot, no flash encryption. Straightforward dump via UART.

**Access:**

Open the base enclosure to reach the main board. Locate the UART TX/RX/GND pads (3.3V logic). Connect to your USB-to-Serial adapter. To enter bootloader mode, hold GPIO0 low while applying power.

**Dump:**

```bash
# Read the partition table first to confirm offsets
esptool.py --port /dev/ttyUSB0 read_flash 0x8000 0x1000 partition_table.bin

# App image (starts at 0x20000 on this device — confirm from partition table)
esptool.py --port /dev/ttyUSB0 --baud 921600 \
    read_flash 0x20000 0x420000 fw_main.bin

# Full 16MB dump if you want everything
esptool.py --port /dev/ttyUSB0 --baud 921600 \
    read_flash 0x0 0x1000000 fw_full_16mb.bin
```

**Verify:**

```bash
python3 -c "
import struct
d = open('fw_main.bin','rb').read()
magic = struct.unpack('<I', d[0x20:0x24])[0]
idf   = d[0x20+112:0x20+144].split(b'\x00')[0].decode()
proj  = d[0x20+48:0x20+80].split(b'\x00')[0].decode()
print(f'magic:   {magic:#010x}  (expect 0xabcd5432)')
print(f'IDF:     {idf}')
print(f'project: {proj}')
"
```

Expected for the firmware this repo was built from:
```
magic:   0xabcd5432
IDF:     v5.5
project: olli_esp_sdk
```

---

## Blade ESP32-U4WDH

Same procedure as the main board — USB-to-Serial adapter, esptool. 4MB flash.

Locate UART and GPIO0 pads on the blade PCB.

```bash
esptool.py --port /dev/ttyUSB0 --baud 921600 \
    read_flash 0x0 0x400000 fanblade_esp32.bin
```

---

## Blade HC32F460JEUA (SWD)

Cortex-M4F, 512KB flash. **No RDP (readout protection) on tested units** — may change in future hardware revisions.

**SWD pads:**

Four pads on the blade PCB: SWDIO, SWDCLK, 3V3, GND. They are small — use fine-tipped probes or solder thin wires carefully. The blade must be powered during the dump (either power it externally via the 3.3V pad or perform the dump while the device is running).

**OpenOCD config (`hc32f460.cfg`):**

```tcl
source [find interface/stlink.cfg]
transport select hla_swd

set CHIPNAME hc32f460
set CPUTAPID 0x6ba02477

source [find target/cortex_m.cfg]

reset_config none
adapter speed 1000
```

**Dump:**

```bash
openocd -f hc32f460.cfg -c "
    init
    reset halt
    dump_image fanblade_hc32.bin 0x00000000 0x80000
    dump_image hc32_sram.bin 0x1FFF8000 0x30000
    resume
    shutdown
"
```

**Verify:**

```bash
python3 -c "
import struct
d = open('fanblade_hc32.bin','rb').read()
sp = struct.unpack('<I', d[0:4])[0]
rv = struct.unpack('<I', d[4:8])[0]
print(f'Initial SP:    {sp:#010x}  (expect 0x1fff8000–0x20030000)')
print(f'Reset vector:  {rv:#010x}  (odd = Thumb mode, correct for Cortex-M)')
"
```

---

## Base MM32G0001A6T (SWD)

Cortex-M0 in an SOP-8 package on the motor board. ~4KB flash. The awkward part: on this package variant, NRST and SWCLK share a physical pin, giving you a very narrow window to attach the debugger after reset.

**Connections:**

Locate SWDIO, SWDCLK/NRST, 3V3, GND on the motor board. The chip runs at 5V but the SWD interface tolerates 3.3V from the ST-Link — verify before connecting.

**OpenOCD config (`mm32g0001.cfg`):**

```tcl
source [find interface/stlink.cfg]
transport select hla_swd

set CHIPNAME mm32g0001
set CPUTAPID 0x0BC11477

source [find target/cortex_m.cfg]

reset_config none
adapter speed 500
```

**Dump:**

The shared NRST/SWCLK pin means timing is tight. Connect while the device is running (don't use reset), or power-cycle and attach immediately:

```bash
openocd -f mm32g0001.cfg -c "
    init
    reset halt
    dump_image mm32g0001.bin 0x08000000 0x1000
    shutdown
"
```

If OpenOCD fails to halt, retry immediately after power-on. The dump is only 4KB so it completes in under a second once attached.

---

## SD-NAND (FORESEE FLSD032G)

The SD-NAND is soldered to the blade PCB but presents a standard microSD interface. Pads are labeled on the PCB silkscreen: CK, C (CMD), D0, D1, D2, D3, 3V3, GND.

Solder thin wires to the pads (or use fine probes) and connect to a microSD breakout/adapter.

**Dump (Linux):**

```bash
# Identify the device
lsblk

# Unmount if auto-mounted
sudo umount /dev/sdX1

# Full image (~3.7GB, takes a few minutes)
sudo dd if=/dev/sdX of=orb_sdnand.img bs=4M status=progress conv=sync,noerror

# Verify with a second read
sudo dd if=/dev/sdX of=orb_sdnand_verify.img bs=4M status=progress conv=sync,noerror
cmp orb_sdnand.img orb_sdnand_verify.img && echo "MATCH ✓" || echo "MISMATCH — check for bus contention"
```

A mismatch usually means the HC32 is still driving the SD-NAND bus. Make sure the blade is powered down before dumping, or isolate the SWD/NRST line to hold the HC32 in reset.

**Mount the image:**

```bash
# Linux
sudo mount -o ro,loop,offset=$((2048*512)) orb_sdnand.img /mnt/orb
cat /mnt/orb/Config.txt
cat /mnt/orb/displist.txt

# Windows: use OSFMount (free, from PassMark)
# Open .img → select partition 0 → mount read-only
```

> **Caution:** `WifiConfig.txt` contains WiFi credentials for the device's AP. Handle accordingly and do not share the raw image publicly.

---

## Ghidra setup — ESP32-S3 main board

1. New project → Import `fw_main.bin` as **raw binary**
2. Language: `Xtensa` (little-endian, 32-bit)
3. **Memory Map — set up these segments manually (critical):**

| Name | Start VA | Length | File Offset | Flags |
|------|----------|--------|-------------|-------|
| DROM | `0x3c1d0020` | `0x223808` | `0x20` | R |
| DRAM | `0x3fc9bf00` | `0x8a80` | `0x223828` | RW |
| IRAM0 | `0x40378000` | `0x3d60` | `0x22c0a8` | RWX |
| IROM | `0x42000020` | `0x1cbbe0` | `0x230020` | RX |
| IRAM1 | `0x4037bd60` | `0x100f0` | `0x22fe08` | RWX |
| RTC | `0x600fe000` | `0x20` | `0x22fef8` | RW |

4. Auto Analyze → enable **Aggressive Instruction Finder**
5. Verify: navigate to `0x4202980c` — should be `fan_sync_binary_file`

Without the IROM segment at `0x42000020` (the most commonly missed step), Ghidra finds far fewer functions and all cross-references to the main code section are broken.

---

## Ghidra setup — HC32F460 blade

1. Import `fanblade_hc32.bin` as raw binary
2. Language: **ARM:LE:32:Cortex** (ARM Cortex little-endian)
3. Load at base `0x00000000`
4. Memory Map:

| Name | Start | Length | Source | Flags |
|------|-------|--------|--------|-------|
| FLASH | `0x00000000` | `0x80000` | `fanblade_hc32.bin` @ offset 0 | RX |
| SRAM | `0x1FFF8000` | `0x30000` | `hc32_sram.bin` (optional, for live data) | RWX |
| PERIPH | `0x40000000` | `0x60000` | uninitialized | RW |

5. Auto Analyze — the Cortex-M vector table at `0x0` gives clean entry points
6. Verify: navigate to `FUN_0000cff4` — the master command loop
