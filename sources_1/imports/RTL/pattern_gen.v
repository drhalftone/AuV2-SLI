`timescale 1ns/1ps
//==============================================================================
// pattern_gen.v -- resolution-adaptive structured-light fringe generator.
//
// Substitutes 8-step phase-shifting sinusoidal fringes for the pass-through
// pixels, at three spatial frequencies in the exact integer ratio 1 : 6 : 36.
// A fourth frequency index (frq=3) selects a flat full-field FLASHING block
// (black/white by frm[0]) for texture/albedo capture -- used by the camera-paced
// cam_pace sequence (seq 24..27). See the flash_mode logic below.
//
// Period derivation (see EDID_Reader/README.md sec.13 + project discussion):
//   F  = active size in the fringe-varying direction
//          orient=0 ("vertical")   -> F = Vactive (rows),    pattern varies down rows
//          orient=1 ("horizontal") -> F = Hactive (columns), pattern varies across cols
//   b      = ceil(F / 288)                       (smallest b s.t. coarse covers field)
//   P_lo   = 288*b   (x1, coarsest, ~1 period over the field)
//   P_mid  = 48*b    (x6)
//   P_hi   = 8*b     (x36)
//   Every period is a multiple of 8 -> the 8-phase shift P/8 is an integer pixel
//   count.  Because 1:6:36 are exact integer ratios, ONE base cosine serves all
//   three frequencies; here that base is a fixed 4096-entry master cosine ROM,
//   sampled by a phase accumulator.
//
// No-banding (README sec.13.1): the 8 phase frames must be integer-pixel reindexes
// of ONE once-quantized array.  We satisfy this directly: the per-frame phase is
// added as frm*(4096/8) = frm*512 in master-address space -- an EXACT integer
// offset into the fixed master ROM.  The spatial phase accumulator is evaluated
// once per pixel (independent of frm), so all 8 frames read the same master
// entries shifted by an exact 512*frm, and the spatial rounding is identical
// across frames -> it cancels in the atan2 demodulation.  The DDS is used only for
// spatial sampling, never to synthesize the inter-frame shift, so README sec.13.1's
// prohibition on fractional phase shifts is respected.
//
// Per-mode the only runtime math is INC1 = 2^24 / P_lo (one sequential divide);
// INC6 = 6*INC1, INC36 = 36*INC1.  Until that constant is ready after a resolution
// change, the block passes the original pixels through (screen never blanks).
//
// Pixel-clock domain only.  Board exposes no DIP switches, so orient / channel
// enables are tie-offs and the phase/frequency indices free-run via an auto-cycler
// (PHASE_FRAMES frames per phase step; after 8 phases the frequency advances).
//==============================================================================
module pattern_gen #(
    parameter integer COS_AW       = 12,           // master cosine address width (4096 entries)
    parameter integer FRAC         = 12,           // phase-accumulator fractional bits
    parameter integer AUTO_CYCLE   = 0,            // 0=static (FRQ_INIT, phase 0); 1=walk phases/freqs
    parameter integer PHASE_FRAMES = 30,           // frames held per phase step (auto-cycle)
    parameter integer EXT_SEQ      = 0,            // 0=internal phase/freq; 1=take frm/frq from ext_*
    parameter [1:0]   FRQ_INIT     = 2'd1,         // static frequency: 0=lo(x1) 1=mid(x6) 2=hi(x36)
    parameter [2:0]   RGB_EN       = 3'b111,       // {red,green,blue} channels (compile-time default)
    parameter integer RGB_RUNTIME  = 0             // 1: take channel enables from the rgb_en input
)(
    input  wire        pixel_clk,
    input  wire        raw_blank,
    input  wire        raw_hsync,
    input  wire        raw_vsync,
    input  wire [7:0]  raw_red,                    // pass-through pixels (raw_ch2/1/0)
    input  wire [7:0]  raw_green,
    input  wire [7:0]  raw_blue,
    input  wire        orient,                     // 0=vertical(rows) 1=horizontal(cols)
    input  wire        enable,                     // 1=emit fringe on active pixels, 0=pass-through
    input  wire [2:0]  chan_en,                    // runtime {R,G,B} channel enables (iff RGB_RUNTIME)
    // EXT_SEQ=1: externally-sequenced phase/frequency (e.g. camera-paced cam_pace).
    // Ignored when EXT_SEQ=0 (the internal auto-cycler drives frm/frq).
    input  wire [2:0]  ext_frm,                    // phase index 0..7
    input  wire [1:0]  ext_frq,                    // spatial-freq index 0..2 (3 = flashing block)
    // radiometric transfer LUT seam (pattern_lut): raw cosine value out, linearized in.
    // Tie lut_dout = lut_din externally for no correction (pattern_lut powers up identity).
    output wire [7:0]  lut_din,                    // raw 8-bit cosine value (pattern only)
    input  wire [7:0]  lut_dout,                   // linearized value back from pattern_lut
    output reg  [7:0]  out_red,
    output reg  [7:0]  out_green,
    output reg  [7:0]  out_blue,
    output wire [15:0] dbg                         // {valid,orient,enable, frq[1:0], frm[2:0], b[7:0]}
);
    localparam integer ACC_W = COS_AW + FRAC;      // 24
    localparam integer COS_N = (1 << COS_AW);      // 4096
    localparam [ACC_W:0] DIVD = (1 << ACC_W);      // 2^24, dividend for INC1 = 2^24/P_lo

    // -------------------------------------------------------------------------
    // 1) Master cosine ROM: 4096 entries of one full period, 8-bit unsigned.
    //    base[a] = round(255 * (0.5 + 0.5*cos(2*pi*a/4096)))
    //    Built at elaboration (Vivado evaluates $cos for ROM init).
    // -------------------------------------------------------------------------
    (* rom_style = "block" *) reg [7:0] mcos [0:COS_N-1];
    integer ii;
    real    th;
    initial begin
        for (ii = 0; ii < COS_N; ii = ii + 1) begin
            th = 6.28318530717958647692 * ii / (1.0*COS_N);
            mcos[ii] = $rtoi(255.0*(0.5 + 0.5*$cos(th)) + 0.5);
        end
    end

    // -------------------------------------------------------------------------
    // 2) Recovered-timing measurement (mirrors hdmi_timing_uart.v).
    //    Hactive (active pixels/line), Vactive (active lines/frame).
    // -------------------------------------------------------------------------
    reg        hs_d = 0, vs_d = 0;
    reg [15:0] ha_cnt = 0, va_cnt = 0;
    reg [15:0] hact = 0, vact = 0;
    reg        line_active = 0;
    wire       hs_rise = raw_hsync & ~hs_d;
    wire       vs_rise = raw_vsync & ~vs_d;

    always @(posedge pixel_clk) begin
        hs_d <= raw_hsync; vs_d <= raw_vsync;
        if (~raw_blank) begin ha_cnt <= ha_cnt + 1'b1; line_active <= 1'b1; end
        if (hs_rise) begin
            if (ha_cnt != 0) hact <= ha_cnt;          // active lines only
            ha_cnt <= 0;
            if (line_active) va_cnt <= va_cnt + 1'b1;
            line_active <= 0;
        end
        if (vs_rise) begin
            vact <= va_cnt;
            va_cnt <= 0;
        end
    end

    // -------------------------------------------------------------------------
    // 3) Per-mode constant solver: F -> P_lo=288*ceil(F/288) -> INC1=2^24/P_lo.
    //    Retriggers when the selected field size changes (debounced 2 frames).
    // -------------------------------------------------------------------------
    wire [15:0] F_now = orient ? hact : vact;
    reg  [15:0] F_prev = 0, F_lock = 0;

    localparam [1:0] S_IDLE = 2'd0, S_PLO = 2'd1, S_DIV = 2'd2, S_FIN = 2'd3;
    reg [1:0]  state = S_IDLE;
    reg        valid = 0;
    reg [15:0] plo   = 0;          // P_lo
    reg [7:0]  bcnt  = 0;          // b
    reg [ACC_W:0] rem = 0;         // divider remainder
    reg [ACC_W:0] quo = 0;         // divider quotient -> INC1 (needs bit ACC_W)
    reg [5:0]  dbit = 0;
    reg [ACC_W-1:0] inc1 = 0, inc6 = 0, inc36 = 0;

    always @(posedge pixel_clk) begin
        if (vs_rise) begin
            F_prev <= F_now;
            // start a recompute on a new, stable, non-zero field size
            if (F_now == F_prev && F_now != 0 && F_now != F_lock && state == S_IDLE) begin
                F_lock <= F_now;
                valid  <= 1'b0;
                plo    <= 16'd288;
                bcnt   <= 8'd1;
                state  <= S_PLO;
            end
        end

        case (state)
            S_PLO: begin
                if (plo < F_lock) begin
                    plo  <= plo + 16'd288;
                    bcnt <= bcnt + 8'd1;
                end else begin
                    // init shift-subtract divide 2^24 / plo
                    rem   <= 0;
                    quo   <= 0;
                    dbit  <= ACC_W;            // 24 -> iterate bits 24..0 (25 steps)
                    state <= S_DIV;
                end
            end
            S_DIV: begin
                // rem = (rem<<1) | DIVD[dbit]; if rem>=plo subtract, set quotient bit
                if ((({rem[ACC_W-1:0], DIVD[dbit]}) ) >= {1'b0, plo}) begin
                    rem        <= {rem[ACC_W-1:0], DIVD[dbit]} - {1'b0, plo};
                    quo[dbit]  <= 1'b1;
                end else begin
                    rem        <= {rem[ACC_W-1:0], DIVD[dbit]};
                end
                if (dbit == 0) state <= S_FIN;
                else           dbit  <= dbit - 6'd1;
            end
            S_FIN: begin
                inc1  <= quo;
                inc6  <= (quo <<< 2) + (quo <<< 1);          // *6
                inc36 <= (quo <<< 5) + (quo <<< 2);          // *36
                valid <= 1'b1;
                state <= S_IDLE;
            end
            default: ; // S_IDLE
        endcase
    end

    // -------------------------------------------------------------------------
    // 4) Auto-cycler: walk phase 0..7, then advance frequency 0..2 (wrap).
    // -------------------------------------------------------------------------
    reg [15:0] frame_cnt = 0;
    reg [2:0]  frm = 0;            // phase index 0..7
    reg [1:0]  frq = FRQ_INIT;     // frequency index 0=lo 1=mid 2=hi
    always @(posedge pixel_clk) begin
        if (AUTO_CYCLE != 0 && vs_rise) begin
            if (frame_cnt >= (PHASE_FRAMES-1)) begin
                frame_cnt <= 0;
                frm <= frm + 3'd1;
                if (frm == 3'd7) frq <= (frq == 2'd2) ? 2'd0 : (frq + 2'd1);
            end else begin
                frame_cnt <= frame_cnt + 16'd1;
            end
        end
    end

    // Active phase/frequency: internal auto-cycler, or externally sequenced (EXT_SEQ).
    wire [2:0] frm_use = (EXT_SEQ != 0) ? ext_frm : frm;
    wire [1:0] frq_use = (EXT_SEQ != 0) ? ext_frq : frq;

    wire [ACC_W-1:0] inc_sel = (frq_use == 2'd0) ? inc1 :
                               (frq_use == 2'd1) ? inc6 : inc36;

    // frq_use==3 selects the FLASHING block (camera-paced cam_pace seq 24..27): a flat
    // full-field level instead of a fringe, for texture/albedo capture during calibration.
    // frm_use[0] alternates black/white (frm 0..3 -> 0x00,0xFF,0x00,0xFF), matching the
    // original pixel_pipe.v (fra[0]?0xFF:0x00). The flash frames BYPASS the radiometric
    // LUT (see output mux) so the projector emits true 0x00/0xFF -- the tone curve corrects
    // the fringe cosine, but albedo/texture wants the raw full-scale white. The spatial
    // accumulators/inc_sel are unused here (pat is overridden).
    wire       flash_mode = (frq_use == 2'd3);
    wire [7:0] flash_val  = frm_use[0] ? 8'hFF : 8'h00;

    // -------------------------------------------------------------------------
    // 5) Spatial phase accumulators.
    //    Horizontal (orient=1): advance every active pixel, reset each line.
    //    Vertical   (orient=0): advance once per active line, reset each frame.
    // -------------------------------------------------------------------------
    reg [ACC_W-1:0] colacc = 0, rowacc = 0;
    always @(posedge pixel_clk) begin
        // column accumulator (horizontal fringes)
        if (raw_blank) colacc <= 0;
        else           colacc <= colacc + inc_sel;
        // row accumulator (vertical fringes): step at each active line boundary
        if (vs_rise)            rowacc <= 0;
        else if (hs_rise && line_active) rowacc <= rowacc + inc_sel;
    end

    wire [ACC_W-1:0] sel_acc  = orient ? colacc : rowacc;
    wire [COS_AW-1:0] int_phase = sel_acc[ACC_W-1 : FRAC];          // top 12 bits
    // Per-frame phase step = frm*512 (exact 1/8 of the master period -> no banding).
    // Its SIGN sets the apparent scroll direction. Row-coordinate fringes (orient=0) use a
    // NEGATIVE step so the pattern moves DOWN the screen (was +, which scrolled upward);
    // column fringes (orient=1) keep the original (+) direction. Subtraction wraps mod 4096.
    wire [COS_AW-1:0] frm_shift = {frm_use, 9'd0};
    wire [COS_AW-1:0] maddr     = orient ? (int_phase + frm_shift)
                                         : (int_phase - frm_shift);

    // -------------------------------------------------------------------------
    // 6) Master ROM read (1-cycle) + aligned output mux.
    // -------------------------------------------------------------------------
    reg [7:0] pat = 0;
    reg       flash_d = 0;          // flash_mode aligned to `pat` (1 stage), for the output mux
    reg       blank_d = 0;
    reg [7:0] r_d = 0, g_d = 0, b_d = 0;
    always @(posedge pixel_clk) begin
        pat     <= flash_mode ? flash_val : mcos[maddr];
        flash_d <= flash_mode;
        blank_d <= raw_blank;
        r_d     <= raw_red;
        g_d     <= raw_green;
        b_d     <= raw_blue;
    end

    // raw cosine value -> external transfer LUT (combinational) -> linearized value
    assign lut_din = pat;

    // Fringe pixels use the LUT-corrected value; FLASH pixels bypass it and emit raw pat
    // (= flash_val, true 0x00/0xFF). pat and lut_dout share a pipeline stage, so flash_d
    // selects cleanly between them.
    wire [7:0] pat_out = flash_d ? pat : lut_dout;

    // Channel enables: compile-time RGB_EN, or the runtime rgb_en input when RGB_RUNTIME.
    wire [2:0] rgb_sel = (RGB_RUNTIME != 0) ? chan_en : RGB_EN;
    wire show = enable & valid & ~blank_d;
    always @(posedge pixel_clk) begin
        out_red   <= show ? (rgb_sel[2] ? pat_out : 8'h00) : r_d;
        out_green <= show ? (rgb_sel[1] ? pat_out : 8'h00) : g_d;
        out_blue  <= show ? (rgb_sel[0] ? pat_out : 8'h00) : b_d;
    end

    assign dbg = {valid, orient, enable, frq_use, frm_use, bcnt};
endmodule
