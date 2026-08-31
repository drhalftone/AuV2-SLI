`timescale 1ps/1ps
//=============================================================================
// tb_hdmi_loopback -- drive hdmi_input from the project's OWN tmds_encoder with
// a known 640x480@75 raster, and watch where the control period appears.
//
// WHY THIS EXISTS. On hardware the decoder is only observable through 8-bit
// windowed counters, and at 640x480 it alternates between decoding perfectly
// (in_vdp 119..760, width 641, exact) and losing control-period decode
// ENTIRELY for stretches -- pix_pos reached 2072, i.e. 2.5 lines with no hsync
// detected. Counters cannot show WHICH cycles break or what symbol was on the
// wire when they did. This can.
//
// The stimulus is a GOLDEN reference by construction: the same timing that the
// offline path generates (and which drives a flawless picture at this exact
// mode, v_active span 0), encoded by the design's own tmds_encoder. So any
// difference between what goes in and what hdmi_input recovers is the decoder's.
//
// Bits are shifted out behaviourally at 10x pixel rate rather than through
// serialiser_10_to_1, so no OSERDES/MMCM-on-the-TX-side models are needed.
// DVI/HDMI transmits LSB first.
//=============================================================================
module tb_hdmi_loopback;
    // Word-boundary rotation. The DUT's 10-bit boundary is fixed by its parallel
    // clock; normally alignment_detect bitslips until it matches, but it needs ~1M
    // pixel clocks per adjustment (33 ms) so it can never converge in simulation.
    // Rotating the transmitted word by WPH bits is exactly equivalent to a bitslip
    // of WPH, so the testbench sweeps WPH to find the alignment instead.
    parameter integer WPH = 0;

    // ---- 640x480@75 DMT, -hsync -vsync (mode_table.vh idx 4) ----------------
    localparam HACT=640, HFP=16,  HS=64, HBP=120, HTOT=840;
    localparam VACT=480, VFP=1,   VS=3,  VBP=16,  VTOT=500;
    localparam HPOL=1'b0, VPOL=1'b0;          // 0 = negative

    // 31.5 MHz pixel, 315 MHz bit
    localparam real PIX_PS = 31746.0;
    localparam real BIT_PS = 3174.6;

    // clk_pix is DERIVED from clk_bit by /10. Two independent always blocks put
    // their edges at exactly the same instant (10 bit periods == 1 pixel period),
    // which races the shifter's load against the symbol register's update and
    // corrupts every symbol. Deriving makes the ordering deterministic.
    reg clk_bit = 0, clk200 = 0, sysclk = 0;
    always #(BIT_PS/2.0) clk_bit <= ~clk_bit;
    always #2500          clk200 <= ~clk200;   // 200 MHz
    always #5000          sysclk <= ~sysclk;   // 100 MHz

    reg [3:0] bitc = 0;
    always @(posedge clk_bit) bitc <= (bitc == 9) ? 4'd0 : bitc + 4'd1;
    wire clk_pix = (bitc < 5);                 // 31.5 MHz, phase-locked to clk_bit

    // ---- raster generator ---------------------------------------------------
    reg [11:0] hc = 0, vc = 0;
    always @(posedge clk_pix) begin
        if (hc == HTOT-1) begin
            hc <= 0;
            vc <= (vc == VTOT-1) ? 0 : vc + 1'b1;
        end else hc <= hc + 1'b1;
    end

    wire h_active = (hc < HACT);
    wire v_active = (vc < VACT);
    wire active   = h_active & v_active;
    wire hsync_r  = (hc >= HACT+HFP) && (hc < HACT+HFP+HS);
    wire vsync_r  = (vc >= VACT+VFP) && (vc < VACT+VFP+VS);
    wire hsync    = HPOL ? hsync_r : ~hsync_r;    // transmitted level
    wire vsync    = VPOL ? vsync_r : ~vsync_r;

    // Video preamble = the 8 characters immediately BEFORE the 2 guard chars,
    // which themselves immediately precede active video.
    wire in_guard = (hc >= HTOT-2);
    wire in_pre   = (hc >= HTOT-10) && (hc < HTOT-2);

    // test pattern: vertical ramp so a shifted raster is visible in the dump
    wire [7:0] px = hc[7:0];

    // ---- the design's own encoder, one per channel --------------------------
    wire [9:0] e0, e1, e2;
    wire [1:0] c0 = {vsync, hsync};
    wire [1:0] c1 = in_pre ? 2'b01 : 2'b00;   // video preamble: ch1 CTL0=1
    wire [1:0] c2 = 2'b00;                    // video preamble: ch2 CTL2=0
    tmds_encoder u_e0 (.clk(clk_pix), .data(px), .c(c0), .blank(~active), .encoded(e0));
    tmds_encoder u_e1 (.clk(clk_pix), .data(px), .c(c1), .blank(~active), .encoded(e1));
    tmds_encoder u_e2 (.clk(clk_pix), .data(px), .c(c2), .blank(~active), .encoded(e2));

    // Video guard band (HDMI 1.3a 5.2.2.1): ch0/ch2 = 1011001100, ch1 = 0100110011
    localparam [9:0] GB02 = 10'b1011001100, GB1 = 10'b0100110011;
    reg [9:0] s0, s1, s2;
    always @(posedge clk_pix) begin
        s0 <= in_guard ? GB02 : e0;
        s1 <= in_guard ? GB1  : e1;
        s2 <= in_guard ? GB02 : e2;
    end

    // ---- behavioural 10:1 serialiser, LSB first -----------------------------
    reg [9:0] sr0, sr1, sr2;
    // Driven on the NEGEDGE so each bit is centred in its bit period at the DUT's
    // sampling edge. alignment_detect cannot help us here: it waits ~1M pixel clocks
    // (33 ms) between IDELAY adjustments, so a 32-tap sweep would be a full second of
    // simulated time. The stimulus has to arrive already aligned.
    always @(negedge clk_bit) begin
        if (bitc == 9) begin
            sr0 <= (s0 >> WPH) | (s0 << (10-WPH));    // load, rotated by WPH
            sr1 <= (s1 >> WPH) | (s1 << (10-WPH));
            sr2 <= (s2 >> WPH) | (s2 << (10-WPH));
        end else begin
            sr0 <= {1'b0, sr0[9:1]}; sr1 <= {1'b0, sr1[9:1]}; sr2 <= {1'b0, sr2[9:1]};
        end
    end
    wire ser0 = sr0[0], ser1 = sr1[0], ser2 = sr2[0];

    // ---- DUT ----------------------------------------------------------------
    wire raw_blank, raw_hsync, raw_vsync, pll_locked, symbol_sync, idelay_rdy;
    wire [7:0] rch0, rch1, rch2;
    wire [31:0] ctl_diag, vdp_diag, pre_diag, gb_diag;

    hdmi_input dut (
        .system_clk(sysclk), .clk200(clk200),
        .hdmi_in_clk(clk_pix), .hdmi_in_ch0(ser0), .hdmi_in_ch1(ser1), .hdmi_in_ch2(ser2),
        .pll_locked(pll_locked), .symbol_sync(symbol_sync), .idelay_rdy(idelay_rdy),
        .raw_blank(raw_blank), .raw_hsync(raw_hsync), .raw_vsync(raw_vsync),
        .raw_ch0(rch0), .raw_ch1(rch1), .raw_ch2(rch2),
        .ctl_diag(ctl_diag), .vdp_diag(vdp_diag), .pre_diag(pre_diag), .gb_diag(gb_diag)
    );

    // ---- observation --------------------------------------------------------
    // Count what the DECODER produces, against what the generator SENT, so the
    // two are directly comparable in the same units the hardware reports.
    wire dut_pix = dut.pixel_clk;
    integer blank_lo = 0, ctl_hi = 0;      // recovered active px / control px
    integer sent_active = 0;
    reg dut_ok = 0;
    always @(posedge dut_pix) if (dut_ok) begin
        if (~raw_blank) blank_lo = blank_lo + 1;
        else            ctl_hi   = ctl_hi + 1;
    end
    always @(posedge clk_pix) if (dut_ok && active) sent_active = sent_active + 1;

    // ---- probe the decoder's own view (this is what hardware cannot show) ----
    integer n_inv0=0, n_dv0=0, n_cv0=0, n_call=0, n_gb=0, n_pfx=0, n_vdp=0, n_dvid=0;
    always @(posedge dut_pix) if (dut_ok) begin
        if (dut.ch0_invalid_symbol) n_inv0 = n_inv0 + 1;
        if (dut.ch0_data_valid)     n_dv0  = n_dv0  + 1;
        if (dut.ch0_ctl_valid)      n_cv0  = n_cv0  + 1;
        if (dut.ch0_ctl_valid & dut.ch1_ctl_valid & dut.ch2_ctl_valid) n_call = n_call + 1;
        if (dut.ch0_guardband_valid) n_gb  = n_gb  + 1;
        if (dut.vdp_prefix_seen)    n_pfx  = n_pfx  + 1;
        if (dut.in_vdp)             n_vdp  = n_vdp  + 1;
        if (dut.dvid_mode)          n_dvid = n_dvid + 1;
    end

    // ---- validate the STIMULUS before trusting any DUT conclusion ------------
    integer n_edges = 0, n_badctl = 0, n_blank = 0;
    reg ser0_d = 0;
    always @(posedge clk_bit) begin
        ser0_d <= ser0;
        if (dut_ok && ser0 !== ser0_d) n_edges = n_edges + 1;
    end
    // during blanking the encoder must emit one of the four DVI control codes
    always @(posedge clk_pix) if (dut_ok && ~active) begin
        n_blank = n_blank + 1;
        if (!(s0 == 10'b1101010100 || s0 == 10'b0010101011 ||
              s0 == 10'b0101010100 || s0 == 10'b1010101011)) n_badctl = n_badctl + 1;
    end

    initial begin
        $dumpfile("tb_hdmi_loopback.vcd");
        $dumpvars(0, tb_hdmi_loopback);
        wait (pll_locked && symbol_sync && idelay_rdy);
        $display("locked at %0t", $time);
        // Give the aligner room to sweep: 32 taps x 1023-cycle holdoff ~ 1 ms.
        // A full alignment search is 32 IDELAY taps x up to 10 bitslips, and each
        // adjustment costs a 1023-cycle holdoff (~32 us at 31.5 MHz) -- so give it
        // ~15 ms. This is what the corrected x"1000000" increment buys; with the
        // original x"000100" a single tap took ~31 ms and the search was hopeless.
        repeat (150) #100_000_000;          // 15 ms of convergence
        dut_ok = 1;
        $display("after convergence: sym=%b pll=%b", symbol_sync, pll_locked);
        repeat (10) #100_000_000;           // measure over the next 1 ms
        $display("--------------------------------------------------------");
        $display("SENT   active pixels : %0d", sent_active);
        $display("RECOVD active pixels : %0d   (raw_blank low)", blank_lo);
        $display("RECOVD control px    : %0d", ctl_hi);
        $display("pre_diag=%08x  in_vdp rise=%0d fall=%0d  width=%0d",
                 pre_diag, pre_diag[11:0], pre_diag[27:16],
                 pre_diag[27:16]-pre_diag[11:0]);
        $display("vdp_diag=%08x  ctl_diag=%08x  gb_diag=%08x",
                 vdp_diag, ctl_diag, gb_diag);
        $display("-- STIMULUS self-check --");
        $display("  ser0 edges          : %0d", n_edges);
        $display("  blank pixels        : %0d", n_blank);
        $display("  s0 NOT a ctl code   : %0d   (should be ~2 per line: guard band)", n_badctl);
        $display("-- what the deserialiser actually produced --");
        $display("  ch0 symbol_i now   : %010b   (sent s0 = %010b)",
                 dut.ch0.symbol_i, s0);
        $display("  ch0 delay_count    : %0d   bitslip=%b",
                 dut.ch0.delay_count, dut.ch0.bitslip);
        $display("-- decoder internals over the same window --");
        $display("  ch0 invalid_symbol : %0d", n_inv0);
        $display("  ch0 data_valid     : %0d", n_dv0);
        $display("  ch0 ctl_valid      : %0d", n_cv0);
        $display("  all-3 ctl_valid    : %0d", n_call);
        $display("  ch0 guardband_valid: %0d", n_gb);
        $display("  vdp_prefix_seen    : %0d", n_pfx);
        $display("  in_vdp             : %0d", n_vdp);
        $display("  dvid_mode          : %0d", n_dvid);
        $finish;
    end
endmodule
