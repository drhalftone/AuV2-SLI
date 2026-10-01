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

#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "laufpga.h"
#include "laulibrary.h"
#include "launetwork.h"
#include "laupins.h"
#include "lauserver.h"

// LAU ESP32 CARD: MICROSD BITSTREAM LIBRARY BEHIND AN MCP SERVER (SD_BITSTREAM_LIBRARY.MD)

static const char *TAG = "LAUMain";

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void readBearerToken(char *token, size_t capacity)
{
    // USE THE MENUCONFIG TOKEN IF ONE WAS SET
    if (strlen(CONFIG_LAU_MCP_TOKEN) > 0) {
        snprintf(token, capacity, "%s", CONFIG_LAU_MCP_TOKEN);
        return;
    }

    // OTHERWISE KEEP ONE IN NVS, GENERATED ON FIRST BOOT, SO NO SECRET LIVES IN SDKCONFIG
    nvs_handle_t handle;
    ESP_ERROR_CHECK(nvs_open("lau", NVS_READWRITE, &handle));
    size_t length = capacity;
    if (nvs_get_str(handle, "token", token, &length) != ESP_OK) {
        // THE RADIO IS ON BY NOW SO THIS IS A TRUE RANDOM SOURCE
        uint8_t randomBytes[16];
        esp_fill_random(randomBytes, sizeof(randomBytes));
        for (int index = 0; index < 16; index++) {
            sprintf(token + 2 * index, "%02x", randomBytes[index]);
        }
        ESP_ERROR_CHECK(nvs_set_str(handle, "token", token));
        ESP_ERROR_CHECK(nvs_commit(handle));
        ESP_LOGW(TAG, "readBearerToken() :: generated a new MCP bearer token");
    }
    nvs_close(handle);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void statusLedTask(void *argument)
{
    // SLOW BLINK MEANS NO WIFI, STEADY MEANS READY, AND FAST BLINK MEANS JTAG IS BUSY
    bool ledOnFlag = false;
    while (true) {
        int periodMs;
        if (lauFpgaLockOwner()) {
            ledOnFlag = !ledOnFlag;
            periodMs = 80;
        } else if (!lauNetworkIsConnected()) {
            ledOnFlag = !ledOnFlag;
            periodMs = 500;
        } else {
            ledOnFlag = true;
            periodMs = 200;
        }
        gpio_set_level(PINSTATUSLED, ledOnFlag);
        vTaskDelay(pdMS_TO_TICKS(periodMs));
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void confirmOrRollback(bool connectedFlag)
{
    // NEW OTA FIRMWARE MUST PROVE IT CAN REACH THE NETWORK OR THE BOOTLOADER ROLLS BACK, WITHOUT
    // THIS A BAD WIFI SETTING IN AN UPDATE WOULD BRICK A SEALED CAMERA
    const esp_partition_t *runningPartition = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(runningPartition, &state) != ESP_OK || state != ESP_OTA_IMG_PENDING_VERIFY) {
        return;
    }
    if (connectedFlag) {
        ESP_LOGI(TAG, "confirmOrRollback() :: new firmware is on the network, marking it valid");
        esp_ota_mark_app_valid_cancel_rollback();
    } else {
        ESP_LOGE(TAG, "confirmOrRollback() :: new firmware never reached the network, rolling back");
        esp_ota_mark_app_invalid_rollback_and_reboot();
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
void app_main(void)
{
    // SAFE STATES BEFORE ANYTHING ELSE: FET GATES LOW, JTAG BUFFER OFF, DONE AS AN INPUT
    lauFpgaInit();

    gpio_config_t ledConfig = { .pin_bit_mask = 1ULL << PINSTATUSLED, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&ledConfig);
    xTaskCreate(statusLedTask, "led", 2048, NULL, 2, NULL);

    esp_err_t error = nvs_flash_init();
    if (error == ESP_ERR_NVS_NO_FREE_PAGES || error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        error = nvs_flash_init();
    }
    ESP_ERROR_CHECK(error);

    // MOUNT THE SD LIBRARY, WHICH IS NOT FATAL SINCE STATUS AND OTA STILL WORK WITHOUT A CARD
    lauLibraryMount();

    // BRING UP THE NETWORK AND THEN THE SERVER, NEVER TOUCHING THE FPGA AT BOOT (NO AUTOLOAD)
    if (lauNetworkStart() != ESP_OK) {
        ESP_LOGE(TAG, "app_main() :: no network configuration, the MCP server is not started");
        return;
    }
    bool connectedFlag = lauNetworkWaitConnected(120 * 1000);

    char token[72];
    readBearerToken(token, sizeof(token));
    ESP_LOGW(TAG, "app_main() :: MCP endpoint http://%s.local/mcp   token: %s", CONFIG_LAU_HOSTNAME, token);
    ESP_ERROR_CHECK(lauServerStart(token));

    confirmOrRollback(connectedFlag);
}
