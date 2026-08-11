`timescale 1ns/1ps
//=============================================================================
// cam_lvds_rx_idelay - cam_lvds_rx with a per-lane IDELAYE2 in front of each
// ISERDESE2, so the sampling point can be moved inside the data eye.
//
// WHY THIS EXISTS. cam_lvds_rx sets IOBDELAY("NONE"): the ISERDES samples
// wherever the recovered clock happens to land, with no way to move it. On this
// board at 720 Mbps that is not good enough -- three of five lanes intermittently
// lose the isolated single-bit '1' in the training word, while the SAME lanes on
// the SAME joints are flawless at 360 Mbps. The joints are good; the sampling
// point is not centred.
//
// cam_align.v named this fix in advance:
//   "If a real board ever shows a lane that cannot find a clean rotation,
//    revisit with IDELAYE2 + IDELAYCTRL (200 MHz ref already exists ...)"
//
// SEPARATE MODULE ON PURPOSE. cam_lvds_rx is instantiated by Au2_SLI. This is a
// bring-up variant living in hello/ so the shared receiver stays untouched until
// eye-centring is proven on hardware. Once it is, this folds back into
// cam_lvds_rx behind a parameter.
//
//-----------------------------------------------------------------------------
// VAR_LOAD, not INC/CE. IDELAY_TYPE("VAR_LOAD") lets the tap be written
// directly through CNTVALUEIN + LD, which makes a scan a simple loop rather
// than a stepping protocol, and makes the final "park at the centre" a single
// write per lane. Taps are 0..31; at 200 MHz REFCLK each is ~78 ps, so the full
// range is ~2.4 ns -- comfortably more than the 1.39 ns bit period at 720 Mbps,
// so at least one full eye is always reachable.
//
// IDELAYCTRL is instantiated by the TOP, not here, because it is per-region and
// needs the 200 MHz reference. Both it and these IDELAYE2s carry the same
// IODELAY_GROUP so Vivado associates them.
//=============================================================================
module cam_lvds_rx_idelay (
    // LVDS in, bank 13
    input  wire        cam_clkout_p, cam_clkout_n,
    input  wire [3:0]  cam_d_p,  cam_d_n,
    input  wire        cam_sync_p, cam_sync_n,

    // per-lane bitslip, {sync, d3, d2, d1, d0}
    input  wire [4:0]  bitslip,

    // per-lane delay taps, 5 bits each, packed {sync, d3, d2, d1, d0}.
    // Loaded on a single `tap_ld` pulse, synchronous to wordclk.
    input  wire [24:0] tap_val,
    input  wire        tap_ld,

    output wire        wordclk,
    output wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word
);
    //---------------------------------------------------------------- clocking
    wire bitclk_raw;
    IBUFDS u_clk (.I(cam_clkout_p), .IB(cam_clkout_n), .O(bitclk_raw));

    wire bitclk;
    BUFIO u_bufio (.I(bitclk_raw), .O(bitclk));

    wire wclk;
    BUFR #(.BUFR_DIVIDE("5"), .SIM_DEVICE("7SERIES")) u_bufr (
        .I(bitclk_raw), .O(wclk), .CE(1'b1), .CLR(1'b0));
    assign wordclk = wclk;

    // Same CLKDIV-synchronous ISERDES reset as cam_lvds_rx: the primitives come
    // up unknown and must see RST asserted then released against a running
    // CLKDIV, which does not exist until the sensor's PLL locks.
    reg [4:0] rcnt = 5'd0;
    reg       serdes_rst = 1'b1;
    always @(posedge wclk) begin
        if (rcnt != 5'h1F) begin rcnt <= rcnt + 5'd1; serdes_rst <= 1'b1; end
        else                     serdes_rst <= 1'b0;
    end

    //------------------------------------------------------------ input buffers
    wire [4:0] din;
    IBUFDS u_sync (.I(cam_sync_p), .IB(cam_sync_n), .O(din[4]));
    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_ibuf
            IBUFDS u_d (.I(cam_d_p[i]), .IB(cam_d_n[i]), .O(din[i]));
        end
    endgenerate

    //-------------------------------------- IDELAY + 1:10 deserialiser per lane
    wire [9:0] q [0:4];
    generate
        for (i = 0; i < 5; i = i + 1) begin : g_des
            wire s1, s2;
            wire ddly;

            (* IODELAY_GROUP = "cam_idelay" *)
            IDELAYE2 #(
                .IDELAY_TYPE           ("VAR_LOAD"),
                .DELAY_SRC             ("IDATAIN"),
                .HIGH_PERFORMANCE_MODE ("TRUE"),
                .IDELAY_VALUE          (0),
                .REFCLK_FREQUENCY      (200.0),
                .SIGNAL_PATTERN        ("DATA"),
                .CINVCTRL_SEL          ("FALSE"),
                .PIPE_SEL              ("FALSE")
            ) u_idly (
                .C           (wclk),
                .LD          (tap_ld),
                .CNTVALUEIN  (tap_val[i*5 +: 5]),
                .CNTVALUEOUT (),
                .IDATAIN     (din[i]),
                .DATAOUT     (ddly),
                .CE          (1'b0),
                .INC         (1'b0),
                .LDPIPEEN    (1'b0),
                .REGRST      (1'b0),
                .CINVCTRL    (1'b0),
                .DATAIN      (1'b0)
            );

            // IOBDELAY("IFD") routes the DELAYED data (DDLY) into the ISERDES
            // input registers. D is left tied off -- with IFD it is unused.
            ISERDESE2 #(
                .DATA_RATE("DDR"), .DATA_WIDTH(10),
                .INTERFACE_TYPE("NETWORKING"), .NUM_CE(2),
                .SERDES_MODE("MASTER"), .IOBDELAY("IFD")
            ) u_m (
                .CLK(bitclk), .CLKB(~bitclk), .CLKDIV(wclk),
                .D(1'b0), .DDLY(ddly), .BITSLIP(bitslip[i]),
                .CE1(1'b1), .CE2(1'b1), .RST(serdes_rst),
                .SHIFTIN1(1'b0), .SHIFTIN2(1'b0), .SHIFTOUT1(s1), .SHIFTOUT2(s2),
                .Q1(q[i][0]), .Q2(q[i][1]), .Q3(q[i][2]), .Q4(q[i][3]),
                .Q5(q[i][4]), .Q6(q[i][5]), .Q7(q[i][6]), .Q8(q[i][7]),
                .CLKDIVP(1'b0), .OCLK(1'b0), .OCLKB(1'b0), .OFB(1'b0),
                .DYNCLKDIVSEL(1'b0), .DYNCLKSEL(1'b0)
            );
            ISERDESE2 #(
                .DATA_RATE("DDR"), .DATA_WIDTH(10),
                .INTERFACE_TYPE("NETWORKING"), .NUM_CE(2),
                .SERDES_MODE("SLAVE"), .IOBDELAY("IFD")
            ) u_s (
                .CLK(bitclk), .CLKB(~bitclk), .CLKDIV(wclk),
                .D(1'b0), .DDLY(1'b0), .BITSLIP(bitslip[i]),
                .CE1(1'b1), .CE2(1'b1), .RST(serdes_rst),
                .SHIFTIN1(s1), .SHIFTIN2(s2),
                .Q3(q[i][8]), .Q4(q[i][9]),
                .CLKDIVP(1'b0), .OCLK(1'b0), .OCLKB(1'b0), .OFB(1'b0),
                .DYNCLKDIVSEL(1'b0), .DYNCLKSEL(1'b0)
            );
        end
    endgenerate

    reg [9:0] d0r, d1r, d2r, d3r, syr;
    always @(posedge wclk) begin
        d0r <= q[0];  d1r <= q[1];  d2r <= q[2];  d3r <= q[3];  syr <= q[4];
    end
    assign d0_word = d0r;  assign d1_word = d1r;  assign d2_word = d2r;
    assign d3_word = d3r;  assign sync_word = syr;

endmodule
