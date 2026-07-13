# STEP A of the recovery-clock DRP rework: rebuild with hdmi_input.vhd swapped from
# MMCME2_BASE -> MMCME2_ADV (static, DRP tied off).  Regression gate: must behave
# exactly like the committed x10 design (720p passthrough).  No new sources; only
# hdmi_input.vhd changed (already mirrored into the project's imports dir).
# Run: vivado -mode batch -source build_drpA.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr

# Au2_SLI.vhd uses a VHDL-2019 conditional expression; pin the standard.
set_property file_type "VHDL 2019" [get_files *Au2_SLI.vhd]
update_compile_order -fileset sources_1

# Keep IP current for this Vivado.
if {[llength [get_ips]] > 0} { catch { upgrade_ip [get_ips] } }

# Emit a raw .bin for the Alchitry loader.
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
set pr [get_property PROGRESS [get_runs impl_1]]
puts "IMPL_STATUS: $st  PROGRESS: $pr"

set binf $root/build/Au2_SLI/Au2_SLI.runs/impl_1/Au2_SLI.bin
if {[file exists $binf]} {
    puts "BIN_OK: $binf ([file size $binf] bytes)"
} else {
    puts "BIN_MISSING"
    exit 1
}
exit 0
