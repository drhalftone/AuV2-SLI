#-----------------------------------------------------------------------------
# build_pt.tcl -- Au2_SLI ported to the Alchitry Pt V2 (XC7A100T-2FGG484I).
#
#   vivado -mode batch -source build_pt.tcl -log build_pt/vivado.log -journal build_pt/vivado.jou
#
# Phase 1 of the port (task #15): the existing SLI design (HDMI passthrough + pattern gen)
# plus the camera SPI/control interface, on Au2_pt.xdc. The LVDS receiver chain is added
# later (task #12). Outputs to build_pt/ (gitignored).
#
# IP HANDLING -- the committed .xci are targeted at the 35T. Retargeting them to the 100T in
# place would (a) mutate the committed files and (b) clash with the Au build's shared gen dir
# (both resolve to <parent>/Au2_SLI.gen). So we COPY the IP into build_pt/ip_work, point each
# copy's gen_directory at itself ("."), and read from the copies. The committed sources_1/ip
# is never touched, and the two builds don't collide.
#-----------------------------------------------------------------------------
set part xc7a100tfgg484-2
set top  Au2_SLI
set here [file normalize [file dirname [info script]]]
set rtl  $here/sources_1/imports/RTL
set ipd  $here/sources_1/ip
set out  $here/build_pt
file mkdir $out

create_project -in_memory -part $part

# ---- IP: work on COPIES retargeted to the 100T; committed .xci untouched ----
set ipwork $out/ip_work
file delete -force $ipwork
file mkdir $ipwork
foreach d [glob -nocomplain -type d $ipd/*] {
    file copy -force $d $ipwork
}
# repoint each copy's output products at its own directory, so nothing lands in the shared
# <parent>/Au2_SLI.gen (which the Au build owns) and nothing needs a relative escape path.
foreach xci [glob -nocomplain $ipwork/*/*.xci] {
    set fp [open $xci r]; set data [read $fp]; close $fp
    regsub -all {"gen_directory"[ ]*:[ ]*"[^"]*"} $data {"gen_directory": "."} data
    set fp [open $xci w]; puts -nonewline $fp $data; close $fp
}
read_ip [glob $ipwork/*/*.xci]
upgrade_ip -quiet [get_ips]
generate_target all [get_ips]
synth_ip [get_ips]

# ---- HDL (Au2_SLI.vhd needs VHDL-2019) ----
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]

# ---- constraints (the Pt re-pin) ----
read_xdc $here/constrs_1/imports/RTL/Au2_pt.xdc

# ---- synth + implement ----
synth_design -top $top -include_dirs $rtl
opt_design
place_design
route_design

# ---- outputs ----
write_bitstream -force $out/Au2_SLI_pt.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI_pt.bit" $out/Au2_SLI_pt.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== AuV2-SLI Pt V2 BUILD DONE ===="
puts "bit : $out/Au2_SLI_pt.bit"
puts "bin : $out/Au2_SLI_pt.bin"
