#!/usr/bin/env python3
"""
splice_patch.py — splice cmd1 patch blob into fw_main.bin and redirect cmd1's entry.

Usage:
    python3 splice_patch.py fw_main.bin cmd1_patch.bin THUNK_FO CMD1_FO THUNK_VA CMD1_OFFSET

Arguments:
    fw_main.bin      original OLLI firmware image
    cmd1_patch.bin   blob from extract_cmd1.py (fan_connect + cmd1_handler)
    THUNK_FO         file offset to place the blob (from find_padding.py)
    CMD1_FO          file offset of cmd1 stub entry (0x273808 for this firmware)
    THUNK_VA         virtual address of the blob placement
    CMD1_OFFSET      byte offset of cmd1_handler within the blob (from extract_cmd1.py)

Produces: fw_patched.bin — ready to flash with:
    esptool.py --port /dev/ttyUSB0 --baud 921600 write_flash 0x20000 fw_patched.bin
"""
import sys, struct

def make_call8(from_va, to_va):
    """3-byte Xtensa CALL8 instruction."""
    offset = (to_va >> 2) - ((from_va >> 2) + 1)
    offset &= 0x3FFFF
    word = (offset << 6) | (2 << 4) | 0x5
    return struct.pack('<I', word)[:3]

def make_j(from_va, to_va):
    """3-byte Xtensa J (unconditional jump)."""
    offset = to_va - (from_va + 4)
    offset &= 0x3FFFF
    word = (offset << 6) | 0x6
    return struct.pack('<I', word)[:3]

if len(sys.argv) < 7:
    print(__doc__)
    sys.exit(1)

fw_path      = sys.argv[1]
patch_path   = sys.argv[2]
thunk_fo     = int(sys.argv[3], 0)
cmd1_fo      = int(sys.argv[4], 0)
thunk_va     = int(sys.argv[5], 0)
cmd1_offset  = int(sys.argv[6], 0)   # offset of cmd1_handler within the blob

fw    = bytearray(open(fw_path, 'rb').read())
patch = open(patch_path, 'rb').read()

# Virtual address of cmd1_handler within the placed blob
cmd1_handler_va = thunk_va + cmd1_offset

print(f"firmware:         {fw_path} ({len(fw)} bytes)")
print(f"patch blob:       {patch_path} ({len(patch)} bytes)")
print(f"thunk placement:  file {thunk_fo:#x}  vaddr {thunk_va:#x}")
print(f"cmd1 entry:       file {cmd1_fo:#x}")
print(f"cmd1_handler va:  {cmd1_handler_va:#x}  (blob+{cmd1_offset:#x})")

# Extend image if needed (partition headroom case)
if thunk_fo + len(patch) > len(fw):
    needed = thunk_fo + len(patch)
    fw.extend(b'\xff' * (needed - len(fw)))
    print(f"extended image to {len(fw)} bytes")

# Check destination
dest = fw[thunk_fo:thunk_fo+len(patch)]
if all(b == 0xFF for b in dest):
    print(f"destination is 0xFF padding ✓")
elif all(b == 0x00 for b in dest):
    print(f"destination is zero-filled stub space ✓")
else:
    print(f"destination contains existing code: {dest[:16].hex()} — overwriting stub")

# 1. Splice the blob
fw[thunk_fo:thunk_fo+len(patch)] = patch
print(f"spliced {len(patch)} bytes at file offset {thunk_fo:#x}")

# 2. Redirect cmd1 stub entry -> J (tail-jump) to cmd1_handler_va.
# We overwrite the stub's first instruction (its `entry`). A J makes cmd1_handler
# BECOME the handler: it runs its own `entry` in the window the console allocated,
# reads argc/argv from a2/a3, and retw's straight back to the console. A CALL8 here
# would be wrong — it neither forwards the console's a2/a3 args nor returns cleanly
# (retw would land in the clobbered stub body), so we never use it for entry-replace.
SEG3_FO = 0x230020
SEG3_VA = 0x42000020
cmd1_va = SEG3_VA + (cmd1_fo - SEG3_FO)

j_offset = cmd1_handler_va - (cmd1_va + 4)
if not (-0x20000 <= j_offset < 0x20000):       # J is an 18-bit signed byte offset (+/-128KB)
    sys.exit(f"ERROR: target {cmd1_handler_va:#x} is {j_offset:#x} from stub — out of J range; "
             f"place the blob within +/-128KB of {cmd1_va:#x} or use a literal+jx trampoline")
jump = make_j(cmd1_va, cmd1_handler_va)
print(f"J: {cmd1_va:#x} -> {cmd1_handler_va:#x}  ({jump.hex()})  [entry-replace tail-jump]")

fw[cmd1_fo:cmd1_fo+3] = jump
print(f"patched cmd1 entry at {cmd1_fo:#x}")

out = fw_path.replace('.bin', '_patched.bin')
open(out, 'wb').write(fw)
print(f"\n=== output: {out} ({len(fw)} bytes) ===")
print(f"Flash with:")
print(f"  esptool.py --port /dev/ttyUSB0 --baud 921600 write_flash 0x20000 {out}")
print(f"\nTest at serial console:")
print(f"  cmd1")
print(f"  (expected: fan_sync_binary_file pushes /sdcard/test/test_idle.bin -> eb_idle_eb1_64.bin)")
