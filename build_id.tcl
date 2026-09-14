# build_id.tcl -- the build identity every Au2_SLI bitstream serves at regs 0x08..0x0F.
#
# Sourced by build.tcl, build_pt.tcl, build_pt_hdmi.tcl and build_merged.tcl, AFTER
# `here` is set. Produces $build_id_generics, appended to synth_design.
#
#   BUILD_GIT   7-hex-digit short hash as an integer (28 bits -- inside a VHDL integer)
#   BUILD_DIRTY 1 if tracked files had uncommitted changes at synthesis
#   BUILD_EPOCH unix seconds at synthesis (31 bits, good until 2038)
#
# NEVER FAILS THE BUILD. No git on PATH, or not a checkout: the hash and dirty flag
# are 0, and a host reading 0x08..0x0B sees "unknown" rather than a plausible lie.
# The epoch is always real.
set build_git   0
set build_dirty 0
set build_epoch [clock seconds]
if {[catch {
    set h [string trim [exec git -C $here rev-parse --short=7 HEAD]]
    scan $h %x build_git
    # --untracked-files=no: a stray log in the checkout is not a different design
    if {[string length [string trim [exec git -C $here status --porcelain --untracked-files=no]]] > 0} {
        set build_dirty 1
    }
} err]} {
    puts "### build_id: git unavailable ($err) -- BUILD_GIT/BUILD_DIRTY left 0"
    set build_git 0
    set build_dirty 0
}
set build_id_generics [list -generic BUILD_GIT=$build_git -generic BUILD_DIRTY=$build_dirty \
                            -generic BUILD_EPOCH=$build_epoch]
puts [format "### build_id: git %07x%s  epoch %d" $build_git \
          [expr {$build_dirty ? " (dirty)" : ""}] $build_epoch]
