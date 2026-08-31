#!/bin/sh
# Build + run the HDMI loopback testbench with Vivado's xsim.
# Usage: sh sim/run_hdmi_tb.sh          (from the repo root)
#
# NOTE: work.glbl MUST be elaborated alongside the testbench -- UNISIM primitives
# rely on the global set/reset that configuration provides on real silicon, and
# without it their flip-flops stay X forever.
set -e
VB=/c/AMDDesignTools/2025.2.1/Vivado/bin
RTL=sources_1/imports/RTL
OUT=sim/xsim_work
mkdir -p $OUT
cd $OUT

"$VB/xvhdl" --nolog \
    ../../$RTL/tmds_encoder.vhd \
    ../../$RTL/tmds_decoder.vhd \
    ../../$RTL/deserialiser_1_to_10.vhd \
    ../../$RTL/alignment_detect.vhd \
    ../../$RTL/input_channel.vhd \
    ../../$RTL/hdmi_input.vhd

"$VB/xvlog" --nolog \
    ../../$RTL/delay1Bit.v \
    ../../$RTL/rx_freq_band.v \
    ../../$RTL/rx_drp_recfg.v \
    ../tb_hdmi_loopback.v \
    "$VB/../data/verilog/src/glbl.v"

"$VB/xelab" --nolog -debug typical -L unisims_ver -L unisim -L secureip \
    -s tb_snap tb_hdmi_loopback work.glbl

"$VB/xsim" --nolog tb_snap -R
