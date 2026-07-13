# PORT Phase 2 step 1: drive vga timing from mode_timing_rom (same curated table as
# the clock), indexed by OFFLINE_MODE_IDX. Hardcoded idx 10 (1024x768@60) to validate
# the table-driven timing+clock path for a NON-default mode.
# Run: vivado -mode batch -source build_port2.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr
set rtl $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL

# Register the geometry ROM (idempotent).
if {[llength [get_files -quiet *mode_timing_rom.v]] == 0} { add_files -norecurse $rtl/mode_timing_rom.v }
set_property file_type Verilog [get_files *mode_timing_rom.v]
# mode_table.vh is `included inside mode_timing_rom.v's initial block.
if {[llength [get_files -quiet *mode_table.vh]] == 0} { add_files -norecurse $rtl/mode_table.vh }
set_property file_type {Verilog Header} [get_files *mode_table.vh]
set_property include_dirs [list $rtl] [get_filesets sources_1]

set_property file_type "VHDL 2019" [get_files *Au2_SLI.vhd]
update_compile_order -fileset sources_1

if {[llength [get_ips]] > 0} { catch { upgrade_ip [get_ips] } }
set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]

reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {
    puts "SYNTH_FAILED: [get_property STATUS [get_runs synth_1]]"
    exit 1
}

launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
set st [get_property STATUS [get_runs impl_1]]
puts "IMPL_STATUS: $st  PROGRESS: [get_property PROGRESS [get_runs impl_1]]"

set binf $root/build/Au2_SLI/Au2_SLI.runs/impl_1/Au2_SLI.bin
if {[file exists $binf]} {
    puts "BIN_OK: $binf ([file size $binf] bytes)"
} else {
    puts "BIN_MISSING"
    exit 1
}
exit 0
