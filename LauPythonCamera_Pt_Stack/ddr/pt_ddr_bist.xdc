# pt_ddr_bist.xdc -- board pins for the DDR3 BIST on the Alchitry Pt V2.
#
# The 51 DDR3 pin assignments are NOT here: the MIG generates them itself from
# mig_pt_v2.prj into ip/mig_ddr3/mig_ddr3/user_design/constraints/mig_ddr3.xdc,
# which the build reads. Duplicating them here would let the two disagree.
#
# Pin numbers below are the same ones the camera builds use (pt_cam_rx.xdc).

set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE   [current_design]
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4   [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 33    [current_design]
set_property BITSTREAM.CONFIG.SPI_32BIT_ADDR NO [current_design]

set_property -dict {PACKAGE_PIN W19  IOSTANDARD LVCMOS33} [get_ports {clk}]
create_clock -period 10.0 -name clk_0 -waveform {0.000 5.0} [get_ports clk]

set_property -dict {PACKAGE_PIN N15  IOSTANDARD LVCMOS33} [get_ports {rst_n}]

set_property -dict {PACKAGE_PIN P19  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN P20  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T21  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN R19  IOSTANDARD LVCMOS33} [get_ports {led[3]}]
set_property -dict {PACKAGE_PIN V22  IOSTANDARD LVCMOS33} [get_ports {led[4]}]
set_property -dict {PACKAGE_PIN U21  IOSTANDARD LVCMOS33} [get_ports {led[5]}]
set_property -dict {PACKAGE_PIN T20  IOSTANDARD LVCMOS33} [get_ports {led[6]}]
set_property -dict {PACKAGE_PIN W20  IOSTANDARD LVCMOS33} [get_ports {led[7]}]

set_property -dict {PACKAGE_PIN AA20 IOSTANDARD LVCMOS33} [get_ports {usb_rx}]
set_property -dict {PACKAGE_PIN AA21 IOSTANDARD LVCMOS33} [get_ports {usb_tx}]

set_false_path -from [get_ports {rst_n usb_rx}]
set_false_path -to   [get_ports {led[*] usb_tx}]
