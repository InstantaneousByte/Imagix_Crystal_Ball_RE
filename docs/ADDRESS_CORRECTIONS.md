# Address Corrections — lwip BSD socket layer (+ two stdio mislabels)

**Date:** 2026-06-14
**Applies to:** `olli_esp_sdk` `1.12-69-gb046458a` (built Apr 7 2026, IDF 5.5.0).

## The OTA-vs-dump question (resolved)

`fw_main.bin` (device flash dump) and `olli_esp_patch_build_1.16_13_May_2026.bin`
(the OTA image Ghidra analyzed) are **byte-for-byte identical** over the entire real
image (`0x40bd50` bytes, zero differing bytes). They report the same version string,
build timestamp, and segment map. The dump is just 82 KB longer because the read ran
past the image end into adjacent flash content. The "1.16 / 13 May" in the OTA filename
is a download label, not the build — the embedded `esp_app_desc` says `1.12-69`, Apr 7.

**Conclusion:** running Ghidra on the OTA was correct. The decomp is valid for the device.

## What was wrong

The first-cut lwip addresses were guessed in the `0x421a8xxx` page on the assumption
that the BSD functions cluster "near `lwip_socket`." They don't. That page is
newlib/string/file helpers. The real lwip BSD socket layer is clustered in `0x420b8xxx`.
The anchor itself was a mislabel: `0x421a8ddc` is a 2-arg function `f(x, ".")` that lazily
allocates a 0x50-byte struct — not `socket()`.

`lwip_send` and `lwip_close` were already correct (they were resolved from real sites and
happen to be in `0x420b8`/`0x420c9`).

## Corrected table

Resolved from genuine BSD call-signatures in the decomp — not slot-name guesses.

| Function | OLD (wrong) | CORRECT | Evidence |
|---|---|---|---|
| `lwip_socket`     | `0x421a8ddc` | **`0x420b8588`** | direct `socket(2,1,0)`; slot `0x420029d0` resolves here |
| `lwip_connect`    | `0x421a8c3c` | **`0x420b8174`** | getaddrinfo client loop `connect(fd,ai_addr,ai_addrlen)`; body validates `sin_family`@off1, calls `netconn_connect` @ `0x420c7420` |
| `lwip_setsockopt` | `0x421a8af4` | **`0x420b8bf0`** | `setsockopt(fd,6,1,&one,4)` (TCP_NODELAY) + SO_REUSEADDR sites; slot `0x42002004` |
| `lwip_send`       | `0x420b84f4` | `0x420b84f4` ✓ | unchanged |
| `lwip_close`      | `0x420c900c` | `0x420c900c` ✓ | unchanged (frees fds on socket error) |
| `lwip_bind`       | —            | `0x420b8030`   | httpd bind |
| `lwip_listen`     | —            | `0x420b821c`   | httpd listen |
| `lwip_accept`     | —            | `0x420b7ecc`   | httpd accept |
| `inet_pton`       | `0x421a8538` | *(dropped)*    | wrong address; unnecessary — build sockaddr directly |
| `fopen`           | `0x421a7684` | `0x421a7684` ✓ | slot `0x42000394`; called `fopen(path, mode)` |
| `fclose`          | `0x421a785c` | **MISLABEL**   | `0x421a785c` is **`fseek`** (3 args → `_fseek_r`). Do **not** use as `fclose`. |

## Two non-address bugs (fixed in `cmd1_patch.c`)

1. **`sockaddr_in` layout.** lwip is `{u8 sin_len; u8 sin_family; u16 sin_port; u32 sin_addr; u8 sin_zero[8]}`.
   `lwip_connect` reads `sin_family` at **offset 1**. The old struct declared `sin_family`
   as a `u16` at offset 0, so lwip read `0x00` = `AF_UNSPEC` → `connect()` silently became a
   **disconnect**. This would have failed even with the addresses fixed.
2. **`inet_pton` removed.** `sin_addr = 0x010A0AAC` is 172.10.10.1 already in network byte
   order — no string parsing needed.
3. **fopen/fclose pre-check removed.** `0x421a785c` was `fseek`, not `fclose` (so the gate
   leaked a `FILE*` per call). `fan_sync_binary_file` (`0x4202980c`) opens/reads the source
   itself and returns `<0` on failure, so the pre-check was redundant anyway.

## Method note (for next time)

Identify a function by the **call signature at a real use site**, not by which PTR slot
appears to point at it:
- `socket(2,1,0)`  ·  `setsockopt(fd,level,optname,val,len)`  ·  `connect(fd,&sockaddr,16)`  ·  `bind/listen/accept`

Then confirm against the body (e.g. `lwip_connect` validates `sin_family` at offset 1 and
tail-calls `netconn_connect`). A landing on an `entry` prologue + a literal reference proves a
real *function*, not which *name* it is.

## Downstream actions

- `cmd1_patch.c` updated. **Rebuild and re-extract**: `idf.py build` → `python3 tools/extract_cmd1.py`.
- The blob size changes (smaller — `inet_pton` + the fopen/fclose gate are gone), so the
  `cmd1_handler`-offset-within-blob argument to `splice_patch.py` changes too. `extract_cmd1.py`
  prints the exact offset and the splice command to run — **use that**, don't reuse the old `136`.
- The `lwip_region.bin` device re-read from the old plan is now moot (addresses resolved from
  the full image).

## Addendum — the `fclose` slot is also mislabeled

Slot `PTR_FUN_420013dc` (which the old registry called `fclose`) resolves to `0x421a785c`,
which is `fseek`. So resolving the slot does not recover `fclose` — the slot label was wrong.
The real `fclose` address is left unconfirmed; the corrected `cmd1_patch.c` does not need it.

## Addendum 2 — literal-pool locality (2026-06-15)

Verifying the rebuilt `cmd1_handler.asm` caught a separate showstopper that also affects the
original blob. `fan_connect`'s `l32r` instructions loaded their constants (the corrected
lwip addresses, the htons port, `0x010A0AAC`) from a literal pool at **`~0x420009b8`** — the
start of `.flash.text`, ~34 KB before the 208-byte blob at `0x420090e8`.

`l32r` is PC-relative with a fixed compile-time offset, and `splice_patch.py` only copies the
blob bytes + writes the entry jump — it does **not** carry the literals or relocate offsets.
So once spliced to `0x42043c40`, each `l32r` resolves to `(new_PC) − 0x874C` and reads a
random word out of the orb firmware instead of the intended address → `callx8` into garbage →
crash on first `cmd1`. (The 233-byte original had the same latent defect; never caught because
never flashed.)

**Fix:** build the cmd1 component with `-mtext-section-literals -fno-function-sections` so each
function's literals are interleaved next to its code and both functions stay contiguous, and
avoid the external `memset` (manual zero) so the blob has zero external symbols. The blob is
then self-contained and position-independent. `extract_cmd1.py` now extracts the code **and**
its literal pool as one contiguous region and **asserts every `l32r` target lands inside the
blob** — it refuses to emit a blob that isn't self-contained.

Files changed: `cmd1_stub/components/cmd1/cmd1_patch.c`, `cmd1_stub/components/cmd1/CMakeLists.txt`,
`tools/extract_cmd1.py`.

## Addendum 3 — relocator (final literal-pool fix, 2026-06-15)

`-mtext-section-literals` co-located *most* literals next to the code, but the linker still
deduped common constants (e.g. `0xfff` = SOL_SOCKET) into a far shared pool, leaving one
`l32r` reaching ~34 KB back — enough to blow the blob up and break after splicing. Fighting
per-constant literal merging is unwinnable.

Final fix: `tools/extract_cmd1.py` now **relocates**. It takes the two functions' code
verbatim (preserving the intra-blob `call8` and any in-region literals), rebuilds a fresh
literal pool at the FRONT of the blob, and rewrites every out-of-region `l32r` offset to point
at it. The result is `[pool][fan_connect][gap lits][cmd1_handler]`, fully position-independent
— splice it anywhere. It disassembles only the code byte-ranges (from `nm` sizes), never the
literal data, and self-checks that every `l32r` resolves inside the blob to the intended value
before writing `cmd1_patch.bin` (refuses otherwise).

Notes:
- Xtensa `l32r` literal address = `((PC+3) & ~3) + signext16(imm16)*4` (reaches backward only;
  hence the pool goes at the front). Easy to get the `(PC+3)` base wrong for non-aligned `l32r`.
- Keep `-fno-function-sections` on the cmd1 component so `fan_connect`/`cmd1_handler` stay in
  one contiguous chunk (no foreign code spliced between them). No rebuild needed to switch to
  the relocator — it reads the existing `build/cmd1_stub.elf`.

## Addendum 4 — redirect must be J; default paths live in rodata (2026-06-15)

cmd1_handler verified from a clean single-function dump: call8 -> fan_connect, callx8 ->
fan_sync_binary_file (0x4202980c), callx8 -> lwip_close (0x420c900c); argc/argv handled
(argv[1]=src, argv[2]=dst); conn<0 -> return 1. Relocator output: 244 B blob, cmd1_handler
offset 172, pool carries the four lwip addrs + 0xfff/optnames/port/IP; fan_sync stays local.

Two runtime-only issues fixed/flagged (neither is in the blob):

1. **Redirect = J, not CALL8.** splice_patch.py auto-picked CALL8 at this distance, which is
   wrong for replacing the stub's `entry`: CALL8 wouldn't forward the console's argc/argv
   (they sit in a2/a3, CALL8 passes a10..a15) and would retw into the clobbered stub body.
   Replaced with a tail-J: cmd1_handler runs its own `entry` in the console-allocated window,
   reads a2/a3, and retw's back to the console. Errors out if target is outside J's +/-128 KB.

2. **Default SRC_PATH/DST_NAME are dead after splicing.** They're `.flash.rodata` pointers
   (0x3c025454, 0x3c025440) into the build's rodata; we splice only code, so those addresses
   hold the orb's rodata in the flashed image. Always invoke with explicit args:
   `cmd1 /sdcard/custom/my_animation.bin eb_idle_eb1_64.bin`. (To support a bare `cmd1`, embed
   the two strings in the blob and fix the pointers up at splice time — small follow-up.)
