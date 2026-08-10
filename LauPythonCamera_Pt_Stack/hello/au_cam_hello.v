`timescale 1ns/1ps
//=============================================================================
// au_cam_hello - Alchitry Au V2 board wrapper for cam_hello_core.
//
// THIS IS THE ONE THAT MATCHES CAMERA_RTL_PLAN.md MILESTONE #5. That milestone
// is written against the Au V2 on purpose: the sensor's SPI is asynchronous to
// its system clock, so the Au can read the chip ID even though it can never
// receive a pixel (CAMERA_IO_MAP.md §8).
//
// All the logic is in cam_hello_core.v -- read that header for the LED map.
//
// ---------------------------------------------------------------------------
// WHY STACKING THE CAMERA ON AN Au IS SAFE, AND WHAT WOULD MAKE IT UNSAFE
//
// The sensor's dout0± pair lands on Au bank 15, which Alchitry documents as NOT
// 3.3 V tolerant (CAMERA_IO_MAP.md §8.2). The sensor's LVDS drivers are powered
// down at reset -- register 112 = 0, all three fields -- so dout0 never drives
// that bank. The rule that keeps it that way is: NEVER WRITE REGISTER 112 ON AN
// Au BUILD.
//
// This design satisfies that rule BY CONSTRUCTION, not by discipline: the SPI
// master's `rw` input is tied to a constant 0 in cam_hello_core, so the design
// is physically incapable of writing any register at all. There is no code path
// to a write, so there is nothing to get wrong.
//
// It also constrains no LVDS pin, so those balls stay Hi-Z with the bitstream's
// UNUSEDPIN PULLDOWN.
// ---------------------------------------------------------------------------
//
// NO RESET PIN. Au2.xdc has none -- the Au V2's button drives PROGRAM_B, not a
// user I/O -- so rst_n_in is tied high and the core runs on its power-on reset.
//=============================================================================
module au_cam_hello #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200
)(
    input  wire       clk,          // 100 MHz, N14

    output wire [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,       // unused; constrained so the pin is defined

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
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD)
    ) u_core (
        .clk         (clk),
        .rst_n_in    (1'b1),        // no user reset pin on the Au V2
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
