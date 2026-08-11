# =============================================================================
# build_hello.tcl - build the PYTHON 1300 chip-ID bring-up bitstreams.
#
#   vivado -mode batch -source build_hello.tcl              # both boards
#   vivado -mode batch -source build_hello.tcl -tclargs au  # just the Au V2
#   vivado -mode batch -source build_hello.tcl -tclargs pt  # just the Pt V2
#
# Builds BOTH boards by default, from the same cam_hello_core.v, so the two can
# never drift apart. Non-project mode, no IP, straight from the tracked RTL.
# Outputs .bit and raw .bin (the Alchitry loader wants the .bin) into build/.
#
# WHICH ONE DO I FLASH? Whichever board is actually on the bench:
#     alchitry.exe load --list        -> "Detected 1 Alchitry Au V2" / "Pt V2"
# The two are NOT interchangeable. An A35T bitstream sent to a Pt (or vice
# versa) transfers over JTAG and prints "Done" without configuring the silicon.
# =============================================================================
set here [file normalize [file dirname [info script]]]
set root [file normalize $here/../..]
set out  $here/build
file mkdir $out

# board -> {part top xdc}
dict set BOARDS pt    {xc7a100tfgg484-2 pt_cam_hello pt_cam_hello.xdc}
dict set BOARDS au    {xc7a35tftg256-2  au_cam_hello au_cam_hello.xdc}
# The diagnostic build (cam_probe.v). Not part of the default set -- ask for it
# by name: -tclargs probe
dict set BOARDS probe {xc7a100tfgg484-2 cam_probe    pt_cam_probe.xdc}
dict set BOARDS walk  {xc7a100tfgg484-2 cam_pinwalk  pt_cam_pinwalk.xdc}
# Same as pt, but clk_pll free-runs at 50 MHz. See pt_cam_hello_clk.v.
dict set BOARDS ptclk {xc7a100tfgg484-2 pt_cam_hello_clk pt_cam_hello.xdc}
# STAGE 2: powers up the sensor's LVDS drivers so they can be metered.
# Same ports as pt_cam_hello, so it reuses that XDC. No bank-13 pin.
dict set BOARDS lvdsen {xc7a100tfgg484-2 cam_lvds_en  pt_cam_hello.xdc}
dict set BOARDS lvdsenclk {xc7a100tfgg484-2 cam_lvds_en_clk pt_cam_hello.xdc}
dict set BOARDS regdump {xc7a100tfgg484-2 cam_regdump  pt_cam_hello.xdc}
dict set BOARDS regslow {xc7a100tfgg484-2 cam_regdump_slow pt_cam_hello.xdc}
# STAGE 1: 72 MHz MMCM + Avnet SEQ01 + PLL lock poll (STOP_AT=8).
dict set BOARDS stage1 {xc7a100tfgg484-2 cam_boot_stage1 pt_cam_hello.xdc}
# STAGE 2: same, STOP_AT=41 -- adds LVDS power-up, sequencer still off.
dict set BOARDS stage2 {xc7a100tfgg484-2 cam_boot_stage2 pt_cam_hello.xdc}
# STAGE 3: recovers clock_out on bank 13. NEEDS VBSEL AT 2.5 V.
dict set BOARDS stage3 {xc7a100tfgg484-2 cam_clkrx_stage3 pt_cam_clkrx.xdc}
# STAGE 4: full receiver + per-lane alignment. All six pairs on bank 13.
dict set BOARDS stage4 {xc7a100tfgg484-2 cam_rx_stage4 pt_cam_rx.xdc}
# Stage-4 diagnostic: raw deserialised word per lane instead of lock status.
dict set BOARDS rxdbg {xc7a100tfgg484-2 cam_rxdbg pt_cam_rx.xdc}
# Margin test: same, but 36 MHz reference -> 360 Mbps/lane, eye twice as wide.
dict set BOARDS rxslow {xc7a100tfgg484-2 cam_rxdbg_slow pt_cam_rx.xdc}
# STAGE 4b: IDELAY eye-centring at the full 720 Mbps.
dict set BOARDS idelay {xc7a100tfgg484-2 cam_idelay_stage4 pt_cam_rx.xdc}
# STAGE 5: full boot (sensor STREAMS) + sync decode + one line dumped as hex.
dict set BOARDS stage5 {xc7a100tfgg484-2 cam_line_stage5 pt_cam_rx.xdc}
# STAGE 6: 1280x256 frame into BRAM, streamed to the PC at 1 Mbaud.
dict set BOARDS stage6 {xc7a100tfgg484-2 cam_frame_stage6 pt_cam_rx.xdc}
# Sync-code counters: does FE ever appear on the wire?
dict set BOARDS syncdbg {xc7a100tfgg484-2 cam_syncdbg pt_cam_rx.xdc}

# Pins each board's XDC promises. Checked after implementation, because a
# bring-up bitstream whose pins silently moved would blame the board for a
# tools problem.
dict set PINS pt {
    clk W19  rst_n N15
    led[0] P19  led[1] P20  led[2] T21  led[3] R19
    led[4] V22  led[5] U21  led[6] T20  led[7] W20
    usb_rx AA20  usb_tx AA21
    cam_mosi AB22  cam_miso AB18  cam_sck AB21  cam_clk_pll AA18
    cam_reset_n E3  cam_ss_n N2
    cam_trigger[0] F3  cam_trigger[1] P2  cam_trigger[2] M2
    cam_monitor[0] L1  cam_monitor[1] M3
}
dict set PINS probe [dict get $PINS pt]      ;# same board, same balls
dict set PINS walk  [dict get $PINS pt]
dict set PINS ptclk [dict get $PINS pt]
dict set PINS lvdsen [dict get $PINS pt]
dict set PINS lvdsenclk [dict get $PINS pt]
dict set PINS regdump [dict get $PINS pt]
dict set PINS regslow [dict get $PINS pt]
dict set PINS stage1 [dict get $PINS pt]
dict set PINS stage2 [dict get $PINS pt]
# stage3 adds the bank-13 clock pair on top of the stage-1/2 pin set.
dict set PINS stage3 [concat [dict get $PINS pt] {cam_clkout_p Y11 cam_clkout_n Y12}]
dict set PINS syncdbg [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS stage6 [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS stage5 [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS idelay [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS rxslow [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS rxdbg [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS stage4 [concat [dict get $PINS stage3] {
    cam_d_p[0] U15  cam_d_n[0] V15   cam_d_p[1] AB16 cam_d_n[1] AB17
    cam_d_p[2] Y16  cam_d_n[2] AA16  cam_d_p[3] T14  cam_d_n[3] T15
    cam_sync_p W14  cam_sync_n Y14
}]
dict set PINS au {
    clk N14
    led[0] K13  led[1] K12  led[2] L14  led[3] L13
    led[4] M15  led[5] M14  led[6] M12  led[7] P14
    usb_rx P15  usb_tx P16
    cam_mosi N6  cam_miso P9  cam_sck M6  cam_clk_pll N9
    cam_reset_n J1  cam_ss_n L2
    cam_trigger[0] K1  cam_trigger[1] L3  cam_trigger[2] H1
    cam_monitor[0] K2  cam_monitor[1] H2
}

set targets [expr {[llength $argv] ? $argv : {pt au}}]
foreach b $targets {
    if {![dict exists $BOARDS $b]} { error "unknown board '$b' -- expected pt or au" }
}

set overall 0
foreach board $targets {
    lassign [dict get $BOARDS $board] part top xdc
    puts "\n########## BUILDING $board : $top on $part ##########\n"

    create_project -in_memory -part $part

    # Single-threaded synth. This Windows host intermittently fails to read
    # Vivado's OWN installed .tcl helpers when the multithreaded synth helper
    # spawns -- a file-lock / AV-scan race on a file that plainly exists. Same
    # workaround, and same reasoning, as build_pt.tcl. These designs are tiny.
    set_param general.maxThreads 1

    # cam_probe.v is self-contained; the two hello tops wrap cam_hello_core.
    set hdl [list \
        $root/sources_1/imports/RTL/cam_spi_master.v \
        $root/sources_1/imports/RTL/uart_tx.v \
        $here/$top.v \
    ]
    # Per-top dependency list. Written out explicitly, one entry per top: the
    # earlier glob-matching version accumulated overlapping patterns until it
    # was silently omitting modules, which surfaces as "module not found" at
    # synthesis rather than anywhere useful.
    switch -exact -- $top {
        pt_cam_hello - au_cam_hello - pt_cam_hello_clk {
            lappend hdl $here/cam_hello_core.v
        }
        cam_probe - cam_pinwalk - cam_regdump { }
        cam_regdump_slow { lappend hdl $here/cam_regdump.v }
        cam_lvds_en      { }
        cam_lvds_en_clk  { lappend hdl $here/cam_lvds_en.v }
        cam_boot_stage1 {
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
        }
        cam_boot_stage2 {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
        }
        cam_clkrx_stage3 {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
        }
        cam_rx_stage4 - cam_rxdbg {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_lvds_rx.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
        }
        cam_rxdbg_slow {
            lappend hdl $here/cam_rxdbg.v
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_lvds_rx.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
        }
        cam_idelay_stage4 {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $here/cam_lvds_rx_idelay.v
            lappend hdl $here/cam_eye_scan.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
        }
        cam_line_stage5 {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $here/cam_lvds_rx_idelay.v
            lappend hdl $here/cam_eye_scan.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
            lappend hdl $root/sources_1/imports/RTL/cam_sync_decode.v
            lappend hdl $root/sources_1/imports/RTL/cam_line_buf.v
        }
        cam_syncdbg {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $here/cam_lvds_rx_idelay.v
            lappend hdl $here/cam_eye_scan.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
        }
        cam_frame_stage6 {
            lappend hdl $here/cam_boot_stage1.v
            lappend hdl $here/cam_lvds_rx_idelay.v
            lappend hdl $here/cam_eye_scan.v
            lappend hdl $root/sources_1/imports/RTL/cam_boot_seq.v
            lappend hdl $root/sources_1/imports/RTL/cam_align.v
            lappend hdl $root/sources_1/imports/RTL/cam_sync_decode.v
        }
        default { error "no HDL dependency list for top '$top'" }
    }
    read_verilog $hdl
    read_xdc $here/$xdc

    synth_design -top $top -part $part
    opt_design
    place_design
    route_design

    # Bitgen retry. This host intermittently fails INSIDE write_bitstream, just
    # after "Loading data files...", with
    #     [Designutils 12-1097] 32 - Unknown term found in boolean expression
    # i.e. Vivado mis-parsing one of its OWN installed data files. Observed once,
    # then three consecutive successes -- including the identical command -- from
    # the same routed checkpoint, so it is a transient read fault, not a property
    # error. Retry from the routed design; write_bitstream is idempotent.
    set bitok 0
    for {set try 1} {$try <= 4} {incr try} {
        if {[catch {write_bitstream -force -bin_file $out/$top.bit} err]} {
            puts "==== $board write_bitstream attempt $try failed: $err"
            puts "==== retrying (suspected transient data-file read fault)"
        } else {
            puts "==== $board write_bitstream succeeded on attempt $try ===="
            set bitok 1
            break
        }
    }

    report_utilization    -file $out/${top}_util.rpt
    report_timing_summary -file $out/${top}_timing.rpt
    report_drc            -file $out/${top}_drc.rpt

    # ---- gate on the things that actually matter --------------------------
    set fail 0
    if {!$bitok} { puts "** write_bitstream failed 4 times"; incr fail }

    set wns [get_property SLACK [get_timing_paths -setup -max_paths 1]]
    set whs [get_property SLACK [get_timing_paths -hold  -max_paths 1]]
    puts "=== $board TIMING: setup WNS = $wns ns, hold WHS = $whs ns ==="
    if {$wns < 0} { puts "** setup timing NOT met"; incr fail }
    if {$whs < 0} { puts "** hold timing NOT met";  incr fail }

    set drc_errs [get_drc_violations -quiet -filter {SEVERITY =~ "*Error*"}]
    puts "=== $board DRC errors: [llength $drc_errs] ==="
    if {[llength $drc_errs] > 0} { incr fail }

    set nbad 0
    set npin 0
    foreach {p pin} [dict get $PINS $board] {
        incr npin
        set got [get_property PACKAGE_PIN [get_ports $p]]
        if {$got ne $pin} { puts "  ** MISMATCH $p: wanted $pin, got $got"; incr nbad }
    }
    puts "=== $board pins placed as constrained: [expr {$nbad == 0 ? "ALL OK ($npin)" : "$nbad MISMATCH(ES)"}] ==="
    incr fail $nbad

    # Bank containment. Assert it rather than trusting nobody added a port.
    if {$board eq "stage4" || [string match "rx*" $board] || $board eq "idelay" || $board eq "stage5" || $board eq "stage6" || $board eq "syncdbg"} {
        # Stage 4 owns twelve bank-13 pins: six input pairs. Assert the count
        # rather than the names -- a stray port here would be a real hazard.
        set n13 0
        foreach p [get_ports] { if {[get_property IOBANK $p] == 13} { incr n13 } }
        puts "=== $board bank-13 ports: $n13 (expect 12) ==="
        if {$n13 != 12} { incr fail }
    } elseif {$board eq "stage3"} {
        # Stage 3 is the first design that legitimately uses bank 13, so the
        # check inverts: bank 13 must contain EXACTLY the clock pair and nothing
        # else. That keeps the VBSEL exposure to the two pins we intend, and
        # catches a stray port wandering into the 2.5 V bank.
        set b13 {}
        foreach p [get_ports] {
            if {[get_property IOBANK $p] == 13} { lappend b13 [get_property NAME $p] }
        }
        set want13 {cam_clkout_n cam_clkout_p}
        set got13  [lsort $b13]
        puts "=== stage3 bank-13 ports: $got13 (expect $want13) ==="
        if {$got13 ne $want13} {
            puts "  ** bank-13 contents are not exactly the clock pair"
            incr fail
        }
    } elseif {$board ne "au"} {
        # Nothing in bank 13: the 2.5 V LVDS bank. Keeping out of it is what makes
        # these bitstreams' safety independent of the VBSEL_A strap.
        set bad 0
        foreach p [get_ports] { if {[get_property IOBANK $p] == 13} { incr bad } }
        puts "=== $board ports in bank 13: $bad (expect 0) ==="
        incr fail $bad
    } else {
        # EVERY port -- not just the camera ones -- must be in bank 14 or 35, both
        # hardwired 3.3 V on the Au. This matters because the camera board straps
        # VBSEL_A/B high, which on the Au sets bank 34's VCCO to 2.5 V
        # (CAMERA_IO_MAP.md §8.3). An LVCMOS33 port in bank 34 would be a real
        # hazard that Vivado cannot see, since it has no idea what the strap did.
        # Verified 2026-08-10: all 22 ports land in 14/35, none in 34.
        #
        # Incidentally this settles a stale comment: Au2.xdc annotates the LEDs
        # "..._15", but they place in bank 14. Bank 15 on the Au is the 1.35 V DDR
        # bank where dout0± lands (§8.2) -- no LED is in it.
        set bad 0
        foreach p [get_ports] {
            set bk [get_property IOBANK $p]
            if {$bk != 14 && $bk != 35} {
                puts "  ** port [get_property NAME $p] is in bank $bk, expected 14 or 35"
                incr bad
            }
        }
        puts "=== au ports outside banks 14/35: $bad (expect 0) ==="
        incr fail $bad
    }

    if {$fail == 0} {
        puts "########## $board BUILD OK -> $out/$top.bin ([file size $out/$top.bin] bytes) ##########"
    } else {
        puts "########## $board BUILD FAILED -- $fail problem(s) ##########"
        incr overall
    }
    close_project
}

puts ""
if {$overall == 0} {
    puts "########## ALL BUILDS OK ##########"
} else {
    puts "########## $overall BOARD(S) FAILED ##########"
    exit 1
}
