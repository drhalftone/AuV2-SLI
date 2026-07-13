# ============================================================================
# led_sign.xdc -- constraints for the "sign of life" LED scanner.
# Target: Alchitry Au V2  (XC7A35T-FTG256-2)
# Only the 100 MHz clock and the 8 user LEDs are used.
# ============================================================================

# --- Bitstream / config (so the .bin can also be flashed permanently) ---
set_property BITSTREAM.GENERAL.COMPRESS        TRUE  [current_design]
set_property CONFIG_VOLTAGE                     3.3   [current_design]
set_property CFGBVS                             VCCO  [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE        33    [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH      1     [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE     YES   [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR    NO    [current_design]

# --- 100 MHz on-board clock ---
set_property -dict { PACKAGE_PIN N14  IOSTANDARD LVCMOS33 } [get_ports { clk100 }]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk100]

# --- 8 user LEDs (same balls as Au2.xdc) ---
set_property -dict { PACKAGE_PIN K13  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN K12  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN L14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN L13  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[3] }]
set_property -dict { PACKAGE_PIN M15  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[4] }]
set_property -dict { PACKAGE_PIN M14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[5] }]
set_property -dict { PACKAGE_PIN M12  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[6] }]
set_property -dict { PACKAGE_PIN P14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[7] }]
