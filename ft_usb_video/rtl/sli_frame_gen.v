`timescale 1ns/1ps
//==============================================================================
// sli_frame_gen.v -- self-clocked 1280x1024 structured-light frame source.
//
// Generates the SAME phase-shifting cosine fringes the HDMI path emits
// (pattern_gen.v), but at a FIXED PYTHON-1300 raster (1280 x 1024) and driven by
// its OWN counters instead of recovered video timing -- because over USB there is
// no incoming video to pace against. Purpose: a realistic, moving, byte-exact
// video stream to push through the FT601 and measure sustained frames/second.
//
// FORMAT (format code 1): 8-bit monochrome, FOUR pixels packed per 32-bit word,
// pixel 0 (leftmost) in the LOW byte -> the FT601's little-endian byte stream
// comes out of the PC as plain row-major grayscale. One word carries 4 columns,
// so the generator can hand the master a fresh word EVERY ft_clk and thus
// saturate the link (up to 4 B * 100 MHz = 400 MB/s theoretical).
//
// FRAMING: every frame begins with an 8-word header the host locks onto:
//   [0] MAGIC  0x30494C53  ("SLI0", little-endian)
//   [1] frame_index               (monotonic; host uses it to detect drops)
//   [2] {height[15:0], width[15:0]} = 0x0400_0500
//   [3] {26'b0, frq[1:0], 1'b0, frm[2:0]}   (current freq/phase, for display)
//   [4] bytes_per_frame (pixels only)       = 1280*1024 = 0x0014_0000
//   [5] pixel words per frame               = 320*1024  = 0x0005_0000
//   [6] format code                         = 1
//   [7] ~MAGIC 0xCFB6B3AC        (end-of-header sentinel / secondary check)
//
// STREAM CONTRACT: FWFT source. `word` is always valid; assert `adv` to consume
// it and present the next. `adv` is driven by the FT601 master, so generation is
// throttled exactly by USB back-pressure -- FPS then == (bytes actually sent)/s
// divided by the 1,310,752-byte frame, i.e. a pure link-rate measurement.
//
// Single clock domain (ft_clk). The cosine is a distributed (LUT) ROM so all four
// per-word samples read combinationally in one cycle.
//==============================================================================
module sli_frame_gen #(
    parameter integer WIDTH        = 1280,
    parameter integer HEIGHT       = 1024,
    parameter integer PHASE_FRAMES = 15,     // frames held per phase step (visual pacing)
    parameter [1:0]   FRQ_INIT     = 2'd1    // 0=coarse(x1) 1=mid(x6) 2=fine(x36)
)(
    input  wire        clk,
    input  wire        rst,                  // synchronous, active-high
    input  wire        adv,                  // consume `word`, advance to next
    output wire [31:0] word,                 // current 32-bit stream word (FWFT)
    output wire        valid,                // always 1 (we always have a word)
    output wire [31:0] frame_index           // for LEDs / debug
);
    localparam integer PPW   = 4;                    // pixels per word
    localparam integer WPL   = WIDTH / PPW;          // 320 words per line
    localparam integer HDR_N = 8;                    // header words per frame

    localparam integer COS_AW = 12;                  // 4096-entry master cosine
    localparam integer COS_N  = (1 << COS_AW);
    localparam integer FRAC   = 12;                  // phase-accumulator fraction
    localparam integer ACC_W  = COS_AW + FRAC;       // 24

    localparam [31:0]  MAGIC = 32'h30494C53;         // "SLI0" (LE)

    // --- per-mode phase increments: INC1 = 2^24 / P_lo, P_lo = 288*ceil(W/288) ---
    // W=1280 -> ceil(1280/288)=5 -> P_lo=1440. INC6=6*INC1, INC36=36*INC1 (exact
    // integer ratios -> one master cosine serves all three, no re-quantization).
    localparam integer P_LO  = 288 * (((WIDTH) + 287) / 288);
    localparam [ACC_W-1:0] INC1  = (1 << ACC_W) / P_LO;
    localparam [ACC_W-1:0] INC6  = 6  * INC1;
    localparam [ACC_W-1:0] INC36 = 36 * INC1;

    // -------------------------------------------------------------------------
    // Master cosine ROM: base[a] = round(255 * (0.5 + 0.5*cos(2*pi*a/4096))).
    // Distributed so the four taps below read combinationally in one cycle.
    // -------------------------------------------------------------------------
    (* rom_style = "distributed" *) reg [7:0] mcos [0:COS_N-1];
    integer ii; real th;
    initial begin
        for (ii = 0; ii < COS_N; ii = ii + 1) begin
            th = 6.28318530717958647692 * ii / (1.0*COS_N);
            mcos[ii] = $rtoi(255.0*(0.5 + 0.5*$cos(th)) + 0.5);
        end
    end

    // -------------------------------------------------------------------------
    // Raster / sequence state.
    // -------------------------------------------------------------------------
    reg               in_hdr = 1'b1;
    reg [2:0]         hdr_idx = 3'd0;
    reg [8:0]         wx = 9'd0;              // word within line, 0..WPL-1 (0..319)
    reg [10:0]        wy = 11'd0;             // line, 0..HEIGHT-1 (0..1023)
    reg [ACC_W-1:0]   acc0 = {ACC_W{1'b0}};   // spatial phase at this word's col 0
    reg [31:0]        frame_idx = 32'd0;
    reg [2:0]         frm = 3'd0;             // phase index 0..7
    reg [1:0]         frq = FRQ_INIT;         // freq index 0..2
    reg [15:0]        phase_hold = 16'd0;

    assign frame_index = frame_idx;
    assign valid = 1'b1;

    wire [ACC_W-1:0] inc_sel = (frq == 2'd0) ? INC1 :
                               (frq == 2'd1) ? INC6 : INC36;
    wire [ACC_W-1:0] step    = inc_sel << 2;           // 4 columns per word

    // per-frame temporal shift = frm*512 = exact 1/8 master period (no banding)
    wire [COS_AW-1:0] frm_shift = {frm, 9'b0};

    // four horizontally-adjacent column phases within this word
    wire [ACC_W-1:0] ph0 = acc0;
    wire [ACC_W-1:0] ph1 = acc0 +  inc_sel;
    wire [ACC_W-1:0] ph2 = acc0 + (inc_sel << 1);
    wire [ACC_W-1:0] ph3 = acc0 + (inc_sel << 1) + inc_sel;

    wire [COS_AW-1:0] a0 = ph0[ACC_W-1:FRAC] + frm_shift;
    wire [COS_AW-1:0] a1 = ph1[ACC_W-1:FRAC] + frm_shift;
    wire [COS_AW-1:0] a2 = ph2[ACC_W-1:FRAC] + frm_shift;
    wire [COS_AW-1:0] a3 = ph3[ACC_W-1:FRAC] + frm_shift;

    // pixel 0 (col 0) in the low byte -> row-major little-endian on the wire
    wire [31:0] pix_word = {mcos[a3], mcos[a2], mcos[a1], mcos[a0]};

    // -------------------------------------------------------------------------
    // Header words (combinational function of the current sequence state).
    // -------------------------------------------------------------------------
    reg [31:0] hdr_word;
    always @(*) begin
        case (hdr_idx)
            3'd0: hdr_word = MAGIC;
            3'd1: hdr_word = frame_idx;
            3'd2: hdr_word = {HEIGHT[15:0], WIDTH[15:0]};
            3'd3: hdr_word = {26'd0, frq, 1'b0, frm};
            3'd4: hdr_word = WIDTH * HEIGHT;                 // bytes/frame (BPP=1)
            3'd5: hdr_word = (WIDTH/PPW) * HEIGHT;           // pixel words/frame
            3'd6: hdr_word = 32'h0000_0001;                  // format 1
            default: hdr_word = ~MAGIC;                      // [7]
        endcase
    end

    assign word = in_hdr ? hdr_word : pix_word;

    // -------------------------------------------------------------------------
    // Advance the sequence one word per `adv`.
    // -------------------------------------------------------------------------
    always @(posedge clk) begin
        if (rst) begin
            in_hdr     <= 1'b1;
            hdr_idx    <= 3'd0;
            wx         <= 9'd0;
            wy         <= 11'd0;
            acc0       <= {ACC_W{1'b0}};
            frame_idx  <= 32'd0;
            frm        <= 3'd0;
            frq        <= FRQ_INIT;
            phase_hold <= 16'd0;
        end else if (adv) begin
            if (in_hdr) begin
                if (hdr_idx == HDR_N-1) begin
                    in_hdr <= 1'b0;          // header done -> first pixel word
                    hdr_idx <= 3'd0;
                    wx <= 9'd0; wy <= 11'd0;
                    acc0 <= {ACC_W{1'b0}};
                end else begin
                    hdr_idx <= hdr_idx + 3'd1;
                end
            end else begin
                if (wx == WPL-1) begin
                    wx   <= 9'd0;
                    acc0 <= {ACC_W{1'b0}};   // restart phase each line
                    if (wy == HEIGHT-1) begin
                        // end of frame: emit next header, bump index, walk sequence
                        wy        <= 11'd0;
                        in_hdr    <= 1'b1;
                        hdr_idx   <= 3'd0;
                        frame_idx <= frame_idx + 32'd1;
                        if (phase_hold == PHASE_FRAMES-1) begin
                            phase_hold <= 16'd0;
                            frm <= frm + 3'd1;
                            if (frm == 3'd7)
                                frq <= (frq == 2'd2) ? 2'd0 : (frq + 2'd1);
                        end else begin
                            phase_hold <= phase_hold + 16'd1;
                        end
                    end else begin
                        wy <= wy + 11'd1;
                    end
                end else begin
                    wx   <= wx + 9'd1;
                    acc0 <= acc0 + step;
                end
            end
        end
    end
endmodule
