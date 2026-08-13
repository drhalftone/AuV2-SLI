# build_cam_ddr.tcl -- the whole focal plane: camera -> DDR3 -> PC.
#   vivado -mode batch -source build_cam_ddr.tcl
# Run gen_mig.tcl first.
set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set root   [file normalize $here/../..]
set rtl    $root/sources_1/imports/RTL
set hello  $root/LauPythonCamera_Pt_Stack/hello
set outdir $here/build
file mkdir $outdir

set xci [file join $here ip mig_ddr3 mig_ddr3.xci]
if {![file exists $xci]} { error "MIG not generated -- run gen_mig.tcl first" }

read_verilog [list \
    $here/cam_frame_ddr.v \
    $hello/cam_boot_stage1.v \
    $hello/cam_lvds_rx_idelay.v \
    $hello/cam_eye_scan.v \
    $rtl/cam_boot_seq.v \
    $rtl/cam_align.v \
    $rtl/cam_sync_decode.v \
    $rtl/cam_async_fifo.v \
    $rtl/cam_spi_master.v \
    $rtl/uart_tx.v \
]
read_ip  $xci
# The camera pins, board pins and clock constraints are the same ones every
# camera build uses. The 51 DDR3 pins come from the MIG's own generated xdc.
read_xdc $hello/pt_cam_rx.xdc

synth_design -top cam_frame_ddr -part $part
opt_design
place_design
route_design

set wns [get_property SLACK [get_timing_paths -delay_type min_max]]
puts "### WNS = $wns"
if {$wns < 0} { puts "### TIMING FAILED" }

for {set try 1} {$try <= 4} {incr try} {
    if {![catch {write_bitstream -force -bin_file $outdir/cam_frame_ddr.bit} msg]} break
    puts "### write_bitstream attempt $try failed: $msg"
}
set bin $outdir/cam_frame_ddr.bin
if {![file exists $bin]} { error "no .bin produced" }
puts "### CAM+DDR BUILD OK -> $bin ([file size $bin] bytes)"
