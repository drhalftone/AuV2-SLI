`timescale 1ns/1ps
//=============================================================================
// tb_pt_cam_hello - prove the bring-up bitstream before it goes on the bench.
//
// Drives the real top level against sim/python1300_spi_model.v (the same model
// that verified cam_spi_master, preloaded with the datasheet reset defaults),
// then against a set of DELIBERATELY BROKEN sensors, because the whole value of
// a bring-up bitstream is what it does when the hardware is wrong.
//
//   1. Good sensor            -> PASS led, reg0=50D0 on the UART, miso live
//   2. miso stuck low  (open) -> FAIL led, reg0=0000, miso_live DARK
//   3. miso stuck high        -> FAIL led, reg0=FFFF, miso_live DARK
//   4. Wrong chip answering   -> FAIL led, but miso_live LIT
//
// Case 4 is the one that matters: it is the only scenario where the difference
// between "nothing is connected" and "something is connected but wrong" shows
// up, and led[4] is the bit that tells them apart.
//
// The UART line is decoded by an actual 115200 8N1 receiver, so the test also
// catches the off-by-one that drops the first character of every line.
//
//   vivado -mode batch -source run_sim.tcl
//=============================================================================
module tb_pt_cam_hello;

    localparam integer CLK_HZ = 100_000_000;
    localparam integer BAUD   =   2_000_000;   // fast UART: keeps the sim short
    localparam real    BIT_NS = 1.0e9 / BAUD;

    integer checks = 0;
    integer errors = 0;

    task check(input cond, input [1023:0] what);
        begin
            checks = checks + 1;
            if (!cond) begin
                errors = errors + 1;
                $display("  ** FAIL: %0s", what);
            end
        end
    endtask

    //--------------------------------------------------------------- stimulus
    reg clk = 1'b0;
    always #5 clk = ~clk;            // 100 MHz

    reg  rst_n = 1'b0;

    wire [7:0] led;
    wire       usb_tx;
    wire       cam_sck, cam_mosi, cam_ss_n, cam_reset_n, cam_clk_pll;
    wire [2:0] cam_trigger;
    wire       cam_miso;

    // How the modelled sensor misbehaves this round.
    //   0 = healthy, 1 = miso stuck low, 2 = miso stuck high, 3 = wrong chip ID
    integer fault = 0;

    wire miso_model;
    // Board pull on miso; the real board fits one (protocol §1).
    pullup (miso_model);

    assign cam_miso = (fault == 1) ? 1'b0 :
                      (fault == 2) ? 1'b1 : miso_model;

    python1300_spi_model u_sensor (
        .sck  (cam_sck),
        .mosi (cam_mosi),
        .ss_n (cam_ss_n),
        .miso (miso_model)
    );

    // The CORE is the device under test, not a board wrapper: it is the logic
    // both the Pt and Au bitstreams run, so proving it here covers both.
    cam_hello_core #(
        .CLK_HZ      (CLK_HZ),
        .BAUD        (BAUD),
        .RST_LOW_CY  (200),          // shrunk from 10 ms
        .RST_WAIT_CY (200),
        .POLL_CY     (4000)          // shrunk from 500 ms
    ) dut (
        .clk         (clk),
        .rst_n_in    (rst_n),
        .led         (led),
        .usb_tx      (usb_tx),
        .cam_sck     (cam_sck),
        .cam_mosi    (cam_mosi),
        .cam_ss_n    (cam_ss_n),
        .cam_miso    (cam_miso),
        .cam_reset_n (cam_reset_n),
        .cam_clk_pll (cam_clk_pll),
        .cam_trigger (cam_trigger),
        .cam_monitor (2'b00)
    );

    //-------------------------------------------------- a real UART receiver
    // Not a peek at internal state: this samples the pin at the mid-bit point
    // exactly like the FT2232 will.
    reg  [7:0] line [0:63];
    integer    nline = 0;
    reg [8*64-1:0] last_line;
    reg            line_ready = 0;

    integer bi;
    reg [7:0] rxb;
    initial begin : uart_rx
        forever begin
            @(negedge usb_tx);                  // start bit
            #(BIT_NS * 1.5);
            for (bi = 0; bi < 8; bi = bi + 1) begin
                rxb[bi] = usb_tx;
                #(BIT_NS);
            end
            if (rxb == 8'h0A) begin             // \n ends the line
                line_ready = 1;
                #1 line_ready = 0;
                nline = 0;
            end else if (rxb != 8'h0D) begin
                line[nline] = rxb;
                if (nline < 63) nline = nline + 1;
            end
        end
    end

    // Latch the most recent complete line as a string for comparison. BODY is
    // 20 chars: the formatter emits 22, but the receiver above strips \r\n.
    localparam integer BODY = 20;
    integer k;
    always @(posedge line_ready) begin
        last_line = {8*64{1'b0}};
        for (k = 0; k < BODY; k = k + 1)
            last_line = {last_line[8*63-1:0], line[k]};
    end

    task wait_line;                     // wait for one freshly-completed line
        begin
            @(posedge line_ready);
            @(negedge line_ready);
            #1;
        end
    endtask

    task restart(input integer f);      // re-run the DUT against fault mode f
        begin
            fault = f;
            rst_n = 1'b0;
            repeat (20) @(posedge clk);
            rst_n = 1'b1;
        end
    endtask

    //-------------------------------------------------------------- the tests
    initial begin
        $display("\n===== tb_pt_cam_hello =====");

        //---------------------------------------------------------- 1. healthy
        restart(0);
        wait_line();
        $display("  case 1 healthy      : led=%b  line=\"%0s\"", led, last_line);
        check(led[6] === 1'b1,               "healthy: PASS led (led[6]) should be lit");
        check(led[5] === 1'b0,               "healthy: FAIL led (led[5]) should be dark");
        check(led[4] === 1'b1,               "healthy: miso-live (led[4]) should be lit");
        check(led[3:0] === 4'h0,             "healthy: led[3:0] should show 0x0 (0x50D0)");
        check(last_line[8*BODY-1 -: 8*BODY] == "reg0=50D0 mon=0 PASS",
                                             "healthy: UART line should read reg0=50D0 ... PASS");
        check(cam_reset_n === 1'b1,          "healthy: reset_n should be released");
        check(cam_clk_pll === 1'b0,          "clk_pll must stay low -- we never clock the sensor");
        check(cam_trigger === 3'b000,        "trigger must stay low");

        // It must keep re-reading, not latch one result and stop.
        wait_line();
        check(led[6] === 1'b1,               "healthy: PASS should still be lit on the second poll");

        //------------------------------------------------- 2. miso stuck low
        restart(1);
        wait_line();
        $display("  case 2 miso stuck lo: led=%b  line=\"%0s\"", led, last_line);
        check(led[6] === 1'b0,               "stuck-low: PASS led must be dark");
        check(led[5] === 1'b1,               "stuck-low: FAIL led must be lit");
        check(led[4] === 1'b0,               "stuck-low: miso-live must be DARK -- the open-circuit tell");
        check(last_line[8*BODY-1 -: 8*BODY] == "reg0=0000 mon=0 FAIL",
                                             "stuck-low: UART should read reg0=0000 ... FAIL");

        //------------------------------------------------ 3. miso stuck high
        restart(2);
        wait_line();
        $display("  case 3 miso stuck hi: led=%b  line=\"%0s\"", led, last_line);
        check(led[5] === 1'b1,               "stuck-high: FAIL led must be lit");
        check(led[4] === 1'b0,               "stuck-high: miso-live must be DARK");
        check(last_line[8*BODY-1 -: 8*BODY] == "reg0=FFFF mon=0 FAIL",
                                             "stuck-high: UART should read reg0=FFFF ... FAIL");

        //-------------------------------------------------- 4. wrong chip ID
        // Something IS answering, and answering correctly at the protocol level
        // -- it is simply not a PYTHON 1300. miso_live separates this from an
        // open circuit, which is the entire reason led[4] exists.
        u_sensor.regs[0] = 16'h1234;
        restart(3);
        wait_line();
        $display("  case 4 wrong chip   : led=%b  line=\"%0s\"", led, last_line);
        check(led[6] === 1'b0,               "wrong-chip: PASS led must be dark");
        check(led[5] === 1'b1,               "wrong-chip: FAIL led must be lit");
        check(led[4] === 1'b1,               "wrong-chip: miso-live must be LIT -- the line is alive");
        check(led[3:0] === 4'h4,             "wrong-chip: led[3:0] should show 0x4 (0x1234)");
        check(last_line[8*BODY-1 -: 8*BODY] == "reg0=1234 mon=0 FAIL",
                                             "wrong-chip: UART should read reg0=1234 ... FAIL");

        //-------------------------------------------- 5. recovery after a fix
        // Put a good sensor back and confirm the design re-locks without a
        // power cycle -- i.e. re-seating the board is enough.
        u_sensor.regs[0] = 16'h50D0;
        restart(0);
        wait_line();
        $display("  case 5 recovered    : led=%b  line=\"%0s\"", led, last_line);
        check(led[6] === 1'b1,               "recovered: PASS led should be lit again");

        $display("\n===== %0d checks, %0d errors =====", checks, errors);
        if (errors == 0) $display("##### PASS #####\n");
        else             $display("##### FAIL #####\n");
        $finish;
    end

    initial begin
        #20_000_000;                     // 20 ms guard
        $display("\n** TIMEOUT -- the DUT never produced the expected lines\n");
        $display("===== %0d checks, %0d errors (TIMEOUT) =====", checks, errors + 1);
        $finish;
    end

endmodule
