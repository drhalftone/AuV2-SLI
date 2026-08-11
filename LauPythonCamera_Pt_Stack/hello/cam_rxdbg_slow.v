`timescale 1ns/1ps
//=============================================================================
// cam_rxdbg_slow - cam_rxdbg at HALF the bit rate. The margin test.
//
// Identical to cam_rxdbg except PLL_DIV = 30.0, so the MMCM emits 36.000 MHz
// instead of 72.000 on clk_pll. The sensor's internal PLL still multiplies by
// five, so the serialiser runs at 180 MHz DDR = 360 Mbps per lane instead of
// 720 -- and the data eye is twice as wide.
//
// WHY. Every lane failure seen so far has been the SAME event: the isolated
// single-bit '1' in the training word 0x3A6 arrives as a '0'. d0 showed it as
// 0x386 (bit 5 lost), then after a reflow as 0x1B8 (bit 1 of a different
// rotation lost -- again the isolated pulse), and sync began dropping its
// isolated bit too. A one-bit-wide pulse is the first thing a bandwidth-limited
// or badly-sampled channel loses, and reflowing moved which lane was worst
// without fixing any of them.
//
// So this asks the question directly:
//
//   all five lanes lock at 360 Mbps  -> the link is sound and simply short of
//                                       margin at 720. The fix is IDELAYE2 eye
//                                       centering, which cam_align.v already
//                                       names as the escape hatch -- not more
//                                       solder.
//   d0 still fails at 360 Mbps       -> a genuine hard fault on that pair, and
//                                       reworking pins 9/10 is the right call
//                                       after all.
//
// 36 MHz is inside the 30-45 MHz reference band Avnet's driver handles
// explicitly, so this is a supported operating point, not an abuse.
//
// NOTE the word clock halves too: the recovered wordclk is 36 MHz here, so any
// frequency check expecting 72000 kHz will read 36000 (0x008CA0) and is
// EXPECTED to. cam_rxdbg does not check it; it only prints the raw words.
//=============================================================================
module cam_rxdbg_slow #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200
)(
    input  wire       clk,
    input  wire       rst_n,
    output wire [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,
    input  wire       cam_clkout_p, cam_clkout_n,
    input  wire [3:0] cam_d_p,      cam_d_n,
    input  wire       cam_sync_p,   cam_sync_n,
    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output wire       cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    cam_rxdbg #(
        .CLK_HZ  (CLK_HZ),
        .BAUD    (BAUD),
        .PLL_DIV (30.000)          // <-- 36 MHz reference, 360 Mbps/lane
    ) u_dbg (
        .clk(clk), .rst_n(rst_n), .led(led), .usb_tx(usb_tx), .usb_rx(usb_rx),
        .cam_clkout_p(cam_clkout_p), .cam_clkout_n(cam_clkout_n),
        .cam_d_p(cam_d_p), .cam_d_n(cam_d_n),
        .cam_sync_p(cam_sync_p), .cam_sync_n(cam_sync_n),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );
endmodule