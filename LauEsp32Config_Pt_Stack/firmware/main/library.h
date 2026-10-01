// The bitstream library on the microSD card (SD_BITSTREAM_LIBRARY.md §5).
// Images are <stem>.bit in /sdcard/bitstreams, each with an optional <stem>.json sidecar.
#pragma once
#include <stdbool.h>
#include <stddef.h>

#include "bitfile.h"
#include "cJSON.h"
#include "esp_err.h"

#define LIB_MOUNT   "/sdcard"
#define LIB_DIR     LIB_MOUNT "/bitstreams"

esp_err_t lib_mount(void);
bool lib_mounted(void);
int lib_card_detect_level(void);   // raw SD_CD_N level; polarity unverified, reported not trusted

// Library names are stems: [A-Za-z0-9._-], 1..64 chars, no leading '.'. A trailing ".bit" is
// accepted and stripped. Returns false for anything else (including path tricks).
bool lib_normalize_name(const char *in, char *stem, size_t cap);
void lib_path(const char *stem, const char *ext, char *out, size_t cap);

// Read and parse the header of <stem>.bit; also returns the file size.
esp_err_t lib_read_header(const char *stem, bitfile_info_t *info, long *file_size);

// Sidecar fields (any may be empty).
typedef struct {
    char description[256];
    char git_commit[48];
    char sha256[72];
    char source_script[96];
} lib_sidecar_t;
bool lib_read_sidecar(const char *stem, lib_sidecar_t *sc);

esp_err_t lib_sha256_file(const char *path, char hex_out[65]);

cJSON *lib_list(void);                       // array of image descriptions
esp_err_t lib_delete(const char *stem);      // .bit and .json
cJSON *lib_card_info(void);

// Atomic install: write to a temp path, then move into place, replacing any existing file.
void lib_temp_path(const char *ext, char *out, size_t cap);
esp_err_t lib_install(const char *temp_path, const char *stem, const char *ext);
