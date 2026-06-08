`timescale 1ns/1ps
//==============================================================================
// uart_tx - simple 8N1 UART transmitter, LSB first.
//   Assert `send` for one cycle while `busy` is low to queue `data`.
//==============================================================================
module uart_tx #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   = 115200
)(
    input  wire       clk,
    input  wire       rst,
    input  wire [7:0] data,
    input  wire       send,
    output reg        tx,
    output reg        busy
);
    localparam integer DIV = CLK_HZ / BAUD;   // clocks per bit

    reg [15:0] cnt;
    reg [3:0]  bit_idx;
    reg [9:0]  shifter;   // {stop, data[7:0], start}

    initial begin tx = 1'b1; busy = 1'b0; end

    always @(posedge clk) begin
        if (rst) begin
            tx      <= 1'b1;
            busy    <= 1'b0;
            cnt     <= 0;
            bit_idx <= 0;
        end else if (!busy) begin
            tx <= 1'b1;
            if (send) begin
                shifter <= {1'b1, data, 1'b0};   // stop + data + start
                busy    <= 1'b1;
                cnt     <= 0;
                bit_idx <= 0;
            end
        end else begin
            if (cnt == DIV-1) begin
                cnt     <= 0;
                tx      <= shifter[0];
                shifter <= {1'b1, shifter[9:1]};
                bit_idx <= bit_idx + 1'b1;
                if (bit_idx == 4'd9) busy <= 1'b0;   // 10 bits sent
            end else begin
                cnt <= cnt + 1'b1;
            end
        end
    end
endmodule
