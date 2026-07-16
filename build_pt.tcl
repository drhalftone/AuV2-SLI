#-----------------------------------------------------------------------------
# build_pt.tcl -- Au2_SLI ported to the Alchitry Pt V2 (XC7A100T-2FGG484I).
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
set out  $here/build_pt
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
        if {[get_property GENERATE_SYNTH_CHECKPOINT $ip] && ![file exists [get_property IP_OUTPUT_DIR $ip]/[get_property NAME $ip].dcp]} {
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

# ---- HDL (Au2_SLI.vhd needs VHDL-2019) ----
set vhd_all [lsort [glob $rtl/*.vhd]]
set top_vhd [file normalize $rtl/Au2_SLI.vhd]
set vhd_lib {}
foreach f $vhd_all { if {[file normalize $f] ne $top_vhd} { lappend vhd_lib $f } }
read_vhdl $vhd_lib
read_vhdl -vhdl2019 $top_vhd
read_verilog [glob $rtl/*.v]

# ---- constraints (the Pt re-pin) ----
read_xdc $here/constrs_1/imports/RTL/Au2_pt.xdc

# ---- synth + implement ----
# The same transient .tcl-read race hits the TOP synth too (it loads unimacro_vhdl.tcl when it
# starts on the VHDL top). synth_design re-elaborates from the already-read HDL/IP, so a retry
# starts clean. A REAL RTL error, though, would fail identically every attempt -- so we only
# retry when the transient's signature ("couldn't read file ...: No error") actually appears in
# the log for this attempt; anything else fails immediately. If the log can't be read (Windows
# sharing), we fall back to assuming transient so behaviour is never worse than a plain retry.
# opt/place/route do not load those helpers and need no retry.
set logf $here/build_pt/vivado.log
for {set try 1} {$try <= 6} {incr try} {
    set mark 0
    if {[file exists $logf]} { set mark [file size $logf] }
    if {[catch {synth_design -top $top -include_dirs $rtl} err]} {
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
opt_design
place_design
route_design

# ---- outputs ----
write_bitstream -force $out/Au2_SLI_pt.bit
write_cfgmem -force -format bin -interface spix4 -size 16 \
    -loadbit "up 0x0 $out/Au2_SLI_pt.bit" $out/Au2_SLI_pt.bin
report_utilization    -file $out/util.rpt
report_timing_summary -file $out/timing.rpt

set wns [get_property SLACK [lindex [get_timing_paths -setup -max_paths 1] 0]]
puts "=== TIMING: setup WNS = $wns ns ==="
puts "==== AuV2-SLI Pt V2 BUILD DONE ===="
puts "bit : $out/Au2_SLI_pt.bit"
puts "bin : $out/Au2_SLI_pt.bin"
