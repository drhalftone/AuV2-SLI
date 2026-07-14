`timescale 1ns/1ps
//==============================================================================
// tb_cam_mailbox - end-to-end test of the PYTHON 1300 SPI mailbox.
//
// Drives REAL serial bytes into usb_link's usb_rx at 115200 8N1 and decodes the
// replies off usb_tx. Nothing is bypassed: uart_rx -> uart_ctrl (0xA5 protocol,
// checksums, ACKs) -> mailbox regs -> cam_spi_master -> the PYTHON slave model ->
// back out through the readback regs -> uart_tx.
//
// The point: prove the exact byte sequence the Qt host will send actually returns
// 0x50D0. If this passes, task #5 on real hardware is the same bytes over a real
// COM port.
//
// Run: xvlog -d SIM sim/tb_cam_mailbox.v sim/python1300_spi_model.v \
//              sources_1/imports/RTL/{usb_link,uart_ctrl,uart_rx,uart_tx,status_line,cam_spi_master}.v
//      xelab -R tb_cam_mailbox
//==============================================================================
module tb_cam_mailbox;

    localparam integer CLK_HZ  = 100_000_000;
    localparam integer BAUD    = 115200;
    localparam real    BIT_NS  = 1.0e9 / BAUD;      // 8680.6 ns

    // 0xA5 protocol constants (uart_ctrl.v)
    localparam [7:0] SYNC = 8'hA5, OP_W = 8'h57, OP_R = 8'h52;
    localparam [7:0] ACK_K = 8'h4B, ACK_E = 8'h45, ACK_N = 8'h4E;

    reg clk = 1'b0;
    always #5 clk = ~clk;                            // 100 MHz

    reg  usb_rx = 1'b1;                              // idle high
    wire usb_tx;

    wire cam_sck, cam_mosi, cam_ss_n, cam_reset_n;
    wire [2:0] cam_trigger;
    wire miso;
    reg  [1:0] cam_monitor = 2'b00;

    integer errors = 0;
    integer checks = 0;

    //--------------------------------------------------------------------------
    // DUT: the whole control plane. WIN is set absurdly high so the periodic
    // status line never fires and corrupts our reply decoding.
    //--------------------------------------------------------------------------
    usb_link #(.CLK_HZ(CLK_HZ), .WIN(2_000_000_000)) dut (
        .clk100(clk),
        .led(8'h00), .dbg(8'h00), .mrg(8'h00),
        .tlp(8'h00), .tcnt(8'h00), .olp(8'h00),
        .usb_rx(usb_rx), .usb_tx(usb_tx),
        .phys_sw(4'h0), .eff_sw(4'h0),
        .sli_ctrl(), .sli_ctrl_en(), .lut_loaded(),
        .corr_addr(8'h00),  .corr_dout(),
        .lut_addr(10'h000), .lut_dout(),
        .lutv_addr(11'h000),.lutv_dout(),
        .edid_rd_addr(),    .edid_rd_data(8'h00),
        .mode_idx_i(4'h0), .mode_valid_i(1'b0), .mode_edid_ok_i(1'b0),
        .mode_refr_i(8'h00), .mode_hact_i(12'h000), .mode_vact_i(12'h000),
        .mode_pclk_i(17'h0), .mode_supp_i(14'h0),
        .corr_pat_addr(8'h00), .corr_pat_dout(),
        .mode_force(),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n), .cam_miso(miso),
        .cam_reset_n(cam_reset_n), .cam_trigger(cam_trigger), .cam_monitor(cam_monitor)
    );

    python1300_spi_model sensor (
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(miso)
    );
    pullup (miso);

    //==========================================================================
    // UART bit-banging
    //==========================================================================
    task uart_send(input [7:0] b);
        integer k;
    begin
        usb_rx = 1'b0;  #(BIT_NS);                   // start
        for (k = 0; k < 8; k = k + 1) begin
            usb_rx = b[k];  #(BIT_NS);               // LSB first
        end
        usb_rx = 1'b1;  #(BIT_NS);                   // stop
    end
    endtask

    task uart_recv(output [7:0] b);
        integer k;
    begin
        @(negedge usb_tx);                           // start bit
        #(BIT_NS * 1.5);                             // centre of bit 0
        for (k = 0; k < 8; k = k + 1) begin
            b[k] = usb_tx;
            if (k < 7) #(BIT_NS);
        end
        #(BIT_NS);                                   // ride out the stop bit
    end
    endtask

    //==========================================================================
    // 0xA5 protocol
    //==========================================================================
    // write:  A5 57 ADDR DATA CK    with (0x57 + ADDR + DATA + CK) == 0 mod 256
    task host_write(input [7:0] a, input [7:0] d);
        reg [7:0] ck, ack;
    begin
        ck = 8'h00 - (OP_W + a + d);
        uart_send(SYNC); uart_send(OP_W); uart_send(a); uart_send(d); uart_send(ck);
        uart_recv(ack);
        checks = checks + 1;
        if (ack !== ACK_K) begin
            $display("*** FAIL: write 0x%02h <= 0x%02h: ack 0x%02h (expected 'K')", a, d, ack);
            errors = errors + 1;
        end
    end
    endtask

    // read:   A5 52 ADDR CK    reply: ADDR DATA CK2  with (ADDR+DATA+CK2) == 0
    task host_read(input [7:0] a, output [7:0] d);
        reg [7:0] ck, ra, rd, rck;
    begin
        ck = 8'h00 - (OP_R + a);
        uart_send(SYNC); uart_send(OP_R); uart_send(a); uart_send(ck);
        uart_recv(ra); uart_recv(rd); uart_recv(rck);
        checks = checks + 1;
        if (ra !== a) begin
            $display("*** FAIL: read 0x%02h: reply echoed addr 0x%02h", a, ra);
            errors = errors + 1;
        end
        checks = checks + 1;
        if (((ra + rd + rck) & 8'hFF) !== 8'h00) begin
            $display("*** FAIL: read 0x%02h: bad reply checksum (%02h %02h %02h)", a, ra, rd, rck);
            errors = errors + 1;
        end
        d = rd;
    end
    endtask

    // Stage operands, fire, poll until done. This IS the host-side sequence.
    task sensor_spi(input rw, input [8:0] sa, input [15:0] wd, output [15:0] rdv);
        reg [7:0] st, lo, hi;
        integer   guard;
    begin
        host_write(8'h30, sa[7:0]);
        host_write(8'h31, {rw, 6'b0, sa[8]});
        if (rw) begin
            host_write(8'h32, wd[7:0]);
            host_write(8'h33, wd[15:8]);
        end
        host_write(8'h34, 8'h01);                    // GO
        guard = 0;
        st = 8'h00;
        while (!(st[7] == 1'b0 && st[6] == 1'b1) && guard < 20) begin
            host_read(8'h34, st);                    // {busy, done, 6'b0}
            guard = guard + 1;
        end
        checks = checks + 1;
        if (guard >= 20) begin
            $display("*** FAIL: SPI never completed (status 0x%02h)", st);
            errors = errors + 1;
        end
        host_read(8'h35, lo);
        host_read(8'h36, hi);
        rdv = {hi, lo};
    end
    endtask

    task expect8(input [255:0] name, input [7:0] got, input [7:0] exp);
    begin
        checks = checks + 1;
        if (got !== exp) begin
            $display("*** FAIL: %0s: got 0x%02h, expected 0x%02h", name, got, exp);
            errors = errors + 1;
        end else $display("    PASS: %0s = 0x%02h", name, got);
    end
    endtask

    task expect16(input [255:0] name, input [15:0] got, input [15:0] exp);
    begin
        checks = checks + 1;
        if (got !== exp) begin
            $display("*** FAIL: %0s: got 0x%04h, expected 0x%04h", name, got, exp);
            errors = errors + 1;
        end else $display("    PASS: %0s = 0x%04h", name, got);
    end
    endtask

    //==========================================================================
    // Stimulus
    //==========================================================================
    reg [15:0] v;
    reg [7:0]  b;

    initial begin
        $display("=== tb_cam_mailbox: 0xA5 over 115200 8N1 -> SPI -> PYTHON 1300 ===");
        usb_rx = 1'b1;
        repeat (50) @(posedge clk);

        // ---- the sensor must be held in reset until the host says otherwise ----
        checks = checks + 1;
        if (cam_reset_n !== 1'b0) begin
            $display("*** FAIL: cam_reset_n = %b at power-up; sensor must be HELD IN RESET", cam_reset_n);
            errors = errors + 1;
        end else $display("    PASS: cam_reset_n = 0 at power-up (sensor held in reset)");

        // ---- release reset via reg 0x37 {reset_n, 4'b0, trigger[2:0]} ----
        host_write(8'h37, 8'h80);
        host_read (8'h37, b);  expect8("reg 0x37 readback", b, 8'h80);
        checks = checks + 1;
        if (cam_reset_n !== 1'b1) begin
            $display("*** FAIL: cam_reset_n did not release");
            errors = errors + 1;
        end else $display("    PASS: cam_reset_n released by reg 0x37");

        // ================= THE HARDWARE GATE, over the real wire =================
        sensor_spi(1'b0, 9'd0, 16'h0000, v);
        expect16("CHIP ID (sensor reg 0)", v, 16'h50D0);
        // =========================================================================

        // ---- a known-default read that is NOT address zero (proves addr decode) ----
        sensor_spi(1'b0, 9'd116, 16'h0000, v);
        expect16("training pattern (sensor reg 116)", v, 16'h03A6);

        // ---- write / read-back through the mailbox ----
        sensor_spi(1'b1, 9'd116, 16'h0155, v);
        sensor_spi(1'b0, 9'd116, 16'h0000, v);
        expect16("sensor reg 116 after write", v, 16'h0155);

        // ---- 9-bit address: bit 8 must survive reg 0x31 ----
        sensor_spi(1'b1, 9'd511, 16'hBEEF, v);
        sensor_spi(1'b0, 9'd511, 16'h0000, v);
        expect16("sensor reg 511 (addr bit 8)", v, 16'hBEEF);

        // ---- staged operands read back before firing ----
        host_write(8'h30, 8'h74);
        host_write(8'h31, 8'h81);                    // rw=1, addr[8]=1  -> addr 0x174
        host_read (8'h30, b);  expect8("reg 0x30 staged", b, 8'h74);
        host_read (8'h31, b);  expect8("reg 0x31 staged", b, 8'h81);

        // ---- monitor pins (reg 0x38, read-only) ----
        cam_monitor = 2'b10;
        repeat (10) @(posedge clk);
        host_read(8'h38, b);  expect8("reg 0x38 monitor pins", b, 8'h02);

        // ---- triggers come out of reg 0x37[2:0] ----
        host_write(8'h37, 8'h85);                    // reset_n=1, trigger=3'b101
        repeat (5) @(posedge clk);
        checks = checks + 1;
        if (cam_trigger !== 3'b101) begin
            $display("*** FAIL: cam_trigger = %b, expected 101", cam_trigger);
            errors = errors + 1;
        end else $display("    PASS: cam_trigger = 101 from reg 0x37");

        // ---- read-only regs must REJECT a write with 'N', not silently accept ----
        begin : ro_check
            reg [7:0] ck, ack;
            ck = 8'h00 - (OP_W + 8'h35 + 8'hFF);
            uart_send(SYNC); uart_send(OP_W); uart_send(8'h35); uart_send(8'hFF); uart_send(ck);
            uart_recv(ack);
            expect8("write to RO reg 0x35 rejected", ack, ACK_N);
        end

        // ---- a bad checksum must return 'E' ----
        begin : ck_check
            reg [7:0] ack;
            uart_send(SYNC); uart_send(OP_W); uart_send(8'h37); uart_send(8'h80);
            uart_send(8'h00);                        // deliberately wrong
            uart_recv(ack);
            expect8("bad checksum rejected", ack, ACK_E);
        end

        $display("");
        $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("=== PASS ===");
        else             $display("=== FAIL ===");
        $finish;
    end

    initial begin
        #200_000_000;                                // 200 ms
        $display("*** FAIL: timeout");
        $finish;
    end

endmodule
