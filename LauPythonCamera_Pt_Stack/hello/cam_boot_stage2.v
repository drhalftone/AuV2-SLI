`timescale 1ns/1ps
//=============================================================================
// cam_boot_stage2 - STAGE 2: the full boot up to and including LVDS power-up,
// with the sequencer still disabled. Then go meter the pairs.
//
// cam_boot_stage1 with STOP_AT = 41 instead of 8. That single number is the
// difference between "ask the PLL to lock" and "everything except streaming":
//
//     STOP_AT =  8   SEQ01 + PLL lock poll.               Stage 1.
//     STOP_AT = 41   ... + SEQ03 + SEQ04 + SEQ05, which includes
//                    rom[39] = reg 112 = 0x0007  <-- LVDS DRIVERS ON.
//                    STOPS BEFORE rom[41] = reg 192 = 0x0801, so the
//                    sequencer stays disabled and the sensor does NOT stream.
//     STOP_AT =  0   all 42. Sensor streams. Not yet.
//
// Stage 1 had to come first: registers 112 and 116-126 live in the
// serializer/LVDS block, which is asleep until clock management is enabled, so
// no write to 112 could land before the PLL was locked and the logic clock was
// running. cam_regdump proved that (112/116-126 read 0x0000 while 16 wrote fine).
//
//-----------------------------------------------------------------------------
// THE GATE: r112 reads back 0x0007, then EVERY sensor pin 7..18 sits near
// 1.2 V common mode. Meter them:
//
//     ~1.2 V on both halves of a pair   -> that driver and both joints are good
//     0 V or a rail                     -> dead driver or a bad hand-solder joint
//
// Six output pairs, twelve pins, one meter, no receiver involved. That is the
// hand-soldering risk retired before a single bit of 720 Mbps RTL exists.
//
//-----------------------------------------------------------------------------
// BANK 13 IS NOW LIVE -- CHECK VBSEL FIRST.
//
// Up to now every bitstream deliberately used zero bank-13 pins, so the strap
// did not matter. It still does not matter for THIS design (it constrains no
// bank-13 pin either), but the sensor is now DRIVING those FPGA pins. Alchitry
// warn that mis-set tri-voltage pins can damage the FPGA, so before stage 3
// instantiates LVDS_25 + DIFF_TERM receivers, re-verify checklist section 1:
// R10 pad 2 and R11 pad 2 both 3.23-3.33 V. It passed 2026-08-07, but the board
// has been reworked several times since.
//
// LEDs and UART are cam_boot_stage1's; watch r112 for 0007.
//=============================================================================
module cam_boot_stage2 #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200
)(
    input  wire       clk,
    input  wire       rst_n,

    output wire [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output wire       cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    cam_boot_stage1 #(
        .CLK_HZ  (CLK_HZ),
        .BAUD    (BAUD),
        .STOP_AT (41)          // through reg 112; sequencer NOT enabled
    ) u_s1 (
        .clk(clk), .rst_n(rst_n),
        .led(led), .usb_tx(usb_tx), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );
endmodule
