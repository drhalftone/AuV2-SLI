# Board pins for the Ft+ continuity probe. Deliberately minimal: pt_cam_rx.xdc
# constrains camera ports this design does not have, and every missing port
# raises a CRITICAL WARNING that would bury a real one.
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
set_property -dict {PACKAGE_PIN AA21 IOSTANDARD LVCMOS33} [get_ports {usb_tx}]
set_false_path -from [get_ports {rst_n}]
set_false_path -to   [get_ports {led[*] usb_tx ft_reset ft_wr ft_oe ft_rd ft_wakeup}]
set_false_path -from [get_ports {ft_txe ft_rxf}]
