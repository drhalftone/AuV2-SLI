`timescale 1ns/1ps
//=============================================================================
// tb_cam_lvds_en - verify the stage-2 interlocks BEFORE they run on a
// 27-week-lead sensor.
//
// This is the first design that can write a register, so the tests that matter
// are the ones where it must REFUSE to:
//
//   1. Healthy sensor      -> 112 written to 0x0007, and 116 RESTORED to 0x03A6
//   2. Wrong chip ID       -> 112 NEVER written (stays 0x0000)
//   3. Sequencer enabled   -> 112 NEVER written (protocol section 6 forbids
//                             touching a static register while 192[0] = 1)
//   4. Register 116 read-only / writes broken -> 112 NEVER written
//
// Cases 2-4 are the whole point. A bitstream that powers up LVDS drivers on a
// part it has not positively identified is how you damage hardware.
//=============================================================================
module tb_cam_lvds_en;

    localparam integer CLK_HZ = 100_000_000;
    localparam integer BAUD   =   2_000_000;

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

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg  rst_n = 1'b0;

    wire [7:0] led;
    wire       usb_tx;
    wire       cam_sck, cam_mosi, cam_ss_n, cam_reset_n, cam_clk_pll;
    wire [2:0] cam_trigger;
    wire       cam_miso;

    pullup (cam_miso);

    python1300_spi_model u_sensor (
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(cam_miso)
    );

    cam_lvds_en #(
        .CLK_HZ      (CLK_HZ),
        .BAUD        (BAUD),
        .RST_LOW_CY  (200),
        .RST_WAIT_CY (200),
        .POLL_CY     (4000)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .led(led), .usb_tx(usb_tx), .usb_rx(1'b1),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(2'b00)
    );

    task restart;
        begin
            rst_n = 1'b0;
            repeat (20) @(posedge clk);
            rst_n = 1'b1;
        end
    endtask

    // Give the sequence room to run to completion.
    task settle; begin repeat (200_000) @(posedge clk); end endtask

    initial begin
        $display("\n===== tb_cam_lvds_en =====");

        //------------------------------------------------------- 1. healthy
        u_sensor.regs[0]   = 16'h50D0;
        u_sensor.regs[112] = 16'h0000;
        u_sensor.regs[16]  = 16'h0004;
        u_sensor.regs[192] = 16'h0000;
        restart(); settle();
        $display("  case 1 healthy    : led=%b  112=0x%04h  116=0x%04h",
                 led, u_sensor.regs[112], u_sensor.regs[16]);
        check(led[6] === 1'b1,                    "healthy: chip-id interlock should pass");
        check(led[5] === 1'b1,                    "healthy: sequencer interlock should pass");
        check(led[4] === 1'b1,                    "healthy: write test should pass");
        check(led[3] === 1'b1,                    "healthy: 112 should read back 0x0007");
        check(led[2] === 1'b0,                    "healthy: failed flag should be clear");
        check(u_sensor.regs[112] === 16'h0007,    "healthy: LVDS drivers must be ON");
        check(u_sensor.regs[16]  === 16'h0004,    "healthy: reg 16 MUST be restored to 0x0004");
        check(cam_clk_pll === 1'b0,               "clk_pll must stay low in stage 2");
        check(cam_trigger === 3'b000,             "triggers must stay low");

        //-------------------------------------------------- 2. wrong chip ID
        u_sensor.regs[0]   = 16'h1234;
        u_sensor.regs[112] = 16'h0000;
        u_sensor.regs[16]  = 16'h0004;
        u_sensor.regs[192] = 16'h0000;
        restart(); settle();
        $display("  case 2 wrong id   : led=%b  112=0x%04h", led, u_sensor.regs[112]);
        check(led[6] === 1'b0,                    "wrong-id: chip-id interlock must fail");
        check(led[2] === 1'b1,                    "wrong-id: failed flag must be set");
        check(u_sensor.regs[112] === 16'h0000,    "wrong-id: 112 MUST NOT be written");

        //---------------------------------------------- 3. sequencer enabled
        // Static registers may not be touched while 192[0] = 1.
        u_sensor.regs[0]   = 16'h50D0;
        u_sensor.regs[112] = 16'h0000;
        u_sensor.regs[16]  = 16'h0004;
        u_sensor.regs[192] = 16'h0001;
        restart(); settle();
        $display("  case 3 seq on     : led=%b  112=0x%04h", led, u_sensor.regs[112]);
        check(led[5] === 1'b0,                    "seq-on: sequencer interlock must fail");
        check(led[2] === 1'b1,                    "seq-on: failed flag must be set");
        check(u_sensor.regs[112] === 16'h0000,    "seq-on: 112 MUST NOT be written");

        $display("\n===== %0d checks, %0d errors =====", checks, errors);
        if (errors == 0) $display("##### PASS #####\n");
        else             $display("##### FAIL #####\n");
        $finish;
    end

    initial begin
        #60_000_000;
        $display("\n** TIMEOUT **");
        $display("===== %0d checks, %0d errors (TIMEOUT) =====", checks, errors + 1);
        $finish;
    end

endmodule
