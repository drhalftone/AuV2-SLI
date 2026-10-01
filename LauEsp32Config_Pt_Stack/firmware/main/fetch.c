#include "fetch.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "library.h"

static const char *TAG = "fetch";

// Download url into `dest`. Returns HTTP status (200 on success), or -1 on transport/write error.
static int download(const char *url, const char *dest, long *bytes_out)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .timeout_ms = 15000,
        .crt_bundle_attach = esp_crt_bundle_attach,   // https works too; LAN http is the norm
        .buffer_size = 4096,
    };
    esp_http_client_handle_t c = esp_http_client_init(&cfg);
    if (!c) return -1;
    int status = -1;
    long total = 0;
    if (esp_http_client_open(c, 0) == ESP_OK) {
        esp_http_client_fetch_headers(c);
        status = esp_http_client_get_status_code(c);
        if (status == 200) {
            FILE *f = fopen(dest, "wb");
            if (!f) {
                status = -1;
            } else {
                static char buf[4096];
                int n;
                while ((n = esp_http_client_read(c, buf, sizeof(buf))) > 0) {
                    if (fwrite(buf, 1, n, f) != (size_t)n) { status = -1; break; }
                    total += n;
                }
                if (n < 0) status = -1;
                if (fclose(f) != 0) status = -1;
                if (status != 200) unlink(dest);
            }
        }
    }
    esp_http_client_close(c);
    esp_http_client_cleanup(c);
    if (bytes_out) *bytes_out = total;
    return status;
}

static cJSON *fail(cJSON *o, bool *is_error, const char *msg)
{
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", msg);
    *is_error = true;
    ESP_LOGW(TAG, "%s", msg);
    return o;
}

cJSON *fetch_bitstream(const char *url, const char *name, bool *is_error)
{
    *is_error = false;
    cJSON *o = cJSON_CreateObject();
    if (!lib_mounted()) return fail(o, is_error, "no SD card mounted");
    if (!url || (strncmp(url, "http://", 7) != 0 && strncmp(url, "https://", 8) != 0)) {
        return fail(o, is_error, "url must start with http:// or https://");
    }

    // Name: explicit, or the URL's last path segment.
    char stem[72];
    const char *base = strrchr(url, '/');
    base = base ? base + 1 : url;
    if (!lib_normalize_name(name && name[0] ? name : base, stem, sizeof(stem))) {
        return fail(o, is_error, "could not derive a valid image name; pass 'name' ([A-Za-z0-9._-], up to 64 chars)");
    }
    cJSON_AddStringToObject(o, "name", stem);

    char tmp[128];
    lib_temp_path("bit", tmp, sizeof(tmp));
    int64_t t0 = esp_timer_get_time();
    long bytes = 0;
    int status = download(url, tmp, &bytes);
    if (status != 200) {
        char msg[96];
        snprintf(msg, sizeof(msg), status < 0 ? "download failed (network or SD write error)" : "server returned HTTP %d", status);
        return fail(o, is_error, msg);
    }

    // It must parse as a .bit before it is allowed into the library.
    FILE *f = fopen(tmp, "rb");
    uint8_t hdr[512];
    size_t got = f ? fread(hdr, 1, sizeof(hdr), f) : 0;
    if (f) fclose(f);
    bitfile_info_t info;
    if (bitfile_parse(hdr, got, &info) != 0 || (long)(info.data_offset + info.data_len) > bytes) {
        unlink(tmp);
        return fail(o, is_error, "downloaded file is not a complete Xilinx .bit (only .bit is accepted, not .bin)");
    }
    if (lib_install(tmp, stem, "bit") != ESP_OK) return fail(o, is_error, "could not move the image into the library");

    // Optional sidecar beside it on the server: same URL with .bit -> .json.
    size_t ul = strlen(url);
    if (ul > 4 && ul < 400 && strcasecmp(url + ul - 4, ".bit") == 0) {
        char surl[408];
        memcpy(surl, url, ul - 4);
        strcpy(surl + ul - 4, ".json");
        char stmp[128];
        lib_temp_path("json", stmp, sizeof(stmp));
        if (download(surl, stmp, NULL) == 200 && lib_install(stmp, stem, "json") == ESP_OK) {
            cJSON_AddBoolToObject(o, "sidecar", true);
        } else {
            cJSON_AddBoolToObject(o, "sidecar", false);
        }
    }

    cJSON_AddNumberToObject(o, "size_bytes", (double)bytes);
    cJSON_AddStringToObject(o, "part", info.part);
    cJSON_AddStringToObject(o, "design", info.design);
    cJSON_AddNumberToObject(o, "seconds", (esp_timer_get_time() - t0) / 1e6);
    cJSON_AddBoolToObject(o, "ok", true);
    return o;
}
