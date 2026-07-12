# =====================================================================
# pt_camera.xdc  -  onsemi PYTHON 1300 camera element on the Alchitry Pt V2
#
# Target: XC7A100T-2FGG484I  (Pt V2)
# Board : LauPythonCamera_Pt_Stack, TOP side
#
# THIS FILE IS NOT A DROP-IN. It constrains the camera interface only.
# The rest of the design (HDMI via the Hd, USB3 via the Ft+, clocks, DDR3)
# still has to be ported from constrs_1/imports/RTL/Au2.xdc, EVERY PACKAGE_PIN
# of which is invalid on the Pt V2 -- different die AND different package
# (Au V2 = XC7A35T/FTG256).
#
# ---------------------------------------------------------------------
# !!  READ THIS BEFORE YOU POWER THE BOARD  !!
#
# Bank 13 MUST be at VCCO = 2.5 V. That is set by hardware, not by this file:
# the camera element strapS VBSEL A (control-header pin 38) and VBSEL B (pin 40)
# both HIGH. Alchitry: "Failing to set the tri-voltage pins correctly could
# damage the FPGA."
#
# The LVDS_25 OUTPUT (cam_lvdsclk, which drives the sensor's clock) and the
# internal DIFF_TERM on the inputs BOTH require VCCO = 2.5 V. If the straps are
# wrong, this design is not merely broken -- it is out of spec.
# ---------------------------------------------------------------------
#
# Verification (2 minutes, do it):
#   run synth_design, then report_io.
#   Vivado hard-errors on a non-pair or a reversed P/N. That independently
#   confirms every pin below against Vivado's own device database.
# =====================================================================


# =====================================================================
# LVDS  -  top Bank B, bank 13, VCCO = 2.5 V
#
# All 7 pairs are on the DF40's ODD row so they escape toward the sensor.
# Polarity rule: LOWER element-bus pin = N, HIGHER = P. No exceptions.
# =====================================================================

# --- Forwarded bit clock from the sensor (MRCC pair - required) -------
# sensor pins 7/8   elem B39/B41   IO_L12{N,P}_T1_MRCC_13
set_property -dict {PACKAGE_PIN W11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_p}]
set_property -dict {PACKAGE_PIN W12 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_n}]

# --- Data lanes, 720 Mbps each ---------------------------------------
# sensor 9/10    elem B45/B47   IO_L13{N,P}_T2_MRCC_13  (spare MRCC)
set_property -dict {PACKAGE_PIN V13 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[0]}]
set_property -dict {PACKAGE_PIN V14 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[0]}]
# sensor 11/12   elem B51/B53   IO_L10{N,P}_T1_13
set_property -dict {PACKAGE_PIN V10 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[1]}]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[1]}]
# sensor 13/14   elem B57/B59   IO_L7{N,P}_T1_13
set_property -dict {PACKAGE_PIN AB11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[2]}]
set_property -dict {PACKAGE_PIN AB12 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[2]}]
# sensor 15/16   elem B63/B65   IO_L9{N,P}_T1_DQS_13
set_property -dict {PACKAGE_PIN AA10 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[3]}]
set_property -dict {PACKAGE_PIN AA11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[3]}]

# --- Sync channel -----------------------------------------------------
# sensor 17/18   elem B69/B71   IO_L3{N,P}_T0_DQS_13
set_property -dict {PACKAGE_PIN AA13 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_p}]
set_property -dict {PACKAGE_PIN AB13 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_n}]

# --- LVDS clock OUT to the sensor  (FPGA drives; PLL bypassed) --------
# sensor 23/24   elem B75/B77   IO_L5{N,P}_T0_13
#
# NOTE: no DIFF_TERM here. This is an OUTPUT -- the FPGA is the driver.
# It is terminated at the far end by R2 (100R, 0402) on the camera board,
# right at sensor pins 23/24. The PYTHON has no internal termination on this
# input and the datasheet requires the external 100R.
set_property -dict {PACKAGE_PIN Y13  IOSTANDARD LVDS_25} [get_ports {cam_lvdsclk_p}]
set_property -dict {PACKAGE_PIN AA14 IOSTANDARD LVDS_25} [get_ports {cam_lvdsclk_n}]

# ---------------------------------------------------------------------
# DIFF_TERM: why it is on the 6 inputs and NOT on the output.
#
# The six sensor->FPGA pairs are terminated INSIDE the die (100R across the
# pair) via DIFF_TERM, which is legal only because bank 13 is at 2.5 V. There
# are deliberately NO termination resistors for them on the camera board: a
# resistor there would terminate MID-CHANNEL, leaving an unterminated stub
# running on through the DF40 and across the Pt V2 to the FPGA pin. That is
# worse than no termination.
#
# So there is exactly ONE physical termination resistor on the whole board
# (R2), and it is on the one pair that flows the other way. If DIFF_TERM is
# ever removed from the lines above, six pairs silently become unterminated at
# 720 Mbps and the link will not lock -- with no resistor to point at while
# debugging, because there is not supposed to be one.
# ---------------------------------------------------------------------


# =====================================================================
# Sensor clocking
#
# The FPGA drives the sensor's LVDS clock directly; the sensor's PLL is
# bypassed. cam_clkout is the sensor's forwarded bit clock and is what the
# ISERDES runs from -- hence the MRCC pair.
#
# 10-bit mode: 4 lanes x 720 Mbps, LVDS clock ~360 MHz.
# Bank 13 is a single HR bank = one clock region, so a BUFIO/BUFR driven from
# the MRCC pair above can clock ISERDES on ANY bank-13 pin.
# =====================================================================
create_clock -name cam_clkout -period 2.778 [get_ports cam_clkout_p]   ;# 360 MHz


# =====================================================================
# Single-ended control  -  top Bank A, LVCMOS33
#
# DELIBERATELY NOT IN BANK 13. The sensor's CMOS pins are 3.3 V; driving them
# from a 2.5 V bank would put VOH uncomfortably close to the sensor's VIH.
# Top Bank A is hardwired 3.3 V and is entirely free (the Ft+ and Hd are on the
# BOTTOM of the Pt).
#
# !! TODO - PACKAGE_PIN VALUES NOT YET FILLED IN !!
# The element-bus assignments below are settled (see README section 5.2), but the
# FGG484 package pins for top Bank A pins A3-A18 have not been pulled from
# Alchitry's PtV2TopPin.kt yet. DO NOT GUESS THEM.
#
#   sensor mosi      (pin 2)   -> elem A3    -> PACKAGE_PIN ???
#   sensor miso      (pin 3)   -> elem A4    -> PACKAGE_PIN ???
#   sensor sck       (pin 4)   -> elem A5    -> PACKAGE_PIN ???
#   sensor ss_n      (pin 47)  -> elem A6    -> PACKAGE_PIN ???
#   sensor reset_n   (pin 46)  -> elem A9    -> PACKAGE_PIN ???
#   sensor clk_pll   (pin 25)  -> elem A10   -> PACKAGE_PIN ???   (unused; routed anyway)
#   sensor trigger0  (pin 41)  -> elem A11   -> PACKAGE_PIN ???
#   sensor trigger1  (pin 42)  -> elem A12   -> PACKAGE_PIN ???
#   sensor trigger2  (pin 43)  -> elem A15   -> PACKAGE_PIN ???
#   sensor monitor0  (pin 44)  -> elem A16   -> PACKAGE_PIN ???
#   sensor monitor1  (pin 45)  -> elem A17   -> PACKAGE_PIN ???
#
# set_property -dict {PACKAGE_PIN ??? IOSTANDARD LVCMOS33} [get_ports {cam_mosi}]
# ... etc
#
# These are all slow (SPI <= a few MHz, triggers, reset), so no timing
# constraints are needed beyond false paths if they cross domains.
# =====================================================================
