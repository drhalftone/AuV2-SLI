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

# ---- IP (ref_clk MMCM, LUT/indexMap BRAMs) ----
#
# read_ip ONLY. Deliberately NO upgrade_ip / generate_target / synth_ip.
#
# upgrade_ip REWRITES the .xci in place -- it bumps ip_revision and SWVERSION to whatever
# Vivado happens to be installed. This script once did that and silently upgraded the
# clocking wizard behind the 200 MHz IDELAYCTRL reference, leaving five modified .xci files
# staged for the next commit. A check must never mutate the thing it is checking.
#
# For -rtl elaboration we do not need the IP built: read_ip supplies the port definitions
# and Vivado black-boxes the body, which is all this check needs to catch port-list, width
# and connectivity errors in OUR RTL. build.tcl still does the real upgrade+synth for a
# real bitstream -- that is where an IP version bump belongs, deliberately and reviewed.
set xcis [glob -nocomplain $ipd/*/*.xci]
if {[llength $xcis] > 0} { read_ip $xcis }

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
