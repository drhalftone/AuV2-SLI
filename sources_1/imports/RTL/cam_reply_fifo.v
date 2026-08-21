`timescale 1ns/1ps
//==============================================================================
// cam_reply_fifo.v -- M6b: control-plane REPLY bytes, clk100 -> ui_clk.
//
// WHY NOT cam_async_fifo. That FIFO is first-word fall-through: rd_data is a
// combinational read of a LUTRAM array, which is what makes `ram_style="block"`
// ineffective on it (Vivado reports "Infeasible attribute" and the design uses
// 0 of 135 BRAMs). This path needs 2,048 bytes so a full table readback fits --
// 1,280 data + prologue + checksum -- and 2,048 x 8 in LUTRAM is roughly 64
// SLICEMs. The camera datapath is already the reason cfifo had to drop from
// AW=10 to AW=9, sitting near 600 of 688 SLICEMs in its clock region, so
// spending another 64 there is exactly the wrong trade.
//
// BRAM is free here: 7 of 270 RAMB18 used. So this is a deliberately SEPARATE
// module with a REGISTERED read, because a registered read is what lets the
// array infer as block RAM at all. Read latency is 1 cycle -- assert rd_en, data
// lands next cycle -- which the consumer must account for.
//
// rd_count is the read side's view of occupancy, derived from the synchronised
// write pointer. It is CONSERVATIVE: the pointer crossing lags, so it can only
// ever under-report. That is the safe direction -- the emitter sends fewer bytes
// this round and the remainder goes out on the next boundary.
//==============================================================================
module cam_reply_fifo #(
    parameter integer DW = 8,
    parameter integer AW = 11                   // 2048 entries
)(
    // ---- write side ----
    input  wire           wr_clk,
    input  wire           wr_rst,
    input  wire           wr_en,
    input  wire [DW-1:0]  wr_data,
    output wire           full,
    output reg            overflow = 1'b0,      // sticky: a byte was lost

    // ---- read side ----
    input  wire           rd_clk,
    input  wire           rd_rst,
    input  wire           rd_en,
    output reg  [DW-1:0]  rd_data = {DW{1'b0}}, // valid ONE cycle after rd_en
    output wire           empty,
    output wire [AW:0]    rd_count             // conservative occupancy
);
    localparam integer DEPTH = (1 << AW);

    (* ram_style = "block" *) reg [DW-1:0] mem [0:DEPTH-1];

    // Pointers are AW+1 bits so full and empty are distinguishable -- with AW
    // bits they alias and a full FIFO reads as empty.
    reg [AW:0] wbin = 0, wgray = 0;
    reg [AW:0] rbin = 0, rgray = 0;
    reg [AW:0] wgray_s1 = 0, wgray_s2 = 0;
    reg [AW:0] rgray_s1 = 0, rgray_s2 = 0;

    wire        wr_go      = wr_en && !full;
    wire [AW:0] wbin_next  = wbin + wr_go;
    wire [AW:0] wgray_next = (wbin_next >> 1) ^ wbin_next;

    wire        rd_go      = rd_en && !empty;
    wire [AW:0] rbin_next  = rbin + rd_go;
    wire [AW:0] rgray_next = (rbin_next >> 1) ^ rbin_next;

    assign full  = (wgray == {~rgray_s2[AW:AW-1], rgray_s2[AW-2:0]});
    assign empty = (rgray == wgray_s2);

    always @(posedge wr_clk) begin
        if (wr_go) mem[wbin[AW-1:0]] <= wr_data;
        if (wr_rst) begin
            wbin <= 0; wgray <= 0; overflow <= 1'b0;
        end else begin
            wbin  <= wbin_next;
            wgray <= wgray_next;
            if (wr_en && full) overflow <= 1'b1;
        end
        rgray_s1 <= rgray;
        rgray_s2 <= rgray_s1;
    end

    always @(posedge rd_clk) begin
        if (rd_go) rd_data <= mem[rbin[AW-1:0]];
        if (rd_rst) begin
            rbin <= 0; rgray <= 0;
        end else begin
            rbin  <= rbin_next;
            rgray <= rgray_next;
        end
        wgray_s1 <= wgray;
        wgray_s2 <= wgray_s1;
    end

    // Gray -> binary of the synchronised write pointer, so the read side can
    // size a reply packet before it starts emitting one.
    integer gi;
    reg [AW:0] wbin_s;
    always @* begin
        for (gi = 0; gi <= AW; gi = gi + 1)
            wbin_s[gi] = ^(wgray_s2 >> gi);
    end
    assign rd_count = wbin_s - rbin;
endmodule
