`timescale 1ns/1ps
//=============================================================================
// cam_idelay_stage4 - STAGE 4b: eye-centred receive at the full 720 Mbps.
//
// Stage 4 got four of five lanes at 720 Mbps and all five at 360 Mbps on the
// same joints, which said the joints are fine and the SAMPLING POINT is not
// centred. This adds the per-lane IDELAYE2 and a tap sweep that finds each
// lane's eye and parks in the middle of it.
//
// THE GATE: lk=1F at 720 Mbps -- and, more informative, an eye WIDTH per lane.
//
// SEQUENCE
//   1. boot the sensor to STOP_AT=41 (PLL locked, LVDS drivers on, sequencer
//      still disabled, so the training pattern is streaming)
//   2. cam_eye_scan holds cam_align in reset and sweeps all 32 delay taps,
//      recording for each lane which taps decode the training word cleanly over
//      256 consecutive words
//   3. it parks each lane at the centre of its own widest passing run
//   4. it releases cam_align, which now does bitslip on clean data
//
// READING THE RESULT
//   d0=0E/09  -> lane d0 parked at tap 14, eye 9 taps wide (~0.70 ns)
//   .../00    -> that lane found NO passing tap: a real fault, not margin
//   lk=1F     -> all five aligned
//
// Eye width is the number this project has never had. At ~78 ps per tap a lane
// with 14 taps has ~1.1 ns of margin against a 1.39 ns bit period, which is
// healthy; a lane with 2 taps is nominally "working" and one temperature change
// from not. Expect the previously-failing lanes to show the narrowest eyes.
//
//-----------------------------------------------------------------------------
// THE 200 MHz IDELAYCTRL REFERENCE
//
// IDELAYE2 taps are calibrated against IDELAYCTRL's REFCLK, so it must exist and
// be accurate or the tap size is meaningless. A second MMCM does it: 100 MHz in,
// D=1, M=10 -> VCO 1000 MHz, /5 = 200.000 MHz exact. (The 72 MHz MMCM inside
// cam_boot_stage1 runs a VCO of 1080 MHz, from which 200 is not an integer
// divide, so sharing it would need a fractional output -- a second MMCM is
// cleaner and the Pt has six.)
//
// IDELAYCTRL and every IDELAYE2 carry IODELAY_GROUP = "cam_idelay" so Vivado
// associates the controller with the delays it calibrates.
//
// LEDs
//   led[7]  heartbeat
//   led[6]  ALIGNED, all five lanes    <-- THE GATE
//   led[5]  eye scan complete
//   led[4:0] per-lane lock {sync, d3, d2, d1, d0}
//
// UART:  d0=0E/09 d1=10/0C d2=0F/0B d3=11/0A sy=12/0D lk=1F
//        (tap/width per lane, in hex)
//=============================================================================
module cam_idelay_stage4 #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter integer REP_CY = CLK_HZ / 2
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
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
    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    //----------------------------------------------- 200 MHz for IDELAYCTRL
    wire fb2, fb2_g, c200_raw, clk200, mmcm2_locked;
    MMCME2_BASE #(
        .BANDWIDTH          ("OPTIMIZED"),
        .CLKIN1_PERIOD      (10.000),
        .DIVCLK_DIVIDE      (1),          // D = 1  -> 100 MHz PFD
        .CLKFBOUT_MULT_F    (10.000),     // M = 10 -> VCO 1000 MHz
        .CLKOUT0_DIVIDE_F   (5.000),      // /5     -> 200.000 MHz exact
        .CLKOUT0_DUTY_CYCLE (0.500),
        .STARTUP_WAIT       ("FALSE")
    ) u_mmcm2 (
        .CLKIN1(clk), .CLKFBIN(fb2_g), .CLKFBOUT(fb2), .CLKOUT0(c200_raw),
        .CLKOUT1(), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2B(), .CLKOUT3B(), .CLKFBOUTB(),
        .LOCKED(mmcm2_locked), .PWRDWN(1'b0), .RST(1'b0)
    );
    BUFG u_fb2  (.I(fb2),      .O(fb2_g));
    BUFG u_c200 (.I(c200_raw), .O(clk200));

    // Hold IDELAYCTRL in reset until its reference is stable, then release.
    reg [7:0] idc_cnt = 8'd0;
    reg       idc_rst = 1'b1;
    always @(posedge clk200) begin
        if (!mmcm2_locked) begin idc_cnt <= 8'd0; idc_rst <= 1'b1; end
        else if (idc_cnt != 8'hFF) begin idc_cnt <= idc_cnt + 8'd1; idc_rst <= 1'b1; end
        else idc_rst <= 1'b0;
    end

    wire idc_rdy;
    (* IODELAY_GROUP = "cam_idelay" *)
    IDELAYCTRL u_idc (.REFCLK(clk200), .RST(idc_rst), .RDY(idc_rdy));

    //--------------------------------------------- sensor side: boot to STOP 41
    wire [7:0] boot_led;
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(41)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(1'b0), .streaming(),   // these stages never stream
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

    //---------------------------------------------------- receiver with IDELAY
    wire        wordclk;
    wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word;
    wire [4:0]  bitslip, lane_locked, lane_failed;
    wire        aligned;
    wire [24:0] tap_val;
    wire        tap_ld;

    cam_lvds_rx_idelay u_rx (
        .cam_clkout_p(cam_clkout_p), .cam_clkout_n(cam_clkout_n),
        .cam_d_p(cam_d_p), .cam_d_n(cam_d_n),
        .cam_sync_p(cam_sync_p), .cam_sync_n(cam_sync_n),
        .bitslip(bitslip), .tap_val(tap_val), .tap_ld(tap_ld),
        .wordclk(wordclk),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word)
    );

    // Wordclk-domain reset: does not advance until the sensor's clock exists.
    // Also gated on IDELAYCTRL being ready, since a scan before the taps are
    // calibrated would measure nonsense.
    reg [7:0] wc_cnt = 8'd0;
    reg       wc_rst = 1'b1;
    always @(posedge wordclk) begin
        if (!idc_rdy) begin wc_cnt <= 8'd0; wc_rst <= 1'b1; end
        else if (wc_cnt != 8'hFF) begin wc_cnt <= wc_cnt + 8'd1; wc_rst <= 1'b1; end
        else wc_rst <= 1'b0;
    end

    wire       scan_done, align_rst;
    wire [4:0] bt0, bt1, bt2, bt3, bts;
    wire [5:0] bl0, bl1, bl2, bl3, bls;

    cam_eye_scan u_scan (
        .wordclk(wordclk), .rst(wc_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .tap_val(tap_val), .tap_ld(tap_ld),
        .scan_done(scan_done), .align_rst(align_rst),
        .best_tap0(bt0), .best_tap1(bt1), .best_tap2(bt2),
        .best_tap3(bt3), .best_taps(bts),
        .best_len0(bl0), .best_len1(bl1), .best_len2(bl2),
        .best_len3(bl3), .best_lens(bls)
    );

    cam_align u_align (
        .wordclk(wordclk), .rst(wc_rst | align_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .bitslip(bitslip), .lane_locked(lane_locked),
        .aligned(aligned), .lane_failed(lane_failed)
    );

    //--------------------------------------------------- cross to the clk side
    // Everything below is stable once the scan finishes, so a plain sample is
    // safe; the values stop changing rather than being sampled mid-flight.
    reg [4:0] s_bt0, s_bt1, s_bt2, s_bt3, s_bts;
    reg [5:0] s_bl0, s_bl1, s_bl2, s_bl3, s_bls;
    reg [4:0] lk_s;
    reg [1:0] al_s, sd_s;
    always @(posedge clk) begin
        s_bt0 <= bt0; s_bt1 <= bt1; s_bt2 <= bt2; s_bt3 <= bt3; s_bts <= bts;
        s_bl0 <= bl0; s_bl1 <= bl1; s_bl2 <= bl2; s_bl3 <= bl3; s_bls <= bls;
        lk_s  <= lane_locked;
        al_s  <= {al_s[0], aligned};
        sd_s  <= {sd_s[0], scan_done};
    end

    reg [26:0] rtm = 27'd0;
    reg        msg_go;
    always @(posedge clk) begin
        msg_go <= 1'b0;
        if (rst) rtm <= 27'd0;
        else if (rtm == REP_CY[26:0] - 27'd1) begin rtm <= 27'd0; msg_go <= 1'b1; end
        else rtm <= rtm + 27'd1;
    end

    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;
    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= al_s[1];
        led[5]   <= sd_s[1];
        led[4:0] <= lk_s;
    end

    //------------------------------------------------------------------- UART
    //   d0=0E/09 d1=10/0C d2=0F/0B d3=11/0A sy=12/0D lk=1F
    localparam integer MSG_LEN = 52;

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [5:0] idx = 6'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    // Explicit, one entry per character. A modulo-9 formatter would be shorter
    // to write and would infer dividers; this is a mux.
    //   0-7 "d0=TT/WW"  8 ' '   9-16 "d1=..." 17 ' '  18-25 "d2" 26 ' '
    //  27-34 "d3"      35 ' '  36-43 "sy=..." 44 ' '  45-49 "lk=XX"  50-51 CRLF
    always @(*) begin
        case (idx)
        6'd0 : ch = "d";  6'd1 : ch = "0";  6'd2 : ch = "=";
        6'd3 : ch = hexd({3'b000, s_bt0[4]});   6'd4 : ch = hexd(s_bt0[3:0]);
        6'd5 : ch = "/";
        6'd6 : ch = hexd({2'b00, s_bl0[5:4]});  6'd7 : ch = hexd(s_bl0[3:0]);
        6'd8 : ch = " ";

        6'd9 : ch = "d";  6'd10: ch = "1";  6'd11: ch = "=";
        6'd12: ch = hexd({3'b000, s_bt1[4]});   6'd13: ch = hexd(s_bt1[3:0]);
        6'd14: ch = "/";
        6'd15: ch = hexd({2'b00, s_bl1[5:4]});  6'd16: ch = hexd(s_bl1[3:0]);
        6'd17: ch = " ";

        6'd18: ch = "d";  6'd19: ch = "2";  6'd20: ch = "=";
        6'd21: ch = hexd({3'b000, s_bt2[4]});   6'd22: ch = hexd(s_bt2[3:0]);
        6'd23: ch = "/";
        6'd24: ch = hexd({2'b00, s_bl2[5:4]});  6'd25: ch = hexd(s_bl2[3:0]);
        6'd26: ch = " ";

        6'd27: ch = "d";  6'd28: ch = "3";  6'd29: ch = "=";
        6'd30: ch = hexd({3'b000, s_bt3[4]});   6'd31: ch = hexd(s_bt3[3:0]);
        6'd32: ch = "/";
        6'd33: ch = hexd({2'b00, s_bl3[5:4]});  6'd34: ch = hexd(s_bl3[3:0]);
        6'd35: ch = " ";

        6'd36: ch = "s";  6'd37: ch = "y";  6'd38: ch = "=";
        6'd39: ch = hexd({3'b000, s_bts[4]});   6'd40: ch = hexd(s_bts[3:0]);
        6'd41: ch = "/";
        6'd42: ch = hexd({2'b00, s_bls[5:4]});  6'd43: ch = hexd(s_bls[3:0]);
        6'd44: ch = " ";

        6'd45: ch = "l";  6'd46: ch = "k";  6'd47: ch = "=";
        6'd48: ch = hexd({3'b000, lk_s[4]});    6'd49: ch = hexd(lk_s[3:0]);
        6'd50: ch = 8'h0D;
        6'd51: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin busy_msg <= 1'b0; idx <= 6'd0; end
        else if (!busy_msg) begin
            if (msg_go) begin busy_msg <= 1'b1; idx <= 6'd0; end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[5:0] - 6'd1) begin busy_msg <= 1'b0; idx <= 6'd0; end
            else idx <= idx + 6'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

endmodule
