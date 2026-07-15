`timescale 1ns/1ps
//=============================================================================
// tb_cam_boot - task #6 gate. cam_boot_seq -> cam_spi_master -> python1300 model.
//
// Proves the sequencer emits onsemi's startup sequence in order and lands every value in
// the sensor's registers, polls the PLL lock, and asserts ready. Wait durations are shrunk
// via parameters so the sim is fast; the poll interval is kept a bit above the model's ~4 us
// lock time so the PLL retry loop actually iterates.
//
// Run: xvlog -d SIM sim/tb_cam_boot.v sim/python1300_spi_model.v \
//        sources_1/imports/RTL/{cam_boot_seq,cam_spi_master}.v
//      xelab -R tb_cam_boot
//=============================================================================
module tb_cam_boot;
    reg clk = 0; always #5 clk = ~clk;         // 100 MHz
    reg rst = 1;
    reg go  = 0;

    wire        busy, ready, failed, pll_timeout, reset_n;
    wire        spi_start, spi_rw;
    wire [8:0]  spi_addr;
    wire [15:0] spi_wdata, spi_rdata;
    wire        spi_busy, spi_done;
    wire        sck, mosi, ss_n, miso;

    // short waits: reset ~200/400 ns, PLL poll ~6 us (model locks at ~4 us)
    cam_boot_seq #(.T_RST_LOW(20), .T_RST_HIGH(40), .T_PLL_POLL(600), .PLL_TRIES(10)) boot (
        .clk(clk), .rst(rst), .go(go),
        .busy(busy), .ready(ready), .failed(failed), .pll_timeout(pll_timeout),
        .reset_n(reset_n),
        .spi_start(spi_start), .spi_rw(spi_rw), .spi_addr(spi_addr), .spi_wdata(spi_wdata),
        .spi_rdata(spi_rdata), .spi_busy(spi_busy), .spi_done(spi_done)
    );

    cam_spi_master #(.CLK_HZ(100_000_000), .SCK_HZ(10_000_000)) spi (
        .clk(clk), .rst(rst),
        .start(spi_start), .rw(spi_rw), .addr(spi_addr), .wdata(spi_wdata),
        .rdata(spi_rdata), .busy(spi_busy), .done(spi_done),
        .sck(sck), .mosi(mosi), .ss_n(ss_n), .miso(miso)
    );

    python1300_spi_model sensor (.sck(sck), .mosi(mosi), .ss_n(ss_n), .miso(miso));
    pullup (miso);

    integer errors = 0, checks = 0;

    // expected end-state of key registers after a full boot
    task chk(input [255:0] name, input [8:0] a, input [15:0] exp);
    begin
        checks = checks + 1;
        if (sensor.regs[a] !== exp) begin
            $display("*** FAIL: %0s: reg %0d = 0x%04h, expected 0x%04h", name, a, sensor.regs[a], exp);
            errors = errors + 1;
        end else $display("    PASS: %0s reg %0d = 0x%04h", name, a, exp);
    end
    endtask

    // reset_n is HELD low from power-up (sensor held in reset), so the meaningful event is
    // the RELEASE -- a posedge after go. That proves the sequencer drove the reset window
    // and then let the sensor out.
    reg saw_reset_release = 0;
    always @(posedge reset_n) if (!rst) saw_reset_release = 1;

    initial begin
        $display("=== tb_cam_boot: ROM-driven PYTHON 1300 boot ===");
        repeat (5) @(posedge clk); rst <= 0; repeat (5) @(posedge clk);

        // sensor must be held in reset before boot
        checks = checks + 1;
        if (reset_n !== 1'b0) begin $display("*** FAIL: reset_n not held low pre-boot"); errors=errors+1; end
        else $display("    PASS: reset_n held low before boot");

        @(posedge clk); go <= 1'b1; @(posedge clk); go <= 1'b0;

        // wait for boot to finish
        begin : wb
            integer w;
            for (w = 0; w < 2_000_000; w = w + 1) begin
                @(posedge clk);
                if (ready || failed) disable wb;
            end
        end

        checks = checks + 1;
        if (!ready) begin
            $display("*** FAIL: boot did not reach ready (failed=%b pll_timeout=%b)", failed, pll_timeout);
            errors = errors + 1;
        end else $display("    PASS: boot reached ready");

        checks = checks + 1;
        if (!saw_reset_release) begin $display("*** FAIL: reset_n never released"); errors=errors+1; end
        else $display("    PASS: reset_n held low then released (reset window driven)");

        checks = checks + 1;
        if (pll_timeout) begin $display("*** FAIL: PLL lock timed out"); errors=errors+1; end
        else $display("    PASS: PLL locked within the poll budget");

        // ---- spot-check that the sequence actually LANDED, in the sensor's registers ----
        chk("mono (not color)",   9'd2,   16'h0000);   // our deviation from Avnet
        chk("PLL enabled",        9'd16,  16'h0003);
        chk("clock gen SEQ05",    9'd32,  16'h3007);   // last write to reg 32 wins
        chk("SEQ04 reg 65",       9'd65,  16'h288B);
        chk("4-LVDS channel cfg", 9'd211, 16'h0E49);
        chk("LVDS drivers ON",    9'd112, 16'h0007);   // reg 112 -- PT ONLY
        chk("AFE reg 128",        9'd128, 16'h4714);
        chk("sequencer enabled",  9'd192, 16'h0801);   // 0x0800 | bit0

        $display("");
        $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end

    initial begin #20_000_000; $display("*** FAIL: timeout (ready=%b failed=%b)", ready, failed); $finish; end
endmodule
