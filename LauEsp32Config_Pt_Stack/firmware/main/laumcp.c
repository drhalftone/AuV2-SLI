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

#include "laumcp.h"
#include "laufetch.h"
#include "laufpga.h"
#include "laulibrary.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"

static const char *TAG = "LAUMcp";

// PROTOCOL VERSIONS NEWEST FIRST, ANSWER WITH THE CLIENT'S VERSION WHEN WE SUPPORT IT
static const char *protocolVersions[] = { "2025-06-18", "2025-03-26" };

static const char *serverInstructions =
    "This server is an ESP32 inside an Alchitry Pt V2 FPGA camera stack. It keeps a library of "
    "precompiled Xilinx .bit images on a microSD card and loads them into the FPGA's configuration "
    "RAM over JTAG. Loading never writes the FPGA's boot flash: a power cycle, or "
    "restore_flash_image, always brings back the camera's shipped image. To add an image, serve "
    "the build output directory over HTTP on the LAN and call fetch_bitstream with its URL.";

// THE TOOL DEFINITIONS AS THE CLIENT SEES THEM, KEPT AS ONE JSON LITERAL SO THE DESCRIPTIONS
// READ LIKE DOCUMENTATION
static const char *toolsJson =
"["
"{\"name\":\"list_bitstreams\","
 "\"description\":\"List every FPGA image in the SD card library: name, size, target part, design name, "
 "build date/time from the .bit header, plus description and git commit when a sidecar .json exists.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}},"
"{\"name\":\"get_fpga_status\","
 "\"description\":\"Report the FPGA's state: DONE pin, JTAG IDCODE and part, chain length, and the last image "
 "this card loaded (unknown if the FPGA booted from its own flash). Briefly takes JTAG.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}},"
"{\"name\":\"load_bitstream\","
 "\"description\":\"Load a library image into the FPGA's configuration RAM over JTAG (a few seconds; blocks "
 "until done). Before touching the FPGA it checks the image's target part against the FPGA's IDCODE and its "
 "sha256 against the sidecar, if any. On failure after programming starts the FPGA is left unconfigured: "
 "call restore_flash_image. Replaces whatever design is running, including the camera image.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\","
 "\"description\":\"Image name as returned by list_bitstreams (with or without .bit)\"}},"
 "\"required\":[\"name\"]}},"
"{\"name\":\"restore_flash_image\","
 "\"description\":\"Pulse the FPGA's PROGRAM_B so it reconfigures from its own QSPI flash, restoring the shipped "
 "camera image. Use this to recover from a bad or failed load without a power cycle.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}},"
"{\"name\":\"fetch_bitstream\","
 "\"description\":\"Download a Xilinx .bit (not .bin) from an http(s) URL on the LAN onto the SD card, replacing "
 "any image of the same name. Also fetches a sidecar at the same URL with .json in place of .bit, if present. "
 "Typical use: run 'python -m http.server' in the build output directory, then pass http://<build-pc>:8000/<file>.bit.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{"
 "\"url\":{\"type\":\"string\",\"description\":\"http:// or https:// URL of the .bit file\"},"
 "\"name\":{\"type\":\"string\",\"description\":\"Library name to store it under; defaults to the URL's file name\"}},"
 "\"required\":[\"url\"]}},"
"{\"name\":\"delete_bitstream\","
 "\"description\":\"Delete an image and its sidecar from the SD card library.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{\"name\":{\"type\":\"string\"}},\"required\":[\"name\"]}},"
"{\"name\":\"get_card_info\","
 "\"description\":\"SD card state: mounted, capacity, free space, number of images.\","
 "\"inputSchema\":{\"type\":\"object\",\"properties\":{}}}"
"]";

static cJSON *toolsArray = NULL;

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static char *buildResponse(const cJSON *id, cJSON *result)
{
    cJSON *response = cJSON_CreateObject();
    cJSON_AddStringToObject(response, "jsonrpc", "2.0");
    cJSON_AddItemToObject(response, "id", id ? cJSON_Duplicate(id, 1) : cJSON_CreateNull());
    cJSON_AddItemToObject(response, "result", result);
    char *string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);
    return (string);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static char *buildErrorResponse(const cJSON *id, int code, const char *message)
{
    cJSON *response = cJSON_CreateObject();
    cJSON_AddStringToObject(response, "jsonrpc", "2.0");
    cJSON_AddItemToObject(response, "id", id ? cJSON_Duplicate(id, 1) : cJSON_CreateNull());
    cJSON *error = cJSON_AddObjectToObject(response, "error");
    cJSON_AddNumberToObject(error, "code", code);
    cJSON_AddStringToObject(error, "message", message);
    char *string = cJSON_PrintUnformatted(response);
    cJSON_Delete(response);
    return (string);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *buildToolResult(cJSON *payload, bool errorFlag)
{
    // RENDER THE PAYLOAD OBJECT AS JSON TEXT CONTENT AND TAKE OWNERSHIP OF IT
    cJSON *result = cJSON_CreateObject();
    cJSON *content = cJSON_AddArrayToObject(result, "content");
    cJSON *item = cJSON_CreateObject();
    cJSON_AddStringToObject(item, "type", "text");
    char *text = cJSON_PrintUnformatted(payload);
    cJSON_AddStringToObject(item, "text", text ? text : "{}");
    free(text);
    cJSON_AddItemToArray(content, item);
    cJSON_AddBoolToObject(result, "isError", errorFlag);
    cJSON_Delete(payload);
    return (result);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *buildToolError(const char *message)
{
    cJSON *object = cJSON_CreateObject();
    cJSON_AddBoolToObject(object, "ok", false);
    cJSON_AddStringToObject(object, "error", message);
    return (buildToolResult(object, true));
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static const char *stringArgument(const cJSON *arguments, const char *key)
{
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(arguments, key);
    return (cJSON_IsString(value) ? value->valuestring : NULL);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *handleInitialize(const cJSON *params)
{
    // NEGOTIATE THE PROTOCOL VERSION
    const char *requestedVersion = stringArgument(params, "protocolVersion");
    const char *version = protocolVersions[0];
    for (size_t index = 0; requestedVersion && index < sizeof(protocolVersions) / sizeof(protocolVersions[0]); index++) {
        if (strcmp(requestedVersion, protocolVersions[index]) == 0) {
            version = protocolVersions[index];
        }
    }

    cJSON *result = cJSON_CreateObject();
    cJSON_AddStringToObject(result, "protocolVersion", version);
    cJSON *capabilities = cJSON_AddObjectToObject(result, "capabilities");
    cJSON *tools = cJSON_AddObjectToObject(capabilities, "tools");
    cJSON_AddBoolToObject(tools, "listChanged", false);
    cJSON *serverInfo = cJSON_AddObjectToObject(result, "serverInfo");
    cJSON_AddStringToObject(serverInfo, "name", "lau-esp32-bitstream-library");
    cJSON_AddStringToObject(serverInfo, "version", esp_app_get_description()->version);
    cJSON_AddStringToObject(result, "instructions", serverInstructions);
    return (result);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static cJSON *handleToolsCall(const cJSON *params)
{
    const char *toolName = stringArgument(params, "name");
    const cJSON *arguments = cJSON_GetObjectItemCaseSensitive(params, "arguments");
    if (toolName == NULL) {
        return (buildToolError("missing tool name"));
    }
    ESP_LOGI(TAG, "handleToolsCall() :: %s", toolName);

    bool errorFlag = false;
    char stem[72];
    if (strcmp(toolName, "list_bitstreams") == 0) {
        if (!lauLibraryIsMounted()) {
            return (buildToolError("no SD card mounted"));
        }
        cJSON *object = cJSON_CreateObject();
        cJSON_AddItemToObject(object, "images", lauLibraryList());
        return (buildToolResult(object, false));
    } else if (strcmp(toolName, "get_fpga_status") == 0) {
        return (buildToolResult(lauFpgaStatus(), false));
    } else if (strcmp(toolName, "get_card_info") == 0) {
        return (buildToolResult(lauLibraryCardInfo(), false));
    } else if (strcmp(toolName, "restore_flash_image") == 0) {
        cJSON *object = lauFpgaRestoreFlash(&errorFlag);
        return (buildToolResult(object, errorFlag));
    } else if (strcmp(toolName, "load_bitstream") == 0) {
        if (!lauLibraryNormalizeName(stringArgument(arguments, "name"), stem, sizeof(stem))) {
            return (buildToolError("'name' is missing or not a valid image name ([A-Za-z0-9._-], up to 64 chars)"));
        }
        cJSON *object = lauFpgaLoad(stem, &errorFlag);
        return (buildToolResult(object, errorFlag));
    } else if (strcmp(toolName, "delete_bitstream") == 0) {
        if (!lauLibraryNormalizeName(stringArgument(arguments, "name"), stem, sizeof(stem))) {
            return (buildToolError("'name' is missing or not a valid image name ([A-Za-z0-9._-], up to 64 chars)"));
        }
        if (!lauLibraryIsMounted()) {
            return (buildToolError("no SD card mounted"));
        }
        if (lauLibraryDelete(stem) != ESP_OK) {
            return (buildToolError("no image by that name"));
        }
        cJSON *object = cJSON_CreateObject();
        cJSON_AddBoolToObject(object, "ok", true);
        cJSON_AddStringToObject(object, "deleted", stem);
        return (buildToolResult(object, false));
    } else if (strcmp(toolName, "fetch_bitstream") == 0) {
        cJSON *object = lauFetchBitstream(stringArgument(arguments, "url"), stringArgument(arguments, "name"), &errorFlag);
        return (buildToolResult(object, errorFlag));
    }

    // AN UNKNOWN TOOL IS A PROTOCOL ERROR RATHER THAN A TOOL ERROR
    return (NULL);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
char *lauMcpHandle(const char *body)
{
    if (toolsArray == NULL) {
        toolsArray = cJSON_Parse(toolsJson);
    }

    cJSON *request = cJSON_Parse(body);
    if (request == NULL) {
        return (buildErrorResponse(NULL, -32700, "parse error"));
    }

    char *response = NULL;
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(request, "id");
    const char *method = stringArgument(request, "method");
    const cJSON *params = cJSON_GetObjectItemCaseSensitive(request, "params");
    if (!cJSON_IsObject(request)) {
        response = buildErrorResponse(NULL, -32600, "batches are not supported; send one JSON-RPC object");
    } else if (id == NULL) {
        // A NOTIFICATION SUCH AS NOTIFICATIONS/INITIALIZED NEEDS NO REPLY
        response = NULL;
    } else if (method == NULL) {
        response = buildErrorResponse(id, -32600, "missing method");
    } else if (strcmp(method, "initialize") == 0) {
        response = buildResponse(id, handleInitialize(params));
    } else if (strcmp(method, "ping") == 0) {
        response = buildResponse(id, cJSON_CreateObject());
    } else if (strcmp(method, "tools/list") == 0) {
        cJSON *result = cJSON_CreateObject();
        cJSON_AddItemToObject(result, "tools", cJSON_Duplicate(toolsArray, 1));
        response = buildResponse(id, result);
    } else if (strcmp(method, "tools/call") == 0) {
        cJSON *result = handleToolsCall(params);
        if (result) {
            response = buildResponse(id, result);
        } else {
            response = buildErrorResponse(id, -32602, "unknown tool");
        }
    } else {
        response = buildErrorResponse(id, -32601, "method not found");
    }
    cJSON_Delete(request);
    return (response);
}
