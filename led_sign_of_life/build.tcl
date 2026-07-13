# ============================================================================
# build.tcl -- non-project Vivado build for the "sign of life" LED scanner.
# Produces led_sign.bit (JTAG/RAM) and led_sign.bin (Alchitry flasher).
#   Run from this folder:  vivado -mode batch -source build.tcl
# (A few LUTs + a counter -- synthesis/PAR take well under a minute.)
# ============================================================================
set part   "xc7a35tftg256-2"
set top    "led_sign_top"
set outdir [file normalize [file dirname [info script]]]

read_verilog [file join $outdir led_sign_top.v]
read_xdc     [file join $outdir led_sign.xdc]

synth_design -top $top -part $part
opt_design
place_design
route_design

report_timing_summary -file [file join $outdir timing_summary.rpt]

write_bitstream -force [file join $outdir led_sign.bit]
write_cfgmem -format bin -interface spix1 -size 16 \
    -loadbit "up 0x0 [file join $outdir led_sign.bit]" \
    -force [file join $outdir led_sign.bin]

puts "=== DONE: led_sign.bit and led_sign.bin written to $outdir ==="
exit 0
