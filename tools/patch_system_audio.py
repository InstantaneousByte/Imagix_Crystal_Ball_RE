#!/usr/bin/env python3
"""
patch_system_audio.py - replace ANY of the 26 EMBEDDED system/UI sounds.

The orb keeps its whole system-sound set (boot chimes, status prompts, error
voices, SFX) compiled into the app image's DROM segment, played by `play_binary`
from a fixed table of {id, start_va, end_va} entries. A filename->id matcher uses
a SUBSTRING (strstr) test, so any play request whose name merely *contains* a
known clip name is routed to the embedded blob -- the SD card is never consulted
for these. `play_binary` has NO SD fallback (id>=0x1a just returns an error), so
swapping SD files cannot change them. The only way to change a system sound is to
replace its embedded blob (this tool).

TABLE: base file offset 0x2238f8, entries = {u32 id, u32 start_va, u32 end_va},
       12 bytes each, ids 0..25. DROM file->VA = +0x3c1d0000.

CONSTRAINT: a replacement must be <= the target slot's size (blobs are packed
contiguously; you cannot grow one without clobbering its neighbour). We zero-pad
the remainder and rewrite the end-pointer so `play_binary: remaining N` reports
the new length. Encode small/mono, e.g.:
    ffmpeg -i in.wav -ac 1 -ar 48000 -c:a libvorbis -b:a 32k out.ogg

TIP - REPURPOSE THE BIG UNUSED SLOTS: on a de-clouded single-persona unit you
likely never trigger id 0 (character_selection_narrator_voice, ~315 KB) or id 10
(setup_device, ~381 KB). Point a long custom clip at one of those for lots of room:
    --set setup_device=my_90s_track.ogg

USAGE:
    python3 patch_system_audio.py IN.bin OUT.bin --set NAME=FILE [--set NAME=FILE ...]
    python3 patch_system_audio.py IN.bin --list      # print the manifest + slot sizes
  NAME may be given with or without the .ogg suffix (e.g. "bootup" or "bootup.ogg").

Run it on your already-patched app (e.g. app_final.bin) so de-cloud patches stay;
this tool only touches DROM audio + the image checksum/SHA. Then:
    esptool.py --chip esp32s3 write_flash 0x20000 OUT.bin
"""
import sys, struct, hashlib, argparse

DROM_BASE  = 0x3c1d0000
TABLE_BASE = 0x2238f8          # file offset of entry[0]
ENTRY      = 12
NCLIPS     = 26

NAMES = {0:"character_selection_narrator_voice",1:"connected_narrator_voice",2:"ap_on",
3:"cheer_full",4:"poweron",5:"bootup",6:"wifi_connected_done",7:"ap_off",8:"cancel_factory_reset",
9:"confirm_factory_reset",10:"setup_device",11:"not_ready",12:"insert_sdcard",
13:"change_character_ember",14:"change_character_ellie",15:"connect_inet_error",
16:"connect_server_error",17:"loss_wifi",18:"error_wifi",19:"setup_with_wifi",20:"setup_with_ble",
21:"energy_up",22:"silent",23:"wake_up",24:"confirm_standby",25:"update_in_progress"}
ID = {v:k for k,v in NAMES.items()}

def entry(d, i):
    idv,sv,ev = struct.unpack('<III', d[TABLE_BASE+i*ENTRY:TABLE_BASE+i*ENTRY+ENTRY])
    return idv, sv, ev

def image_layout(d):
    if d[0] != 0xE9: sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    off = 24
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8 + ln
    ck = off
    while ck % 16 != 15: ck += 1
    return ck, ck + 1, bool(d[23] & 1)

def recompute_checksum(d):
    off = 24; ck = 0xEF
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8
        for b in d[off:off+ln]: ck ^= b
        off += ln
    return ck & 0xff

def do_list(d):
    print(" id  slot(B)   file_off   name")
    for i in range(NCLIPS):
        idv,sv,ev = entry(d,i)
        print(f" {i:2d}  {ev-sv:7d}  0x{sv-DROM_BASE:06x}   {NAMES[i]}"
              f"{'   <== big, repurpose-able' if ev-sv>200000 else ''}")

def resolve(name):
    n = name[:-4] if name.lower().endswith(".ogg") else name
    if n not in ID: sys.exit(f"ERROR: unknown clip '{name}'. Use --list to see the 26 names.")
    return ID[n], n

def splice(d, i, name, ogg):
    idv,sv,ev = entry(d,i)
    if idv != i: sys.exit(f"ERROR [{name}]: table entry {i} id={idv}, expected {i}. Wrong build/offset.")
    slot = ev - sv; fo = sv - DROM_BASE
    if ogg[:4] != b'OggS': sys.exit(f"ERROR [{name}]: not an Ogg file (no 'OggS' magic).")
    n = len(ogg)
    if n > slot: sys.exit(f"ERROR [{name}]: {n} bytes > slot {slot}. Re-encode smaller (mono/lower bitrate).")
    d[fo:fo+n] = ogg
    if n < slot: d[fo+n:fo+slot] = b'\x00'*(slot-n)
    new_end = sv + n
    d[TABLE_BASE+i*ENTRY+8:TABLE_BASE+i*ENTRY+12] = struct.pack('<I', new_end)
    print(f"  [{name:34s}] id {i:2d}: wrote {n} bytes @0x{fo:06x} "
          f"(slot {slot}, {'exact' if n==slot else f'padded {slot-n}'}); remaining={n}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst", nargs="?")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=FILE")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    d = bytearray(open(a.src, 'rb').read())
    if a.list or not a.set:
        do_list(d); return
    if not a.dst: sys.exit("ERROR: need OUT.bin when using --set.")
    ck_pos, img_end, hashed = image_layout(d)
    print(f"image: checksum @0x{ck_pos:06x}, sha @0x{img_end:06x}, hash_appended={hashed}, size 0x{len(d):x}")
    for spec in a.set:
        if "=" not in spec: sys.exit(f"ERROR: --set expects NAME=FILE, got '{spec}'")
        name, path = spec.split("=", 1)
        i, n = resolve(name)
        splice(d, i, n, open(path, 'rb').read())
    d[ck_pos] = recompute_checksum(d)
    print(f"checksum @0x{ck_pos:06x} -> {d[ck_pos]:02x}")
    if hashed and len(d) >= img_end + 32:
        d[img_end:img_end+32] = hashlib.sha256(bytes(d[:img_end])).digest()
        print(f"sha256   @0x{img_end:06x} -> {d[img_end:img_end+8].hex()}...")
    open(a.dst, 'wb').write(d)
    print(f"wrote {a.dst}  ->  esptool.py --chip esp32s3 write_flash 0x20000 {a.dst}")

if __name__ == "__main__":
    main()
