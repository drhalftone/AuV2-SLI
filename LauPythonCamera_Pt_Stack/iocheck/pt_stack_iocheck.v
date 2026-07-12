`timescale 1ns / 1ps
//=============================================================================
// pt_stack_iocheck.v
//
// THE WHOLE STACK, in one top level:
//
//     Alchitry Pt V2  +  Hd (bottom)  +  Ft+ (bottom)  +  camera (top)
//
// pt_camera_iocheck.v proved the CAMERA's 25 pins are self-consistent. It did
// NOT prove that the Hd and the Ft+ can coexist with them -- that claim rested
// entirely on reading Alchitry's .acf sources by hand.
//
// This does. It is built against Alchitry's OWN published constraint files
// (pt_base.xdc, pt_hd_bottom.xdc, pt_ft_plus_bottom.xdc) plus our pt_camera.xdc,
// all four loaded together. 105 ports.
//
// If Vivado places this, then:
//   * no pin is claimed twice across the four boards
//   * bank 13 @ 2.5 V (camera LVDS) coexists with banks 14/16/34/35 @ 3.3 V
//     (HDMI TMDS_33 + Ft+ LVCMOS33 + everything else)
//   * TMDS_33 outputs and LVDS_25 inputs are simultaneously legal on this device
//
// That is the entire stack-compatibility argument, checked by the tool instead
// of by me.
//
// NOT a functional design -- but note the HDMI section is wired as a genuine
// pass-through (port 1 = sink from the host PC, port 2 = source to the
// projector), which is what the real SLI system does.
//=============================================================================
module pt_stack_iocheck (
    // ---------------- Pt V2 base ----------------
    input  wire        clk,
    input  wire        rst_n,
    output wire [7:0]  led,
    input  wire        usb_rx,
    output wire        usb_tx,

    // ---------------- Hd, port 1 : SINK (from host PC) ----------------
    input  wire        hdmi_clk_1_p,
    input  wire        hdmi_clk_1_n,
    input  wire [2:0]  hdmi_data_1_p,
    input  wire [2:0]  hdmi_data_1_n,
    inout  wire        hdmi_sda_1,
    inout  wire        hdmi_scl_1,
    inout  wire        hdmi_cec_1,
    output wire        hdmi_hp_1,       // FPGA asserts hot-plug toward the PC

    // ---------------- Hd, port 2 : SOURCE (to projector) ----------------
    output wire        hdmi_clk_2_p,
    output wire        hdmi_clk_2_n,
    output wire [2:0]  hdmi_data_2_p,
    output wire [2:0]  hdmi_data_2_n,
    inout  wire        hdmi_sda_2,
    inout  wire        hdmi_scl_2,
    inout  wire        hdmi_cec_2,
    input  wire        hdmi_hp_2,       // projector asserts hot-plug toward us

    // ---------------- Ft+ : FT601Q, 32-bit USB3 FIFO ----------------
    input  wire        ft_clk,
    inout  wire [31:0] ft_data,
    inout  wire [3:0]  ft_be,
    input  wire        ft_rxf,
    input  wire        ft_txe,
    output wire        ft_oe,
    output wire        ft_rd,
    output wire        ft_wr,
    output wire        ft_wakeup,
    output wire        ft_reset,

    // ---------------- Camera : bank 13 (LVDS) + banks 14/35 (control) ------
    input  wire        cam_clkout_p,
    input  wire        cam_clkout_n,
    input  wire [3:0]  cam_d_p,
    input  wire [3:0]  cam_d_n,
    input  wire        cam_sync_p,
    input  wire        cam_sync_n,
    output wire        cam_lvdsclk_p,
    output wire        cam_lvdsclk_n,
    output wire        cam_mosi,
    input  wire        cam_miso,
    output wire        cam_sck,
    output wire        cam_ss_n,
    output wire        cam_reset_n,
    output wire        cam_clk_pll,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor
);

    genvar i;

    //================================================== HDMI  (TMDS_33)
    // Port 1 in -> port 2 out. A real pass-through, which is what the SLI
    // system does: host PC -> FPGA -> projector.
    wire hdmi_clk_in;
    IBUFDS u_hclk (.I(hdmi_clk_1_p), .IB(hdmi_clk_1_n), .O(hdmi_clk_in));
    OBUFDS u_hclk_o (.I(hdmi_clk_in), .O(hdmi_clk_2_p), .OB(hdmi_clk_2_n));

    wire [2:0] hdmi_d;
    generate
        for (i = 0; i < 3; i = i + 1) begin : g_tmds
            IBUFDS u_i (.I(hdmi_data_1_p[i]), .IB(hdmi_data_1_n[i]), .O(hdmi_d[i]));
            OBUFDS u_o (.I(hdmi_d[i]), .O(hdmi_data_2_p[i]), .OB(hdmi_data_2_n[i]));
        end
    endgenerate

    // DDC / CEC are open-drain-ish; model them as tri-states so the ports stay.
    reg ddc_drive = 1'b0;
    assign hdmi_sda_1 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_scl_1 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_cec_1 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_sda_2 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_scl_2 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_cec_2 = ddc_drive ? 1'b0 : 1'bz;
    assign hdmi_hp_1  = hdmi_hp_2;          // mirror the projector's HPD to the PC

    //================================================== Ft+  (LVCMOS33)
    reg        ft_drive = 1'b0;
    reg [31:0] ft_out   = 32'd0;
    assign ft_data   = ft_drive ? ft_out    : 32'bz;
    assign ft_be     = ft_drive ? 4'hF      : 4'bz;
    assign ft_oe     = ~ft_drive;
    assign ft_rd     = ft_rxf;
    assign ft_wr     = ft_txe;
    assign ft_wakeup = 1'b1;
    assign ft_reset  = rst_n;

    always @(posedge ft_clk) begin
        ft_drive <= ~ft_rxf;
        ft_out   <= ft_out + {31'd0, ft_txe} + ft_data;
    end

    //================================================== Camera  (LVDS_25, bank 13)
    wire cam_bitclk;
    IBUFDS u_cclk (.I(cam_clkout_p), .IB(cam_clkout_n), .O(cam_bitclk));

    wire [3:0] cam_d;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_lane
            IBUFDS u_d (.I(cam_d_p[i]), .IB(cam_d_n[i]), .O(cam_d[i]));
        end
    endgenerate

    wire cam_sync;
    IBUFDS u_csync (.I(cam_sync_p), .IB(cam_sync_n), .O(cam_sync));

    wire cam_clk;
    BUFG u_cbufg (.I(cam_bitclk), .O(cam_clk));

    // the ~360 MHz LVDS clock we drive INTO the sensor. No DIFF_TERM: output.
    wire cam_lvdsclk;
    ODDR #(.DDR_CLK_EDGE("OPPOSITE_EDGE"), .INIT(1'b0), .SRTYPE("SYNC")) u_coddr (
        .Q(cam_lvdsclk), .C(cam_clk), .CE(1'b1), .D1(1'b1), .D2(1'b0), .R(1'b0), .S(1'b0));
    OBUFDS u_coclk (.I(cam_lvdsclk), .O(cam_lvdsclk_p), .OB(cam_lvdsclk_n));

    reg [7:0] cam_acc = 8'd0;
    always @(posedge cam_clk) begin
        cam_acc <= cam_acc + {4'b0, cam_d} + {7'b0, cam_sync}
                           + {6'b0, cam_monitor} + {7'b0, cam_miso};
    end
    assign cam_mosi    = cam_acc[0];
    assign cam_sck     = cam_acc[1];
    assign cam_ss_n    = cam_acc[2];
    assign cam_reset_n = cam_acc[3];
    assign cam_clk_pll = cam_acc[4];
    assign cam_trigger = cam_acc[7:5];

    //================================================== base
    reg [7:0] heartbeat = 8'd0;
    always @(posedge clk) heartbeat <= heartbeat + {7'd0, usb_rx} + {7'd0, rst_n};
    assign led    = heartbeat ^ cam_acc ^ hdmi_d[2:0];
    assign usb_tx = heartbeat[0];

endmodule
