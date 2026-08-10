`timescale 1ns/1ps
//=============================================================================
// pt_cam_hello_clk - pt_cam_hello, but with clk_pll FREE-RUNNING.
//
// Identical to pt_cam_hello.v in every other respect. The only difference is
// CLK_PLL_EN(1), which makes cam_hello_core drive a 50 MHz reference clock at
// the sensor instead of holding the pin at 0.
//
// WHY THIS BUILD EXISTS. CAMERA_SENSOR_PROTOCOL.md §1 asserts the sensor's SPI
// works with no input clock at all, and the chip-ID bring-up was designed around
// that. §4.0 of the same document records the datasheet's ratspi ceiling as
// fin/6 -- which is zero when fin is zero. Both cannot be right, and on the
// bench the sensor is silent while every wire to it has been proven good.
//
// Kept as a SEPARATE top rather than a parameter override so the two bitstreams
// coexist and the comparison is one command apart:
//
//     pt_cam_hello.bin      clk_pll = 0   -> the original assumption
//     pt_cam_hello_clk.bin  clk_pll = 50 MHz
//
// If this one reads 0x50D0 and the other does not, §1 is wrong and the Au
// bring-up path in CAMERA_IO_MAP.md §8 needs revising along with it.
//=============================================================================
module pt_cam_hello_clk #(
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
    wire _unused = usb_rx;

    cam_hello_core #(
        .CLK_HZ     (CLK_HZ),
        .BAUD       (BAUD),
        .CLK_PLL_EN (1)              // <-- the whole point of this build
    ) u_core (
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
        .cam_monitor (cam_monitor)
    );
endmodule
