// HTTP front door: POST /mcp, PUT /bitstreams/<name>.{bit,json}, PUT /ota, GET /.
#pragma once
#include "esp_err.h"

esp_err_t server_start(const char *bearer_token);
