#include "net.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "mdns.h"
#include "sdkconfig.h"

static const char *TAG = "net";
static EventGroupHandle_t s_events;
#define CONNECTED_BIT BIT0

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_events, CONNECTED_BIT);
        ESP_LOGW(TAG, "disconnected (reason %d), retrying",
                 ((wifi_event_sta_disconnected_t *)data)->reason);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "got IP " IPSTR " -- http://%s.local/mcp", IP2STR(&e->ip_info.ip), CONFIG_LAU_HOSTNAME);
        xEventGroupSetBits(s_events, CONNECTED_BIT);
    }
}

esp_err_t net_start(void)
{
    if (strlen(CONFIG_LAU_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "no WiFi SSID configured: set it with 'idf.py menuconfig' -> LAU ESP32 card");
        return ESP_ERR_INVALID_STATE;
    }
    s_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *sta = esp_netif_create_default_wifi_sta();
    esp_netif_set_hostname(sta, CONFIG_LAU_HOSTNAME);

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));

    wifi_config_t wc = {0};
    strncpy((char *)wc.sta.ssid, CONFIG_LAU_WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, CONFIG_LAU_WIFI_PASSWORD, sizeof(wc.sta.password) - 1);
    wc.sta.threshold.authmode = strlen(CONFIG_LAU_WIFI_PASSWORD) ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Power save adds 100+ ms latency to every request; the card is stack-powered, so turn it off.
    esp_wifi_set_ps(WIFI_PS_NONE);

    ESP_ERROR_CHECK(mdns_init());
    mdns_hostname_set(CONFIG_LAU_HOSTNAME);
    mdns_instance_name_set("LAU FPGA bitstream library");
    mdns_txt_item_t txt[] = {{"path", "/mcp"}, {"protocol", "mcp-streamable-http"}};
    mdns_service_add(NULL, "_http", "_tcp", 80, txt, 2);
    return ESP_OK;
}

bool net_wait_connected(int timeout_ms)
{
    if (!s_events) return false;
    return xEventGroupWaitBits(s_events, CONNECTED_BIT, pdFALSE, pdTRUE, pdMS_TO_TICKS(timeout_ms)) & CONNECTED_BIT;
}

bool net_connected(void)
{
    return s_events && (xEventGroupGetBits(s_events) & CONNECTED_BIT);
}
