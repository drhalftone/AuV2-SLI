# =============================================================================
# run_sim_lvds.tcl - verify the stage-2 interlocks (cam_lvds_en).
#   vivado -mode batch -source run_sim_lvds.tcl
# =============================================================================
set here [file normalize [file dirname [info script]]]
set root [file normalize $here/../..]
set out  $here/sim_work_lvds
file mkdir $out
cd $out

set srcs [list \
    $root/sources_1/imports/RTL/cam_spi_master.v \
    $root/sources_1/imports/RTL/uart_tx.v \
    $root/sim/python1300_spi_model.v \
    $here/cam_lvds_en.v \
    $here/tb_cam_lvds_en.v ]

if {[catch {exec xvlog -sv {*}$srcs} msg]} { puts $msg; puts "##### COMPILE FAILED #####"; exit 1 }
puts $msg
if {[catch {exec xelab -debug typical tb_cam_lvds_en -s tb_lvds} msg]} { puts $msg; puts "##### ELAB FAILED #####"; exit 1 }
puts $msg
set rc [catch {exec xsim tb_lvds -runall} msg]
puts $msg
if {$rc} { puts "##### SIM ERROR #####"; exit 1 }
if {[string match "*##### PASS #####*" $msg]} { exit 0 } else { puts "##### SELF-CHECK FAILED #####"; exit 1 }
