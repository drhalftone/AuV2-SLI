# ddr3_park.xdc -- pin locations for the DDR3 signals in a build WITH NO MIG.
#
# WHY THIS EXISTS. build_roimin.tcl does not read the MIG, and the MIG's own
# generated .xdc is where the DDR3 pin locations lived. The ddr3_* ports are
# still on the Au2_SLI entity, so without these constraints the build
# place-and-routes in full, dies at write_bitstream on DRC UCIO-1 / NSTD-1, and
# vivado.bat STILL EXITS 0. A complete-looking run, a success exit code, and no
# bitstream -- which is why this is a committed constraints file rather than a
# downgraded DRC check.
#
# LOCATIONS COPIED FROM THE MIG, NOT INVENTED. Every PACKAGE_PIN below was
# extracted from mig_ddr3/user_design/constraints/mig_ddr3.xdc, so the pins are
# the ones the DDR3 device is actually wired to. Letting Vivado place them itself
# was the alternative and it is not acceptable: real package pins, a real DRAM
# behind them, and an arbitrary assignment drives arbitrary nets on a populated
# board.
#
# TWO DELIBERATE DEPARTURES FROM THE MIG's FILE, both found by DRC:
#
#   dq / dqs ARE NOT LISTED. They are bidirectional, nothing drives or reads
#   them here, and they are optimised out of the netlist -- constraining a port
#   that does not exist is itself an error. Driving them to 'Z' to keep them
#   alive was tried and is worse: that gives them SSTL135 INPUT buffers, which
#   need a bank VREF only the MIG configures, and the build then fails
#   DRC BIVRU-1 at place_design instead.
#
#   ck_p / ck_n ARE SSTL135, NOT DIFF_SSTL135. The MIG drives them as one
#   differential pair through an OBUFDS. On the Au2_SLI entity they are two
#   independent single-ended ports, and a differential IOSTANDARD on a
#   single-ended port is DRC IOSTDTYPE-1. They are parked at static levels, so
#   there is no clock here for a differential buffer to carry.
#
# The ports are driven to their INACTIVE levels in Au2_SLI's gen_cammin branch
# -- reset asserted, CKE low, CS deselected, commands high -- so the DRAM is
# held quiescent rather than left at whatever an undriven output ties to.

set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[0]}]
set_property PACKAGE_PIN K14 [get_ports {ddr3_addr[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[1]}]
set_property PACKAGE_PIN M15 [get_ports {ddr3_addr[1]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[2]}]
set_property PACKAGE_PIN N18 [get_ports {ddr3_addr[2]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[3]}]
set_property PACKAGE_PIN K16 [get_ports {ddr3_addr[3]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[4]}]
set_property PACKAGE_PIN L14 [get_ports {ddr3_addr[4]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[5]}]
set_property PACKAGE_PIN K18 [get_ports {ddr3_addr[5]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[6]}]
set_property PACKAGE_PIN M13 [get_ports {ddr3_addr[6]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[7]}]
set_property PACKAGE_PIN L18 [get_ports {ddr3_addr[7]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[8]}]
set_property PACKAGE_PIN L13 [get_ports {ddr3_addr[8]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[9]}]
set_property PACKAGE_PIN M18 [get_ports {ddr3_addr[9]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[10]}]
set_property PACKAGE_PIN K13 [get_ports {ddr3_addr[10]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[11]}]
set_property PACKAGE_PIN L15 [get_ports {ddr3_addr[11]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[12]}]
set_property PACKAGE_PIN M16 [get_ports {ddr3_addr[12]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_addr[13]}]
set_property PACKAGE_PIN L16 [get_ports {ddr3_addr[13]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ba[0]}]
set_property PACKAGE_PIN K19 [get_ports {ddr3_ba[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ba[1]}]
set_property PACKAGE_PIN N20 [get_ports {ddr3_ba[1]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ba[2]}]
set_property PACKAGE_PIN M20 [get_ports {ddr3_ba[2]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ck_n[0]}]
set_property PACKAGE_PIN J17 [get_ports {ddr3_ck_n[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ck_p[0]}]
set_property PACKAGE_PIN K17 [get_ports {ddr3_ck_p[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_cke[0]}]
set_property PACKAGE_PIN M22 [get_ports {ddr3_cke[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_cs_n[0]}]
set_property PACKAGE_PIN N19 [get_ports {ddr3_cs_n[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_dm[0]}]
set_property PACKAGE_PIN H22 [get_ports {ddr3_dm[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_dm[1]}]
set_property PACKAGE_PIN G13 [get_ports {ddr3_dm[1]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_odt[0]}]
set_property PACKAGE_PIN M17 [get_ports {ddr3_odt[0]}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_cas_n}]
set_property PACKAGE_PIN N22 [get_ports {ddr3_cas_n}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_ras_n}]
set_property PACKAGE_PIN L20 [get_ports {ddr3_ras_n}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_reset_n}]
set_property PACKAGE_PIN J19 [get_ports {ddr3_reset_n}]
set_property IOSTANDARD SSTL135 [get_ports {ddr3_we_n}]
set_property PACKAGE_PIN L19 [get_ports {ddr3_we_n}]
