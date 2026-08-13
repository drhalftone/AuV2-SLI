# gen_mig.tcl -- generate the DDR3 controller for the Alchitry Pt V2.
#
#   vivado -mode batch -source gen_mig.tcl
#
# The pinout and timing come from mig_pt_v2.prj, which is Alchitry's own MIG
# configuration for this board (Alchitry-Labs-V2,
# src/main/resources/library/cores/mig_pt_v2.prj). That file is the reason this
# is a short script rather than a long afternoon: it carries all 51 DDR3 pad
# assignments plus the part and timing, none of which is published anywhere else
# we could find.
#
#   MT41K128M16XX-15E   128M x 16 DDR3L  = 256 MB
#   TimePeriod 2500 ps  -> 800 MT/s, ~1.6 GB/s
#   PHYRatio 4:1        -> ui_clk = 100 MHz
#   1.35 V (DDR3L), bank 15
#
# A full 1280x1024 8-bit frame is 1,310,720 bytes. The XC7A100T has ~607 KB of
# block RAM, so the whole focal plane only fits here -- DDR holds ~195 of them.

set part   xc7a100tfgg484-2
set here   [file dirname [file normalize [info script]]]
set outdir $here/ip
file mkdir $outdir

create_project -in_memory -part $part
set_property target_language Verilog [current_project]

# Pick whatever mig_7series this Vivado ships; pinning a version would just rot.
set defs [lsort -decreasing [get_ipdefs -all *:mig_7series:*]]
if {[llength $defs] == 0} { error "no mig_7series IP found in this Vivado install" }
set vlnv [lindex $defs 0]
puts "### MIG ipdef: $vlnv"

create_ip -vlnv $vlnv -module_name mig_ddr3 -dir $outdir
set_property CONFIG.XML_INPUT_FILE [file normalize $here/mig_pt_v2.prj] [get_ips mig_ddr3]

generate_target {instantiation_template synthesis implementation} [get_ips mig_ddr3]
synth_ip [get_ips mig_ddr3]

set ok [get_files -quiet [file join $outdir mig_ddr3 mig_ddr3.xci]]
if {$ok eq ""} { error "MIG did not generate" }
puts "### MIG OK -> $ok"

# Report the user-facing interface so the wrapper can be written against fact
# rather than recollection.
set veo [glob -nocomplain [file join $outdir mig_ddr3 *.veo]]
if {$veo ne ""} { puts "### template: [lindex $veo 0]" }
