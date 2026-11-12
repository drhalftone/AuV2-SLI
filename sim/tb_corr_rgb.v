`timescale 1ns/1ps
//==============================================================================
// tb_corr_rgb - the per-channel corr tables and the impulse colour they make.
//
// Drives uart_ctrl at the BYTE level (rx_data/rx_valid in, tx_send/tx_data out):
// the UART itself is unchanged and covered elsewhere, and bytes keep this fast.
//
//   1. power-up: imp_col is white, corr_pat_dout is identity on all three channels
//   2. upload TGT 0x06 (768 B: red, green, blue curves) -> 'K'
//   3. read TGT 0x06 back: the same 768 bytes and a closing checksum
//   4. read TGT 0x02 back: the red curve, as before
//   5. corr_pat_dout: {corr_r, corr_g, corr_b} at the same address
//   6. imp_col / imp_col2 with IMPRGB / IMPRGB2 bit 3 off (level), on (curves),
//      and with channels masked off
//   7. upload TGT 0x02 (256 B): one curve lands in all three tables
//   8. a bad checksum and an unknown target are both answered 'E'
//   9. THETA (0x03/0x04): with sin_en, frame n's colour is c(THETA - 512 n) through the
//      mask and, with IMPRGB bit 3, through the curves; THETA reads back; sin_en off
//      restores the plain level. Expected levels computed outside the RTL (Python).
//  10. impulse_gen: 8-frame cycle with cyc8, the IMPCYC length without
//
// Run (from sim/):
//   xvlog tb_corr_rgb.v ../sources_1/imports/RTL/uart_ctrl.v ../sources_1/imports/RTL/impulse_gen.v
//   xelab -R tb_corr_rgb
//==============================================================================
module tb_corr_rgb;

    localparam [7:0] SYNC = 8'hA5, OP_W = 8'h57, OP_R = 8'h52, OP_L = 8'h5B, OP_LR = 8'h72;
    localparam [7:0] ACK_K = 8'h4B, ACK_E = 8'h45;

    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst = 1'b1;

    reg  [7:0] rx_data = 8'h00;
    reg        rx_valid = 1'b0;
    wire [7:0] tx_data;
    wire       tx_send;
    reg  [7:0] pat_addr = 8'h00;
    wire [23:0] pat_dout, imp_col, imp_col2;
    reg  [2:0]  phase_i = 3'd0;
    wire        sin_en;

    uart_ctrl #(.CLK_HZ(100_000_000)) dut (
        .clk(clk), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(tx_data), .tx_send(tx_send), .tx_busy(1'b0), .tx_active(),
        .led(8'h00), .pins(8'h00),
        .corr_addr(8'h00), .corr_dout(),
        .corr_pat_addr(pat_addr), .corr_pat_dout(pat_dout),
        .imp_col(imp_col), .imp_col2(imp_col2),
        .imp_phase_i(phase_i), .sin_en(sin_en), .theta()
    );

    // impulse_gen on the same clock, vsync driven by the test
    reg        vs = 1'b0, cyc8 = 1'b0;
    wire [2:0] ig_phase;
    impulse_gen #(.CYCLE(5)) ig (
        .pclk(clk), .vsync_pos(vs), .en(1'b1), .lvl(8'hFF), .cyc(3'd5), .bpos(3'd0),
        .inv(1'b0), .cyc8(cyc8), .level(), .phase(ig_phase), .phase0());

    integer errors = 0, checks = 0;

    // every byte the controller sends, in order
    reg [7:0] txq [0:2047];
    integer   txn = 0;
    always @(posedge clk) if (tx_send) begin txq[txn] <= tx_data; txn <= txn + 1; end

    task send(input [7:0] b);
    begin
        @(posedge clk); rx_data <= b; rx_valid <= 1'b1;
        @(posedge clk); rx_valid <= 1'b0;
        repeat (4) @(posedge clk);
    end
    endtask

    task expect_eq(input [255:0] what, input [31:0] got, input [31:0] want);
    begin
        checks = checks + 1;
        if (got !== want) begin
            errors = errors + 1;
            $display("*** FAIL: %0s: got 0x%06h, want 0x%06h", what, got, want);
        end
    end
    endtask

    // wait until n reply bytes have arrived from index `first` (txn before the request)
    task wait_tx(input integer n, input integer first);
        integer guard;
    begin
        guard = 0;
        while (txn < first + n && guard < 200000) begin
            @(posedge clk); guard = guard + 1;
        end
        if (txn < first + n) begin
            errors = errors + 1;
            $display("*** FAIL: timed out waiting for %0d reply bytes", n);
        end
    end
    endtask

    task wr(input [7:0] a, input [7:0] d);
        integer f;
    begin
        f = txn;
        send(SYNC); send(OP_W); send(a); send(d); send(8'h00 - (OP_W + a + d));
        wait_tx(1, f);
        expect_eq("write ack", txq[f], ACK_K);
        repeat (4) @(posedge clk);                   // imp_col is registered
    end
    endtask

    task rd(input [7:0] a, output [7:0] d);
        integer f;
    begin
        f = txn;
        send(SYNC); send(OP_R); send(a); send(8'h00 - (OP_R + a));
        wait_tx(3, f);
        d = txq[f + 1];
    end
    endtask

    // c(THETA - 512 n) for n = 0..7, from Python, for THETA = 0x123 and 0xFFF (wraps)
    reg [7:0] s123 [0:7];
    reg [7:0] sfff [0:7];
    initial begin
        s123[0]=243; s123[1]=248; s123[2]=183; s123[3]=85;
        s123[4]=12;  s123[5]=7;   s123[6]=72;  s123[7]=170;
        sfff[0]=255; sfff[1]=218; sfff[2]=127; sfff[3]=37;
        sfff[4]=0;   sfff[5]=37;  sfff[6]=128; sfff[7]=218;
    end

    // the three test curves
    function [7:0] cr(input integer i); cr = 8'd255 - i[7:0];      endfunction
    function [7:0] cg(input integer i); cg = i[7:0] >> 1;          endfunction
    function [7:0] cb(input integer i); cb = (i * 3) & 8'hFF;      endfunction
    function [7:0] c1(input integer i); c1 = i[7:0] ^ 8'h55;       endfunction

    integer i, f, k, n;
    reg [7:0] sum, rv;

    initial begin
        repeat (10) @(posedge clk);
        rst <= 1'b0;
        repeat (10) @(posedge clk);

        // ---- 1. power-up ------------------------------------------------------
        expect_eq("power-up imp_col (white)", imp_col, 24'hFFFFFF);
        expect_eq("power-up imp_col2 (white)", imp_col2, 24'hFFFFFF);
        pat_addr = 8'h3C; #1;
        expect_eq("power-up corr_pat_dout (identity)", pat_dout, 24'h3C3C3C);

        // ---- 2. upload 0x06 ---------------------------------------------------
        f = txn;
        send(SYNC); send(OP_L); send(8'h06);
        sum = 8'h06;
        for (i = 0; i < 256; i = i + 1) begin send(cr(i)); sum = sum + cr(i); end
        for (i = 0; i < 256; i = i + 1) begin send(cg(i)); sum = sum + cg(i); end
        for (i = 0; i < 256; i = i + 1) begin send(cb(i)); sum = sum + cb(i); end
        send(8'h00 - sum);
        wait_tx(1, f);
        expect_eq("upload 0x06 ack", txq[f], ACK_K);

        // ---- 3. read 0x06 back ------------------------------------------------
        f = txn;
        send(SYNC); send(OP_LR); send(8'h06); send(8'h00 - (OP_LR + 8'h06));
        wait_tx(1 + 768 + 1, f);
        expect_eq("readback 0x06 prologue", txq[f], 8'h06);
        k = 0;
        for (i = 0; i < 768; i = i + 1)
            if (txq[f + 1 + i] !== (i < 256 ? cr(i) : i < 512 ? cg(i - 256) : cb(i - 512))) begin
                if (k < 5) $display("*** FAIL: readback 0x06 byte %0d: 0x%02h", i, txq[f + 1 + i]);
                k = k + 1;
            end
        checks = checks + 1;
        if (k) errors = errors + 1;
        sum = 8'h06;
        for (i = 0; i <= 768; i = i + 1) sum = sum + txq[f + 1 + i];
        expect_eq("readback 0x06 checksum", sum, 8'h00);

        // ---- 4. read 0x02 back: the red curve ---------------------------------
        f = txn;
        send(SYNC); send(OP_LR); send(8'h02); send(8'h00 - (OP_LR + 8'h02));
        wait_tx(1 + 256 + 1, f);
        k = 0;
        for (i = 0; i < 256; i = i + 1) if (txq[f + 1 + i] !== cr(i)) k = k + 1;
        expect_eq("readback 0x02 = red curve (mismatches)", k, 0);

        // ---- 5. the fringe path -----------------------------------------------
        for (i = 0; i < 256; i = i + 37) begin
            pat_addr = i; #1;
            expect_eq("corr_pat_dout", pat_dout, {cr(i), cg(i), cb(i)});
        end

        // ---- 6. the impulse colours -------------------------------------------
        wr(8'h12, 8'h40);                            // IMPLVL = 64
        wr(8'h1F, 8'h07);                            // white, not through corr
        expect_eq("imp_col level, bit 3 off", imp_col, 24'h404040);
        wr(8'h1F, 8'h0F);                            // white through corr
        expect_eq("imp_col through corr", imp_col, {cr(64), cg(64), cb(64)});
        wr(8'h1F, 8'h0A);                            // green only, through corr
        expect_eq("imp_col green through corr", imp_col, {8'h00, cg(64), 8'h00});
        wr(8'h1F, 8'h05);                            // red + blue, level
        expect_eq("imp_col red+blue level", imp_col, 24'h400040);
        wr(8'h07, 8'hC8);                            // IMPLVL2 = 200
        wr(8'h08, 8'h0F);                            // IMPRGB2 white through corr
        expect_eq("imp_col2 through corr", imp_col2, {cr(200), cg(200), cb(200)});
        expect_eq("imp_col unchanged by IMPLVL2", imp_col, 24'h400040);

        // ---- 7. upload 0x02: one curve to all three ----------------------------
        f = txn;
        send(SYNC); send(OP_L); send(8'h02);
        sum = 8'h02;
        for (i = 0; i < 256; i = i + 1) begin send(c1(i)); sum = sum + c1(i); end
        send(8'h00 - sum);
        wait_tx(1, f);
        expect_eq("upload 0x02 ack", txq[f], ACK_K);
        for (i = 0; i < 256; i = i + 51) begin
            pat_addr = i; #1;
            expect_eq("corr_pat_dout after 0x02", pat_dout, {c1(i), c1(i), c1(i)});
        end
        repeat (4) @(posedge clk);
        expect_eq("imp_col2 after 0x02", imp_col2, {c1(200), c1(200), c1(200)});

        // ---- 8. rejects -------------------------------------------------------
        f = txn;
        send(SYNC); send(OP_L); send(8'h06);
        for (i = 0; i < 768; i = i + 1) send(8'h00);
        send(8'h01);                                 // wrong: the sum is 0x06
        wait_tx(1, f);
        expect_eq("upload 0x06 bad checksum -> E", txq[f], ACK_E);
        f = txn;
        send(SYNC); send(OP_L); send(8'h07);
        wait_tx(1, f);
        expect_eq("upload unknown target 0x07 -> E", txq[f], ACK_E);

        // ---- 9. the F = 0 sinusoid ---------------------------------------------
        // Step 8's rejected upload still wrote its bytes: the RAM is written as they
        // arrive and only the loaded flag waits for the checksum. Put c1 back.
        f = txn;
        send(SYNC); send(OP_L); send(8'h02);
        sum = 8'h02;
        for (i = 0; i < 256; i = i + 1) begin send(c1(i)); sum = sum + c1(i); end
        send(8'h00 - sum);
        wait_tx(1, f);
        expect_eq("re-upload 0x02 ack", txq[f], ACK_K);
        wr(8'h1F, 8'h07);                            // white, not through corr
        wr(8'h03, 8'h23);
        wr(8'h04, 8'h81);                            // sin_en, THETA = 0x123
        expect_eq("sin_en", sin_en, 1);
        rd(8'h03, rv); expect_eq("THETA[7:0] readback", rv, 8'h23);
        rd(8'h04, rv); expect_eq("0x04 readback", rv, 8'h81);
        for (n = 0; n < 8; n = n + 1) begin
            phase_i = n; repeat (4) @(posedge clk);
            expect_eq("sin 0x123 imp_col", imp_col, {3{s123[n]}});
            expect_eq("sin 0x123 imp_col2", imp_col2, {3{s123[n]}});
        end
        wr(8'h1F, 8'h0F);                            // through the curves (c1, from step 7)
        for (n = 0; n < 8; n = n + 1) begin
            phase_i = n; repeat (4) @(posedge clk);
            expect_eq("sin 0x123 through corr", imp_col, {3{c1(s123[n])}});
        end
        wr(8'h1F, 8'h02);                            // green only
        phase_i = 3'd2; repeat (4) @(posedge clk);
        expect_eq("sin green only", imp_col, {8'h00, s123[2], 8'h00});
        wr(8'h1F, 8'h07);
        wr(8'h03, 8'hFF);
        expect_eq("THETA staged, not yet applied", imp_col, {3{s123[2]}});
        wr(8'h04, 8'h8F);                            // THETA = 0xFFF: 0xFFF - 512 n wraps
        for (n = 0; n < 8; n = n + 1) begin
            phase_i = n; repeat (4) @(posedge clk);
            expect_eq("sin 0xFFF imp_col", imp_col, {3{sfff[n]}});
        end
        wr(8'h12, 8'h40);
        wr(8'h04, 8'h0F);                            // sin_en off
        expect_eq("sin_en off", sin_en, 0);
        expect_eq("sin_en off: plain level", imp_col, 24'h404040);

        // ---- 10. impulse_gen cycle length -------------------------------------
        k = 0;
        cyc8 = 1'b1;
        for (n = 0; n < 20; n = n + 1) begin
            @(posedge clk) vs <= 1'b1; repeat (3) @(posedge clk); vs <= 1'b0;
            repeat (3) @(posedge clk);
            if (ig_phase !== ((n + 1) % 8)) k = k + 1;
        end
        expect_eq("impulse_gen cyc8: phase 0..7 (mismatches)", k, 0);
        cyc8 = 1'b0; k = 0;
        @(posedge clk) vs <= 1'b1; repeat (3) @(posedge clk); vs <= 1'b0;   // wraps at 5
        repeat (3) @(posedge clk);
        for (n = 0; n < 12; n = n + 1) begin
            i = ig_phase;
            @(posedge clk) vs <= 1'b1; repeat (3) @(posedge clk); vs <= 1'b0;
            repeat (3) @(posedge clk);
            if (ig_phase !== ((i + 1) % 5)) k = k + 1;
        end
        expect_eq("impulse_gen cyc 5 without cyc8 (mismatches)", k, 0);

        if (errors == 0) $display("PASS: tb_corr_rgb, %0d checks", checks);
        else             $display("FAIL: tb_corr_rgb, %0d of %0d checks failed", errors, checks);
        $finish;
    end
endmodule
