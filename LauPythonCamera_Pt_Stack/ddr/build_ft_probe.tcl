set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set root   [file normalize $here/../..]
set outdir $here/build
file mkdir $outdir
read_verilog [list $here/ft_probe_bottom.v $root/sources_1/imports/RTL/uart_tx.v]
read_xdc $here/ft_probe_bottom.xdc
read_xdc $root/LauPythonCamera_Pt_Stack/iocheck/alchitry_pt_ft_plus_bottom.xdc
synth_design -top ft_probe_bottom -part $part
opt_design
place_design
route_design
puts "### WNS = [get_property SLACK [get_timing_paths -delay_type min_max]]"
write_bitstream -force -bin_file $outdir/ft_probe_bottom.bit
puts "### PROBE BUILD OK"
