# Rebuild AuV2-SLI with the usb_status telemetry module added.
# Run: vivado -mode batch -source build_uart.tcl
set root C:/Users/dllau/Developer/AuV2-SLI
open_project $root/build/Au2_SLI/Au2_SLI.xpr

# Register the new telemetry source (already copied into the project's imports dir).
set newsrc $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL/usb_status.v
if {[lsearch -exact [get_files -quiet usb_status.v] ""] < 0 && [llength [get_files -quiet *usb_status.v]] == 0} {
    add_files -norecurse $newsrc
}
set_property file_type Verilog [get_files *usb_status.v]
# L1 EDID reader sources (copied into the project's imports dir).
foreach f {edid_reader.v i2c_master_edid.v edid_hex_dumper.v uart_tx.v status_line.v edid_merge.v edid_builder.v} {
    if {[llength [get_files -quiet *$f]] == 0} {
        add_files -norecurse $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL/$f
    }
    set_property file_type Verilog [get_files *$f]
}
# edid_serve.vhd is VHDL (RX DDC slave with double-buffered RAM)
if {[llength [get_files -quiet *edid_serve.vhd]] == 0} {
    add_files -norecurse $root/build/Au2_SLI/Au2_SLI.srcs/sources_1/imports/RTL/edid_serve.vhd
}
set_property file_type {VHDL} [get_files *edid_serve.vhd]
# Au2_SLI.vhd uses a VHDL-2019 conditional expression (line ~377); the xpr never
# recorded the standard, so pin it explicitly or synth reads it as VHDL-93.
set_property file_type "VHDL 2019" [get_files *Au2_SLI.vhd]
update_compile_order -fileset sources_1

# Project was authored in 2024.1; upgrade any out-of-date IP for this Vivado.
if {[llength [get_ips]] > 0} { catch { upgrade_ip [get_ips] } }

# Retime the OFFLINE pixel/serializer clocks for 800x600@60 (was 1280x720@120).
#   clk125 = CLKOUT3 : 125 -> 40 MHz  (pixel clock)
#   clk625 = CLKOUT4 : 625 -> 200 MHz (5x serializer clock)
# These feed only the clk_selector offline mux, so the HDMI passthrough path is unaffected.
set_property -dict [list \
    CONFIG.CLKOUT3_REQUESTED_OUT_FREQ {40.000} \
    CONFIG.CLKOUT4_REQUESTED_OUT_FREQ {200.000} \
] [get_ips ref_clk]
generate_target all [get_ips ref_clk]
catch { reset_run ref_clk_synth_1 }

# Make sure a raw .bin (for the Alchitry loader) is emitted, like the original.
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
