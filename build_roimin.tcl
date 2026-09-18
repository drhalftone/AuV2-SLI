# build_roimin.tcl -- the MINIMAL PROFILING bitstream: HDMI/SLI + cam_roi_min.
#
# Derived from build_profile.tcl by SUBTRACTION. Same part, same IP handling
# (including the absolutised .coe paths -- see below), same VHDL top. The
# differences are all removals:
#
#   WITH_CAM=2   selects cam_roi_min instead of cam_frame_ft, so the DDR3 ring,
#                the Ft+ master, the frame buffer, the per-slot ROI array and the
#                32-entry trigger queue are not in the design at all.
#   no MIG       nothing instantiates it, so it is not read or synthesised. This
#                is most of the build time.
#   no FT601     cam_frame_ft.v and the ft601_sync_* sources are not read.
#
# Two post-synthesis constraints from build_profile.tcl do not apply here and are
# gone rather than left to match nothing: the Ft+ tristate multicycle (there is no
# Ft+ bus) and, of the TLP crossing pair, the destination names have moved into
# cam_roi_min. Both remaining CDC constraints still ERROR if they match nothing.
#
# Outputs build_roimin/Au2_SLI_roimin.{bit,bin}.
#
# (original header follows)
# build_profile.tcl -- the PROJECTOR-PROFILING bitstream.
#
# Derived from build_merged.tcl. One difference that matters: WITH_TLP=0.
#
# This build drives the projector from the FPGA's own WHITE,K,K,K,K sequence and
# reads the camera's ROI mean back over the Pt's UART. There is no PC playing
# patterns over HDMI, so top-left-pixel detection has nothing to detect -- and
# dropping it removes the tlp_dbg -> tlp_ui asynchronous crossing entirely rather
# than constraining it. That crossing is 0 logic levels and 91% route, so its
# slack is set by placement luck: it measured +0.082 ns in one build and
# -3.262 ns in the next, on an unrelated change.
#
# GENLOCK_ON=1: the camera exposes once per projected frame, locked to that frame's
# vsync with zero delay, from power-up and with no host command. Genlock is normally
# enabled by camera opcode 7, which arrives only over the FT601 -- so on a rig that
# runs on the Pt's USB 2.0 port alone it could not be turned on at all.
#
# Outputs build_profile/Au2_SLI_roimin.{bit,bin} -- it does not touch the
# merged build's directory or its bitstreams.
#
#-----------------------------------------------------------------------------
# build_profile.tcl -- MERGED build: HDMI/SLI + camera + Ft+ on one Pt V2.
#
# MERGE MILESTONE M1 (MERGE_MILESTONES.md): the merged build VEHICLE. Same top
# and same function as build_pt.tcl, plus the Ft+ pins and their bus timing, so
# the datapath work at M2 lands in a project that already places and routes.
#
# Proof M1 must produce: HDMI telemetry N counting, test_silicon.py 15/15,
# offline mode selecting correctly, camera idle. Plus a REAL place-and-route,
# which is what confirms M0's static resource counts.
#
# DO NOT RUN CONCURRENTLY WITH build.tcl OR build_pt.tcl. All three resolve
# their IP gen directory to <parent>/Au2_SLI.gen and will fight over it.
#
# Derived from build_pt.tcl -- keep changes in step.
#
#   vivado -mode batch -source build_pt.tcl -log build_pt/vivado.log -journal build_pt/vivado.jou
#
# Phase 1 of the port (task #15): the existing SLI design (HDMI passthrough + pattern gen)
# plus the camera SPI/control interface, on Au2_pt.xdc. The LVDS receiver chain is added
# later (task #12). Outputs to build_pt/ (gitignored).
#
# IP HANDLING -- the committed .xci are targeted at the 35T. Retargeting them to the 100T in
# place would (a) mutate the committed files and (b) clash with the Au build's shared gen dir
# (both resolve to <parent>/Au2_SLI.gen). So we COPY the IP into build_pt/ip_work, point each
# copy's gen_directory at itself ("."), and read from the copies. The committed sources_1/ip
# is never touched, and the two builds don't collide.
#-----------------------------------------------------------------------------
set part xc7a100tfgg484-2
set top  Au2_SLI
set here [file normalize [file dirname [info script]]]
set rtl  $here/sources_1/imports/RTL
set ipd  $here/sources_1/ip
set out  $here/build_roimin
file mkdir $out

create_project -in_memory -part $part

# Force single-threaded synth. Vivado 2025.1 on this Windows host intermittently fails to
# read its OWN installed .tcl helpers ("couldn't read file .../{unimacro,retarget}_vhdl.tcl:
# No error") when the multithreaded synth helper process spawns -- a file-lock / AV-scan race
# on a file that plainly exists. Not spawning that helper sidesteps it. Costs a little
# wall-clock on the 100T; buys a deterministic build.
set_param general.maxThreads 1

# ---- IP: work on COPIES retargeted to the 100T; committed .xci untouched ----
set ipwork $out/ip_work
file delete -force $ipwork
file mkdir $ipwork
foreach d [glob -nocomplain -type d $ipd/*] {
    file copy -force $d $ipwork
}
# Repoint each copy's gen_directory. HONEST NOTE: Vivado 2025.1 largely IGNORES this for a
# physically-relocated .xci -- it detects the move and forces output products back to the .xci's
# recorded original location, i.e. the SHARED <parent>/Au2_SLI.gen that the Au 35T build
# (build.tcl) also resolves to. Reliable path-isolation of a moved .xci is not achievable this
# way, so we do NOT depend on it; correctness is guaranteed by the fresh-DCP wipe below instead.
# Consequence: the Au and Pt builds share one IP gen dir and MUST NOT be run concurrently.
#
# AND ABSOLUTISE coefficient_file, WHICH IS NOT COSMETIC. Four of these IPs are ROMs
# whose contents come from a .coe recorded as a path RELATIVE to the .xci:
#
#     sources_1/ip/LUT/LUT.xci      ../../imports/RTL/LUT.coe -> sources_1/imports/RTL/LUT.coe
#     build_profile/ip_work/LUT/... ../../imports/RTL/LUT.coe -> build_profile/imports/... GONE
#
# Copying the IP therefore breaks every .coe reference, and the way it breaks is the
# worst available: Vivado logs "Unable to open file", binds C_MEM_INIT_FILE to
# no_coe_file_loaded, and SYNTHESISES THE ROM FULL OF ZEROS. It does not stop. The
# DCP appears on disk, so the missing-DCP check below is satisfied and the build runs
# to completion -- producing a bitstream whose radiometric LUT and index maps are
# blank. A DCP existing says nothing about what is inside it.
#
# This is why LUT / LUT_V / indexMap / indexMapV -- exactly the four .coe-bearing IPs
# -- were the ones failing synth_ip deterministically. Rewriting the path to an
# absolute one removes the dependence on where the .xci happens to sit.
foreach xci [glob -nocomplain $ipwork/*/*.xci] {
    set fp [open $xci r]; set data [read $fp]; close $fp
    regsub -all {"gen_directory"[ ]*:[ ]*"[^"]*"} $data {"gen_directory": "."} data
    # The recorded path is relative to the ORIGINAL .xci directory, which is the
    # matching subdirectory of $ipd -- not to the copy it now lives in.
    set orig $ipd/[file tail [file dirname $xci]]
    while {[regexp {"coefficient_file"[ ]*:[ ]*\[[ ]*\{[ ]*"value"[ ]*:[ ]*"([^"]*)"} \
                   $data -> coe]} {
        if {[file pathtype $coe] eq "absolute"} break
        set abs [file normalize [file join $orig $coe]]
        if {![file exists $abs]} {
            error "IP coefficient file not found: '$coe' from $orig -> $abs.\
                   Left unfixed this does NOT fail the build -- it silently synthesises\
                   a ZERO-FILLED ROM. Refusing to build a bitstream with blank tables."
        }
        set before $data
        regsub -all "\"$coe\"" $data "\"$abs\"" data
        # A substitution that changes nothing would spin here forever. Fail loudly
        # instead -- a build script that hangs is worse than one that stops.
        if {$data eq $before} {
            error "could not rewrite coefficient_file '$coe' in $xci"
        }
    }
    set fp [open $xci w]; puts -nonewline $fp $data; close $fp
}
read_ip [glob $ipwork/*/*.xci]
upgrade_ip -quiet [get_ips]
generate_target all [get_ips]

# PROVE THE .coe ACTUALLY LOADED. The rewrite above fixes the path; this confirms the
# fix took, because every failure mode here is silent by nature.
#
# THE TEST IS THE GENERATED .mif, NOT AN IP PROPERTY. generate_target turns a loaded
# .coe into <IP_OUTPUT_DIR>/<name>.mif; when the .coe cannot be opened Vivado binds
# C_MEM_INIT_FILE to no_coe_file_loaded and NO .mif appears. So the file's existence
# is direct evidence the contents were read. (An earlier version of this check probed
# CONFIG.Coe_File, which is not the property name here -- it matched nothing, printed
# nothing, and would have passed a blank build silently. A check that cannot fail is
# not a check.)
set coe_checked 0
foreach xci [glob -nocomplain $ipwork/*/*.xci] {
    set fp [open $xci r]; set data [read $fp]; close $fp
    if {![regexp {"coefficient_file"} $data]} { continue }
    set nm  [file rootname [file tail $xci]]
    set ip  [get_ips $nm]
    set mif [get_property IP_OUTPUT_DIR $ip]/$nm.mif
    if {![file exists $mif]} {
        error "IP $nm declares a coefficient_file but generate_target produced no\
               $nm.mif at $mif -- the .coe did NOT load. Vivado would synthesise this\
               ROM ZERO-FILLED and still report success. Refusing to continue."
    }
    puts "### IP $nm coe loaded OK -> $nm.mif"
    incr coe_checked
}
if {$coe_checked != 4} {
    error "expected 4 .coe-bearing IPs (LUT, LUT_V, indexMap, indexMapV), checked\
           $coe_checked. Either the IP set changed or this check stopped matching --\
           and a check that matches nothing passes everything."
}

# Force fresh, part-correct IP synthesis. Because the outputs land in the shared gen dir (above),
# a stale 35T DCP from an Au build could otherwise sit there and satisfy the missing-DCP check
# below without being re-synthesised for the 100T. Delete each IP's DCP first so synth_ip MUST
# regenerate it for THIS part and the check is meaningful.
foreach ip [get_ips] {
    set dcp [get_property IP_OUTPUT_DIR $ip]/[get_property NAME $ip].dcp
    if {[file exists $dcp]} { file delete -force -- $dcp }
}

# synth_ip runs each IP out-of-context in a spawned child Vivado process. On this Windows host
# those children intermittently fail to read Vivado's OWN installed .tcl helpers ("couldn't read
# file .../{unimacro,retarget}_{vhdl,verilog}.tcl: No error") -- a file-lock / AV-scan race on a
# file that plainly exists, hitting a DIFFERENT IP each run. synth_ip is idempotent (re-synths
# only IPs whose DCP is missing), so retry. We DISTINGUISH the transient from a real error by
# PROGRESS: the transient always clears the IP it hit on the next attempt, so the missing set
# must shrink. If the SAME IPs are still missing after a re-run, that is a deterministic failure
# -- stop immediately instead of burning 6 attempts and masking it as "transient".
set prev_missing {}
for {set try 1} {$try <= 6} {incr try} {
    catch {synth_ip [get_ips]}
    set missing {}
    foreach ip [get_ips] {
        # GENERATE_SYNTH_CHECKPOINT IS NOT ALWAYS READABLE. Vivado 2025.2.1 errors
        # outright -- "Failed to get property 'GENERATE_SYNTH_CHECKPOINT' on IP
        # 'LUT'" -- and took the whole build down AFTER every IP had synthesised
        # and all five DCPs were on disk. The property is only a hint about
        # whether to expect a checkpoint; the DCP itself is the fact. Default to
        # expecting one and let the file check decide.
        set want_dcp 1
        catch { set want_dcp [get_property GENERATE_SYNTH_CHECKPOINT $ip] }
        if {$want_dcp && ![file exists [get_property IP_OUTPUT_DIR $ip]/[get_property NAME $ip].dcp]} {
            lappend missing [get_property NAME $ip]
        }
    }
    if {[llength $missing] == 0} { puts "==== all IP DCPs present after attempt $try ===="; break }
    if {$try > 1 && $missing eq $prev_missing} {
        error "synth_ip: no progress on attempt $try -- '$missing' failing deterministically, NOT the transient glitch. Check the log for a real IP synth error."
    }
    puts "==== synth_ip attempt $try: still missing '$missing' (transient glitch); retrying ===="
    set prev_missing $missing
    if {$try == 6} { error "synth_ip: DCPs still missing after 6 attempts: $missing" }
}

# ---- NO MIG. Nothing in a WITH_CAM=2 design instantiates DDR3, so reading the
# IP would only cost synthesis time and put an uninstantiated core in the
# checkpoint. The ddr3_* top-level pins stay in the entity, undriven, exactly as
# the WITH_CAM=0 build leaves them. ----

# ---- camera datapath sources not already under sources_1/imports/RTL ----
# cam_roi_min (in $rtl, picked up by the glob below) needs the boot sequencer,
# the IDELAY receiver and the eye scan. cam_frame_ft.v is NOT read: nothing
# instantiates it, and reading 2808 lines of DDR3 and FT601 logic to leave it
# uninstantiated is how a build ends up carrying what it claims to have dropped.
set camdir $here/LauPythonCamera_Pt_Stack
read_verilog [list     $camdir/hello/cam_boot_stage1.v     $camdir/hello/cam_lvds_rx_idelay.v     $camdir/hello/cam_eye_scan.v ]

# ---- HDL (Au2_SLI.vhd needs VHDL-2019) ----
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]

# ---- constraints: the Pt re-pin, plus the Ft+ pins and bus timing ----
# Au2_pt.xdc owns everything including usb_tx; pt_ftplus_merged.xdc adds only
# the 44 Ft+ pins. Verified disjoint: usb_tx was the ONLY overlap in the whole
# merge, and both files had assigned it the same ball.
read_xdc $here/constrs_1/imports/RTL/Au2_pt.xdc
read_xdc $here/constrs_1/imports/RTL/pt_ftplus_merged.xdc
# Pin locations for the ddr3_* ports, which the MIG's own .xdc used to supply.
# Without this the build place-and-routes fully, fails DRC at write_bitstream,
# and STILL exits 0. See constrs_1/imports/RTL/ddr3_park.xdc.
read_xdc $here/constrs_1/imports/RTL/ddr3_park.xdc

# ---- CAM_DIAG: route the camera status word to usb_tx for debugging ----
# Set CAM_DIAG=1 in the environment to build a diagnostic bitstream. It takes
# Port A away from the 0xA5 control plane, so test_silicon.py will not work
# against it -- that is the trade for being able to see the camera at all.
set camdiag 0
if {[info exists ::env(CAM_DIAG)]} { set camdiag $::env(CAM_DIAG) }
puts "### CAM_DIAG = $camdiag"

# ---- synth + implement ----
# The same transient .tcl-read race hits the TOP synth too (it loads unimacro_vhdl.tcl when it
# starts on the VHDL top). synth_design re-elaborates from the already-read HDL/IP, so a retry
# starts clean. A REAL RTL error, though, would fail identically every attempt -- so we only
# retry when the transient's signature ("couldn't read file ...: No error") actually appears in
# the log for this attempt; anything else fails immediately. If the log can't be read (Windows
# sharing), we fall back to assuming transient so behaviour is never worse than a plain retry.
# opt/place/route do not load those helpers and need no retry.
# A DUPLICATE SIGNAL NAME MUST STOP THE BUILD, NOT WARN.
#
# cam_frame_ft.v is 2500 lines and declares hundreds of regs. Verilog does not object
# to declaring one twice: the FIRST declaration wins, the second is DROPPED, and the
# two intended uses silently become ONE register. That is how a 3-bit FIFO pointer and
# a 1-bit reply-FIFO read enable ended up as the same signal -- popping each other's
# state. It synthesised, met timing at +0.082 ns, and would have misbehaved on
# hardware as something with no obvious connection to either.
#
# There is no reading of the netlist that recovers the intent, so this is promoted to
# an ERROR rather than left as a critical warning in a 4000-line log.
set_msg_config -id {Synth 8-11152} -new_severity ERROR
set_msg_config -id {Synth 8-9339}  -new_severity ERROR

set logf $here/build_roimin/vivado.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top -include_dirs $rtl -generic CAM_DIAG=$camdiag -generic WITH_TLP=0 -generic GENLOCK_ON=1 -generic WITH_CAM=2} err]} {
        set transient 1
        if {![catch {set fp [open $logf r]; seek $fp $mark; set tail [read $fp]; close $fp}]} {
            set transient [string match {*couldn't read file*No error*} $tail]
        }
        if {!$transient} {
            error "synth_design failed with a REAL error (not the .tcl-read glitch): $err"
        }
        puts "==== synth_design attempt $try hit the transient .tcl-read glitch; retrying ===="
        if {$try == 6} { error "synth_design: transient .tcl-read glitch persisted 6 attempts" }
    } else {
        puts "==== synth_design succeeded on attempt $try ===="
        break
    }
}
# ---- NO Ft+ TRISTATE MULTICYCLE IN THIS BUILD ------------------------------
# build_profile.tcl constrains doe_reg/boe_reg -> ft_data[*] and ERRORS if fewer
# than 36 pins match. There is no FT601 here, so those registers do not exist and
# that check would fail every time. Deleted rather than relaxed: a constraint that
# is allowed to match nothing is worse than no constraint, because it reports
# success either way.

# ---- The TLP hand-off is an ASYNCHRONOUS crossing and was never told so -------
#
# pixel_pipe's tlp_dbg (pixel clock) is captured by cam_frame_ft's tlp_ui on
# ui_clk, and the DATA is only sampled on a 2FF-synchronised toggle edge:
#
#     tlp_s <= {tlp_s[1:0], ext_tlp_tog};
#     if (tlp_s[2] ^ tlp_s[1]) tlp_ui <= ext_tlp;
#
# so the payload has been stable for two destination clocks before it is taken.
# Timing it synchronously is meaningless: the "requirement" is whatever phase two
# unrelated clocks happen to present -- 0.100 ns in the build that exposed this.
#
# THIS PATH HAS ALWAYS PASSED BY LUCK. It is ZERO logic levels and 91% route, so
# its slack is set entirely by where the placer happened to drop the two
# registers. Adding unrelated logic elsewhere in the design moved them apart and
# it failed at WNS -3.262 ns with nothing whatever wrong in the design -- the
# same shape of trap as the tristate constraint above.
#
# -datapath_only ignores clock skew and uncertainty, both meaningless across
# asynchronous clocks, and bounds only the data route -- which is the real
# requirement: land well inside the two destination clocks the handshake buys.
# THE CROSSING IS BACK, AND IT IS NOT OPTIONAL ANY MORE. It used to be deleted here
# (WITH_TLP=0) because it was failing timing and carried nothing this build needed.
# It now carries the TOP-LEFT PIXEL AS TRANSMITTED, sampled off out_red after the
# impulse mux -- the only thing in the header that says WHICH PROJECTED FRAME was on
# the wire, independent of any free-running counter. A projector whose latency exceeds
# the sequence length makes the phase counter alias, and this is what breaks the tie.
#
# So the "absent is fine" branch is gone: absent now means the field silently reads
# zero and the aliasing goes unnoticed, which is the failure this was added to prevent.
#
# A TOGGLE HANDSHAKE HAS TWO CROSSINGS, NOT ONE -- constraining the payload alone just
# relocates the violation onto the toggle (measured at -2.778 ns in build_merged.tcl).
# Both halves, and both ERROR if they match nothing.
set tog_src [get_cells -quiet -hier -regexp {.*tlp_tx_tog_reg}]
set tog_dst [get_cells -quiet -hier -regexp {.*tlp_cs_reg\[0\]}]
if {[llength $tog_src] >= 1 && [llength $tog_dst] >= 1} {
    set_max_delay -datapath_only -from $tog_src -to $tog_dst 10.000
    puts "### TLP toggle CDC max_delay applied ([llength $tog_src] -> [llength $tog_dst])"
} else {
    error "TLP TOGGLE CDC matched [llength $tog_src] src / [llength $tog_dst] dst cells.           Refusing to build: the data bus would be constrained while the toggle that           gates it is not."
}
set tlp_src [get_cells -quiet -hier -regexp {.*tlp_tx_reg\[[0-9]+\]}]
set tlp_dst [get_cells -quiet -hier -regexp {.*tlp_r[0-3]_reg\[[0-9]+\]}]
if {[llength $tlp_src] >= 8 && [llength $tlp_dst] >= 8} {
    set_max_delay -datapath_only -from $tlp_src -to $tlp_dst 10.000
    puts "### TLP CDC max_delay applied ([llength $tlp_src] src -> [llength $tlp_dst] dst)"
} else {
    error "TLP CDC constraint matched [llength $tlp_src] src / [llength $tlp_dst] dst cells.           Refusing to build: the crossing would be timed synchronously and would pass           or fail on placement luck -- it measured -3.262 ns the last time it did."
}

# ---- the ROI mean crossing: same structure, constrained BEFORE it bites -------
# roi_hold_w (wordclk) -> roi_*_o (clk), captured on a 2FF-synchronised toggle,
# exactly like the TLP path above. It is not violating today. It is the same
# shape and would fail the same way the first time placement moves, in some build
# that has nothing to do with it -- which is precisely how the TLP path was found.
set roi_src [get_cells -quiet -hier -regexp {.*roi_hold_w_reg\[[0-9]+\]}]
set roi_dst [get_cells -quiet -hier -regexp {.*roi_(mean|npx|fcnt|blk|sat)_o_reg(\[[0-9]+\])?}]
if {[llength $roi_src] >= 8 && [llength $roi_dst] >= 4} {
    set_max_delay -datapath_only -from $roi_src -to $roi_dst 10.000
    puts "### ROI CDC max_delay applied ([llength $roi_src] src -> [llength $roi_dst] dst)"
} else {
    puts "### WARNING: ROI CDC constraint matched [llength $roi_src] src /          [llength $roi_dst] dst cells and was NOT applied. The wordclk->clk          crossing is being timed synchronously; check the register names."
}

# TIMING-FOCUSED DIRECTIVES. The merged design's remaining violation is clock
# INSERTION DELAY -- ft_clk needs 6.1 ns of a 10 ns period to reach the FT601
# registers, because HDMI + camera + DDR3 now compete for the die and the FT601
# logic gets placed away from its own pins. These directives spend more effort
# on exactly that: placement that respects timing over area, and a router that
# explores alternatives.
opt_design
place_design
# phys_opt_design replicates high-fanout drivers and retimes -- the remedy for
# small routing-bound violations that would otherwise need RTL changes. The
# standalone camera build added it for exactly that reason.
phys_opt_design
# ROUTER DIRECTIVE, and the reason is a tool crash, not timing.
# The default router died TWICE at the same point -- Phase 5.1 Global Iteration,
# immediately after overlaps reached zero -- with EXCEPTION_ACCESS_VIOLATION and
# no error text (exit 116). Identical both times, so it is this netlist tripping
# a router bug rather than random instability. Explore completed on this design
# earlier in the session, so it is the strategy with evidence behind it.
route_design -directive Explore
# Post-route pass: only helps if something is still negative, and costs a minute.
if {[get_property SLACK [get_timing_paths -delay_type min_max]] < 0} {
    puts "### post-route phys_opt (still negative after route)"
    phys_opt_design
}

# ---- outputs ----
# CHECKPOINT BEFORE THE BITSTREAM. Vivado 2025.2.1 on this host intermittently
# dies with EXCEPTION_ACCESS_VIOLATION -- it took this build down mid-route once,
# and build_cam_ft.tcl carries the same guard for the same reason. Implementation
# is the expensive part and it has already succeeded by this point, so a crash at
# bitstream time can be recovered with open_checkpoint + write_bitstream instead
# of a full rebuild.
write_checkpoint -force $out/Au2_SLI_roimin_routed.dcp

write_bitstream -force $out/Au2_SLI_roimin.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI_roimin.bit" $out/Au2_SLI_roimin.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
# ---- ASSERT THE ARTEFACTS, BECAUSE THE EXIT CODE DOES NOT ---------------------
#
# vivado.bat -mode batch RETURNS 0 EVEN WHEN THE SCRIPT ABORTS. This build proved
# it twice in one session: once dying at write_bitstream on DRC UCIO-1 (the ddr3
# pins had no LOC once the MIG was dropped) and once at place_design on BIVRU-1
# -- both times printing a full, healthy-looking log and reporting success to the
# shell with nothing on disk.
#
# So the exit code is not evidence and neither is "the log ends normally". The
# file is. Same reasoning as the missing-DCP check earlier in this script, which
# was itself defeated once by a DCP that existed and was full of zeros.
#
# A Pt V2 bitstream is ~2.5 MB; anything under 1 MB is a truncated write, not a
# small design.
foreach f [list $out/Au2_SLI_roimin.bit $out/Au2_SLI_roimin.bin] {
    if {![file exists $f]} {
        puts "### BUILD FAILED: $f was never written"
        exit 1
    }
    if {[file size $f] < 1000000} {
        puts "### BUILD FAILED: $f is [file size $f] bytes -- truncated"
        exit 1
    }
}

puts "==== AuV2-SLI MINIMAL PROFILING BUILD DONE ===="
puts "bit : $out/Au2_SLI_roimin.bit ([file size $out/Au2_SLI_roimin.bit] bytes)"
puts "bin : $out/Au2_SLI_roimin.bin ([file size $out/Au2_SLI_roimin.bin] bytes)"
