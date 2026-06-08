`timescale 1ns/1ps
//==============================================================================
// video_timing_gen_rt - RUNTIME-configurable video timing for OFFLINE mode.
//
//   Identical behaviour to video_timing_gen.v, but the per-mode geometry is taken
//   from input ports instead of parameters, so a single instance can be retargeted
//   on the fly (Phase D2: fed by mode_timing_rom / mode_select while drp_clkgen13
//   retargets the matching pixel clock). The parameter version is kept untouched
//   for Phase B / D1.
//
//   Outputs match hdmi_input's raw_* semantics:
//     blank = 1 during blanking; hsync/vsync at the supplied polarity.
//   Counters advance only while `enable` is high (tie to mmcm_locked) and reset to
//   the top-left when it is low, so the first frame after each re-lock starts clean
//   AND any geometry change is absorbed while the MMCM is unlocked (enable low).
//
//   Widths: all geometry fields 12-bit; largest total in the curated table is
//   1280x720@60 (Htotal 1650, Vtotal 750) -> well inside 12 bits (4095).
//==============================================================================
module video_timing_gen_rt (
    input  wire        pixel_clk,
    input  wire        enable,        // run while high (e.g. mmcm_locked); reset when low
    // runtime geometry (e.g. from mode_timing_rom, all in pixels/lines)
    input  wire [11:0] h_active,
    input  wire [11:0] h_fp,
    input  wire [11:0] h_sync,
    input  wire [11:0] h_bp,
    input  wire [11:0] v_active,
    input  wire [11:0] v_fp,
    input  wire [11:0] v_sync,
    input  wire [11:0] v_bp,
    input  wire        hsync_pol,     // 1 = active-high sync
    input  wire        vsync_pol,
    output reg         hsync,
    output reg         vsync,
    output reg         blank,         // 1 during blanking
    output reg         active,        // = ~blank
    output reg [11:0]  hpos,          // 0..h_active-1 during active (else 0)
    output reg [11:0]  vpos           // 0..v_active-1 during active (else 0)
);
    wire [11:0] h_total  = h_active + h_fp + h_sync + h_bp;
    wire [11:0] v_total  = v_active + v_fp + v_sync + v_bp;
    wire [11:0] h_sync_s = h_active + h_fp;             // sync start
    wire [11:0] h_sync_e = h_active + h_fp + h_sync;    // sync end
    wire [11:0] v_sync_s = v_active + v_fp;
    wire [11:0] v_sync_e = v_active + v_fp + v_sync;

    reg [11:0] hcount = 0, vcount = 0;
    wire hlast = (hcount == h_total - 12'd1);
    wire vlast = (vcount == v_total - 12'd1);

    always @(posedge pixel_clk) begin
        if (!enable) begin
            hcount <= 0; vcount <= 0;
        end else if (hlast) begin
            hcount <= 0;
            vcount <= vlast ? 12'd0 : (vcount + 12'd1);
        end else begin
            hcount <= hcount + 12'd1;
        end
    end

    // registered decodes (all lag hcount/vcount by one cycle -> mutually aligned)
    always @(posedge pixel_clk) begin
        blank  <= (hcount >= h_active) || (vcount >= v_active);
        active <= (hcount <  h_active) && (vcount <  v_active);
        hsync  <= (hcount >= h_sync_s && hcount < h_sync_e) ?  hsync_pol : ~hsync_pol;
        vsync  <= (vcount >= v_sync_s && vcount < v_sync_e) ?  vsync_pol : ~vsync_pol;
        hpos   <= (hcount < h_active) ? hcount : 12'd0;
        vpos   <= (vcount < v_active) ? vcount : 12'd0;
    end
endmodule
