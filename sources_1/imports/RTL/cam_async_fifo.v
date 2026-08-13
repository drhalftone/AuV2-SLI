`timescale 1ns/1ps
//=============================================================================
// cam_async_fifo.v - dual-clock FIFO, gray-coded pointers.
//
// The CDC that CAMERA_RTL_PLAN.md's streaming architecture puts between
// cam_sync_decode and the MIG. The camera runs on wordclk -- 72 MHz off a BUFR,
// a REGIONAL buffer -- and the MIG on ui_clk, 100 MHz from its own PLL. The two
// are unrelated: same nominal decade, no phase relationship, and ui_clk stops
// during DDR calibration. Anything that crosses between them needs this.
//
// video_phase_fifo.v in this repo is NOT a substitute: it free-runs with no
// wr_en/rd_en/full/empty, which is right for a phase-tracking video path and
// wrong for a burst that must not drop a pixel.
//
// Standard construction: binary counters for addressing, gray counters for
// crossing, two flip-flops each way. Pointers are AW+1 bits so full and empty
// are distinguishable -- with AW bits they alias and a full FIFO reads as empty,
// which loses a whole frame rather than one word.
//
// RATE. Kernels arrive one per 2 wordclks = 36 M/s; ui_clk drains at up to
// 100 M/s less DDR stalls, so this is a slack absorber for refresh and bank
// conflicts, not a rate matcher. 256 deep is ~7 us of camera data.
//=============================================================================
module cam_async_fifo #(
    parameter integer DW = 64,
    parameter integer AW = 8                  // depth = 2^AW
)(
    input  wire          wr_clk,
    input  wire          wr_rst,
    input  wire          wr_en,
    input  wire [DW-1:0] wr_data,
    output wire          full,
    output reg           overflow = 1'b0,     // sticky: a write was DROPPED

    input  wire          rd_clk,
    input  wire          rd_rst,
    input  wire          rd_en,
    output wire [DW-1:0] rd_data,
    output wire          empty
);
    localparam integer DEPTH = (1 << AW);

    (* ram_style = "block" *) reg [DW-1:0] mem [0:DEPTH-1];

    reg  [AW:0] wbin = 0, wgray = 0;
    reg  [AW:0] rbin = 0, rgray = 0;
    reg  [AW:0] wgray_s1 = 0, wgray_s2 = 0;   // into rd_clk
    reg  [AW:0] rgray_s1 = 0, rgray_s2 = 0;   // into wr_clk

    function [AW:0] bin2gray(input [AW:0] b); bin2gray = b ^ (b >> 1); endfunction

    wire [AW:0] wbin_next  = wbin + (wr_en && !full);
    wire [AW:0] rbin_next  = rbin + (rd_en && !empty);

    // full: pointers equal with the TOP TWO bits inverted
    assign full  = (wgray == {~rgray_s2[AW:AW-1], rgray_s2[AW-2:0]});
    assign empty = (rgray == wgray_s2);
    assign rd_data = mem[rbin[AW-1:0]];

    always @(posedge wr_clk) begin
        if (wr_rst) begin
            wbin <= 0; wgray <= 0; overflow <= 1'b0;
        end else begin
            if (wr_en && !full) mem[wbin[AW-1:0]] <= wr_data;
            if (wr_en && full)  overflow <= 1'b1;   // never silently
            wbin  <= wbin_next;
            wgray <= bin2gray(wbin_next);
        end
        rgray_s1 <= rgray;
        rgray_s2 <= rgray_s1;
    end

    always @(posedge rd_clk) begin
        if (rd_rst) begin
            rbin <= 0; rgray <= 0;
        end else begin
            rbin  <= rbin_next;
            rgray <= bin2gray(rbin_next);
        end
        wgray_s1 <= wgray;
        wgray_s2 <= wgray_s1;
    end
endmodule
