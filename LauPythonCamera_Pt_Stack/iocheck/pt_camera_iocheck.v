`timescale 1ns / 1ps
//=============================================================================
// pt_camera_iocheck.v
//
// NOT a functional design. Its only job is to hand Vivado a top level whose
// ports are EXACTLY the ones named in pt_camera.xdc, so that
// synth_design / place_design can independently confirm the pin plan against
// Xilinx's own device database -- rather than against my reading of Alchitry's
// source files.
//
// Vivado hard-errors on:
//   * a reversed differential P/N            ("...cannot be assigned to the
//                                              N-side of a differential pair")
//   * a pin that is not half of a legal pair
//   * a bank asked to be two VCCOs at once   (bank 13 @ 2.5 V for the LVDS_25
//                                              pairs, banks 14/35 @ 3.3 V for
//                                              the LVCMOS33 control signals)
//   * DIFF_TERM requested on a bank that is not at 2.5 V
//
// If this places clean, README section 5 is confirmed.
//
// IOSTANDARD and DIFF_TERM are deliberately NOT set on the buffer primitives.
// They come from the XDC, because the XDC is the thing under test.
//=============================================================================
module pt_camera_iocheck (
    // ---- LVDS in, bank 13 (sensor -> FPGA). DIFF_TERM TRUE, set in the XDC.
    input  wire       cam_clkout_p,   // sensor 7/8    forwarded bit clock (MRCC pair)
    input  wire       cam_clkout_n,
    input  wire [3:0] cam_d_p,        // sensor 9..16  four 720 Mbps LVDS lanes
    input  wire [3:0] cam_d_n,
    input  wire       cam_sync_p,     // sensor 17/18
    input  wire       cam_sync_n,

    // ---- LVDS out, bank 13 (FPGA -> sensor). NO DIFF_TERM -- it is an OUTPUT.
    output wire       cam_lvdsclk_p,  // sensor 23/24  ~360 MHz
    output wire       cam_lvdsclk_n,

    // ---- single-ended control, banks 14/35 (hardwired 3.3 V), LVCMOS33
    output wire       cam_mosi,
    input  wire       cam_miso,
    output wire       cam_sck,
    output wire       cam_ss_n,
    output wire       cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);

    //---------------------------------------------------------------- LVDS in
    wire clkout;
    IBUFDS u_clkout (.I(cam_clkout_p), .IB(cam_clkout_n), .O(clkout));

    wire [3:0] d;
    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_lane
            IBUFDS u_d (.I(cam_d_p[i]), .IB(cam_d_n[i]), .O(d[i]));
        end
    endgenerate

    wire sync;
    IBUFDS u_sync (.I(cam_sync_p), .IB(cam_sync_n), .O(sync));

    // cam_clkout is the forwarded bit clock; it must land on an MRCC pair or
    // this BUFG will not route.
    wire clk;
    BUFG u_bufg (.I(clkout), .O(clk));

    //--------------------------------------------------------------- LVDS out
    wire lvdsclk;
    ODDR #(
        .DDR_CLK_EDGE ("OPPOSITE_EDGE"),
        .INIT         (1'b0),
        .SRTYPE       ("SYNC")
    ) u_oddr (
        .Q  (lvdsclk),
        .C  (clk),
        .CE (1'b1),
        .D1 (1'b1),
        .D2 (1'b0),
        .R  (1'b0),
        .S  (1'b0)
    );
    OBUFDS u_oclk (.I(lvdsclk), .O(cam_lvdsclk_p), .OB(cam_lvdsclk_n));

    //---------------------------------------------- keep every port alive
    reg [7:0] acc = 8'd0;
    always @(posedge clk) begin
        acc <= acc + {4'b0, d}
                   + {7'b0, sync}
                   + {6'b0, cam_monitor}
                   + {7'b0, cam_miso};
    end

    assign cam_mosi    = acc[0];
    assign cam_sck     = acc[1];
    assign cam_ss_n    = acc[2];
    assign cam_reset_n = acc[3];
    assign cam_clk_pll = acc[4];
    assign cam_trigger = acc[7:5];

endmodule
