`timescale 1ns/1ps
//=============================================================================
// cam_roi_top -- the camera, alone, reporting one number per frame.
//
// Derived from cam_frame_stage6 by DELETING its frame buffer. Everything up to
// and including cam_sync_decode is stage6 verbatim -- the same boot sequencer,
// the same LVDS receiver, the same eye scan, the same aligner, the same decoder,
// in the same order with the same resets. That chain is proven; this file must
// not be where it gets re-typed and subtly changed.
//
// WHAT REPLACES THE FRAME BUFFER. stage6 stored 1280x64 pixels in ~15 BRAMs and
// streamed them to the PC. We do not want the picture -- we want the MEAN of a
// 16x16 patch, one scalar per frame. Computing it as the pixels stream past
// removes the buffer entirely:
//
//   * no block RAM at all, so the BUFR clock-region constraint that capped
//     stage6 at 64 lines simply does not apply -- this sees EVERY line;
//   * no DDR3 and no MIG, which is what dominates the merged build;
//   * no FT601, so the Ft+ does not even need to be cabled;
//   * 18 bytes per frame instead of 80 kB, so it fits the UART with room over.
//
// WHY THIS EXISTS SEPARATELY FROM THE MERGED BUILD. Same argument stage6's own
// README makes: the merged design reaches the same pixels, but it also brings up
// HDMI, an EDID stack, a DRP clock generator, DDR3 and USB 3. If the ROI mean
// comes back wrong in there, it has a hundred possible causes. Here it has
// almost none -- a sensor, four LVDS lanes, a decoder and an adder.
//
// FREE-RUNNING, NOT TRIGGERED. cam_boot_stage1 is instantiated with
// TRIGGERED(0), which is the configuration that has always produced sane pixels
// on this sensor; triggered mode over-integrates and saturates. Genlock and a
// commanded exposure belong to the merged design, not to a bring-up whose only
// question is "does the ROI mean track the light".
//
// THE LINE FORMAT IS roi_line.v's, byte for byte, so host/roi_live.py parses
// this build and the merged build identically.
//=============================================================================
module cam_roi_top #(
    parameter integer CLK_HZ   = 100_000_000,
    parameter integer BAUD     =     115_200,  // matches host/roi_live.py
    parameter [7:0]   ROI_COL8 = 8'd80,        // 80*8 = column 640
    parameter [7:0]   ROI_ROW8 = 8'd64,        // 64*8 = row 512
    parameter [15:0]  EXPOSURE = 16'h01F4      // 500 x 375 ns = 187.5 us
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
    //------------------------------------------------------------ reset (stage6)
    reg [1:0] rstn_sync = 2'b00;
    always @(posedge clk) rstn_sync <= {rstn_sync[0], rst_n};
    reg [7:0] por = 8'd0;
    always @(posedge clk) if (!por[7]) por <= por + 8'd1;
    wire rst = !por[7] || !rstn_sync[1];

    //----------------------------------------------- 200 MHz for IDELAYCTRL
    wire fb2, fb2_g, c200_raw, clk200, mmcm2_locked;
    MMCME2_BASE #(
        .BANDWIDTH("OPTIMIZED"), .CLKIN1_PERIOD(10.000),
        .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(10.000),
        .CLKOUT0_DIVIDE_F(5.000), .CLKOUT0_DUTY_CYCLE(0.500),
        .STARTUP_WAIT("FALSE")
    ) u_mmcm2 (
        .CLKIN1(clk), .CLKFBIN(fb2_g), .CLKFBOUT(fb2), .CLKOUT0(c200_raw),
        .CLKOUT1(), .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2B(), .CLKOUT3B(), .CLKFBOUTB(),
        .LOCKED(mmcm2_locked), .PWRDWN(1'b0), .RST(1'b0)
    );
    BUFG u_fb2  (.I(fb2),      .O(fb2_g));
    BUFG u_c200 (.I(c200_raw), .O(clk200));

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

    //--------------------------------------------- sensor boot / SPI / reset
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(45),
                      .TRIGGERED(0), .TESTPAT(0), .EXPOSURE(EXPOSURE)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(),
        .cam_monitor(cam_monitor)
    );
    // Free-running: the sensor paces itself, nothing here drives a trigger.
    assign cam_trigger = 3'b000;

    //---------------------------------------------------- receive + align
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

    // IDELAYE2 EYE CENTRING IS NOT OPTIONAL at 720 Mbps. Without it one isolated
    // bit drops and it looks exactly like a bad solder joint.
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

    // start the sensor streaming once the taps are parked and all lanes aligned
    reg [1:0] rdy_s = 2'b00;
    reg       fired = 1'b0;
    always @(posedge clk) begin
        stream_go <= 1'b0;
        rdy_s <= {rdy_s[0], (scan_done & aligned)};
        if (rst) fired <= 1'b0;
        else if (rdy_s[1] && !fired && !streaming) begin
            stream_go <= 1'b1;
            fired     <= 1'b1;
        end
    end

    //------------------------------------------------------- sync decode
    wire [9:0]  kp0,kp1,kp2,kp3,kp4,kp5,kp6,kp7;
    wire [10:0] kbase;
    wire        kvalid, line_start, frame_start, frame_end, in_black;

    cam_sync_decode u_dec (
        .wordclk(wordclk), .rst(wc_rst), .aligned(aligned),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .kpix0(kp0), .kpix1(kp1), .kpix2(kp2), .kpix3(kp3),
        .kpix4(kp4), .kpix5(kp5), .kpix6(kp6), .kpix7(kp7),
        .kbase(kbase), .kvalid(kvalid),
        .line_start(line_start), .frame_start(frame_start),
        .frame_end(frame_end), .in_black(in_black)
    );

    //=========================================================================
    // ...and here the frame buffer used to be.
    //=========================================================================
    wire [9:0]  roi_mean_w;
    wire [8:0]  roi_npx_w;
    wire [15:0] roi_fcnt_w;
    wire        roi_blk_w, roi_done_w;

    roi_mean u_roi (
        .wordclk(wordclk), .rst(wc_rst),
        .roi_col8(ROI_COL8), .roi_row8(ROI_ROW8),
        .kvalid(kvalid), .kbase(kbase),
        .kp0(kp0), .kp1(kp1), .kp2(kp2), .kp3(kp3),
        .kp4(kp4), .kp5(kp5), .kp6(kp6), .kp7(kp7),
        .line_start(line_start), .frame_start(frame_start),
        .frame_end(frame_end), .in_black(in_black),
        .mean(roi_mean_w), .npx(roi_npx_w), .fcnt(roi_fcnt_w),
        .blk(roi_blk_w), .done(roi_done_w)
    );

    // wordclk -> clk. Toggle handshake with a payload held for a whole frame
    // period, five orders of magnitude longer than the two sync flops.
    reg [35:0] hold_w = 36'd0;
    reg        tog_w  = 1'b0;
    always @(posedge wordclk) if (roi_done_w) begin
        hold_w <= {roi_blk_w, roi_npx_w, roi_fcnt_w, roi_mean_w};
        tog_w  <= ~tog_w;
    end

    reg [2:0]  tog_s = 3'b000;
    reg [35:0] hold_c = 36'd0;
    reg        sample = 1'b0;
    always @(posedge clk) begin
        tog_s  <= {tog_s[1:0], tog_w};
        sample <= tog_s[2] ^ tog_s[1];
        if (tog_s[2] ^ tog_s[1]) hold_c <= hold_w;
    end

    //------------------------------------------------------- one line per frame
    wire [7:0] tx_data;
    wire       tx_send, tx_busy;

    roi_line u_line (
        .clk(clk), .go(sample),
        .mean(hold_c[9:0]), .fcnt(hold_c[25:10]),
        .npx(hold_c[34:26]), .blk(hold_c[35]),
        .tx_data(tx_data), .tx_send(tx_send), .tx_busy(tx_busy), .busy()
    );

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(tx_data), .send(tx_send),
        .tx(usb_tx), .busy(tx_busy)
    );

    //-------------------------------------------------------------------- LEDs
    // Enough to tell "dead" from "running but not what you expected", with no
    // host attached: each stage of the chain gets a bit, in order.
    reg [23:0] hb = 24'd0;
    always @(posedge clk) hb <= hb + 24'd1;
    reg [3:0] fr_div = 4'd0;
    always @(posedge clk) if (sample) fr_div <= fr_div + 4'd1;

    always @(posedge clk) begin
        led[0] <= mmcm2_locked;          // 200 MHz MMCM
        led[1] <= idc_rdy;               // IDELAYCTRL ready
        led[2] <= scan_done;             // eye scan finished
        led[3] <= aligned;               // all four lanes aligned
        led[4] <= streaming;             // sensor configured and streaming
        led[5] <= fr_div[3];             // frames arriving (blinks ~ fps/16)
        led[6] <= (hold_c[34:26] == 9'd256);  // npx == 256: the ROI is on the sensor
        led[7] <= hb[23];                // heartbeat
    end

endmodule
