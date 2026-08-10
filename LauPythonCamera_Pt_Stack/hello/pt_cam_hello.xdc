# =============================================================================
# pt_cam_hello.xdc - constraints for the PYTHON 1300 chip-ID bring-up bitstream
#
# Target: Alchitry Pt V2, XC7A100T-2FGG484I, with LauPythonCamera_Pt_Stack on top.
#
# Base pins (clk / rst_n / led / usb) are copied from iocheck/alchitry_pt_base.xdc.
# Camera pins are the 11 single-ended control signals from ../pt_camera.xdc,
# which are all in banks 14/35 at a fixed 3.3 V.
#
# ---------------------------------------------------------------------------
# THERE IS DELIBERATELY NO BANK-13 PIN IN THIS FILE.
#
# The LVDS interface is not part of this design. That is not an omission, it is
# the safety argument: the chip-ID read needs no LVDS (CAMERA_SENSOR_PROTOCOL.md
# §2), the sensor's LVDS drivers are powered down at reset and we never write
# register 112 to wake them (§3), and with no bank-13 port in the design this
# bitstream's safety does not depend on the VBSEL_A strap having set bank 13 to
# 2.5 V. Unused bank-13 pins are left to the UNUSEDPIN rule at the bottom.
# ---------------------------------------------------------------------------
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

# FT2232 UART. usb_rx is unused by this design but stays constrained so the pin
# is driven by the UNUSEDPIN rule rather than left to chance.
set_property -dict {PACKAGE_PIN AA20 IOSTANDARD LVCMOS33} [get_ports {usb_rx}]
set_property -dict {PACKAGE_PIN AA21 IOSTANDARD LVCMOS33} [get_ports {usb_tx}]

# ---- PYTHON 1300 single-ended control, banks 14/35 @ 3.3 V -----------------
# Identical to ../pt_camera.xdc; see CAMERA_IO_MAP.md §4 for the sensor-pin and
# element-pin provenance of each one.
set_property -dict {PACKAGE_PIN AB22 IOSTANDARD LVCMOS33} [get_ports {cam_mosi}]
set_property -dict {PACKAGE_PIN AB18 IOSTANDARD LVCMOS33} [get_ports {cam_miso}]
set_property -dict {PACKAGE_PIN AB21 IOSTANDARD LVCMOS33} [get_ports {cam_sck}]
set_property -dict {PACKAGE_PIN AA18 IOSTANDARD LVCMOS33} [get_ports {cam_clk_pll}]
set_property -dict {PACKAGE_PIN E3   IOSTANDARD LVCMOS33} [get_ports {cam_reset_n}]
set_property -dict {PACKAGE_PIN N2   IOSTANDARD LVCMOS33} [get_ports {cam_ss_n}]
set_property -dict {PACKAGE_PIN F3   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[0]}]
set_property -dict {PACKAGE_PIN P2   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[1]}]
set_property -dict {PACKAGE_PIN M2   IOSTANDARD LVCMOS33} [get_ports {cam_trigger[2]}]
set_property -dict {PACKAGE_PIN L1   IOSTANDARD LVCMOS33} [get_ports {cam_monitor[0]}]
set_property -dict {PACKAGE_PIN M3   IOSTANDARD LVCMOS33} [get_ports {cam_monitor[1]}]

# ---- timing ----------------------------------------------------------------
# Everything to the sensor is either static or a 1 MHz SPI bus generated from
# this clock; nothing here is a source-synchronous path Vivado can help with.
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

# Unused pins pulled DOWN, matching ../pt_camera.xdc. For the sensor's control
# inputs this is the safe direction anyway: a pulled-low reset_n during the
# configuration window holds the sensor in reset rather than releasing it into
# an unconfigured state.
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN [current_design]
