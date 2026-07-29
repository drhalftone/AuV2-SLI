#-----------------------------------------------------------------------------
# run_ftvideo.tcl -- build the FT601 USB-3 video throughput bitstream for the Pt V2.
#
#   vivado -mode batch -source run_ftvideo.tcl \
#          -log out/vivado.log -journal out/vivado.jou
#
# Pure Verilog, no IP. Reuses Alchitry's own published constraint files (the same
# pins the full-stack I/O check already placed):
#     pt_base.xdc            (clk / rst_n / led / usb, + bitstream config)
#     pt_ft_plus_bottom.xdc  (FT601Q 32-bit 245-sync FIFO, LVCMOS33, ft_clk 100 MHz)
#
# Outputs .bit + .bin (spix4) to ft_usb_video/build/out/. Flash the .bin with the
# Alchitry loader:  Alchitry.exe load --bin out/ft_video.bin --board PtV2 --ram
#-----------------------------------------------------------------------------
set part xc7a100tfgg484-2
set top  ft_video_top

set here  [file normalize [file dirname [info script]]]
set root  [file normalize $here/..]              ;# ft_usb_video/
set repo  [file normalize $root/..]              ;# repo root
set rtl   $root/rtl
set xdcd  $repo/LauPythonCamera_Pt_Stack/iocheck
set out   $here/out
file mkdir $out

# Single-threaded synth: this Windows host intermittently fails to read Vivado's
# OWN installed .tcl helpers when the multithreaded synth helper spawns (a file-lock
# / AV-scan race). Not spawning it sidesteps the glitch. See build_pt.tcl.
set_param general.maxThreads 1

create_project -in_memory -part $part

read_verilog [glob $rtl/*.v]
read_xdc $xdcd/alchitry_pt_base.xdc
# Ft+ is on TOP of the Pt in this stack -> use the TOP pin map (ft_clk = H4, etc.).
# The Pt's top and bottom connectors are independent banks with different FPGA pins;
# using the _bottom map with the board on top leaves ft_clk (D17) unconnected.
read_xdc $here/alchitry_pt_ft_plus_top.xdc

# ---- synth (retry only on the known transient .tcl-read glitch) ----
set logf $out/vivado.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top -include_dirs $rtl} err]} {
        set transient 1
        if {![catch {set fp [open $logf r]; seek $fp $mark; set tail [read $fp]; close $fp}]} {
            set transient [string match {*couldn't read file*No error*} $tail]
        }
        if {!$transient} { error "synth_design failed with a REAL error: $err" }
        puts "==== synth_design attempt $try hit the transient .tcl-read glitch; retrying ===="
        if {$try == 6} { error "synth_design: transient glitch persisted 6 attempts" }
    } else {
        puts "==== synth_design succeeded on attempt $try ===="
        break
    }
}

opt_design
place_design
route_design

write_bitstream -force $out/ft_video.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/ft_video.bit" $out/ft_video.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== FT601 USB-3 VIDEO BUILD DONE ===="
puts "bit : $out/ft_video.bit"
puts "bin : $out/ft_video.bin"
