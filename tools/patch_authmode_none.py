#!/usr/bin/env python3
"""
patch_authmode_none.py -- make the ORB accept ANY TLS server cert, permanently.

WHY (verified against olli_esp_patch_build_1.16, fw_main MD5 c281edec...):
  The downchannel TLS is set up in open_ssl_connection (FUN_4201f8f0). The ONLY
  thing gating a bad cert is mbedtls_ssl_handshake running under authmode
  VERIFY_REQUIRED -- on an untrusted cert the handshake aborts internally
  (-0x12288) before the code's own (non-enforcing) get_verify_result block is
  reached. That post-handshake block already logs the verify failure as a WARNING
  and returns 0 (success); the handshake is the real gate.

  mbedtls_ssl_conf_authmode is FUN_421b5aa0 -- a one-line setter:
      entry a1,0x20 ; s32i a3,a2,0x28 (conf->authmode = arg) ; retw.n
  It has EXACTLY ONE caller in the whole image: open_ssl_connection, at
      VA 0x4201fa6a:  movi.n a11, 0x2      ; authmode = MBEDTLS_SSL_VERIFY_REQUIRED
      VA 0x4201fa6c:  mov   a10, a7        ; conf
      VA 0x4201fa6f:  l32r  a8, 0x42001de4 ; -> 0x421b5aa0 (conf_authmode)
      VA 0x4201fa72:  callx8 a8

WHAT
  Change the immediate 2 (VERIFY_REQUIRED) to 0 (VERIFY_NONE). Under VERIFY_NONE
  mbedtls skips cert verification in the handshake, so the handshake succeeds for
  any cert (self-signed, CN-mismatch, expired, MITM), then the non-enforcing
  block returns success. No per-cert callback games, no CA bundle edits.

  Encoding (Xtensa narrow movi.n at, imm):
      bits[15:12]=imm[3:0], bits[11:8]=at, bits[7:4]=imm[6:4], bits[3:0]=0xC
      movi.n a11,2 = 0x2B0C -> LE bytes 0c 2b   (current)
      movi.n a11,0 = 0x0B0C -> LE bytes 0c 0b   (patched)  == single byte 2b->0b

Also re-fixes the image's 1-byte XOR checksum and appended SHA256 so the result is
valid for any boot/OTA path (same approach as patch_pin_endpoint.py). Safe to run
AFTER patch_pin_endpoint.py on the same image -- different offset.

USAGE
  python3 patch_authmode_none.py <in_app.bin> <out_app.bin>
  Flash result to the active OTA slot @ 0x20000.
"""
import sys, struct, hashlib

CALL_FOFF = 0x4201fa6a - 0x41dd0000      # = 0x24fa6a (movi.n a11,2)
EXPECT    = bytes.fromhex("0c2b")        # movi.n a11, 2  (VERIFY_REQUIRED)
PATCH     = bytes.fromhex("0c0b")        # movi.n a11, 0  (VERIFY_NONE)
LIT_FOFF  = 0x42001de4 - 0x41dd0000      # literal that must point at conf_authmode
LIT_EXPECT= 0x421b5aa0                   # mbedtls_ssl_conf_authmode

def image_layout(d):
    if d[0] != 0xE9:
        sys.exit("ERROR: not an ESP image (magic != 0xE9)")
    off = 24
    for _ in range(d[1]):
        _la, ln = struct.unpack('<II', d[off:off+8]); off += 8 + ln
    ck = off
    while ck % 16 != 15:
        ck += 1
    return ck, ck + 1, bool(d[23] & 1)

def main():
    if len(sys.argv) != 3:
        print(__doc__); sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, 'rb').read())

    # sanity: the literal really points at conf_authmode (guards wrong build)
    lit = struct.unpack('<I', d[LIT_FOFF:LIT_FOFF+4])[0]
    if lit != LIT_EXPECT:
        sys.exit(f"ERROR: literal @0x{LIT_FOFF:06x} -> 0x{lit:08x}, expected "
                 f"0x{LIT_EXPECT:08x} (conf_authmode). Wrong build -- not patching.")
    got = bytes(d[CALL_FOFF:CALL_FOFF+2])
    if got == PATCH:
        sys.exit("Already patched (authmode=NONE). Nothing to do.")
    if got != EXPECT:
        sys.exit(f"ERROR: call-site @0x{CALL_FOFF:06x} is {got.hex()}, expected "
                 f"{EXPECT.hex()} (movi.n a11,2). Wrong build/offset -- not patching.")

    ck_pos, img_end, hash_appended = image_layout(d)
    print(f"image: checksum @0x{ck_pos:06x}, image_end 0x{img_end:06x}, "
          f"hash_appended={hash_appended}, size 0x{len(d):x}")

    delta = 0
    for o, n in zip(got, PATCH):
        delta ^= o ^ n
    d[CALL_FOFF:CALL_FOFF+2] = PATCH
    print(f"patch  @0x{CALL_FOFF:06x}: {got.hex()} -> {PATCH.hex()}  "
          f"(movi.n a11,2 -> movi.n a11,0  ; VERIFY_REQUIRED -> VERIFY_NONE)")

    old_ck = d[ck_pos]; d[ck_pos] = old_ck ^ delta
    print(f"cksum  @0x{ck_pos:06x}: {old_ck:02x} -> {d[ck_pos]:02x}")

    if hash_appended and len(d) >= img_end + 32:
        new = hashlib.sha256(bytes(d[:img_end])).digest()
        d[img_end:img_end+32] = new
        print(f"rehash @0x{img_end:06x}: -> {new.hex()[:16]}...")

    open(dst, 'wb').write(d)
    print(f"wrote  : {dst} ({len(d)} bytes). Flash to the active OTA slot @0x20000.")

if __name__ == '__main__':
    main()
