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
//   FIX: also require `data_valid` (hdmi_input symbol_sync = real TMDS symbols are
//   decoding), debounced into the clk10 domain. sel = (clock present) AND (valid
//   decode). sel drops promptly when decode is lost; it asserts only after the
//   decode has been stable (~6.5 ms) -- so the ghost can no longer select passthrough.
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

    // data_valid: 2-FF sync into clk10, then debounce (must hold high to count as valid)
    reg dv0 = 1'b0, dv1 = 1'b0, dv_db = 1'b0;
    reg [15:0] dvcnt = 16'd0;

    reg rec; reg flag = 1'b0;
    always@(posedge clk10) begin
        // ---- data_valid sync + debounce (fast drop, ~6.5ms rise) ----
        dv0 <= data_valid; dv1 <= dv0;
        if (dv1) begin
            if (dvcnt == 16'hFFFF) dv_db <= 1'b1; else dvcnt <= dvcnt + 16'd1;
        end else begin
            dvcnt <= 16'd0; dv_db <= 1'b0;
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
        if (~dv_db) sel <= 1'b0;              // no valid decode -> force offline now
    end

    BUFGMUX mux    (.O(oclk),  .I0(clk125), .I1(hdmi_clk),  .S(sel));
    BUFGMUX mux_x1 (.O(oclk1), .I0(clk125), .I1(hdmi_clk1), .S(sel));
    BUFGMUX mux_x5 (.O(oclk5), .I0(clk625), .I1(hdmi_clk5), .S(sel));
endmodule
