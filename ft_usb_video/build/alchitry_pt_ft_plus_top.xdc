# Ft+ V2 pinout for the TOP of the Pt V2.
# Derived from Alchitry ft_plus_v2.acf (element pins) resolved through PtV2TopPin
# (Alchitry Labs 2.0.52). Verified: the same tool maps A41->D17 on the BOTTOM, which
# matches alchitry_pt_ft_plus_bottom.xdc -- so this TOP map is from the same source.

set_property PACKAGE_PIN H4 [get_ports {ft_clk}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_clk}]
# ft_clk => 100 MHz, sourced by the FT601 (CLKOUT)
create_clock -period 10.0 -name ft_clk_top -waveform {0.000 5.0} [get_ports ft_clk]
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks ft_clk_top]

# -----------------------------------------------------------------------------
# FT601 245-sync-FIFO interface timing.  ADDED 2026-07-30.
#
# These were MISSING, and their absence was a real bug, not a cosmetic one: with
# no set_output_delay the tool never timed the bus at all, reported WNS >= 0, and
# shipped a design whose upper 16 data bits missed the FT601's setup window.
# ft_data[15:0] arrived perfect while ft_data[31:16] (far bank) was corrupt --
# see the byte-lane histogram in rtl/ft601_sync_tx.v. Silent, because an
# unconstrained path is not a failing path.
#
# FT601Q synchronous-FIFO AC spec (FTDI FT600Q/FT601Q datasheet):
#   master -> FT601 : setup 1.0 ns, hold 1.0 ns  (DATA/BE/WR#/RD#/OE# to CLK^)
#   FT601  -> master: TXE#/RXF#/data valid <= 7.0 ns after CLK^, >= 1.0 ns hold
#
# Output: -max = the FT601's setup requirement; -min = -(its hold requirement).
# Input : -max = the FT601's clock-to-out max; -min = its clock-to-out min.
# Board trace delay on the Ft+ stack connector is short (a few tens of ps) and is
# folded into the margins above rather than modelled separately.
# -----------------------------------------------------------------------------
set ft_out_ports [get_ports {ft_data[*] ft_be[*] ft_wr ft_oe ft_rd}]
set_output_delay -clock ft_clk_top -max  1.0 $ft_out_ports
set_output_delay -clock ft_clk_top -min -1.0 $ft_out_ports

set ft_in_ports [get_ports {ft_txe ft_rxf}]
set_input_delay -clock ft_clk_top -max 7.0 $ft_in_ports
set_input_delay -clock ft_clk_top -min 1.0 $ft_in_ports

# ft_data is bidirectional; this design is TX-only (OE# tied high) so the input
# side of those pads is never sampled. Constrain it anyway so a future read path
# cannot inherit the same silent hole.
set_input_delay -clock ft_clk_top -max 7.0 [get_ports {ft_data[*] ft_be[*]}]
set_input_delay -clock ft_clk_top -min 1.0 [get_ports {ft_data[*] ft_be[*]}]

# ft_reset/ft_wakeup are static level controls, not bus-timed signals.
set_false_path -to [get_ports {ft_reset ft_wakeup}]

set_property PACKAGE_PIN AB22 [get_ports {ft_wakeup}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wakeup}]

set_property PACKAGE_PIN AB21 [get_ports {ft_reset}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_reset}]

set_property PACKAGE_PIN N2 [get_ports {ft_rxf}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_rxf}]

set_property PACKAGE_PIN P2 [get_ports {ft_txe}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_txe}]

set_property PACKAGE_PIN AB18 [get_ports {ft_oe}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_oe}]

set_property PACKAGE_PIN AA18 [get_ports {ft_rd}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_rd}]

set_property PACKAGE_PIN E3 [get_ports {ft_wr}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wr}]

set_property PACKAGE_PIN M2 [get_ports {ft_be[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[0]}]

set_property PACKAGE_PIN M1 [get_ports {ft_be[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[1]}]

set_property PACKAGE_PIN L1 [get_ports {ft_be[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[2]}]

set_property PACKAGE_PIN F3 [get_ports {ft_be[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[3]}]

set_property PACKAGE_PIN R14 [get_ports {ft_data[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[0]}]

set_property PACKAGE_PIN P14 [get_ports {ft_data[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[1]}]

set_property PACKAGE_PIN R16 [get_ports {ft_data[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[2]}]

set_property PACKAGE_PIN P15 [get_ports {ft_data[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[3]}]

set_property PACKAGE_PIN R17 [get_ports {ft_data[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[4]}]

set_property PACKAGE_PIN P16 [get_ports {ft_data[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[5]}]

set_property PACKAGE_PIN P17 [get_ports {ft_data[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[6]}]

set_property PACKAGE_PIN N17 [get_ports {ft_data[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[7]}]

set_property PACKAGE_PIN W17 [get_ports {ft_data[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[8]}]

set_property PACKAGE_PIN V17 [get_ports {ft_data[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[9]}]

set_property PACKAGE_PIN T18 [get_ports {ft_data[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[10]}]

set_property PACKAGE_PIN R18 [get_ports {ft_data[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[11]}]

set_property PACKAGE_PIN AB20 [get_ports {ft_data[12]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[12]}]

set_property PACKAGE_PIN AA19 [get_ports {ft_data[13]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[13]}]

set_property PACKAGE_PIN V19 [get_ports {ft_data[14]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[14]}]

set_property PACKAGE_PIN V18 [get_ports {ft_data[15]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[15]}]

set_property PACKAGE_PIN G4 [get_ports {ft_data[16]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[16]}]

set_property PACKAGE_PIN H3 [get_ports {ft_data[17]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[17]}]

set_property PACKAGE_PIN G3 [get_ports {ft_data[18]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[18]}]

set_property PACKAGE_PIN P5 [get_ports {ft_data[19]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[19]}]

set_property PACKAGE_PIN P4 [get_ports {ft_data[20]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[20]}]

set_property PACKAGE_PIN P6 [get_ports {ft_data[21]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[21]}]

set_property PACKAGE_PIN N5 [get_ports {ft_data[22]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[22]}]

set_property PACKAGE_PIN M6 [get_ports {ft_data[23]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[23]}]

set_property PACKAGE_PIN M5 [get_ports {ft_data[24]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[24]}]

set_property PACKAGE_PIN L5 [get_ports {ft_data[25]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[25]}]

set_property PACKAGE_PIN L4 [get_ports {ft_data[26]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[26]}]

set_property PACKAGE_PIN K6 [get_ports {ft_data[27]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[27]}]

set_property PACKAGE_PIN J6 [get_ports {ft_data[28]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[28]}]

set_property PACKAGE_PIN E2 [get_ports {ft_data[29]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[29]}]

set_property PACKAGE_PIN D2 [get_ports {ft_data[30]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[30]}]

set_property PACKAGE_PIN M3 [get_ports {ft_data[31]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[31]}]
