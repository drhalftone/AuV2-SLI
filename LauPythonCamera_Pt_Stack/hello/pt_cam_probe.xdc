# =============================================================================
# pt_cam_probe.xdc - constraints for the SPI-failure diagnostic bitstream.
#
# Identical to pt_cam_hello.xdc EXCEPT for one deliberate change:
#
#     cam_monitor[0] and cam_monitor[1] get an internal PULLDOWN.
#
# That single difference is the point of this bitstream. In the hello build the
# monitor pins have no pull, so "floating" and "driven high by the sensor" are
# indistinguishable -- which is exactly the ambiguity the mon=2 reading left us
# with. With a pulldown fitted, a pin that still reads 1 is being actively
# driven, and the only thing on that net is the sensor. See cam_probe.v Test 1.
#
# Everything else -- no bank-13 pin, no register write possible, clk_pll held
# low -- carries over unchanged. See pt_cam_hello.xdc for that reasoning.
# =============================================================================

# ---- Pt V2 base ------------------------------------------------------------
set_property -dict {PACKAGE_PIN W19 IOSTANDARD LVCMOS33} [get_ports {clk}]
create_clock -period 10.0 -name clk_0 -waveform {0.000 5.0} [get_ports clk]

set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33} [get_ports {rst_n}]

set_property -dict {PACKAGE_PIN P19 IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P20 IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T21 IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN R19 IOSTANDARD LVCMOS33} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN V22 IOSTANDARD LVCMOS33} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN U21 IOSTANDARD LVCMOS33} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN T20 IOSTANDARD LVCMOS33} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN W20 IOSTANDARD LVCMOS33} [get_ports {led[7]}]

set_property -dict {PACKAGE_PIN AA20 IOSTANDARD LVCMOS33} [get_ports {usb_rx}]
set_property -dict {PACKAGE_PIN AA21 IOSTANDARD LVCMOS33} [get_ports {usb_tx}]

# ---- PYTHON 1300 single-ended control, banks 14/35 @ 3.3 V -----------------
set_property -dict {PACKAGE_PIN AB22 IOSTANDARD LVCMOS33} [get_ports {cam_mosi}]
# >>> DIAGNOSTIC: pulldown on miso too. <<<
# The board fits NO pull on miso (README §"bank 14 / bank 35 split", Pull column
# is "—") and the FPGA had none either, so the hello bitstream's steady miso=H
# was a FLOATING input parking high -- not evidence of anything driving. With a
# pulldown, a floating line reads a hard 0 and reg0 becomes 0000; any 1 bit that
# still appears was actively driven by the sensor.
set_property -dict {PACKAGE_PIN AB18 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {cam_miso}]
set_property -dict {PACKAGE_PIN AB21 IOSTANDARD LVCMOS33} [get_ports {cam_sck}]
set_property -dict {PACKAGE_PIN AA18 IOSTANDARD LVCMOS33} [get_ports {cam_clk_pll}]
set_property -dict {PACKAGE_PIN E3   IOSTANDARD LVCMOS33} [get_ports {cam_reset_n}]
set_property -dict {PACKAGE_PIN N2   IOSTANDARD LVCMOS33} [get_ports {cam_ss_n}]
set_property -dict {PACKAGE_PIN F3   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[0]}]
set_property -dict {PACKAGE_PIN P2   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[1]}]
set_property -dict {PACKAGE_PIN M2   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[2]}]

# >>> THE DIAGNOSTIC: pulldowns on the two sensor outputs. <<<
set_property -dict {PACKAGE_PIN L1 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {cam_monitor[0]}]
set_property -dict {PACKAGE_PIN M3 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {cam_monitor[1]}]

# ---- timing ----------------------------------------------------------------
set_false_path -to   [get_ports {cam_reset_n cam_ss_n cam_sck cam_mosi cam_clk_pll cam_trigger[*]}]
set_false_path -from [get_ports {cam_miso cam_monitor[*] rst_n usb_rx}]
set_false_path -to   [get_ports {led[*] usb_tx}]

# ---- bitstream -------------------------------------------------------------
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 66 [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE YES [current_design]
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN [current_design]
