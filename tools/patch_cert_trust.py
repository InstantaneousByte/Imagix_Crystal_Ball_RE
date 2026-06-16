#!/usr/bin/env python3
"""
!!! DISPROVEN ON HARDWARE 2026-06-16 -- DO NOT USE !!!
Neutering esp_crt_verify_callback to *flags=0; return 0 did NOT make the ORB accept certs.
It left authmode at VERIFY_REQUIRED, so mbedtls_ssl_handshake still enforced verification, and
overwriting the whole esp_crt_bundle callback corrupted its flag accounting -- a VALID cert then
failed (mbedtls -0x9984). Correct fix: tools/patch_authmode_none.py (authmode REQUIRED->NONE,
one byte at VA 0x4201fa6a). Kept for reference only.

patch_cert_trust.py  --  make the ORB accept ANY TLS server cert.

WHY
  The downchannel (`/connect`) is always TLS; `esp_crt_bundle_attach` installs a
  verify callback `esp_crt_verify_callback` @ VA 0x420c8d70 (registered via
  mbedtls_ssl_conf_verify). mbedTLS runs it DURING the handshake; on an untrusted
  cert it logs `esp-x509-crt-bundle: Failed to verify certificate` and returns
  0xffffd900, which aborts the handshake (observed as mbedtls_ssl_handshake
  -0x12288). That blocks any self-signed local endpoint and any MITM.

WHAT
  The callback's signature is (ctx, cert, depth, uint32_t *flags). Its own success
  path is literally `*flags = 0; return 0`. This patch makes the WHOLE callback do
  exactly that, unconditionally -> every cert is accepted (incl. self-signed,
  CN-mismatch, expired). TLS stays on; verification is defanged.

PATCH (3 Xtensa instructions, right after the windowed `entry`):
    movi.n a2, 0       ; 0c 02   a2 = 0
    s32i.n a2, a5, 0   ; 09 25   *flags = 0      (a5 = 4th arg = flags ptr)
    retw.n             ; 1d f0   return 0        -> "cert OK"
  Encodings lifted from the firmware itself (movi.n a2,0 / retw.n from
  FUN_420c8fd0; s32i.n a2,a5,0 = 0x2509 per the RRRN field layout).

Also fixes the image's 1-byte XOR checksum and appended SHA256 (same approach as
patch_pin_endpoint.py), so the image is valid for any boot/OTA path.

USAGE
  python3 patch_cert_trust.py <in_app.bin> <out_app.bin>
  (Run on the ota_0 app image, e.g. fw_main.bin. Safe to run AFTER
   patch_pin_endpoint.py on the same image -- different offset; checksum/SHA
   are re-fixed here. Flash result to the active OTA slot @ 0x20000.)
"""
import sys, struct, hashlib

ENTRY_FOFF = 0x2f8d70                       # esp_crt_verify_callback start (VA 0x420c8d70)
ENTRY_EXPECT = bytes.fromhex("362101")      # entry a1, 0x108  (sanity: right function)
PATCH_FOFF = 0x2f8d73                       # body, right after the 3-byte entry
BODY_EXPECT = bytes.fromhex("88059191dc52") # original first 6 body bytes
PATCH      = bytes.fromhex("0c0209251df0")  # movi.n a2,0 ; s32i.n a2,a5,0 ; retw.n

def image_layout(d):
    if d[0] != 0xE9:
        sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    off = 24
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8 + ln
    ck = off
    while ck % 16 != 15:
        ck += 1
    return ck, ck + 1, bool(d[23] & 1)      # checksum_pos, image_end, hash_appended

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, 'rb').read())

    ent = bytes(d[ENTRY_FOFF:ENTRY_FOFF+3])
    if ent != ENTRY_EXPECT:
        sys.exit(f"ERROR: entry @0x{ENTRY_FOFF:06x} is {ent.hex()}, expected "
                 f"{ENTRY_EXPECT.hex()} -- wrong build/offset, not patching.")
    got = bytes(d[PATCH_FOFF:PATCH_FOFF+len(PATCH)])
    if got == PATCH:
        sys.exit("Already patched (cert-trust). Nothing to do.")
    if got != BODY_EXPECT:
        sys.exit(f"ERROR: body @0x{PATCH_FOFF:06x} is {got.hex()}, expected "
                 f"{BODY_EXPECT.hex()} -- wrong build/offset, not patching.")

    ck_pos, img_end, hash_appended = image_layout(d)
    print(f"image: checksum @0x{ck_pos:06x}, image_end 0x{img_end:06x}, "
          f"hash_appended={hash_appended}, size 0x{len(d):x}")

    # 1) overwrite the callback body
    delta = 0
    for o, n in zip(got, PATCH):
        delta ^= o ^ n
    d[PATCH_FOFF:PATCH_FOFF+len(PATCH)] = PATCH
    print(f"patch  @0x{PATCH_FOFF:06x}: {got.hex()} -> {PATCH.hex()}  "
          f"(movi.n a2,0 ; s32i.n a2,a5,0 ; retw.n)")

    # 2) fix 1-byte XOR checksum (linear -> XOR in the delta)
    old_ck = d[ck_pos]; d[ck_pos] = old_ck ^ delta
    print(f"cksum  @0x{ck_pos:06x}: {old_ck:02x} -> {d[ck_pos]:02x}")

    # 3) recompute appended SHA256 over [0:img_end]
    if hash_appended and len(d) >= img_end + 32:
        new = hashlib.sha256(bytes(d[:img_end])).digest()
        d[img_end:img_end+32] = new
        print(f"rehash @0x{img_end:06x}: -> {new.hex()[:16]}...")

    open(dst, 'wb').write(d)
    print(f"wrote  : {dst} ({len(d)} bytes). Flash to the active OTA slot @0x20000.")

if __name__ == '__main__':
    main()
