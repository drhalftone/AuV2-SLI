`timescale 1ns / 1ps
//==============================================================================
// roi_block.v -- one line per SEQUENCE: the five ROI means, already in order.
//
//   B=aaa,bbb,ccc,ddd,eee,ffff,v,dddddd<CR><LF>        37 bytes
//
//   index: 0..4  B = a a a      18..20 e e e
//          5..8  , b b b        21..25 , f f f f
//          9..12 , c c c        26..27 , v
//          13..16 , d d d       28..34 , d d d d d d      35,36 CR LF
//
//     aaa   ROI mean of the frame transmitted with TOP-LEFT PIXEL 255 (the flash)
//     bbb..eee   the next four frames, in arrival order, all transmitted with TLP 0
//     ffff  frame counter of the FIRST frame in the block -- a gap is a dropped block
//     v     1 = every one of the five had npx == 256; 0 = at least one did not
//     dddddd  THE GENLOCK DELAY IN FORCE FOR THIS BLOCK, 24-bit, 10 ns ticks.
//
// THE DELAY IS REPORTED, NOT ASSUMED. The host writes the delay register and then reads
// blocks, so without this it is plotting against the delay it BELIEVES it set. Two ways
// that goes wrong: a write that did not land, and -- unavoidable with a five-frame block
// -- the one block that straddles the change and holds means from two different delays.
// Sending the value makes each block self-describing, and a block whose delay changed
// mid-assembly is DROPPED below rather than emitted with a delay that was true for only
// part of it.
//
// WHY THE FPGA GROUPS THEM AND NOT THE HOST.
//
// The five frames arrive in order and the FPGA already knows which is which: it
// TRANSMITTED them, and it samples the top-left pixel back off its own output. Sending
// five separate lines and having the PC work out where the sequence starts throws that
// knowledge away and then tries to reconstruct it -- which is how a run of host-side
// bugs got written: rotating on a free-running phase counter, majority-voting an
// anchor, re-binning live, rejecting delays. Every one of those was a workaround for
// information the FPGA had and did not send.
//
// Here the block IS the unit. Position in the line is the frame index, by construction.
// The host reads five numbers and plots five numbers.
//
// ALIGNMENT IS ENFORCED, NOT ASSUMED. A block starts ONLY on a frame whose transmitted
// TLP is 255, and the four that follow must all read 0. Anything else -- a 255 arriving
// early, a sixth frame without a marker -- discards the partial block and waits for the
// next marker. A malformed sequence is DROPPED rather than emitted with a plausible
// number in the wrong slot, because the wrong number in the right-looking place is the
// failure mode this whole path keeps producing.
//
// A dropped block costs 41.6 ms at 120 Hz and the counter shows it. That is the correct
// trade against a silently mislabelled one.
//==============================================================================
module roi_block #(
    parameter integer NF = 5                   // frames per sequence
)(
    input  wire        clk,
    input  wire        go,                     // 1-cycle: a new ROI result is valid
    input  wire [9:0]  mean,
    input  wire [15:0] fcnt,
    input  wire [8:0]  npx,
    input  wire [7:0]  tlp,                    // top-left pixel AS TRANSMITTED
    input  wire [23:0] dly,                    // genlock delay register, 10 ns ticks
    input  wire        dly_we,                 // 1-cycle: the delay just changed
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy,
    output reg  [15:0] dropped = 16'd0         // malformed / unaligned sequences
);
    localparam integer LEN = 37;
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k = 0; k < LEN; k = k + 1) msg[k] = 8'h20;
        msg[0]  = "B";  msg[1]  = "=";
        msg[5]  = ",";  msg[9]  = ",";  msg[13] = ",";  msg[17] = ",";
        msg[21] = ",";  msg[26] = ",";  msg[28] = ",";
        msg[35] = 8'h0D; msg[36] = 8'h0A;
        busy = 1'b0; tx_send = 1'b0;
    end

    function [7:0] h2a; input [3:0] n; h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    // ---- collect ------------------------------------------------------------
    reg [9:0]  buf_m [0:7];
    reg [2:0]  cnt   = 3'd0;        // how many of the NF are in hand
    reg        armed = 1'b0;        // a block is in progress
    reg [15:0] f0    = 16'd0;
    reg [23:0] d0    = 24'd0;       // delay in force when this block started
    reg        allok = 1'b1;
    reg        ready = 1'b0;        // a complete, aligned block is waiting

    // SIX BITS, NOT FIVE. LEN is 37, so a 5-bit index wraps at 31: the line was cut
    // after 32 characters with no CRLF, `idx == LEN-1` could never be true, busy never
    // cleared, and the same buffer was re-sent forever. It looked like a working stream
    // -- blocks arrived at the right rate with plausible values -- and every one was
    // truncated and identical. Width the counter to the message, not to the last one.
    reg [5:0] idx;
    reg       st;

    always @(posedge clk) begin
        tx_send <= 1'b0;
        ready   <= ready;

        // A DELAY CHANGE INVALIDATES THE BLOCK UNDER WAY. Its five frames would span
        // two delays, and averaging across the step is exactly the kind of quiet error
        // that reads as a real feature. One block is 41.6 ms; the sweep discards more
        // than that settling anyway.
        if (dly_we && armed) begin
            dropped <= dropped + 16'd1;
            armed   <= 1'b0;
            cnt     <= 3'd0;
        end else if (go) begin
            if (tlp == 8'hFF) begin
                // Marker. Starts a block -- and if one was already in progress it was
                // short, so it is discarded rather than padded.
                if (armed && cnt != 3'd0) dropped <= dropped + 16'd1;
                buf_m[0] <= mean;
                f0       <= fcnt;
                d0       <= dly;
                allok    <= (npx == 9'd256);
                cnt      <= 3'd1;
                armed    <= 1'b1;
            end else if (armed) begin
                if (cnt >= NF[2:0]) begin
                    // NF frames already in hand and still no marker: the sequence is
                    // longer than advertised. Drop and wait for the next 255.
                    dropped <= dropped + 16'd1;
                    armed   <= 1'b0;
                    cnt     <= 3'd0;
                end else begin
                    buf_m[cnt] <= mean;
                    allok      <= allok & (npx == 9'd256);
                    cnt        <= cnt + 3'd1;
                    if (cnt == NF[2:0] - 3'd1) begin
                        // The block completes on this frame.
                        ready <= 1'b1;
                        armed <= 1'b0;
                    end
                end
            end
            // else: not armed and no marker -- still hunting for the start.
        end

        // ---- emit ----------------------------------------------------------
        if (!busy) begin
            if (ready) begin
                msg[2]  <= h2a({2'b00, buf_m[0][9:8]});
                msg[3]  <= h2a(buf_m[0][7:4]);
                msg[4]  <= h2a(buf_m[0][3:0]);
                msg[6]  <= h2a({2'b00, buf_m[1][9:8]});
                msg[7]  <= h2a(buf_m[1][7:4]);
                msg[8]  <= h2a(buf_m[1][3:0]);
                msg[10] <= h2a({2'b00, buf_m[2][9:8]});
                msg[11] <= h2a(buf_m[2][7:4]);
                msg[12] <= h2a(buf_m[2][3:0]);
                msg[14] <= h2a({2'b00, buf_m[3][9:8]});
                msg[15] <= h2a(buf_m[3][7:4]);
                msg[16] <= h2a(buf_m[3][3:0]);
                msg[18] <= h2a({2'b00, buf_m[4][9:8]});
                msg[19] <= h2a(buf_m[4][7:4]);
                msg[20] <= h2a(buf_m[4][3:0]);
                msg[22] <= h2a(f0[15:12]);
                msg[23] <= h2a(f0[11:8]);
                msg[24] <= h2a(f0[7:4]);
                msg[25] <= h2a(f0[3:0]);
                msg[27] <= allok ? "1" : "0";
                msg[29] <= h2a(d0[23:20]);
                msg[30] <= h2a(d0[19:16]);
                msg[31] <= h2a(d0[15:12]);
                msg[32] <= h2a(d0[11:8]);
                msg[33] <= h2a(d0[7:4]);
                msg[34] <= h2a(d0[3:0]);
                idx <= 6'd0; st <= 1'b0; busy <= 1'b1; ready <= 1'b0;
                cnt <= 3'd0;
            end
        end else begin
            case (st)
                1'b0: if (!tx_busy) begin
                          tx_data <= msg[idx]; tx_send <= 1'b1; st <= 1'b1;
                      end
                1'b1: if (idx == LEN-1) busy <= 1'b0;
                      else begin idx <= idx + 6'd1; st <= 1'b0; end
            endcase
        end
    end
endmodule
