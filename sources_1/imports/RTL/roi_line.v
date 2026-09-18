`timescale 1ns / 1ps
//==============================================================================
// roi_line.v -- ONE LINE PER CAMERA FRAME. No grouping, no sequence, no state.
//
//   R=mmm,nnn,rrggbb,cc<CR><LF>    21 bytes
//
//   index: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20
//          R = m m m , n n n ,  r  r  g  g  b  b  ,  c  c CR LF
//
//     mmm   THE MEASUREMENT. ROI mean of the 16x16 patch at the centre of the
//           sensor, 10-bit, 000..3FF.
//     nnn   pixels accumulated. MUST read 100 (= 256).
//     rrggbb  THE TOP-LEFT PIXEL AS TRANSMITTED, all three channels, on the
//           projected frame that was on screen when this frame's exposure was
//           triggered -- not the frame being read out. See cam_roi_min.v for how
//           that pairing is made.
//
//           IT IS SAMPLED, NOT SYNTHESISED. Whatever the design was already
//           sending occupies that pixel -- the impulse sequence, an SLI fringe,
//           the offline colour chart, a passed-through HDMI frame -- and nothing
//           is written into it. So for a binary pattern it reads FF0000 or
//           000000, and the "every transmitted pixel is 0 or 255" invariant holds
//           with no exception for telemetry.
//
//           ALL THREE CHANNELS, because the pixel is a colour. Sampling red alone
//           made a green- or blue-only sequence read 000000 on every frame with
//           no way to tell the frames apart -- which is what tempted an earlier
//           version into overriding the pixel to force an identity onto red.
//     cc    THE TRIGGER ORDINAL, 8-bit, incremented once per trigger issued.
//           Consecutive lines differ by exactly one; anything else is a loss.
//
// WHY THERE IS NO BLOCK FORMAT ANY MORE.
//
// This file used to have a sibling, roi_block.v, that grouped five consecutive
// frames into one line in fabric -- because the measurement is made in five-frame
// sequences and it seemed natural for the FPGA to send the sequence. It is out of
// the design now, and the reasons are worth keeping:
//
//   A BLOCK LETS ONE BAD FRAME CORRUPT FOUR GOOD ONES. A malformed sequence
//   dropped the whole block -- five means discarded because of one -- and a
//   mis-started block reported four innocent frames in the wrong slots. Here every
//   frame carries its own label, so a bad frame is one bad line and its
//   neighbours are untouched.
//
//   GROUPING IS FREE ON THE HOST AND EXPENSIVE IN FABRIC. The pixel says when the
//   flash was transmitted and cc says how far each frame is from it, so a host
//   that wants sequences bins in one line of Python -- with no partial-block
//   case, no "the delay changed mid-block" case, and no drop counter.
//
//   IT WAS THE LARGEST REMAINING PIECE OF LOGIC THAT COULD BE WRONG. An alignment
//   rule, a drop rule, a sequence-continuity check and a 43-byte assembler, none
//   of which is needed to send three numbers.
//
// THERE IS NO FRAME TAG ANY MORE. Two versions of this design encoded a
// {sequence, position} identity into the transmitted top-left pixel so each frame
// could name itself -- first as a grey level in one pixel, then as eight binary
// pixels. Both were solving a problem cc had already solved: ordering needs a
// counter that does not drift, and cc is one. The anchor comes from this pixel
// changing when the flash arrives, and position is
// (cc - cc_at_last_change) mod 5, exactly. Nothing needs encoding into the image.
//
// WHAT IS DELIBERATELY NOT SENT. The sensor's own frame counter and the impulse
// phase both used to ride along. The phase was measured racing its clock crossing
// at short delays -- it reported position 3 as 2, every sequence -- and the pixel
// plus cc carry the same information correctly. The frame counter duplicated what a gap in cc
// already says. A field that is redundant or wrong is worse than absent, because
// it invites someone to trust it.
//
// npx IS THE ONE FIELD HERE THAT WAS NOT ASKED FOR, and it stays: without it a
// genuinely dark ROI and an ROI that has drifted off the sensor produce the same
// small number, and nothing downstream can tell them apart. Three characters to
// make the mean falsifiable.
//
// hh AND ll ARE THE SATURATION CHECK, appended rather than inserted so every
// field before them keeps its offset:
//
//    R=mmm,nnn,rrggbb,cc,hh,ll<CR><LF>          27 bytes
//
//        hh   ROI pixels at or above 1020 -- saturated. The mean is only a mean
//             when this is 00; a handful of clipped pixels hide inside an average.
//        ll   ROI pixels at or below 3 -- clamped at the floor.
//
// Both are capped at FF. A parser written for the 21-byte line does NOT still
// match: the host parsers anchor on CRLF straight after cc, precisely so a half-
// arrived line cannot match on its fixed-width fields. Every current-format
// parser in host/ was updated to accept the two fields as optional.
//
// WHY ASCII AND NOT BINARY. Every other telemetry path in this design is readable
// in a terminal, and that has repeatedly been what separated "the link is dead"
// from "the link is fine and the data is wrong". 27 B x 120/s = 3240 B/s against
// 11520 B/s at 115200 8N1, about 28%.
//
// Handshake matches status_line exactly (go / tx_data / tx_send / tx_busy / busy)
// so it drops into the same arbiter slot.
//==============================================================================
module roi_line (
    input  wire        clk,
    input  wire        go,            // 1-cycle: latch and send
    input  wire [9:0]  mean,
    input  wire [8:0]  npx,
    input  wire [23:0] tlp,           // top-left pixel AS TRANSMITTED, {R,G,B}
    input  wire [7:0]  tcnt,          // trigger ordinal
    input  wire [15:0] sat,           // {saturated px, clamped-low px}, capped at FF
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy
);
    localparam integer LEN = 27;
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k = 0; k < LEN; k = k + 1) msg[k] = 8'h20;
        msg[0]  = "R";  msg[1] = "=";
        msg[5]  = ",";  msg[9] = ",";  msg[16] = ",";
        msg[19] = ",";  msg[22] = ",";
        msg[25] = 8'h0D; msg[26] = 8'h0A;
        busy = 1'b0; tx_send = 1'b0;
    end

    function [7:0] h2a; input [3:0] n; h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    // Six bits for a 27-byte message. It needs five today, but roi_block was once
    // truncated silently by exactly this -- its LEN grew past 32 while the index
    // stayed 5 bits, and the stream looked healthy while every line was cut short
    // and re-sent forever. The margin costs one flip-flop.
    reg [5:0] idx;
    reg       st;
    always @(posedge clk) begin
        tx_send <= 1'b0;
        if (!busy) begin
            if (go) begin
                msg[2]  <= h2a({2'b00, mean[9:8]});
                msg[3]  <= h2a(mean[7:4]);
                msg[4]  <= h2a(mean[3:0]);
                msg[6]  <= h2a({3'b000, npx[8]});
                msg[7]  <= h2a(npx[7:4]);
                msg[8]  <= h2a(npx[3:0]);
                msg[10] <= h2a(tlp[23:20]);   // R
                msg[11] <= h2a(tlp[19:16]);
                msg[12] <= h2a(tlp[15:12]);   // G
                msg[13] <= h2a(tlp[11:8]);
                msg[14] <= h2a(tlp[7:4]);     // B
                msg[15] <= h2a(tlp[3:0]);
                msg[17] <= h2a(tcnt[7:4]);
                msg[18] <= h2a(tcnt[3:0]);
                msg[20] <= h2a(sat[15:12]);   // saturated
                msg[21] <= h2a(sat[11:8]);
                msg[23] <= h2a(sat[7:4]);     // clamped low
                msg[24] <= h2a(sat[3:0]);
                idx <= 6'd0; st <= 1'b0; busy <= 1'b1;
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
