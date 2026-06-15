/*
 * app_main.c — stub entry point.
 * This exists only to give the linker a valid app_main symbol so
 * idf.py build succeeds. We never actually flash this firmware —
 * we only want the compiled bytes of cmd1_handler extracted from
 * the .elf for splicing into the real OLLI firmware image.
 */
#include <stddef.h>
#include "cmd1_patch.h"

void app_main(void)
{
    /* stub — never runs on real hardware */
    cmd1_handler(0, NULL);
}
