# Ft+ pinout for bottom of Pt

set_property PACKAGE_PIN D17 [get_ports {ft_clk}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_clk}]
# ft_clk => 100000000Hz
create_clock -period 10.0 -name ft_clk_13 -waveform {0.000 5.0} [get_ports ft_clk]
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks ft_clk_13]

set_property PACKAGE_PIN AB1 [get_ports {ft_wakeup}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wakeup}]

set_property PACKAGE_PIN AA1 [get_ports {ft_reset}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_reset}]

set_property PACKAGE_PIN A16 [get_ports {ft_rxf}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_rxf}]

set_property PACKAGE_PIN A15 [get_ports {ft_txe}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_txe}]

set_property PACKAGE_PIN AA3 [get_ports {ft_oe}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_oe}]

set_property PACKAGE_PIN Y3 [get_ports {ft_rd}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_rd}]

set_property PACKAGE_PIN D16 [get_ports {ft_wr}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wr}]

set_property PACKAGE_PIN A19 [get_ports {ft_be[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[0]}]

set_property PACKAGE_PIN B21 [get_ports {ft_be[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[1]}]

set_property PACKAGE_PIN A21 [get_ports {ft_be[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[2]}]

set_property PACKAGE_PIN E16 [get_ports {ft_be[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_be[3]}]

set_property PACKAGE_PIN Y2 [get_ports {ft_data[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[0]}]

set_property PACKAGE_PIN W2 [get_ports {ft_data[1]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[1]}]

set_property PACKAGE_PIN AB6 [get_ports {ft_data[2]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[2]}]

set_property PACKAGE_PIN AB7 [get_ports {ft_data[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[3]}]

set_property PACKAGE_PIN Y7 [get_ports {ft_data[4]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[4]}]

set_property PACKAGE_PIN Y8 [get_ports {ft_data[5]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[5]}]

set_property PACKAGE_PIN AB8 [get_ports {ft_data[6]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[6]}]

set_property PACKAGE_PIN AA8 [get_ports {ft_data[7]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[7]}]

set_property PACKAGE_PIN AA6 [get_ports {ft_data[8]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[8]}]

set_property PACKAGE_PIN Y6 [get_ports {ft_data[9]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[9]}]

set_property PACKAGE_PIN AB5 [get_ports {ft_data[10]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[10]}]

set_property PACKAGE_PIN AA5 [get_ports {ft_data[11]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[11]}]

set_property PACKAGE_PIN AB2 [get_ports {ft_data[12]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[12]}]

set_property PACKAGE_PIN AB3 [get_ports {ft_data[13]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[13]}]

set_property PACKAGE_PIN Y9 [get_ports {ft_data[14]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[14]}]

set_property PACKAGE_PIN W9 [get_ports {ft_data[15]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[15]}]

set_property PACKAGE_PIN C17 [get_ports {ft_data[16]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[16]}]

set_property PACKAGE_PIN B17 [get_ports {ft_data[17]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[17]}]

set_property PACKAGE_PIN B18 [get_ports {ft_data[18]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[18]}]

set_property PACKAGE_PIN C13 [get_ports {ft_data[19]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[19]}]

set_property PACKAGE_PIN B13 [get_ports {ft_data[20]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[20]}]

set_property PACKAGE_PIN A13 [get_ports {ft_data[21]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[21]}]

set_property PACKAGE_PIN A14 [get_ports {ft_data[22]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[22]}]

set_property PACKAGE_PIN C22 [get_ports {ft_data[23]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[23]}]

set_property PACKAGE_PIN B22 [get_ports {ft_data[24]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[24]}]

set_property PACKAGE_PIN E22 [get_ports {ft_data[25]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[25]}]

set_property PACKAGE_PIN D22 [get_ports {ft_data[26]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[26]}]

set_property PACKAGE_PIN D20 [get_ports {ft_data[27]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[27]}]

set_property PACKAGE_PIN C20 [get_ports {ft_data[28]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[28]}]

set_property PACKAGE_PIN E21 [get_ports {ft_data[29]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[29]}]

set_property PACKAGE_PIN D21 [get_ports {ft_data[30]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[30]}]

set_property PACKAGE_PIN A18 [get_ports {ft_data[31]}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_data[31]}]


# -----------------------------------------------------------------------------
# FT601 245-sync-FIFO interface timing -- PORTED FROM THE TOP XDC.
#
# The bottom file came from the I/O check, which only proved the pins PLACE; it
# moved no data and carried no bus timing. Building the streamer against it as-is
# would reproduce the exact bug the top file was written to fix: with no
# set_output_delay the tool never times the bus, reports WNS >= 0, and ships a
# design whose far-bank data bits miss the FT601's setup window. That test
# measured 192 fps with ZERO dropped frames while ft_data[31:16] was corrupt --
# an unconstrained path is not a failing path.
#
# FT601Q synchronous-FIFO AC spec (FT600Q/FT601Q datasheet):
#   master -> FT601 : setup 1.0 ns, hold 1.0 ns
#   FT601  -> master: valid <= 7.0 ns after CLK^, >= 1.0 ns hold
# -----------------------------------------------------------------------------
set ft_out_ports [get_ports {ft_data[*] ft_be[*] ft_wr ft_oe ft_rd}]
set_output_delay -clock ft_clk_13 -max  1.0 $ft_out_ports
set_output_delay -clock ft_clk_13 -min -1.0 $ft_out_ports

set ft_in_ports [get_ports {ft_txe ft_rxf}]
set_input_delay -clock ft_clk_13 -max 7.0 $ft_in_ports
set_input_delay -clock ft_clk_13 -min 1.0 $ft_in_ports

set_input_delay -clock ft_clk_13 -max 7.0 [get_ports {ft_data[*] ft_be[*]}]
set_input_delay -clock ft_clk_13 -min 1.0 [get_ports {ft_data[*] ft_be[*]}]

set_false_path -to [get_ports {ft_reset ft_wakeup}]

# Pt's own USB-serial (COM6) -- status telemetry. Not on the Ft+ bottom map,
# so it cannot collide with the FT601 bus.
set_property -dict {PACKAGE_PIN AA21 IOSTANDARD LVCMOS33} [get_ports {usb_tx}]
set_false_path -to [get_ports {usb_tx}]

# ft_data is now BIDIRECTIONAL -- the control channel reads the FT601 OUT pipe,
# so the bus needs input delays as well as the output delays above. Same window
# as TXE#/RXF#: the FT601 drives DATA from its own clock edge.
set ft_bidir [get_ports {ft_data[*]}]
set_input_delay -clock ft_clk_13 -max 7.0 $ft_bidir
set_input_delay -clock ft_clk_13 -min 1.0 $ft_bidir
