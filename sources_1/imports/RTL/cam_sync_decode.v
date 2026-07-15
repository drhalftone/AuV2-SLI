`timescale 1ns / 1ps
//=============================================================================
// cam_sync_decode.v - PYTHON 1300 sync-channel decode + 4-lane de-interleave.
//
// Turns aligned 10-bit words (from cam_lvds_rx, after cam_align has locked) into a raster
// pixel stream. Two jobs:
//
//  1. SYNC DECODE. The 5th channel carries framing codes (CAMERA_SENSOR_PROTOCOL.md §5):
//        FS 0x2AA  FE 0x32A  LS 0x0AA  LE 0x12A   (frame/line start/end)
//        BL 0x015  IMG 0x035  CRC 0x059  TR 0x3A6 (data-class this cycle)
//     Each frame-sync code is followed by a 3-bit window-ID word -- consumed, not decoded.
//
//  2. DE-INTERLEAVE (datasheet Figure 36). Pixels arrive in 8-pixel kernels, TWO IMG words
//     per kernel: word A carries kernel positions {0,2,4,6} on lanes {d0,d1,d2,d3}, word B
//     carries {1,3,5,7}. EVEN kernels are spatially ascending, ODD kernels descending.
//
// THROUGHPUT -- the thing an earlier 1-pixel-per-clock version got wrong: each IMG word is
// 4 pixels (one per data lane), so the sensor delivers 4 px/wordclk. A raster stream at
// 1 px/clk cannot keep up and the kernel buffer overruns. So the output is a WHOLE KERNEL
// at once: on word B, all 8 spatial pixels of the kernel are emitted in parallel with their
// base column. Kernels complete every 2 wordclks => 4 px/clk average, matching the source.
//
// kpix[s] is the pixel at column kbase+s (already un-reversed for odd kernels), so the
// consumer writes kpix[s] straight to column kbase+s. That inverts
// python1300_lvds_model.kernel_words() exactly and matches osrf/ovc's UNSWAP_KERNELS. The
// exact within-kernel channel pairing is the one silicon-only unknown (task #12) and lives
// ONLY in the position assignment below.
//=============================================================================
module cam_sync_decode (
    input  wire        wordclk,
    input  wire        rst,
    input  wire        aligned,          // from cam_align: hold decode off until locked

    input  wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word,

    // one full 8-pixel kernel, emitted in parallel when kvalid pulses
    output reg  [9:0]  kpix0, kpix1, kpix2, kpix3, kpix4, kpix5, kpix6, kpix7,
    output reg  [7:0]  kbase,            // column of kpix0 within the line
    output reg         kvalid,           // pulse: kpix0..7 valid this cycle
    output reg         line_start,       // pulse: a new image line begins
    output reg         frame_start,      // pulse: a new frame begins
    output reg         in_black          // current line is a black-reference line (BL)
);
    localparam [9:0] SC_FS=10'h2AA, SC_FE=10'h32A, SC_LS=10'h0AA, SC_LE=10'h12A;
    localparam [9:0] SC_BL=10'h015, SC_IMG=10'h035, SC_CRC=10'h059, SC_TR=10'h3A6;

    localparam [2:0] S_IDLE=0, S_AFTER_FS=1, S_LINE_WAIT=2, S_AFTER_LS=3, S_LINE=4, S_LINE_END=5;
    reg [2:0] st;

    reg        word_b;                   // 0 = expecting word A, 1 = word B
    reg        kpar;                      // kernel parity: 0 even (ascending), 1 odd (descending)
    reg [7:0]  kcol;                      // base column of the current kernel
    reg [9:0]  a0,a1,a2,a3;               // held word-A lanes = positions 0,2,4,6

    always @(posedge wordclk) begin
        if (rst || !aligned) begin
            st <= S_IDLE; kvalid <= 1'b0; line_start <= 1'b0; frame_start <= 1'b0;
            in_black <= 1'b0; word_b <= 1'b0; kpar <= 1'b0; kcol <= 8'd0;
        end else begin
            kvalid      <= 1'b0;          // single-cycle strobes
            line_start  <= 1'b0;
            frame_start <= 1'b0;

            case (st)
                S_IDLE:
                    if (sync_word == SC_FS) begin frame_start <= 1'b1; st <= S_AFTER_FS; end
                S_AFTER_FS: st <= S_LINE_WAIT;             // consume window-ID word
                S_LINE_WAIT: begin
                    if      (sync_word == SC_LS) st <= S_AFTER_LS;
                    else if (sync_word == SC_FE) st <= S_IDLE;
                end
                S_AFTER_LS: begin                          // consume window-ID word
                    line_start <= 1'b1;
                    word_b <= 1'b0; kpar <= 1'b0; kcol <= 8'd0;
                    st <= S_LINE;
                end
                S_LINE: begin
                    if (sync_word == SC_IMG) begin
                        in_black <= 1'b0;
                        if (!word_b) begin
                            a0 <= d0_word; a1 <= d1_word; a2 <= d2_word; a3 <= d3_word;
                            word_b <= 1'b1;
                        end else begin
                            // kernel complete -- positions:
                            //   p0=a0 p2=a1 p4=a2 p6=a3 (word A)
                            //   p1=d0 p3=d1 p5=d2 p7=d3 (word B, this cycle)
                            // spatial pixel s: even kernel = p[s]; odd kernel = p[7-s].
                            if (!kpar) begin
                                kpix0 <= a0;      kpix1 <= d0_word;
                                kpix2 <= a1;      kpix3 <= d1_word;
                                kpix4 <= a2;      kpix5 <= d2_word;
                                kpix6 <= a3;      kpix7 <= d3_word;
                            end else begin
                                kpix7 <= a0;      kpix6 <= d0_word;
                                kpix5 <= a1;      kpix4 <= d1_word;
                                kpix3 <= a2;      kpix2 <= d2_word;
                                kpix1 <= a3;      kpix0 <= d3_word;
                            end
                            kbase  <= kcol;
                            kvalid <= 1'b1;
                            kcol   <= kcol + 8'd8;
                            word_b <= 1'b0;
                            kpar   <= ~kpar;
                        end
                    end else if (sync_word == SC_BL) begin
                        in_black <= 1'b1;                  // black line: flagged, not emitted
                    end else if (sync_word == SC_LE) begin
                        st <= S_LINE_END;
                    end
                end
                S_LINE_END: st <= S_LINE_WAIT;             // consume CRC word
                default: st <= S_IDLE;
            endcase
        end
    end
endmodule
