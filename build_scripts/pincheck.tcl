# Re-run synth+place far enough to read back the actual pin placement, then verify each
# camera port landed on the ball cam_au2.xdc asked for. Fails loudly on any mismatch.
set part xc7a35tftg256-2
set here [file normalize [file dirname [info script]]/..]
set rtl  $here/sources_1/imports/RTL
set ipd  $here/sources_1/ip
create_project -in_memory -part $part
read_ip [glob -nocomplain $ipd/*/*.xci]
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]
read_xdc $here/constrs_1/imports/RTL/Au2.xdc
read_xdc $here/constrs_1/imports/RTL/cam_au2.xdc
synth_design -top Au2_SLI -part $part -include_dirs $rtl
array set want {
  cam_mosi N6  cam_sck M6  cam_miso P9
  cam_ss_n L2  cam_reset_n J1
  {cam_trigger[0]} K1  {cam_trigger[1]} L3  {cam_trigger[2]} H1
  {cam_monitor[0]} K2  {cam_monitor[1]} H2
}
set bad 0
foreach {port ball} [array get want] {
  set got [get_property PACKAGE_PIN [get_ports $port]]
  set iostd [get_property IOSTANDARD [get_ports $port]]
  if {$got ne $ball} { puts "  MISMATCH $port: got $got, want $ball"; incr bad } \
  else { puts [format "  OK  %-16s %-4s %s" $port $got $iostd] }
}
if {$bad == 0} { puts "=== PINCHECK_OK ===" } else { puts "=== PINCHECK_FAIL ($bad) ===" }
exit 0
