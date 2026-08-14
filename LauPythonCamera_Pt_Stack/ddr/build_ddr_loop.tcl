# ddr_loop_ft: concurrent DDR3 write + read, no camera. See ddr_loop_ft.v.
set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set root   [file normalize $here/../..]
set rtl    $root/sources_1/imports/RTL
set ftrtl  $root/ft_usb_video/rtl
set outdir $here/build
file mkdir $outdir

set xci [file join $here ip mig_ddr3 mig_ddr3.xci]
if {![file exists $xci]} { error "MIG not generated -- run gen_mig.tcl first" }

read_verilog [list $here/ddr_loop_ft.v $ftrtl/ft601_sync_tx.v \
                   $rtl/cam_async_fifo.v $rtl/uart_tx.v]
read_ip  $xci
read_xdc $here/pt_ddr_loop.xdc
read_xdc $here/pt_ft_plus_bottom_timed.xdc

synth_design -top ddr_loop_ft -part $part
opt_design
place_design
phys_opt_design
route_design
write_checkpoint -force $outdir/ddr_loop_ft_routed.dcp

puts "### SETUP WNS = [get_property SLACK [get_timing_paths -delay_type max]]"
puts "### HOLD  WHS = [get_property SLACK [get_timing_paths -delay_type min]]"

set noiob {}
foreach p [get_ports {ft_data[*] ft_be[*] ft_wr}] {
    if {[get_property IOB [get_cells -quiet -of_objects [get_nets -quiet -of_objects $p]]] eq "FALSE"} {
        lappend noiob $p
    }
}
puts "### ports not IOB-packed: [llength $noiob]"

for {set try 1} {$try <= 4} {incr try} {
    if {![catch {write_bitstream -force -bin_file $outdir/ddr_loop_ft.bit} msg]} break
    puts "### write_bitstream attempt $try failed: $msg"
}
puts "### DDR LOOP BUILD OK -> $outdir/ddr_loop_ft.bin"
