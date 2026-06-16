#!/usr/bin/env python3
"""
patch_boot_audio.py - replace the EMBEDDED boot chimes (poweron.ogg / bootup.ogg).

WHY THIS IS NEEDED (don't waste time swapping SD files):
  poweron.ogg and bootup.ogg are NOT played from the SD card. They are compiled
  into the app image's read-only data segment (DROM) and played by `play_binary`
  via a fixed {id, start, end} table. Worse, the filename->id matcher uses a
  SUBSTRING (strstr) test, so even an SD path like
  `/sdcard/narrator/bootup.ogg` contains "bootup.ogg" -> matches -> plays the
  EMBEDDED copy. The only way to change the sound is to replace the embedded blob
  (this tool) or repoint the matcher (see roadmap "boot audio -> SD" option).

EMBEDDED AUDIO TABLE (verified in fw_main.bin, app dumped from ota_0 @0x20000):
  entry      id   start VA      end VA        file offset   slot (bytes)
  poweron    4    0x3c29326f    0x3c298e70    0x0c326f      23553
  bootup     5    0x3c286031    0x3c29326b    0x0b6031      53818
  (DROM file->VA: VA = file_offset + 0x3c1d0000)

CONSTRAINTS:
  * The replacement OGG must be <= the slot size (blobs are packed contiguously;
    growing one would overwrite the next). Encode small:
       ffmpeg -i in.wav -ac 1 -ar 48000 -c:a libvorbis -b:a 32k -t 11 out.ogg
    Stock bootup is ~53.8 KB / ~11 s mono. Check `ls -l out.ogg` <= 53818.
  * Smaller is fine: we zero-pad the slot and rewrite the end-pointer to the
    exact new length, so `play_binary: remaining N` will report the new size.

USAGE:
  python3 patch_boot_audio.py IN.bin OUT.bin [--bootup new_bootup.ogg] [--poweron new_poweron.ogg]
  (at least one of --bootup/--poweron required)

Then flash the app to ota_0:
  esptool.py --chip esp32s3 write_flash 0x20000 OUT.bin
"""
import sys, struct, hashlib, argparse

DROM_BASE = 0x3c1d0000
SLOTS = {
    #          file_off  start_va     end_ptr_foff  slot
    "poweron": (0x0c326f, 0x3c29326f, 0x223930,     23553),
    "bootup":  (0x0b6031, 0x3c286031, 0x22393c,     53818),
}

def image_layout(d):
    """Return (checksum_byte_offset, sha256_offset, hash_appended)."""
    if d[0] != 0xE9:
        sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    off = 24
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8 + ln
    ck = off
    while ck % 16 != 15:
        ck += 1
    return ck, ck + 1, bool(d[23] & 1)

def recompute_image_checksum(d):
    """Full ESP32 image checksum = 0xEF XOR all segment payload bytes."""
    off = 24; ck = 0xEF
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8
        for b in d[off:off+ln]:
            ck ^= b
        off += ln
    return ck & 0xff

def splice(d, name, ogg):
    foff, start_va, endp_foff, slot = SLOTS[name]
    # guard: confirm this build matches the table (start pointer present where we expect)
    cur_start = struct.unpack('<I', d[endp_foff-4:endp_foff])[0]
    if cur_start != start_va:
        sys.exit(f"ERROR [{name}]: start-ptr @0x{endp_foff-4:06x} is 0x{cur_start:08x}, "
                 f"expected 0x{start_va:08x}. Wrong build/offset -- not patching.")
    if ogg[:4] != b'OggS':
        sys.exit(f"ERROR [{name}]: replacement does not start with 'OggS' (not an Ogg file).")
    n = len(ogg)
    if n > slot:
        sys.exit(f"ERROR [{name}]: replacement is {n} bytes, slot is only {slot}. "
                 f"Re-encode smaller (lower bitrate / shorter). See header for ffmpeg.")
    # write the new blob, zero-pad the remainder of the slot
    d[foff:foff+n] = ogg
    if n < slot:
        d[foff+n:foff+slot] = b'\x00' * (slot - n)
    # rewrite end-pointer = start_va + n  (so remaining == n)
    new_end = start_va + n
    d[endp_foff:endp_foff+4] = struct.pack('<I', new_end)
    print(f"  [{name}] wrote {n} bytes @0x{foff:06x} (slot {slot}, "
          f"{'exact' if n==slot else f'padded {slot-n}'}); "
          f"end-ptr @0x{endp_foff:06x} -> 0x{new_end:08x} (remaining={n})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("dst")
    ap.add_argument("--bootup"); ap.add_argument("--poweron")
    a = ap.parse_args()
    if not (a.bootup or a.poweron):
        sys.exit("Nothing to do: pass --bootup and/or --poweron.")
    d = bytearray(open(a.src, 'rb').read())
    ck_pos, img_end, hashed = image_layout(d)
    print(f"image: checksum @0x{ck_pos:06x}, sha @0x{img_end:06x}, hash_appended={hashed}, size 0x{len(d):x}")
    if a.poweron: splice(d, "poweron", open(a.poweron, 'rb').read())
    if a.bootup:  splice(d, "bootup",  open(a.bootup, 'rb').read())
    # recompute checksum byte + appended SHA256 (full, since this is a large edit)
    d[ck_pos] = recompute_image_checksum(d)
    print(f"checksum @0x{ck_pos:06x} -> {d[ck_pos]:02x}")
    if hashed and len(d) >= img_end + 32:
        d[img_end:img_end+32] = hashlib.sha256(bytes(d[:img_end])).digest()
        print(f"sha256   @0x{img_end:06x} -> {d[img_end:img_end+8].hex()}...")
    open(a.dst, 'wb').write(d)
    print(f"wrote {a.dst}  ->  flash to ota_0:  esptool.py --chip esp32s3 write_flash 0x20000 {a.dst}")

if __name__ == "__main__":
    main()
