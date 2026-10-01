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

#include "laufetch.h"
#include "laulibrary.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "LAUFetch";

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static int downloadToFile(const char *url, const char *destination, long *bytesOut)
{
    // HTTPS WORKS TOO THROUGH THE CERTIFICATE BUNDLE, BUT LAN HTTP IS THE NORM
    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 15000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 4096,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return (-1);
    }

    // RETURN THE HTTP STATUS, 200 ON SUCCESS, OR -1 FOR A TRANSPORT OR WRITE ERROR
    int status = -1;
    long totalBytes = 0;
    if (esp_http_client_open(client, 0) == ESP_OK) {
        esp_http_client_fetch_headers(client);
        status = esp_http_client_get_status_code(client);
        if (status == 200) {
            FILE *file = fopen(destination, "wb");
            if (file == NULL) {
                status = -1;
            } else {
                // STREAM THE BODY TO THE CARD IN 4 KB CHUNKS
                static char buffer[4096];
                int bytesRead;
                while ((bytesRead = esp_http_client_read(client, buffer, sizeof(buffer))) > 0) {
                    if (fwrite(buffer, 1, bytesRead, file) != (size_t)bytesRead) {
                        status = -1;
                        break;
                    }
                    totalBytes += bytesRead;
                }
                if (bytesRead < 0) {
                    status = -1;
                }
                if (fclose(file) != 0) {
                    status = -1;
                }
                if (status != 200) {
                    unlink(destination);
                }
            }
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (bytesOut) {
        *bytesOut = totalBytes;
    }
    return (status);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *failWithMessage(cJSON *object, bool *errorFlag, const char *message)
{
    cJSON_AddBoolToObject(object, "ok", false);
    cJSON_AddStringToObject(object, "error", message);
    *errorFlag = true;
    ESP_LOGW(TAG, "%s", message);
    return (object);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
cJSON *lauFetchBitstream(const char *url, const char *name, bool *errorFlag)
{
    *errorFlag = false;
    cJSON *object = cJSON_CreateObject();
    if (!lauLibraryIsMounted()) {
        return (failWithMessage(object, errorFlag, "no SD card mounted"));
    }
    if (url == NULL || (strncmp(url, "http://", 7) != 0 && strncmp(url, "https://", 8) != 0)) {
        return (failWithMessage(object, errorFlag, "url must start with http:// or https://"));
    }

    // USE THE EXPLICIT NAME OR ELSE THE LAST SEGMENT OF THE URL PATH
    char stem[72];
    const char *baseName = strrchr(url, '/');
    baseName = baseName ? baseName + 1 : url;
    if (!lauLibraryNormalizeName((name && name[0]) ? name : baseName, stem, sizeof(stem))) {
        return (failWithMessage(object, errorFlag, "could not derive a valid image name; pass 'name' ([A-Za-z0-9._-], up to 64 chars)"));
    }
    cJSON_AddStringToObject(object, "name", stem);

    // DOWNLOAD TO A TEMPORARY FILE FIRST
    char tempPath[128];
    lauLibraryTempPath("bit", tempPath, sizeof(tempPath));
    int64_t startTime = esp_timer_get_time();
    long totalBytes = 0;
    int status = downloadToFile(url, tempPath, &totalBytes);
    if (status != 200) {
        char message[96];
        if (status < 0) {
            snprintf(message, sizeof(message), "download failed (network or SD write error)");
        } else {
            snprintf(message, sizeof(message), "server returned HTTP %d", status);
        }
        return (failWithMessage(object, errorFlag, message));
    }

    // IT MUST PARSE AS A COMPLETE .BIT BEFORE IT IS ALLOWED INTO THE LIBRARY
    FILE *file = fopen(tempPath, "rb");
    uint8_t header[512];
    size_t bytesRead = file ? fread(header, 1, sizeof(header), file) : 0;
    if (file) {
        fclose(file);
    }
    LAUBitFileInfo info;
    if (lauBitFileParse(header, bytesRead, &info) != 0 || (long)(info.dataOffset + info.dataLength) > totalBytes) {
        unlink(tempPath);
        return (failWithMessage(object, errorFlag, "downloaded file is not a complete Xilinx .bit (only .bit is accepted, not .bin)"));
    }
    if (lauLibraryInstall(tempPath, stem, "bit") != ESP_OK) {
        return (failWithMessage(object, errorFlag, "could not move the image into the library"));
    }

    // TRY FOR AN OPTIONAL SIDECAR BESIDE IT ON THE SERVER, THE SAME URL WITH .BIT -> .JSON
    size_t urlLength = strlen(url);
    if (urlLength > 4 && urlLength < 400 && strcasecmp(url + urlLength - 4, ".bit") == 0) {
        char sidecarUrl[408];
        memcpy(sidecarUrl, url, urlLength - 4);
        strcpy(sidecarUrl + urlLength - 4, ".json");
        char sidecarTempPath[128];
        lauLibraryTempPath("json", sidecarTempPath, sizeof(sidecarTempPath));
        if (downloadToFile(sidecarUrl, sidecarTempPath, NULL) == 200 && lauLibraryInstall(sidecarTempPath, stem, "json") == ESP_OK) {
            cJSON_AddBoolToObject(object, "sidecar", true);
        } else {
            cJSON_AddBoolToObject(object, "sidecar", false);
        }
    }

    cJSON_AddNumberToObject(object, "size_bytes", (double)totalBytes);
    cJSON_AddStringToObject(object, "part", info.partName);
    cJSON_AddStringToObject(object, "design", info.designName);
    cJSON_AddNumberToObject(object, "seconds", (esp_timer_get_time() - startTime) / 1e6);
    cJSON_AddBoolToObject(object, "ok", true);
    return (object);
}
