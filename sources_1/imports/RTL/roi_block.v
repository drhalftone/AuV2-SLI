`timescale 1ns / 1ps
//==============================================================================
// roi_block.v -- one line per SEQUENCE: the five ROI means, already in order.
//
//   B=aaa,bbb,ccc,ddd,eee,ffff,v,dddddd,tt,cc<CR><LF>  43 bytes
//
//   index: 0..4  B = a a a      18..20 e e e
//          5..8  , b b b        21..25 , f f f f
//          9..12 , c c c        26..27 , v
//          13..16 , d d d       28..34 , d d d d d d      35,36 CR LF
//
//     aaa   ROI mean of the frame at POSITION 0 of the sequence, named by its TAG
//     bbb..eee   positions 1..4, in arrival order
//     ffff  frame counter of the FIRST frame in the block -- a gap is a dropped block
//     v     1 = every one of the five had npx == 256; 0 = at least one did not
//     tt    the frame TAG that opened this block: {cyc[4:0], 3'd0}. A gap in cyc
//           between consecutive blocks is a DROPPED SEQUENCE, visible rather than
//           silently closed up.
//     cc    THE TRIGGER ORDINAL of the frame that opened this block. Blocks are
//           five frames, so consecutive blocks must differ by exactly 5. This
//           counts triggers ISSUED, where ffff counts frames the sensor
//           DELIVERED -- see roi_line.v for how the pair localises a loss.
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
// ALIGNMENT IS ENFORCED, NOT ASSUMED. A block starts only on a frame whose tag says
// POSITION 0, and each frame after it must carry the position expected next AND the
// same sequence number. Anything else discards the partial block and waits for the next
// position 0. A malformed sequence is DROPPED rather than emitted with a plausible
// number in the wrong slot, because the wrong number in the right-looking place is the
// failure mode this whole path keeps producing.
//
// THE MARKER USED TO BE THE VALUE 255. The top-left pixel carried the flash LEVEL --
// 255 on the bright frame, 0 on the other four -- which named one frame in five and
// left four indistinguishable, so everything downstream had to recover their order by
// counting, and every scheme that counted drifted. With the tag, 255 is not a marker
// and cannot occur: 0xFF decodes as position 7, and a five-frame cycle only ever
// reaches position 4. tlp[2:0] <= 4 is an invariant of a healthy stream.
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
    input  wire [7:0]  tcnt,                   // trigger ordinal of this frame
    input  wire [23:0] dly,                    // genlock delay register, 10 ns ticks
    input  wire        dly_we,                 // 1-cycle: the delay just changed
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy,
    output reg  [15:0] dropped = 16'd0         // malformed / unaligned sequences
);
    localparam integer LEN = 43;
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k = 0; k < LEN; k = k + 1) msg[k] = 8'h20;
        msg[0]  = "B";  msg[1]  = "=";
        msg[5]  = ",";  msg[9]  = ",";  msg[13] = ",";  msg[17] = ",";
        msg[21] = ",";  msg[26] = ",";  msg[28] = ",";  msg[35] = ",";
        msg[38] = ",";
        msg[41] = 8'h0D; msg[42] = 8'h0A;
        busy = 1'b0; tx_send = 1'b0;
    end

    function [7:0] h2a; input [3:0] n; h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    // ---- collect ------------------------------------------------------------
    reg [9:0]  buf_m [0:7];
    reg [2:0]  cnt   = 3'd0;        // how many of the NF are in hand
    reg        armed = 1'b0;        // a block is in progress
    reg [15:0] f0    = 16'd0;
    reg [23:0] d0    = 24'd0;       // delay in force when this block started
    reg [7:0]  t0    = 8'd0;        // the frame tag that opened this block
    reg [7:0]  c0    = 8'd0;        // trigger ordinal of that opening frame
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
            if (tlp[2:0] == 3'd0) begin
                // POSITION 0 OF A SEQUENCE. tag[2:0] names the frame outright, so this
                // is the bright frame by definition rather than by brightness. If a
                // block was already in progress it was short -- discarded, not padded.
                if (armed && cnt != 3'd0) dropped <= dropped + 16'd1;
                buf_m[0] <= mean;
                f0       <= fcnt;
                d0       <= dly;
                t0       <= tlp;
                c0       <= tcnt;
                allok    <= (npx == 9'd256);
                cnt      <= 3'd1;
                armed    <= 1'b1;
            end else if (armed) begin
                // EVERY FRAME IS CHECKED AGAINST ITS OWN TAG, not merely counted. The
                // position must be the one expected next AND the cycle number must
                // match the frame that opened the block -- so a dropped or repeated
                // frame is caught here rather than shifting all five means by one.
                if (cnt >= NF[2:0] || tlp[2:0] != cnt || tlp[7:3] != t0[7:3]) begin
                    dropped <= dropped + 16'd1;
                    armed   <= 1'b0;
                    cnt     <= 3'd0;
                end else begin
                    buf_m[cnt] <= mean;
                    allok      <= allok & (npx == 9'd256);
                    cnt        <= cnt + 3'd1;
                    if (cnt == NF[2:0] - 3'd1) begin
                        ready <= 1'b1;
                        armed <= 1'b0;
                    end
                end
            end
            // else: not armed and not at position 0 -- still hunting for a sequence start.
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
                msg[36] <= h2a(t0[7:4]);
                msg[37] <= h2a(t0[3:0]);
                msg[39] <= h2a(c0[7:4]);
                msg[40] <= h2a(c0[3:0]);
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
