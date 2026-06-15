idf_component_register(
    SRCS "cmd1_patch.c"
    INCLUDE_DIRS "."
    REQUIRES lwip
)

# CRITICAL for the splice: the blob is relocated to a new flash address, so its l32r
# literal pool must travel WITH the code. -mtext-section-literals interleaves literals
# into the text next to their references; -fno-function-sections keeps fan_connect and
# cmd1_handler (and their literals) in one contiguous chunk. Result: a self-contained,
# position-independent blob.
target_compile_options(${COMPONENT_LIB} PRIVATE
    -mtext-section-literals
    -mlongcalls
    -fno-function-sections
    -fno-data-sections
)
