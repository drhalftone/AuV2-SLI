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
//     Each frame-sync code is followed by a 3-bit window-ID word.
//
//  2. DE-INTERLEAVE (datasheet Figure 36). Pixels arrive in 8-pixel kernels, TWO words
//     per kernel: word A carries kernel positions {0,2,4,6} on lanes {d0,d1,d2,d3}, word B
//     carries {1,3,5,7}. EVEN kernels are spatially ascending, ODD kernels descending.
//
// THROUGHPUT -- the thing an earlier 1-pixel-per-clock version got wrong: each word is
// 4 pixels (one per data lane), so the sensor delivers 4 px/wordclk. A raster stream at
// 1 px/clk cannot keep up and the kernel buffer overruns. So the output is a WHOLE KERNEL
// at once: on word B, all 8 spatial pixels of the kernel are emitted in parallel with their
// base column. Kernels complete every 2 wordclks => 4 px/clk average, matching the source.
//
//=============================================================================
// THE FRAMING WORDS CARRY PIXELS. This is the thing that took longest to find.
//
// An earlier version accumulated a kernel only when sync_word == IMG, treating LS/FS/LE/FE
// and the window-ID word as pure markers. They are not. The data lanes are live during
// every one of them, and dropping them loses FOUR words -- SIXTEEN pixels -- from every
// single line.
//
// The symptom was a 16-pixel-wide, pixel-perfect-vertical black bar in every captured
// frame. It survived being blamed on solder, on dropped kernels, and on debris sitting on
// the sensor glass. It was none of those: it was these sixteen pixels.
//
// MEASURED ON SILICON with cam_syncdbg, per line, latched:
//
//     WL = 319   words from line-start through line-end, inclusive
//     IL = 316   of those are IMG
//     OL =   1   one word decodes to none of the eight codes -- the window ID
//     LF = 1024  lines per frame
//     W0 = 319   the FS line is exactly as long as every other line
//
// So the line is  [LS|FS] [WN] IMG x316 [LE|FE] [WN]  = 320 data words
//                 = 320 x 4 lanes = 1280 pixels = 160 kernels. Exactly one line.
//
// 316 IMG words alone give 1264 pixels. The missing 16 are the two framing words and the
// two window-ID words. This matches osrf/ovc's decoder, whose sync_img_data accepts
// IM | WN | FS | LS | FE | LE, and their sim_python.v, which sends exactly (COLS/4)-4
// IMG words per line and increments pixel data on the framing sends too.
//
// W0 == WL also says FS *replaces* row 0's LS rather than preceding it. The old FSM went
// S_IDLE -> S_AFTER_FS -> S_LINE_WAIT and then waited for an LS that had already gone past
// as FS, so row 0 was dropped and every frame was one line out of registration.
//
// Because the window-ID word decodes to no defined code, it cannot be recognised by VALUE.
// It is identified by POSITION -- the word right after a line start, and the word right
// after a line end. Hence the explicit S_WN1/S_WN2 states: `acc` is a function of the STATE,
// not of sync_word.
//=============================================================================
module cam_sync_decode (
    input  wire        wordclk,
    input  wire        rst,
    input  wire        aligned,          // from cam_align: hold decode off until locked

    input  wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word,

    // one full 8-pixel kernel, emitted in parallel when kvalid pulses
    output reg  [9:0]  kpix0, kpix1, kpix2, kpix3, kpix4, kpix5, kpix6, kpix7,
    output reg  [10:0] kbase,            // column of kpix0 within the line (0..1279, so 11 bits)
    output reg         kvalid,           // pulse: kpix0..7 valid this cycle
    output reg         line_start,       // pulse: a new image line begins
    output reg         frame_start,      // pulse: a new frame begins
    // Pulse on the end of the FE line. A free-running consumer can treat the NEXT
    // frame_start as "the previous frame ended", but a TRIGGERED one cannot -- there is no
    // next frame until the FPGA asks for one, so waiting for it hangs the capture.
    output reg         frame_end,
    output reg         in_black          // current line is a black-reference line (BL)
);
    localparam [9:0] SC_FS=10'h2AA, SC_FE=10'h32A, SC_LS=10'h0AA, SC_LE=10'h12A;
    localparam [9:0] SC_BL=10'h015, SC_IMG=10'h035, SC_CRC=10'h059, SC_TR=10'h3A6;

    // S_WN1 = the window-ID word after a line start; S_WN2 = the one after a line end.
    // S_CRC consumes the checksum word. S_GAP spans the training words between lines.
    localparam [2:0] S_IDLE=0, S_WN1=1, S_LINE=2, S_WN2=3, S_CRC=4, S_GAP=5;
    reg [2:0] st;

    reg        word_b;                   // 0 = expecting word A, 1 = word B
    reg        kpar;                     // kernel parity: 0 even (ascending), 1 odd (descending)
    reg [10:0] kcol;                     // base column of the current kernel (0..1279)
    reg [9:0]  a0,a1,a2,a3;              // held word-A lanes = positions 0,2,4,6
    reg        last_fe;                  // the line just ended on FE, not LE

    wire h_ls = (sync_word == SC_LS);
    wire h_fs = (sync_word == SC_FS);
    wire h_le = (sync_word == SC_LE);
    wire h_fe = (sync_word == SC_FE);

    // Does THIS word carry pixels, and does it begin a line? Both are functions of the
    // state, because the window-ID word has no recognisable value of its own.
    reg acc, newline;
    always @(*) begin
        acc     = 1'b0;
        newline = 1'b0;
        case (st)
            S_IDLE: begin acc = h_fs;              newline = h_fs; end
            S_GAP:  begin acc = h_ls;              newline = h_ls; end
            S_WN1:    acc = 1'b1;                  // window ID, carries pixels
            S_LINE:   acc = (sync_word == SC_IMG) || (sync_word == SC_BL) || h_le || h_fe;
            S_WN2:    acc = 1'b1;                  // trailing window ID, carries pixels
            default:  acc = 1'b0;                  // CRC word: no pixels
        endcase
    end

    always @(posedge wordclk) begin
        if (rst || !aligned) begin
            st <= S_IDLE; kvalid <= 1'b0; line_start <= 1'b0; frame_start <= 1'b0;
            frame_end <= 1'b0; in_black <= 1'b0;
            word_b <= 1'b0; kpar <= 1'b0; kcol <= 11'd0; last_fe <= 1'b0;
        end else begin
            kvalid      <= 1'b0;          // single-cycle strobes
            line_start  <= 1'b0;
            frame_start <= 1'b0;
            frame_end   <= 1'b0;

            //---------------------------------------------------------- framing
            case (st)
                S_IDLE:
                    if (h_fs) begin
                        frame_start <= 1'b1;
                        line_start  <= 1'b1;
                        in_black    <= 1'b0;
                        last_fe     <= 1'b0;
                        st          <= S_WN1;
                    end

                S_WN1: st <= S_LINE;

                S_LINE: begin
                    if (sync_word == SC_BL) in_black <= 1'b1;
                    else if (sync_word == SC_IMG) in_black <= 1'b0;

                    if (h_le || h_fe) begin
                        last_fe <= h_fe;
                        st      <= S_WN2;
                    end
                end

                S_WN2: st <= S_CRC;

                // The checksum word. By now the FE line's final kernel has been
                // accumulated, so this is the earliest point at which the frame can
                // honestly be declared over.
                S_CRC:
                    if (last_fe) begin
                        frame_end <= 1'b1;
                        st        <= S_IDLE;
                    end else st <= S_GAP;

                S_GAP:
                    if (h_ls) begin
                        line_start <= 1'b1;
                        in_black   <= 1'b0;
                        st         <= S_WN1;
                    end else if (h_fs) begin
                        // Should not happen mid-frame; means sync was lost. Restart
                        // cleanly rather than decode the rest of the frame wrongly.
                        frame_start <= 1'b1;
                        line_start  <= 1'b1;
                        in_black    <= 1'b0;
                        last_fe     <= 1'b0;
                        st          <= S_WN1;
                    end

                default: st <= S_IDLE;
            endcase

            // FE outside S_LINE means sync is lost -- resynchronise. FE cannot alias:
            // the sync channel carries only the eight defined codes plus 3-bit window
            // IDs, and none of those is 0x32A.
            if (h_fe && st != S_LINE) begin
                st      <= S_IDLE;
                word_b  <= 1'b0;
                last_fe <= 1'b0;
            end

            //------------------------------------------------ pixel accumulation
            // Runs on EVERY data-carrying word, framing words included. A line is
            // 320 such words = 160 kernels = 1280 pixels.
            if (acc) begin
                if (newline || !word_b) begin
                    a0 <= d0_word; a1 <= d1_word; a2 <= d2_word; a3 <= d3_word;
                    word_b <= 1'b1;
                    if (newline) begin kpar <= 1'b0; kcol <= 11'd0; end
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
                    kcol   <= kcol + 11'd8;
                    word_b <= 1'b0;
                    kpar   <= ~kpar;
                end
            end
        end
    end
endmodule
