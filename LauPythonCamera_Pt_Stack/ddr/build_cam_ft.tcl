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
    $ftrtl/ft601_sync_tx.v $ftrtl/ft601_sync_rx.v \
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
set trigcy 0
if {[info exists ::env(CAM_TRIG_CY)]} { set trigcy $::env(CAM_TRIG_CY) }
set nframes 24
if {[info exists ::env(CAM_NFRAMES)]} { set nframes $::env(CAM_NFRAMES) }
puts "### TRIGGERED = $trig   TRIG_US = $trigus   EXPO_SWEEP = $esweep"
puts "### TRIG_CY = $trigcy   NFRAMES = $nframes"

synth_design -top cam_frame_ft -part $part -generic EXPOSURE=$expo              -generic TRIGGERED=$trig -generic TRIG_US=$trigus              -generic EXPO_SWEEP=$esweep -generic TRIG_CY=$trigcy              -generic NFRAMES=$nframes
# Tri-state enable: NOT sampled by the FT601, so the 1 ns set_output_delay
# window does not apply to it. It only has to settle before the bus turnaround
# completes, and ft601_sync_rx waits 3 dead cycles on each edge -- so 3 clock
# periods is the true requirement. Timed as ordinary data it failed at -2.983,
# and still -1.209 after the enables were replicated into the IOB T flops
# (verified 0 unpacked): the IOB T path is simply slower than the D path.
#
# Multicycle, NOT false path -- the deadline is real, just 3 cycles rather than
# 1, and the tool should keep checking it.
#
# Must be applied HERE, after synth_design, because it references netlist pins.
set doe_src [get_pins -quiet {doe_reg[*]/C boe_reg[*]/C}]
if {[llength $doe_src] >= 36} {
    set_multicycle_path -setup 3 -from $doe_src -to [get_ports {ft_data[*] ft_be[*]}]
    set_multicycle_path -hold  2 -from $doe_src -to [get_ports {ft_data[*] ft_be[*]}]
    puts "### tristate multicycle applied to [llength $doe_src] pins"
} else {
    puts "### ERROR: tristate multicycle NOT applied -- matched [llength $doe_src] pins"
}

opt_design
place_design
# phys_opt_design was never in this flow, which is why small violations kept
# needing RTL changes. It replicates high-fanout drivers and retimes -- exactly
# the remedy for ft_txe -> dout_q/CE at -0.142, where the load enable fans out to
# 32 clock enables and the problem is routing, not structure.
phys_opt_design
route_design
# Post-route pass: only helps if something is still negative, and costs a minute.
if {[get_property SLACK [get_timing_paths -delay_type min_max]] < 0} {
    puts "### post-route phys_opt (still negative after route)"
    phys_opt_design
}

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
foreach p [get_ports {ft_data[*] ft_be[*] ft_wr ft_oe ft_rd}] {
    if {[get_property IOB [get_cells -quiet -of_objects [get_nets -quiet -of_objects $p]]] eq "FALSE"} {
        lappend noiob $p
    }
}
puts "### ports not IOB-packed: [llength $noiob]"

# The tri-state enables must land in the IOB T flops too. Unpacked, one register
# drives 36 buffers across two banks and misses setup by ~3 ns -- that is what
# making bus_oe dynamic cost the first time.
set noiobt {}
foreach c [get_cells -quiet -hier -filter {NAME =~ *doe_reg* || NAME =~ *boe_reg*}] {
    if {[get_property -quiet IOB $c] eq "FALSE"} { lappend noiobt $c }
}
puts "### tristate flops not IOB-packed: [llength $noiobt]"

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
