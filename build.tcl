#-----------------------------------------------------------------------------
# build.tcl -- non-project batch build for AuV2-SLI (Vivado 2025.1).
#
# Reproducible bitstream build straight from the git-tracked sources -- no zip,
# no GUI. From the repo root:
#     vivado -mode batch -source build.tcl -log build_au2/vivado.log -journal build_au2/vivado.jou
#
# Outputs to build_au2/ (gitignored): Au2_SLI.bit, Au2_SLI.bin (SPIx1, 16 MB),
# util.rpt, timing.rpt. Flash Au2_SLI.bin with the AlchitryFlasher / Alchitry loader.
#-----------------------------------------------------------------------------
set part xc7a35tftg256-2
set top  Au2_SLI
set here [file normalize [file dirname [info script]]]
set rtl  $here/sources_1/imports/RTL
set ipd  $here/sources_1/ip
set out  $here/build_au2
file mkdir $out

create_project -in_memory -part $part

# ---- IP cores (ROM LUTs + clocking + index maps) ----
read_ip [list \
    $ipd/ref_clk/ref_clk.xci \
    $ipd/indexMap/indexMap.xci \
    $ipd/indexMapV/indexMapV.xci \
    $ipd/LUT/LUT.xci \
    $ipd/LUT_V/LUT_V.xci ]
# The .xci were customized with Vivado 2024.1; migrate them to the running tool
# (else they are "locked" and no output-product DCPs are generated).
upgrade_ip -quiet [get_ips]
generate_target all [get_ips]
synth_ip [get_ips]

# ---- HDL (glob all current RTL; Au2_SLI.vhd needs VHDL-2019) ----
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]

# ---- constraints ----
read_xdc $here/constrs_1/imports/RTL/Au2.xdc
# PYTHON 1300 camera element: the 10 CMOS control pins only. No LVDS -- on the Au the
# pairs scatter across banks 14/15/34 and dout0 lands on the 1.35 V DDR3 bank. See the
# header of cam_au2.xdc.
read_xdc $here/constrs_1/imports/RTL/cam_au2.xdc

# ---- synth + implement ----
synth_design -top $top -include_dirs $rtl
opt_design
place_design
route_design

# ---- outputs ----
write_bitstream -force $out/Au2_SLI.bit
write_cfgmem -force -format bin -interface spix1 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI.bit" $out/Au2_SLI.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== AuV2-SLI STAGE-1 BUILD DONE ===="
puts "bit : $out/Au2_SLI.bit"
puts "bin : $out/Au2_SLI.bin"
