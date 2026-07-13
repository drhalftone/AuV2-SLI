# PORT Phase 2 step 2: offline mode_idx is now driven by the projector's EDID.
#  - i2c_master_edid: added 2nd read port (rd_addr2/rd_data2)
#  - edid_merge: exposes that port + edid_ok
#  - mode_select.v (ported): EDID -> best curated mode, on clk10
#  - Au2_SLI: picker controller + CDC drive drp_clkgen13 mode_idx/SEN + mode_timing_rom
# Run: vivado -mode batch -source build_port2b.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr
set rtl $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL

if {[llength [get_files -quiet *mode_select.v]] == 0} { add_files -norecurse $rtl/mode_select.v }
set_property file_type Verilog [get_files *mode_select.v]
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
if {[file exists $binf]} { puts "BIN_OK: $binf ([file size $binf] bytes)" } else { puts "BIN_MISSING"; exit 1 }
exit 0
