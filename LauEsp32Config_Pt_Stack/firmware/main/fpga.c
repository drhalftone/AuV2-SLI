// JTAG configuration of the Pt V2's XC7A100T from a .bit on the SD card, following the
// single-device flow of UG470 (7 Series Configuration User Guide), JTAG chapter:
//   TLR -> JPROGRAM -> poll INIT_COMPLETE (IR capture bit 4) -> CFG_IN, shift data ->
//   JSTART -> >= 2000 TCK in RTI -> TLR -> check DONE (IR capture bit 5 and the DONE pin).
// Configuration RAM only: nothing here touches the QSPI boot flash (README §2).
#include "fpga.h"

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
#include "jtag.h"
#include "library.h"
#include "pins.h"

static const char *TAG = "fpga";

static SemaphoreHandle_t s_lock;
static const char *s_owner;
static char s_last_image[72] = "unknown (FPGA booted from its own flash, or not loaded by this card)";

// IDCODE (version nibble masked) -> part-name prefix as it appears in the .bit header 'b' field.
static const struct { uint32_t id; const char *part; } k_parts[] = {
    {0x0362E093, "7a15t"},  {0x0362D093, "7a35t"},  {0x0362C093, "7a50t"},
    {0x03632093, "7a75t"},  {0x03631093, "7a100t"}, {0x03636093, "7a200t"},
};

static const char *part_for_idcode(uint32_t id)
{
    for (size_t i = 0; i < sizeof(k_parts) / sizeof(k_parts[0]); i++) {
        if ((id & 0x0FFFFFFF) == k_parts[i].id) return k_parts[i].part;
    }
    return NULL;
}

void fpga_init(void)
{
    // FET gates low before anything else runs (R4/R5 already hold them; make it explicit).
    gpio_set_level(PIN_PROG_EN, 0);
    gpio_set_level(PIN_RESET_EN, 0);
    gpio_config_t fets = {
        .pin_bit_mask = (1ULL << PIN_PROG_EN) | (1ULL << PIN_RESET_EN),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&fets);
    gpio_set_level(PIN_PROG_EN, 0);
    gpio_set_level(PIN_RESET_EN, 0);

    // DONE is input-only, no pull: the Pt's R34 4.7k is the pull-up. Never make this an output.
    gpio_config_t done = {.pin_bit_mask = 1ULL << PIN_FPGA_DONE, .mode = GPIO_MODE_INPUT};
    gpio_config(&done);

    jtag_init();
    s_lock = xSemaphoreCreateMutex();
}

bool fpga_done_pin(void) { return gpio_get_level(PIN_FPGA_DONE) != 0; }

bool fpga_try_lock(const char *owner)
{
    if (xSemaphoreTake(s_lock, 0) != pdTRUE) return false;
    s_owner = owner;
    return true;
}

void fpga_unlock(void)
{
    s_owner = NULL;
    xSemaphoreGive(s_lock);
}

const char *fpga_lock_owner(void) { return s_owner; }

static cJSON *fail(cJSON *o, bool *is_error, const char *fmt, ...)
{
    char msg[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    cJSON_AddBoolToObject(o, "ok", false);
    cJSON_AddStringToObject(o, "error", msg);
    *is_error = true;
    ESP_LOGW(TAG, "%s", msg);
    return o;
}

static void hex32(char *out, uint32_t v) { sprintf(out, "0x%08lx", (unsigned long)v); }

// Must be called with the lock held and U2 enabled. Fills idcode/chain/part; false on failure.
static bool probe(cJSON *o, uint32_t *idcode_out, const char **part_out, char *err, size_t errcap)
{
    int chain = jtag_chain_length();
    cJSON_AddNumberToObject(o, "jtag_chain_length", chain);
    if (chain != 1) {
        snprintf(err, errcap, "JTAG chain length is %d, expected exactly 1 (the Pt's FPGA). "
                 "0 usually means U2 is not driving or TDO is open; check the rev A/B straps", chain);
        return false;
    }
    jtag_reset();
    uint32_t id = jtag_shift_dr32(0);   // TLR selects IDCODE
    char h[12];
    hex32(h, id);
    cJSON_AddStringToObject(o, "idcode", h);
    const char *part = part_for_idcode(id);
    cJSON_AddStringToObject(o, "part", part ? part : "unrecognised");
    if (!part) {
        snprintf(err, errcap, "IDCODE %s is not a known 7-series part", h);
        return false;
    }
    *idcode_out = id;
    *part_out = part;
    return true;
}

cJSON *fpga_status(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddBoolToObject(o, "done_pin", fpga_done_pin());
    cJSON_AddStringToObject(o, "last_loaded_by_this_card", s_last_image);
    const char *owner = fpga_lock_owner();
    if (owner) {
        cJSON_AddStringToObject(o, "jtag_busy", owner);
        return o;   // do not touch JTAG while someone else holds it
    }
    if (!fpga_try_lock("status")) {
        cJSON_AddStringToObject(o, "jtag_busy", "yes");
        return o;
    }
    jtag_enable(true);
    uint32_t id;
    const char *part;
    char err[200];
    if (probe(o, &id, &part, err, sizeof(err))) {
        uint32_t cap = jtag_shift_ir(XC7_BYPASS, XC7_IR_LEN);
        cJSON_AddBoolToObject(o, "ir_capture_done", (cap & XC7_IRCAP_DONE) != 0);
        cJSON_AddBoolToObject(o, "ir_capture_init_complete", (cap & XC7_IRCAP_INIT_COMPLETE) != 0);
        jtag_reset();
    } else {
        cJSON_AddStringToObject(o, "jtag_error", err);
    }
    jtag_enable(false);
    fpga_unlock();
    return o;
}

cJSON *fpga_load(const char *stem, bool *is_error)
{
    *is_error = false;
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "name", stem);
    if (!lib_mounted()) return fail(o, is_error, "no SD card mounted");

    // ---- checks that do not touch the FPGA (SD_BITSTREAM_LIBRARY.md §5.3)
    bitfile_info_t info;
    long size;
    esp_err_t herr = lib_read_header(stem, &info, &size);
    if (herr == ESP_ERR_NOT_FOUND) return fail(o, is_error, "no image named '%s' (call list_bitstreams)", stem);
    if (herr != ESP_OK) return fail(o, is_error, "'%s.bit' has an unreadable or truncated header", stem);
    cJSON_AddStringToObject(o, "image_part", info.part);
    cJSON_AddStringToObject(o, "image_design", info.design);

    char path[128];
    lib_path(stem, "bit", path, sizeof(path));
    lib_sidecar_t sc;
    if (lib_read_sidecar(stem, &sc) && sc.sha256[0]) {
        char got[65];
        if (lib_sha256_file(path, got) != ESP_OK) return fail(o, is_error, "could not read '%s' to hash it", path);
        if (strcasecmp(got, sc.sha256) != 0) {
            return fail(o, is_error, "sha256 mismatch: sidecar says %.16s..., file is %.16s... -- refusing to load", sc.sha256, got);
        }
        cJSON_AddStringToObject(o, "sha256_verified", got);
    } else {
        cJSON_AddStringToObject(o, "sha256_verified", "no sidecar hash -- integrity not checked");
    }

    FILE *f = fopen(path, "rb");
    if (!f) return fail(o, is_error, "could not open '%s'", path);
    if (fseek(f, info.data_offset, SEEK_SET) != 0) {
        fclose(f);
        return fail(o, is_error, "seek failed in '%s'", path);
    }

    // ---- take JTAG
    if (!fpga_try_lock("load_bitstream")) {
        fclose(f);
        return fail(o, is_error, "JTAG is busy (%s); try again when it is free", fpga_lock_owner() ? fpga_lock_owner() : "?");
    }
    int64_t t0 = esp_timer_get_time();
    jtag_enable(true);

    uint32_t id;
    const char *part;
    char err[200];
    if (!probe(o, &id, &part, err, sizeof(err))) {
        jtag_enable(false);
        fpga_unlock();
        fclose(f);
        return fail(o, is_error, "%s -- FPGA untouched", err);
    }
    if (strncmp(info.part, part, strlen(part)) != 0) {
        jtag_enable(false);
        fpga_unlock();
        fclose(f);
        return fail(o, is_error, "image is for '%s' but the FPGA is '%s' -- FPGA untouched", info.part, part);
    }

    // ---- point of no return: JPROGRAM clears the running design
    jtag_reset();
    jtag_shift_ir(XC7_JPROGRAM, XC7_IR_LEN);
    vTaskDelay(pdMS_TO_TICKS(10));
    bool init_ok = false;
    for (int i = 0; i < 100 && !init_ok; i++) {
        init_ok = (jtag_shift_ir(XC7_ISC_NOOP, XC7_IR_LEN) & XC7_IRCAP_INIT_COMPLETE) != 0;
        if (!init_ok) vTaskDelay(pdMS_TO_TICKS(2));
    }
    if (!init_ok) {
        jtag_reset();
        jtag_enable(false);
        fpga_unlock();
        fclose(f);
        snprintf(s_last_image, sizeof(s_last_image), "none (load of '%s' failed)", stem);
        return fail(o, is_error, "INIT_COMPLETE never set after JPROGRAM; the FPGA is now UNCONFIGURED. "
                    "Call restore_flash_image to bring back the camera image");
    }

    jtag_shift_ir(XC7_CFG_IN, XC7_IR_LEN);
    jtag_dr_begin();
    static uint8_t buf[4096];
    uint32_t left = info.data_len;
    bool read_err = false;
    while (left > 0) {
        size_t want = left < sizeof(buf) ? left : sizeof(buf);
        size_t got = fread(buf, 1, want, f);
        if (got != want) {
            read_err = true;
            // still exit Shift-DR cleanly with what we have
            jtag_dr_bytes_msb(buf, got ? got : 1, true);
            break;
        }
        left -= got;
        jtag_dr_bytes_msb(buf, got, left == 0);
    }
    fclose(f);

    jtag_shift_ir(XC7_JSTART, XC7_IR_LEN);
    jtag_run_test(2000);
    jtag_reset();
    uint32_t cap = jtag_shift_ir(XC7_BYPASS, XC7_IR_LEN);
    jtag_reset();
    jtag_enable(false);
    fpga_unlock();

    // DONE through the pin as well: it is what the rest of the stack sees.
    bool done_pin = false;
    for (int i = 0; i < 50 && !done_pin; i++) {
        done_pin = fpga_done_pin();
        if (!done_pin) vTaskDelay(pdMS_TO_TICKS(2));
    }
    double secs = (esp_timer_get_time() - t0) / 1e6;
    cJSON_AddBoolToObject(o, "ir_capture_done", (cap & XC7_IRCAP_DONE) != 0);
    cJSON_AddBoolToObject(o, "done_pin", done_pin);
    cJSON_AddNumberToObject(o, "seconds", secs);
    cJSON_AddNumberToObject(o, "bytes_shifted", (double)(info.data_len - left));

    if (read_err) {
        snprintf(s_last_image, sizeof(s_last_image), "none (load of '%s' failed)", stem);
        return fail(o, is_error, "SD read error part-way through the image; FPGA is not configured. Call restore_flash_image");
    }
    if (!done_pin) {
        snprintf(s_last_image, sizeof(s_last_image), "none (load of '%s' failed)", stem);
        return fail(o, is_error, "DONE did not go high; the image did not start. Call restore_flash_image to recover");
    }
    snprintf(s_last_image, sizeof(s_last_image), "%s", stem);
    cJSON_AddBoolToObject(o, "ok", true);
    ESP_LOGI(TAG, "loaded '%s' in %.2f s", stem, secs);
    return o;
}

cJSON *fpga_restore_flash(bool *is_error)
{
    *is_error = false;
    cJSON *o = cJSON_CreateObject();
    if (!fpga_try_lock("restore_flash_image")) {
        return fail(o, is_error, "JTAG/config is busy (%s)", fpga_lock_owner() ? fpga_lock_owner() : "?");
    }
    // PROGRAM_B low for well over T_PROGRAM (250 ns min), then let the Pt's pull-up release it.
    gpio_set_level(PIN_PROG_EN, 1);
    esp_rom_delay_us(1000);
    gpio_set_level(PIN_PROG_EN, 0);

    int64_t t0 = esp_timer_get_time();
    bool done = false;
    while (!done && esp_timer_get_time() - t0 < 5 * 1000 * 1000) {   // QSPI load is ~1 s or less
        vTaskDelay(pdMS_TO_TICKS(10));
        done = fpga_done_pin();
    }
    fpga_unlock();
    cJSON_AddBoolToObject(o, "done_pin", done);
    cJSON_AddNumberToObject(o, "seconds", (esp_timer_get_time() - t0) / 1e6);
    if (!done) return fail(o, is_error, "PROGRAM_B pulsed but DONE stayed low for 5 s -- is there a valid image in the QSPI flash?");
    snprintf(s_last_image, sizeof(s_last_image), "flash image (restored by PROGRAM_B)");
    cJSON_AddBoolToObject(o, "ok", true);
    return o;
}
