`timescale 1ns / 1ps
//==============================================================================
// roi_line.v -- one short telemetry line per camera frame, carrying the ROI mean.
//
//   R=mmm,ffff,nnn,b<CR><LF>          18 bytes
//
//   index: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17
//          R = m m m , f f f f  ,  n  n  n  ,  b CR LF
//
//     mmm   ROI mean, 10-bit -> 3 hex digits (000..3FF)
//     ffff  frame counter, hex -- a gap here is a DROPPED LINE, not a dropped frame
//     nnn   pixels accumulated, hex; MUST read 100 (= 256)
//     b     1 = the ROI sat on black-reference rows
//
// WHY NOT REUSE status_line. status_line fires on a 0.5 s window (usb_link WIN =
// 50e6) and is 62 bytes of mixed ASCII. This needs one sample per camera frame --
// 120 per second -- so it needs its own trigger and a line short enough to fit:
// 18 B x 120/s = 2160 B/s against 11520 B/s at 115200 8N1, about 19%.
//
// WHY ASCII AND NOT BINARY. Every other telemetry path in this design is readable in
// a terminal, and that has repeatedly been what separated "the link is dead" from
// "the link is fine and the data is wrong". A binary stream costs half the bytes and
// all of that.
//
// npx AND fcnt ARE NOT OPTIONAL FIELDS. The mean alone cannot distinguish a genuinely
// dark ROI from an ROI that is off the sensor, and a plot with silently missing
// samples looks exactly like a plot of a slower phenomenon. Both failures are
// invisible without these two numbers, and both are cheap to carry.
//
// Handshake matches status_line exactly (go / tx_data / tx_send / tx_busy / busy) so
// it drops into the same arbiter slot.
//==============================================================================
module roi_line (
    input  wire        clk,
    input  wire        go,            // 1-cycle: latch and send
    input  wire [9:0]  mean,
    input  wire [15:0] fcnt,
    input  wire [8:0]  npx,
    input  wire        blk,
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy
);
    localparam integer LEN = 18;
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k = 0; k < LEN; k = k + 1) msg[k] = 8'h20;
        msg[0]  = "R";  msg[1]  = "=";
        msg[5]  = ",";  msg[10] = ",";  msg[14] = ",";
        msg[16] = 8'h0D; msg[17] = 8'h0A;
        busy = 1'b0; tx_send = 1'b0;
    end

    function [7:0] h2a; input [3:0] n; h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    reg [4:0] idx;
    reg       st;
    always @(posedge clk) begin
        tx_send <= 1'b0;
        if (!busy) begin
            if (go) begin
                msg[2]  <= h2a({2'b00, mean[9:8]});
                msg[3]  <= h2a(mean[7:4]);
                msg[4]  <= h2a(mean[3:0]);
                msg[6]  <= h2a(fcnt[15:12]);
                msg[7]  <= h2a(fcnt[11:8]);
                msg[8]  <= h2a(fcnt[7:4]);
                msg[9]  <= h2a(fcnt[3:0]);
                msg[11] <= h2a({3'b000, npx[8]});
                msg[12] <= h2a(npx[7:4]);
                msg[13] <= h2a(npx[3:0]);
                msg[15] <= blk ? "1" : "0";
                idx <= 5'd0; st <= 1'b0; busy <= 1'b1;
            end
        end else begin
            case (st)
                1'b0: if (!tx_busy) begin
                          tx_data <= msg[idx]; tx_send <= 1'b1; st <= 1'b1;
                      end
                1'b1: if (idx == LEN-1) busy <= 1'b0;
                      else begin idx <= idx + 5'd1; st <= 1'b0; end
            endcase
        end
    end
endmodule
