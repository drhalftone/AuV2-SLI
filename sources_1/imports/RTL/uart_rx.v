`timescale 1ns/1ps
//==============================================================================
// uart_rx - simple 8N1 UART receiver, LSB first. Companion to uart_tx.v.
//
//   Idle line is high. A start bit (line low) is detected, re-checked at its
//   centre (false-start reject), then the 8 data bits are sampled at their
//   centres and one stop bit is waited out. On a complete byte `valid` pulses
//   high for one clk with `data` stable. No parity. The stop-bit LEVEL is not
//   enforced (robust against framing slop); the next start edge re-syncs.
//
//   Drive `rx` from the FT2232H channel-B receive pin (PC -> FPGA, P15).
//==============================================================================
module uart_rx #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   = 115200
)(
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg  [7:0] data,
    output reg        valid
);
    localparam integer DIV = CLK_HZ / BAUD;       // clocks per bit (868 @100M/115200)

    // 2-FF synchroniser for the asynchronous rx pin (idle high).
    reg rx_d0 = 1'b1, rx_d1 = 1'b1, rx_d2 = 1'b1;
    always @(posedge clk) begin
        rx_d0 <= rx; rx_d1 <= rx_d0; rx_d2 <= rx_d1;
    end

    localparam [1:0] S_IDLE = 2'd0, S_START = 2'd1, S_DATA = 2'd2, S_STOP = 2'd3;
    reg [1:0]  state = S_IDLE;
    reg [15:0] cnt   = 16'd0;
    reg [3:0]  bidx  = 4'd0;
    reg [7:0]  sh    = 8'd0;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE; valid <= 1'b0; cnt <= 16'd0; bidx <= 4'd0;
        end else begin
            valid <= 1'b0;                          // single-cycle strobe
            case (state)
                S_IDLE: begin
                    if (rx_d2 == 1'b0) begin        // falling edge -> start bit
                        state <= S_START; cnt <= 16'd0;
                    end
                end
                S_START: begin                       // sample at mid start-bit
                    if (cnt == (DIV/2) - 1) begin
                        if (rx_d2 == 1'b0) begin     // still low -> real start
                            cnt <= 16'd0; bidx <= 4'd0; state <= S_DATA;
                        end else begin
                            state <= S_IDLE;         // glitch -> abort
                        end
                    end else cnt <= cnt + 16'd1;
                end
                S_DATA: begin                        // sample each bit at its centre
                    if (cnt == DIV - 1) begin
                        cnt <= 16'd0;
                        sh  <= {rx_d2, sh[7:1]};     // LSB first
                        if (bidx == 4'd7) state <= S_STOP;
                        else bidx <= bidx + 4'd1;
                    end else cnt <= cnt + 16'd1;
                end
                S_STOP: begin                        // wait out one stop bit, then emit
                    if (cnt == DIV - 1) begin
                        cnt   <= 16'd0;
                        state <= S_IDLE;
                        data  <= sh;
                        valid <= 1'b1;
                    end else cnt <= cnt + 16'd1;
                end
            endcase
        end
    end
endmodule
