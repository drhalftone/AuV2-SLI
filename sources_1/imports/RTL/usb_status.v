`timescale 1ns / 1ps
//============================================================================
// usb_status.v -- one-way UART telemetry for AuV2-SLI
//
// Streams a status line out the Alchitry Au V2's FT2232H channel B (the COM
// port) about twice a second. TX only, 115200 8-N-1, clocked off clk100 so it
// keeps reporting even in offline mode (no HDMI clock needed). Pin usb_tx=P16.
//
// Input `led` is the SAME 8-bit status that lights the board LEDs:
//   [7]=vsync [6]=hsync [5]=VPolarity [4]=sel(HDMI clk detected/passthrough)
//   [3]=mode(C1_in1) [2]=rdy(C1_in0) [1]=f_frm [0]=trig
// `dbg` is the hdmi_io debug bus.
//
// Line format (CRLF terminated):
//   S=x V=x T=x F=x M=x R=x N=hhhh L=hh D=hh
//   N = vsync rising edges counted in the last ~0.5 s window (frame tick /
//       liveness: ~60 @120Hz, ~30 @60Hz, 0000 = no frames).
//============================================================================
module usb_status #(
    parameter integer CLK_FREQ = 100_000_000,
    parameter integer BAUD     = 115_200,
    parameter integer WIN      = 50_000_000    // ~0.5 s status period
)(
    input  wire       clk100,
    input  wire [7:0] led,
    input  wire [7:0] dbg,
    output wire       usb_tx
);
    localparam integer DIV = CLK_FREQ / BAUD;  // 868 cycles/bit @100MHz/115200
    localparam integer LEN = 42;

    // ---- CDC: sample async status into the clk100 domain ----
    reg [7:0] led_d0=0, led_s=0, dbg_d0=0, dbg_s=0;
    reg vs_d0=0, vs_d1=0, vs_d2=0;
    always @(posedge clk100) begin
        led_d0 <= led; led_s <= led_d0;
        dbg_d0 <= dbg; dbg_s <= dbg_d0;
        vs_d0  <= led[7]; vs_d1 <= vs_d0; vs_d2 <= vs_d1;
    end
    wire vs_rise = vs_d1 & ~vs_d2;

    // ---- window timer + per-window vsync (frame) counter ----
    reg [31:0] win_cnt = 0;
    reg [15:0] vs_run = 0, vs_lat = 0;
    reg        latch = 0;
    always @(posedge clk100) begin
        latch <= 1'b0;
        if (win_cnt >= WIN-1) begin
            win_cnt <= 0;
            vs_lat  <= vs_run;
            vs_run  <= 0;
            latch   <= 1'b1;
        end else begin
            win_cnt <= win_cnt + 1;
            if (vs_rise) vs_run <= vs_run + 1'b1;
        end
    end

    // ---- message buffer: fixed template + variable fields ----
    reg [7:0] msg [0:LEN-1];
    integer k;
    initial begin
        for (k=0;k<LEN;k=k+1) msg[k] = 8'h20;     // spaces
        msg[0]="S";  msg[1]="=";  msg[3]=" ";
        msg[4]="V";  msg[5]="=";  msg[7]=" ";
        msg[8]="T";  msg[9]="=";  msg[11]=" ";
        msg[12]="F"; msg[13]="="; msg[15]=" ";
        msg[16]="M"; msg[17]="="; msg[19]=" ";
        msg[20]="R"; msg[21]="="; msg[23]=" ";
        msg[24]="N"; msg[25]="="; msg[30]=" ";
        msg[31]="L"; msg[32]="="; msg[35]=" ";
        msg[36]="D"; msg[37]="=";
        msg[40]=8'h0D; msg[41]=8'h0A;             // CRLF
    end

    function [7:0] b2a; input v;        b2a = v ? "1" : "0"; endfunction
    function [7:0] h2a; input [3:0] n;  h2a = (n < 10) ? (8'h30 + n) : (8'h41 + n - 4'd10); endfunction

    always @(posedge clk100) begin
        if (latch) begin
            msg[2]  <= b2a(led_s[4]);  // S = sel (HDMI clock detected)
            msg[6]  <= b2a(led_s[5]);  // V = VPolarity
            msg[10] <= b2a(led_s[0]);  // T = trig
            msg[14] <= b2a(led_s[1]);  // F = f_frm
            msg[18] <= b2a(led_s[3]);  // M = mode
            msg[22] <= b2a(led_s[2]);  // R = rdy
            msg[26] <= h2a(vs_lat[15:12]);
            msg[27] <= h2a(vs_lat[11:8]);
            msg[28] <= h2a(vs_lat[7:4]);
            msg[29] <= h2a(vs_lat[3:0]);
            msg[33] <= h2a(led_s[7:4]);
            msg[34] <= h2a(led_s[3:0]);
            msg[38] <= h2a(dbg_s[7:4]);
            msg[39] <= h2a(dbg_s[3:0]);
        end
    end

    // ---- UART transmitter (start + 8 data + stop, LSB first) ----
    reg        busy  = 1'b0;
    reg        start = 1'b0;
    reg [5:0]  idx   = 6'd0;
    reg [3:0]  bitc  = 4'd0;
    reg [15:0] baud  = 16'd0;
    reg [9:0]  sh    = 10'h3FF;   // line idles high
    assign usb_tx = sh[0];

    always @(posedge clk100) begin
        if (latch) start <= 1'b1;          // request a fresh line each window
        if (!busy) begin
            if (start) begin
                start <= 1'b0; busy <= 1'b1;
                idx <= 6'd0; bitc <= 4'd0; baud <= 16'd0;
                sh  <= {1'b1, msg[0], 1'b0};   // {stop, data, start}
            end
        end else begin
            if (baud == DIV-1) begin
                baud <= 16'd0;
                if (bitc == 4'd9) begin                 // stop bit elapsed
                    if (idx == LEN-1) busy <= 1'b0;     // whole line sent
                    else begin
                        idx  <= idx + 1'b1; bitc <= 4'd0;
                        sh   <= {1'b1, msg[idx+1], 1'b0};
                    end
                end else begin
                    bitc <= bitc + 1'b1;
                    sh   <= {1'b1, sh[9:1]};            // shift out next bit
                end
            end else baud <= baud + 1'b1;
        end
    end
endmodule
