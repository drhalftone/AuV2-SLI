`timescale 1ns / 1ps
//=============================================================================
// pt_camera_rx.v
//
// ############################################################################
// ##  PLACEMENT PROOF ONLY. DO NOT COPY THIS INTO THE REAL DESIGN.          ##
// ############################################################################
//
// This file exists to prove ONE thing to Vivado: that an SRCC pin can drive
// BUFIO + BUFR into a cascaded ISERDESE2 on the even-row pin assignment. It
// does that, and the result is quoted in the README. It is NOT a receiver.
//
// TWO REASONS IT MUST NOT BE PASTED FORWARD:
//
//  1. THE lvds_clock_in OUTPUT IS A STARTUP DEADLOCK. The ODDR near the bottom
//     of this file is clocked from `wordclk`, which is BUFR-divided from the
//     sensor's RETURNED clock_out. So the clock we send TO the sensor is derived
//     from the clock the sensor sends BACK: at power-up the sensor has no input
//     clock, emits no output clock, and we therefore never generate its input
//     clock. It can never start. That is harmless here (nothing is being
//     simulated; the loop just has to place) and fatal anywhere else.
//
//  2. WE NO LONGER DRIVE lvds_clock_in AT ALL. The design uses the sensor's
//     INTERNAL PLL: the FPGA supplies 72 MHz CMOS on clk_pll and the sensor
//     multiplies x5 internally. See CAMERA_SENSOR_PROTOCOL.md §4. The whole
//     360 MHz LVDS transmit path -- ODDR, OBUFDS, and the deadlock with it --
//     does not exist in the real receiver.
//
// Also: BITSLIP is tied to 0 and IOBDELAY is "NONE", so there is no word
// alignment here whatsoever, and nothing decodes the sync channel.
//
// The real receiver is cam_lvds_rx.v (task #8). See CAMERA_RTL_PLAN.md.
//
// WHY THIS EXISTS
// ---------------
// The DF40's two rows escape in OPPOSITE directions. Bank B sits at y=41 on a
// 45 mm board, so its ODD row escapes into a 2.6 mm strip against the board
// edge, and only its EVEN row faces the sensor. A 16.76 mm socket cannot fit
// below Bank B, so the sensor must sit above it -- and the LVDS must therefore
// land on the EVEN row.
//
// Bank 13's even row has no MRCC pairs. It has two SRCC pairs. This design
// exists to prove that an SRCC pin can drive BUFIO + BUFR into a cascaded
// ISERDESE2 -- i.e. that the real 1:10 LVDS receiver places on these pins.
//
// A BUFG (which needs MRCC) is the wrong structure for a 720 Mbps source-
// synchronous link anyway; BUFIO is the low-skew I/O clock you actually want.
//
// STRUCTURE (standard Xilinx 7-series 1:10 LVDS RX)
//   IBUFDS  -> BUFIO   -> ISERDESE2.CLK      (360 MHz bit clock, DDR)
//           -> BUFR/5  -> ISERDESE2.CLKDIV   (72 MHz word clock)
//   ISERDESE2 master+slave cascade = 10 bits per lane
//   4 data lanes + 1 sync channel = 5 deserialisers
//
//   720 Mbps/lane, DDR, 10-bit  ->  bit clock 360 MHz, word clock 72 MHz.
//   BUFR_DIVIDE = 5  (360 / 72).
//=============================================================================
module pt_camera_rx (
    // ---- LVDS in, bank 13 @ 2.5 V, EVEN row of DF40 Bank B ----
    input  wire        cam_clkout_p,   // sensor 7/8    forwarded bit clock (SRCC)
    input  wire        cam_clkout_n,
    input  wire [3:0]  cam_d_p,        // sensor 9..16
    input  wire [3:0]  cam_d_n,
    input  wire        cam_sync_p,     // sensor 17/18
    input  wire        cam_sync_n,

    // ---- LVDS out, bank 13 (FPGA -> sensor) ----
    output wire        cam_lvdsclk_p,  // sensor 23/24  ~360 MHz
    output wire        cam_lvdsclk_n,

    // ---- single-ended control, banks 14/35 @ 3.3 V ----
    output wire        cam_mosi,
    input  wire        cam_miso,
    output wire        cam_sck,
    output wire        cam_ss_n,
    output wire        cam_reset_n,
    output wire        cam_clk_pll,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor
);
    // NOTE: the deserialised pixel data is kept INTERNAL on purpose.
    // Exposing word_clk / pix[] as top-level ports without constraining them
    // makes Vivado default them to LVCMOS18 and then try to place a BUFR-driven
    // output inside the 2.5 V bank -- which fails with [Place 30-294] and looks
    // like an SRCC problem when it is really a testbench problem.
    reg [9:0] pix0, pix1, pix2, pix3, sync_word;

    genvar i;

    //---------------------------------------------------------------- clocking
    wire bitclk_raw;
    IBUFDS u_clk (.I(cam_clkout_p), .IB(cam_clkout_n), .O(bitclk_raw));

    // BUFIO: the low-skew I/O clock. Drivable from an SRCC or MRCC pin in the
    // SAME clock region. Bank 13 is one region, and every LVDS pin is in it.
    wire bitclk;
    BUFIO u_bufio (.I(bitclk_raw), .O(bitclk));

    // BUFR: the divided word clock, same region.
    wire wordclk;
    BUFR #(.BUFR_DIVIDE("5"), .SIM_DEVICE("7SERIES")) u_bufr (
        .I(bitclk_raw), .O(wordclk), .CE(1'b1), .CLR(1'b0));

    //---------------------------------------------- 1:10 deserialiser per lane
    wire [9:0] q [0:4];          // 0..3 = data lanes, 4 = sync channel
    wire [4:0] din;

    IBUFDS u_sync (.I(cam_sync_p), .IB(cam_sync_n), .O(din[4]));
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_lane
            IBUFDS u_d (.I(cam_d_p[i]), .IB(cam_d_n[i]), .O(din[i]));
        end

        for (i = 0; i < 5; i = i + 1) begin : g_des
            wire s1, s2;
            ISERDESE2 #(
                .DATA_RATE      ("DDR"),
                .DATA_WIDTH     (10),
                .INTERFACE_TYPE ("NETWORKING"),
                .NUM_CE         (2),
                .SERDES_MODE    ("MASTER"),
                .IOBDELAY       ("NONE")
            ) u_m (
                .CLK(bitclk), .CLKB(~bitclk), .CLKDIV(wordclk),
                .D(din[i]), .DDLY(1'b0), .BITSLIP(1'b0),
                .CE1(1'b1), .CE2(1'b1), .RST(1'b0),
                .SHIFTIN1(1'b0), .SHIFTIN2(1'b0),
                .SHIFTOUT1(s1), .SHIFTOUT2(s2),
                .Q1(q[i][0]), .Q2(q[i][1]), .Q3(q[i][2]), .Q4(q[i][3]),
                .Q5(q[i][4]), .Q6(q[i][5]), .Q7(q[i][6]), .Q8(q[i][7]),
                .CLKDIVP(1'b0), .OCLK(1'b0), .OCLKB(1'b0), .OFB(1'b0),
                .DYNCLKDIVSEL(1'b0), .DYNCLKSEL(1'b0)
            );
            ISERDESE2 #(
                .DATA_RATE      ("DDR"),
                .DATA_WIDTH     (10),
                .INTERFACE_TYPE ("NETWORKING"),
                .NUM_CE         (2),
                .SERDES_MODE    ("SLAVE"),
                .IOBDELAY       ("NONE")
            ) u_s (
                .CLK(bitclk), .CLKB(~bitclk), .CLKDIV(wordclk),
                .D(1'b0), .DDLY(1'b0), .BITSLIP(1'b0),
                .CE1(1'b1), .CE2(1'b1), .RST(1'b0),
                .SHIFTIN1(s1), .SHIFTIN2(s2),
                .Q3(q[i][8]), .Q4(q[i][9]),
                .CLKDIVP(1'b0), .OCLK(1'b0), .OCLKB(1'b0), .OFB(1'b0),
                .DYNCLKDIVSEL(1'b0), .DYNCLKSEL(1'b0)
            );
        end
    endgenerate

    always @(posedge wordclk) begin
        pix0      <= q[0];
        pix1      <= q[1];
        pix2      <= q[2];
        pix3      <= q[3];
        sync_word <= q[4];
    end

    //------------------------------------- LVDS clock OUT to the sensor
    wire lvdsclk;
    ODDR #(.DDR_CLK_EDGE("OPPOSITE_EDGE"), .INIT(1'b0), .SRTYPE("SYNC")) u_oddr (
        .Q(lvdsclk), .C(wordclk), .CE(1'b1), .D1(1'b1), .D2(1'b0), .R(1'b0), .S(1'b0));
    OBUFDS u_oclk (.I(lvdsclk), .O(cam_lvdsclk_p), .OB(cam_lvdsclk_n));

    //------------------------------------- control (slow, keeps the ports alive)
    // Consume ALL TEN bits of every lane. If only [7:0] are used, the SLAVE
    // half of each ISERDESE2 cascade is optimised away and you get 5 ISERDES
    // instead of 10 -- which looks like a placement failure and is not.
    reg [9:0] acc = 10'd0;
    always @(posedge wordclk)
        acc <= acc + {8'b0, cam_monitor} + {9'b0, cam_miso}
                   ^ pix0 ^ pix1 ^ pix2 ^ pix3 ^ sync_word;
    assign cam_mosi    = acc[0];
    assign cam_sck     = acc[1];
    assign cam_ss_n    = acc[2];
    assign cam_reset_n = acc[3];
    assign cam_clk_pll = acc[4] ^ acc[8] ^ acc[9];
    assign cam_trigger = acc[7:5];

endmodule
