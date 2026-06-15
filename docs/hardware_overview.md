# Hardware Overview

## Board layout

Three PCBs: a **main board** (sits in the base housing), a **motor board** (underneath the base, drives the fan motor), and the **blade** (the spinning arm). They communicate wirelessly — the main board runs a WiFi AP (`172.10.10.x`) that the blade ESP connects to. The main board and motor board communicate via a 3-pin JST connector through a level-shifting/inverting transistor (Q10, 3 Mbaud). The motor board was not a primary focus of this research — its role is motor PWM and transparent UART relay — but it is documented below.

---

## Main board

**MCU: Espressif ESP32-S3** (dual-core Xtensa LX7, 16MB flash)

- Firmware: `olli_esp_sdk`, IDF 5.5.0, built April 2026
- Flash at `0x20000`, image ~4143 KB, flash chip 16MB
- Handles: WiFi, cloud (HTTP/2), audio playback, serial console, persona logic, fan command dispatch
- SD card mounted at `/sdcard` (main board's own SD — audio files, config, animation transit)
- SPIFFS at `/spiffs` (secondary wifi config)
- Serial console: 3 Mbaud, GPIO6 TX (inverted by Q10 before the 3-pin JST to base)

---

## Motor board

**MCU: MindMotion MM32G0001A6T** (Cortex-M0, SOP-8, ~4KB flash)

- Version string: `C11-CD_MM_25080101`
- Runs at 5V
- USART1: 3-pin JST → main board (3 Mbaud, inverted/level-shifted by Q10)
- USART2: optical link candidate (to blade)
- Role: motor PWM control, UART relay
- Dump method: one-shot `reset halt` via SWD (NRST shared with SWCLK)

---

## Blade

**WiFi bridge: Espressif ESP32-U4WDH** (Xtensa LX6, 4MB embedded flash)

- Role: WiFi modem and content bridge only — no filesystem, doesn't own the SD-NAND
- Connects to main board AP, relays content to HC32 over SPI

**Render/storage: HDSC HC32F460JEUA** (Cortex-M4F, 512KB flash, 192KB SRAM)

- Version string: `C11_HC_25071000`
- Flash at `0x0`, SRAM at `0x1FFF8000`
- Role: owns SD-NAND, renders POV columns, Hall-locked
- Peripherals: SDIOC1 (SD-NAND), TMR4 (column clock), AOS (Hall→timer→DMA event routing), DMA1/2 (pixel feed), SPI1/2 (LED output), USART1/2
- SWD unprotected at time of dump

---

## Storage: FORESEE FLSD032G SD-NAND

4GB SD-NAND soldered to the blade PCB. Standard microSD-equivalent interface (CK, CMD, D0–D3, 3V3, GND). FAT filesystem, single partition.

**Key files:**

`Config.txt`:
```
play mode:1
brightness:100
angle:229
baud:4
speed:0
```

`WifiConfig.txt`:
```
AP_SSID:[redacted]
AP_PWD:[redacted]
SER_IP:172.10.10.1
SER_PORT:4800
WIFI_MODE:2
```

`displist.txt`: plain newline-separated list of animation filenames, ~111 entries. Line index = slot number used by the persona state machine.

**File naming conventions:**
- `eb_*` = Ember the dragon animations
- `el_*` = Ellie the fairy animations
- `bu_*` = boot/utility animations (shared)
- `_64` suffix = primary display profile (`baud:4`)
- `_67` suffix = alternate display profile

---

## Communication paths

```
cloud ←→ ESP32-S3 (main board)
              ↕ WiFi AP (172.10.10.x)
         ESP32-U4WDH (blade WiFi bridge)
              ↕ SPI
         HC32F460 (blade render/storage)
              ↕ SDIOC1
         FORESEE SD-NAND (animations)
              ↕ Hall sensor + AOS + TMR4 + DMA
         90× LED strip (POV display)
```

```
ESP32-S3 ←→ MM32G0001 (base)
              ↕ optical link (USART2, candidate)
         blade rotation sync
```

---

## Dump methods

| Chip | Method | Notes |
|------|--------|-------|
| ESP32-S3 | `esptool.py read_flash` | Standard, via USB |
| HC32F460 | OpenOCD + ST-Link via SWD | Pads on blade PCB, unprotected |
| MM32G0001 | OpenOCD one-shot `reset halt` | NRST/SWCLK shared, requires careful sequencing |
| ESP32-U4WDH | `esptool.py read_flash` | Via blade test pads |
| SD-NAND | `dd if=/dev/sdX` | Unmount first; double-read to verify |
