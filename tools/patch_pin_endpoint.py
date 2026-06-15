#!/usr/bin/env python3
"""
patch_pin_endpoint.py - stop the firmware from overwriting ENDPOINT_STR.

The endpoint is written in exactly ONE place: RegisterNotifySuccess
(FUN_42005ec4), which on every successful cloud registration commits the
server-supplied endpoint (struct +0xb4) into NVS via set_endpoint
(FUN_4200aaa8). That call is `call?8 0x4200aaa8` at VA 0x42005f06
(file offset 0x235f06 in the app image). NOPing it makes ENDPOINT_STR
fully user-controlled: registration still sets token / user_id /
REGISTER_STR=1, so the boot gate is satisfied, but the endpoint you put in
NVS is never clobbered.

This script:
  1. verifies the image (magic, chip, that the 3 target bytes are the expected
     call instruction),
  2. replaces the 3-byte call at 0x235f06 with a 3-byte Xtensa NOP (F0 20 00),
  3. recomputes the appended SHA256 (last 32 bytes) so the bootloader accepts it.

Usage:
    python3 patch_pin_endpoint.py fw_main.bin fw_main_patched.bin

Build/version checked against: olli_esp_patch_build_1.16_13_May_2026 (ESP32-S3).
If the target bytes differ, the offset must be re-resolved for your build
(find `call ... 0x4200aaa8` inside RegisterNotifySuccess).

FLASHING NOTES (read before you flash):
  * Flash the patched image to the ACTIVE OTA slot (fw_main.bin was dumped from
    flash 0x20000 -> typically ota_0). Make sure otadata boots that slot.
  * Secure boot is OFF on this device, so no signing is needed.
  * This assumes flash encryption is OFF (your readable dump indicates it is).
    If encryption were on, a raw byte patch would not work.
  * A future cloud OTA would overwrite this patch -- not a concern once the
    cloud is gone, but disable/blackhole OTA if you keep the cloud reachable.
"""
import sys, hashlib

CALL_FOFF   = 0x235f06            # file offset of `call ... 0x4200aaa8`
EXPECT      = bytes.fromhex("25ba04")   # the call instruction bytes (set_endpoint)
NOP3        = bytes.fromhex("f02000")   # Xtensa 3-byte NOP (encoding 0x0020f0)

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    data = bytearray(open(src, 'rb').read())

    if data[0] != 0xE9:
        sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    if data[23] & 1 == 0:
        print("WARN: image does not declare an appended SHA256; skipping rehash")
        rehash = False
    else:
        rehash = True

    got = bytes(data[CALL_FOFF:CALL_FOFF+3])
    if got != EXPECT:
        sys.exit(f"ERROR: bytes at 0x{CALL_FOFF:06x} are {got.hex()}, expected "
                 f"{EXPECT.hex()}. Wrong build/offset -- re-resolve the "
                 f"set_endpoint call inside RegisterNotifySuccess.")

    print(f"patch  @ 0x{CALL_FOFF:06x}: {got.hex()} -> {NOP3.hex()}  "
          f"(call set_endpoint -> nop)")
    data[CALL_FOFF:CALL_FOFF+3] = NOP3

    if rehash:
        old = bytes(data[-32:])
        new = hashlib.sha256(bytes(data[:-32])).digest()
        data[-32:] = new
        print(f"rehash : SHA256 {old.hex()[:16]}... -> {new.hex()[:16]}...")

    open(dst, 'wb').write(data)
    print(f"wrote  : {dst} ({len(data)} bytes)")
    print("done. Flash to the active OTA slot (see notes in this file's header).")

if __name__ == '__main__':
    main()
