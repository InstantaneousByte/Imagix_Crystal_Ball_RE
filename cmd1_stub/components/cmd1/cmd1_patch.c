/*
 * cmd1_patch.c — cmd1 console command handler (hardcoded-address, self-contained).
 * Replaces the dev-stub at 0x42043808.
 *
 * 2026-06-14 ADDRESS-CORRECTION PASS — see docs/ADDRESS_CORRECTIONS.md.
 * 2026-06-15 LITERAL-LOCALITY FIX:
 *   The blob is spliced to a new flash address, so it MUST carry its own l32r
 *   literal pool adjacent to the code. That requires building this component with
 *   -mtext-section-literals (see components/cmd1/CMakeLists.txt). We also avoid the
 *   external `memset` (manual zero below) so the blob has NO external symbols and
 *   every l32r literal is a self-contained absolute value.
 *   extract_cmd1.py now asserts every l32r target lands inside the extracted blob.
 */
#include <stdint.h>
#include "cmd1_patch.h"

/* Function pointers — verified from real call-signatures in the Apr-7 build decomp. */
typedef int (*lwip_socket_fn)    (int, int, int);
typedef int (*lwip_connect_fn)   (int, const void*, uint32_t);
typedef int (*lwip_close_fn)     (int);
typedef int (*lwip_setsockopt_fn)(int, int, int, const void*, uint32_t);
typedef int (*fan_sync_fn)       (int, const char*, const char*, char);

#define OLLI_lwip_socket     ((lwip_socket_fn)     0x420b8588)
#define OLLI_lwip_connect    ((lwip_connect_fn)    0x420b8174)
#define OLLI_lwip_setsockopt ((lwip_setsockopt_fn) 0x420b8bf0)
#define OLLI_lwip_close      ((lwip_close_fn)      0x420c900c)
#define OLLI_fan_sync        ((fan_sync_fn)        0x4202980c)

#define FAN_PORT     4800
#define FAN_ADDR_BE  0x010A0AACu   /* 172.10.10.1, network byte order */
#define AF_INET_     2
#define SOCK_STREAM_ 1
#define SOL_SOCKET_  0xfff
#define SO_SNDTIMEO_ 0x1005
#define SO_RCVTIMEO_ 0x1006

#define SRC_PATH  "/sdcard/custom/my_animation.bin"
#define DST_NAME  "eb_idle_eb1_64.bin"

/* lwip sockaddr_in: u8 sin_len + u8 sin_family. lwip_connect reads sin_family @ offset 1. */
typedef struct {
    uint8_t  sin_len;
    uint8_t  sin_family;
    uint16_t sin_port;
    uint32_t sin_addr;
    uint8_t  sin_zero[8];
} olli_sockaddr_in;

static inline uint16_t olli_htons(uint16_t v) {
    return (uint16_t)((v << 8) | (v >> 8));
}

/* keep fan_connect adjacent to cmd1_handler (same TU, -fno-function-sections) and
 * noinline so extract_cmd1.py finds the symbol. */
static __attribute__((noinline)) int fan_connect(void)
{
    olli_sockaddr_in addr;
    addr.sin_len    = (uint8_t)sizeof(addr);   /* 16 */
    addr.sin_family = AF_INET_;                /* offset 1 — critical */
    addr.sin_port   = olli_htons(FAN_PORT);
    addr.sin_addr   = FAN_ADDR_BE;
    *(uint32_t *)&addr.sin_zero[0] = 0;        /* no external memset */
    *(uint32_t *)&addr.sin_zero[4] = 0;

    int s = OLLI_lwip_socket(AF_INET_, SOCK_STREAM_, 0);
    if (s < 0) return -1;

    uint32_t tv[2] = {5, 0};                   /* 5s timeouts (struct timeval) */
    OLLI_lwip_setsockopt(s, SOL_SOCKET_, SO_RCVTIMEO_, tv, sizeof(tv));
    OLLI_lwip_setsockopt(s, SOL_SOCKET_, SO_SNDTIMEO_, tv, sizeof(tv));

    if (OLLI_lwip_connect(s, &addr, sizeof(addr)) != 0) {
        OLLI_lwip_close(s);
        return -1;
    }
    return s;
}

int cmd1_handler(int argc, char **argv)
{
    const char *src = (argc > 1) ? argv[1] : SRC_PATH;
    const char *dst = (argc > 2) ? argv[2] : DST_NAME;

    int conn = fan_connect();
    if (conn < 0) return 1;

    int rc = OLLI_fan_sync(conn, src, dst, 1);
    OLLI_lwip_close(conn);
    return (rc == 1 || rc == 2) ? 0 : 1;
}
