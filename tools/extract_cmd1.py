#!/usr/bin/env python3
"""
extract_cmd1.py — extract a self-contained, position-independent cmd1 blob.

RELOCATE approach: we don't rely on the compiler co-locating literals (it merges
common constants into a far shared pool). We take the two functions' CODE verbatim
(call8 + any in-region literals preserved), then rebuild a fresh literal pool at the
FRONT of the blob and rewrite the out-of-region l32r offsets to point at it. Result:
blob = [pool][fan_connect][gap lits][cmd1_handler], fully position-independent.

We disassemble ONLY the code byte-ranges (from nm sizes), never literal data, so
interspersed pools (the inter-function gap) are preserved as-is, not mis-decoded.

Xtensa l32r: literal addr = ((PC+3) & ~3) + signext16(imm16)*4   (only reaches backward).
Run AFTER `idf.py build`.
"""
import subprocess, struct, sys, os

ELF, OBJCOPY, NM, READELF = 'build/cmd1_stub.elf', \
    'xtensa-esp-elf-objcopy', 'xtensa-esp-elf-nm', 'xtensa-esp-elf-readelf'
SECTION = '.flash.text'
if not os.path.exists(ELF): sys.exit(f"ERROR: {ELF} not found — run `idf.py build` first")

# --- symbols ---
syms = {}
for ln in subprocess.check_output([NM,'--defined-only','--print-size','--radix=x',ELF]).decode().splitlines():
    p = ln.split()
    if len(p) >= 4 and p[3] in ('fan_connect','cmd1_handler'): syms[p[3]] = (int(p[0],16), int(p[1],16))
for k in ('fan_connect','cmd1_handler'):
    if k not in syms: sys.exit(f"ERROR: {k} not found (inlined? add __attribute__((noinline)))")
fc, fcs = syms['fan_connect']; ch, chs = syms['cmd1_handler']
print(f"fan_connect:   {fc:#010x}  {fcs} bytes\ncmd1_handler:  {ch:#010x}  {chs} bytes")
if ch < fc: sys.exit("ERROR: expected fan_connect before cmd1_handler")

# --- .flash.text image + VA ---
subprocess.run([OBJCOPY, f'--only-section={SECTION}','-O','binary',ELF,'_ft.bin'], check=True)
ft = open('_ft.bin','rb').read(); text_va = None
for ln in subprocess.check_output([READELF,'-S',ELF]).decode().splitlines():
    if SECTION in ln:
        pr = ln.split()
        for i,t in enumerate(pr):
            if t == SECTION and i+3 < len(pr):
                try: text_va = int(pr[i+2],16); break
                except ValueError: pass
        if text_va: break
if text_va is None: sys.exit(f"ERROR: {SECTION} VA not found")
def word(va): return struct.unpack('<I', ft[va-text_va:va-text_va+4])[0]

region_start, region_end = fc, ch + chs
main = bytearray(ft[region_start-text_va : region_end-text_va])
# code sub-ranges (offsets into main); the [fcs, ch-fc) gap is cmd1_handler's literal data
code_ranges = [(0, fcs), (ch - fc, ch - fc + chs)]
print(f"code region:   {region_start:#010x}–{region_end:#010x} ({len(main)} B); "
      f"gap (lits) {fcs}..{ch-fc}")

def ilen(b0): return 2 if 0x8 <= (b0 & 0xF) <= 0xD else 3
def signw(imm16): return imm16 - 0x10000 if imm16 >= 0x8000 else imm16
def l32r_dst(pc, b1, b2): return ((pc + 3) & ~3) + (signw(b1 | (b2 << 8)) << 2)

def walk(buf, ranges):
    for lo, hi in ranges:
        i = lo
        while i < hi:
            n = ilen(buf[i])
            if n == 3 and (buf[i] & 0xF) == 1: yield i
            i += n

# --- find external l32r (literal outside the code region), build pool ---
externals, pool_values = [], []
for off in walk(main, code_ranges):
    tgt = l32r_dst(region_start + off, main[off+1], main[off+2]) & 0xffffffff
    if not (region_start <= tgt < region_end):
        if not (text_va <= tgt < text_va + len(ft)):
            sys.exit(f"ERROR: l32r @ {region_start+off:#x} targets {tgt:#x} outside {SECTION}")
        v = word(tgt)
        if v not in pool_values: pool_values.append(v)
        externals.append((off, v))
pool_size = len(pool_values) * 4
print(f"external l32r:  {len(externals)} refs -> {len(pool_values)} unique ({pool_size} B pool)")

# --- assemble [pool][code] and rewrite external l32r to hit the pool ---
blob = bytearray(b''.join(struct.pack('<I', v) for v in pool_values)) + main
for off, v in externals:
    io = pool_size + off; to = pool_values.index(v) * 4
    imm = ((to - ((io + 3) & ~3)) >> 2) & 0xFFFF      # backward ref
    blob[io+1] = imm & 0xFF; blob[io+2] = (imm >> 8) & 0xFF
cmd1_offset = pool_size + (ch - fc)

# --- self-check: walk ONLY the (shifted) code ranges; every l32r must resolve in-blob ---
blob_ranges = [(pool_size + lo, pool_size + hi) for lo, hi in code_ranges]
bad = []
for off in walk(blob, blob_ranges):
    t = l32r_dst(off, blob[off+1], blob[off+2])
    if not (0 <= t < len(blob)): bad.append((off, t))
if bad:
    print("\n*** SELF-CHECK FAILED — DO NOT FLASH ***")
    for o, t in bad: print(f"  l32r @ blob+{o:#x} -> {t:#x} outside blob")
    sys.exit(1)
for off, v in externals:                              # external refs read back the right value
    io = pool_size + off; t = l32r_dst(io, blob[io+1], blob[io+2])
    got = struct.unpack('<I', blob[t:t+4])[0]
    assert got == v, f"pool mismatch @ blob+{io:#x}: {got:#x} != {v:#x}"

open('cmd1_patch.bin','wb').write(blob); os.remove('_ft.bin')
print(f"self-contained ✓  blob = {len(blob)} bytes (pool {pool_size} + code {len(main)})")
print(f"cmd1_handler offset in blob: {cmd1_offset} ({cmd1_offset:#x})")
if len(blob) > 256: print(f"NOTE: {len(blob)} B — confirm placement budget (cmd2 stub = 256 B)")
print(f"pool: {', '.join(hex(v) for v in pool_values)}")
print(f"first 16 bytes: {blob[:16].hex()}")
print("\n=== SPLICE ===")
print(f"python3 tools/splice_patch.py fw_main.bin cmd1_patch.bin \\")
print(f"    0x273c40 0x273808 0x42043c40 {cmd1_offset}")
print("then: esptool.py --port /dev/ttyUSB0 --baud 921600 write_flash 0x20000 fw_main_patched.bin")
