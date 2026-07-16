`timescale 1ns / 1ps
//=============================================================================
// cam_lvds_rx.v - PYTHON 1300 1:10 LVDS receiver (PLL mode).
//
// The real receiver. Structure is the proven one from iocheck/pt_camera_rx.v -- which
// Vivado has already placed on the even-row SRCC pins -- but with the two things that
// stub deliberately lacked:
//   - NO transmit-clock path. In PLL mode the FPGA drives 72 MHz on clk_pll and the sensor
//     multiplies x5 internally, so we never drive lvds_clock_in. The stub's ODDR/OBUFDS
//     (and its startup deadlock) are simply gone. See CAMERA_SENSOR_PROTOCOL.md §4.
//   - Per-lane BITSLIP inputs, so word alignment is possible (the stub tied them to 0).
//     The alignment FSM that drives them is cam_align (task #9); this module just wires
//     them out and deserialises.
//
// CLOCKING (source-synchronous, from the sensor's forwarded clock):
//   cam_clkout ->IBUFDS-> BUFIO  -> ISERDESE2.CLK    (360 MHz bit clock, DDR)
//                     \-> BUFR/5 -> ISERDESE2.CLKDIV (72 MHz word clock)
//   5 channels (4 data + sync), each a master+slave ISERDESE2 cascade = 10 bits/word.
//
// COLD START: the sensor emits no clock_out until its PLL locks, so bitclk/wordclk simply
// do not run until then. Everything downstream is in the wordclk domain and comes up when
// the clock appears. The ISERDESE2 primitives DO still need a reset sequenced synchronous to
// CLKDIV once that clock exists -- see the serdes_rst counter below; the iocheck stub omitted
// it (tied RST=0) and left the outputs undefined.
//
// BIT ORDER: the model sends MSB first. ISERDESE2 Q1..Q8/Q3s/Q4s emit in receive-time
// order; word_bit_map() below folds that into a 10-bit value whose interpretation the
// task-#8 testbench pins down against the known 0x3A6 training pattern. Bitslip rotates the
// WORD BOUNDARY; it does not reorder bits within the captured window.
//=============================================================================
module cam_lvds_rx (
    // LVDS in (bank 13 @ 2.5 V on the Pt; from the model in sim)
    input  wire        cam_clkout_p, cam_clkout_n,
    input  wire [3:0]  cam_d_p,  cam_d_n,
    input  wire        cam_sync_p, cam_sync_n,

    // per-lane bitslip, {sync, d3, d2, d1, d0}. One pulse rotates that lane's window by 1.
    input  wire [4:0]  bitslip,

    // deserialised output, in the wordclk domain
    output wire        wordclk,           // 72 MHz recovered word clock
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

    // ISERDESE2 powers up in an unknown state and must see RST asserted then released
    // SYNCHRONOUS to CLKDIV (= wclk) before its Q outputs are valid. The iocheck stub tied
    // RST to 0 -- fine for a placement proof, but it leaves the outputs at X in simulation
    // and undefined on real silicon. BUFR free-runs (CLR=0), so wclk is available to time
    // this: hold RST for the first 31 word clocks after the sensor's clock appears, then
    // release. Registers power up to their INIT on 7-series, so rcnt/serdes_rst start known.
    reg [4:0] rcnt = 5'd0;
    reg       serdes_rst = 1'b1;
    always @(posedge wclk) begin
        if (rcnt != 5'h1F) begin rcnt <= rcnt + 5'd1; serdes_rst <= 1'b1; end
        else                     serdes_rst <= 1'b0;
    end

    //---------------------------------------------- 1:10 deserialiser per lane
    wire [4:0] din;
    IBUFDS u_sync (.I(cam_sync_p), .IB(cam_sync_n), .O(din[4]));
    genvar i;
    generate
        for (i = 0; i < 4; i = i + 1) begin : g_ibuf
            IBUFDS u_d (.I(cam_d_p[i]), .IB(cam_d_n[i]), .O(din[i]));
        end
    endgenerate

    wire [9:0] q [0:4];
    generate
        for (i = 0; i < 5; i = i + 1) begin : g_des
            wire s1, s2;
            ISERDESE2 #(
                .DATA_RATE("DDR"), .DATA_WIDTH(10),
                .INTERFACE_TYPE("NETWORKING"), .NUM_CE(2),
                .SERDES_MODE("MASTER"), .IOBDELAY("NONE")
            ) u_m (
                .CLK(bitclk), .CLKB(~bitclk), .CLKDIV(wclk),
                .D(din[i]), .DDLY(1'b0), .BITSLIP(bitslip[i]),
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
                .SERDES_MODE("SLAVE"), .IOBDELAY("NONE")
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

    //---------------------------------------------- word bit mapping
    // IDENTITY -- the ISERDES already presents the word with the MSB at bit [9].
    //
    // Determined empirically, not assumed (tb_probe): the model sends 0x3A6 MSB first, and
    // at the training-aligned bitslip the RAW q reads exactly 0x3A6, cycling through all ten
    // rotations of 0x3A6 as bitslip steps. So q[9] is the first-in-time (MSB) bit -- no
    // reversal. (An earlier reverse was exactly backwards: it turned 0x3A6 into 0x197, which
    // bitslip can never reach because bitslip rotates the window, it does not reorder bits.)
    reg [9:0] d0r, d1r, d2r, d3r, syr;
    always @(posedge wclk) begin
        d0r <= q[0];
        d1r <= q[1];
        d2r <= q[2];
        d3r <= q[3];
        syr <= q[4];
    end
    assign d0_word = d0r;  assign d1_word = d1r;  assign d2_word = d2r;
    assign d3_word = d3r;  assign sync_word = syr;

endmodule
