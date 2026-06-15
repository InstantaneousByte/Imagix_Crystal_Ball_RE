#!/usr/bin/env python3
"""
patch_pin_endpoint.py - stop the firmware from overwriting ENDPOINT_STR.

The endpoint has exactly one writer: RegisterNotifySuccess (FUN_42005ec4),
which on every successful cloud registration commits the server-supplied
endpoint (response field +0xb4) into NVS via set_endpoint (FUN_4200aaa8). That
call is at VA 0x42005f06 (file offset 0x235f06, bytes `25 ba 04`). NOPing it
makes ENDPOINT_STR fully user-controlled: token / user_id / REGISTER_STR=1 are
still written, so the boot gate is satisfied, but your NVS endpoint is never
clobbered.

WHAT THIS DOES
  1. verify image + that the 3 target bytes are the expected call,
  2. replace the call with a 3-byte Xtensa NOP (F0 20 00),
  3. parse the image to find the REAL 1-byte XOR checksum and (if present) the
     appended SHA256, and fix both so the image is fully valid.

NOTE ON VALIDATION (important): on this device the bootloader skips image
validation on normal power-on -- a raw splice that fixed neither checksum nor
hash was observed to boot. So for a direct esptool/JTAG flash the NOP alone is
enough; steps (3) only matter if the image ever goes through a validated (OTA)
path. They are done anyway so the output is a clean, valid image.

IMPORTANT: fw_main.bin is a full partition dump (~4 MB). The actual app image is
much smaller; the checksum/hash live at the END OF THE IMAGE, not the end of the
dump. This script locates them by parsing the segment table -- do not assume
fixed offsets from the file end.

Usage:
    python3 patch_pin_endpoint.py fw_main.bin fw_main_patched.bin

Checked against olli_esp_patch_build_1.16_13_May_2026 (ESP32-S3). If the target
bytes differ, re-resolve the set_endpoint call inside RegisterNotifySuccess.

FLASHING: write to the active OTA slot (dump came from flash 0x20000 ~ ota_0).
Secure boot is OFF (no signing); assumes flash encryption is OFF (readable dump
indicates it is). A future cloud OTA would revert the patch (moot once decloud'd).
"""
import sys, struct, hashlib

CALL_FOFF = 0x235f06
EXPECT    = bytes.fromhex("25ba04")   # call set_endpoint (FUN_4200aaa8)
NOP3      = bytes.fromhex("f02000")   # Xtensa 3-byte NOP (0x0020f0)

def image_layout(d):
    """Return (checksum_pos, image_end, hash_appended) by parsing the segments."""
    if d[0] != 0xE9:
        sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    seg_cnt = d[1]
    off = 24
    for _ in range(seg_cnt):
        _la, ln = struct.unpack('<II', d[off:off+8])
        off += 8 + ln
    ck = off
    while ck % 16 != 15:
        ck += 1
    img_end = ck + 1                      # 16-aligned; SHA256 (if any) follows
    hash_appended = bool(d[23] & 1)
    return ck, img_end, hash_appended

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, 'rb').read())

    got = bytes(d[CALL_FOFF:CALL_FOFF+3])
    if got != EXPECT:
        sys.exit(f"ERROR: bytes at 0x{CALL_FOFF:06x} are {got.hex()}, expected "
                 f"{EXPECT.hex()} -- wrong build/offset.")

    ck_pos, img_end, hash_appended = image_layout(d)
    print(f"image: checksum byte @0x{ck_pos:06x}, image_end 0x{img_end:06x}, "
          f"hash_appended={hash_appended}, dump size 0x{len(d):x}")

    # 1) NOP the call
    delta = 0
    for o, n in zip(got, NOP3):
        delta ^= o ^ n
    d[CALL_FOFF:CALL_FOFF+3] = NOP3
    print(f"patch  @ 0x{CALL_FOFF:06x}: {got.hex()} -> {NOP3.hex()}")

    # 2) fix the 1-byte XOR checksum (linear, so just XOR in the delta)
    old_ck = d[ck_pos]
    d[ck_pos] = old_ck ^ delta
    print(f"cksum  @ 0x{ck_pos:06x}: {old_ck:02x} -> {d[ck_pos]:02x}")

    # 3) recompute appended SHA256 over [0:img_end] (covers the checksum byte)
    if hash_appended and len(d) >= img_end + 32:
        old = bytes(d[img_end:img_end+32])
        new = hashlib.sha256(bytes(d[:img_end])).digest()
        d[img_end:img_end+32] = new
        print(f"rehash @ 0x{img_end:06x}: {old.hex()[:16]}... -> {new.hex()[:16]}...")

    open(dst, 'wb').write(d)
    print(f"wrote  : {dst} ({len(d)} bytes). Flash to the active OTA slot.")

if __name__ == '__main__':
    main()
