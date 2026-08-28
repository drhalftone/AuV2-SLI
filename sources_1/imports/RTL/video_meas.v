// video_meas.v -- measure the INCOMING (pass-through) HDMI timing.
//
// WHY THIS EXISTS. Nothing in the design measured the signal the PC is actually
// sending. Three things looked like they did and none of them do:
//
//   * regs 0x22..0x25 (HACT/VACT) come from mode_timing_rom -- the OFFLINE mode
//     mode_select chose out of the display's EDID. With a PC sending 1280x720
//     those registers read 1280x800, so reading them as "the input" is not
//     merely unhelpful, it is wrong by a whole mode.
//   * pattern_gen measures the active region, but only to size its fringe
//     period; the counts never leave the module.
//   * regs 0x4A..0x4C measure a vsync PERIOD, but Au2_SLI.vhd wires that to
//     out_vsync -- the outgoing side.
//
// So this measures the input, on the input's own clock, and publishes it.
//
// TWO CLOCKS, DELIBERATELY. Active pixels and lines are counted on pixel_clk,
// because that is the only clock the incoming timing is coherent with. The
// frame PERIOD is counted on clk100 instead, because pixel_clk's frequency is
// whatever the source happens to be sending -- counting pixel_clk edges would
// give a period in units of an unknown clock and could not be turned into Hz.
// clk100 is 100 MHz by construction, so the period lands in 10 ns units and the
// host divides: rate = 1e8 / period.
//
// hact/vact cross to clk100 through a TOGGLE HANDSHAKE, not a plain 2FF on the
// bus. A 24-bit value 2FF-synced bit by bit can tear across a frame boundary
// and produce a resolution that never existed -- 1280x1080 out of a 1280x720
// and a 1920x1080. The snapshot is written once per frame and held for the
// whole of the next one (~16 ms at 60 Hz), so by the time the toggle has
// crossed two flops the data behind it has been stable for a million cycles.
//
// VALIDITY IS REFUSAL, NOT A DEFAULT. If the link is not decoding, or no vsync
// has arrived within the counter's 168 ms range, meas_ok reads 0 and the fields
// read 0 rather than holding the last good value. A stale resolution that looks
// current is worse than no answer -- this project has already shipped one bus
// that measured perfectly while corrupting its data.
module video_meas (
    input  wire        pixel_clk,     // recovered pixel clock (source's own rate)
    input  wire        in_hsync,      // recovered timing, pixel_clk domain
    input  wire        in_vsync,
    input  wire        in_blank,      // 1 = blanking, 0 = active pixel
    input  wire        clk100,        // 100 MHz, the only known-rate clock here
    input  wire        vid_valid,     // symbol_sync & pll_locked (async)
    output wire [55:0] meas,
    output wire [17:0] pix_khz      // recovered pixel clock, kHz
);
    // ---------------------------------------------------------------------
    // pixel_clk domain: active pixels per line, active lines per frame.
    // Mirrors the counting pattern_gen already proves in service every frame.
    // ---------------------------------------------------------------------
    reg        hs_d = 1'b0, vs_d = 1'b0;
    reg [11:0] ha_cnt = 12'd0, va_cnt = 12'd0;
    reg [11:0] hact = 12'd0;
    reg        line_active = 1'b0;
    reg [23:0] snap = 24'd0;           // {vact, hact} held for a whole frame
    reg        snap_tog = 1'b0;

    // ---------------------------------------------------------------------
    // VSYNC RECONSTRUCTION.  (hsync needs none -- see the note at the end.)
    //
    // raw_vsync is FORCED TO 0 during the video data period, so it carries the
    // source's vsync level only during BLANKING. For a positive-polarity source
    // that is harmless -- vsync idles low anyway, so the gated signal still rises
    // once per frame. For a NEGATIVE-polarity source (1024x768@60 is -hsync
    // -vsync) the idle level is HIGH, so the gated signal rises at the start of
    // EVERY horizontal blanking: once per LINE. video_meas then reported the LINE
    // rate as the frame rate -- 48379 Hz against a 48.363 kHz line rate -- and
    // v_active read 0 because va_cnt was cleared every line.
    //
    // INVERTING IT DOES NOT HELP. That only moves the once-per-line rise onto the
    // active-video edge; the first attempt at this fix did exactly that and
    // changed nothing. The signal has to be sampled where it is MEANINGFUL (during
    // blanking) and HELD through active video. Polarity then follows from the duty
    // of the held signal, which separates enormously: at 1024x768@60 an idle-low
    // vsync is high ~2.7% of the frame and an idle-high one ~97.3%, so a simple
    // half-window threshold decides it with margin to spare. Two consecutive
    // agreeing windows must vote before the polarity flips, so one anomalous
    // window cannot invert the meter.
    reg vs_held = 1'b0;
    always @(posedge pixel_clk)
        if (in_blank) vs_held <= in_vsync;      // hold through active video

    localparam POLW = 21;                       // 2^21 px ~ 28 ms at 74.25 MHz
    reg [POLW-1:0] pol_cnt = {POLW{1'b0}};
    reg [POLW:0]   vs_hi   = {(POLW+1){1'b0}};
    reg            vs_pol  = 1'b0;              // 1 = source idles high (active low)
    reg            vs_vote = 1'b0;

    always @(posedge pixel_clk) begin
        pol_cnt <= pol_cnt + 1'b1;
        if (vs_held) vs_hi <= vs_hi + 1'b1;
        if (pol_cnt == {POLW{1'b1}}) begin      // window closed
            vs_vote <= vs_hi[POLW-1];                       // >= half the window
            if (vs_hi[POLW-1] == vs_vote) vs_pol <= vs_hi[POLW-1];
            vs_hi <= {(POLW+1){1'b0}};
        end
    end

    // HSYNC IS USED RAW, DELIBERATELY. It is only needed to delimit lines, and
    // BOTH polarities give exactly one rise per line through the same VDP gating:
    // idle-low rises at the sync pulse, idle-high at the start of blanking. Either
    // way it lands after that line's active pixels, so h_active is correct -- 1024,
    // 800 and 1280 all measured right at their modes, including the -hsync one.
    // Reconstructing it would add risk for no gain.
    wire hsync_c = in_hsync;
    wire vsync_c = vs_held ^ vs_pol;

    wire hs_rise = hsync_c & ~hs_d;
    wire vs_rise = vsync_c & ~vs_d;

    always @(posedge pixel_clk) begin
        hs_d <= hsync_c;
        vs_d <= vsync_c;

        if (~in_blank) begin
            if (ha_cnt != 12'hFFF) ha_cnt <= ha_cnt + 1'b1;
            line_active <= 1'b1;
        end

        if (hs_rise) begin
            if (ha_cnt != 12'd0) hact <= ha_cnt;      // ignore lines with no active pixels
            ha_cnt <= 12'd0;
            if (line_active && va_cnt != 12'hFFF) va_cnt <= va_cnt + 1'b1;
            line_active <= 1'b0;
        end

        if (vs_rise) begin
            // Snapshot the frame that just finished: va_cnt is its line count and
            // hact the last line's width. Both are then stable until the next
            // vs_rise, which is what makes the handshake below safe.
            snap     <= {va_cnt, hact};
            snap_tog <= ~snap_tog;
            va_cnt   <= 12'd0;
        end
    end

    // ---------------------------------------------------------------------
    // clk100 domain: frame period, and the snapshot handshake.
    // ---------------------------------------------------------------------
    reg  [2:0] tog_s = 3'd0;
    reg [23:0] snap_c = 24'd0;
    reg  [2:0] vsq = 3'd0;
    reg  [1:0] vvq = 2'd0;
    reg [23:0] per_cnt = 24'd0, per_last = 24'd0;
    reg        armed = 1'b0;

    wire vs100_rise = vsq[1] & ~vsq[2];
    wire tog_edge   = tog_s[2] ^ tog_s[1];
    wire stalled    = (per_cnt == 24'hFFFFFF);   // ~168 ms with no vsync

    always @(posedge clk100) begin
        tog_s <= {tog_s[1:0], snap_tog};
        vsq   <= {vsq[1:0], vsync_c};   // normalised, not the raw pin
        vvq   <= {vvq[0], vid_valid};

        if (tog_edge)
            snap_c <= snap;

        if (vs100_rise) begin
            per_cnt <= 24'd0;
            // The first edge after arming measures from a reset, not from a real
            // frame boundary, so it is discarded rather than published as a
            // spuriously short period -- same rule usb_link uses for out_vsync.
            if (armed)
                per_last <= per_cnt;
            armed <= 1'b1;
        end else if (!stalled) begin
            per_cnt <= per_cnt + 24'd1;
        end else begin
            // No vsync for 168 ms: the source is gone. Drop the answer.
            per_last <= 24'd0;
            armed    <= 1'b0;
        end
    end

    // ---------------------------------------------------------------------
    // RECOVERED PIXEL CLOCK FREQUENCY, in kHz.
    //
    // This is the number that separates the two candidate faults, and nothing
    // else in the design reports it. Pass-through on the Pt works at exactly
    // one source mode (1280x800, 71.1 MHz) and fails at every other, while
    // symbol_sync and pll_locked both read 1 throughout. Two explanations fit:
    //   (a) the recovery MMCM is not TRACKING -- pixel_clk sits near 71 MHz
    //       whatever the source sends, so only the mode that happens to match
    //       survives; or
    //   (b) pixel_clk tracks correctly and the sync extraction downstream is
    //       what breaks.
    // Measuring the clock tells them apart in one read. Guessing between them
    // costs a rebuild either way, so measure.
    //
    // Counted as pixel_clk edges over a known clk100 gate, because clk100 is
    // the only clock here whose frequency is known by construction. A 100,000
    // cycle gate is 1.000 ms exactly, so the pixel-edge count IS the frequency
    // in kHz with no scaling.
    localparam [16:0] GATE = 17'd100_000;         // 1.000 ms at 100 MHz
    reg [16:0] gate_cnt = 17'd0;
    reg        gate_tog = 1'b0;
    always @(posedge clk100) begin
        if (gate_cnt == GATE - 1) begin
            gate_cnt <= 17'd0;
            gate_tog <= ~gate_tog;
        end else begin
            gate_cnt <= gate_cnt + 17'd1;
        end
    end

    // Gate toggle into the pixel domain, count pixel edges per gate window.
    reg [2:0]  gt_p = 3'd0;
    reg [17:0] pix_cnt = 18'd0, pix_freq = 18'd0;
    always @(posedge pixel_clk) begin
        gt_p <= {gt_p[1:0], gate_tog};
        if (gt_p[2] ^ gt_p[1]) begin              // one gate window elapsed
            pix_freq <= pix_cnt;
            pix_cnt  <= 18'd1;
        end else if (pix_cnt != 18'h3FFFF) begin
            pix_cnt <= pix_cnt + 18'd1;
        end
    end

    // ...and back to clk100. pix_freq is stable for a whole 1 ms window, so a
    // toggle handshake is unnecessary here: sample it on the same gate edge
    // that produced it, one window later.
    reg [17:0] pix_freq_c = 18'd0;
    always @(posedge clk100) begin
        if (gate_cnt == 17'd0)
            pix_freq_c <= pix_freq;
    end

    wire        vv_ok   = vvq[1];
    wire [11:0] hact_c  = snap_c[11:0];
    wire [11:0] vact_c  = snap_c[23:12];
    wire        meas_ok = vv_ok & (per_last != 24'd0)
                                & (hact_c  != 12'd0) & (vact_c != 12'd0);

    // The fields are published RAW and meas_ok is the flag, rather than the
    // fields being blanked when meas_ok is 0.
    //
    // The first cut did blank them, on the reasoning that a stale resolution
    // which looks live is worse than no answer. That reasoning is right for a
    // CONSUMER and wrong for this register block: on the very first bring-up
    // meas_ok came back 0 with vid_valid 1, and because all three terms had
    // been zeroed there was no way to tell WHICH of "no vsync period", "no
    // active pixels" or "no active lines" was the one failing. A diagnostic
    // register that cannot diagnose is not worth the rebuild it costs.
    //
    // The refusal is not lost, it moves: meas_ok is still authoritative and
    // host/rx_timing.py reads 0x67 first and declines to interpret the rest
    // when it is 0. Safety stays at the consumer; the fabric stays readable.
    // 0x67: [0]meas_ok [1]vid_valid [2]vs_pol  (1 = source vsync idles high)
    assign meas    = {5'b0, vs_pol, vv_ok, meas_ok, per_last, vact_c, hact_c};
    assign pix_khz = pix_freq_c;
endmodule
