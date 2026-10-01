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

#include "lauserver.h"
#include "laubitfile.h"
#include "laulibrary.h"
#include "laumcp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define MCPMAXIMUMBODYSIZE  (16 * 1024)

static const char *TAG = "LAUServer";
static char authorizationString[96];

// SINK CALLBACK FOR STREAMING A REQUEST BODY, RETURNS FALSE TO STOP
typedef bool (*LAUBodySink)(void *context, const char *buffer, int length);

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool constantTimeEquals(const char *stringA, const char *stringB)
{
    // COMPARE EVERY CHARACTER SO THE TIME TAKEN DOES NOT LEAK HOW MUCH OF THE TOKEN MATCHED
    size_t lengthA = strlen(stringA);
    size_t lengthB = strlen(stringB);
    unsigned char difference = (unsigned char)(lengthA ^ lengthB);
    for (size_t index = 0; index < lengthA && index < lengthB; index++) {
        difference |= (unsigned char)(stringA[index] ^ stringB[index]);
    }
    return (difference == 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t sendText(httpd_req_t *request, const char *status, const char *message)
{
    httpd_resp_set_status(request, status);
    httpd_resp_set_type(request, "text/plain");
    return (httpd_resp_sendstr(request, message));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool isAuthorized(httpd_req_t *request)
{
    // REFUSE ANY ORIGIN HEADER, WHICH CLOSES THE DNS-REBINDING HOLE THE MCP TRANSPORT SPEC WARNS
    // ABOUT, SINCE COMMAND LINE CLIENTS SUCH AS CLAUDE CODE SEND NO ORIGIN
    if (httpd_req_get_hdr_value_len(request, "Origin") > 0) {
        sendText(request, "403 Forbidden", "browser origins are not accepted\n");
        return (false);
    }

    // REQUIRE THE BEARER TOKEN ON EVERY REQUEST
    char header[128] = "";
    if (httpd_req_get_hdr_value_str(request, "Authorization", header, sizeof(header)) != ESP_OK || !constantTimeEquals(header, authorizationString)) {
        httpd_resp_set_hdr(request, "WWW-Authenticate", "Bearer");
        sendText(request, "401 Unauthorized", "missing or wrong bearer token\n");
        return (false);
    }
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool receiveBody(httpd_req_t *request, LAUBodySink sink, void *context)
{
    // STREAM THE REQUEST BODY TO THE SINK IN 4 KB CHUNKS
    static char buffer[4096];
    int bytesLeft = request->content_len;
    while (bytesLeft > 0) {
        int bytesRead = httpd_req_recv(request, buffer, (bytesLeft < (int)sizeof(buffer)) ? bytesLeft : (int)sizeof(buffer));
        if (bytesRead == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        } else if (bytesRead <= 0) {
            return (false);
        }
        if (!sink(context, buffer, bytesRead)) {
            return (false);
        }
        bytesLeft -= bytesRead;
    }
    return (true);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t onMcpPost(httpd_req_t *request)
{
    if (!isAuthorized(request)) {
        return (ESP_OK);
    }
    if (request->content_len <= 0 || request->content_len > MCPMAXIMUMBODYSIZE) {
        return (sendText(request, "413 Payload Too Large", "JSON-RPC body must be 1..16384 bytes\n"));
    }

    // READ THE WHOLE JSON-RPC MESSAGE INTO MEMORY
    char *body = malloc(request->content_len + 1);
    if (body == NULL) {
        return (sendText(request, "500 Internal Server Error", "out of memory\n"));
    }
    int bytesReceived = 0;
    while (bytesReceived < request->content_len) {
        int bytesRead = httpd_req_recv(request, body + bytesReceived, request->content_len - bytesReceived);
        if (bytesRead == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        } else if (bytesRead <= 0) {
            free(body);
            return (ESP_FAIL);
        }
        bytesReceived += bytesRead;
    }
    body[bytesReceived] = 0;

    // A NULL RESPONSE MEANS THE MESSAGE WAS A NOTIFICATION
    char *response = lauMcpHandle(body);
    free(body);
    if (response == NULL) {
        httpd_resp_set_status(request, "202 Accepted");
        return (httpd_resp_send(request, NULL, 0));
    }
    httpd_resp_set_type(request, "application/json");
    esp_err_t error = httpd_resp_sendstr(request, response);
    free(response);
    return (error);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t onMcpOther(httpd_req_t *request)
{
    // A STATELESS SERVER HAS NO SSE STREAM FOR GET AND NO SESSIONS FOR DELETE TO END
    httpd_resp_set_hdr(request, "Allow", "POST");
    return (sendText(request, "405 Method Not Allowed", "POST JSON-RPC to /mcp\n"));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool fileSink(void *context, const char *buffer, int length)
{
    return (fwrite(buffer, 1, length, (FILE *)context) == (size_t)length);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t onBitstreamPut(httpd_req_t *request)
{
    if (!isAuthorized(request)) {
        return (ESP_OK);
    }
    if (!lauLibraryIsMounted()) {
        return (sendText(request, "503 Service Unavailable", "no SD card mounted\n"));
    }

    // SPLIT THE URI INTO A LIBRARY STEM AND AN EXTENSION OF EITHER BIT OR JSON
    const char *filename = request->uri + strlen("/bitstreams/");
    size_t filenameLength = strlen(filename);
    const char *extension = NULL;
    if (filenameLength > 4 && strcasecmp(filename + filenameLength - 4, ".bit") == 0) {
        extension = "bit";
    } else if (filenameLength > 5 && strcasecmp(filename + filenameLength - 5, ".json") == 0) {
        extension = "json";
    }
    char stem[72];
    char rawName[80];
    if (extension == NULL || filenameLength - strlen(extension) - 1 >= sizeof(rawName)) {
        return (sendText(request, "400 Bad Request", "PUT /bitstreams/<name>.bit or .json\n"));
    }
    memcpy(rawName, filename, filenameLength - strlen(extension) - 1);
    rawName[filenameLength - strlen(extension) - 1] = 0;
    if (!lauLibraryNormalizeName(rawName, stem, sizeof(stem))) {
        return (sendText(request, "400 Bad Request", "invalid name\n"));
    }
    if (strcmp(extension, "json") == 0 && request->content_len > 2048) {
        return (sendText(request, "413 Payload Too Large", "sidecar > 2 KB\n"));
    }

    // RECEIVE THE BODY INTO A TEMPORARY FILE
    char tempPath[128];
    lauLibraryTempPath(extension, tempPath, sizeof(tempPath));
    FILE *file = fopen(tempPath, "wb");
    if (file == NULL) {
        return (sendText(request, "500 Internal Server Error", "cannot create temp file\n"));
    }
    bool successFlag = receiveBody(request, fileSink, file);
    if (fclose(file) != 0) {
        successFlag = false;
    }
    if (!successFlag) {
        unlink(tempPath);
        return (sendText(request, "500 Internal Server Error", "receive or SD write failed\n"));
    }

    // A .BIT MUST PARSE AS A COMPLETE IMAGE BEFORE IT IS ALLOWED INTO THE LIBRARY
    if (strcmp(extension, "bit") == 0) {
        FILE *headerFile = fopen(tempPath, "rb");
        uint8_t header[512];
        size_t bytesRead = headerFile ? fread(header, 1, sizeof(header), headerFile) : 0;
        if (headerFile) {
            fclose(headerFile);
        }
        LAUBitFileInfo info;
        if (lauBitFileParse(header, bytesRead, &info) != 0 || (long)(info.dataOffset + info.dataLength) > request->content_len) {
            unlink(tempPath);
            return (sendText(request, "422 Unprocessable Entity", "not a complete Xilinx .bit (only .bit is accepted)\n"));
        }
    }
    if (lauLibraryInstall(tempPath, stem, extension) != ESP_OK) {
        return (sendText(request, "500 Internal Server Error", "install failed\n"));
    }
    ESP_LOGI(TAG, "onBitstreamPut() :: stored %s.%s (%d bytes)", stem, extension, request->content_len);
    return (sendText(request, "201 Created", "stored\n"));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static bool otaSink(void *context, const char *buffer, int length)
{
    return (esp_ota_write(*(esp_ota_handle_t *)context, buffer, length) == ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void rebootTask(void *argument)
{
    // GIVE THE HTTP RESPONSE TIME TO LEAVE BEFORE RESTARTING
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t onOtaPut(httpd_req_t *request)
{
    if (!isAuthorized(request)) {
        return (ESP_OK);
    }

    // WRITE THE NEW FIRMWARE INTO THE OTHER OTA SLOT, ROLLBACK HAPPENS IF IT NEVER MARKS ITSELF VALID
    const esp_partition_t *partition = esp_ota_get_next_update_partition(NULL);
    if (partition == NULL) {
        return (sendText(request, "500 Internal Server Error", "no OTA partition\n"));
    }
    esp_ota_handle_t handle;
    if (esp_ota_begin(partition, OTA_WITH_SEQUENTIAL_WRITES, &handle) != ESP_OK) {
        return (sendText(request, "500 Internal Server Error", "ota_begin failed\n"));
    }
    if (!receiveBody(request, otaSink, &handle)) {
        esp_ota_abort(handle);
        return (sendText(request, "500 Internal Server Error", "receive or flash write failed\n"));
    }
    if (esp_ota_end(handle) != ESP_OK) {
        return (sendText(request, "422 Unprocessable Entity", "image failed validation\n"));
    }
    if (esp_ota_set_boot_partition(partition) != ESP_OK) {
        return (sendText(request, "500 Internal Server Error", "set_boot_partition failed\n"));
    }
    sendText(request, "200 OK", "firmware written; rebooting\n");
    xTaskCreate(rebootTask, "reboot", 2048, NULL, 5, NULL);
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static esp_err_t onRootGet(httpd_req_t *request)
{
    return (sendText(request, "200 OK", "LAU ESP32 bitstream library. MCP: POST /mcp (bearer token required).\n"));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauServerStart(const char *bearerToken)
{
    snprintf(authorizationString, sizeof(authorizationString), "Bearer %s", bearerToken);

    // A LARGER STACK COVERS CJSON, FATFS, AND THE CALL DEPTH OF THE LOAD PATH
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 12288;
    config.uri_match_fn = httpd_uri_match_wildcard;
    config.recv_wait_timeout = 30;
    config.send_wait_timeout = 30;
    config.lru_purge_enable = true;

    httpd_handle_t server;
    esp_err_t error = httpd_start(&server, &config);
    if (error != ESP_OK) {
        return (error);
    }

    const httpd_uri_t handlers[] = {
        { .uri = "/mcp", .method = HTTP_POST, .handler = onMcpPost },
        { .uri = "/mcp", .method = HTTP_GET, .handler = onMcpOther },
        { .uri = "/mcp", .method = HTTP_DELETE, .handler = onMcpOther },
        { .uri = "/bitstreams/*", .method = HTTP_PUT, .handler = onBitstreamPut },
        { .uri = "/ota", .method = HTTP_PUT, .handler = onOtaPut },
        { .uri = "/", .method = HTTP_GET, .handler = onRootGet },
    };
    for (size_t index = 0; index < sizeof(handlers) / sizeof(handlers[0]); index++) {
        httpd_register_uri_handler(server, &handlers[index]);
    }
    ESP_LOGI(TAG, "lauServerStart() :: listening on port %d", config.server_port);
    return (ESP_OK);
}
