#-----------------------------------------------------------------------------
# run_probe.tcl -- build the dual-clock FT601 clock-locator diagnostic bitstream.
#   vivado -mode batch -source run_probe.tcl -log out/vivado.log -journal out/vivado.jou
# Outputs out/ft_clk_probe.bin. Flash to RAM:
#   Alchitry.exe load --bin out/ft_clk_probe.bin --board PtV2 --ram
#-----------------------------------------------------------------------------
set part xc7a100tfgg484-2
set top  ft_clk_probe

set here [file normalize [file dirname [info script]]]         ;# build/diag/
set repo [file normalize $here/../../..]                       ;# repo root
set base $repo/LauPythonCamera_Pt_Stack/iocheck/alchitry_pt_base.xdc
set uart $repo/ft_usb_video/rtl/uart_tx.v
set out  $here/out
file mkdir $out

set_param general.maxThreads 1
create_project -in_memory -part $part

read_verilog [list $here/ft_clk_probe.v $uart]
read_xdc $base
read_xdc $here/ft_clk_probe.xdc

set logf $out/vivado.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top} err]} {
        set transient 1
        if {![catch {set fp [open $logf r]; seek $fp $mark; set tail [read $fp]; close $fp}]} {
            set transient [string match {*couldn't read file*No error*} $tail]
        }
        if {!$transient} { error "synth_design failed with a REAL error: $err" }
        puts "==== synth attempt $try hit the transient .tcl-read glitch; retrying ===="
        if {$try == 6} { error "synth_design: transient glitch persisted 6 attempts" }
    } else {
        puts "==== synth succeeded on attempt $try ===="
        break
    }
}

opt_design
place_design
route_design

write_bitstream -force $out/ft_clk_probe.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/ft_clk_probe.bit" $out/ft_clk_probe.bin
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== FT601 CLOCK-PROBE BUILD DONE ===="
puts "bin : $out/ft_clk_probe.bin"
