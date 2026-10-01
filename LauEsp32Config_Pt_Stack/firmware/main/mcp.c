#include "mcp.h"

#include <string.h>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "fetch.h"
#include "fpga.h"
#include "library.h"

static const char *TAG = "mcp";

// Newest first; we answer with the client's version when we support it, else the newest.
static const char *k_versions[] = {"2025-06-18", "2025-03-26"};

static const char *k_instructions =
    "This server is an ESP32 inside an Alchitry Pt V2 FPGA camera stack. It keeps a library of "
    "precompiled Xilinx .bit images on a microSD card and loads them into the FPGA's configuration "
    "RAM over JTAG. Loading never writes the FPGA's boot flash: a power cycle, or "
    "restore_flash_image, always brings back the camera's shipped image. To add an image, serve "
    "the build output directory over HTTP on the LAN and call fetch_bitstream with its URL.";

// Tool definitions, as the client sees them. Kept as one JSON literal so the descriptions read
// like documentation.
static const char *k_tools_json =
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

static cJSON *s_tools;   // parsed once

// ---- JSON-RPC envelope helpers

static char *respond(const cJSON *id, cJSON *result)
{
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "jsonrpc", "2.0");
    cJSON_AddItemToObject(r, "id", id ? cJSON_Duplicate(id, 1) : cJSON_CreateNull());
    cJSON_AddItemToObject(r, "result", result);
    char *s = cJSON_PrintUnformatted(r);
    cJSON_Delete(r);
    return s;
}

static char *respond_error(const cJSON *id, int code, const char *msg)
{
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "jsonrpc", "2.0");
    cJSON_AddItemToObject(r, "id", id ? cJSON_Duplicate(id, 1) : cJSON_CreateNull());
    cJSON *e = cJSON_AddObjectToObject(r, "error");
    cJSON_AddNumberToObject(e, "code", code);
    cJSON_AddStringToObject(e, "message", msg);
    char *s = cJSON_PrintUnformatted(r);
    cJSON_Delete(r);
    return s;
}

// A tool result: the payload object rendered as JSON text content.
static cJSON *tool_result(cJSON *payload, bool is_error)
{
    cJSON *res = cJSON_CreateObject();
    cJSON *content = cJSON_AddArrayToObject(res, "content");
    cJSON *item = cJSON_CreateObject();
    cJSON_AddStringToObject(item, "type", "text");
    char *txt = cJSON_PrintUnformatted(payload);
    cJSON_AddStringToObject(item, "text", txt ? txt : "{}");
    free(txt);
    cJSON_AddItemToArray(content, item);
    cJSON_AddBoolToObject(res, "isError", is_error);
    cJSON_Delete(payload);
    return res;
}

static cJSON *tool_error(const char *msg)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", msg);
    return tool_result(o, true);
}

static const char *arg_str(const cJSON *args, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(args, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

// ---- methods

static cJSON *do_initialize(const cJSON *params)
{
    const char *want = arg_str(params, "protocolVersion");
    const char *ver = k_versions[0];
    for (size_t i = 0; want && i < sizeof(k_versions) / sizeof(k_versions[0]); i++) {
        if (strcmp(want, k_versions[i]) == 0) ver = k_versions[i];
    }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "protocolVersion", ver);
    cJSON *caps = cJSON_AddObjectToObject(r, "capabilities");
    cJSON *tools = cJSON_AddObjectToObject(caps, "tools");
    cJSON_AddBoolToObject(tools, "listChanged", false);
    cJSON *info = cJSON_AddObjectToObject(r, "serverInfo");
    cJSON_AddStringToObject(info, "name", "lau-esp32-bitstream-library");
    cJSON_AddStringToObject(info, "version", esp_app_get_description()->version);
    cJSON_AddStringToObject(r, "instructions", k_instructions);
    return r;
}

static cJSON *do_tools_call(const cJSON *params)
{
    const char *name = arg_str(params, "name");
    const cJSON *args = cJSON_GetObjectItemCaseSensitive(params, "arguments");
    if (!name) return tool_error("missing tool name");
    ESP_LOGI(TAG, "tools/call %s", name);
    bool is_error = false;

    if (strcmp(name, "list_bitstreams") == 0) {
        if (!lib_mounted()) return tool_error("no SD card mounted");
        cJSON *o = cJSON_CreateObject();
        cJSON_AddItemToObject(o, "images", lib_list());
        return tool_result(o, false);
    }
    if (strcmp(name, "get_fpga_status") == 0) return tool_result(fpga_status(), false);
    if (strcmp(name, "get_card_info") == 0) return tool_result(lib_card_info(), false);
    if (strcmp(name, "restore_flash_image") == 0) {
        cJSON *o = fpga_restore_flash(&is_error);
        return tool_result(o, is_error);
    }

    char stem[72];
    if (strcmp(name, "load_bitstream") == 0 || strcmp(name, "delete_bitstream") == 0) {
        if (!lib_normalize_name(arg_str(args, "name"), stem, sizeof(stem))) {
            return tool_error("'name' is missing or not a valid image name ([A-Za-z0-9._-], up to 64 chars)");
        }
        if (name[0] == 'l') return tool_result(fpga_load(stem, &is_error), is_error);
        if (!lib_mounted()) return tool_error("no SD card mounted");
        if (lib_delete(stem) != ESP_OK) return tool_error("no image by that name");
        cJSON *o = cJSON_CreateObject();
        cJSON_AddBoolToObject(o, "ok", true);
        cJSON_AddStringToObject(o, "deleted", stem);
        return tool_result(o, false);
    }
    if (strcmp(name, "fetch_bitstream") == 0) {
        cJSON *o = fetch_bitstream(arg_str(args, "url"), arg_str(args, "name"), &is_error);
        return tool_result(o, is_error);
    }
    return NULL;   // unknown tool -> protocol error
}

char *mcp_handle(const char *body)
{
    if (!s_tools) s_tools = cJSON_Parse(k_tools_json);

    cJSON *req = cJSON_Parse(body);
    if (!req) return respond_error(NULL, -32700, "parse error");
    char *out = NULL;
    if (!cJSON_IsObject(req)) {
        out = respond_error(NULL, -32600, "batches are not supported; send one JSON-RPC object");
        goto done;
    }
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(req, "id");
    const char *method = arg_str(req, "method");
    const cJSON *params = cJSON_GetObjectItemCaseSensitive(req, "params");

    if (!id) {           // notification (e.g. notifications/initialized): nothing to say
        goto done;
    }
    if (!method) {
        out = respond_error(id, -32600, "missing method");
    } else if (strcmp(method, "initialize") == 0) {
        out = respond(id, do_initialize(params));
    } else if (strcmp(method, "ping") == 0) {
        out = respond(id, cJSON_CreateObject());
    } else if (strcmp(method, "tools/list") == 0) {
        cJSON *r = cJSON_CreateObject();
        cJSON_AddItemToObject(r, "tools", cJSON_Duplicate(s_tools, 1));
        out = respond(id, r);
    } else if (strcmp(method, "tools/call") == 0) {
        cJSON *r = do_tools_call(params);
        out = r ? respond(id, r) : respond_error(id, -32602, "unknown tool");
    } else {
        out = respond_error(id, -32601, "method not found");
    }
done:
    cJSON_Delete(req);
    return out;
}
