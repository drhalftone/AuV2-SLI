`timescale 1ns / 1ps
//============================================================================
// led_sign_top.v -- "sign of life" LED scanner for the Alchitry Au V2.
//
// A single lit LED slides led0 -> led7 and wraps, driven only by the on-board
// 100 MHz clock. Needs NO HDMI, NO camera, NO host -- so it is an unambiguous
// "the .bin loaded and the FPGA is configured and running" indicator.
//
//   step period = 2^DIVBITS / 100 MHz.  DIVBITS=24 -> ~168 ms/step (~6 Hz),
//   a clear, easily-seen left-to-right march.
//============================================================================
module led_sign_top #(
    parameter integer DIVBITS = 24
)(
    input  wire       clk100,
    output wire [7:0] led
);
    reg [DIVBITS-1:0] div = {DIVBITS{1'b0}};
    reg [2:0]         pos = 3'd0;          // 0..7, wraps -> continuous slide

    always @(posedge clk100) begin
        div <= div + 1'b1;
        if (div == {DIVBITS{1'b0}})        // one tick per 2^DIVBITS cycles
            pos <= pos + 3'd1;             // advance the lit position
    end

    assign led = 8'b0000_0001 << pos;      // exactly one LED lit, led0 -> led7
endmodule
