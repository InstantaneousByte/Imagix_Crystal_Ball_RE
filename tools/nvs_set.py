#!/usr/bin/env python3
"""
nvs_set.py -- set a string (STR) key in an ESP-IDF NVS partition dump.

Mutates the partition the same way the firmware does: writes a NEW entry for the
key into the active page's free space and marks the OLD entry erased (NVS is
log-structured; latest live entry wins). All other entries -- token, user id,
profile, etc. -- are left untouched. CRCs are recomputed and validated.

Primary use: point the ORB at your local server.
    python3 nvs_set.py nvs_clean.bin nvs_out.bin ENDPOINT_STR https://192.168.8.245:9000
Then flash:
    esptool.py write_flash 0x9000 nvs_out.bin

Only STR keys are supported (that's all we need). Round-trip verify with:
    python3 nvs_parse.py nvs_out.bin
"""
import sys, struct, zlib, math

PAGE, ENTRY = 4096, 32
TYPE_STR = 0x21
ST_ACTIVE = 0xFFFFFFFE

def crc_entry(e):                 # CRC over bytes [0:4] + [8:32]
    return zlib.crc32(e[8:32], zlib.crc32(e[0:4], 0xffffffff)) & 0xffffffff
def crc_data(b):
    return zlib.crc32(b, 0xffffffff) & 0xffffffff

def get_state(d, pg, i):
    return (d[pg*PAGE+32 + i//4] >> ((i % 4) * 2)) & 3
def set_state(d, pg, i, val):     # 2=written, 0=erased
    o = pg*PAGE+32 + i//4; sh = (i % 4) * 2
    d[o] = (d[o] & ~(3 << sh)) | (val << sh)

def main():
    if len(sys.argv) != 5:
        print(__doc__); sys.exit(1)
    src, dst, key, value = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].encode()
    keyb = key.encode()
    if len(keyb) > 15: sys.exit("ERROR: key name > 15 chars")
    d = bytearray(open(src, 'rb').read())
    npages = len(d) // PAGE

    # locate the live entry for `key` and the active page
    live = None                    # (pg, slot, span)
    active = None
    for pg in range(npages):
        st = struct.unpack('<I', d[pg*PAGE:pg*PAGE+4])[0]
        if st == 0xFFFFFFFF: continue
        if st == ST_ACTIVE: active = pg
        i = 0
        while i < 126:
            off = pg*PAGE+64+i*ENTRY
            nsi, typ, span = d[off], d[off+1], d[off+2]
            sp = span if 0 < span <= 32 else 1
            ek = bytes(d[off+8:off+24]).split(b'\x00')[0]
            if get_state(d, pg, i) == 2 and typ == TYPE_STR and ek == keyb:
                live = (pg, i, sp)
            i += sp
    if live is None:
        sys.exit(f"ERROR: '{key}' not found as a live STR entry")
    lpg, lslot, lspan = live
    nsi = d[lpg*PAGE+64+lslot*ENTRY]                 # namespace index of the key
    chunk = d[lpg*PAGE+64+lslot*ENTRY+3]             # chunk index byte (0xFF for STR)
    reserved = bytes(d[lpg*PAGE+64+lslot*ENTRY+26:lpg*PAGE+64+lslot*ENTRY+28])
    print(f"live '{key}': page {lpg} slot {lslot} span {lspan} (ns idx {nsi})")

    if active is None:
        sys.exit("ERROR: no ACTIVE page found (0xFFFFFFFE)")

    # build the new entry: header + data
    vdata = value + b'\x00'
    size = len(vdata)
    ndata = math.ceil(size / ENTRY)
    span = 1 + ndata
    hdr = bytearray(ENTRY)
    hdr[0] = nsi; hdr[1] = TYPE_STR; hdr[2] = span; hdr[3] = chunk
    hdr[8:8+len(keyb)] = keyb                         # key (rest stays 0x00)
    struct.pack_into('<H', hdr, 24, size)             # size incl. null
    hdr[26:28] = reserved
    struct.pack_into('<I', hdr, 28, crc_data(vdata))  # data CRC
    struct.pack_into('<I', hdr, 4, crc_entry(hdr))    # entry CRC
    payload = vdata + b'\xff' * (ndata*ENTRY - size)  # data entries, 0xFF padded
    print(f"new entry: size {size} span {span} ({ndata} data entries)")

    # find `span` consecutive EMPTY slots (state 3 + bytes 0xFF) on the active page
    def empty(pg, i):
        off = pg*PAGE+64+i*ENTRY
        return get_state(d, pg, i) == 3 and d[off:off+ENTRY] == b'\xff'*ENTRY
    start = None
    for i in range(0, 126 - span + 1):
        if all(empty(active, i+k) for k in range(span)):
            start = i; break
    if start is None:
        sys.exit("ERROR: no room on the active page for the new entry "
                 "(would need a fresh page -- tell me and I'll extend the tool)")
    print(f"writing into active page {active} at slot {start}")

    # write header + data, then flip bitmap states (write new, erase old)
    base = active*PAGE+64+start*ENTRY
    d[base:base+ENTRY] = hdr
    d[base+ENTRY:base+ENTRY+len(payload)] = payload
    for k in range(span):
        set_state(d, active, start+k, 2)             # written
    for k in range(lspan):
        set_state(d, lpg, lslot+k, 0)                # erase old

    open(dst, 'wb').write(d)
    print(f"wrote {dst} ({len(d)} bytes). Flash: esptool.py write_flash 0x9000 {dst}")

if __name__ == '__main__':
    main()
