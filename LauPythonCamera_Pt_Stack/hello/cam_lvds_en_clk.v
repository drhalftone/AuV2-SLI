`timescale 1ns/1ps
//=============================================================================
// cam_lvds_en_clk - cam_lvds_en with clk_pll FREE-RUNNING at 50 MHz.
//
// The A/B partner to cam_lvds_en.bin. Same design, same interlocks, one bit
// different:
//
//     cam_lvds_en.bin      clk_pll = 0        -> t0=0000, no register access
//     cam_lvds_en_clk.bin  clk_pll = 50 MHz
//
// If this one reads t0=03A6 and goes on to set 112=0007, then the sensor's
// register file needs its reference clock and only register 0 is readable
// without it -- which is what Table 5's ratspi = fin/fspi (Min 6) implies, and
// what an earlier test missed by checking register 0 alone.
//
// See cam_lvds_en.v for the interlocks and the safety argument; nothing about
// them changes here.
//=============================================================================
module cam_lvds_en_clk #(
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
    cam_lvds_en #(
        .CLK_HZ     (CLK_HZ),
        .BAUD       (BAUD),
        .CLK_PLL_EN (1)              // <-- the whole point of this build
    ) u_en (
        .clk         (clk),
        .rst_n       (rst_n),
        .led         (led),
        .usb_tx      (usb_tx),
        .usb_rx      (usb_rx),
        .cam_sck     (cam_sck),
        .cam_mosi    (cam_mosi),
        .cam_ss_n    (cam_ss_n),
        .cam_miso    (cam_miso),
        .cam_reset_n (cam_reset_n),
        .cam_clk_pll (cam_clk_pll),
        .cam_trigger (cam_trigger),
        .cam_monitor (cam_monitor)
    );
endmodule
