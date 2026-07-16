# =============================================================================
# Au2_pt.xdc  --  Au2_SLI ported to the Alchitry Pt V2  (XC7A100T-2FGG484I)
#
# The Pt port (CAMERA_RTL_PLAN.md). Phase 1 was the EXISTING SLI design (HDMI passthrough
# + offline pattern generator) re-pinned. Phase 2 (task #12) adds the camera on top: the
# bank-13 LVDS receive interface + the 72 MHz cam_clk_pll reference are at the bottom of
# this file; the CMOS SPI/control pins were already here from Phase 1.
#
# This is a from-scratch re-pin: every PACKAGE_PIN in Au2.xdc is invalid on the Pt V2
# (different die AND package). Derivation, all cross-checked:
#   - Onboard clk/led/usb/rst: LauPythonCamera_Pt_Stack/iocheck/alchitry_pt_base.xdc.
#   - HDMI: the Hd V2 sits on the Pt; its bottom connector maps HDMI to Pt balls in
#     iocheck/alchitry_pt_hd_bottom.xdc. WHICH Hd port is TX vs RX was resolved by tracing
#     the WORKING Au design: its hdmi_tx balls -> element pins (AuV2Pin.kt) -> Hd port
#     (hd_v2.acf) = PORT 1; hdmi_rx = PORT 2. So here TX = Hd port 1, RX = Hd port 2.
#   - C1 handshake: element B27-B30 -> Pt balls (PtV2TopPin.kt), same element pins as the Au.
#   - newSW / C2: spare 3.3 V element pins -- see notes at those blocks.
#
# The timing constraints (false_paths, clock_groups, set_max_delay) are INSTANCE-based and
# carry over verbatim from Au2.xdc; only the two pin-based create_clock lines change.
# =============================================================================

# --- bitstream config (Pt values: bigger flash, x4 SPI, faster config) --------------
set_property BITSTREAM.GENERAL.COMPRESS   TRUE   [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE   66    [current_design]
set_property CONFIG_VOLTAGE                3.3   [current_design]
set_property CFGBVS                        VCCO  [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO  [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH  4    [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE YES  [current_design]

# =============================================================================
#  CLOCKS
# =============================================================================
# 100 MHz onboard oscillator (Pt: W19)
set_property -dict { PACKAGE_PIN "W19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { clk100 }]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk100]
# recovered HDMI-input pixel clock (Pt RX = Hd port 2 clk = C18)
create_clock -add -name hdmi_clk -period 13.300 -waveform {0 5} [get_ports hdmi_rx_clk_p]

# --- timing exceptions: VERBATIM from Au2.xdc (all instance-based) -------------------
set_false_path -from [get_clocks sys_clk_pin] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]]
set_false_path -through [get_ports led[*]]
set_false_path -through [get_ports newSW[*]]
set_false_path -from [get_clocks hdmi_clk] -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]]
set_false_path -from [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]] -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT1]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT1]]
set_false_path -to   [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT1]]

set_clock_groups -name pix_domains_async -asynchronous \
  -group [get_clocks -of_objects [get_pins {i_drp_clkgen13/i_mmcm/CLKOUT0 i_drp_clkgen13/i_mmcm/CLKOUT1 i_drp_clkgen13/i_mmcm/CLKOUT2}]] \
  -group [get_clocks -of_objects [get_pins {i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0 i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT1 i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT2}]] \
  -group "[get_clocks sys_clk_pin] [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT*]]"

set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch1/i_deser/ISERDESE2_slave/CE1]
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch2/i_deser/ISERDESE2_slave/CE1]
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch1/i_deser/ISERDESE2_master/CE1]
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch0/i_deser/ISERDESE2_slave/CE1]
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch0/i_deser/ISERDESE2_master/CE1]
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch2/i_deser/ISERDESE2_master/CE1]

# =============================================================================
#  LEDs (onboard)
# =============================================================================
set_property -dict { PACKAGE_PIN "P19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN "P20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN "T21" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN "R19" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[3] }]
set_property -dict { PACKAGE_PIN "V22" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[4] }]
set_property -dict { PACKAGE_PIN "U21" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[5] }]
set_property -dict { PACKAGE_PIN "T20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[6] }]
set_property -dict { PACKAGE_PIN "W20" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { led[7] }]

# =============================================================================
#  USB serial (onboard FT2232 channel B -- 0xA5 control protocol + telemetry)
# =============================================================================
set_property -dict { PACKAGE_PIN "AA21" IOSTANDARD LVCMOS33 SLEW SLOW }            [get_ports { usb_tx }]
set_property -dict { PACKAGE_PIN "AA20" IOSTANDARD LVCMOS33 PULLUP TRUE }          [get_ports { usb_rx }]

# =============================================================================
#  HDMI OUT (design's TX -> projector) = Hd V2 PORT 1
# =============================================================================
set_property -dict { PACKAGE_PIN "C14" IOSTANDARD LVCMOS33 PULLUP TRUE }   [get_ports { hdmi_tx_scl }]
set_property -dict { PACKAGE_PIN "C15" IOSTANDARD LVCMOS33 PULLUP TRUE }   [get_ports { hdmi_tx_sda }]
set_property -dict { PACKAGE_PIN "E13" IOSTANDARD LVCMOS33 PULLDOWN TRUE } [get_ports { hdmi_tx_hpd }]
set_property -dict { PACKAGE_PIN "E19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_clk_p }]
set_property -dict { PACKAGE_PIN "D19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_clk_n }]
set_property -dict { PACKAGE_PIN "B15" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[0] }]
set_property -dict { PACKAGE_PIN "B16" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[0] }]
set_property -dict { PACKAGE_PIN "F19" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[1] }]
set_property -dict { PACKAGE_PIN "F20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[1] }]
set_property -dict { PACKAGE_PIN "B20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_p[2] }]
set_property -dict { PACKAGE_PIN "A20" IOSTANDARD TMDS_33 } [get_ports { hdmi_tx_n[2] }]

# =============================================================================
#  HDMI IN (design's RX <- PC) = Hd V2 PORT 2
# =============================================================================
set_property -dict { PACKAGE_PIN "D15" IOSTANDARD LVCMOS33 } [get_ports { hdmi_rx_cec }]
set_property -dict { PACKAGE_PIN "D14" IOSTANDARD LVCMOS33 } [get_ports { hdmi_rx_hpa }]
set_property -dict { PACKAGE_PIN "G21" IOSTANDARD LVCMOS33 } [get_ports { hdmi_rx_scl }]
set_property -dict { PACKAGE_PIN "G22" IOSTANDARD LVCMOS33 } [get_ports { hdmi_rx_sda }]
set_property -dict { PACKAGE_PIN "C18" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_clk_p }]
set_property -dict { PACKAGE_PIN "C19" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_clk_n }]
set_property -dict { PACKAGE_PIN "F13" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_p[0] }]
set_property -dict { PACKAGE_PIN "F14" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_n[0] }]
set_property -dict { PACKAGE_PIN "F16" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_p[1] }]
set_property -dict { PACKAGE_PIN "E17" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_n[1] }]
set_property -dict { PACKAGE_PIN "F18" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_p[2] }]
set_property -dict { PACKAGE_PIN "E18" IOSTANDARD TMDS_33 } [get_ports { hdmi_rx_n[2] }]

# TMDS DATA-LANE ORDER: verified index-preserving for lane 0 (Au tx_data[0]=hdmi_data_1[0]).
# Lanes 1/2 assumed index-preserving (same Hd board, same channel routing). If R/B come out
# swapped on hardware, swap hdmi_{tx,rx}_[1]<->[2] here -- a trivial fix, flagged deliberately.

# =============================================================================
#  Camera handshake C1 (element B27-B30, bank 14) -- same element pins as the Au
# =============================================================================
set_property -dict { PACKAGE_PIN "Y19" IOSTANDARD LVCMOS33 SLEW FAST }                 [get_ports { C1_in[0]  }]
set_property -dict { PACKAGE_PIN "V20" IOSTANDARD LVCMOS33 SLEW FAST }                 [get_ports { C1_out[0] }]
set_property -dict { PACKAGE_PIN "Y18" IOSTANDARD LVCMOS33 SLEW FAST }                 [get_ports { C1_out[1] }]
set_property -dict { PACKAGE_PIN "U20" IOSTANDARD LVCMOS33 SLEW FAST PULLDOWN TRUE }   [get_ports { C1_in[1]  }]

# =============================================================================
#  SLI R/G/B/orient switches (newSW) -- spare bank-35 3.3 V element pins.
#  NOTE: the Pt camera stack does NOT break these out (bank 13 = B33-B78 is all camera).
#  On the Pt the switches are driven OVER USB via reg 0x13 (sw_en override), so these pins
#  are placeholders that keep the ports legal; the defaults (down/up) match Au2.xdc so the
#  pattern is off by default if nothing drives them.
# =============================================================================
set_property -dict { PACKAGE_PIN "E2" IOSTANDARD LVCMOS33 SLEW FAST PULLDOWN TRUE } [get_ports { newSW[0] }]
set_property -dict { PACKAGE_PIN "L5" IOSTANDARD LVCMOS33 SLEW FAST PULLUP TRUE }   [get_ports { newSW[1] }]
set_property -dict { PACKAGE_PIN "N5" IOSTANDARD LVCMOS33 SLEW FAST PULLUP TRUE }   [get_ports { newSW[2] }]
set_property -dict { PACKAGE_PIN "P6" IOSTANDARD LVCMOS33 SLEW FAST PULLUP TRUE }   [get_ports { newSW[3] }]

# =============================================================================
#  Second camera handshake C2 (unused on the Pt -- the camera is integrated).
#  C2_out on spare bank-35 pins; C2_in left unconstrained, as in Au2.xdc.
# =============================================================================
set_property -dict { PACKAGE_PIN "G3" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { C2_out[0] }]
set_property -dict { PACKAGE_PIN "H3" IOSTANDARD LVCMOS33 SLEW FAST } [get_ports { C2_out[1] }]

# =============================================================================
#  PYTHON 1300 camera -- SPI + discrete control (CMOS, banks 14/35).
#  These ARE top-level ports (the SPI mailbox in usb_link), so they must be pinned even in
#  Phase 1. Balls are the Pt V2 assignment from pt_camera.xdc. The 7 LVDS pairs are NOT here
#  -- the receiver chain is not integrated yet (task #12). On the Pt these run at 3.3 V and
#  are SAFE, unlike the Au: this is the real bring-up path for the chip-ID read.
#  Pull directions match the board's external 10k resistors (ss_n up, reset_n/triggers down).
# =============================================================================
set_property -dict { PACKAGE_PIN "AB22" IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 }                [get_ports { cam_mosi }]
set_property -dict { PACKAGE_PIN "AB21" IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 }                [get_ports { cam_sck }]
set_property -dict { PACKAGE_PIN "AB18" IOSTANDARD LVCMOS33 }                                  [get_ports { cam_miso }]
set_property -dict { PACKAGE_PIN "N2"   IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLUP TRUE }    [get_ports { cam_ss_n }]
set_property -dict { PACKAGE_PIN "E3"   IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE }  [get_ports { cam_reset_n }]
set_property -dict { PACKAGE_PIN "F3"   IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE }  [get_ports { cam_trigger[0] }]
set_property -dict { PACKAGE_PIN "P2"   IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE }  [get_ports { cam_trigger[1] }]
set_property -dict { PACKAGE_PIN "M2"   IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8 PULLDOWN TRUE }  [get_ports { cam_trigger[2] }]
set_property -dict { PACKAGE_PIN "L1"   IOSTANDARD LVCMOS33 }                                  [get_ports { cam_monitor[0] }]
set_property -dict { PACKAGE_PIN "M3"   IOSTANDARD LVCMOS33 }                                  [get_ports { cam_monitor[1] }]

set_false_path -to   [get_ports {cam_sck cam_mosi cam_ss_n cam_reset_n cam_trigger[*]}]
set_false_path -from [get_ports {cam_miso cam_monitor[*]}]

# =============================================================================
#  PYTHON 1300 camera -- LVDS receive interface (bank 13 @ 2.5 V), task #12.
#
#  Balls + rationale are from pt_camera.xdc (the camera-only proof that iocheck
#  already placed clean). Folded in here now that the receiver chain is integrated.
#  Bank 13 is 2.5 V, strapped in HARDWARE by the camera element (VBSEL A/B both HIGH);
#  LVDS_25 + internal DIFF_TERM both require it. LVDS is on the DF40 EVEN row (SRCC
#  pairs, no MRCC) -- forced by geometry, see pt_camera.xdc / README 5.1.1.
#
#  PLL MODE (CAMERA_SENSOR_PROTOCOL.md §4): the FPGA drives 72 MHz on cam_clk_pll
#  (CMOS) and the sensor's internal PLL makes the 360 MHz DDR bit clock it forwards
#  back on cam_clkout. lvds_clock_in (elem B76/B78, W15/W16) is NOT driven and is
#  deliberately absent here -- those balls fall under UNUSEDPIN PULLDOWN below.
# =============================================================================
# forwarded 360 MHz bit clock -- SRCC pair, reaches BUFIO + BUFR inside cam_lvds_rx
set_property -dict {PACKAGE_PIN Y11 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_p}]
set_property -dict {PACKAGE_PIN Y12 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_clkout_n}]
# 4 data lanes, 720 Mbps DDR
set_property -dict {PACKAGE_PIN U15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[0]}]
set_property -dict {PACKAGE_PIN V15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[0]}]
set_property -dict {PACKAGE_PIN AB16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[1]}]
set_property -dict {PACKAGE_PIN AB17 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[1]}]
set_property -dict {PACKAGE_PIN Y16  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[2]}]
set_property -dict {PACKAGE_PIN AA16 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[2]}]
set_property -dict {PACKAGE_PIN T14  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_p[3]}]
set_property -dict {PACKAGE_PIN T15  IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_d_n[3]}]
# sync channel
set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_p}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVDS_25 DIFF_TERM TRUE} [get_ports {cam_sync_n}]
# 72 MHz sensor PLL reference clock -- CMOS out (bank 14/35 @ 3.3 V), forwarded via ODDR
set_property -dict {PACKAGE_PIN AA18 IOSTANDARD LVCMOS33} [get_ports {cam_clk_pll}]

# 360 MHz forwarded bit clock. cam_lvds_rx's BUFR /5 word clock is derived from it.
create_clock -name cam_clkout -period 2.778 [get_ports cam_clkout_p]
# clk_pll is a forwarded clock (ODDR->OBUF), no setup relationship to capture.
set_false_path -to [get_ports cam_clk_pll]
# The camera receive clocks (360 MHz + the BUFR /5 word clock) cross into clk100 only
# through cam_line_buf's dual-port BRAM (quasi-static line readback); the 72 MHz sensor-ref
# MMCM only feeds an output pin. Both are asynchronous to the HDMI / system domains.
set_clock_groups -name cam_rx_async  -asynchronous -group [get_clocks -include_generated_clocks cam_clkout]
set_clock_groups -name cam_ref_async -asynchronous -group [get_clocks -of_objects [get_pins i_cam_mmcm/CLKOUT0]]

set_property BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN [current_design]
