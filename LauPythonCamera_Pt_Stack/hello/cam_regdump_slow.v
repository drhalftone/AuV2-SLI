`timescale 1ns/1ps
//=============================================================================
// cam_regdump_slow - cam_regdump with sck at 100 kHz instead of 1 MHz.
//
// THE MARGIN TEST, applied to SPI. Same technique that settled the LVDS lane
// failures: if slowing the link down fixes it, the wiring is fine and the
// problem is margin; if it does not, the fault is real.
//
// WHY NOW. With the camera direct on the Pt, register 0 reads 0x50D0 reliably.
// Stacked on Hd + Ft+ it reads a stable 0x D48A -- wrong, but miso is clearly
// DRIVEN, so the sensor is alive and answering. Power through the pass-throughs
// measured good at TP1-TP5, so this is a signal-integrity question about sck /
// mosi / miso crossing two extra DF40 pairs, not a power one.
//
// 100 kHz gives ten times the settling time per bit. The sensor's SPI maximum
// is 10 MHz and scales with its input clock, so slowing down is always legal --
// CAMERA_SENSOR_PROTOCOL.md 1 notes the default was already chosen slow "because
// it removes the question entirely and costs nothing".
//
//   reads 0x50D0 at 100 kHz  -> margin. The stack needs a slower SPI, or better
//                               termination; the connectors are not broken.
//   still 0xD48A             -> not a rate problem. Look at seating or at a
//                               specific pin.
//
// READ-ONLY, like cam_regdump: rw is tied to a constant 0.
//=============================================================================
module cam_regdump_slow #(
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
    cam_regdump #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD),
        .SCK_HZ (100_000)        // <-- 10x slower than the default
    ) u_dump (
        .clk(clk), .rst_n(rst_n), .led(led), .usb_tx(usb_tx), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );
endmodule