# wkkkk_top.xdc -- pins for the WHITE,K,K,K,K projector-only bitstream.
#
# ONLY the pins wkkkk_top actually has. Au2_pt.xdc cannot be reused: it constrains
# the HDMI receiver, the camera LVDS, DDR3 and the Ft+ bus, and a set_property on a
# port that does not exist is an ERROR, not a warning. Pin assignments below are
# copied verbatim from Au2_pt.xdc -- same board, same connector.

# --- bitstream config (Pt values, verbatim from Au2_pt.xdc lines 22-30) -------
# SPI_BUSWIDTH 4 is NOT cosmetic: write_cfgmem -interface spix4 REFUSES to run
# without it, so omitting these produced a valid .bit and no .bin at all.
set_property BITSTREAM.GENERAL.COMPRESS      TRUE  [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE     66    [current_design]
set_property CONFIG_VOLTAGE                  3.3   [current_design]
set_property CFGBVS                          VCCO  [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO    [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH   4     [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE  YES   [current_design]

# ---- 100 MHz system clock ----------------------------------------------------
set_property -dict { PACKAGE_PIN "W19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { clk100 }]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk100]

# ---- HDMI OUT (Hd V2 port 1) -------------------------------------------------
set_property -dict { PACKAGE_PIN "E19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_clk_p }]
set_property -dict { PACKAGE_PIN "D19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_clk_n }]
set_property -dict { PACKAGE_PIN "B15" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[0] }]
set_property -dict { PACKAGE_PIN "B16" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[0] }]
set_property -dict { PACKAGE_PIN "F19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[1] }]
set_property -dict { PACKAGE_PIN "F20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[1] }]
set_property -dict { PACKAGE_PIN "B20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[2] }]
set_property -dict { PACKAGE_PIN "A20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[2] }]

# ---- status LEDs (pins verbatim from Au2_pt.xdc lines 79-86) ----------------
set_property -dict { PACKAGE_PIN "P19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN "P20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN "T21" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN "R19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[3] }]
set_property -dict { PACKAGE_PIN "V22" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[4] }]
set_property -dict { PACKAGE_PIN "U21" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[5] }]
set_property -dict { PACKAGE_PIN "T20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[6] }]
set_property -dict { PACKAGE_PIN "W20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[7] }]

# LEDs are asynchronous indicators read by a human, not a timed interface.
set_false_path -through [get_ports {led[*]}]
