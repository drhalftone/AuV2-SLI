`timescale 1ns/1ps
//==============================================================================
// debug_status.v -- periodic ASCII telemetry over the Pt's USB serial (COM).
//
// Runs entirely in the Pt's OWN 100 MHz oscillator domain (which is alive even
// when the FT601 isn't clocking us), so it can report the health of the FT601
// FIFO path over the COM port. Every ~0.5 s it transmits one line:
//
//   FTCLK=x TXE=x WR=xxxxxxxx FR=xxxxxxxx\r\n
//
//   FTCLK : 1 = ft_clk is toggling (FT601 is clocking the FPGA), 0 = dead
//   TXE   : current TXE# level (1 = FT601 NOT accepting writes)
//   WR    : words accepted by the FT601 so far (hex)  -- frozen at 0 if no ft_clk
//   FR    : frame index generated so far (hex)        -- frozen at 0 if no ft_clk
//
// 115200 8N1. `ftclk_alive` is computed by the caller (top) from an ft_clk-domain
// toggling bit re-timed into this clock; WR/FR are ft_clk-domain counters sampled
// asynchronously at each report (cosmetic tearing only).
//==============================================================================
module debug_status #(
    parameter integer CLK_HZ    = 100_000_000,
    parameter integer BAUD      = 115200,
    parameter integer PERIOD    = 50_000_000     // ~0.5 s between lines
)(
    input  wire        clk,
    input  wire        rst,
    input  wire        ftclk_alive,
    input  wire        txe,
    input  wire [31:0] word_ctr,
    input  wire [31:0] frame_index,
    output wire        tx
);
    localparam integer LEN = 39;                 // bytes in the line (incl CR LF)

    // ---- UART ----
    reg  [7:0] ubyte;
    reg        usend;
    wire       ubusy;
    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_uart (
        .clk(clk), .rst(rst), .data(ubyte), .send(usend), .tx(tx), .busy(ubusy)
    );

    // ---- latched snapshot for the line currently being sent ----
    reg        ftclk_lat, txe_lat;
    reg [31:0] w_lat, f_lat;

    function [7:0] hexch(input [3:0] n);
        hexch = (n < 4'd10) ? (8'h30 + n) : (8'h41 + n - 4'd10);   // '0'..'9','A'..'F'
    endfunction

    // combinational: the byte for character position `pos` of the line
    reg [7:0] mb;
    reg [2:0] nyb;   // nibble index within a 32-bit field (0..7)
    always @(*) begin
        mb  = 8'h20;                                  // default space
        nyb = 3'd0;                                   // default (avoid latch)
        case (pos)
            6'd0:  mb = "F";   6'd1:  mb = "T";  6'd2: mb = "C";  6'd3: mb = "L";
            6'd4:  mb = "K";   6'd5:  mb = "=";
            6'd6:  mb = ftclk_lat ? "1" : "0";
            6'd7:  mb = " ";
            6'd8:  mb = "T";   6'd9:  mb = "X";  6'd10: mb = "E"; 6'd11: mb = "=";
            6'd12: mb = txe_lat ? "1" : "0";
            6'd13: mb = " ";
            6'd14: mb = "W";   6'd15: mb = "R";  6'd16: mb = "=";
            6'd25: mb = " ";
            6'd26: mb = "F";   6'd27: mb = "R";  6'd28: mb = "=";
            6'd37: mb = 8'h0D;                        // CR
            6'd38: mb = 8'h0A;                        // LF
            default: begin
                if (pos >= 6'd17 && pos <= 6'd24) begin
                    nyb = 6'd24 - pos;                // MSN first
                    mb  = hexch(w_lat[{nyb,2'b00} +: 4]);
                end else if (pos >= 6'd29 && pos <= 6'd36) begin
                    nyb = 6'd36 - pos;
                    mb  = hexch(f_lat[{nyb,2'b00} +: 4]);
                end
            end
        endcase
    end

    // ---- sequencer ----
    reg [25:0] timer   = 26'd0;
    reg [5:0]  pos     = 6'd0;
    reg        sending = 1'b0;

    always @(posedge clk) begin
        if (rst) begin
            timer <= 0; pos <= 0; sending <= 0; usend <= 0;
        end else begin
            usend <= 1'b0;
            ubyte <= mb;                              // present current char
            if (!sending) begin
                if (timer == PERIOD-1) begin
                    timer     <= 0;
                    ftclk_lat <= ftclk_alive;         // snapshot
                    txe_lat   <= txe;
                    w_lat     <= word_ctr;
                    f_lat     <= frame_index;
                    pos       <= 0;
                    sending   <= 1'b1;
                end else begin
                    timer <= timer + 1'b1;
                end
            end else begin
                if (usend) begin
                    // the byte just queued (busy was low) has been accepted; advance
                    if (pos == LEN-1) sending <= 1'b0;
                    else              pos     <= pos + 1'b1;
                end else if (!ubusy) begin
                    usend <= 1'b1;                     // queue mb for `pos`
                end
            end
        end
    end
endmodule
