# build_ddr.tcl -- DDR3 BIST bitstream.  vivado -mode batch -source build_ddr.tcl
#
# Run gen_mig.tcl first; this reads the MIG it produced rather than regenerating
# it, because MIG generation is slow and its output is an input to timing.
set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set root   [file normalize $here/../..]
set outdir $here/build
file mkdir $outdir

set xci [file join $here ip mig_ddr3 mig_ddr3.xci]
if {![file exists $xci]} { error "MIG not generated -- run gen_mig.tcl first" }

read_verilog [list $here/ddr_bist.v $root/sources_1/imports/RTL/uart_tx.v]
read_ip      $xci
read_xdc     $here/pt_ddr_bist.xdc

synth_design -top ddr_bist -part $part
opt_design
place_design
route_design

set wns [get_property SLACK [get_timing_paths -delay_type min_max]]
puts "### WNS = $wns"
if {$wns < 0} { puts "### TIMING FAILED" ; error "negative slack" }

for {set try 1} {$try <= 4} {incr try} {
    if {![catch {write_bitstream -force -bin_file $outdir/ddr_bist.bit} msg]} break
    puts "### write_bitstream attempt $try failed: $msg"
}
set bin $outdir/ddr_bist.bin
if {![file exists $bin]} { error "no .bin produced" }
puts "### DDR BIST BUILD OK -> $bin ([file size $bin] bytes)"
