# =============================================================================
# pt_cam_clkrx.xdc - STAGE 3. THE FIRST XDC HERE THAT USES BANK 13.
#
# Adds exactly one differential pair to the stage-1/2 pin set: the sensor's
# forwarded bit clock, clock_out (sensor pins 7/8, Pt balls Y11/Y12). Pins and
# properties are taken verbatim from ../pt_camera.xdc.
#
# ---------------------------------------------------------------------
# !!  BANK 13 MUST BE AT VCCO = 2.5 V BEFORE THIS BITSTREAM IS LOADED  !!
#
# LVDS_25 and DIFF_TERM both require it, and it is set by HARDWARE: the camera
# element straps the Pt's VBSEL_A (control header pin 38) and VBSEL_B (pin 40)
# both HIGH. Alchitry: "Failing to set the tri-voltage pins correctly could
# damage the FPGA."
#
# Verify BRINGUP_DMM_CHECKLIST.md section 1 first: R10 pad 2 and R11 pad 2 both
# read 3.23-3.33 V. It passed 2026-08-07; the board has been reworked since.
# ---------------------------------------------------------------------
#
# Y11 is IO_L11P_T1_SRCC_13. The SRCC (not MRCC) choice is forced by geometry --
# only the DF40's EVEN row faces the sensor and bank 13's even row has no MRCC
# pairs. iocheck/pt_camera_rx.v proved an SRCC pin PLACES into BUFIO + BUFR;
# this stage is where that starts being true in silicon rather than in Vivado.
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


# ---- bank 13: the forwarded bit clock, LVDS_25 + DIFF_TERM ------------------
# sensor 7/8   elem B40/B42   IO_L11{N,P}_T1_SRCC_13
set_property -dict {PACKAGE_PIN Y11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_p}]
set_property -dict {PACKAGE_PIN Y12 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_n}]

# 360 MHz DDR bit clock. BUFR divides it by 5 for the 72 MHz word clock.
create_clock -name cam_clkout -period 2.778 [get_ports cam_clkout_p]

# The recovered domain is asynchronous to our 100 MHz reference. The only path
# between them is the 2FF toggle handshake in cam_clkrx_stage3, so tell the
# timer not to try to relate them.
set_clock_groups -asynchronous \
    -group [get_clocks -include_generated_clocks clk_0] \
    -group [get_clocks -include_generated_clocks cam_clkout]
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
