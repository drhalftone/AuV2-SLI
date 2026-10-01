// LAU ESP32 card: microSD bitstream library behind an MCP server (SD_BITSTREAM_LIBRARY.md).
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_random.h"
#include "fpga.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "library.h"
#include "net.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "pins.h"
#include "sdkconfig.h"
#include "server.h"

static const char *TAG = "main";

// Token: menuconfig value if set; otherwise one generated on first boot and kept in NVS, so no
// secret has to live in sdkconfig. Printed to the console at boot -- read it over USB/UART.
static void get_token(char *out, size_t cap)
{
    if (strlen(CONFIG_LAU_MCP_TOKEN) > 0) {
        snprintf(out, cap, "%s", CONFIG_LAU_MCP_TOKEN);
        return;
    }
    nvs_handle_t h;
    ESP_ERROR_CHECK(nvs_open("lau", NVS_READWRITE, &h));
    size_t len = cap;
    if (nvs_get_str(h, "token", out, &len) != ESP_OK) {
        uint8_t r[16];
        esp_fill_random(r, sizeof(r));   // RF is on by now, so this is a true random source
        for (int i = 0; i < 16; i++) sprintf(out + 2 * i, "%02x", r[i]);
        ESP_ERROR_CHECK(nvs_set_str(h, "token", out));
        ESP_ERROR_CHECK(nvs_commit(h));
        ESP_LOGW(TAG, "generated a new MCP bearer token");
    }
    nvs_close(h);
}

// LED: slow blink = no WiFi, steady = ready, fast blink = JTAG busy.
static void led_task(void *arg)
{
    bool on = false;
    for (;;) {
        int period_ms;
        if (fpga_lock_owner()) { on = !on; period_ms = 80; }
        else if (!net_connected()) { on = !on; period_ms = 500; }
        else { on = true; period_ms = 200; }
        gpio_set_level(PIN_LED, on);
        vTaskDelay(pdMS_TO_TICKS(period_ms));
    }
}

// New OTA firmware must prove it can reach the network, or the bootloader rolls back to the
// previous image. Without this a bad WiFi setting in an update would brick a sealed camera.
static void confirm_or_rollback(bool connected)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) return;
    if (connected) {
        ESP_LOGI(TAG, "new firmware is on the network: marking it valid");
        esp_ota_mark_app_valid_cancel_rollback();
    } else {
        ESP_LOGE(TAG, "new firmware never reached the network: rolling back");
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}

void app_main(void)
{
    // 1. Safe states before anything else: FET gates low, JTAG buffer off, DONE as input.
    fpga_init();

    gpio_config_t led = {.pin_bit_mask = 1ULL << PIN_LED, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&led);
    xTaskCreate(led_task, "led", 2048, NULL, 2, NULL);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    // 2. SD library. Not fatal: status and OTA still work without a card.
    lib_mount();

    // 3. Network, then the server. The FPGA is never touched at boot (§4: no autoload).
    if (net_start() != ESP_OK) {
        ESP_LOGE(TAG, "no network configuration; the MCP server is not started");
        return;
    }
    bool connected = net_wait_connected(120 * 1000);

    char token[72];
    get_token(token, sizeof(token));
    ESP_LOGW(TAG, "MCP endpoint http://%s.local/mcp   token: %s", CONFIG_LAU_HOSTNAME, token);
    ESP_ERROR_CHECK(server_start(token));

    confirm_or_rollback(connected);
}
