// FPGA control: JTAG configuration from the library, PROGRAM_B, DONE, and the JTAG lock.
#pragma once
#include <stdbool.h>

#include "cJSON.h"
#include "esp_err.h"

void fpga_init(void);   // call FIRST in app_main: puts PROG_EN/RESET_EN/JTAG in their safe states

bool fpga_done_pin(void);

// One JTAG user at a time: MCP, the PUT endpoints' callers, and (later) XVC. Non-blocking.
bool fpga_try_lock(const char *owner);
void fpga_unlock(void);
const char *fpga_lock_owner(void);   // NULL when free

// Each returns a JSON object describing the outcome (caller frees). On a refused or failed
// operation the object carries "ok": false and "error"; *is_error is set accordingly.
cJSON *fpga_status(void);
cJSON *fpga_load(const char *stem, bool *is_error);
cJSON *fpga_restore_flash(bool *is_error);
