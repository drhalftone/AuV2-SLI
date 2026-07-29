#-----------------------------------------------------------------------------
# run_blink.tcl -- build the onboard-only LED chaser sanity bitstream.
#   Outputs out/blink.bin -> Alchitry.exe load --bin out/blink.bin --board PtV2 --ram
#-----------------------------------------------------------------------------
set part xc7a100tfgg484-2
set top  blink

set here [file normalize [file dirname [info script]]]
set repo [file normalize $here/../../..]
set base $repo/LauPythonCamera_Pt_Stack/iocheck/alchitry_pt_base.xdc
set out  $here/out
file mkdir $out

set_param general.maxThreads 1
create_project -in_memory -part $part
read_verilog $here/blink.v
read_xdc $base

set logf $out/vivado_blink.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top} err]} {
        set transient 1
        if {![catch {set fp [open $logf r]; seek $fp $mark; set tail [read $fp]; close $fp}]} {
            set transient [string match {*couldn't read file*No error*} $tail]
        }
        if {!$transient} { error "synth_design failed: $err" }
        puts "==== synth attempt $try transient glitch; retry ===="
        if {$try == 6} { error "transient glitch persisted" }
    } else { puts "==== synth ok attempt $try ===="; break }
}
opt_design
place_design
route_design
write_bitstream -force $out/blink.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/blink.bit" $out/blink.bin
puts "==== BLINK BUILD DONE ===="
puts "bin : $out/blink.bin"
