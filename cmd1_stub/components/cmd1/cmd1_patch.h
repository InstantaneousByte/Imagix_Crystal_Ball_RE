#pragma once

/*
 * cmd1_patch.h — public interface for the cmd1 console handler.
 * Replaces the dev-stub at flash address 0x42043808.
 */

#ifdef __cplusplus
extern "C" {
#endif

/**
 * cmd1_handler() — push a .bin file from the main board's SD card
 * to the fan blade over TCP:4800, overwriting an existing animation slot.
 *
 * Usage at the serial console:
 *   cmd1                        → uses default src/dst (see cmd1_patch.c)
 *   cmd1 /sdcard/myfile.bin     → custom source path
 *   cmd1 /sdcard/myfile.bin eb_idle_eb1_64.bin  → custom src + dst
 *
 * @param argc  argument count (0 = use defaults)
 * @param argv  argument vector
 * @return 0 on success, 1 on failure
 */
int cmd1_handler(int argc, char **argv);

#ifdef __cplusplus
}
#endif
