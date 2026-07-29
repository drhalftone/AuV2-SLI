# ft_clk_probe pin map -- watch BOTH candidate FT601 clock pins at once.
# Base pins (clk/rst_n/led/usb_tx) come from alchitry_pt_base.xdc (read first).

# --- TOP element map candidate (ft_clk element A41 -> H4) ---
set_property PACKAGE_PIN H4  [get_ports {ftclk_top}]
set_property IOSTANDARD LVCMOS33 [get_ports {ftclk_top}]
create_clock -period 10.0 -name ftclk_top -waveform {0.000 5.0} [get_ports ftclk_top]
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks ftclk_top]

set_property PACKAGE_PIN AB21 [get_ports {ft_reset_top}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_reset_top}]
set_property PACKAGE_PIN AB22 [get_ports {ft_wakeup_top}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wakeup_top}]

# --- BOTTOM element map candidate (ft_clk element A41 -> D17) ---
set_property PACKAGE_PIN D17 [get_ports {ftclk_bot}]
set_property IOSTANDARD LVCMOS33 [get_ports {ftclk_bot}]
create_clock -period 10.0 -name ftclk_bot -waveform {0.000 5.0} [get_ports ftclk_bot]
set_clock_groups -asynchronous -group [get_clocks -include_generated_clocks ftclk_bot]

set_property PACKAGE_PIN AA1 [get_ports {ft_reset_bot}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_reset_bot}]
set_property PACKAGE_PIN AB1 [get_ports {ft_wakeup_bot}]
set_property IOSTANDARD LVCMOS33 [get_ports {ft_wakeup_bot}]
