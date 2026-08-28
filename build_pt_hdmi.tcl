#-----------------------------------------------------------------------------
# build_pt_hdmi.tcl -- the HDMI/SLI half ALONE on the Pt V2 (xc7a100t).
#
# WHY THIS EXISTS. Pass-through does not work on the Pt and does on the Au
# (PT_PASSTHROUGH_DEBUG.md). The merged design carries THREE IDELAYCTRLs --
# HDMI, camera and MIG -- sharing one clk200, where the Au that pass-through was
# verified on had exactly ONE. All three of the extra consumers live inside
# cam_frame_ft, so WITH_CAM=0 removes the camera, the MIG and the Ft+ in one
# move and leaves the HDMI path alone with its own delay controller.
#
# That makes this a real experiment, not just a faster build: if pass-through
# works here and not in the merged build, the merge is implicated directly. If
# it fails here too, the merge is exonerated and the fault is in the Pt HDMI
# path itself -- which is equally worth knowing, and cheaper to iterate on.
#
# It is also much faster (no MIG, no camera chain, no out-of-tree IP), which
# matters when roughly half the Vivado runs on this host die for reasons
# unrelated to the design. CHECK FOR "BUILD DONE" -- not the exit code.
#-----------------------------------------------------------------------------
#-----------------------------------------------------------------------------
# build_pt_hdmi.tcl -- MERGED build: HDMI/SLI + camera + Ft+ on one Pt V2.
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
set out  $here/build_pt_hdmi
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
foreach xci [glob -nocomplain $ipwork/*/*.xci] {
    set fp [open $xci r]; set data [read $fp]; close $fp
    regsub -all {"gen_directory"[ ]*:[ ]*"[^"]*"} $data {"gen_directory": "."} data
    set fp [open $xci w]; puts -nonewline $fp $data; close $fp
}
read_ip [glob $ipwork/*/*.xci]
upgrade_ip -quiet [get_ips]
generate_target all [get_ips]

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

# ---- NO MIG HERE, DELIBERATELY. ----
# mig_ddr3 is instantiated INSIDE cam_frame_ft.v, which WITH_CAM=0 does not
# instantiate, so synthesising the MIG out-of-context would burn the single most
# expensive IP in the build to produce a netlist nothing references. Dropping it
# is most of the reason this build exists: the remaining HDMI work needs many
# iterations, and roughly half of all Vivado runs on this host die for reasons
# unrelated to the design, so halving the cost per attempt compounds.
#
# WHY THIS BUILD IS VIABLE AT ALL (an earlier attempt was abandoned on a wrong
# premise): WITH_CAM=0 removes the Ft+ path, but NOT the register readback.
# Au2_SLI has `usb_tx <= cam_usb_tx when CAM_DIAG /= 0 else sli_usb_tx`, and
# sli_usb_tx comes from usb_link, which is instantiated independently of the
# camera. So the COM6 UART -- and every diagnostic register 0x60..0x80 in
# uart_ctrl -- is fully alive here. host tools just need --uart.

# ---- camera datapath sources not already under sources_1/imports/RTL ----
# cam_frame_ft brings the IDELAY receiver, the packer, the DDR3 ring and the
# FT601 master. cam_align / cam_sync_decode / cam_async_fifo / cam_boot_seq /
# cam_spi_master / uart_tx are already in $rtl and picked up by the glob below.
set camdir $here/LauPythonCamera_Pt_Stack
# HDMI-ONLY: the camera / Ft+ / DDR RTL is NOT read. cam_frame_ft is not
# instantiated at all (WITH_CAM=0), so there is nothing to resolve.

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
# The MIG is not instantiated here (it lives inside cam_frame_ft), so its XDC is
# never read -- but Au2_SLI's entity still declares the ddr3_* ports. Without pin
# constraints they fail DRC, and downgrading that check would let Vivado place
# them on arbitrary package pins that are physically wired to the DDR3 device.
# Constrain them to their real locations instead; with no MIG they simply idle.
read_xdc $here/constrs_1/imports/RTL/ddr3_pins_only.xdc
read_xdc $here/constrs_1/imports/RTL/pt_ftplus_merged.xdc

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
set logf $here/build_pt_hdmi/vivado.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top -include_dirs $rtl -generic CAM_DIAG=$camdiag -generic WITH_CAM=0} err]} {
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
# ---- Ft+ tri-state enables: multicycle, and it is NOT optional ----------------
#
# Ported from build_cam_ft.tcl. The tri-state enable is NOT sampled by the FT601,
# so the 1 ns set_output_delay window does not apply to it -- it only has to
# settle before the bus turnaround completes, and ft601_sync_rx waits 3 dead
# cycles on each edge. Three clock periods is the true requirement.
#
# Multicycle, NOT false path: the deadline is real, just 3 cycles rather than 1,
# and the tool should keep checking it.
#
# Without this the merged build fails at WNS -2.269 on doe_reg[*] -> ft_data[*],
# which is how it was found. The standalone camera build had it all along; the
# HDMI build (build_pt.tcl) that build_pt_hdmi.tcl was derived from never needed
# it, so it was not inherited.
#
# Must be applied HERE, after synth_design, because it references netlist pins.
# The registers now sit inside the cam_frame_ft instance, so match hierarchically
# rather than at the top level as the standalone build does.
# HDMI-ONLY: there IS no Ft+ in this build -- WITH_CAM=0 removes cam_frame_ft and
# with it the FT601 interface, so 0 pins match and the guard below would refuse.
# The guard is right to exist and is KEPT for the merged build; here the correct
# behaviour is to require ZERO, and to fail loudly if some appear, because that
# would mean WITH_CAM=0 did not actually drop the camera.
set doe_src [get_pins -quiet -hier -regexp {.*/(doe|boe)_reg\[[0-9]+\]/C}]
if {[llength $doe_src] != 0} {
    error "HDMI-only build still contains [llength $doe_src] Ft+ tristate registers --           WITH_CAM=0 did not remove cam_frame_ft. Refusing to build something that is           not the isolated HDMI design it claims to be."
}
puts "### HDMI-only: no Ft+ tristate registers present, as expected"
# (the merged build's Ft+ multicycle constraint and its guard live in
# build_merged.tcl; there is no Ft+ bus here to constrain.)

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
write_checkpoint -force $out/Au2_SLI_pt_hdmi_routed.dcp

write_bitstream -force $out/Au2_SLI_pt_hdmi.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI_pt_hdmi.bit" $out/Au2_SLI_pt_hdmi.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== AuV2-SLI Pt HDMI-ONLY BUILD DONE ===="
puts "bit : $out/Au2_SLI_pt_hdmi.bit"
puts "bin : $out/Au2_SLI_pt_hdmi.bin"
