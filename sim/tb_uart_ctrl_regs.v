`timescale 1ns/1ps
//==============================================================================
// tb_uart_ctrl_regs -- protocol VERSION 0x02: undefined reads, build identity, and
// camera settings as read/write registers.
//
// Run (repo root):
//   iverilog -g2012 -o tb_uart_ctrl_regs.vvp sim/tb_uart_ctrl_regs.v sources_1/imports/RTL/uart_ctrl.v
//   vvp tb_uart_ctrl_regs.vvp
//
// Drives protocol bytes straight into uart_ctrl (no UART) and collects every reply
// byte off the producer handshake. What it must prove, and why each is here:
//
//  * An undefined read answers 'N' ADDR CK -- NOT ADDR 00 CK. Plus the one case that
//    could be ambiguous: a read OF 0x4E (='N') must still come back in the echo form.
//  * Build identity reads back the exact parameters passed in.
//  * Each camera commit issues exactly ONE opcode word with the right payload, and a
//    REFUSED commit issues NONE. "Replied N" alone is not enough: a commit that
//    replies N but still fires the word would wedge the sensor while the host is told
//    it was refused.
//  * The exposure/period safety checks use the LIVE settings, both directions,
//    free-running and genlocked.
//==============================================================================
module tb_uart_ctrl_regs;
    reg clk = 0; always #5 clk = ~clk;
    reg rst = 1;

    reg  [7:0] rx_data = 0;
    reg        rx_valid = 0;
    wire [7:0] tx_data;
    wire       tx_send;

    reg  [223:0] cam_stat = 224'd0;
    reg  [39:0]  maxexp   = 40'd0;
    reg  [40:0]  live     = 41'd0;
    wire [31:0]  cmd_word;
    wire         cmd_valid;

    localparam integer GIT   = 28'h94227FC;
    localparam integer EPOCH = 32'h6A9B1234;

    uart_ctrl #(.BUILD_GIT(GIT), .BUILD_DIRTY(1), .BUILD_EPOCH(EPOCH),
                .CLK_HZ(1_000_000)) dut (
        .clk(clk), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(tx_data), .tx_send(tx_send), .tx_busy(1'b0), .tx_active(),
        .led(8'h00), .pins(8'h00),
        .sli_ctrl(), .cam_sim(), .sli_ctrl_en(), .lut_loaded(),
        .corr_addr(8'd0), .corr_dout(), .lut_addr(10'd0), .lut_dout(),
        .lutv_addr(11'd0), .lutv_dout(),
        .edid_rd_addr(), .edid_rd_data(8'd0),
        .esrv_rd_addr(), .esrv_rd_data(8'd0),
        .cam_line_addr(), .cam_line_data(8'd0),
        .mode_idx_i(4'd0), .mode_valid_i(1'b0), .mode_edid_ok_i(1'b0),
        .mode_refr_i(8'd0), .mode_hact_i(12'd0), .mode_vact_i(12'd0),
        .mode_pclk_i(17'd0), .mode_supp_i(14'd0),
        .corr_pat_addr(8'd0), .corr_pat_dout(),
        .mode_force(), .link_drop_host(), .link_drop_proj(),
        .cam_spi_addr(), .cam_spi_rw(), .cam_spi_wdata(), .cam_spi_start(),
        .cam_spi_rdata(16'd0),
        .cam_stat_i(cam_stat), .vs_per_i(72'd0),
        .rx_meas_i(56'd0), .rx_pixkhz_i(18'd0), .rx_diag_i(8'd0), .vfifo_rep_i(8'd0),
        .ctl_diag_i(32'd0), .vdp_diag_i(32'd0), .pre_diag_i(32'd0), .gb_diag_i(32'd0),
        .evt_diag_i(32'd0), .hpd_diag_i(32'd0), .out_meas_i(56'd0), .out_pixkhz_i(18'd0),
        .maxexp_i(maxexp),
        .cam_cmd_word(cmd_word), .cam_cmd_valid(cmd_valid), .cam_live_i(live),
        .cam_spi_busy(1'b0), .cam_spi_done(1'b0),
        .cam_gpio(), .cam_gpio_in(8'd0),
        .cam_boot_go(), .cam_boot_stat(4'd0)
    );

    // ---- reply capture ----
    reg [7:0] rq [0:63];
    integer   rn = 0;
    always @(posedge clk) if (tx_send) begin rq[rn] <= tx_data; rn <= rn + 1; end

    // ---- command capture: count every strobe, keep the last word ----
    integer    ncmd = 0;
    reg [31:0] lastcmd = 0;
    always @(posedge clk) if (cmd_valid) begin ncmd <= ncmd + 1; lastcmd <= cmd_word; end

    integer errors = 0, checks = 0;
    task check(input cond, input [8*100-1:0] msg);
        begin
            checks = checks + 1;
            if (cond) $display("    PASS: %0s", msg);
            else begin errors = errors + 1; $display("*** FAIL: %0s", msg); end
        end
    endtask

    task send(input [7:0] b);
        begin
            @(negedge clk); rx_data = b; rx_valid = 1;
            @(negedge clk); rx_valid = 0;
        end
    endtask

    task settle; repeat (20) @(posedge clk); endtask

    function [7:0] ck(input [7:0] s); ck = 8'h00 - s; endfunction

    // read: returns the three reply bytes
    reg [7:0] r0, r1, r2;
    task rd(input [7:0] a);
        begin
            rn = 0;
            send(8'hA5); send(8'h52); send(a); send(ck(8'h52 + a));
            settle;
            r0 = rq[0]; r1 = rq[1]; r2 = rq[2];
        end
    endtask

    // write: returns the ACK byte
    reg [7:0] ack;
    task wr(input [7:0] a, input [7:0] d);
        begin
            rn = 0;
            send(8'hA5); send(8'h57); send(a); send(d); send(ck(8'h57 + a + d));
            settle;
            ack = rq[0];
        end
    endtask

    integer n0;

    initial begin
        repeat (5) @(posedge clk); rst = 0; repeat (5) @(posedge clk);

        $display("\n[1] identity and version");
        rd(8'h00); check(r0 == 8'h00 && r1 == 8'h48 && ((r0+r1+r2) & 8'hFF) == 0, "ID 0x00 = 'H', good checksum");
        rd(8'h01); check(r1 == 8'h02, "VERSION = 0x02");

        $display("\n[2] undefined reads answer 'N', not zero");
        rd(8'h03); check(r0 == 8'h4E && r1 == 8'h03 && ((r0+r1+r2) & 8'hFF) == 0, "undefined 0x03 -> 'N' 03 CK");
        rd(8'hFF); check(r0 == 8'h4E && r1 == 8'hFF && ((r0+r1+r2) & 8'hFF) == 0, "undefined 0xFF -> 'N' FF CK");
        rd(8'h92); check(r0 == 8'h4E && r1 == 8'h92, "first address past the map (0x92) -> 'N'");
        rd(8'h4E); check(r0 == 8'h4E && r1 == 8'h00 && r2 == ck(8'h4E), "DEFINED 0x4E still echoes: 4E DATA CK, not the 'N' form");
        rn = 0; send(8'hA5); send(8'h52); send(8'h03); send(8'h00); settle;
        check(rq[0] == 8'h45 && rn == 1, "bad request checksum -> single 'E'");

        $display("\n[3] build identity");
        rd(8'h08); check(r1 == 8'hFC, "0x08 git[7:0]");
        rd(8'h09); check(r1 == 8'h27, "0x09 git[15:8]");
        rd(8'h0A); check(r1 == 8'h42, "0x0A git[23:16]");
        rd(8'h0B); check(r1 == 8'h89, "0x0B {dirty=1, git[27:24]=9}");
        rd(8'h0C); check(r1 == 8'h34, "0x0C epoch[7:0]");
        rd(8'h0F); check(r1 == 8'h6A, "0x0F epoch[31:24]");

        $display("\n[4] exposure, FREE-RUNNING at 120 Hz (trig_per 833333 ticks)");
        live = {1'b0, 24'd833333, 16'd1600};
        wr(8'h40, 8'h20); check(ack == 8'h4B, "stage exposure lo -> 'K'");
        n0 = ncmd;
        wr(8'h41, 8'h4E);     // 0x4E20 = 20000 units = 7.5 ms -> 750000 + 5410 <= 833333
        check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h1000_4E20, "20000 units fits -> 'K', ONE opcode-1 word 0x10004E20");
        wr(8'h40, 8'hB8);
        n0 = ncmd;
        wr(8'h41, 8'h56);     // 0x56B8 = 22200 -> 832500 + 5410 = 837910 > 833333
        check(ack == 8'h4E && ncmd == n0, "22200 units over the period -> 'N' and NO word issued");
        rd(8'h40); check(r1 == cam_stat[55:48], "readback of 0x40 is the RUNNING value, not the staged byte");

        $display("\n[5] trigger period vs the LIVE exposure");
        live = {1'b0, 24'd833333, 16'd20000};
        wr(8'h8E, 8'h60); wr(8'h8F, 8'hAE);            // 0x0AAE60 = 700000
        n0 = ncmd;
        wr(8'h90, 8'h0A);
        check(ack == 8'h4E && ncmd == n0, "700000 ticks < 20000-unit exposure + reserve -> 'N', no word");
        wr(8'h8E, 8'hA0); wr(8'h8F, 8'hBB);            // 0x0DBBA0 = 900000
        n0 = ncmd;
        wr(8'h90, 8'h0D);
        check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h200D_BBA0, "900000 ticks -> 'K', opcode-2 word 0x200DBBA0");
        wr(8'h8E, 8'hE8); wr(8'h8F, 8'h03);            // 1000 ticks
        n0 = ncmd;
        wr(8'h90, 8'h00);
        check(ack == 8'h4E && ncmd == n0, "1000 ticks (opcode 2's own floor) -> 'N'");

        $display("\n[6] exposure, GENLOCKED: the published max exposure governs");
        live   = {1'b1, 24'd2000, 16'd1600};       // tiny trig_per must NOT matter now
        maxexp = {16'd5410, 1'b1, 1'b0, 1'b1, 5'd0, 16'd44297};
        wr(8'h40, 8'h09); n0 = ncmd; wr(8'h41, 8'hAD);  // 0xAD09 = 44297
        check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h1000_AD09, "exactly the max (44297) -> 'K'");
        wr(8'h40, 8'h0A); n0 = ncmd; wr(8'h41, 8'hAD);  // 44298
        check(ack == 8'h4E && ncmd == n0, "one unit over the max -> 'N', no word");
        maxexp[23] = 1'b0;
        wr(8'h40, 8'h40); n0 = ncmd; wr(8'h41, 8'h06);  // 1600, trivially small
        check(ack == 8'h4E && ncmd == n0, "max exposure NOT valid -> refuse even a small value");
        wr(8'h8E, 8'hE9); wr(8'h8F, 8'h03); n0 = ncmd; wr(8'h90, 8'h00);   // 1001 ticks
        check(ack == 8'h4B && ncmd == n0 + 1, "genlocked: trigger period is not exposure-checked");

        $display("\n[7] no camera: live settings all zero");
        live = 41'd0; maxexp = 40'd0;
        wr(8'h40, 8'h01); n0 = ncmd; wr(8'h41, 8'h00);
        check(ack == 8'h4E && ncmd == n0, "exposure of 1 unit with no camera -> 'N'");

        $display("\n[8] frames per scan");
        n0 = ncmd; wr(8'h91, 8'd0);  check(ack == 8'h4E && ncmd == n0, "0 frames -> 'N' (a scan would never end)");
        n0 = ncmd; wr(8'h91, 8'd64); check(ack == 8'h4E && ncmd == n0, "64 frames -> 'N' (MAXF is 63)");
        n0 = ncmd; wr(8'h91, 8'd24); check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h4000_0018, "24 frames -> opcode-4 word 0x40000018");
        cam_stat[221:216] = 6'd24;
        rd(8'h91); check(r1 == 8'd24, "0x91 reads frames per scan from the status snapshot");

        $display("\n[9] genlock delay: staged, committed by the enable byte");
        wr(8'h5A, 8'h56); wr(8'h5B, 8'h34);
        n0 = ncmd; wr(8'h5C, 8'h12);
        check(ncmd == n0, "staging the delay issues nothing");
        wr(8'h5D, 8'h01);
        check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h7812_3456, "enable=1 -> opcode-7 word 0x78123456");

        $display("\n[10] commands");
        n0 = ncmd; wr(8'h17, 8'h01); check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h3000_0000, "CAMCMD bit0 -> opcode-3 re-arm");
        n0 = ncmd; wr(8'h17, 8'h00); check(ack == 8'h4B && ncmd == n0, "CAMCMD bit0 clear -> nothing issued");
        wr(8'h18, 8'hE8); wr(8'h19, 8'h03);
        rd(8'h18); check(r1 == 8'hE8, "idle ms reads back staged");
        n0 = ncmd; wr(8'h1A, 8'h01);
        check(ack == 8'h4B && ncmd == n0 + 1 && lastcmd == 32'h6800_03E8, "CAMIDLE -> opcode-6 word 0x680003E8 (1000 ms)");
        n0 = ncmd; wr(8'h1A, 8'h00);
        check(ncmd == n0 + 1 && lastcmd == 32'h6000_03E8, "CAMIDLE bit0 clear -> release");

        $display("\n[11] unchanged behaviour");
        wr(8'h13, 8'hAB); rd(8'h13); check(r1 == 8'hAB, "SLICTRL 0x13 still R/W");
        wr(8'h20, 8'h00); check(ack == 8'h4E, "write to read-only 0x20 -> 'N'");
        wr(8'hF0, 8'h00); check(ack == 8'h4E, "write to undefined 0xF0 -> 'N'");

        $display("\n=== %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end
endmodule
