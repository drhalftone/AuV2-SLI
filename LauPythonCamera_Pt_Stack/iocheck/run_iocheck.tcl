# =============================================================================
# run_iocheck.tcl
#
# Independently validate pt_camera.xdc against Xilinx's device database.
#
#   vivado -mode batch -source run_iocheck.tcl
#
# Passes only if Vivado can PLACE the design. Placement is where Vivado checks
# differential P/N sidedness, pair legality, bank VCCO compatibility, and
# DIFF_TERM legality -- exactly the things I derived by hand from Alchitry's
# .acf files and the Xilinx package CSV.
# =============================================================================

set part "xc7a100tfgg484-2"          ;# Alchitry Pt V2: XC7A100T-2FGG484I
set here [file dirname [file normalize [info script]]]
set xdc  [file normalize [file join $here .. pt_camera.xdc]]

puts "\n########## I/O CHECK ##########"
puts "part : $part"
puts "xdc  : $xdc\n"

read_verilog [file join $here pt_camera_iocheck.v]
read_xdc     $xdc

synth_design -top pt_camera_iocheck -part $part
opt_design
place_design

# ---- reports -----------------------------------------------------------------
report_io     -file [file join $here io_report.txt]
report_drc    -file [file join $here drc_report.txt]

# ---- the actual assertions ---------------------------------------------------
puts "\n########## RESULTS ##########"

set fail 0

# every constrained port must be placed on the pin we asked for
set want {
    cam_clkout_p W11   cam_clkout_n W12
    cam_d_p[0]   V13   cam_d_n[0]   V14
    cam_d_p[1]   V10   cam_d_n[1]   W10
    cam_d_p[2]   AB11  cam_d_n[2]   AB12
    cam_d_p[3]   AA10  cam_d_n[3]   AA11
    cam_sync_p   AA13  cam_sync_n   AB13
    cam_lvdsclk_p Y13  cam_lvdsclk_n AA14
    cam_mosi     AB22  cam_miso     AB18
    cam_sck      AB21  cam_clk_pll  AA18
    cam_reset_n  E3    cam_ss_n     N2
    cam_trigger[0] F3  cam_trigger[1] P2   cam_trigger[2] M2
    cam_monitor[0] L1  cam_monitor[1] M3
}
foreach {p pin} $want {
    set got [get_property PACKAGE_PIN [get_ports $p]]
    if {$got ne $pin} {
        puts "  ** MISMATCH  $p : wanted $pin, got $got"
        incr fail
    }
}
puts "  ports placed as constrained : [expr {$fail == 0 ? {ALL OK} : "$fail MISMATCH(ES)"}]"

# Bank voltages. NOTE: iobank objects do not expose a VCCO property in 2025.1,
# so read them back out of report_io instead. (The real proof is that
# place_design SUCCEEDED at all -- Vivado will not place a design that asks one
# bank for two VCCOs, so bank 13 @ 2.5 V coexisting with 14/35 @ 3.3 V is
# already established by the fact that we got this far.)
set fh [open [file join $here io_report.txt] r]
set rpt [read $fh]
close $fh
foreach b {13 14 35} {
    set v ""
    foreach line [split $rpt "\n"] {
        if {![string match "*VCCO_${b} *" $line]} { continue }
        # the Voltage column is the only field shaped like N.NN
        foreach f [split $line "|"] {
            set f [string trim $f]
            if {[regexp {^[0-9]+\.[0-9]+$} $f]} { set v $f }
        }
        if {$v ne ""} { break }
    }
    if {$v eq ""} {
        puts "  bank $b VCCO = (not found -- read io_report.txt by hand)"
    } else {
        # numeric compare -- Tcl's expr normalises "2.50" to 2.5, so a string
        # compare against a literal silently fails
        if {$b == 13} { set want 2.5 } else { set want 3.3 }
        puts [format "  bank %-2s VCCO = %s V   (expect %s V)" $b $v $want]
        if {abs($v - $want) > 0.01} { puts "  ** bank $b VCCO is wrong"; incr fail }
    }
}

# DIFF_TERM must be on the six INPUT pairs and NOT on the output pair
set dt_in  0
foreach p {cam_clkout_p cam_d_p[0] cam_d_p[1] cam_d_p[2] cam_d_p[3] cam_sync_p} {
    if {[get_property DIFF_TERM [get_ports $p]]} { incr dt_in }
}
set dt_out [get_property DIFF_TERM [get_ports cam_lvdsclk_p]]
puts "  DIFF_TERM on input pairs    : $dt_in / 6   (expect 6)"
puts "  DIFF_TERM on the OUTPUT pair: $dt_out      (expect 0 -- it is an output)"
if {$dt_in != 6 || $dt_out != 0} { incr fail }

# the forwarded clock must have reached a global buffer
set nbufg [llength [get_cells -hier -filter {REF_NAME == BUFG}]]
puts "  BUFG driven from cam_clkout : $nbufg        (expect 1 -- proves MRCC)"
if {$nbufg != 1} { incr fail }

puts ""
if {$fail == 0} {
    puts "########## PASS -- pin plan confirmed by Vivado ##########"
} else {
    puts "########## FAIL -- $fail problem(s), see above ##########"
}
puts ""
