// WiFi station + mDNS (<hostname>.local).
#pragma once
#include <stdbool.h>

#include "esp_err.h"

esp_err_t net_start(void);                 // non-blocking; reconnects forever in the background
bool net_wait_connected(int timeout_ms);
bool net_connected(void);
