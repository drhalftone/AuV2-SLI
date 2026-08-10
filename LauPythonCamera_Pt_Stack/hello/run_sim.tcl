# =============================================================================
# run_sim.tcl - simulate the chip-ID bring-up bitstream before it goes on the
# bench, including against deliberately broken sensors.
#
#   vivado -mode batch -source run_sim.tcl
#
# Runs from anywhere; all paths are derived from this script's location.
# =============================================================================
set here [file normalize [file dirname [info script]]]
set root [file normalize $here/../..]
set out  $here/sim_work
file mkdir $out
cd $out

set srcs [list \
    $root/sources_1/imports/RTL/cam_spi_master.v \
    $root/sources_1/imports/RTL/uart_tx.v \
    $root/sim/python1300_spi_model.v \
    $here/cam_hello_core.v \
    $here/pt_cam_hello.v \
    $here/au_cam_hello.v \
    $here/tb_pt_cam_hello.v \
]

set fail 0
if {[catch {exec xvlog -sv {*}$srcs} msg]} { puts $msg; set fail 1 }
puts $msg
if {$fail} { puts "##### COMPILE FAILED #####"; exit 1 }

if {[catch {exec xelab -debug typical tb_pt_cam_hello -s tb_snap} msg]} {
    puts $msg; puts "##### ELABORATION FAILED #####"; exit 1
}
puts $msg

set rc [catch {exec xsim tb_snap -runall} msg]
puts $msg
if {$rc} { puts "##### SIM ERROR #####"; exit 1 }

if {[string match "*##### PASS #####*" $msg]} {
    exit 0
} else {
    puts "##### SELF-CHECK FAILED #####"
    exit 1
}
