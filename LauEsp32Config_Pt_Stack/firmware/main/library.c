#include "library.h"

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
#include "pins.h"
#include "sdmmc_cmd.h"

static const char *TAG = "library";
static sdmmc_card_t *s_card;

esp_err_t lib_mount(void)
{
    gpio_config_t cd = {.pin_bit_mask = 1ULL << PIN_SD_CD_N, .mode = GPIO_MODE_INPUT};
    gpio_config(&cd);   // R36 is the pull-up

    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    host.max_freq_khz = SDMMC_FREQ_DEFAULT;   // 20 MHz for bring-up; HIGHSPEED once proven

    sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
    slot.width = 4;
    slot.clk = PIN_SD_CLK;
    slot.cmd = PIN_SD_CMD;
    slot.d0 = PIN_SD_D0;
    slot.d1 = PIN_SD_D1;
    slot.d2 = PIN_SD_D2;
    slot.d3 = PIN_SD_D3;
    // slot.cd is deliberately NOT set: the driver assumes active-low and the socket's detect
    // polarity has not been checked on hardware. Try the mount and report the raw level instead.

    esp_vfs_fat_sdmmc_mount_config_t mc = {
        .format_if_mount_failed = false,   // never wipe a library because of a flaky contact
        .max_files = 4,
        .allocation_unit_size = 16 * 1024,
    };
    esp_err_t err = esp_vfs_fat_sdmmc_mount(LIB_MOUNT, &host, &slot, &mc, &s_card);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "SD mount failed: %s (CD_N level %d)", esp_err_to_name(err), lib_card_detect_level());
        s_card = NULL;
        return err;
    }
    sdmmc_card_print_info(stdout, s_card);
    mkdir(LIB_DIR, 0775);
    return ESP_OK;
}

bool lib_mounted(void) { return s_card != NULL; }

int lib_card_detect_level(void) { return gpio_get_level(PIN_SD_CD_N); }

bool lib_normalize_name(const char *in, char *stem, size_t cap)
{
    if (!in) return false;
    size_t n = strlen(in);
    if (n > 4 && strcasecmp(in + n - 4, ".bit") == 0) n -= 4;
    if (n == 0 || n > 64 || n >= cap || in[0] == '.') return false;
    for (size_t i = 0; i < n; i++) {
        char c = in[i];
        if (!(isalnum((unsigned char)c) || c == '_' || c == '-' || c == '.')) return false;
    }
    memcpy(stem, in, n);
    stem[n] = 0;
    return true;
}

void lib_path(const char *stem, const char *ext, char *out, size_t cap)
{
    snprintf(out, cap, LIB_DIR "/%s.%s", stem, ext);
}

esp_err_t lib_read_header(const char *stem, bitfile_info_t *info, long *file_size)
{
    char path[128];
    lib_path(stem, "bit", path, sizeof(path));
    FILE *f = fopen(path, "rb");
    if (!f) return ESP_ERR_NOT_FOUND;
    uint8_t buf[512];
    size_t got = fread(buf, 1, sizeof(buf), f);
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fclose(f);
    if (file_size) *file_size = size;
    if (bitfile_parse(buf, got, info) != 0) return ESP_ERR_INVALID_RESPONSE;
    if ((long)info->data_offset + (long)info->data_len > size) return ESP_ERR_INVALID_SIZE;
    return ESP_OK;
}

static void copy_str(char *dst, size_t cap, const cJSON *obj, const char *key)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    if (cJSON_IsString(v) && v->valuestring) {
        strncpy(dst, v->valuestring, cap - 1);
        dst[cap - 1] = 0;
    }
}

bool lib_read_sidecar(const char *stem, lib_sidecar_t *sc)
{
    memset(sc, 0, sizeof(*sc));
    char path[128];
    lib_path(stem, "json", path, sizeof(path));
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    char buf[2048];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    cJSON *j = cJSON_Parse(buf);
    if (!j) {
        ESP_LOGW(TAG, "%s: sidecar is not valid JSON, ignored", stem);
        return false;
    }
    copy_str(sc->description, sizeof(sc->description), j, "description");
    copy_str(sc->git_commit, sizeof(sc->git_commit), j, "git_commit");
    copy_str(sc->sha256, sizeof(sc->sha256), j, "sha256");
    copy_str(sc->source_script, sizeof(sc->source_script), j, "source_script");
    cJSON_Delete(j);
    return true;
}

esp_err_t lib_sha256_file(const char *path, char hex_out[65])
{
    FILE *f = fopen(path, "rb");
    if (!f) return ESP_ERR_NOT_FOUND;
    static uint8_t buf[4096];
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) mbedtls_sha256_update(&ctx, buf, n);
    bool err = ferror(f);
    fclose(f);
    uint8_t d[32];
    mbedtls_sha256_finish(&ctx, d);
    mbedtls_sha256_free(&ctx);
    if (err) return ESP_FAIL;
    for (int i = 0; i < 32; i++) sprintf(hex_out + 2 * i, "%02x", d[i]);
    hex_out[64] = 0;
    return ESP_OK;
}

cJSON *lib_list(void)
{
    cJSON *arr = cJSON_CreateArray();
    DIR *d = opendir(LIB_DIR);
    if (!d) return arr;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        size_t n = strlen(e->d_name);
        if (n < 5 || strcasecmp(e->d_name + n - 4, ".bit") != 0) continue;
        char stem[72];
        if (!lib_normalize_name(e->d_name, stem, sizeof(stem))) continue;

        cJSON *o = cJSON_CreateObject();
        cJSON_AddStringToObject(o, "name", stem);
        bitfile_info_t info;
        long size = 0;
        esp_err_t herr = lib_read_header(stem, &info, &size);
        cJSON_AddNumberToObject(o, "size_bytes", (double)size);
        if (herr == ESP_OK) {
            cJSON_AddStringToObject(o, "part", info.part);
            cJSON_AddStringToObject(o, "design", info.design);
            cJSON_AddStringToObject(o, "build_date", info.date);
            cJSON_AddStringToObject(o, "build_time", info.time);
        } else {
            cJSON_AddStringToObject(o, "error", "unreadable .bit header -- will not be loaded");
        }
        lib_sidecar_t sc;
        if (lib_read_sidecar(stem, &sc)) {
            if (sc.description[0]) cJSON_AddStringToObject(o, "description", sc.description);
            if (sc.git_commit[0]) cJSON_AddStringToObject(o, "git_commit", sc.git_commit);
            if (sc.source_script[0]) cJSON_AddStringToObject(o, "source_script", sc.source_script);
            cJSON_AddBoolToObject(o, "has_sha256", sc.sha256[0] != 0);
        }
        cJSON_AddItemToArray(arr, o);
    }
    closedir(d);
    return arr;
}

esp_err_t lib_delete(const char *stem)
{
    char path[128];
    lib_path(stem, "bit", path, sizeof(path));
    if (unlink(path) != 0) return ESP_ERR_NOT_FOUND;
    lib_path(stem, "json", path, sizeof(path));
    unlink(path);   // sidecar is optional
    return ESP_OK;
}

cJSON *lib_card_info(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddBoolToObject(o, "mounted", lib_mounted());
    cJSON_AddNumberToObject(o, "card_detect_raw_level", lib_card_detect_level());
    if (!lib_mounted()) return o;
    uint64_t total = 0, free_b = 0;
    if (esp_vfs_fat_info(LIB_MOUNT, &total, &free_b) == ESP_OK) {
        cJSON_AddNumberToObject(o, "total_bytes", (double)total);
        cJSON_AddNumberToObject(o, "free_bytes", (double)free_b);
    }
    cJSON_AddStringToObject(o, "card_name", s_card->cid.name);
    int images = 0;
    DIR *d = opendir(LIB_DIR);
    if (d) {
        struct dirent *e;
        while ((e = readdir(d)) != NULL) {
            size_t n = strlen(e->d_name);
            if (n > 4 && strcasecmp(e->d_name + n - 4, ".bit") == 0) images++;
        }
        closedir(d);
    }
    cJSON_AddNumberToObject(o, "image_count", images);
    return o;
}

void lib_temp_path(const char *ext, char *out, size_t cap)
{
    snprintf(out, cap, LIB_DIR "/_incoming.%s", ext);
}

esp_err_t lib_install(const char *temp_path, const char *stem, const char *ext)
{
    char dst[128];
    lib_path(stem, ext, dst, sizeof(dst));
    unlink(dst);   // FAT rename() will not replace an existing file
    if (rename(temp_path, dst) != 0) {
        unlink(temp_path);
        return ESP_FAIL;
    }
    return ESP_OK;
}
