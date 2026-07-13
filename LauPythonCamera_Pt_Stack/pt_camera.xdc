# =============================================================================
# pt_camera.xdc  -  onsemi PYTHON 1300 camera element on the Alchitry Pt V2
#
# Target: XC7A100T-2FGG484I  (Pt V2)
# Board : LauPythonCamera_Pt_Stack, TOP side
#
# LVDS IS ON THE DF40's **EVEN** ROW. This is forced by geometry, not chosen.
# An earlier revision used the odd row (both MRCC pairs are there) and was wrong.
# See README section 5.1.1. Short version:
#
#   The DF40's two rows escape in OPPOSITE directions. Bank B sits at y=41 on the
#   55x45 mm element board, so its ODD row escapes into a 2.6 mm strip against the
#   board edge. A 16.76 mm socket cannot fit below Bank B, so the sensor MUST sit
#   above it -- and only the EVEN row faces the sensor.
#
#   Bank 13's even row has NO MRCC pairs, only two SRCC. Verified with the real
#   1:10 receiver (iocheck/pt_camera_rx.v): an SRCC pin drives BUFIO + BUFR into a
#   cascaded ISERDESE2 and places clean. BUFG (which needs MRCC) is the wrong
#   structure for a 720 Mbps source-synchronous link anyway.
#
# ---------------------------------------------------------------------
# !!  READ THIS BEFORE YOU POWER THE BOARD  !!
#
# Bank 13 MUST be at VCCO = 2.5 V. That is set by HARDWARE, not by this file: the
# camera element straps VBSEL A (control-header pin 38) and VBSEL B (pin 40) both
# HIGH. Alchitry: "Failing to set the tri-voltage pins correctly could damage the
# FPGA."  Both the LVDS_25 OUTPUT and the internal DIFF_TERM require 2.5 V.
# ---------------------------------------------------------------------
#
# THIS FILE IS NOT A DROP-IN. It constrains the camera interface only. The rest of
# the design (HDMI via the Hd, USB3 via the Ft+, clocks, DDR3) still has to be
# ported -- every PACKAGE_PIN in constrs_1/imports/RTL/Au2.xdc is invalid on the
# Pt V2 (different die AND package: Au V2 = XC7A35T/FTG256).
#
# VERIFIED:
#   vivado -mode batch -source iocheck/run_iocheck.tcl     camera alone      PASS
#   vivado -mode batch -source iocheck/run_evencheck.tcl   + real receiver   PASS
#   vivado -mode batch -source iocheck/run_stackcheck.tcl  + Hd + Ft+        PASS
# =============================================================================

# --- forwarded bit clock. SRCC pair -- must reach BUFIO and BUFR. ------------
# sensor 7/8    elem B40/B42    IO_L11{N,P}_T1_SRCC_13
set_property -dict {PACKAGE_PIN Y11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_p}]
set_property -dict {PACKAGE_PIN Y12 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_n}]

# --- 4 data lanes, 720 Mbps each --------------------------------------------
# sensor 9/10   elem B46/B48    IO_L14{N,P}_T2_SRCC_13   (spare SRCC)
set_property -dict {PACKAGE_PIN U15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[0]}]
set_property -dict {PACKAGE_PIN V15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[0]}]
# sensor 11/12  elem B52/B54    IO_L2{N,P}_T0_13
set_property -dict {PACKAGE_PIN AB16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[1]}]
set_property -dict {PACKAGE_PIN AB17 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[1]}]
# sensor 13/14  elem B58/B60    IO_L1{N,P}_T0_13
set_property -dict {PACKAGE_PIN Y16  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[2]}]
set_property -dict {PACKAGE_PIN AA16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[2]}]
# sensor 15/16  elem B64/B66    IO_L15{N,P}_T2_DQS_13
set_property -dict {PACKAGE_PIN T14  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[3]}]
set_property -dict {PACKAGE_PIN T15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[3]}]

# --- sync channel ------------------------------------------------------------
# sensor 17/18  elem B70/B72    IO_L6{N,P}_T0_13   (B70 is the bank VREF pin;
#                                                   irrelevant for LVDS_25)
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_p}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_n}]

# --- LVDS clock OUT to the sensor. NO DIFF_TERM: it is an output. -----------
# Terminated at the far end by R2 (100R, 0402) at sensor pins 23/24.
# sensor 23/24  elem B76/B78    IO_L16{N,P}_T2_13
set_property -dict {PACKAGE_PIN W15 IOSTANDARD LVDS_25} [get_ports {cam_lvdsclk_p}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVDS_25} [get_ports {cam_lvdsclk_n}]

create_clock -name cam_clkout -period 2.778 [get_ports cam_clkout_p]   ;# 360 MHz

# --- single-ended control, banks 14/35 @ 3.3 V. UNCHANGED. ------------------
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

set_false_path -to   [get_ports {cam_reset_n cam_ss_n cam_sck cam_mosi cam_trigger[*]}]
set_false_path -from [get_ports {cam_miso cam_monitor[*]}]
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN [current_design]
