# cmd1_stub — IDF build project for the orb cmd1 patch

> **ABANDONED approach — see [`../docs/AUDIT.md`](../docs/AUDIT.md).** This code is correct
> (lwip addresses, sockaddr, literal relocation, J-redirect all verified) but the inject-and-splice
> strategy does not work on this firmware: there is **no free flash** to place the blob (the
> `0x42043c40` "stub region" is live code), and `fan_sync_binary_file` **overflows the console
> task stack**. Flashing it broke boot audio. Kept for reference; use the `syncing_tracking.info`
> data path instead.

Compiles `cmd1_handler()` against ESP-IDF 5.5 so we can extract
the Xtensa machine code and splice it into the OLLI firmware image.

## Prerequisites
- ESP-IDF 5.5 installed at `~/esp/esp-idf-5.5`
- Target: ESP32-S3

## Build

```bash
# Source the IDF environment
. ~/esp/esp-idf-5.5/export.sh

# Set target (only needed once)
idf.py set-target esp32s3

# Build
idf.py build
```

## Extract the patch bytes

```bash
python3 extract_cmd1.py
```

This produces:
- `cmd1_handler.bin` — raw Xtensa bytes, ready to splice
- `cmd1_handler.asm` — disassembly for sanity-checking

## Splice into firmware

```bash
# First check available space in the firmware
python3 find_padding.py fw_main.bin

# Then splice (addresses from find_padding.py output)
python3 splice_patch.py fw_main.bin cmd1_handler.bin <thunk_fo> 0x273808 <thunk_va>

# Flash
esptool.py --port /dev/ttyUSB0 --baud 921600 write_flash 0x20000 fw_patched.bin
```

## Customising the source/dest paths

Edit `components/cmd1/cmd1_patch.c`:
```c
#define SRC_PATH  "/sdcard/custom/my_animation.bin"   // file on main board SD
#define DST_NAME  "eb_idle_eb1_64.bin"            // slot to overwrite on fan
```

Then rebuild and re-extract.

## Project structure

```
cmd1_stub/
  CMakeLists.txt          top-level IDF project
  sdkconfig.defaults      minimal ESP32-S3 config
  main/
    CMakeLists.txt
    app_main.c            stub entry point (never flashed)
  components/
    cmd1/
      CMakeLists.txt
      cmd1_patch.h        public header
      cmd1_patch.c        the actual handler
  extract_cmd1.py         pulls handler bytes from the built ELF
```
