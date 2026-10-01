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

#include "launetwork.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "mdns.h"
#include "sdkconfig.h"

#define NETWORKCONNECTEDBIT     BIT0

static const char *TAG = "LAUNetwork";
static EventGroupHandle_t networkEvents = NULL;

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
static void onNetworkEvent(void *argument, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        // KEEP TRYING FOREVER, THE CARD HAS NO OTHER WAY TO BE REACHED INSIDE THE STACK
        xEventGroupClearBits(networkEvents, NETWORKCONNECTEDBIT);
        ESP_LOGW(TAG, "onNetworkEvent() :: disconnected (reason %d), retrying", ((wifi_event_sta_disconnected_t *)data)->reason);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = data;
        ESP_LOGI(TAG, "onNetworkEvent() :: got IP " IPSTR " -- http://%s.local/mcp", IP2STR(&event->ip_info.ip), CONFIG_LAU_HOSTNAME);
        xEventGroupSetBits(networkEvents, NETWORKCONNECTEDBIT);
    }
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
esp_err_t lauNetworkStart(void)
{
    if (strlen(CONFIG_LAU_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "lauNetworkStart() :: no WiFi SSID configured, set it with 'idf.py menuconfig' -> LAU ESP32 card");
        return (ESP_ERR_INVALID_STATE);
    }

    // CREATE THE NETWORK INTERFACE AND REGISTER FOR WIFI AND IP EVENTS
    networkEvents = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *stationInterface = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(stationInterface, CONFIG_LAU_HOSTNAME);

    wifi_init_config_t initConfig = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&initConfig));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, onNetworkEvent, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, onNetworkEvent, NULL));

    // CONFIGURE THE STATION FROM MENUCONFIG AND START IT
    wifi_config_t wifiConfig = { 0 };
    strncpy((char *)wifiConfig.sta.ssid, CONFIG_LAU_WIFI_SSID, sizeof(wifiConfig.sta.ssid) - 1);
    strncpy((char *)wifiConfig.sta.password, CONFIG_LAU_WIFI_PASSWORD, sizeof(wifiConfig.sta.password) - 1);
    if (strlen(CONFIG_LAU_WIFI_PASSWORD)) {
        wifiConfig.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    } else {
        wifiConfig.sta.threshold.authmode = WIFI_AUTH_OPEN;
    }
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifiConfig));
    ESP_ERROR_CHECK(esp_wifi_start());

    // POWER SAVE ADDS 100+ MS OF LATENCY TO EVERY REQUEST AND THE CARD IS STACK POWERED
    esp_wifi_set_ps(WIFI_PS_NONE);

    // ADVERTISE THE MCP ENDPOINT OVER MDNS
    ESP_ERROR_CHECK(mdns_init());
    mdns_hostname_set(CONFIG_LAU_HOSTNAME);
    mdns_instance_name_set("LAU FPGA bitstream library");
    mdns_txt_item_t textItems[] = { { "path", "/mcp" }, { "protocol", "mcp-streamable-http" } };
    mdns_service_add(NULL, "_http", "_tcp", 80, textItems, 2);
    return (ESP_OK);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauNetworkWaitConnected(int timeoutMs)
{
    if (networkEvents == NULL) {
        return (false);
    }
    EventBits_t bits = xEventGroupWaitBits(networkEvents, NETWORKCONNECTEDBIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeoutMs));
    return ((bits & NETWORKCONNECTEDBIT) != 0);
}

/***************************************************************************************************/
/***************************************************************************************************/
/***************************************************************************************************/
bool lauNetworkIsConnected(void)
{
    if (networkEvents == NULL) {
        return (false);
    }
    return ((xEventGroupGetBits(networkEvents) & NETWORKCONNECTEDBIT) != 0);
}
