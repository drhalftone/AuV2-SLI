# ============================================================================
# led_test.xdc -- constraints for the Stack-board bring-up smoke test
# Target: Alchitry Au V2  (XC7A35T-FTG256-2)
# Pins from ROADMAP.md section 5.2 (Bank A low -> Bank B high remap).
# ============================================================================

# --- Bitstream / config (so the .bin can also be flashed permanently) ---
set_property BITSTREAM.GENERAL.COMPRESS        TRUE  [current_design]
set_property CONFIG_VOLTAGE                     3.3   [current_design]
set_property CFGBVS                             VCCO  [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE        33    [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH      1     [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE     YES   [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR    NO    [current_design]

# ----------------------------------------------------------------------------
# Inputs -- Bank B high (camera + DIP switch), via DF40 -> Hd pass-through -> Au.
#
# NOTE: every line uses PULLDOWN here so an undriven pin parks LOW (LED off) and
# any driven HIGH lights the LED -- the clearest "high = on" bring-up semantics.
# This deliberately differs from the *functional* pulls in ROADMAP 5.2
# (mixed PULLUP/PULLDOWN for safe SLI defaults); use those, not these, in the
# real Au2.xdc remap.
#
# A SPDT DIP switch drives the net hard high/low, so the internal pull is
# irrelevant for SW1 -- it matters only for floating camera lines.
# ----------------------------------------------------------------------------

# Camera GPIO (MASTER JST)
set_property -dict { PACKAGE_PIN R11  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { cam_ready   }] ;# DF40 27 / JST5  (camera out)
set_property -dict { PACKAGE_PIN R16  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { cam_trig    }] ;# DF40 28 / JST2  (camera in -- jumper to test)
set_property -dict { PACKAGE_PIN R10  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { cam_pattern }] ;# DF40 29 / JST4  (camera in -- jumper to test)
set_property -dict { PACKAGE_PIN R15  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { cam_mode    }] ;# DF40 30 / JST3  (camera out)

# DIP switches (SW1, SPDT)
set_property -dict { PACKAGE_PIN K5   IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { sw_hvsv     }] ;# DF40 33 / SW1-1
set_property -dict { PACKAGE_PIN N16  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { sw_blue     }] ;# DF40 34 / SW1-2
set_property -dict { PACKAGE_PIN E6   IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { sw_green    }] ;# DF40 35 / SW1-3
set_property -dict { PACKAGE_PIN M16  IOSTANDARD LVCMOS33  PULLDOWN TRUE } [get_ports { sw_red      }] ;# DF40 36 / SW1-4

# ----------------------------------------------------------------------------
# Outputs -- 8 user LEDs on the Au V2 (same balls as Au2.xdc).
# ----------------------------------------------------------------------------
set_property -dict { PACKAGE_PIN K13  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[0] }] ;# led0
set_property -dict { PACKAGE_PIN K12  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[1] }] ;# led1
set_property -dict { PACKAGE_PIN L14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[2] }] ;# led2
set_property -dict { PACKAGE_PIN L13  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[3] }] ;# led3
set_property -dict { PACKAGE_PIN M15  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[4] }] ;# led4
set_property -dict { PACKAGE_PIN M14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[5] }] ;# led5
set_property -dict { PACKAGE_PIN M12  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[6] }] ;# led6
set_property -dict { PACKAGE_PIN P14  IOSTANDARD LVCMOS33  SLEW FAST } [get_ports { led[7] }] ;# led7
