# build_wkkkk.tcl -- the WHITE,K,K,K,K projector-only bitstream.
#
# HDMI OUTPUT ONLY. No receiver, no EDID, no mode selection, no SLI pattern
# generator, no transfer LUT, no camera, no DDR3/MIG, no Ft+, no UART. The file
# list below IS the design -- if a module is not named here it is not in the
# bitstream, which is the entire point of building this separately.
#
# It also builds in a couple of minutes rather than half an hour, because the MIG
# is what dominates the merged build. On a host where builds have been dying at
# random that difference is worth having on its own.
#
# Outputs build_wkkkk/Au2_SLI_wkkkk.{bit,bin}.

set here [file normalize [file dirname [info script]]]
set rtl  $here/sources_1/imports/RTL
set out  $here/build_wkkkk
set part xc7a100tfgg484-2
set top  wkkkk_top

file mkdir $out
create_project -in_memory -part $part

# ---- the whole design, named explicitly -------------------------------------
# VHDL
read_vhdl [list \
    $rtl/wkkkk_top.vhd \
    $rtl/vga.vhd \
    $rtl/dvid_output.vhd \
    $rtl/tmds_encoder.vhd \
    $rtl/serialiser_10_to_1.vhd ]
# Verilog
read_verilog [list \
    $rtl/drp_clkgen13.v \
    $rtl/drp_recfg.v \
    $rtl/mode_timing_rom.v \
    $rtl/impulse_gen.v ]

read_xdc $here/constrs_1/imports/RTL/wkkkk_top.xdc

# mode_table.vh is `include-d by mode_timing_rom / drp_recfg
synth_design -top $top -part $part -include_dirs $rtl

opt_design
place_design
phys_opt_design
route_design

report_utilization -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

# ---- REFUSE TO SHIP A BITSTREAM THAT DOES NOT MEET TIMING --------------------
# The merged build writes a bitstream regardless and prints the WNS afterwards,
# which is how a -3.262 ns build produced a .bin that looked finished. This design
# is small and has one clock domain; a negative WNS here means something is wrong,
# not that the die is full. Fail loudly instead of handing over a bad artifact.
set wns [get_property SLACK [get_timing_paths -delay_type max]]
set whs [get_property SLACK [get_timing_paths -delay_type min]]
puts "=== TIMING: setup WNS = $wns ns,  hold WHS = $whs ns ==="
if {$wns < 0 || $whs < 0} {
    error "TIMING FAILED (WNS $wns, WHS $whs) -- refusing to write a bitstream."
}

write_bitstream -force $out/Au2_SLI_wkkkk.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI_wkkkk.bit" $out/Au2_SLI_wkkkk

puts "==== WKKKK PROJECTOR BUILD DONE ===="
puts "bit : $out/Au2_SLI_wkkkk.bit"
puts "bin : $out/Au2_SLI_wkkkk.bin"
