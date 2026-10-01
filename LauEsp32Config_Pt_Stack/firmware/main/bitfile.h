// Xilinx .bit header parser. Pure C, no ESP-IDF dependency, so it can be unit-tested on a host.
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef struct {
    char design[160];     // field 'a': design name, often with ";UserID=...;Version=..."
    char part[48];        // field 'b': e.g. "7a100tfgg484"
    char date[24];        // field 'c'
    char time[24];        // field 'd'
    uint32_t data_offset; // byte offset of the configuration data in the file
    uint32_t data_len;    // field 'e': length of the configuration data, bytes
} bitfile_info_t;

// Parse the first `len` bytes of a .bit file (512 is plenty for real headers).
// Returns 0 on success, negative on a malformed or truncated header.
int bitfile_parse(const uint8_t *buf, size_t len, bitfile_info_t *out);
