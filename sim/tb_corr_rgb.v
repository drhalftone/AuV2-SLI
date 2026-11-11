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
//
// Run (from sim/):
//   xvlog tb_corr_rgb.v ../sources_1/imports/RTL/uart_ctrl.v
//   xelab -R tb_corr_rgb
//==============================================================================
module tb_corr_rgb;

    localparam [7:0] SYNC = 8'hA5, OP_W = 8'h57, OP_L = 8'h5B, OP_LR = 8'h72;
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

    uart_ctrl #(.CLK_HZ(100_000_000)) dut (
        .clk(clk), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(tx_data), .tx_send(tx_send), .tx_busy(1'b0), .tx_active(),
        .led(8'h00), .pins(8'h00),
        .corr_addr(8'h00), .corr_dout(),
        .corr_pat_addr(pat_addr), .corr_pat_dout(pat_dout),
        .imp_col(imp_col), .imp_col2(imp_col2)
    );

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

    // the three test curves
    function [7:0] cr(input integer i); cr = 8'd255 - i[7:0];      endfunction
    function [7:0] cg(input integer i); cg = i[7:0] >> 1;          endfunction
    function [7:0] cb(input integer i); cb = (i * 3) & 8'hFF;      endfunction
    function [7:0] c1(input integer i); c1 = i[7:0] ^ 8'h55;       endfunction

    integer i, f, k;
    reg [7:0] sum;

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

        if (errors == 0) $display("PASS: tb_corr_rgb, %0d checks", checks);
        else             $display("FAIL: tb_corr_rgb, %0d of %0d checks failed", errors, checks);
        $finish;
    end
endmodule
