# ============================================================================
# build.tcl -- non-project Vivado build for the Stack-board LED smoke test.
# Produces led_test.bit (JTAG/RAM) and led_test.bin (Alchitry flasher).
#
#   Run from this folder:
#     vivado -mode batch -source build.tcl
#
# (Use the Vivado that ships with this project's toolchain. The design is a
#  handful of LUTs of pure routing, so synthesis/PAR take well under a minute.)
# ============================================================================

set part      "xc7a35tftg256-2"
set top       "led_test_top"
set outdir    [file normalize [file dirname [info script]]]

read_verilog [file join $outdir led_test_top.v]
read_xdc     [file join $outdir led_test.xdc]

synth_design -top $top -part $part
opt_design
place_design
route_design

report_timing_summary -file [file join $outdir timing_summary.rpt]
report_drc            -file [file join $outdir drc.rpt]

write_bitstream -force [file join $outdir led_test.bit]

# SPI flash image for the Alchitry loader / AlchitryFlasher GUI.
write_cfgmem -format bin -interface spix1 -size 16 \
    -loadbit "up 0x0 [file join $outdir led_test.bit]" \
    -force [file join $outdir led_test.bin]

puts "=== DONE: led_test.bit and led_test.bin written to $outdir ==="
