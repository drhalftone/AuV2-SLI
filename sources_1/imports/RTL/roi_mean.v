`timescale 1ns / 1ps
//==============================================================================
// roi_mean.v -- average of a 16x16 ROI, computed in fabric, one number per frame.
//
// WHY THIS EXISTS. The projector-profiling experiment needs one scalar per camera
// frame -- the mean of a small patch -- and nothing else. Sending a whole 1280x1024
// frame over USB 3 to compute a 256-pixel average on the PC is 2.6 MB of traffic per
// sample and requires the Ft+ to be cabled. Computing it here costs a handful of
// LUTs and lets the entire experiment run over the Pt's 115200 UART.
//
// THE ROI IS 16x16 ON PURPOSE. cam_sync_decode delivers EIGHT pixels per kernel, so
// 16 columns is exactly two kernels and needs no barrel shifting: a kernel is either
// wholly in the ROI or wholly out. 16 rows x 16 columns = 256 pixels, so the divide
// is >>8. No divider, no multiplier, no rounding decision.
//
// COORDINATES. `kbase` is the column of kpix0 within the line (0..1279, from
// cam_sync_decode line 69). Rows are counted from frame_start by line_start pulses.
//
// ROWS ARE COUNTED INCLUDING BLACK-REFERENCE LINES, DELIBERATELY. The sensor emits
// SC_BL black rows as well as SC_IMG image rows and both raise line_start; which
// comes first, and how many there are, depends on sensor configuration this module
// has no business knowing. Rather than encode an assumption that would be silently
// wrong, the row origin is a RUNTIME register -- dial it in against npx and the
// picture. `in_black` is exported as a status bit so the host can see whether the
// chosen row is a black line.
//
// npx IS THE HONEST WITNESS. It counts the pixels actually accumulated. It must read
// exactly 256. A mis-set ROI -- off the end of a line, past the last row, or landing
// where no kernels arrive -- produces a perfectly plausible small mean and no other
// symptom. A mean reported without npx == 256 beside it is not a measurement.
//==============================================================================
module roi_mean (
    input  wire        wordclk,      // camera word clock -- the pixel stream domain
    input  wire        rst,

    // ---- ROI placement, quasi-static, in the wordclk domain (2FF-synced outside
    //      or simply written while the stream is idle -- an ROI that moves mid-frame
    //      only corrupts that frame, and npx will say so).
    input  wire [7:0]  roi_col8,     // ROI first column / 8   (kernel index)
    input  wire [7:0]  roi_row8,     // ROI first row    / 8

    // ---- the pixel stream, straight off cam_sync_decode ----
    input  wire        kvalid,
    input  wire [10:0] kbase,
    input  wire [9:0]  kp0, kp1, kp2, kp3, kp4, kp5, kp6, kp7,
    input  wire        line_start,
    input  wire        frame_start,
    input  wire        frame_end,
    input  wire        in_black,

    // ---- one result per frame, wordclk domain ----
    output reg  [9:0]  mean,         // sum >> 8
    output reg  [8:0]  npx,          // pixels accumulated; MUST be 256
    output reg  [15:0] fcnt,         // frames since reset -- lets the host see gaps
    output reg         blk,          // the ROI row was a black-reference line
    output reg         done          // 1-cycle pulse, wordclk
);
    // 256 px x 1023 = 261,888 -> 18 bits. Sized from the worst case, not guessed.
    reg [17:0] sum  = 18'd0;
    reg [8:0]  cnt  = 9'd0;
    reg [10:0] row  = 11'd0;
    reg        blk_l = 1'b0;

    wire [10:0] row0 = {roi_row8, 3'b000};
    wire [10:0] col0 = {roi_col8, 3'b000};

    // Row window: 16 rows starting at row0. Column window: the two kernels whose
    // base is col0 and col0+8. col0 is 8-aligned by construction (it is roi_col8
    // shifted up by 3), so those two kernels tile the 16 columns exactly.
    wire row_hit = (row >= row0) && (row < row0 + 11'd16);
    wire col_hit = (kbase == col0) || (kbase == col0 + 11'd8);
    wire take    = kvalid && row_hit && col_hit;

    wire [12:0] ksum = {3'b0, kp0} + {3'b0, kp1} + {3'b0, kp2} + {3'b0, kp3}
                     + {3'b0, kp4} + {3'b0, kp5} + {3'b0, kp6} + {3'b0, kp7};

    always @(posedge wordclk) begin
        done <= 1'b0;

        if (rst) begin
            sum <= 18'd0; cnt <= 9'd0; row <= 11'd0; fcnt <= 16'd0;
            mean <= 10'd0; npx <= 9'd0; blk <= 1'b0; blk_l <= 1'b0;
        end else begin
            // ---- row tracking -------------------------------------------------
            // frame_start and line_start pulse together on the first line, so the
            // frame_start branch must win or the first line would be counted as
            // row 1. cam_sync_decode asserts both (lines 129-131).
            if (frame_start)      row <= 11'd0;
            else if (line_start)  row <= row + 11'd1;

            // Latch whether the ROI's own rows are black-reference lines. Sampled
            // while inside the row window, so it reports the ROI, not the frame.
            if (frame_start)          blk_l <= 1'b0;
            else if (line_start && (row + 11'd1 >= row0) && (row + 11'd1 < row0 + 11'd16))
                                      blk_l <= blk_l | in_black;

            // ---- accumulate ---------------------------------------------------
            if (take) begin
                sum <= sum + {5'b0, ksum};
                cnt <= cnt + 9'd8;
            end

            // ---- publish ------------------------------------------------------
            // frame_end can coincide with a kvalid that is inside the ROI. Adding
            // that kernel here rather than dropping it keeps npx at 256 on the
            // boundary case; without it the last frame of every burst would report
            // 248 and look like a mis-set ROI.
            if (frame_end) begin
                if (take) begin
                    mean <= (sum + {5'b0, ksum}) >> 8;
                    npx  <= cnt + 9'd8;
                end else begin
                    mean <= sum >> 8;
                    npx  <= cnt;
                end
                blk  <= blk_l;
                fcnt <= fcnt + 16'd1;
                done <= 1'b1;
                sum  <= 18'd0;
                cnt  <= 9'd0;
            end
        end
    end
endmodule
