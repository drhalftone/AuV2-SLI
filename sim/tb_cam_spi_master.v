`timescale 1ns/1ps
//==============================================================================
// tb_cam_spi_master - self-checking testbench for the PYTHON 1300 SPI master.
//
// Contains a behavioral model of the sensor's SPI slave built straight from the
// datasheet (see CAMERA_SENSOR_PROTOCOL.md §1/§2), preloaded with the REAL reset
// defaults. The point of using real defaults is that a passing read of 0x50D0 from
// register 0 here is the same transaction we will run on hardware in task #5.
//
// The model asserts protocol timing too (Table 11), so a master that "works" but
// violates tsck / tsssck / tsckss / the inter-transaction gap FAILS here rather
// than on the bench.
//
// Run: xvlog -d SIM sim/tb_cam_spi_master.v sources_1/imports/RTL/cam_spi_master.v
//      xelab -R tb_cam_spi_master
//==============================================================================
module tb_cam_spi_master;

    localparam integer CLK_HZ = 100_000_000;
    localparam integer SCK_HZ =  10_000_000;   // fastest LEGAL rate -- worst case for timing
    localparam real    TSCK_MIN_NS = 100.0;    // Table 11: tsck >= 100 ns

    reg clk = 1'b0;
    always #5 clk = ~clk;                      // 100 MHz
    reg rst = 1'b1;

    reg         start = 1'b0;
    reg         rw    = 1'b0;
    reg  [8:0]  addr  = 9'd0;
    reg  [15:0] wdata = 16'd0;
    wire [15:0] rdata;
    wire        busy, done;

    wire sck, mosi, ss_n;
    wire miso;

    integer errors = 0;
    integer checks = 0;

    //--------------------------------------------------------------------------
    // DUT
    //--------------------------------------------------------------------------
    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(SCK_HZ)) dut (
        .clk(clk), .rst(rst),
        .start(start), .rw(rw), .addr(addr), .wdata(wdata),
        .rdata(rdata), .busy(busy), .done(done),
        .sck(sck), .mosi(mosi), .ss_n(ss_n), .miso(miso)
    );

    //==========================================================================
    // Behavioral PYTHON 1300 SPI slave
    //==========================================================================
    // The model lives in sim/python1300_spi_model.v -- shared with tb_cam_mailbox so the
    // two testbenches cannot drift apart. It encodes two subtleties worth reading before
    // trusting any result here: miso is driven on the RISING edge with D15 landing on
    // cycle ELEVEN, and the address is sh_in[9:1] not [8:0].
    python1300_spi_model sensor (
        .sck(sck), .mosi(mosi), .ss_n(ss_n), .miso(miso)
    );
    pullup (miso);                             // board fits a pull; miso is Hi-Z outside reads

    //==========================================================================
    // Protocol timing checks (Table 11)
    //==========================================================================
    // NOTE on arming: at t=0 the DUT's ss_n is X and goes to 1 when reset is applied.
    // That X->1 counts as a posedge, so every check below is armed by a flag that is only
    // set once a REAL transaction has actually clocked. Otherwise the reset transition
    // reports phantom tsckss / gap violations, which is noise that hides real ones.
    real t_sck_rise = 0.0, t_sck_fall = 0.0, t_ss_fall = 0.0, t_ss_rise = 0.0;
    reg  first_sck      = 1'b0;   // next sck rise is the first of this transaction
    reg  seen_sck_fall  = 1'b0;   // this transaction has clocked at least once
    reg  had_txn        = 1'b0;   // at least one transaction has completed

    always @(posedge sck) if (!ss_n) begin
        checks = checks + 1;
        if (first_sck) begin
            // tsssck: ss_n falling -> first sck rising edge, must be >= tsck
            if (($realtime - t_ss_fall) < TSCK_MIN_NS - 0.001) begin
                $display("*** FAIL: tsssck = %0.1f ns < %0.1f ns",
                         $realtime - t_ss_fall, TSCK_MIN_NS);
                errors = errors + 1;
            end
            first_sck = 1'b0;
        end else begin
            // tsck: sck period
            if (($realtime - t_sck_rise) < TSCK_MIN_NS - 0.001) begin
                $display("*** FAIL: tsck = %0.1f ns < %0.1f ns",
                         $realtime - t_sck_rise, TSCK_MIN_NS);
                errors = errors + 1;
            end
        end
        t_sck_rise = $realtime;
    end

    always @(negedge sck) if (!ss_n) begin
        t_sck_fall    = $realtime;
        seen_sck_fall = 1'b1;
    end

    always @(negedge ss_n) begin
        // >= 2 sck periods with ss_n HIGH between transactions
        if (had_txn) begin
            checks = checks + 1;
            if (($realtime - t_ss_rise) < 2.0*TSCK_MIN_NS - 0.001) begin
                $display("*** FAIL: inter-transaction ss_n-high gap = %0.1f ns < %0.1f ns",
                         $realtime - t_ss_rise, 2.0*TSCK_MIN_NS);
                errors = errors + 1;
            end
        end
        t_ss_fall = $realtime;
        first_sck = 1'b1;
    end

    always @(posedge ss_n) begin
        // Only a transaction that actually clocked can violate tsckss.
        if (seen_sck_fall) begin
            checks = checks + 1;
            // tsckss: last sck falling edge -> ss_n high, must be >= tsck
            if (($realtime - t_sck_fall) < TSCK_MIN_NS - 0.001) begin
                $display("*** FAIL: tsckss = %0.1f ns < %0.1f ns",
                         $realtime - t_sck_fall, TSCK_MIN_NS);
                errors = errors + 1;
            end
            t_ss_rise = $realtime;
            had_txn   = 1'b1;
        end
        seen_sck_fall = 1'b0;
    end

    //==========================================================================
    // Stimulus
    //==========================================================================
    task spi_read(input [8:0] a, output [15:0] d);
    begin
        @(posedge clk);
        addr <= a; rw <= 1'b0; start <= 1'b1;
        @(posedge clk);
        start <= 1'b0;
        wait (done == 1'b1);
        @(posedge clk);
        d = rdata;
    end
    endtask

    task spi_write(input [8:0] a, input [15:0] d);
    begin
        @(posedge clk);
        addr <= a; rw <= 1'b1; wdata <= d; start <= 1'b1;
        @(posedge clk);
        start <= 1'b0;
        wait (done == 1'b1);
        @(posedge clk);
    end
    endtask

    task expect16(input [255:0] name, input [15:0] got, input [15:0] exp);
    begin
        checks = checks + 1;
        if (got !== exp) begin
            $display("*** FAIL: %0s: got 0x%04h, expected 0x%04h", name, got, exp);
            errors = errors + 1;
        end else begin
            $display("    PASS: %0s = 0x%04h", name, got);
        end
    end
    endtask

    reg [15:0] v;

    initial begin
        $display("=== tb_cam_spi_master: SCK = %0d Hz (tsck = %0.1f ns) ===",
                 SCK_HZ, 1.0e9/SCK_HZ);
        repeat (5) @(posedge clk);
        rst <= 1'b0;
        repeat (5) @(posedge clk);

        // ---- THE hardware gate, in simulation: read the chip ID ----
        spi_read(9'd0, v);
        expect16("chip_id (reg 0)", v, 16'h50D0);

        // ---- known-default reads: proves address decode, not just one lucky value ----
        spi_read(9'd116, v);  expect16("training pattern (reg 116)", v, 16'h03A6);
        spi_read(9'd117, v);  expect16("frame sync marker (reg 117)", v, 16'h002A);
        spi_read(9'd119, v);  expect16("IMG code (reg 119)",          v, 16'h0035);
        spi_read(9'd112, v);  expect16("LVDS power (reg 112) OFF",    v, 16'h0000);
        spi_read(9'd10,  v);  expect16("soft_reset_analog (reg 10)",  v, 16'h0999);

        // ---- write / read-back: proves the link in BOTH directions ----
        spi_write(9'd116, 16'h0155);
        spi_read (9'd116, v);  expect16("reg 116 after write", v, 16'h0155);
        spi_write(9'd116, 16'h03A6);            // restore
        spi_read (9'd116, v);  expect16("reg 116 restored",    v, 16'h03A6);

        // ---- back-to-back transactions (exercises the >= 2 sck gap) ----
        spi_write(9'd192, 16'h0001);
        spi_write(9'd112, 16'h0007);
        spi_read (9'd192, v);  expect16("reg 192 back-to-back", v, 16'h0001);
        spi_read (9'd112, v);  expect16("reg 112 back-to-back", v, 16'h0007);

        // ---- chip_id is read-only: a write must NOT land ----
        spi_write(9'd0, 16'hDEAD);
        spi_read (9'd0, v);  expect16("chip_id still read-only", v, 16'h50D0);

        // ---- all-ones / all-zeros data patterns (catches stuck bits & off-by-one) ----
        spi_write(9'd200, 16'hFFFF);
        spi_read (9'd200, v);  expect16("reg 200 = 0xFFFF", v, 16'hFFFF);
        spi_write(9'd200, 16'h0000);
        spi_read (9'd200, v);  expect16("reg 200 = 0x0000", v, 16'h0000);
        spi_write(9'd200, 16'hA5A5);
        spi_read (9'd200, v);  expect16("reg 200 = 0xA5A5", v, 16'hA5A5);
        // address 0x1FF exercises all 9 address bits
        spi_write(9'd511, 16'h1234);
        spi_read (9'd511, v);  expect16("reg 511 (9-bit addr)", v, 16'h1234);

        repeat (20) @(posedge clk);

        $display("");
        $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors == 0) $display("=== PASS ===");
        else             $display("=== FAIL ===");
        $finish;
    end

    initial begin
        #2_000_000;
        $display("*** FAIL: timeout");
        $finish;
    end

endmodule
