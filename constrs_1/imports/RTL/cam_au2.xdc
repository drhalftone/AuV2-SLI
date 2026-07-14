# =============================================================================
# cam_au2.xdc  --  PYTHON 1300 camera element, SPI bring-up on the Alchitry Au V2
#
# Target: XC7A35T-FTG256-2  (Au V2).  Companion to Au2.xdc, which it does not touch.
#
# SCOPE: THE 10 CMOS CONTROL SIGNALS ONLY. There is NO LVDS here, and that is not an
# omission -- see the block below. This is the constraint set for the chip-ID hardware
# gate (CAMERA_RTL_PLAN.md #5): read register 0, expect 0x50D0, and thereby prove the
# power tree, the DF40 pin map, the stack pass-through and the RTL in one transaction.
#
# ---------------------------------------------------------------------
# !!  WHY THERE IS NO LVDS IN THIS FILE  !!
#
# On the Pt V2 all seven LVDS pairs sit in ONE bank (13) at 2.5 V. On the Au V2 the SAME
# element-bus pins scatter across THREE banks at three FIXED voltages:
#
#     clock_out+-  B40/B42 -> P13/N13  bank 14  -> 3.3 V only. No LVDS_25, no DIFF_TERM.
#     dout0+-      B46/B48 -> D9/D10   bank 15  -> 1.35 V. The DDR3 bank. Alchitry:
#                                                  "The 1.35V pins are not 3.3V tolerant."
#     dout1-3, sync, lvds_clock_in     bank 34  -> 3.3/2.5/1.8 selectable
#
# The forwarded bit clock is stranded in a bank that can never reach 2.5 V, so the one
# pair that matters most gets neither LVDS_25 nor DIFF_TERM. This is NOT fixable in RTL.
# Real pixels need the Pt V2. See CAMERA_IO_MAP.md section 8.2.
#
# It is nevertheless SAFE to stack the camera on an Au: the sensor's LVDS drivers are
# POWERED DOWN AT RESET (register 112 = 0, all three fields), so dout0 never drives those
# bank-15 pins -- provided NOTHING WRITES REGISTER 112 ON AN Au BUILD.
# ---------------------------------------------------------------------
#
# VBSEL: the camera board straps control-header pin 38 (VBSEL_A) high, which on the Au
# selects the VCCO of bank 34. The SLI design uses ZERO bank-34 pins (its Bank-B remap
# lands in banks 14/35, both hardwired 3.3 V), so the strap is harmless. Recommended
# anyway: leave that 1k resistor unpopulated on an Au build -- nothing here needs 2.5 V,
# and it keeps the whole board uniformly 3.3 V. See CAMERA_IO_MAP.md section 8.3.
#
# PIN MAP -- element bus -> Au V2 ball. THREE independent sources agree:
#   1. Alchitry Labs 2, AuV2Pin.kt
#   2. Au2.xdc's own commented-out Bank-A lines (M6 = "A5", N9 = "A6", K1 = "A11", L3 = "A12")
#   3. The Pt-side derivation in CAMERA_IO_MAP.md section 4, via the shared element bus
#
# >>> THE NAMESPACE TRAP: Au2.xdc puts HDMI TMDS on FPGA BALLS literally named A3, A4, A5.
# >>> The camera uses ELEMENT PINS also named A3, A4, A5. They are unrelated.
# >>> Element A3 -> ball N6. Never carry a pin number between the two namespaces.
# =============================================================================

# --- SPI (element A3..A5, bank 14 -- the config bank; no external pulls) -------------
# Bank 14 floats Hi-Z until DONE. That is safe because ss_n (below) is pulled HIGH
# externally, so the sensor ignores whatever sck/mosi do during configuration.
# sensor 2   elem A3
set_property -dict { PACKAGE_PIN "N6"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8 } [get_ports {cam_mosi}]
# sensor 4   elem A5
set_property -dict { PACKAGE_PIN "M6"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8 } [get_ports {cam_sck}]
# sensor 3   elem A4   (input; Hi-Z outside a read -- the board fits a pull)
set_property -dict { PACKAGE_PIN "P9"  IOSTANDARD LVCMOS33 } [get_ports {cam_miso}]

# --- Discretes (element A9..A17, bank 35) --------------------------------------------
# THE PULL DIRECTIONS MATCH THE BOARD'S EXTERNAL 10k RESISTORS. Do not invert them.
# The external resistors are the primary guarantee -- they hold during the whole FPGA
# configuration window, when the internal ones do nothing. These make the internal pulls
# AGREE with the external ones rather than fight them. See the camera board README 5.2.
#
# sensor 47  elem A10  R3 = 10k PULL-UP.   A floating ss_n could read as ASSERTED, and
#                                          the sensor would clock in garbage.
set_property -dict { PACKAGE_PIN "L2"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8  PULLUP TRUE }   [get_ports {cam_ss_n}]
# sensor 46  elem A9   R4 = 10k PULL-DOWN. Holds the sensor IN RESET until the FPGA is
#                                          configured and the host deliberately releases
#                                          it (reg 0x37 bit 7). Fail-safe direction.
set_property -dict { PACKAGE_PIN "J1"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8  PULLDOWN TRUE } [get_ports {cam_reset_n}]
# sensor 41/42/43  elem A11/A12/A15   R5/R6/R7 = 10k PULL-DOWN: no spurious exposures
#                                     during configuration.
set_property -dict { PACKAGE_PIN "K1"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8  PULLDOWN TRUE } [get_ports {cam_trigger[0]}]
set_property -dict { PACKAGE_PIN "L3"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8  PULLDOWN TRUE } [get_ports {cam_trigger[1]}]
set_property -dict { PACKAGE_PIN "H1"  IOSTANDARD LVCMOS33  SLEW SLOW  DRIVE 8  PULLDOWN TRUE } [get_ports {cam_trigger[2]}]
# sensor 44/45  elem A16/A17  (inputs)
set_property -dict { PACKAGE_PIN "K2"  IOSTANDARD LVCMOS33 } [get_ports {cam_monitor[0]}]
set_property -dict { PACKAGE_PIN "H2"  IOSTANDARD LVCMOS33 } [get_ports {cam_monitor[1]}]

# --- Timing --------------------------------------------------------------------------
# These are asynchronous, and slow by construction. sck runs at 1 MHz (usb_link.v sets
# SCK_HZ = 1_000_000), so mosi is driven on the falling edge and the sensor samples it
# 500 ns later on the rising edge -- against a required setup of 20 ns (datasheet Table
# 11, ts_mosi). Four orders of magnitude of margin; board skew is picoseconds. The SPI
# timing is guaranteed by the clock divider, not by static timing analysis, so there is
# no path here for STA to usefully constrain.
#
# miso is captured through a 2FF synchroniser in cam_spi_master, sampled at the end of
# the 500 ns sck-high phase -- likewise nothing for STA to say.
set_false_path -to   [get_ports {cam_sck cam_mosi cam_ss_n cam_reset_n cam_trigger[*]}]
set_false_path -from [get_ports {cam_miso cam_monitor[*]}]

# --- NOT constrained here ------------------------------------------------------------
#   cam_clk_pll (element A6 -> ball N9, bank 14). The sensor's 72 MHz PLL reference.
#   Not needed for SPI -- the sensor's SPI is ASYNCHRONOUS to its system clock and works
#   with no clock running at all, which is the entire reason the Au bring-up is possible.
#   It arrives with the LVDS receiver (CAMERA_RTL_PLAN.md #8) and needs an MMCM.
#
#   The 7 LVDS pairs. Deliberately unconstrained and undriven -- see the header.
