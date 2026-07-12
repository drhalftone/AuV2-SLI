# =============================================================================
# run_stackcheck.tcl
#
# Prove the WHOLE STACK fits, not just the camera:
#
#     Pt V2  +  Hd (bottom)  +  Ft+ (bottom)  +  camera element (top)
#
#   vivado -mode batch -source run_stackcheck.tcl
#
# Loads Alchitry's OWN published constraint files alongside ours:
#     pt_base.xdc            (the Pt's own clk / rst / LEDs / USB)
#     pt_hd_bottom.xdc       (2x micro-HDMI, TMDS_33)
#     pt_ft_plus_bottom.xdc  (FT601Q 32-bit FIFO, LVCMOS33)
#     pt_camera.xdc          (ours: 7 LVDS_25 pairs in bank 13 + 11 LVCMOS33)
#
# If place_design succeeds, the stack-compatibility argument is proved by the
# tool rather than by my reading of Alchitry's .acf sources.
# =============================================================================

set part "xc7a100tfgg484-2"
set here [file dirname [file normalize [info script]]]

puts "\n########## FULL-STACK I/O CHECK ##########\n"

read_verilog [file join $here pt_stack_iocheck.v]

read_xdc [file join $here alchitry_pt_base.xdc]
read_xdc [file join $here alchitry_pt_hd_bottom.xdc]
read_xdc [file join $here alchitry_pt_ft_plus_bottom.xdc]
read_xdc [file normalize [file join $here .. pt_camera.xdc]]

synth_design -top pt_stack_iocheck -part $part
opt_design
place_design

report_io  -file [file join $here stack_io_report.txt]
report_drc -file [file join $here stack_drc_report.txt]

puts "\n########## RESULTS ##########"
set fail 0

# --- 1. every port placed, and no pin claimed twice --------------------------
set pins {}
set nports 0
foreach p [get_ports] {
    set pin [get_property PACKAGE_PIN $p]
    if {$pin eq ""} { puts "  ** $p has no PACKAGE_PIN"; incr fail; continue }
    if {[dict exists $pins $pin]} {
        puts "  ** PIN COLLISION on $pin: [dict get $pins $pin] and $p"
        incr fail
    }
    dict set pins $pin $p
    incr nports
}
puts "  ports placed                 : $nports"
puts "  pin collisions               : [expr {$fail == 0 ? {NONE} : $fail}]"

# --- 2. bank voltages --------------------------------------------------------
set fh [open [file join $here stack_io_report.txt] r]; set rpt [read $fh]; close $fh
foreach b {13 14 16 34 35} {
    set v ""
    foreach line [split $rpt "\n"] {
        if {![string match "*VCCO_${b} *" $line]} { continue }
        foreach f [split $line "|"] {
            set f [string trim $f]
            if {[regexp {^[0-9]+\.[0-9]+$} $f]} { set v $f }
        }
        if {$v ne ""} { break }
    }
    if {$v ne ""} { puts [format "  bank %-2s VCCO = %s V" $b $v] }
}

# --- 3. the headline: LVDS_25 @ 2.5V coexisting with TMDS_33 @ 3.3V ----------
set n_lvds  [llength [get_ports -filter {IOSTANDARD == LVDS_25}]]
set n_tmds  [llength [get_ports -filter {IOSTANDARD == TMDS_33}]]
set n_cmos  [llength [get_ports -filter {IOSTANDARD == LVCMOS33}]]
puts ""
puts "  LVDS_25  ports (camera, bank 13 @ 2.5 V) : $n_lvds   (expect 14)"
puts "  TMDS_33  ports (Hd HDMI,        @ 3.3 V) : $n_tmds   (expect 16)"
puts "  LVCMOS33 ports (Ft+/base/cam ctl @3.3 V) : $n_cmos"
if {$n_lvds != 14 || $n_tmds != 16} { incr fail }

puts ""
if {$fail == 0} {
    puts "########## PASS ##########"
    puts "Pt V2 + Hd + Ft+ + camera all place together."
    puts "No pin conflicts. Bank 13 @ 2.5V coexists with TMDS_33/LVCMOS33 @ 3.3V."
} else {
    puts "########## FAIL -- $fail problem(s) ##########"
}
puts ""
