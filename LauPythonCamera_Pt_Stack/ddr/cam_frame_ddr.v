`timescale 1ns/1ps
//=============================================================================
// cam_frame_ddr.v - THE WHOLE FOCAL PLANE. 1280 x 1024 into DDR3, then to the PC.
//
// Joins two separately proven subsystems:
//
//   * the camera path -- proven BYTE-EXACT against the sensor's own built-in
//     test pattern: four values at exactly 25.00 % each, 1024/1024 lines
//     matching with zero errors on a single phase.
//   * DDR3 -- proven BYTE-EXACT against a known pattern: a full 1,310,720-byte
//     frame written, read back and streamed with 0 mismatches (ddr_bist.v).
//
// Neither is a suspect, which is the point of having done them separately. Up
// to now every image has been 320 x 1024 because a full 8-bit frame is
// 1,310,720 bytes against ~607 KB of block RAM. In DDR it is 0.5 % of the part.
//
//-----------------------------------------------------------------------------
// THE CLOCK CROSSING is the new thing here. The camera runs on wordclk, 72 MHz
// off a BUFR (a REGIONAL buffer), and the MIG on ui_clk, 100 MHz from its own
// PLL -- unrelated clocks, and ui_clk does not even run until DDR calibration
// finishes. cam_async_fifo carries kernels across; nothing else does.
//
// GEOMETRY. cam_sync_decode emits one 8-pixel kernel (64 bits) per 2 wordclks.
// Two kernels are one 128-bit DDR word, so:
//
//     1280 px/line = 160 kernels = 80 DDR words per line
//     1024 lines                 = 81,920 DDR words = 1,310,720 bytes
//
// The line is a whole number of DDR words, so no line ever straddles a word and
// the address is simply sequential. app_addr steps by 8 per word (BL8).
//
// ONE SHOT. The frame is captured once and then re-streamed indefinitely, so a
// host that misses a dump just takes the next one and always gets the identical
// image. At 1 Mbaud a full frame takes 13.1 s to leave the board -- the UART is
// the bottleneck by three orders of magnitude, not DDR.
//
// LEDs
//   led[7] heartbeat  led[6] calib  led[5] aligned  led[4] capturing
//   led[3] frame in DDR  led[2] streaming  led[1] FIFO OVERFLOW (dropped data)
//=============================================================================
module cam_frame_ddr #(
    parameter integer BAUD  = 1_000_000,
    parameter integer NCOL  = 1280,
    parameter integer NROW  = 1024,
    parameter [15:0]  EXPOSURE = 16'h0640
)(
    input  wire        clk,          // 100 MHz board clock
    input  wire        rst_n,

    output reg  [7:0]  led,
    output wire        usb_tx,
    input  wire        usb_rx,

    input  wire        cam_clkout_p, cam_clkout_n,
    input  wire [3:0]  cam_d_p,      cam_d_n,
    input  wire        cam_sync_p,   cam_sync_n,
    output wire        cam_sck, cam_mosi, cam_ss_n,
    input  wire        cam_miso,
    output wire        cam_reset_n, cam_clk_pll,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor,

    inout  wire [15:0] ddr3_dq,
    inout  wire [1:0]  ddr3_dqs_p, ddr3_dqs_n,
    output wire [13:0] ddr3_addr,
    output wire [2:0]  ddr3_ba,
    output wire        ddr3_ras_n, ddr3_cas_n, ddr3_we_n, ddr3_reset_n,
    output wire [0:0]  ddr3_ck_p, ddr3_ck_n, ddr3_cke, ddr3_cs_n,
    output wire [1:0]  ddr3_dm,
    output wire [0:0]  ddr3_odt
);
    localparam integer NBYTES = NCOL * NROW;        // 1,310,720
    localparam integer NWORDS = NBYTES / 16;        // 81,920
    localparam integer ASTEP  = 8;

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    //--- one MMCM: 100 MHz for the MIG, 200 MHz for both IDELAYCTRLs ---------
    wire fb, fb_g, c200_raw, clk200, c100_raw, clk100, mmcm_locked;
    MMCME2_BASE #(
        .BANDWIDTH("OPTIMIZED"), .CLKIN1_PERIOD(10.000),
        .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(10.000),
        .CLKOUT0_DIVIDE_F(5.000), .CLKOUT0_DUTY_CYCLE(0.500),
        .CLKOUT1_DIVIDE(10),      .CLKOUT1_DUTY_CYCLE(0.500),
        .STARTUP_WAIT("FALSE")
    ) u_mmcm (
        .CLKIN1(clk), .CLKFBIN(fb_g), .CLKFBOUT(fb),
        .CLKOUT0(c200_raw), .CLKOUT1(c100_raw),
        .CLKOUT2(), .CLKOUT3(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .CLKOUT0B(), .CLKOUT1B(), .CLKOUT2B(), .CLKOUT3B(), .CLKFBOUTB(),
        .LOCKED(mmcm_locked), .PWRDWN(1'b0), .RST(1'b0)
    );
    BUFG u_fb (.I(fb), .O(fb_g));
    BUFG u_c200 (.I(c200_raw), .O(clk200));
    BUFG u_c100 (.I(c100_raw), .O(clk100));

    // Our IDELAYCTRL for the camera's bank-13 lanes. The MIG has its own for
    // bank 15; they coexist because each carries its own IODELAY_GROUP.
    reg [7:0] idc_cnt = 8'd0;
    reg       idc_rst = 1'b1;
    always @(posedge clk200) begin
        if (!mmcm_locked) begin idc_cnt <= 8'd0; idc_rst <= 1'b1; end
        else if (idc_cnt != 8'hFF) begin idc_cnt <= idc_cnt + 8'd1; idc_rst <= 1'b1; end
        else idc_rst <= 1'b0;
    end
    wire idc_rdy;
    (* IODELAY_GROUP = "cam_idelay" *)
    IDELAYCTRL u_idc (.REFCLK(clk200), .RST(idc_rst), .RDY(idc_rdy));

    //--------------------------------------------------- camera front end
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    cam_boot_stage1 #(.CLK_HZ(100_000_000), .BAUD(BAUD), .STOP_AT(45),
                      .TRIGGERED(0), .EXPOSURE(EXPOSURE)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

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
    wire [4:0] bt0,bt1,bt2,bt3,bts;
    wire [5:0] bl0,bl1,bl2,bl3,bls;
    cam_eye_scan u_scan (
        .wordclk(wordclk), .rst(wc_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .tap_val(tap_val), .tap_ld(tap_ld),
        .scan_done(scan_done), .align_rst(align_rst),
        .best_tap0(bt0), .best_tap1(bt1), .best_tap2(bt2), .best_tap3(bt3),
        .best_taps(bts), .best_len0(bl0), .best_len1(bl1), .best_len2(bl2),
        .best_len3(bl3), .best_lens(bls)
    );

    cam_align u_align (
        .wordclk(wordclk), .rst(wc_rst | align_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .bitslip(bitslip), .lane_locked(lane_locked),
        .aligned(aligned), .lane_failed(lane_failed)
    );

    // Stream only once the eye is parked AND DDR is ready to take data: there is
    // no point capturing a frame we cannot store.
    wire init_calib_complete;
    reg [2:0] rdy_s = 3'b000;
    reg       fired = 1'b0;
    always @(posedge clk) begin
        stream_go <= 1'b0;
        rdy_s <= {rdy_s[1:0], (scan_done & aligned & init_calib_complete)};
        if (rst) fired <= 1'b0;
        else if (rdy_s[2] && !fired && !streaming) begin
            stream_go <= 1'b1; fired <= 1'b1;
        end
    end

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
    wire [63:0] kword = { kp7[9:2], kp6[9:2], kp5[9:2], kp4[9:2],
                          kp3[9:2], kp2[9:2], kp1[9:2], kp0[9:2] };

    //--------------------------------- capture window, in the wordclk domain
    reg cap = 1'b0, cap_dn = 1'b0;
    always @(posedge wordclk) begin
        if (wc_rst) begin cap <= 1'b0; cap_dn <= 1'b0; end
        else if (frame_end && cap) begin cap <= 1'b0; cap_dn <= 1'b1; end
        else if (frame_start && !cap && !cap_dn) cap <= 1'b1;
    end

    wire fifo_full, fifo_empty, fifo_ovf;
    wire [63:0] fifo_dout;
    reg  fifo_rd = 1'b0;

    cam_async_fifo #(.DW(64), .AW(8)) u_fifo (
        .wr_clk(wordclk), .wr_rst(wc_rst), .wr_en(cap && kvalid),
        .wr_data(kword), .full(fifo_full), .overflow(fifo_ovf),
        .rd_clk(ui_clk), .rd_rst(ui_rst), .rd_en(fifo_rd),
        .rd_data(fifo_dout), .empty(fifo_empty)
    );

    //---------------------------------------------------------------- MIG
    wire        ui_clk, ui_rst;
    wire [27:0] app_addr;
    wire [2:0]  app_cmd;
    wire        app_en, app_rdy;
    wire [127:0] app_wdf_data;
    wire        app_wdf_end, app_wdf_wren, app_wdf_rdy;
    wire [127:0] app_rd_data;
    wire        app_rd_data_valid, app_rd_data_end;

    reg [27:0]  r_addr = 28'd0;
    reg [2:0]   r_cmd  = 3'd0;
    reg [127:0] r_wdata = 128'd0;
    reg         cmd_done = 1'b0, dat_done = 1'b0;

    localparam [3:0] W_WAIT=0, W_LO=1, W_HI=2, W_ISSUE=3, W_DONE=4,
                     R_HDR=5, R_CMD=6, R_WAIT=7, R_BYTE=8, R_FTR=9, R_LOOP=10;
    reg [3:0] st = W_WAIT;

    // app_en / app_wdf_wren must be HELD until accepted in the SAME cycle --
    // see ddr_bist.v. Registering them a cycle after sampling rdy drops every
    // command issued while rdy is low for a refresh, and almost nothing lands.
    wire issuing = (st == W_ISSUE) || (st == R_CMD);
    assign app_addr     = r_addr;
    assign app_cmd      = r_cmd;
    assign app_en       = issuing && !cmd_done;
    assign app_wdf_data = r_wdata;
    assign app_wdf_wren = (st == W_ISSUE) && !dat_done;
    assign app_wdf_end  = (st == W_ISSUE) && !dat_done;

    mig_ddr3 u_mig (
        .ddr3_addr(ddr3_addr), .ddr3_ba(ddr3_ba),
        .ddr3_cas_n(ddr3_cas_n), .ddr3_ck_n(ddr3_ck_n), .ddr3_ck_p(ddr3_ck_p),
        .ddr3_cke(ddr3_cke), .ddr3_ras_n(ddr3_ras_n), .ddr3_reset_n(ddr3_reset_n),
        .ddr3_we_n(ddr3_we_n), .ddr3_dq(ddr3_dq),
        .ddr3_dqs_n(ddr3_dqs_n), .ddr3_dqs_p(ddr3_dqs_p),
        .init_calib_complete(init_calib_complete),
        .ddr3_cs_n(ddr3_cs_n), .ddr3_dm(ddr3_dm), .ddr3_odt(ddr3_odt),
        .app_addr(app_addr), .app_cmd(app_cmd), .app_en(app_en),
        .app_wdf_data(app_wdf_data), .app_wdf_end(app_wdf_end),
        .app_wdf_wren(app_wdf_wren), .app_wdf_mask(16'h0000),
        .app_rd_data(app_rd_data), .app_rd_data_end(app_rd_data_end),
        .app_rd_data_valid(app_rd_data_valid),
        .app_rdy(app_rdy), .app_wdf_rdy(app_wdf_rdy),
        .app_sr_req(1'b0), .app_ref_req(1'b0), .app_zq_req(1'b0),
        .app_sr_active(), .app_ref_ack(), .app_zq_ack(),
        .ui_clk(ui_clk), .ui_clk_sync_rst(ui_rst),
        .sys_clk_i(clk100), .clk_ref_i(clk200),
        .sys_rst(mig_rst)                   // ACTIVE HIGH, per mig_pt_v2.prj
    );
    reg [1:0] mrstn_s = 2'b00;
    always @(posedge clk100) mrstn_s <= {mrstn_s[0], rst_n};
    wire mig_rst = !mrstn_s[1] || !mmcm_locked;

    //------------------------------------------------- writer / reader / UART
    reg [16:0] widx = 17'd0;
    reg [127:0] rword = 128'd0;
    reg [3:0]  byi = 4'd0;
    reg [7:0]  data_q = 8'h00;
    reg        send = 1'b0;
    wire       tx_busy;
    wire       tx_free = !tx_busy && !send;
    reg [4:0]  hi = 5'd0;

    reg [7:0] hdr_ch, ftr_ch;
    always @(*) begin
        case (hi)
        5'd0:hdr_ch="F"; 5'd1:hdr_ch="R"; 5'd2:hdr_ch="A"; 5'd3:hdr_ch="M";
        5'd4:hdr_ch="E"; 5'd5:hdr_ch="S"; 5'd6:hdr_ch="T"; 5'd7:hdr_ch="A";
        5'd8:hdr_ch="R"; 5'd9:hdr_ch="T"; default:hdr_ch=8'h0A;
        endcase
        case (hi)
        5'd0:ftr_ch=8'h0A; 5'd1:ftr_ch="F"; 5'd2:ftr_ch="R"; 5'd3:ftr_ch="A";
        5'd4:ftr_ch="M"; 5'd5:ftr_ch="E"; 5'd6:ftr_ch="E"; 5'd7:ftr_ch="N";
        5'd8:ftr_ch="D"; default:ftr_ch=8'h0A;
        endcase
    end

    always @(posedge ui_clk) begin
        send    <= 1'b0;
        fifo_rd <= 1'b0;

        if (ui_rst) begin
            st <= W_WAIT; widx <= 17'd0; r_addr <= 28'd0; r_cmd <= 3'd0;
            byi <= 4'd0; hi <= 5'd0; cmd_done <= 1'b0; dat_done <= 1'b0;
        end else begin
            case (st)
            W_WAIT: if (init_calib_complete) begin
                        st <= W_LO; widx <= 17'd0; r_addr <= 28'd0; r_cmd <= 3'd0;
                    end

            // Two 64-bit kernels make one 128-bit DDR word. Kernel 2j is the LOW
            // half so byte order on the wire matches the camera's column order.
            W_LO: if (!fifo_empty) begin
                      r_wdata[63:0] <= fifo_dout; fifo_rd <= 1'b1; st <= W_HI;
                  end
            W_HI: if (!fifo_empty) begin
                      r_wdata[127:64] <= fifo_dout; fifo_rd <= 1'b1;
                      cmd_done <= 1'b0; dat_done <= 1'b0; st <= W_ISSUE;
                  end

            W_ISSUE: begin
                if (app_en && app_rdy)             cmd_done <= 1'b1;
                if (app_wdf_wren && app_wdf_rdy)   dat_done <= 1'b1;
                if ((cmd_done || (app_en && app_rdy)) &&
                    (dat_done || (app_wdf_wren && app_wdf_rdy))) begin
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    if (widx == NWORDS[16:0] - 17'd1) begin
                        st <= W_DONE;
                    end else begin
                        widx   <= widx + 17'd1;
                        r_addr <= r_addr + ASTEP;
                        st     <= W_LO;
                    end
                end
            end

            W_DONE: begin                       // frame is in DDR; stream it
                widx <= 17'd0; r_addr <= 28'd0; r_cmd <= 3'd1;
                hi <= 5'd0; st <= R_HDR;
            end

            R_HDR: if (tx_free) begin
                data_q <= hdr_ch; send <= 1'b1;
                if (hi == 5'd10) begin hi <= 5'd0; st <= R_CMD; end
                else hi <= hi + 5'd1;
            end

            R_CMD: if (app_en && app_rdy) st <= R_WAIT;
            R_WAIT: if (app_rd_data_valid) begin
                rword <= app_rd_data; byi <= 4'd0; st <= R_BYTE;
            end
            R_BYTE: if (tx_free) begin
                data_q <= rword[byi*8 +: 8]; send <= 1'b1;
                if (byi == 4'd15) begin
                    if (widx == NWORDS[16:0] - 17'd1) begin hi <= 5'd0; st <= R_FTR; end
                    else begin
                        widx <= widx + 17'd1; r_addr <= r_addr + ASTEP; st <= R_CMD;
                    end
                end else byi <= byi + 4'd1;
            end

            R_FTR: if (tx_free) begin
                data_q <= ftr_ch; send <= 1'b1;
                if (hi == 5'd9) begin hi <= 5'd0; st <= R_LOOP; end
                else hi <= hi + 5'd1;
            end
            R_LOOP: begin                        // re-offer the same frame
                widx <= 17'd0; r_addr <= 28'd0; hi <= 5'd0; st <= R_HDR;
            end
            default: st <= W_WAIT;
            endcase
        end
    end

    uart_tx #(.CLK_HZ(100_000_000), .BAUD(BAUD)) u_tx (
        .clk(ui_clk), .rst(ui_rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

    reg [1:0] cap_s = 2'b00, ovf_s = 2'b00;
    always @(posedge ui_clk) begin
        cap_s <= {cap_s[0], cap};
        ovf_s <= {ovf_s[0], fifo_ovf};
    end

    reg [26:0] hb = 27'd0;
    always @(posedge ui_clk) begin
        hb <= hb + 27'd1;
        led[7] <= hb[26];
        led[6] <= init_calib_complete;
        led[5] <= aligned;
        led[4] <= cap_s[1];
        led[3] <= (st >= R_HDR);
        led[2] <= (st == R_BYTE);
        led[1] <= ovf_s[1];          // FIFO overflowed: pixels were DROPPED
        led[0] <= streaming;
    end
endmodule
