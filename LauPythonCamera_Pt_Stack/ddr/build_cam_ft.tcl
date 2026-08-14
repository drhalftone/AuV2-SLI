# build_cam_ft.tcl -- camera -> DDR3 -> FT601 (Ft+ on the BOTTOM).
set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set root   [file normalize $here/../..]
set rtl    $root/sources_1/imports/RTL
set hello  $root/LauPythonCamera_Pt_Stack/hello
set ftrtl  $root/ft_usb_video/rtl
set outdir $here/build
file mkdir $outdir

set xci [file join $here ip mig_ddr3 mig_ddr3.xci]
if {![file exists $xci]} { error "MIG not generated -- run gen_mig.tcl first" }

read_verilog [list \
    $here/cam_frame_ft.v \
    $ftrtl/ft601_sync_tx.v \
    $hello/cam_boot_stage1.v $hello/cam_lvds_rx_idelay.v $hello/cam_eye_scan.v \
    $rtl/cam_boot_seq.v $rtl/cam_align.v $rtl/cam_sync_decode.v \
    $rtl/cam_async_fifo.v $rtl/cam_spi_master.v $rtl/uart_tx.v \
]
read_ip  $xci
# camera + board pins, then the Ft+ BOTTOM pins with real bus timing
read_xdc $hello/pt_cam_rx.xdc
read_xdc $here/pt_ft_plus_bottom_timed.xdc

# EXPOSURE and the output name come from the environment so a sweep needs no
# source edit: CAM_EXPOSURE is a Verilog literal (e.g. 16'h0640 = 1600 units of
# 375 ns = 600 us), CAM_TAG suffixes the bitstream so points are not overwritten.
set expo "16'h0640"
if {[info exists ::env(CAM_EXPOSURE)]} { set expo $::env(CAM_EXPOSURE) }
set tag ""
if {[info exists ::env(CAM_TAG)]} { set tag $::env(CAM_TAG) }
puts "### EXPOSURE = $expo   TAG = '$tag'"

set trig 0
if {[info exists ::env(CAM_TRIGGERED)]} { set trig $::env(CAM_TRIGGERED) }
set trigus 7500
if {[info exists ::env(CAM_TRIG_US)]} { set trigus $::env(CAM_TRIG_US) }
set esweep 0
if {[info exists ::env(CAM_EXPO_SWEEP)]} { set esweep $::env(CAM_EXPO_SWEEP) }
puts "### TRIGGERED = $trig   TRIG_US = $trigus   EXPO_SWEEP = $esweep"

synth_design -top cam_frame_ft -part $part -generic EXPOSURE=$expo              -generic TRIGGERED=$trig -generic TRIG_US=$trigus              -generic EXPO_SWEEP=$esweep
opt_design
place_design
route_design

# Vivado 2025.2.1 on this machine intermittently dies with
# EXCEPTION_ACCESS_VIOLATION inside write_bitstream's DRC (see the hs_err_pid*
# files it drops in the repo root). Implementation is the expensive part and it
# has already succeeded by this point, so checkpoint here: a crash at bitstream
# time can then be recovered with open_checkpoint + write_bitstream instead of a
# full 13-minute rebuild.
write_checkpoint -force $outdir/cam_frame_ft${tag}_routed.dcp

set wns [get_property SLACK [get_timing_paths -delay_type min_max]]
puts "### WNS = $wns"

# The bus MUST be IOB-registered; that is half of the corruption fix.
set noiob {}
foreach p [get_ports {ft_data[*] ft_be[*] ft_wr}] {
    if {[get_property IOB [get_cells -quiet -of_objects [get_nets -quiet -of_objects $p]]] eq "FALSE"} {
        lappend noiob $p
    }
}
puts "### ports not IOB-packed: [llength $noiob]"

# WHERE is the slack negative? A failing FT601 bus path is not shippable -- that
# is the corruption mode. A failing path elsewhere is a different conversation.
foreach grp [get_timing_paths -delay_type min_max -max_paths 6 -nworst 1 -sort_by group] {
    puts [format "### PATH slack=%-8s group=%-22s from=%s -> to=%s"         [get_property SLACK $grp] [get_property GROUP $grp]         [get_property STARTPOINT_PIN $grp] [get_property ENDPOINT_PIN $grp]]
}
report_timing_summary -file $outdir/timing_summary.rpt -quiet

for {set try 1} {$try <= 4} {incr try} {
    if {![catch {write_bitstream -force -bin_file $outdir/cam_frame_ft$tag.bit} msg]} break
    puts "### write_bitstream attempt $try failed: $msg"
}
puts "### CAM+DDR+FT601 BUILD OK -> $outdir/cam_frame_ft$tag.bin"
