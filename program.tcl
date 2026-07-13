#-----------------------------------------------------------------------------
# program.tcl -- volatile JTAG load of the AuV2-SLI bitstream (Vivado 2025.1).
#
# Programs the FPGA's SRAM directly over the Au V2's FT2232 channel-A JTAG. This
# is NON-PERSISTENT: the board reverts to its SPI-flash image on power cycle, so
# it is the safe/reversible way to bring a build under test.
#   vivado -mode batch -source program.tcl
#
# (To make it permanent later, program the SPI flash with build_au2/Au2_SLI.bin.)
#-----------------------------------------------------------------------------
set here [file normalize [file dirname [info script]]]
set bit  $here/build_au2/Au2_SLI.bit

open_hw_manager
connect_hw_server
open_hw_target
set dev [lindex [get_hw_devices] 0]
current_hw_device $dev
refresh_hw_device -update_hw_probes false $dev
puts "=== target: [get_property PART $dev] ==="

set_property PROGRAM.FILE $bit $dev
program_hw_devices $dev
puts "==== PROGRAMMED (volatile): $bit ===="

close_hw_target
disconnect_hw_server
close_hw_manager
