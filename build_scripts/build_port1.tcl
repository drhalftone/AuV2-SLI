# PORT Phase 1: replace the from-scratch offline clock gen with the proven Mimas A7
# drp_clkgen13 (+ drp_recfg + mmcm_drp_func.h). vga still driven by 800x600 constants;
# offline mode hardcoded to idx 11 (800x600@60). Validates the proven DRP clock gen.
# Run: vivado -mode batch -source build_port1.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr
set rtl $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL

# Drop the from-scratch modules that were registered earlier.
foreach f {out_clk_drp.v pixclk_synth.v mmcm_drp_func_7s.vh} {
    set ff [get_files -quiet *$f]
    if {[llength $ff] > 0} { remove_files $ff }
}

# Register the ported Mimas clock-gen sources (idempotent).
foreach f {drp_clkgen13.v drp_recfg.v} {
    if {[llength [get_files -quiet *$f]] == 0} { add_files -norecurse $rtl/$f }
    set_property file_type Verilog [get_files *$f]
}
# XAPP888 functions (Verilog header, `included by drp_recfg.v).
if {[llength [get_files -quiet *mmcm_drp_func.h]] == 0} {
    add_files -norecurse $rtl/mmcm_drp_func.h
}
set_property file_type {Verilog Header} [get_files *mmcm_drp_func.h]
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
