set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33 [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 1 [current_design]
set_property BITSTREAM.CONFIG.SPI_FALL_EDGE YES [current_design]
####################################################################################################################
#                                               CLOCK 100MHz                                                       #
####################################################################################################################
##Clock Signal
set_property -dict { PACKAGE_PIN "N14"    IOSTANDARD LVCMOS33       SLEW FAST} [get_ports { clk100 }]     ;     
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports clk100]
create_clock -add -name hdmi_clk -period 13.300 -waveform {0 5} [get_ports hdmi_rx_clk_p] 
##HDMI in 1080p or 720p or 1080p@50
#create_clock -add -name hdmi_clk -period 6.734 -waveform {0 5} [get_ports hdmi_rx_clk_p]  
#create_clock -add -name hdmi_clk -period 8.08080808081 -waveform {0 5} [get_ports hdmi_rx_clk_p] 

# ignore inter-clock path 
set_false_path -from [get_clocks sys_clk_pin] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]]
set_false_path -through [get_ports led[*]]
set_false_path -through [get_ports newSW[*]]
set_false_path -from [get_clocks hdmi_clk] -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]]
set_false_path -from [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0]] -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT1]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]] -to [get_clocks -of_objects [get_pins i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT2]]
set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT1]]
set_false_path -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT1]]

# --- Offline + recovered pixel clocks are asynchronous to all other domains ---
# The OFFLINE (drp_clkgen13 MMCM) and PASSTHROUGH (recovered HDMI-input MMCM) pixel
# clocks are mutually exclusive (clk_selector muxes between them) AND only meet clk100
# at true CDCs (status/telemetry sampling, quasi-static mode_idx, the DRP control port).
# A single-group -asynchronous makes each pixel domain async to EVERYTHING else, which
# covers offline<->recovered, offline<->clk100, and recovered<->clk100. Otherwise STA
# analyses those crossings with ~0ns requirements -> phantom violations through the
# pixel datapath (e.g. phaseV -> DVID TMDS, VPolarity -> edid_reader telemetry).
# (Supersedes the stale line 24-27 false-paths that pointed at ref_clk's old offline clk625.
#  clk10 is already false-pathed everywhere by the CLKOUT1 false-paths above.)
# NOTE: -of_objects [get_pins ...] (not clock names) so it resolves regardless of the
# order IP-generated clocks are created during constraint processing.
# THREE explicit, mutually-asynchronous groups (one set_clock_groups so they compose
# correctly): [offline pixel] | [recovered pixel] | [system: clk100 + ref_clk MMCM].
# Cross-group paths (offline<->recovered, offline<->system telemetry/mode_idx, recovered
# <->system) are all CDCs handled by 2FF/toggle syncs or quasi-static; cutting them removes
# the phantom ~0ns-requirement violations. Intra-group stays timed (e.g. clk100<->clk200).
# System group lists ref_clk's MMCM outputs by pin wildcard + sys_clk_pin; it deliberately
# EXCLUDES offline_pix (a different MMCM) so offline_pix is only in group 1.
set_clock_groups -name pix_domains_async -asynchronous \
  -group [get_clocks -of_objects [get_pins {i_drp_clkgen13/i_mmcm/CLKOUT0 i_drp_clkgen13/i_mmcm/CLKOUT1 i_drp_clkgen13/i_mmcm/CLKOUT2}]] \
  -group [get_clocks -of_objects [get_pins {i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT0 i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT1 i_hdmi_io/i_hdmi_input/hdmi_MMCME2_BASE_inst/CLKOUT2}]] \
  -group "[get_clocks sys_clk_pin] [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT*]]"

# set CE false path
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch1/i_deser/ISERDESE2_slave/CE1] 
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch2/i_deser/ISERDESE2_slave/CE1] 
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch1/i_deser/ISERDESE2_master/CE1] 
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch0/i_deser/ISERDESE2_slave/CE1] 
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch0/i_deser/ISERDESE2_master/CE1] 
set_max_delay 26.0 -from [get_pins {i_hdmi_io/i_hdmi_input/CE_Delay/m_reg[1]/C}] -to [get_pins i_hdmi_io/i_hdmi_input/ch2/i_deser/ISERDESE2_master/CE1] 
####################################################################################################################
#                                               LEDs                                                               #
####################################################################################################################
set_property -dict { PACKAGE_PIN "K13"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[0] }];                      # IO_L21P_T3_DQS_15             Sch = led0
set_property -dict { PACKAGE_PIN "K12"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[1] }];                      # IO_L21N_T3_DQS_A18_15         Sch = led1
set_property -dict { PACKAGE_PIN "L14"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[2] }];                      # IO_L22P_T3_A17_15             Sch = led2
set_property -dict { PACKAGE_PIN "L13"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[3] }];                      # IO_L22N_T3_A16_15             Sch = led3
set_property -dict { PACKAGE_PIN "M15"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[4] }];                      # IO_L23P_T3_FOE_B_15           Sch = led4
set_property -dict { PACKAGE_PIN "M14"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[5] }];                      # IO_L23N_T3_FWE_B_15           Sch = led5
set_property -dict { PACKAGE_PIN "M12"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[6] }];                      # IO_L24P_T3_RS1_15             Sch = led6
set_property -dict { PACKAGE_PIN "P14"   IOSTANDARD LVCMOS33    SLEW FAST} [get_ports { led[7] }];                      # IO_L24N_T3_RS0_15             Sch = led7

####################################################################################################################
#                              USB serial (FT2232H channel B) - status telemetry, TX only                          #
####################################################################################################################
# Stock Alchitry Au (V1) USB-UART pin: FPGA TX -> PC. usb_rx (P15) left unconnected (one-way telemetry).
set_property -dict { PACKAGE_PIN "P16"   IOSTANDARD LVCMOS33    SLEW SLOW} [get_ports { usb_tx }];
set_false_path -to [get_ports usb_tx]

####################################################################################################################
#                       HDMI-OUT DDC/HPD (Hd V2 port 1, bank 35) - TX-side EDID reading                            #
####################################################################################################################
# Open-drain DDC (internal weak pull-ups; the Hd V2 also level-shifts/pulls these). HPD is an input.
set_property -dict { PACKAGE_PIN "C7"   IOSTANDARD LVCMOS33   PULLUP TRUE } [get_ports { hdmi_tx_scl }];   # A72
set_property -dict { PACKAGE_PIN "C6"   IOSTANDARD LVCMOS33   PULLUP TRUE } [get_ports { hdmi_tx_sda }];   # A70
set_property -dict { PACKAGE_PIN "B7"   IOSTANDARD LVCMOS33   PULLDOWN TRUE } [get_ports { hdmi_tx_hpd }]; # A78
set_false_path -from [get_ports hdmi_tx_hpd]
set_false_path -to   [get_ports {hdmi_tx_scl hdmi_tx_sda}]
set_false_path -from [get_ports {hdmi_tx_scl hdmi_tx_sda}]



####################################################################################################################
#                                               HDMI Signals                                                       #
####################################################################################################################

##HDMI out
set_property -dict { PACKAGE_PIN "F3"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_clk_n}];  #BANKA 46
set_property -dict { PACKAGE_PIN "F4"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_clk_p}]; #BANKA 48
set_property -dict { PACKAGE_PIN "D5"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_n[0] }]; #BANKA 52
set_property -dict { PACKAGE_PIN "D6"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_p[0] }]; #BANKA 54 
set_property -dict { PACKAGE_PIN "B1"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_n[1] }]; #BANKA 58
set_property -dict { PACKAGE_PIN "C1"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_p[1] }];  #BANKA 60
set_property -dict { PACKAGE_PIN "A2"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_n[2] }];  #BANKA 64  
set_property -dict { PACKAGE_PIN "B2"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_tx_p[2] }];  #BANKA 66

##HDMI in

# HDMI_In
set_property -dict { PACKAGE_PIN "B5"    IOSTANDARD LVCMOS33 }  [get_ports { hdmi_rx_cec  }];    #BANKA 75    
set_property -dict { PACKAGE_PIN "E5"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_clk_n}];    #BANKA 45
set_property -dict { PACKAGE_PIN "F5"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_clk_p}];    #BANKA 47
set_property -dict { PACKAGE_PIN "B6"    IOSTANDARD LVCMOS33 }  [get_ports { hdmi_rx_hpa  }];    #BANKA 77  
set_property -dict { PACKAGE_PIN "C3"    IOSTANDARD LVCMOS33 }  [get_ports { hdmi_rx_scl  }];      #BANKA 71                          
set_property -dict { PACKAGE_PIN "C2"    IOSTANDARD LVCMOS33 }  [get_ports { hdmi_rx_sda  }];      #BANKA 69 
set_property -dict { PACKAGE_PIN "A3"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_n[0] }];    #BANKA 51
set_property -dict { PACKAGE_PIN "B4"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_p[0] }];    #BANKA 53
set_property -dict { PACKAGE_PIN "A4"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_n[1] }];    #BANKA 57
set_property -dict { PACKAGE_PIN "A5"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_p[1] }];    #BANKA 59
set_property -dict { PACKAGE_PIN "D1"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_n[2] }];    #BANKA 63
set_property -dict { PACKAGE_PIN "E2"    IOSTANDARD TMDS_33  }  [get_ports { hdmi_rx_p[2] }];    #BANKA 65


#####################################################################################################################
##                                          Bank A for Camera 1                                                  #
#####################################################################################################################
## A17    trigger ready  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "H2"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_in[0]}];                   # IO_L1P_T0_AD0P_15             Sch = GPIO_20_P
## A23  trigger  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "F2"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_out[0]}];                     # IO_L3P_T0_DQS_AD1P_15         Sch = GPIO_19_P
## A29  first frame  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "G5"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_out[1]}];                      # IO_L20P_T3_A20_15             Sch = GPIO_18_P
## A35 hdmi switch  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "G2"   IOSTANDARD LVCMOS33   SLEW FAST  PULLDOWN TRUE } [get_ports {C1_in[1]}];        # IO_L19P_T3_A22_15  Sch = GPIO_17_P  (pulled low: default passthrough for color-bar test)
#####################################################################################################################
##                                          Bank A for    Camera 2                                                  #
#####################################################################################################################
## A18    reserved 
#set_property -dict  { PACKAGE_PIN "K3"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C2_in[0]}];                      # IO_L2P_T0_16                  Sch = GPIO_40_P
## A24    trigger
set_property -dict  { PACKAGE_PIN "J3"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C2_out[0]}];                      # IO_L1P_T0_16                  Sch = GPIO_39_P
## A30    reserved  
set_property -dict  { PACKAGE_PIN "H5"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C2_out[1]}];                      # IO_L4P_T0_16                  Sch = GPIO_38_P  
## A36   reserved 
#set_property -dict  { PACKAGE_PIN "J5"   IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C2_in[1]}];                      # IO_L6P_T0_16                  Sch = GPIO_37_P

#####################################################################################################################
##                                          Bank A for    newSW                                                #
#####################################################################################################################
## A5   Scan Orientation  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "M6"   IOSTANDARD LVCMOS33   SLEW FAST  PULLDOWN TRUE } [get_ports {newSW[0]}];        # A5 orientation: default 0 = vertical stripes
## A6    Blue Enable  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "N9"   IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[1]}];          # A6 Blue enable: default ON
## A11    Green Enable  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "K1"   IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[2]}];          # A11 Green enable: default ON
## A12   Red Enable  -- [BANK-B REMAP active: Bank-A line disabled]
#set_property -dict  { PACKAGE_PIN "L3"   IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[3]}];          # A12 Red enable: default ON

#####################################################################################################################
##           STACKING-BOARD (DF40 daughter board) BANK-B REMAP  --  ACTIVE
##  For the LauCameraTrigger_Alchitry_Stack board (taps Bank B / Site C, DF40C-80DP).
##  Camera 1 + the 4 config switches now live on Bank B; the 8 Bank-A lines above are disabled.
##  Balls verified vs Alchitry AuV2Pin.kt (V2). Pulls per ROADMAP.md 5.2. (Camera 2 J3/H5 stays Bank A.)
#####################################################################################################################
## B27  trigger ready (cam in)
set_property -dict  { PACKAGE_PIN "R11"  IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_in[0]}];                       # B27
## B28  trigger (cam out)
set_property -dict  { PACKAGE_PIN "R16"  IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_out[0]}];                      # B28
## B29  first frame (cam out)
set_property -dict  { PACKAGE_PIN "R10"  IOSTANDARD LVCMOS33   SLEW FAST } [get_ports {C1_out[1]}];                      # B29
## B30  mode / hdmi switch (cam in)  -- default low
set_property -dict  { PACKAGE_PIN "R15"  IOSTANDARD LVCMOS33   SLEW FAST  PULLDOWN TRUE } [get_ports {C1_in[1]}];        # B30
## B33  scan orientation (HvsV)      -- default 0 = vertical
set_property -dict  { PACKAGE_PIN "K5"   IOSTANDARD LVCMOS33   SLEW FAST  PULLDOWN TRUE } [get_ports {newSW[0]}];        # B33
## B34  blue enable                  -- default ON
set_property -dict  { PACKAGE_PIN "N16"  IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[1]}];          # B34
## B35  green enable                 -- default ON
set_property -dict  { PACKAGE_PIN "E6"   IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[2]}];          # B35
## B36  red enable                   -- default ON
set_property -dict  { PACKAGE_PIN "M16"  IOSTANDARD LVCMOS33   SLEW FAST  PULLUP TRUE } [get_ports {newSW[3]}];          # B36

#####inter-clock false path######
#set_false_path -from [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT1]] -to [get_clocks -of_objects [get_pins ref_clk_pll/inst/mmcm_adv_inst/CLKOUT2]]