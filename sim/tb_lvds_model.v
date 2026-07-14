`timescale 1ns/1ps
//=============================================================================
// tb_lvds_model - golden loopback for python1300_lvds_model (task #7 gate).
//
// Proves the model emits a datasheet-conformant stream by deserialising the ACTUAL
// serial lanes (sampled on clock_out edges), decoding the sync framing, inverting the
// Figure 36 kernel map, and recovering the source image BIT-EXACT.
//
// The golden decoder is idealised: it counts clock_out edges from enable, so it knows the
// model's word phase. That is legitimate for a conformance/golden check -- the UNKNOWN
// phase case is task #9's bitslip, tested against this same model later. What is NOT
// idealised here is the deserialisation: it captures real DDR bits off the wire, so a
// serialisation bug (wrong bit order, wrong DDR mapping) fails here.
//
// Run: xvlog -d SIM sim/tb_lvds_model.v sim/python1300_lvds_model.v
//      xelab -R tb_lvds_model
//=============================================================================
module tb_lvds_model #(
    // per-lane transmit skew (ns), overridable so the same TB proves the skew path works.
    // The golden samples at bit centre, so any skew < BIT/2 (~0.69 ns) still recovers --
    // a working skew path passes, a broken one corrupts only the skewed lane. Task #9
    // cranks these against the REAL receiver, where bitslip/IDELAY must absorb them.
    parameter real SKEW_D0=0.0, SKEW_D1=0.0, SKEW_D2=0.0, SKEW_D3=0.0, SKEW_SYNC=0.0
);

    localparam integer COLS = 32, ROWS = 8, BLACK_ROWS = 2;
    localparam integer NKER = COLS / 8;

    // sync codes (must match the model / datasheet)
    localparam [9:0] SC_FS=10'h2AA, SC_FE=10'h32A, SC_LS=10'h0AA, SC_LE=10'h12A;
    localparam [9:0] SC_BL=10'h015, SC_IMG=10'h035, SC_CRC=10'h059, SC_TR=10'h3A6;

    reg enable = 1'b0;
    reg go_frame = 1'b0;
    wire busy;
    wire clock_out_p, clock_out_n, sync_p, sync_n;
    wire [3:0] d_p, d_n;

    python1300_lvds_model #(.COLS(COLS), .ROWS(ROWS), .BLACK_ROWS(BLACK_ROWS),
        .SKEW_D0(SKEW_D0), .SKEW_D1(SKEW_D1), .SKEW_D2(SKEW_D2),
        .SKEW_D3(SKEW_D3), .SKEW_SYNC(SKEW_SYNC)) dut (
        .enable(enable), .go_frame(go_frame), .busy(busy),
        .clock_out_p(clock_out_p), .clock_out_n(clock_out_n),
        .d_p(d_p), .d_n(d_n), .sync_p(sync_p), .sync_n(sync_n)
    );

    integer errors = 0, checks = 0;

    //========================================================================
    // Golden deserialiser: sample all 5 lanes on every clock_out edge (DDR).
    // clock_out edges land at bit centres (the model offsets by BIT/2), so edge k
    // samples bit k. 10 bits = 1 word, MSB first.
    //========================================================================
    integer ecnt = 0;
    reg [9:0] sh0=0, sh1=0, sh2=0, sh3=0, shS=0;

    always @(clock_out_p) begin
        if (!enable) begin
            ecnt = 0;
        end else begin
            sh0 = {sh0[8:0], d_p[0]};   // MSB-first: first bit ends up at [9]
            sh1 = {sh1[8:0], d_p[1]};
            sh2 = {sh2[8:0], d_p[2]};
            sh3 = {sh3[8:0], d_p[3]};
            shS = {shS[8:0], sync_p};
            ecnt = ecnt + 1;
            if (ecnt % 10 == 0) begin
                process_word(sh0, sh1, sh2, sh3, shS);
            end
        end
    end

    //========================================================================
    // Golden framing FSM + kernel reassembly.
    //========================================================================
    localparam [2:0] G_IDLE=0, G_AFTER_FS=1, G_LINE_WAIT=2, G_AFTER_LS=3,
                     G_LINE=4, G_LINE_END=5, G_DONE=6;
    reg [2:0]  gstate = G_IDLE;

    reg [9:0]  recon [0:ROWS*COLS-1];
    // per-line pixel-word buffer (2 words per kernel)
    reg [9:0]  lb0 [0:2*NKER-1], lb1 [0:2*NKER-1], lb2 [0:2*NKER-1], lb3 [0:2*NKER-1];
    integer    licnt = 0;
    reg        had_img = 1'b0;
    integer    img_row = 0;
    integer    frame_done = 0;

    task reconstruct_row(input integer row);
        integer k, kb, i, wa, wb;
        reg [9:0] p [0:7];
    begin
        for (k = 0; k < NKER; k = k + 1) begin
            kb = k * 8;
            wa = 2*k; wb = 2*k + 1;
            // wordA lanes -> positions {0,2,4,6}; wordB lanes -> {1,3,5,7}
            p[0]=lb0[wa]; p[2]=lb1[wa]; p[4]=lb2[wa]; p[6]=lb3[wa];
            p[1]=lb0[wb]; p[3]=lb1[wb]; p[5]=lb2[wb]; p[7]=lb3[wb];
            // invert the even/odd kernel reversal
            for (i = 0; i < 8; i = i + 1) begin
                if (k[0] == 1'b0) recon[row*COLS + kb + i] = p[i];
                else              recon[row*COLS + kb + i] = p[7 - i];
            end
        end
    end
    endtask

    task process_word(input [9:0] d0, d1, d2, d3, sync);
    begin
        case (gstate)
            G_IDLE:      if (sync == SC_FS) gstate = G_AFTER_FS;
            G_AFTER_FS:  gstate = G_LINE_WAIT;                 // consume window-ID word
            G_LINE_WAIT: begin
                if      (sync == SC_LS) gstate = G_AFTER_LS;
                else if (sync == SC_FE) begin gstate = G_DONE; frame_done = 1; end
            end
            G_AFTER_LS: begin gstate = G_LINE; licnt = 0; had_img = 1'b0; end
            G_LINE: begin
                if (sync == SC_IMG) begin
                    lb0[licnt]=d0; lb1[licnt]=d1; lb2[licnt]=d2; lb3[licnt]=d3;
                    licnt = licnt + 1; had_img = 1'b1;
                end else if (sync == SC_BL) begin
                    // black reference line -- ignored
                end else if (sync == SC_LE) begin
                    if (had_img) begin reconstruct_row(img_row); img_row = img_row + 1; end
                    gstate = G_LINE_END;
                end
            end
            G_LINE_END: gstate = G_LINE_WAIT;                  // consume CRC word
            G_DONE: ;
        endcase
    end
    endtask

    //========================================================================
    // Stimulus
    //========================================================================
    integer i, r, c;
    reg [9:0] expv, gotv;

    initial begin
        // ---- fill the model's image with a known, structured pattern ----
        // value = distinctive per (row,col), full 10-bit range, plus a hard edge and the
        // corners pinned so a de-interleave phase error can't hide.
        for (r = 0; r < ROWS; r = r + 1)
            for (c = 0; c < COLS; c = c + 1)
                dut.img[r*COLS + c] = ((r*COLS + c) * 7 + (c << 3)) & 10'h3FF;
        // pin recognisable landmarks
        dut.img[0]                 = 10'h3FF;   // first pixel, first line
        dut.img[COLS-1]            = 10'h001;   // last pixel, first line
        dut.img[(ROWS-1)*COLS]     = 10'h155;   // first pixel, last line
        dut.img[ROWS*COLS-1]       = 10'h2AA;   // last pixel, last line
        for (i = 0; i < ROWS*COLS; i = i + 1) recon[i] = 10'h3FF ^ dut.img[i]; // poison

        $display("=== tb_lvds_model: %0dx%0d image, %0d black lines ===", COLS, ROWS, BLACK_ROWS);

        // ---- cold start: enable was low; nothing should have clocked ----
        #100;
        checks = checks + 1;
        if (ecnt != 0) begin
            $display("*** FAIL: %0d clock edges before enable -- sensor must be silent at cold start", ecnt);
            errors = errors + 1;
        end else $display("    PASS: silent before enable (cold start)");

        // ---- power up, let training run, then send one frame ----
        enable = 1'b1;
        #500;                          // training/idle for a while (bitslip would lock here)
        @(negedge clock_out_p);
        go_frame = 1'b1; #10; go_frame = 1'b0;

        // wait for the golden FSM to see frame end
        wait (frame_done == 1);
        #200;

        // ---- compare recovered image to source, BIT-EXACT ----
        checks = checks + 1;
        if (img_row != ROWS) begin
            $display("*** FAIL: recovered %0d image rows, expected %0d", img_row, ROWS);
            errors = errors + 1;
        end else $display("    PASS: recovered all %0d image rows", ROWS);

        for (r = 0; r < ROWS; r = r + 1) begin
            for (c = 0; c < COLS; c = c + 1) begin
                expv = dut.img[r*COLS + c];
                gotv = recon[r*COLS + c];
                checks = checks + 1;
                if (gotv !== expv) begin
                    errors = errors + 1;
                    if (errors <= 12)
                        $display("*** FAIL: pixel (r%0d,c%0d): got 0x%03h, expected 0x%03h",
                                 r, c, gotv, expv);
                end
            end
        end

        if (errors == 0)
            $display("    PASS: all %0d pixels recovered bit-exact", ROWS*COLS);

        $display("");
        $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end

    initial begin
        #5_000_000;
        $display("*** FAIL: timeout (frame_done=%0d img_row=%0d)", frame_done, img_row);
        $finish;
    end
endmodule
