`timescale 1ns/1ps
//=============================================================================
// pt_cam_hello - Alchitry Pt V2 board wrapper for cam_hello_core.
//
// All the logic is in cam_hello_core.v; read that file's header for what this
// bitstream does and how to read the LEDs. This wrapper exists only because the
// two boards differ in their board-level pins:
//
//   Pt V2  XC7A100T-FGG484  has a user reset pin (N15)  -> wired here
//   Au V2  XC7A35T-FTG256   has none                    -> au_cam_hello.v
//
// The camera interface itself is identical on both: the same 11 element-bus
// signals, just landing on different balls. See CAMERA_IO_MAP.md §4 (Pt) / §8.1 (Au).
//=============================================================================
module pt_cam_hello #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200
)(
    input  wire       clk,          // 100 MHz, W19
    input  wire       rst_n,        // reset button, N15, active low

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
    // usb_rx is deliberately unread. Referencing it here keeps the port from
    // being stripped, without inventing a use for it.
    wire _unused = usb_rx;

    cam_hello_core #(
        .CLK_HZ (CLK_HZ),
        .BAUD   (BAUD)
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
