#-----------------------------------------------------------------------------
# elab_check.tcl -- fast RTL elaboration of the whole design. No synth, no place.
#
# Catches port-list mismatches, undeclared signals and width errors -- exactly what
# breaks when a new block is threaded through uart_ctrl -> usb_link -> Au2_SLI.
#
#   vivado -mode batch -source build_scripts/elab_check.tcl
#
# Prints "=== ELAB_OK ===" on success. Reads sources straight from the working tree,
# in-memory: no .xpr, so it cannot rot the way the old hard-coded-project-path
# version did (it pointed at C:/Users/dllau/... and had stopped working).
#-----------------------------------------------------------------------------
set part xc7a35tftg256-2
set here [file normalize [file dirname [info script]]/..]
set rtl  $here/sources_1/imports/RTL
set ipd  $here/sources_1/ip

create_project -in_memory -part $part

# ---- IP (ref_clk MMCM) ----
set xcis [glob -nocomplain $ipd/*/*.xci]
if {[llength $xcis] > 0} {
    read_ip $xcis
    upgrade_ip -quiet [get_ips]
    generate_target all [get_ips]
    synth_ip [get_ips]
}

# ---- HDL. Au2_SLI.vhd needs VHDL-2019; the rest is plain VHDL. ----
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]

synth_design -top Au2_SLI -part $part -rtl -name elab_check -include_dirs $rtl

puts "=== ELAB_OK ==="
exit 0
