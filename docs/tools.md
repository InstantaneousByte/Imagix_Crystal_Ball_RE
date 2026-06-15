# Tools Used

## Hardware

- **ST-Link V2** (or clone) — SWD debugger for HC32F460 and MM32G0001 via OpenOCD
- **USB-to-Serial UART adapter** (3.3V logic, e.g. FTDI FT232, CH340, CP2102) — ESP32 flash dumping and serial console access
- **microSD breakout/adapter** — SD-NAND dumping via soldered wires to blade PCB pads
- **Fine-tipped probes / soldering equipment** — accessing PCB test pads

---

## Firmware extraction

- **[esptool.py](https://github.com/espressif/esptool)** — ESP32 flash read/write, partition table parsing
- **[OpenOCD](https://openocd.org/)** — SWD interface for ARM Cortex-M dumps (HC32F460, MM32G0001)
- **[tio](https://github.com/tio/tio)** — serial console access to the ESP32-S3 at 3 Mbaud
- **dd** (standard Linux utility) — SD-NAND imaging
- **[OSFMount](https://www.osforensics.com/tools/mount-disk-images.html)** (Windows) — mounting raw .img files for filesystem access

---

## Reverse engineering / static analysis

- **[Ghidra](https://ghidra-sre.org/)** (NSA Research Directorate, open source) — primary disassembly and decompilation tool for both ESP32-S3 (Xtensa) and HC32F460 (ARM Cortex-M4). The Xtensa plugin ships with Ghidra. 9946 functions analyzed on the main board firmware; 309 on the blade.
- **[binwalk](https://github.com/ReFirmLabs/binwalk)** — initial firmware structure analysis, segment identification
- **Python 3** — scripting throughout: segment map parsing, address resolution, string extraction, binary patching

---

## Animation decoding / encoding

All implemented in Python:

- **[NumPy](https://numpy.org/)** — bit unpacking, array operations, FFT cross-correlation for phase analysis
- **[Pillow (PIL)](https://pillow.readthedocs.io/)** — GIF rendering and export
- **[SciPy](https://scipy.org/)** — curve fitting (linear + sinusoidal model for rotation correction), uniform filtering

See `tools/orb_decoder.py` and `tools/orb_encode.py`.

---

## Firmware patching

- **[ESP-IDF 5.5](https://github.com/espressif/esp-idf)** — build environment for the cmd1 patch handler (`cmd1_stub/`)
- **xtensa-esp-elf-gcc 14.2.0** — Xtensa compiler (ships with IDF 5.5)
- **xtensa-esp-elf-objdump / objcopy / nm / readelf** — ELF inspection and binary extraction
- **cmake + ninja** — build system (IDF dependency)

---

## A note on methodology

A significant portion of this reverse engineering work was carried out with the assistance of a large language model (Claude, by Anthropic), used as an interactive analysis partner throughout the process. The LLM assisted with:

- Ghidra decompilation interpretation and function naming
- Binary format reverse engineering (POV column format, TCP frame structure)
- Cross-referencing function addresses and call graphs
- Writing and debugging the Python analysis tools
- Reconstructing C source from decompiled output
- Designing and iterating on the firmware patch

All conclusions were verified against the actual binary data. The hardware work — soldering, dumping, console access, physical disassembly — was done by the human researcher.

This is mentioned not as a caveat but because it's an honest description of how the work was done, and because the approach (LLM-assisted binary analysis) may be useful to others attempting similar projects.

## nvs_parse.py

Parses an ESP-IDF NVS partition dump (v2). Prints the current live config (latest written entry per key) and optional per-key write history across the log-structured partition. Redacts `TOKEN_STR`/`PROFILE_STR`/JWTs by default (`--raw` to override).

```
python3 tools/nvs_parse.py nvs_readback.bin
python3 tools/nvs_parse.py nvs_readback.bin --history ENDPOINT_STR
```

Confirmed namespace `my-app`; see [nvs_config_reference.md](nvs_config_reference.md).
