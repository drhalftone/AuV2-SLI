#-----------------------------------------------------------------------------
# run_ftvideo.tcl -- build the FT601 USB-3 video throughput bitstream for the Pt V2.
#
#   vivado -mode batch -source run_ftvideo.tcl \
#          -log out/vivado.log -journal out/vivado.jou -tclargs <1|3>
#
# -tclargs picks the pixel format (default 1): 1 = 8-bit SLI cosine fringes,
# 3 = packed 10-bit MIPI RAW10 counter. Outputs are named per format, so the two
# bitstreams coexist in out/ and neither overwrites the other.
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

# Pixel format, selected with -tclargs (default 1). See ft_video_top.v.
#   1 = 8-bit SLI cosine fringes   -> ft_video.bin        (1,310,752 B/frame)
#   3 = packed 10-bit MIPI RAW10   -> ft_video_raw10.bin  (1,638,432 B/frame)
set pix_fmt 1
if {[llength $argv] > 0} { set pix_fmt [lindex $argv 0] }
if {$pix_fmt != 1 && $pix_fmt != 3} {
    error "PIX_FMT must be 1 (8-bit SLI) or 3 (packed RAW10), got '$pix_fmt'"
}
set name [expr {$pix_fmt == 3 ? "ft_video_raw10" : "ft_video"}]
puts "==== building PIX_FMT=$pix_fmt -> $name ===="

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
    if {[catch {synth_design -top $top -include_dirs $rtl                                 -generic PIX_FMT=$pix_fmt} err]} {
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

write_bitstream -force $out/$name.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/$name.bit" $out/$name.bin
report_utilization    -file $out/$name.util.rpt
report_timing_summary -file $out/$name.timing.rpt

# -----------------------------------------------------------------------------
# FT601 bus integrity checks. Both of these guard the bug found on 2026-07-30,
# where ft_data[31:16] was silently corrupt on the wire: the bus was driven
# combinationally AND was completely unconstrained, so the tool reported a clean
# WNS for a design that missed the FT601 setup window on half its data bits.
# A green WNS means nothing if the path was never in the timing graph.
# -----------------------------------------------------------------------------

# (1) Every per-cycle-toggling signal the FT601 samples must launch from an IOB
#     (OLOGIC) flop: the 32 data bits and WR#. BE/OE#/RD# are static levels driven
#     from constants -- no per-cycle edge to violate -- so they are correctly
#     absent here.
#
#     WR# is the subtle one. `accepted` reads it back, and an IOB flop's Q is not
#     visible to the fabric, so ONE flop cannot serve both. Vivado resolves this
#     itself by replicating (wr_n_pad_reg stays in a SLICE for the logic,
#     wr_n_pad_reg_rep goes to OLOGIC and drives the pin). We therefore assert on
#     placement, not on names: all 32 data flops must be in OLOGIC, and at least
#     one WR# copy must be too. Counting cells by name would fail purely because
#     the tool chose to replicate.
set data_ffs [get_cells -hier -filter {NAME =~ *u_tx/dout_q_reg*}]
if {[llength $data_ffs] != 32} {
    error "expected 32 FT601 data flops, found [llength $data_ffs] -- did the RTL change?"
}
set stragglers {}
foreach c $data_ffs {
    if {![string match "OLOGIC*" [get_property LOC $c]]} {
        lappend stragglers "[get_property NAME $c] @ [get_property LOC $c]"
    }
}
set wr_iob 0
foreach c [get_cells -hier -filter {NAME =~ *u_tx/wr_n_pad*}] {
    if {[string match "OLOGIC*" [get_property LOC $c]]} { incr wr_iob }
}
puts "=== FT601 IOB packing: [expr {32 - [llength $stragglers]}]/32 data flops + \
$wr_iob WR# copy in OLOGIC ==="
if {[llength $stragglers] > 0 || $wr_iob < 1} {
    puts "!!! FT601 OUTPUTS NOT IOB-PACKED -- the setup-window bug can return."
    foreach s $stragglers { puts "      $s" }
    if {$wr_iob < 1} { puts "      WR# has no OLOGIC copy driving the pad" }
    error "FT601 output flops not packed into IOBs"
}

# (2) The bus must actually BE IN THE TIMING GRAPH. This is the check that would
#     have caught the original bug: with set_output_delay missing, there are no
#     max-delay paths to these pins at all, so the tool has nothing to fail on and
#     happily reports a clean WNS. Zero paths here is the smoking gun, so treat
#     "no paths" as an error rather than as "nothing to check".
report_timing -to [get_ports {ft_data[*] ft_wr}] -delay_type max \
              -max_paths 10 -file $out/$name.bus_timing.rpt
set ft_paths [get_timing_paths -quiet -to [get_ports {ft_data[*] ft_wr}] \
                               -delay_type max -max_paths 1]
if {[llength $ft_paths] == 0} {
    error "FT601 bus has NO max-delay timing paths -- set_output_delay is missing \
and the interface is untimed (this is the 2026-07-30 bug)"
}
set ft_wns [get_property SLACK [lindex $ft_paths 0]]
puts "=== FT601 bus setup slack (clock-to-out vs FT601 window) = $ft_wns ns ==="
if {$ft_wns < 0} {
    puts "!!! FT601 BUS MISSES SETUP -- expect corrupt bytes on the far-bank bits."
    error "FT601 output timing not met ($ft_wns ns)"
}

set ft_hold [get_timing_paths -quiet -to [get_ports {ft_data[*] ft_wr}] \
                              -delay_type min -max_paths 1]
if {[llength $ft_hold] > 0} {
    set ft_whs [get_property SLACK [lindex $ft_hold 0]]
    puts "=== FT601 bus hold slack = $ft_whs ns ==="
    if {$ft_whs < 0} { error "FT601 output hold not met ($ft_whs ns)" }
}

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== FT601 USB-3 VIDEO BUILD DONE ===="
puts "bit : $out/$name.bit"
puts "bin : $out/$name.bin"
