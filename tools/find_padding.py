#!/usr/bin/env python3
"""
find_padding.py — locate free 0xFF padding in seg3 of the orb main board firmware.
The patch thunk and string literals go here.

Usage: python3 find_padding.py fw_main.bin [patch_size_bytes]
"""
import sys

FW = sys.argv[1] if len(sys.argv) > 1 else 'fw_main.bin'
PATCH_SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 512  # pessimistic upper bound

# seg3: IROM flash-text segment (confirmed from image header parse)
SEG3_FO  = 0x230020   # file offset
SEG3_LEN = 0x1cbbe0   # 1,883,104 bytes
SEG3_VA  = 0x42000020 # virtual load address (R-X, Xtensa executes from here)

d = open(FW, 'rb').read()
seg = d[SEG3_FO : SEG3_FO + SEG3_LEN]

# find the last non-FF byte — everything after is free padding
last_nonff = max((i for i in range(len(seg)) if seg[i] != 0xFF), default=0)
pad_start_in_seg = last_nonff + 1
pad_len = len(seg) - pad_start_in_seg

fo_pad = SEG3_FO + pad_start_in_seg
va_pad = SEG3_VA + pad_start_in_seg

print(f"=== seg3 padding analysis: {FW} ===")
print(f"  seg3 file range  : {SEG3_FO:#x} – {SEG3_FO+SEG3_LEN:#x}")
print(f"  seg3 virt range  : {SEG3_VA:#x} – {SEG3_VA+SEG3_LEN:#x}")
print(f"  last non-FF byte : seg offset {last_nonff:#x}  (file {SEG3_FO+last_nonff:#x})")
print(f"")
print(f"  FREE PADDING START:")
print(f"    file offset  : {fo_pad:#x}")
print(f"    virt address : {va_pad:#x}  ← thunk goes here")
print(f"    available    : {pad_len} bytes ({pad_len//1024} KB)")
print(f"")
if pad_len >= PATCH_SIZE:
    print(f"  FITS: {PATCH_SIZE} byte patch leaves {pad_len - PATCH_SIZE} bytes spare")
else:
    print(f"  WARNING: {PATCH_SIZE} byte patch > {pad_len} available — need a different approach")

print(f"")
print(f"=== cmd1 stub entry to patch ===")
# cmd1 handler stub @ 0x42043808 — the CALL8 or J instruction to redirect to va_pad
CMD1_VA = 0x42043808
CMD1_FO = SEG3_FO + (CMD1_VA - SEG3_VA)
print(f"  cmd1 stub virt   : {CMD1_VA:#x}")
print(f"  cmd1 stub fileoff: {CMD1_FO:#x}")
print(f"  current bytes    : {d[CMD1_FO:CMD1_FO+12].hex()}")
print(f"")
print(f"  Patch: overwrite cmd1's body to CALL8 -> {va_pad:#x}")
print(f"  Or: overwrite the L32R literal that cmd1 loads to point -> {va_pad:#x}")
print(f"")
print(f"=== splice command (after compiling cmd1_patch.c) ===")
print(f"  # 1. compile:  see cmd1_patch.c build instructions")
print(f"  # 2. splice:   python3 splice_patch.py fw_main.bin cmd1_patch.bin {fo_pad:#x} {CMD1_FO:#x} {va_pad:#x}")
print(f"  # 3. flash:    esptool.py write_flash 0x20000 fw_patched.bin")
