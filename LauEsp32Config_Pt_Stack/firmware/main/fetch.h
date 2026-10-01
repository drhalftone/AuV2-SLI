// fetch_bitstream: the ESP32 pulls an image from the build machine straight onto the SD card
// (SD_BITSTREAM_LIBRARY.md §6.3), so a 2.5 MB image never passes through a tool call.
#pragma once
#include <stdbool.h>

#include "cJSON.h"

// url must end in .bit unless `name` is given. Also tries <url without .bit>.json as a sidecar.
cJSON *fetch_bitstream(const char *url, const char *name, bool *is_error);
