`timescale 1ns / 1ps
//==============================================================================
// status_line.v -- one status telemetry line over a shared UART (handshake), e.g.:
//   S=x V=x T=x F=x M=x R=x N=hhhh L=hh D=hh G=hh P=hh C=hh<CR><LF>
// led_s : [7]vsync [6]hsync [5]VPol [4]sel [3]mode [2]rdy [1]f_frm [0]trig
// dbg_s : hdmi_io debug [3]symbol_sync [2]pll_locked [1]sel [0]heartbeat
// mrg   : edid_merge dbg2[7:0] ([7]built_valid [4]chk0_ok [2]monitor_present [1:0]cstate)
// tlp   : last sampled top-left red value (diagnostic)
// tcnt  : free-running trigger-pulse count (diagnostic; watch the delta for rate)
// vs_lat: vsync edges in the last status window
//==============================================================================
module status_line (
    input  wire        clk,
    input  wire        go,
    input  wire [7:0]  led_s,
    input  wire [7:0]  dbg_s,
    input  wire [7:0]  mrg,
    input  wire [7:0]  tlp,
    input  wire [7:0]  tcnt,
    input  wire [15:0] vs_lat,
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy
);
    localparam integer LEN = 57;
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k=0;k<LEN;k=k+1) msg[k] = 8'h20;
        msg[0]="S";  msg[1]="=";  msg[3]=" ";
        msg[4]="V";  msg[5]="=";  msg[7]=" ";
        msg[8]="T";  msg[9]="=";  msg[11]=" ";
        msg[12]="F"; msg[13]="="; msg[15]=" ";
        msg[16]="M"; msg[17]="="; msg[19]=" ";
        msg[20]="R"; msg[21]="="; msg[23]=" ";
        msg[24]="N"; msg[25]="="; msg[30]=" ";
        msg[31]="L"; msg[32]="="; msg[35]=" ";
        msg[36]="D"; msg[37]="="; msg[40]=" ";
        msg[41]="G"; msg[42]="="; msg[45]=" ";
        msg[46]="P"; msg[47]="="; msg[50]=" ";
        msg[51]="C"; msg[52]="=";
        msg[55]=8'h0D; msg[56]=8'h0A;
        busy = 1'b0; tx_send = 1'b0;
    end

    function [7:0] b2a; input v;       b2a = v ? "1" : "0"; endfunction
    function [7:0] h2a; input [3:0] n; h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    reg [5:0] idx;
    reg       st;
    always @(posedge clk) begin
        tx_send <= 1'b0;
        if (!busy) begin
            if (go) begin
                msg[2]  <= b2a(led_s[4]); msg[6]  <= b2a(led_s[5]); msg[10] <= b2a(led_s[0]);
                msg[14] <= b2a(led_s[1]); msg[18] <= b2a(led_s[3]); msg[22] <= b2a(led_s[2]);
                msg[26] <= h2a(vs_lat[15:12]); msg[27] <= h2a(vs_lat[11:8]);
                msg[28] <= h2a(vs_lat[7:4]);   msg[29] <= h2a(vs_lat[3:0]);
                msg[33] <= h2a(led_s[7:4]); msg[34] <= h2a(led_s[3:0]);
                msg[38] <= h2a(dbg_s[7:4]); msg[39] <= h2a(dbg_s[3:0]);
                msg[43] <= h2a(mrg[7:4]);   msg[44] <= h2a(mrg[3:0]);
                msg[48] <= h2a(tlp[7:4]);   msg[49] <= h2a(tlp[3:0]);
                msg[53] <= h2a(tcnt[7:4]);  msg[54] <= h2a(tcnt[3:0]);
                idx <= 6'd0; st <= 1'b0; busy <= 1'b1;
            end
        end else begin
            case (st)
                1'b0: if (!tx_busy) begin tx_data <= msg[idx]; tx_send <= 1'b1; st <= 1'b1; end
                1'b1: if (idx == LEN-1) busy <= 1'b0;
                      else begin idx <= idx + 6'd1; st <= 1'b0; end
            endcase
        end
    end
endmodule
