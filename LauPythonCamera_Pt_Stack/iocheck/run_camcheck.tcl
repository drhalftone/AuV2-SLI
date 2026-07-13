# =============================================================================
# run_evencheck.tcl
#
# Does the EVEN-row pin plan support the REAL LVDS receiver?
#
#   vivado -mode batch -source run_evencheck.tcl
#
# The DF40's two rows escape in OPPOSITE directions, and only Bank B's EVEN row
# faces the sensor (see pt_camera_even.xdc for the geometry). But bank 13's even
# row has NO MRCC pairs -- only two SRCC pairs.
#
# So: can an SRCC pin drive BUFIO + BUFR into a cascaded ISERDESE2? If yes, the
# even-row plan supports the real 1:10 LVDS receiver and the layout problem is
# solved. If no, the whole floorplan has to change.
# =============================================================================

set part "xc7a100tfgg484-2"
set here [file dirname [file normalize [info script]]]

read_verilog [file join $here pt_camera_rx.v]
read_xdc     [file normalize [file join $here .. pt_camera.xdc]]

synth_design -top pt_camera_rx -part $part
opt_design
place_design

report_io -file [file join $here even_io_report.txt]

puts "\n########## CAMERA: EVEN-ROW PINS + REAL RECEIVER ##########"
set fail 0

# --- every port placed exactly where the XDC asked -------------------------
set want {
    cam_clkout_p Y11   cam_clkout_n Y12
    cam_d_p[0]   U15   cam_d_n[0]   V15
    cam_d_p[1]   AB16  cam_d_n[1]   AB17
    cam_d_p[2]   Y16   cam_d_n[2]   AA16
    cam_d_p[3]   T14   cam_d_n[3]   T15
    cam_sync_p   W14   cam_sync_n   Y14
    cam_lvdsclk_p W15  cam_lvdsclk_n W16
    cam_mosi     AB22  cam_miso     AB18
    cam_sck      AB21  cam_clk_pll  AA18
    cam_reset_n  E3    cam_ss_n     N2
    cam_trigger[0] F3  cam_trigger[1] P2   cam_trigger[2] M2
    cam_monitor[0] L1  cam_monitor[1] M3
}
set nbad 0
foreach {p pin} $want {
    set got [get_property PACKAGE_PIN [get_ports $p]]
    if {$got ne $pin} { puts "  ** MISMATCH $p: wanted $pin, got $got"; incr nbad }
}
puts "  ports placed as constrained : [expr {$nbad == 0 ? {ALL OK (25)} : "$nbad MISMATCH(ES)"}]"
incr fail $nbad

# --- DIFF_TERM on the six INPUT pairs, and NOT on the output ---------------
set dt_in 0
foreach p {cam_clkout_p cam_d_p[0] cam_d_p[1] cam_d_p[2] cam_d_p[3] cam_sync_p} {
    if {[get_property DIFF_TERM [get_ports $p]]} { incr dt_in }
}
set dt_out [get_property DIFF_TERM [get_ports cam_lvdsclk_p]]
puts "  DIFF_TERM on input pairs    : $dt_in / 6   (expect 6)"
puts "  DIFF_TERM on the OUTPUT pair: $dt_out       (expect 0 -- R2 terminates it at the sensor)"
if {$dt_in != 6 || $dt_out != 0} { incr fail }

set n_bufio [llength [get_cells -hier -filter {REF_NAME == BUFIO}]]
set n_bufr  [llength [get_cells -hier -filter {REF_NAME == BUFR}]]
set n_iser  [llength [get_cells -hier -filter {REF_NAME == ISERDESE2}]]
set n_oddr  [llength [get_cells -hier -filter {REF_NAME == ODDR}]]

puts "  BUFIO placed     : $n_bufio   (expect 1  -- proves SRCC can drive BUFIO)"
puts "  BUFR  placed     : $n_bufr   (expect 1  -- proves SRCC can drive BUFR)"
puts "  ISERDESE2 placed : $n_iser  (expect 10 -- 5 lanes x master+slave)"
puts "  ODDR placed      : $n_oddr   (expect 1  -- the LVDS clock out)"

if {$n_bufio != 1}  { incr fail }
if {$n_bufr  != 1}  { incr fail }
if {$n_iser  != 10} { incr fail }

set clkpin [get_property PACKAGE_PIN [get_ports cam_clkout_p]]
puts "  cam_clkout_p pin : $clkpin  (expect Y11 = IO_L11P_T1_SRCC_13)"
if {$clkpin ne "Y11"} { incr fail }

set bad 0
foreach p [get_ports -filter {IOSTANDARD == LVDS_25}] {
    if {[get_property IOBANK $p] != 13} { incr bad }
}
puts "  LVDS ports outside bank 13 : $bad  (expect 0)"
incr fail $bad

puts ""
if {$fail == 0} {
    puts "########## PASS ##########"
    puts "An SRCC pin drives BUFIO + BUFR into a cascaded ISERDESE2."
    puts "The EVEN-row pin plan supports the real 1:10 LVDS receiver."
} else {
    puts "########## FAIL -- $fail problem(s) ##########"
}
puts ""
