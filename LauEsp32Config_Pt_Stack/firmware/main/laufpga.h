/*********************************************************************************
 *                                                                               *
 * Copyright (c) 2026, Dr. Daniel L. Lau                                         *
 * All rights reserved.                                                          *
 *                                                                               *
 * Redistribution and use in source and binary forms, with or without            *
 * modification, is strictly forbidden.                                          *
 *                                                                               *
 * THIS SOFTWARE IS PROVIDED BY DR. DANIEL L. LAU ''AS IS'' AND ANY              *
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED     *
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE        *
 * DISCLAIMED. IN NO EVENT SHALL DR. DANIEL L. LAU BE LIABLE FOR ANY             *
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES    *
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;  *
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND   *
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT    *
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS *
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.                  *
 *                                                                               *
 *********************************************************************************/

#ifndef LAUFPGA_H
#define LAUFPGA_H

#include <stdbool.h>

#include "cJSON.h"
#include "esp_err.h"

// FPGA CONTROL: JTAG CONFIGURATION FROM THE LIBRARY, PROGRAM_B, DONE, AND THE JTAG LOCK

// CALL THIS FIRST IN APP_MAIN SO PROG_EN, RESET_EN, AND JTAG START IN THEIR SAFE STATES
void lauFpgaInit(void);
bool lauFpgaDonePin(void);

// ONE JTAG USER AT A TIME (MCP, THE PUT ENDPOINTS, AND LATER XVC), NEVER BLOCKS
bool lauFpgaTryLock(const char *owner);
void lauFpgaUnlock(void);
const char *lauFpgaLockOwner(void);

// EACH RETURNS A JSON OBJECT DESCRIBING THE OUTCOME FOR THE CALLER TO FREE, AND A REFUSED OR
// FAILED OPERATION CARRIES "ok": false AND "error" WITH ERRORFLAG SET TO TRUE
cJSON *lauFpgaStatus(void);
cJSON *lauFpgaLoad(const char *stem, bool *errorFlag);
cJSON *lauFpgaRestoreFlash(bool *errorFlag);

#endif // LAUFPGA_H
