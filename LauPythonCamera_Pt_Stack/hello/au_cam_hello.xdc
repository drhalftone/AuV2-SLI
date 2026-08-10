# =============================================================================
# au_cam_hello.xdc - PYTHON 1300 chip-ID bring-up bitstream on the Alchitry Au V2
#
# Target: XC7A35T-FTG256-2 (Au V2), with the camera element stacked on top.
#
# This is CAMERA_RTL_PLAN.md milestone #5 -- the Au V2 hardware gate. The camera
# pins, pull directions and their justification are lifted verbatim from
# constrs_1/imports/RTL/cam_au2.xdc; the base pins come from Au2.xdc. Neither of
# those files is touched.
#
# ---------------------------------------------------------------------------
# NO LVDS PIN IS CONSTRAINED HERE, AND THAT IS LOAD-BEARING.
#
# On the Au the seven LVDS pairs scatter across three banks at three fixed
# voltages, and dout0± lands on bank 15, which Alchitry documents as NOT 3.3 V
# tolerant (CAMERA_IO_MAP.md §8.2). Real pixels need the Pt V2 and cannot be
# made to work here in RTL.
#
# It is safe because the sensor's LVDS drivers are powered down at reset
# (register 112 = 0) and this design cannot write any register -- the SPI
# master's rw input is a hard-wired 0. See au_cam_hello.v's header.
# ---------------------------------------------------------------------------
#
# THE NAMESPACE TRAP (CAMERA_IO_MAP.md §8.1): Au2.xdc puts HDMI TMDS on FPGA
# BALLS literally named A3/A4/A5, and the camera uses ELEMENT PINS also named
# A3/A4/A5. They are unrelated. Element A3 -> ball N6.
# =============================================================================

# ---- Au V2 base (from Au2.xdc) ---------------------------------------------
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {clk}]
create_clock -period 10.0 -name clk_0 -waveform {0.000 5.0} [get_ports clk]

set_property -dict {PACKAGE_PIN K13 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN K12 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN L14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN L13 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN M15 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN M14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN M12 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33 SLEW FAST} [get_ports {led[7]}]

set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33 SLEW SLOW}    [get_ports {usb_tx}]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33 PULLUP TRUE}  [get_ports {usb_rx}]

# ---- Camera SPI (element A3..A5, bank 14 -- the config bank) ---------------
# Bank 14 floats Hi-Z until DONE. Safe because ss_n is pulled HIGH externally,
# so the sensor ignores whatever sck/mosi do during configuration.
set_property -dict {PACKAGE_PIN N6 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8} [get_ports {cam_mosi}]
set_property -dict {PACKAGE_PIN M6 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8} [get_ports {cam_sck}]
set_property -dict {PACKAGE_PIN P9 IOSTANDARD LVCMOS33}                   [get_ports {cam_miso}]

# clk_pll (element A6 -> ball N9, bank 14). cam_au2.xdc leaves this pin out
# because the design there never drove it; this design has the port, so it is
# constrained and held LOW. No sensor clock is needed for a chip-ID read, and
# never starting one means we can never stop one while the sensor is out of
# reset -- the failure mode CAMERA_SENSOR_PROTOCOL.md §6 warns about.
set_property -dict {PACKAGE_PIN N9 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE} [get_ports {cam_clk_pll}]

# ---- Camera discretes (element A9..A17, bank 35) ---------------------------
# THE PULL DIRECTIONS MATCH THE BOARD'S EXTERNAL 10k RESISTORS. Do not invert
# them. The external resistors are the primary guarantee -- they hold through
# the whole FPGA configuration window, when the internal ones do nothing.
set_property -dict {PACKAGE_PIN L2 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLUP TRUE}   [get_ports {cam_ss_n}]
set_property -dict {PACKAGE_PIN J1 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE} [get_ports {cam_reset_n}]
set_property -dict {PACKAGE_PIN K1 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE} [get_ports {cam_trigger[0]}]
set_property -dict {PACKAGE_PIN L3 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE} [get_ports {cam_trigger[1]}]
set_property -dict {PACKAGE_PIN H1 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE} [get_ports {cam_trigger[2]}]
set_property -dict {PACKAGE_PIN K2 IOSTANDARD LVCMOS33} [get_ports {cam_monitor[0]}]
set_property -dict {PACKAGE_PIN H2 IOSTANDARD LVCMOS33} [get_ports {cam_monitor[1]}]

# ---- timing ----------------------------------------------------------------
set_false_path -to   [get_ports {cam_sck cam_mosi cam_ss_n cam_reset_n cam_clk_pll cam_trigger[*]}]
set_false_path -from [get_ports {cam_miso cam_monitor[*] usb_rx}]
set_false_path -to   [get_ports {led[*] usb_tx}]

# ---- bitstream (Au V2 flash settings, from Au2.xdc) ------------------------
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33 [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 1 [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE YES [current_design]
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN [current_design]
