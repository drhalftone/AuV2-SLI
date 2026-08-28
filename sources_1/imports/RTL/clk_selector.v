`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Engineer: Qishi Hu  (sel-gating added 2026-06-08)
// Module: clk_selector
// Description: Selects the output pixel/serializer clock between the local
//   oscillator-derived clocks (clk125/clk625 = offline) and the HDMI-RX recovered
//   clocks (hdmi_clk* = passthrough).
//
//   ORIGINAL bug: sel was driven purely by "is tmds_clk toggling?" (cnt[21] changes
//   over the window). With clk10=10MHz and a 2^24 window, ANY tmds_clk > ~1.25 MHz
//   trips it -- and a floating HDMI-RX IBUFDS self-oscillates (the "ghost clock"),
//   so sel latched high with no real source -> garbage output clock.
//
//   FIX 1 (2026-06-08): also require `data_valid`, debounced into the clk10 domain.
//   sel = (clock present) AND (valid decode). sel drops promptly when decode is lost;
//   it asserts only after the decode has been stable (~6.5 ms).
//
//   FIX 2 (2026-07-13): FIX 1 was NOT sufficient. It passed symbol_sync alone, and
//   the ghost clock fools symbol_sync too: with the RX cable unplugged the decoder
//   happily locks onto the self-oscillation, so symbol_sync = 1 while pll_locked = 0.
//   sel therefore still latched high with no source, the output blanked, decode then
//   collapsed, sel dropped, the pattern came back -- a ~1-2 s blank/show cycle on the
//   display. The caller now drives data_valid with symbol_sync AND pll_locked (see
//   hdmi_io.vhd): pll_locked is the honest "there is a real clock" signal -- the RX
//   MMCM cannot lock to the ghost. This is the same term the idle-LED animation gates
//   on, which is why the LED slider stayed rock-steady while sel hunted.
//////////////////////////////////////////////////////////////////////////////////
module clk_selector (
    input rx, tmds_clk,
    input hdmi_clk, hdmi_clk1, hdmi_clk5, vsync,
    input clk125, clk625, clk10,
    input data_valid,                 // hdmi_input symbol_sync (RX-domain, async here)
    output reg sel,
    output oclk, oclk1, oclk5
);
    reg [24:0] count = 0;   // detect/decide window
    reg [21:0] cnt   = 0;   // free-running counter on tmds_clk
    always@(posedge tmds_clk) cnt <= cnt + 1'b1;

    // data_valid: 2-FF sync into clk10, then debounce BOTH EDGES.
    //
    // FIX 3 (2026-08-28): THE FALL USED TO BE INSTANT, AND THAT WAS THE BUG.
    // dv_db dropped the moment data_valid went low, and `if (~dv_db) sel <= 0`
    // then flipped the output clock to the local oscillator immediately. A glitch
    // of a few MICROSECONDS was therefore enough to hand the display a completely
    // different timebase; the sink lost lock, blanked, and took a second or two to
    // re-acquire -- the "black screens on and off" reported at 800x600. Every
    // polled register read perfectly clean throughout, because the event is far
    // shorter than the host's ~12 reads/second could ever catch.
    //
    // Reacting instantly bought responsiveness to an unplug. The user does not
    // want that: plug/unplug is rare and several seconds of latency is fine "in
    // human terms". So the fall is now debounced like the rise -- data_valid must
    // be absent for FALL_HOLD (~0.5 s at 10 MHz) before sel is allowed to drop.
    // Brief glitches are ridden through and the display never sees a discontinuity;
    // a genuine unplug shows garbage for that half second before the offline
    // pattern appears, which is the trade that was explicitly asked for.
    //
    // This makes the receiver TOLERANT of the glitch rather than removing it. At
    // 800x600@60 the underlying cause is the recovery MMCM running at 40 MHz x 15
    // = 600 MHz VCO, exactly its rated minimum; fixing that needs DRP retuning.
    // evt_diag (regs 0x7D..0x80) counts pll_locked/symbol_sync drops separately
    // from sel drops, so the glitch rate stays visible even once it is absorbed.
    localparam [22:0] FALL_HOLD = 23'd5_000_000;   // ~0.5 s at 10 MHz
    reg dv0 = 1'b0, dv1 = 1'b0, dv_db = 1'b0;
    reg [15:0] dvcnt = 16'd0;
    reg [22:0] dvlo  = 23'd0;

    reg rec; reg flag = 1'b0;
    always@(posedge clk10) begin
        // ---- data_valid sync + debounce (fast drop, ~6.5ms rise) ----
        dv0 <= data_valid; dv1 <= dv0;
        if (dv1) begin
            dvlo <= 23'd0;                                  // signal back: restart fall timer
            if (dvcnt == 16'hFFFF) dv_db <= 1'b1; else dvcnt <= dvcnt + 16'd1;
        end else begin
            dvcnt <= 16'd0;                                 // restart the rise timer
            if (dvlo == FALL_HOLD) dv_db <= 1'b0;           // gone long enough: really gone
            else                   dvlo  <= dvlo + 23'd1;
        end

        // ---- clock-present detector (original) ----
        rec   <= cnt[21];
        count <= count + 1'b1;
        if (count == 0) flag <= 1'b0;
        else if (~count[24]) begin            // detection stage
            if (cnt[21] != rec) flag <= 1'b1;
        end else begin                        // decision stage
            sel <= flag & dv_db;              // require a clock AND a valid decode
        end
        if (~dv_db) sel <= 1'b0;              // decode gone for FALL_HOLD -> offline
    end

    // The output serializer's TMDS symbol is ENCODED on oclk (=pixel_clk) but PARALLEL-LOADED
    // on oclk1. In offline both mux to the same net (clk125), so that handoff has zero skew and
    // closes at 78.67 MHz. In passthrough oclk1 used to take hdmi_clk1 (CLKOUT1) while oclk took
    // hdmi_clk (CLKOUT0) -- two distinct clock nets with independent BUFG->BUFGMUX skew, which
    // ate the OSERDES parallel-load setup margin at 78.67 MHz (1024x768@75) and blacked the
    // output, while 65 MHz (60 Hz) survived. Drive oclk1's passthrough input from hdmi_clk too,
    // so pixel-clock and word-clock are ONE net (mirrors the working offline path). hdmi_clk1
    // (CLKOUT1) is unaffected -- the RX deserialiser still uses it internally in hdmi_input.
    BUFGMUX mux    (.O(oclk),  .I0(clk125), .I1(hdmi_clk),  .S(sel));
    BUFGMUX mux_x1 (.O(oclk1), .I0(clk125), .I1(hdmi_clk),  .S(sel));
    BUFGMUX mux_x5 (.O(oclk5), .I0(clk625), .I1(hdmi_clk5), .S(sel));
endmodule
