#!/usr/bin/env python3
"""
nvs_parse.py - parse an ESP-IDF NVS partition dump (v2 format).

Prints the current *live* config (latest written entry per key) and, for any key
named on the command line with --history, its full write history across the
log-structured partition.

Usage:
    python3 nvs_parse.py nvs_readback.bin
    python3 nvs_parse.py nvs_readback.bin --history ENDPOINT_STR
    python3 nvs_parse.py nvs_readback.bin --raw          # show secrets in full

By default TOKEN_STR / PROFILE_STR / any obvious JWT value is REDACTED, because a
real device dump contains a live access token. Use --raw to override (careful).
"""
import sys, struct

PAGE = 4096
ENTRY = 32
TYPES = {0x01:'U8',0x11:'I8',0x02:'U16',0x12:'I16',0x04:'U32',0x14:'I32',
         0x08:'U64',0x18:'I64',0x21:'STR',0x41:'BLOB_DATA',0x42:'BLOB',0x48:'BLOB_IDX'}
SECRET_KEYS = {'TOKEN_STR', 'PROFILE_STR'}

def redact(key, val, raw):
    if raw:
        return val
    if key in SECRET_KEYS or (isinstance(val, str) and val.startswith('eyJ') and val.count('.') == 2):
        return f'<redacted {len(val) if isinstance(val,str) else 0} chars>'
    return val

def entry_state(data, pg, i):
    bm = data[pg*PAGE+32:pg*PAGE+64]
    return (bm[i//4] >> ((i % 4) * 2)) & 0x3   # 3=empty 2=written 0=erased

def parse(path):
    data = open(path, 'rb').read()
    npages = len(data) // PAGE
    ns = {}            # idx -> name
    live = {}          # (ns_idx, key) -> (seq, pos, value)
    hist = {}          # key -> [(seq,pos,state,value)]
    for pg in range(npages):
        state = struct.unpack('<I', data[pg*PAGE:pg*PAGE+4])[0]
        seq = struct.unpack('<I', data[pg*PAGE+4:pg*PAGE+8])[0]
        if state == 0xFFFFFFFF:
            continue
        i = 0
        while i < 126:
            off = pg*PAGE + 64 + i*ENTRY
            e = data[off:off+ENTRY]
            nsi, typ, span = e[0], e[1], e[2]
            key = e[8:24].split(b'\x00')[0]
            if typ not in TYPES or span == 0 or span > 32 or (key and not all(32 <= c < 127 for c in key)):
                i += 1
                continue
            keys = key.decode('ascii', 'replace')
            tname = TYPES[typ]
            if tname == 'STR':
                size = struct.unpack('<H', e[24:26])[0]
                val = data[off+ENTRY:off+ENTRY+size].split(b'\x00')[0].decode('ascii', 'replace')
            else:
                val = int.from_bytes(e[24:32], 'little')
                if nsi == 0:
                    ns[e[24]] = keys
            st = entry_state(data, pg, i)
            hist.setdefault(keys, []).append((seq, i, st, val))
            if nsi != 0 and st == 0x2:
                k = (nsi, keys)
                cur = live.get(k)
                if cur is None or (seq, i) > (cur[0], cur[1]):
                    live[k] = (seq, i, val)
            i += max(span, 1)
    return ns, live, hist

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]
    raw = '--raw' in sys.argv
    ns, live, hist = parse(path)
    print('namespaces:', ns)
    print('\n=== current live config ===')
    for (nsi, k), (s, p, v) in sorted(live.items(), key=lambda x: x[0][1]):
        print(f'  [{ns.get(nsi, nsi)}] {k:14} = {redact(k, v, raw)!r}')
    if '--history' in sys.argv:
        key = sys.argv[sys.argv.index('--history') + 1]
        print(f'\n=== history for {key} (seq,pos,state[3=empty 2=live 0=erased]) ===')
        for s, p, st, v in hist.get(key, []):
            print(f'  seq={s} pos={p} state={st} -> {redact(key, v, raw)!r}')

if __name__ == '__main__':
    main()
