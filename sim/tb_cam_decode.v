`timescale 1ns/1ps
//=============================================================================
// tb_cam_decode - task #10 gate. Full receive chain, bit-exact.
//
//   python1300_lvds_model -> cam_lvds_rx -> cam_align -> cam_sync_decode
//
// Pushes a known 32x8 test image through the model, lets bitslip lock, decodes the framing,
// de-interleaves the kernels, and captures the output pixel stream into a frame buffer. Then
// compares to the source image BIT-EXACT -- not "looks like a picture". A period-4 comb (the
// de-interleave phase bug) fails here, including on the pinned corner pixels.
//
// Run: xvlog -d SIM sim/tb_cam_decode.v sim/python1300_lvds_model.v \
//        sources_1/imports/RTL/{cam_lvds_rx,cam_align,cam_sync_decode}.v <glbl.v>
//      xelab -L unisims_ver -L secureip -R tb_cam_decode work.glbl
//=============================================================================
module tb_cam_decode;
    localparam integer COLS=32, ROWS=8, BLACK_ROWS=2;

    reg enable=0, go_frame=0; wire busy, clkp,clkn,syp,syn; wire [3:0] dp,dn;
    python1300_lvds_model #(.COLS(COLS),.ROWS(ROWS),.BLACK_ROWS(BLACK_ROWS)) model (
        .enable(enable),.go_frame(go_frame),.busy(busy),
        .clock_out_p(clkp),.clock_out_n(clkn),.d_p(dp),.d_n(dn),.sync_p(syp),.sync_n(syn));

    wire [4:0] bitslip, lane_locked, lane_failed; wire aligned, wordclk;
    wire [9:0] d0w,d1w,d2w,d3w,syw;
    cam_lvds_rx rx (.cam_clkout_p(clkp),.cam_clkout_n(clkn),.cam_d_p(dp),.cam_d_n(dn),
        .cam_sync_p(syp),.cam_sync_n(syn),.bitslip(bitslip),.wordclk(wordclk),
        .d0_word(d0w),.d1_word(d1w),.d2_word(d2w),.d3_word(d3w),.sync_word(syw));

    reg align_rst=1'b1;
    cam_align aligner (.wordclk(wordclk),.rst(align_rst),
        .d0_word(d0w),.d1_word(d1w),.d2_word(d2w),.d3_word(d3w),.sync_word(syw),
        .bitslip(bitslip),.lane_locked(lane_locked),.aligned(aligned),.lane_failed(lane_failed));

    wire [9:0] kp0,kp1,kp2,kp3,kp4,kp5,kp6,kp7; wire [10:0] kbase;
    wire kvalid, line_start, frame_start, in_black;
    cam_sync_decode decode (.wordclk(wordclk),.rst(1'b0),.aligned(aligned),
        .d0_word(d0w),.d1_word(d1w),.d2_word(d2w),.d3_word(d3w),.sync_word(syw),
        .kpix0(kp0),.kpix1(kp1),.kpix2(kp2),.kpix3(kp3),
        .kpix4(kp4),.kpix5(kp5),.kpix6(kp6),.kpix7(kp7),
        .kbase(kbase),.kvalid(kvalid),.line_start(line_start),
        .frame_start(frame_start),.in_black(in_black));

    // capture kernels into a frame buffer: kpix[s] -> column kbase+s.
    // line_idx counts EVERY line incl. black ones (which emit no kvalid); the first
    // BLACK_ROWS are black, so image row = line_idx - BLACK_ROWS.
    reg [9:0] recon [0:ROWS*COLS-1];
    integer line_idx = -1, img_row, npix = 0;
    integer errors = 0, checks = 0;

    always @(posedge wordclk) begin
        if (frame_start) line_idx <= -1;
        if (line_start)  line_idx <= line_idx + 1;
        if (kvalid) begin
            img_row = line_idx - BLACK_ROWS;
            if (img_row >= 0 && img_row < ROWS) begin
                recon[img_row*COLS + kbase + 0] <= kp0;
                recon[img_row*COLS + kbase + 1] <= kp1;
                recon[img_row*COLS + kbase + 2] <= kp2;
                recon[img_row*COLS + kbase + 3] <= kp3;
                recon[img_row*COLS + kbase + 4] <= kp4;
                recon[img_row*COLS + kbase + 5] <= kp5;
                recon[img_row*COLS + kbase + 6] <= kp6;
                recon[img_row*COLS + kbase + 7] <= kp7;
                npix <= npix + 8;
            end
        end
    end

    integer i, r, c;
    reg [9:0] expv, gotv;

    initial begin
        for (r=0;r<ROWS;r=r+1) for (c=0;c<COLS;c=c+1)
            model.img[r*COLS+c] = ((r*COLS+c)*7 + (c<<3)) & 10'h3FF;
        model.img[0]              = 10'h3FF;
        model.img[COLS-1]         = 10'h001;
        model.img[(ROWS-1)*COLS]  = 10'h155;
        model.img[ROWS*COLS-1]    = 10'h2AA;
        for (i=0;i<ROWS*COLS;i=i+1) recon[i] = 10'h3FF ^ model.img[i];  // poison

        $display("=== tb_cam_decode: full chain, %0dx%0d, bit-exact ===", COLS, ROWS);
        #200; enable = 1'b1;
        repeat (40) @(posedge wordclk);
        align_rst = 1'b0;

        // wait for alignment
        begin : wa
            integer w;
            for (w=0; w<2000; w=w+1) begin @(posedge wordclk); if (aligned) disable wa; end
        end
        checks = checks + 1;
        if (!aligned) begin $display("*** FAIL: never aligned (locked=%b)", lane_locked); errors=errors+1; end
        else $display("    aligned (locked=%b)", lane_locked);

        // send a frame and wait for capture
        @(negedge clkp); go_frame = 1'b1; #10; go_frame = 1'b0;
        // wait until we've captured a full frame worth of pixels (or timeout)
        begin : wf
            integer w;
            for (w=0; w<20000; w=w+1) begin @(posedge wordclk); if (npix >= ROWS*COLS) disable wf; end
        end

        checks = checks + 1;
        if (npix != ROWS*COLS) begin
            $display("*** FAIL: captured %0d pixels, expected %0d", npix, ROWS*COLS);
            errors = errors + 1;
        end else $display("    captured all %0d pixels", npix);

        for (r=0;r<ROWS;r=r+1) for (c=0;c<COLS;c=c+1) begin
            expv = model.img[r*COLS+c]; gotv = recon[r*COLS+c];
            checks = checks + 1;
            if (gotv !== expv) begin
                errors = errors + 1;
                if (errors <= 16)
                    $display("*** FAIL: pixel (r%0d,c%0d): got 0x%03h exp 0x%03h", r,c,gotv,expv);
            end
        end
        if (errors == 0) $display("    PASS: all %0d pixels bit-exact", ROWS*COLS);

        $display(""); $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors==0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end
    initial begin #6_000_000; $display("*** FAIL: timeout npix=%0d aligned=%b", npix, aligned); $finish; end
endmodule
