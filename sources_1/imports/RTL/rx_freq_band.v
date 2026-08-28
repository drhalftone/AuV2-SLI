`timescale 1ns/1ps
//==============================================================================
// rx_freq_band - pick the recovery-MMCM band from the MEASURED input frequency.
//
// WHY MEASURE tmds_clk AND NOT pixel_clk. video_meas already reports a recovered
// pixel-clock frequency, but it counts the MMCM's OUTPUT. Today that happens to
// equal the input because M = O0 = 15, so the MMCM reproduces its own input --
// the moment the multiplier starts varying, that stops being true and using it
// to choose the multiplier would be a feedback loop. This counts the MMCM's
// INPUT, which is the source's pixel rate whatever the MMCM is doing.
//
// WHY A HYSTERESIS BAND AND A SETTLE COUNT. Retuning the MMCM drops PLL lock and
// blanks the output for a moment, so a detector that chattered between two bands
// would blank the display continuously -- far worse than the fault being fixed.
// A new band therefore has to be measured CONSECUTIVELY for SETTLE windows before
// it is acted on, and the band edges are placed away from any real pixel clock:
//
//     edges      :      28    36    45       57       68        MHz
//     real clocks: 25.175  31.5  40.0  49.5 50.0  65.0  71.1  74.25  78.75
//
// EVERY EDGE MUST SIT IN A GAP, and getting this wrong is not subtle. The first
// cut put an edge at exactly 65 MHz -- which IS 1024x768@60's pixel clock -- while
// the comment claimed the edges avoided real clocks. Measurement noise around
// 65000 then flipped the detector between two bands, the MMCM retuned
// continuously and NEVER LOCKED: pll_locked stayed 0 and the recovered clock
// wandered around 36 MHz. That mode had worked perfectly before the adaptive
// clock existed. The edge moved to 68 MHz, which sits in the 65.0 -> 71.1 gap.
// Smallest remaining margin is 2.8 MHz (25.175 -> 28); every other edge has 3+.
//
// BAND 5 IS THE RESET STATE AND THE FALLBACK. It is the configuration the MMCM is
// built with and the one every currently-working mode uses, so if measurement is
// unavailable or nonsense, the design behaves exactly as it did before.
//
// BANDS ARE BIASED HIGH (VCO ~1200-1300), not merely "in range". An earlier cut
// aimed for 1000-1300 and 800x600@60 landed on VCO 1000: the clock locked, the
// raster measured perfect end to end, and the display still blinked -- perfect
// picture whenever it held, i.e. marginal LOCK. The offline path had already
// shown this mode needs 1200 (600 failed, 1200 worked), and a RECOVERED clock
// carries the source's jitter on top, so it has less margin than the offline one,
// not more.
//==============================================================================
module rx_freq_band #(
    parameter integer CLK100_HZ = 100_000_000,
    parameter integer SETTLE    = 8            // consecutive agreeing windows
)(
    input  wire       clk100,
    input  wire       tmds_clk,      // the recovery MMCM's INPUT (async here)
    input  wire       valid,         // pll_locked & symbol_sync: measurement is meaningful
    output reg  [3:0] band = 4'd5,   // 5 = the original proven M=15 config
    output reg        band_changed = 1'b0,  // 1-cycle strobe on clk100
    output wire [17:0] khz           // measured input clock, kHz (diagnostics)
);
    // ---- 1.000 ms gate on clk100: the edge count IS the frequency in kHz -------
    localparam integer GATE = CLK100_HZ / 1000;
    reg [16:0] gate_cnt = 17'd0;
    reg        gate_tog = 1'b0;
    always @(posedge clk100) begin
        if (gate_cnt == GATE[16:0] - 1) begin gate_cnt <= 17'd0; gate_tog <= ~gate_tog; end
        else                                  gate_cnt <= gate_cnt + 17'd1;
    end

    // ---- count tmds_clk edges per gate window ---------------------------------
    reg [2:0]  gt_t = 3'd0;
    reg [17:0] cnt_t = 18'd0, freq_t = 18'd0;
    always @(posedge tmds_clk) begin
        gt_t <= {gt_t[1:0], gate_tog};
        if (gt_t[2] ^ gt_t[1]) begin freq_t <= cnt_t; cnt_t <= 18'd1; end
        else if (cnt_t != 18'h3FFFF)  cnt_t <= cnt_t + 18'd1;
    end

    // freq_t is stable for a whole window, so sampling it on the gate edge one
    // window later needs no handshake.
    reg [17:0] freq_c = 18'd0;
    always @(posedge clk100) if (gate_cnt == 17'd0) freq_c <= freq_t;
    assign khz = freq_c;

    // ---- band decision, with guards around the on-edge clocks -----------------
    reg [3:0] want;
    always @* begin
        // Edges sit AWAY from every real pixel clock so no mode lands on a boundary:
        //   real clocks: 25.175  31.5  40.0  49.5  50.0  65.0  71.1  74.25  78.75
        //   edges      :      28    36    45          57    65
        if      (freq_c <  18'd20_000) want = 4'd5;   // nothing / nonsense -> proven config
        else if (freq_c <  18'd28_000) want = 4'd0;   // 20-28   (25.175 -> VCO 1259)
        else if (freq_c <  18'd36_000) want = 4'd1;   // 28-36   (31.5   -> VCO 1260)
        else if (freq_c <  18'd45_000) want = 4'd2;   // 36-45   (40.0   -> VCO 1200)
        else if (freq_c <  18'd57_000) want = 4'd3;   // 45-57   (49.5/50 -> 1237/1250)
        else if (freq_c <  18'd68_000) want = 4'd4;   // 57-68   (65.0    -> VCO 1300)
        else                           want = 4'd5;   // 68+     (71.1, 74.25, 78.75)
    end

    // ---- settle before acting -------------------------------------------------
    reg [3:0] cand = 4'd5;
    reg [7:0] agree = 8'd0;
    reg       gate_q = 1'b0;
    wire      window_done = (gate_cnt == 17'd0);
    always @(posedge clk100) begin
        band_changed <= 1'b0;
        gate_q <= gate_tog;
        if (window_done) begin
            if (!valid) begin
                agree <= 8'd0;            // no trustworthy input: hold, do not retune
            end else if (want == cand) begin
                if (agree != 8'hFF) agree <= agree + 8'd1;
            end else begin
                cand  <= want;
                agree <= 8'd0;
            end
            if (valid && (want == cand) && (agree == SETTLE[7:0]) && (cand != band)) begin
                band         <= cand;
                band_changed <= 1'b1;
            end
        end
    end
endmodule
