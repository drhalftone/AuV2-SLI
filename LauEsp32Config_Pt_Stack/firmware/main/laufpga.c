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

#include "laufpga.h"
#include "laujtag.h"
#include "laulibrary.h"
#include "laupins.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

// JTAG CONFIGURATION OF THE PT V2'S XC7A100T FROM A .BIT ON THE SD CARD, FOLLOWING THE
// SINGLE-DEVICE FLOW IN THE JTAG CHAPTER OF UG470:
//   TLR -> JPROGRAM -> POLL INIT_COMPLETE (IR CAPTURE BIT 4) -> CFG_IN AND SHIFT THE DATA ->
//   JSTART -> AT LEAST 2000 TCK IN RUN-TEST/IDLE -> TLR -> CHECK DONE (IR CAPTURE BIT 5 AND PIN)
// CONFIGURATION RAM ONLY, NOTHING HERE TOUCHES THE QSPI BOOT FLASH (README SECTION 2)

static const char *TAG = "LAUFpga";

static SemaphoreHandle_t jtagLock = NULL;
static const char *jtagLockOwner = NULL;
static char lastLoadedImage[72] = "unknown (FPGA booted from its own flash, or not loaded by this card)";

// IDCODE WITH THE VERSION NIBBLE MASKED AND THE PART NAME PREFIX FROM THE .BIT HEADER 'b' FIELD
typedef struct {
    uint32_t idCode;
    const char *partName;
} LAUPartEntry;

static const LAUPartEntry partTable[] = {
    { 0x0362E093, "7a15t" },  { 0x0362D093, "7a35t" },  { 0x0362C093, "7a50t" },
    { 0x03632093, "7a75t" },  { 0x03631093, "7a100t" }, { 0x03636093, "7a200t" },
};

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static const char *partNameForIdCode(uint32_t idCode)
{
    for (size_t index = 0; index < sizeof(partTable) / sizeof(partTable[0]); index++) {
        if ((idCode & 0x0FFFFFFF) == partTable[index].idCode) {
            return (partTable[index].partName);
        }
    }
    return (NULL);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauFpgaInit(void)
{
    // DRIVE BOTH FET GATES LOW BEFORE ANYTHING ELSE RUNS, R4 AND R5 ALREADY HOLD THEM THERE
    gpio_set_level(PINPROGRAMENABLE, 0);
    gpio_set_level(PINRESETENABLE, 0);
    gpio_config_t fetConfig = {
        .pin_bit_mask = (1ULL << PINPROGRAMENABLE) | (1ULL << PINRESETENABLE),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&fetConfig);
    gpio_set_level(PINPROGRAMENABLE, 0);
    gpio_set_level(PINRESETENABLE, 0);

    // DONE IS AN INPUT WITH NO PULL BECAUSE THE PT'S R34 IS THE PULL-UP, NEVER MAKE IT AN OUTPUT
    gpio_config_t doneConfig = { .pin_bit_mask = 1ULL << PINFPGADONE, .mode = GPIO_MODE_INPUT };
    gpio_config(&doneConfig);

    lauJtagInit();
    jtagLock = xSemaphoreCreateMutex();
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauFpgaDonePin(void)
{
    return (gpio_get_level(PINFPGADONE) != 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauFpgaTryLock(const char *owner)
{
    if (xSemaphoreTake(jtagLock, 0) != pdTRUE) {
        return (false);
    }
    jtagLockOwner = owner;
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauFpgaUnlock(void)
{
    jtagLockOwner = NULL;
    xSemaphoreGive(jtagLock);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
const char *lauFpgaLockOwner(void)
{
    return (jtagLockOwner);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *failWithMessage(cJSON *object, bool *errorFlag, const char *format, ...)
{
    char message[256];
    va_list arguments;
    va_start(arguments, format);
    vsnprintf(message, sizeof(message), format, arguments);
    va_end(arguments);

    cJSON_AddBoolToObject(object, "ok", false);
    cJSON_AddStringToObject(object, "error", message);
    *errorFlag = true;
    ESP_LOGW(TAG, "%s", message);
    return (object);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool probeChain(cJSON *object, uint32_t *idCodeOut, const char **partNameOut, char *errorString, size_t capacity)
{
    // MUST BE CALLED WITH THE LOCK HELD AND U2 ENABLED, THE CHAIN MUST BE EXACTLY THE PT'S FPGA
    int chainLength = lauJtagChainLength();
    cJSON_AddNumberToObject(object, "jtag_chain_length", chainLength);
    if (chainLength != 1) {
        snprintf(errorString, capacity, "JTAG chain length is %d, expected exactly 1 (the Pt's FPGA). "
                 "0 usually means U2 is not driving or TDO is open; check the rev A/B straps", chainLength);
        return (false);
    }

    // TEST-LOGIC-RESET SELECTS IDCODE SO A 32-BIT DATA SHIFT READS IT OUT
    lauJtagReset();
    uint32_t idCode = lauJtagShiftDR32(0);
    char idString[12];
    sprintf(idString, "0x%08lx", (unsigned long)idCode);
    cJSON_AddStringToObject(object, "idcode", idString);

    const char *partName = partNameForIdCode(idCode);
    cJSON_AddStringToObject(object, "part", partName ? partName : "unrecognised");
    if (partName == NULL) {
        snprintf(errorString, capacity, "IDCODE %s is not a known 7-series part", idString);
        return (false);
    }
    *idCodeOut = idCode;
    *partNameOut = partName;
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauFpgaStatus(void)
{
    cJSON *object = cJSON_CreateObject();
    cJSON_AddBoolToObject(object, "done_pin", lauFpgaDonePin());
    cJSON_AddStringToObject(object, "last_loaded_by_this_card", lastLoadedImage);

    // DO NOT TOUCH JTAG WHILE SOMEONE ELSE HOLDS IT
    const char *owner = lauFpgaLockOwner();
    if (owner) {
        cJSON_AddStringToObject(object, "jtag_busy", owner);
        return (object);
    }
    if (!lauFpgaTryLock("status")) {
        cJSON_AddStringToObject(object, "jtag_busy", "yes");
        return (object);
    }

    // PROBE THE CHAIN AND READ THE DONE AND INIT BITS FROM THE INSTRUCTION CAPTURE
    lauJtagEnable(true);
    uint32_t idCode;
    const char *partName;
    char errorString[200];
    if (probeChain(object, &idCode, &partName, errorString, sizeof(errorString))) {
        uint32_t capture = lauJtagShiftIR(XC7BYPASS, XC7IRLENGTH);
        cJSON_AddBoolToObject(object, "ir_capture_done", (capture & XC7IRCAPTUREDONE) != 0);
        cJSON_AddBoolToObject(object, "ir_capture_init_complete", (capture & XC7IRCAPTUREINITCOMPLETE) != 0);
        lauJtagReset();
    } else {
        cJSON_AddStringToObject(object, "jtag_error", errorString);
    }
    lauJtagEnable(false);
    lauFpgaUnlock();
    return (object);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauFpgaLoad(const char *stem, bool *errorFlag)
{
    *errorFlag = false;
    cJSON *object = cJSON_CreateObject();
    cJSON_AddStringToObject(object, "name", stem);
    if (!lauLibraryIsMounted()) {
        return (failWithMessage(object, errorFlag, "no SD card mounted"));
    }

    // CHECKS THAT DO NOT TOUCH THE FPGA (SD_BITSTREAM_LIBRARY.MD SECTION 5.3)
    LAUBitFileInfo info;
    long fileSize;
    esp_err_t headerError = lauLibraryReadHeader(stem, &info, &fileSize);
    if (headerError == ESP_ERR_NOT_FOUND) {
        return (failWithMessage(object, errorFlag, "no image named '%s' (call list_bitstreams)", stem));
    } else if (headerError != ESP_OK) {
        return (failWithMessage(object, errorFlag, "'%s.bit' has an unreadable or truncated header", stem));
    }
    cJSON_AddStringToObject(object, "image_part", info.partName);
    cJSON_AddStringToObject(object, "image_design", info.designName);

    // VERIFY THE SHA256 AGAINST THE SIDECAR WHEN ONE IS PRESENT
    char path[128];
    lauLibraryPath(stem, "bit", path, sizeof(path));
    LAUSidecar sidecar;
    if (lauLibraryReadSidecar(stem, &sidecar) && sidecar.sha256[0]) {
        char hashString[65];
        if (lauLibrarySha256File(path, hashString) != ESP_OK) {
            return (failWithMessage(object, errorFlag, "could not read '%s' to hash it", path));
        }
        if (strcasecmp(hashString, sidecar.sha256) != 0) {
            return (failWithMessage(object, errorFlag, "sha256 mismatch: sidecar says %.16s..., file is %.16s... -- refusing to load", sidecar.sha256, hashString));
        }
        cJSON_AddStringToObject(object, "sha256_verified", hashString);
    } else {
        cJSON_AddStringToObject(object, "sha256_verified", "no sidecar hash -- integrity not checked");
    }

    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return (failWithMessage(object, errorFlag, "could not open '%s'", path));
    }
    if (fseek(file, info.dataOffset, SEEK_SET) != 0) {
        fclose(file);
        return (failWithMessage(object, errorFlag, "seek failed in '%s'", path));
    }

    // TAKE THE JTAG LOCK AND TURN ON THE BUFFER
    if (!lauFpgaTryLock("load_bitstream")) {
        fclose(file);
        return (failWithMessage(object, errorFlag, "JTAG is busy (%s); try again when it is free", lauFpgaLockOwner() ? lauFpgaLockOwner() : "?"));
    }
    int64_t startTime = esp_timer_get_time();
    lauJtagEnable(true);

    // CONFIRM THE CHAIN AND THAT THE IMAGE WAS BUILT FOR THIS PART
    uint32_t idCode;
    const char *partName;
    char errorString[200];
    if (!probeChain(object, &idCode, &partName, errorString, sizeof(errorString))) {
        lauJtagEnable(false);
        lauFpgaUnlock();
        fclose(file);
        return (failWithMessage(object, errorFlag, "%s -- FPGA untouched", errorString));
    }
    if (strncmp(info.partName, partName, strlen(partName)) != 0) {
        lauJtagEnable(false);
        lauFpgaUnlock();
        fclose(file);
        return (failWithMessage(object, errorFlag, "image is for '%s' but the FPGA is '%s' -- FPGA untouched", info.partName, partName));
    }

    // POINT OF NO RETURN, JPROGRAM CLEARS THE RUNNING DESIGN
    lauJtagReset();
    lauJtagShiftIR(XC7JPROGRAM, XC7IRLENGTH);
    vTaskDelay(pdMS_TO_TICKS(10));

    // WAIT FOR THE CONFIGURATION MEMORY TO CLEAR
    bool initCompleteFlag = false;
    for (int attempt = 0; attempt < 100 && !initCompleteFlag; attempt++) {
        initCompleteFlag = (lauJtagShiftIR(XC7ISCNOOP, XC7IRLENGTH) & XC7IRCAPTUREINITCOMPLETE) != 0;
        if (!initCompleteFlag) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
    }
    if (!initCompleteFlag) {
        lauJtagReset();
        lauJtagEnable(false);
        lauFpgaUnlock();
        fclose(file);
        snprintf(lastLoadedImage, sizeof(lastLoadedImage), "none (load of '%s' failed)", stem);
        return (failWithMessage(object, errorFlag, "INIT_COMPLETE never set after JPROGRAM; the FPGA is now UNCONFIGURED. "
                                "Call restore_flash_image to bring back the camera image"));
    }

    // SHIFT THE CONFIGURATION DATA THROUGH CFG_IN IN 4 KB CHUNKS
    lauJtagShiftIR(XC7CFGIN, XC7IRLENGTH);
    lauJtagShiftDRBegin();
    static uint8_t buffer[4096];
    uint32_t bytesLeft = info.dataLength;
    bool readErrorFlag = false;
    while (bytesLeft > 0) {
        size_t bytesWanted = (bytesLeft < sizeof(buffer)) ? bytesLeft : sizeof(buffer);
        size_t bytesRead = fread(buffer, 1, bytesWanted, file);
        if (bytesRead != bytesWanted) {
            // STILL EXIT SHIFT-DR CLEANLY WITH WHATEVER WE HAVE
            readErrorFlag = true;
            lauJtagShiftDRBytes(buffer, bytesRead ? bytesRead : 1, true);
            break;
        }
        bytesLeft -= bytesRead;
        lauJtagShiftDRBytes(buffer, bytesRead, bytesLeft == 0);
    }
    fclose(file);

    // START THE DESIGN AND READ BACK DONE FROM THE INSTRUCTION CAPTURE
    lauJtagShiftIR(XC7JSTART, XC7IRLENGTH);
    lauJtagRunTest(2000);
    lauJtagReset();
    uint32_t capture = lauJtagShiftIR(XC7BYPASS, XC7IRLENGTH);
    lauJtagReset();
    lauJtagEnable(false);
    lauFpgaUnlock();

    // ALSO CHECK THE DONE PIN SINCE THAT IS WHAT THE REST OF THE STACK SEES
    bool donePinFlag = false;
    for (int attempt = 0; attempt < 50 && !donePinFlag; attempt++) {
        donePinFlag = lauFpgaDonePin();
        if (!donePinFlag) {
            vTaskDelay(pdMS_TO_TICKS(2));
        }
    }
    double seconds = (esp_timer_get_time() - startTime) / 1e6;
    cJSON_AddBoolToObject(object, "ir_capture_done", (capture & XC7IRCAPTUREDONE) != 0);
    cJSON_AddBoolToObject(object, "done_pin", donePinFlag);
    cJSON_AddNumberToObject(object, "seconds", seconds);
    cJSON_AddNumberToObject(object, "bytes_shifted", (double)(info.dataLength - bytesLeft));

    if (readErrorFlag) {
        snprintf(lastLoadedImage, sizeof(lastLoadedImage), "none (load of '%s' failed)", stem);
        return (failWithMessage(object, errorFlag, "SD read error part-way through the image; FPGA is not configured. Call restore_flash_image"));
    } else if (!donePinFlag) {
        snprintf(lastLoadedImage, sizeof(lastLoadedImage), "none (load of '%s' failed)", stem);
        return (failWithMessage(object, errorFlag, "DONE did not go high; the image did not start. Call restore_flash_image to recover"));
    }
    snprintf(lastLoadedImage, sizeof(lastLoadedImage), "%s", stem);
    cJSON_AddBoolToObject(object, "ok", true);
    ESP_LOGI(TAG, "lauFpgaLoad() :: loaded '%s' in %.2f s", stem, seconds);
    return (object);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauFpgaRestoreFlash(bool *errorFlag)
{
    *errorFlag = false;
    cJSON *object = cJSON_CreateObject();
    if (!lauFpgaTryLock("restore_flash_image")) {
        return (failWithMessage(object, errorFlag, "JTAG/config is busy (%s)", lauFpgaLockOwner() ? lauFpgaLockOwner() : "?"));
    }

    // HOLD PROGRAM_B LOW WELL PAST T_PROGRAM (250 NS MINIMUM), THEN LET THE PT'S PULL-UP RELEASE IT
    gpio_set_level(PINPROGRAMENABLE, 1);
    esp_rom_delay_us(1000);
    gpio_set_level(PINPROGRAMENABLE, 0);

    // A QSPI LOAD TAKES ABOUT A SECOND OR LESS, SO GIVE UP AFTER FIVE
    int64_t startTime = esp_timer_get_time();
    bool donePinFlag = false;
    while (!donePinFlag && esp_timer_get_time() - startTime < 5 * 1000 * 1000) {
        vTaskDelay(pdMS_TO_TICKS(10));
        donePinFlag = lauFpgaDonePin();
    }
    lauFpgaUnlock();

    cJSON_AddBoolToObject(object, "done_pin", donePinFlag);
    cJSON_AddNumberToObject(object, "seconds", (esp_timer_get_time() - startTime) / 1e6);
    if (!donePinFlag) {
        return (failWithMessage(object, errorFlag, "PROGRAM_B pulsed but DONE stayed low for 5 s -- is there a valid image in the QSPI flash?"));
    }
    snprintf(lastLoadedImage, sizeof(lastLoadedImage), "flash image (restored by PROGRAM_B)");
    cJSON_AddBoolToObject(object, "ok", true);
    return (object);
}
