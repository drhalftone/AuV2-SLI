# Functional SLI build with Camera-1 + config switches remapped to Bank B
# (LauCameraTrigger_Alchitry_Stack daughter board). Same flow as build_pat.tcl;
# the only change is the activated Bank-B remap in the project's Au2.xdc.
#   Run: vivado -mode batch -source build\build_stackb.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr
set rtl $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL

if {[llength [get_files -quiet *pattern_gen.v]] == 0} { add_files -norecurse $rtl/pattern_gen.v }
set_property file_type Verilog [get_files *pattern_gen.v]
if {[llength [get_files -quiet *video_phase_fifo.v]] == 0} { add_files -norecurse $rtl/video_phase_fifo.v }
set_property file_type Verilog [get_files *video_phase_fifo.v]
if {[llength [get_files -quiet *edid_hex_dumper.v]] == 0} { add_files -norecurse $rtl/edid_hex_dumper.v }
set_property file_type Verilog [get_files *edid_hex_dumper.v]
if {[llength [get_files -quiet *led_idle_anim.v]] == 0} { add_files -norecurse $rtl/led_idle_anim.v }
set_property file_type Verilog [get_files *led_idle_anim.v]
set_property include_dirs [list $rtl] [get_filesets sources_1]

set_property file_type "VHDL 2019" [get_files *Au2_SLI.vhd]
update_compile_order -fileset sources_1

if {[llength [get_ips]] > 0} { catch { upgrade_ip [get_ips] } }
set_property STEPS.WRITE_BITSTREAM.ARGS.BIN_FILE true [get_runs impl_1]

# Force a full (non-incremental) synthesis -- the incremental reference checkpoint
# can choke after back-to-back RTL changes; a clean run is more reliable here.
catch { set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1] }
catch { set_property INCREMENTAL_CHECKPOINT {} [get_runs synth_1] }

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
    set dst $root/build/Au2_SLI_stackB.bin
    file copy -force $binf $dst
    puts "BIN_OK: $dst ([file size $dst] bytes)"
} else {
    puts "BIN_MISSING"; exit 1
}
exit 0
