`timescale 1ns/1ps
//=============================================================================
// tb_cam_align - task #9 gate. model -> cam_lvds_rx -> cam_align.
//
// The automatic bitslip FSM must lock ALL FIVE lanes to the 0x3A6 training pattern from an
// UNKNOWN starting rotation, and under per-lane transmit skew. Because the model's word
// phase relative to the receiver's CLKDIV is set by when enable rises (a value swept below),
// each run starts at a different rotation -- so passing across the sweep means "locks from
// every rotation", which is the real gate.
//
// Run (with unisims, since cam_lvds_rx uses ISERDESE2):
//   xvlog -d SIM sim/tb_cam_align.v sim/python1300_lvds_model.v \
//         sources_1/imports/RTL/cam_lvds_rx.v sources_1/imports/RTL/cam_align.v <glbl.v>
//   xelab -L unisims_ver -L secureip -R tb_cam_align work.glbl
//=============================================================================
module tb_cam_align #(
    parameter integer ENABLE_DELAY = 0,      // extra ns before enable -> shifts word phase
    parameter real SKEW_D0=0.0, SKEW_D1=0.0, SKEW_D2=0.0, SKEW_D3=0.0, SKEW_SYNC=0.0
);
    localparam [9:0] TRAIN = 10'h3A6;

    reg enable = 0, go_frame = 0;
    wire busy, clkp, clkn, syp, syn;
    wire [3:0] dp, dn;

    python1300_lvds_model #(.COLS(32), .ROWS(8), .BLACK_ROWS(2),
        .SKEW_D0(SKEW_D0), .SKEW_D1(SKEW_D1), .SKEW_D2(SKEW_D2),
        .SKEW_D3(SKEW_D3), .SKEW_SYNC(SKEW_SYNC)) model (
        .enable(enable), .go_frame(go_frame), .busy(busy),
        .clock_out_p(clkp), .clock_out_n(clkn),
        .d_p(dp), .d_n(dn), .sync_p(syp), .sync_n(syn)
    );

    wire [4:0] bitslip, lane_locked, lane_failed;
    wire       aligned, wordclk;
    wire [9:0] d0w, d1w, d2w, d3w, syw;

    cam_lvds_rx rx (
        .cam_clkout_p(clkp), .cam_clkout_n(clkn),
        .cam_d_p(dp), .cam_d_n(dn), .cam_sync_p(syp), .cam_sync_n(syn),
        .bitslip(bitslip), .wordclk(wordclk),
        .d0_word(d0w), .d1_word(d1w), .d2_word(d2w), .d3_word(d3w), .sync_word(syw)
    );

    reg align_rst = 1'b1;
    cam_align aligner (
        .wordclk(wordclk), .rst(align_rst),
        .d0_word(d0w), .d1_word(d1w), .d2_word(d2w), .d3_word(d3w), .sync_word(syw),
        .bitslip(bitslip), .lane_locked(lane_locked),
        .aligned(aligned), .lane_failed(lane_failed)
    );

    integer errors = 0;

    initial begin
        $display("=== tb_cam_align: ENABLE_DELAY=%0d skew={%0.2f %0.2f %0.2f %0.2f %0.2f} ===",
                 ENABLE_DELAY, SKEW_D0, SKEW_D1, SKEW_D2, SKEW_D3, SKEW_SYNC);
        #(200 + ENABLE_DELAY);
        enable = 1'b1;
        // let the ISERDES reset lift and wclk settle before releasing the aligner
        repeat (40) @(posedge wordclk);
        align_rst = 1'b0;

        // wait for alignment, with a bounded timeout
        begin : wait_align
            integer w;
            for (w = 0; w < 2000; w = w + 1) begin
                @(posedge wordclk);
                if (aligned) disable wait_align;
                if (|lane_failed) begin
                    $display("*** FAIL: lane_failed=%b before alignment", lane_failed);
                    errors = errors + 1; disable wait_align;
                end
            end
        end

        if (!aligned) begin
            $display("*** FAIL: not aligned. locked=%b failed=%b words=%03h %03h %03h %03h %03h",
                     lane_locked, lane_failed, d0w,d1w,d2w,d3w,syw);
            errors = errors + 1;
        end else begin
            // confirm every lane really reads TRAIN, not just that the FSM says locked
            repeat (5) @(posedge wordclk);
            if (d0w!==TRAIN||d1w!==TRAIN||d2w!==TRAIN||d3w!==TRAIN||syw!==TRAIN) begin
                $display("*** FAIL: aligned but words != TRAIN: %03h %03h %03h %03h %03h",
                         d0w,d1w,d2w,d3w,syw);
                errors = errors + 1;
            end else
                $display("    PASS: all 5 lanes locked to 0x3A6 (locked=%b)", lane_locked);
        end

        if (errors == 0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end

    initial begin #3_000_000; $display("*** FAIL: timeout aligned=%b locked=%b", aligned, lane_locked); $finish; end
endmodule
