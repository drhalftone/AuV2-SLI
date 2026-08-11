`timescale 1ns/1ps
//=============================================================================
// cam_line_stage5 - STAGE 5: capture one real image line and dump it.
//
// THE GATE: 1280 bytes come out that respond to light. This is
// CAMERA_RTL_PLAN.md milestone #12 -- real photons through the whole chain:
//
//   sensor -> LVDS -> IDELAY (eye-centred) -> ISERDES -> cam_align (bitslip)
//          -> cam_sync_decode (framing + de-interleave) -> cam_line_buf -> UART
//
// FIRST BITSTREAM THAT MAKES THE SENSOR STREAM -- but ONLY after alignment.
// The boot stops at 41 (LVDS on, sequencer off, training pattern continuous),
// the eye scan and cam_align run against that idle pattern, and only then is
// reg 192 = 0x0801 written to start the sequencer. ORDER MATTERS: see the
// stream_go block below for what happens if you enable streaming first.
//
// cam_line_buf grabs the FIRST IMAGE LINE of every frame (black-reference lines
// emit no kvalid, so they are skipped for free) and re-arms each frame_start, so
// any dump reflects the most recent frame.
//
// 8-bit truncation: the buffer stores pix[9:2], the top 8 of 10. Enough to see a
// scene; the full 10-bit path is a streaming concern, not a bring-up one.
//
//-----------------------------------------------------------------------------
// THE TRAP TO WATCH FOR: THE DE-INTERLEAVE
//
// Pixels do NOT arrive as a simple mod-4 split across the four lanes. They come
// in 8-pixel kernels, two IMG words each, with EVEN kernels ascending and ODD
// kernels descending -- the sensor's ADC column-sequencer ordering
// (CAMERA_SENSOR_PROTOCOL.md 8.3, and osrf/ovc's UNSWAP_KERNELS). A naive mod-4
// de-interleave produces a scrambled image THAT STILL LOOKS LIKE AN IMAGE.
//
// The tell is a PERIOD-4 COMB: neighbouring pixels that should be nearly equal
// alternating in groups of four. Look for it specifically in the dump rather
// than trusting that the line "looks plausible". cam_sync_decode implements the
// kernel un-swap, and the exact within-kernel channel pairing is the one thing
// that could only ever be settled against silicon -- which is what this stage
// settles.
//
//-----------------------------------------------------------------------------
// OUTPUT FORMAT, chosen to be machine-parsable rather than pretty:
//
//   LINE
//   <40 rows of 64 hex chars = 1280 bytes>
//   END
//
// ~2640 characters, about 230 ms at 115200, repeated every ~2 s.
//
// LEDs
//   led[7] heartbeat   led[6] aligned    led[5] eye scan done
//   led[4] frame seen  led[3] line captured  led[2] streaming
//=============================================================================
module cam_line_stage5 #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter integer REP_CY = CLK_HZ * 2,       // dump every ~2 s
    parameter integer NPIX   = 1280
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

    //------------------- sensor side: boot to 41, stream only AFTER alignment
    //
    // NOT STOP_AT = 0. The eye scan needs 256 CONSECUTIVE training words, and
    // once the sequencer is enabled the training pattern survives only in
    // inter-line gaps -- so a scan against a streaming sensor finds nothing and
    // parks the taps badly. First bench run of this stage did exactly that: it
    // captured one partial line (the last two kernels never arrived) and then
    // stopped producing frame_start entirely.
    //
    // So: boot to 41 (idle, training pattern continuous) -> scan -> align ->
    // THEN pulse stream_go, which performs the one deferred write of reg 192.
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(41)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

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

    // The scan runs against the IDLE training pattern, before streaming starts.
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

    //-------------------------------------------- sync decode + line capture
    wire [9:0]  kp0,kp1,kp2,kp3,kp4,kp5,kp6,kp7;
    wire [10:0] kbase;
    wire        kvalid, line_start, frame_start, in_black;

    cam_sync_decode u_dec (
        .wordclk(wordclk), .rst(wc_rst), .aligned(aligned),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .kpix0(kp0), .kpix1(kp1), .kpix2(kp2), .kpix3(kp3),
        .kpix4(kp4), .kpix5(kp5), .kpix6(kp6), .kpix7(kp7),
        .kbase(kbase), .kvalid(kvalid),
        .line_start(line_start), .frame_start(frame_start), .in_black(in_black)
    );

    wire [7:0] rd_data;
    reg [10:0] rd_addr;

    cam_line_buf #(.DEPTH(NPIX), .ADDR_W(11)) u_buf (
        .wordclk(wordclk),
        .frame_start(frame_start), .line_start(line_start), .kvalid(kvalid),
        .kbase(kbase),
        .kpix0(kp0), .kpix1(kp1), .kpix2(kp2), .kpix3(kp3),
        .kpix4(kp4), .kpix5(kp5), .kpix6(kp6), .kpix7(kp7),
        .rd_clk(clk), .rd_addr(rd_addr), .rd_data(rd_data)
    );

    // Start streaming exactly once, after the scan has parked the taps AND all
    // five lanes have aligned on the idle training pattern.
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

    // Frame and line COUNTERS, not just sticky flags. If the dump repeats
    // byte-identically these say whether frames are still arriving at all --
    // a frozen F= means the decode stopped, a rising F= with a frozen image
    // means the capture is not re-arming. Different bugs, same symptom.
    reg [15:0] fr_cnt = 16'd0, ln_cnt = 16'd0;
    always @(posedge wordclk) begin
        if (wc_rst) begin fr_cnt <= 16'd0; ln_cnt <= 16'd0; end
        else begin
            if (frame_start) fr_cnt <= fr_cnt + 16'd1;
            if (line_start)  ln_cnt <= ln_cnt + 16'd1;
        end
    end
    reg [15:0] fr_s, ln_s;
    always @(posedge clk) begin fr_s <= fr_cnt; ln_s <= ln_cnt; end

    // sticky "we have seen a frame / a line" for the LEDs
    reg fs_seen = 1'b0, ls_seen = 1'b0;
    always @(posedge wordclk) begin
        if (wc_rst) begin fs_seen <= 1'b0; ls_seen <= 1'b0; end
        else begin
            if (frame_start) fs_seen <= 1'b1;
            if (line_start)  ls_seen <= 1'b1;
        end
    end
    reg [1:0] fs_s, ls_s, al_s, sd_s;
    always @(posedge clk) begin
        fs_s <= {fs_s[0], fs_seen};  ls_s <= {ls_s[0], ls_seen};
        al_s <= {al_s[0], aligned};  sd_s <= {sd_s[0], scan_done};
    end

    //------------------------------------------------------------- dump FSM
    reg [7:0]  data_q = 8'h00;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    localparam [3:0] D_WAIT=0, D_HDR=1, D_SETA=2, D_LAT=3, D_HI=4, D_LO=5,
                     D_CR=6, D_LF=7, D_NEXT=8, D_END=9;
    reg [3:0]  ds = D_WAIT;
    reg [4:0]  hi = 5'd0;         // header/footer char index
    reg [5:0]  col = 6'd0;        // bytes on this output row
    reg [27:0] rtm = 28'd0;

    // "LINE F=XXXX L=XXXX\r\n" (20 chars) and "END\r\n" (5)
    reg [7:0] hdr_ch, end_ch;
    always @(*) begin
        case (hi)
        5'd0 : hdr_ch = "L"; 5'd1 : hdr_ch = "I"; 5'd2 : hdr_ch = "N";
        5'd3 : hdr_ch = "E"; 5'd4 : hdr_ch = " ";
        5'd5 : hdr_ch = "F"; 5'd6 : hdr_ch = "=";
        5'd7 : hdr_ch = hexd(fr_s[15:12]); 5'd8 : hdr_ch = hexd(fr_s[11:8]);
        5'd9 : hdr_ch = hexd(fr_s[7:4]);   5'd10: hdr_ch = hexd(fr_s[3:0]);
        5'd11: hdr_ch = " ";
        5'd12: hdr_ch = "L"; 5'd13: hdr_ch = "=";
        5'd14: hdr_ch = hexd(ln_s[15:12]); 5'd15: hdr_ch = hexd(ln_s[11:8]);
        5'd16: hdr_ch = hexd(ln_s[7:4]);   5'd17: hdr_ch = hexd(ln_s[3:0]);
        5'd18: hdr_ch = 8'h0D; default: hdr_ch = 8'h0A;
        endcase
        case (hi)
        5'd0: end_ch = "E"; 5'd1: end_ch = "N"; 5'd2: end_ch = "D";
        5'd3: end_ch = 8'h0D; default: end_ch = 8'h0A;
        endcase
    end

    wire tx_free = !tx_busy && !send;

    always @(posedge clk) begin
        send <= 1'b0;

        if (rst) begin
            ds <= D_WAIT; hi <= 5'd0; col <= 6'd0; rd_addr <= 11'd0; rtm <= 28'd0;
        end else begin
            case (ds)

            D_WAIT: begin
                if (rtm == REP_CY[27:0] - 28'd1) begin
                    rtm <= 28'd0;
                    hi  <= 5'd0;
                    ds  <= D_HDR;
                end else rtm <= rtm + 28'd1;
            end

            D_HDR: if (tx_free) begin
                data_q <= hdr_ch; send <= 1'b1;
                if (hi == 5'd19) begin
                    hi <= 5'd0; rd_addr <= 11'd0; col <= 6'd0; ds <= D_SETA;
                end else hi <= hi + 5'd1;
            end

            D_SETA: ds <= D_LAT;              // rd_addr is already set
            D_LAT:  ds <= D_HI;               // rd_data valid one clock later

            D_HI: if (tx_free) begin data_q <= hexd(rd_data[7:4]); send <= 1'b1; ds <= D_LO; end

            D_LO: if (tx_free) begin
                data_q <= hexd(rd_data[3:0]); send <= 1'b1;
                if (col == 6'd31) begin col <= 6'd0; ds <= D_CR; end
                else begin col <= col + 6'd1; ds <= D_NEXT; end
            end

            D_CR: if (tx_free) begin data_q <= 8'h0D; send <= 1'b1; ds <= D_LF; end
            D_LF: if (tx_free) begin data_q <= 8'h0A; send <= 1'b1; ds <= D_NEXT; end

            D_NEXT: begin
                if (rd_addr == NPIX[10:0] - 11'd1) begin hi <= 5'd0; ds <= D_END; end
                else begin rd_addr <= rd_addr + 11'd1; ds <= D_SETA; end
            end

            D_END: if (tx_free) begin
                data_q <= end_ch; send <= 1'b1;
                if (hi == 5'd4) begin hi <= 5'd0; rtm <= 28'd0; ds <= D_WAIT; end
                else hi <= hi + 5'd1;
            end

            default: ds <= D_WAIT;
            endcase
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;
    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= al_s[1];
        led[5]   <= sd_s[1];
        led[4]   <= fs_s[1];
        led[3]   <= ls_s[1];
        led[2]   <= streaming;
        led[1:0] <= 2'd0;
    end

endmodule
