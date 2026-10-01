idf_component_register(
    SRCS "main.c" "bitfile.c" "jtag.c" "fpga.c" "library.c" "fetch.c" "mcp.c" "server.c" "net.c"
    INCLUDE_DIRS "."
    PRIV_REQUIRES esp_driver_gpio esp_driver_sdmmc sdmmc fatfs vfs json mbedtls esp_http_server
                  esp_http_client app_update esp_wifi esp_netif esp_event nvs_flash esp_timer
                  esp_app_format)
