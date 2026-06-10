`timescale 1ns/1ps
//==============================================================================
// video_phase_fifo.v -- elastic phase-compensation buffer for the recovered HDMI
// video {blank,hsync,vsync,red,green,blue} (27b) crossing from the DECODE clock
// domain (wclk = pixel_clk_i, a BUFG of the recovered MMCM CLKOUT0) into the
// OUTPUT/serialiser clock domain (rclk = oclk, a BUFGMUX of the SAME CLKOUT0).
//
// In passthrough the two clocks are the SAME frequency with a fixed but unknown
// phase (different buffers: BUFG vs BUFGMUX). The previous plain register crossing
// in the Au2_SLI mux was metastable -> a black line that drifted across the screen
// plus brief sync dropouts. This buffer writes every wclk and, once primed to about
// half depth, reads every rclk; identical rates hold a constant fill so it never
// under/overflows after priming. The write pointer is gray-coded across the CDC.
//
// Offline mode (rclk != wclk) makes the fill drift; the safe-band guard simply
// re-primes and the (unused-in-offline) output recovers cleanly on return to
// passthrough -- the in_* path is only consumed when sel=1 (passthrough).
//==============================================================================
module video_phase_fifo #(
    parameter integer DW = 27,
    parameter integer AW = 4                 // depth 2^AW = 16
)(
    input  wire           wclk,
    input  wire [DW-1:0]  wdata,
    input  wire           rclk,
    output reg  [DW-1:0]  rdata
);
    localparam integer DEPTH = (1 << AW);

    (* ram_style = "distributed" *) reg [DW-1:0] mem [0:DEPTH-1];

    // ---------- write side (wclk): free-running ----------
    reg [AW-1:0] wbin  = {AW{1'b0}};
    reg [AW-1:0] wgray = {AW{1'b0}};
    always @(posedge wclk) begin
        mem[wbin] <= wdata;
        wbin  <= wbin + 1'b1;
        // gray code of the NEXT write pointer (one bit changes per increment)
        wgray <= (wbin + 1'b1) ^ ((wbin + 1'b1) >> 1);
    end

    // ---------- wgray -> rclk domain (2-FF synchroniser) ----------
    reg [AW-1:0] wg0 = {AW{1'b0}}, wg1 = {AW{1'b0}};
    always @(posedge rclk) begin wg0 <= wgray; wg1 <= wg0; end

    // gray -> binary (combinational)
    integer gi;
    reg [AW-1:0] wbin_r;
    always @(*) begin
        wbin_r[AW-1] = wg1[AW-1];
        for (gi = AW-2; gi >= 0; gi = gi - 1)
            wbin_r[gi] = wbin_r[gi+1] ^ wg1[gi];
    end

    // ---------- read side (rclk) ----------
    reg [AW-1:0] rbin   = {AW{1'b0}};
    reg          primed = 1'b0;
    wire [AW-1:0] fill = wbin_r - rbin;        // approx occupancy (mod DEPTH)
    always @(posedge rclk) begin
        if (!primed) begin
            rbin <= {AW{1'b0}};
            if (fill >= (DEPTH/2)) primed <= 1'b1;          // wait ~half full, then read
        end else if (fill == 0 || fill >= (DEPTH-1)) begin
            primed <= 1'b0;                                  // drifted (e.g. offline): re-prime
        end else begin
            rdata <= mem[rbin];
            rbin  <= rbin + 1'b1;
        end
    end
endmodule
