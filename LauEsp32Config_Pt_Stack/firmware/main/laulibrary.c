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

#include "laulibrary.h"
#include "laupins.h"

#include <ctype.h>
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

#include "driver/gpio.h"
#include "driver/sdmmc_host.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "mbedtls/sha256.h"
#include "sdmmc_cmd.h"

static const char *TAG = "LAULibrary";
static sdmmc_card_t *sdCard = NULL;

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool hasBitExtension(const char *filename)
{
    size_t length = strlen(filename);
    return (length > 4 && strcasecmp(filename + length - 4, ".bit") == 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauLibraryMount(void)
{
    // THE CARD DETECT SWITCH IS AN INPUT WITH R36 AS ITS PULL-UP
    gpio_config_t cardDetectConfig = { .pin_bit_mask = 1ULL << PINSDCARDDETECT, .mode = GPIO_MODE_INPUT };
    gpio_config(&cardDetectConfig);

    // RUN THE BUS AT 20 MHZ FOR BRING-UP AND MOVE TO HIGHSPEED ONCE IT IS PROVEN
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;

    // ROUTE THE 4-BIT SLOT TO OUR PINS THROUGH THE GPIO MATRIX, LEAVING CARD DETECT UNSET BECAUSE
    // THE DRIVER ASSUMES ACTIVE LOW AND THE SOCKET'S POLARITY HAS NOT BEEN CHECKED ON HARDWARE
    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = PINSDCLK;
    slot.cmd = PINSDCMD;
    slot.d0 = PINSDDATA0;
    slot.d1 = PINSDDATA1;
    slot.d2 = PINSDDATA2;
    slot.d3 = PINSDDATA3;

    // NEVER FORMAT ON A FAILED MOUNT, A FLAKY CONTACT MUST NOT WIPE THE LIBRARY
    esp_vfs_fat_sdmmc_mount_config_t mountConfig = {
        .format_if_mount_failed = false,
        .max_files = 4,
        .allocation_unit_size = 16 * 1024,
    };
    esp_err_t error = esp_vfs_fat_sdmmc_mount(LIBRARYMOUNTPOINT, &host, &slot, &mountConfig, &sdCard);
    if (error != ESP_OK) {
        ESP_LOGW(TAG, "lauLibraryMount() :: SD mount failed: %s (CD_N level %d)", esp_err_to_name(error), lauLibraryCardDetectLevel());
        sdCard = NULL;
        return (error);
    }
    sdmmc_card_print_info(stdout, sdCard);
    mkdir(LIBRARYDIRECTORY, 0775);
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauLibraryIsMounted(void)
{
    return (sdCard != NULL);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
int lauLibraryCardDetectLevel(void)
{
    return (gpio_get_level(PINSDCARDDETECT));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauLibraryNormalizeName(const char *name, char *stem, size_t capacity)
{
    if (name == NULL) {
        return (false);
    }

    // STRIP A TRAILING .BIT AND CHECK THE LENGTH LIMITS
    size_t length = strlen(name);
    if (hasBitExtension(name)) {
        length -= 4;
    }
    if (length == 0 || length > 64 || length >= capacity || name[0] == '.') {
        return (false);
    }

    // ONLY ALLOW CHARACTERS THAT CANNOT FORM A PATH
    for (size_t index = 0; index < length; index++) {
        char character = name[index];
        if (!(isalnum((unsigned char)character) || character == '_' || character == '-' || character == '.')) {
            return (false);
        }
    }
    memcpy(stem, name, length);
    stem[length] = 0;
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauLibraryPath(const char *stem, const char *extension, char *path, size_t capacity)
{
    snprintf(path, capacity, LIBRARYDIRECTORY "/%s.%s", stem, extension);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauLibraryReadHeader(const char *stem, LAUBitFileInfo *info, long *fileSize)
{
    char path[128];
    lauLibraryPath(stem, "bit", path, sizeof(path));
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return (ESP_ERR_NOT_FOUND);
    }

    // READ THE FIRST 512 BYTES FOR THE HEADER AND THE FILE SIZE FOR THE LENGTH CHECK
    uint8_t buffer[512];
    size_t bytesRead = fread(buffer, 1, sizeof(buffer), file);
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    fclose(file);
    if (fileSize) {
        *fileSize = size;
    }

    if (lauBitFileParse(buffer, bytesRead, info) != 0) {
        return (ESP_ERR_INVALID_RESPONSE);
    }
    if ((long)info->dataOffset + (long)info->dataLength > size) {
        return (ESP_ERR_INVALID_SIZE);
    }
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void copyJsonString(char *destination, size_t capacity, const cJSON *object, const char *key)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
    if (cJSON_IsString(value) && value->valuestring) {
        strncpy(destination, value->valuestring, capacity - 1);
        destination[capacity - 1] = 0;
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauLibraryReadSidecar(const char *stem, LAUSidecar *sidecar)
{
    memset(sidecar, 0, sizeof(*sidecar));

    char path[128];
    lauLibraryPath(stem, "json", path, sizeof(path));
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return (false);
    }
    char buffer[2048];
    size_t bytesRead = fread(buffer, 1, sizeof(buffer) - 1, file);
    fclose(file);
    buffer[bytesRead] = 0;

    cJSON *json = cJSON_Parse(buffer);
    if (json == NULL) {
        ESP_LOGW(TAG, "lauLibraryReadSidecar() :: %s sidecar is not valid JSON, ignored", stem);
        return (false);
    }
    copyJsonString(sidecar->description, sizeof(sidecar->description), json, "description");
    copyJsonString(sidecar->gitCommit, sizeof(sidecar->gitCommit), json, "git_commit");
    copyJsonString(sidecar->sha256, sizeof(sidecar->sha256), json, "sha256");
    copyJsonString(sidecar->sourceScript, sizeof(sidecar->sourceScript), json, "source_script");
    cJSON_Delete(json);
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauLibrarySha256File(const char *path, char hexString[65])
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return (ESP_ERR_NOT_FOUND);
    }

    // HASH THE FILE IN 4 KB CHUNKS
    static uint8_t buffer[4096];
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    mbedtls_sha256_starts(&context, 0);
    size_t bytesRead;
    while ((bytesRead = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        mbedtls_sha256_update(&context, buffer, bytesRead);
    }
    bool readErrorFlag = ferror(file);
    fclose(file);

    uint8_t digest[32];
    mbedtls_sha256_finish(&context, digest);
    mbedtls_sha256_free(&context);
    if (readErrorFlag) {
        return (ESP_FAIL);
    }

    // CONVERT THE DIGEST TO LOWER CASE HEX
    for (int index = 0; index < 32; index++) {
        sprintf(hexString + 2 * index, "%02x", digest[index]);
    }
    hexString[64] = 0;
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauLibraryList(void)
{
    cJSON *array = cJSON_CreateArray();
    DIR *directory = opendir(LIBRARYDIRECTORY);
    if (directory == NULL) {
        return (array);
    }

    // DESCRIBE EVERY .BIT FILE FROM ITS HEADER AND ITS SIDECAR
    struct dirent *entry;
    while ((entry = readdir(directory)) != NULL) {
        char stem[72];
        if (!hasBitExtension(entry->d_name) || !lauLibraryNormalizeName(entry->d_name, stem, sizeof(stem))) {
            continue;
        }

        cJSON *object = cJSON_CreateObject();
        cJSON_AddStringToObject(object, "name", stem);
        LAUBitFileInfo info;
        long size = 0;
        esp_err_t headerError = lauLibraryReadHeader(stem, &info, &size);
        cJSON_AddNumberToObject(object, "size_bytes", (double)size);
        if (headerError == ESP_OK) {
            cJSON_AddStringToObject(object, "part", info.partName);
            cJSON_AddStringToObject(object, "design", info.designName);
            cJSON_AddStringToObject(object, "build_date", info.buildDate);
            cJSON_AddStringToObject(object, "build_time", info.buildTime);
        } else {
            cJSON_AddStringToObject(object, "error", "unreadable .bit header -- will not be loaded");
        }

        LAUSidecar sidecar;
        if (lauLibraryReadSidecar(stem, &sidecar)) {
            if (sidecar.description[0]) {
                cJSON_AddStringToObject(object, "description", sidecar.description);
            }
            if (sidecar.gitCommit[0]) {
                cJSON_AddStringToObject(object, "git_commit", sidecar.gitCommit);
            }
            if (sidecar.sourceScript[0]) {
                cJSON_AddStringToObject(object, "source_script", sidecar.sourceScript);
            }
            cJSON_AddBoolToObject(object, "has_sha256", sidecar.sha256[0] != 0);
        }
        cJSON_AddItemToArray(array, object);
    }
    closedir(directory);
    return (array);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauLibraryDelete(const char *stem)
{
    char path[128];
    lauLibraryPath(stem, "bit", path, sizeof(path));
    if (unlink(path) != 0) {
        return (ESP_ERR_NOT_FOUND);
    }

    // THE SIDECAR IS OPTIONAL SO IGNORE A FAILURE HERE
    lauLibraryPath(stem, "json", path, sizeof(path));
    unlink(path);
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauLibraryCardInfo(void)
{
    cJSON *object = cJSON_CreateObject();
    cJSON_AddBoolToObject(object, "mounted", lauLibraryIsMounted());
    cJSON_AddNumberToObject(object, "card_detect_raw_level", lauLibraryCardDetectLevel());
    if (!lauLibraryIsMounted()) {
        return (object);
    }

    uint64_t totalBytes = 0;
    uint64_t freeBytes = 0;
    if (esp_vfs_fat_info(LIBRARYMOUNTPOINT, &totalBytes, &freeBytes) == ESP_OK) {
        cJSON_AddNumberToObject(object, "total_bytes", (double)totalBytes);
        cJSON_AddNumberToObject(object, "free_bytes", (double)freeBytes);
    }
    cJSON_AddStringToObject(object, "card_name", sdCard->cid.name);

    // COUNT THE IMAGES IN THE LIBRARY DIRECTORY
    int imageCount = 0;
    DIR *directory = opendir(LIBRARYDIRECTORY);
    if (directory) {
        struct dirent *entry;
        while ((entry = readdir(directory)) != NULL) {
            if (hasBitExtension(entry->d_name)) {
                imageCount++;
            }
        }
        closedir(directory);
    }
    cJSON_AddNumberToObject(object, "image_count", imageCount);
    return (object);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void lauLibraryTempPath(const char *extension, char *path, size_t capacity)
{
    snprintf(path, capacity, LIBRARYDIRECTORY "/_incoming.%s", extension);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauLibraryInstall(const char *tempPath, const char *stem, const char *extension)
{
    char destination[128];
    lauLibraryPath(stem, extension, destination, sizeof(destination));

    // FAT RENAME WILL NOT REPLACE AN EXISTING FILE, SO REMOVE IT FIRST
    unlink(destination);
    if (rename(tempPath, destination) != 0) {
        unlink(tempPath);
        return (ESP_FAIL);
    }
    return (ESP_OK);
}
