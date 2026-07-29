`timescale 1ns/1ps
//==============================================================================
// blink.v -- "is the board programmed?" sanity blink. Pure onboard-oscillator
// LED chaser (Knight Rider bounce across led[7:0]). NO FT601 pins, no ft_clk --
// if this sweeps, the FPGA configures and runs and the fault is isolated to the
// FT601 clock path.
//==============================================================================
module blink (
    input  wire       clk,        // W19 onboard 100 MHz
    input  wire       rst_n,      // N15 (unused; free-running)
    output wire [7:0] led
);
    reg [31:0] cnt = 32'd0;
    reg [3:0]  idx = 4'd0;        // 0..13 bounce sequence over 8 LEDs
    always @(posedge clk) begin
        if (cnt == 32'd8_000_000) begin      // ~0.08 s per step @100 MHz
            cnt <= 32'd0;
            idx <= (idx == 4'd13) ? 4'd0 : idx + 4'd1;
        end else begin
            cnt <= cnt + 32'd1;
        end
    end
    // position: 0->7 then 6->1 (bounce), 14 states total
    wire [3:0] pos = (idx <= 4'd7) ? idx : (4'd14 - idx);
    assign led = (8'd1 << pos);
endmodule
