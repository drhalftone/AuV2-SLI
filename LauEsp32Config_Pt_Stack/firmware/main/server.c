#include "server.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "bitfile.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "library.h"
#include "mcp.h"

static const char *TAG = "server";
static char s_auth[96];   // "Bearer <token>"

#define MCP_MAX_BODY (16 * 1024)

// ---- helpers

static bool consteq(const char *a, const char *b)
{
    size_t la = strlen(a), lb = strlen(b);
    unsigned char d = (unsigned char)(la ^ lb);
    for (size_t i = 0; i < la && i < lb; i++) d |= (unsigned char)(a[i] ^ b[i]);
    return d == 0;
}

static esp_err_t send_text(httpd_req_t *req, const char *status, const char *msg)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "text/plain");
    return httpd_resp_sendstr(req, msg);
}

// Bearer token on every request; any Origin header is refused, which closes the DNS-rebinding
// hole the MCP transport spec warns about (CLI clients such as Claude Code send no Origin).
static bool authorized(httpd_req_t *req)
{
    if (httpd_req_get_hdr_value_len(req, "Origin") > 0) {
        send_text(req, "403 Forbidden", "browser origins are not accepted\n");
        return false;
    }
    char got[128] = "";
    if (httpd_req_get_hdr_value_str(req, "Authorization", got, sizeof(got)) != ESP_OK || !consteq(got, s_auth)) {
        httpd_resp_set_hdr(req, "WWW-Authenticate", "Bearer");
        send_text(req, "401 Unauthorized", "missing or wrong bearer token\n");
        return false;
    }
    return true;
}

// Stream the request body to `write(chunk)`; false on socket error or when write refuses.
typedef bool (*sink_fn)(void *ctx, const char *buf, int n);
static bool recv_body(httpd_req_t *req, sink_fn sink, void *ctx)
{
    static char buf[4096];
    int left = req->content_len;
    while (left > 0) {
        int n = httpd_req_recv(req, buf, left < (int)sizeof(buf) ? left : (int)sizeof(buf));
        if (n == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (n <= 0) return false;
        if (!sink(ctx, buf, n)) return false;
        left -= n;
    }
    return true;
}

// ---- POST /mcp

static esp_err_t mcp_post(httpd_req_t *req)
{
    if (!authorized(req)) return ESP_OK;
    if (req->content_len <= 0 || req->content_len > MCP_MAX_BODY) {
        return send_text(req, "413 Payload Too Large", "JSON-RPC body must be 1..16384 bytes\n");
    }
    char *body = malloc(req->content_len + 1);
    if (!body) return send_text(req, "500 Internal Server Error", "out of memory\n");
    int got = 0;
    while (got < req->content_len) {
        int n = httpd_req_recv(req, body + got, req->content_len - got);
        if (n == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (n <= 0) { free(body); return ESP_FAIL; }
        got += n;
    }
    body[got] = 0;

    char *resp = mcp_handle(body);
    free(body);
    if (!resp) {
        httpd_resp_set_status(req, "202 Accepted");
        return httpd_resp_send(req, NULL, 0);
    }
    httpd_resp_set_type(req, "application/json");
    esp_err_t err = httpd_resp_sendstr(req, resp);
    free(resp);
    return err;
}

static esp_err_t mcp_other(httpd_req_t *req)
{
    // Stateless server: no SSE stream (GET) and no sessions to end (DELETE).
    httpd_resp_set_hdr(req, "Allow", "POST");
    return send_text(req, "405 Method Not Allowed", "POST JSON-RPC to /mcp\n");
}

// ---- PUT /bitstreams/<name>.bit | <name>.json  (scripted upload, §6.3 "push")

static bool file_sink(void *ctx, const char *buf, int n) { return fwrite(buf, 1, n, (FILE *)ctx) == (size_t)n; }

static esp_err_t bitstream_put(httpd_req_t *req)
{
    if (!authorized(req)) return ESP_OK;
    if (!lib_mounted()) return send_text(req, "503 Service Unavailable", "no SD card mounted\n");

    const char *fname = req->uri + strlen("/bitstreams/");
    size_t fl = strlen(fname);
    const char *ext = NULL;
    if (fl > 4 && strcasecmp(fname + fl - 4, ".bit") == 0) ext = "bit";
    else if (fl > 5 && strcasecmp(fname + fl - 5, ".json") == 0) ext = "json";
    char stem[72], raw[80];
    if (!ext || fl - strlen(ext) - 1 >= sizeof(raw)) return send_text(req, "400 Bad Request", "PUT /bitstreams/<name>.bit or .json\n");
    memcpy(raw, fname, fl - strlen(ext) - 1);
    raw[fl - strlen(ext) - 1] = 0;
    if (!lib_normalize_name(raw, stem, sizeof(stem))) return send_text(req, "400 Bad Request", "invalid name\n");
    if (strcmp(ext, "json") == 0 && req->content_len > 2048) return send_text(req, "413 Payload Too Large", "sidecar > 2 KB\n");

    char tmp[128];
    lib_temp_path(ext, tmp, sizeof(tmp));
    FILE *f = fopen(tmp, "wb");
    if (!f) return send_text(req, "500 Internal Server Error", "cannot create temp file\n");
    bool ok = recv_body(req, file_sink, f);
    if (fclose(f) != 0) ok = false;
    if (!ok) {
        unlink(tmp);
        return send_text(req, "500 Internal Server Error", "receive or SD write failed\n");
    }
    if (strcmp(ext, "bit") == 0) {
        FILE *h = fopen(tmp, "rb");
        uint8_t hdr[512];
        size_t got = h ? fread(hdr, 1, sizeof(hdr), h) : 0;
        if (h) fclose(h);
        bitfile_info_t info;
        if (bitfile_parse(hdr, got, &info) != 0 || (long)(info.data_offset + info.data_len) > req->content_len) {
            unlink(tmp);
            return send_text(req, "422 Unprocessable Entity", "not a complete Xilinx .bit (only .bit is accepted)\n");
        }
    }
    if (lib_install(tmp, stem, ext) != ESP_OK) return send_text(req, "500 Internal Server Error", "install failed\n");
    ESP_LOGI(TAG, "stored %s.%s (%d bytes)", stem, ext, req->content_len);
    return send_text(req, "201 Created", "stored\n");
}

// ---- PUT /ota  (new ESP32 firmware; rollback if it never marks itself valid)

static bool ota_sink(void *ctx, const char *buf, int n) { return esp_ota_write(*(esp_ota_handle_t *)ctx, buf, n) == ESP_OK; }

static void reboot_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

static esp_err_t ota_put(httpd_req_t *req)
{
    if (!authorized(req)) return ESP_OK;
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) return send_text(req, "500 Internal Server Error", "no OTA partition\n");
    esp_ota_handle_t h;
    if (esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &h) != ESP_OK) return send_text(req, "500 Internal Server Error", "ota_begin failed\n");
    if (!recv_body(req, ota_sink, &h)) {
        esp_ota_abort(h);
        return send_text(req, "500 Internal Server Error", "receive or flash write failed\n");
    }
    if (esp_ota_end(h) != ESP_OK) return send_text(req, "422 Unprocessable Entity", "image failed validation\n");
    if (esp_ota_set_boot_partition(part) != ESP_OK) return send_text(req, "500 Internal Server Error", "set_boot_partition failed\n");
    send_text(req, "200 OK", "firmware written; rebooting\n");
    xTaskCreate(reboot_task, "reboot", 2048, NULL, 5, NULL);
    return ESP_OK;
}

// ---- GET /

static esp_err_t root_get(httpd_req_t *req)
{
    return send_text(req, "200 OK", "LAU ESP32 bitstream library. MCP: POST /mcp (bearer token required).\n");
}

esp_err_t server_start(const char *bearer_token)
{
    snprintf(s_auth, sizeof(s_auth), "Bearer %s", bearer_token);

    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.stack_size = 12288;              // cJSON + FATFS + the load path's call depth
    cfg.uri_match_fn = httpd_uri_match_wildcard;
    cfg.recv_wait_timeout = 30;
    cfg.send_wait_timeout = 30;
    cfg.lru_purge_enable = true;

    httpd_handle_t srv;
    esp_err_t err = httpd_start(&srv, &cfg);
    if (err != ESP_OK) return err;

    const httpd_uri_t uris[] = {
        {.uri = "/mcp", .method = HTTP_POST, .handler = mcp_post},
        {.uri = "/mcp", .method = HTTP_GET, .handler = mcp_other},
        {.uri = "/mcp", .method = HTTP_DELETE, .handler = mcp_other},
        {.uri = "/bitstreams/*", .method = HTTP_PUT, .handler = bitstream_put},
        {.uri = "/ota", .method = HTTP_PUT, .handler = ota_put},
        {.uri = "/", .method = HTTP_GET, .handler = root_get},
    };
    for (size_t i = 0; i < sizeof(uris) / sizeof(uris[0]); i++) httpd_register_uri_handler(srv, &uris[i]);
    ESP_LOGI(TAG, "listening on :%d", cfg.server_port);
    return ESP_OK;
}
