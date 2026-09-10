`timescale 1ns/1ps
//=============================================================================
// cam_roi_min.v -- the camera side, reduced to one job.
//
// ONE NUMBER PER FRAME, PAIRED WITH THE PIXEL THAT WAS SENT.
//
//   trigger on the projector's vsync, after a programmable delay
//   integrate for a programmable exposure
//   average the 16x16 patch at the centre of the sensor
//   emit that mean beside the top-left pixel of the frame that was ON SCREEN
//   while the exposure was open
//
// That is the whole contract. There is no frame buffer, no DDR3, no MIG, no
// FT601, no sequence grouping, no per-slot result array and no trigger queue.
//
// WHY THIS REPLACES cam_frame_ft FOR PROFILING. cam_frame_ft is 2808 lines and
// carries a full video pipeline: a DDR3 ring, a USB 3 reader, a frame header,
// a per-slot ROI array with a FIFO in front of it, and a 32-entry trigger
// queue. Every one of those exists to stream PICTURES. The projector profile
// does not want pictures -- it wants a scalar and a label -- and each of those
// mechanisms has at some point put the scalar and the label out of step:
//
//   * a trigger-time phase FIFO that rotated permanently on one missed trigger
//   * a per-slot ROI queue that returned the previous frame's mean
//   * a top-left pixel latched at readout rather than at the exposure
//
// None of those failures is possible here, because none of those mechanisms is
// here. What survives is the part that was never in doubt: the boot sequencer,
// the LVDS receiver, the IDELAY eye scan, the aligner and the decoder, all
// instantiated exactly as cam_roi_top and cam_frame_ft instantiate them. That
// chain is proven and this file is not where it gets re-typed.
//
// -----------------------------------------------------------------------------
// THE LABELLING, WHICH IS THE ONLY SUBTLE PART.
//
// The obvious implementation is wrong, and it is wrong in a way that was
// MEASURED: sample the transmitted top-left pixel off out_red, latch it when
// the trigger fires, ship it with the mean. That is what the old design did.
//
// It fails because THE TOP-LEFT PIXEL DOES NOT EXIST YET. It is transmitted
// ~354 us into the frame, when active video starts; before that the wire is in
// the back porch and the sampled register still holds the PREVIOUS frame's
// value. So every trigger with delay < 354 us pairs its mean with the wrong
// frame, and the sweep shows a one-frame rotation at that delay -- observed at
// ~450 us in an 8325.8 us period, with the bright frame stepping from slots
// [2,3] to [3,4] and never stepping back.
//
// THE FIX IS TO PAIR BY INDEX AND RESOLVE THE VALUE LATER.
//
//   vs_idx    counts projected frames, incremented ON the vsync, so it is
//             already correct at delay 0 -- it names the frame being displayed
//             from the first cycle of the frame, back porch included.
//   tlp_ring  holds the transmitted top-left pixel, written at whatever moment
//             it actually appears on the wire, into the slot vs_idx names.
//   trig_idx  the index latched when the trigger fires. An INDEX, not a value.
//
// The value is fetched when the mean publishes. By then the pixel has long
// since been transmitted, so the pairing is exact for every delay:
//
//        vsync_N ---- +354 us ---- ring[N] written
//        vsync_N + delay ---- trigger, latch index N
//        ... exposure + ~1.1 ms sensor latency + readout ...
//        mean publishes, ~5.7 ms after the trigger --> read ring[N]
//        vsync_N + 4T = 33 ms ---- ring[N] finally overwritten
//
// Written at +354 us, read at +5.7 ms, overwritten at +33 ms. Comfortable at
// both ends, and four entries is the smallest depth that makes it so.
//
// THE INDEX RIDES WITH THE FRAME, it is not held in a register until publish.
// trig_idx is sampled into the wordclk domain at the sensor's own frame_start
// and travels in the same payload as the mean. That removes the readout time
// from the timing budget: the constraint becomes exposure + sensor latency < T
// rather than exposure + latency + readout < T, which at L0 ~ 1.1 ms and
// T = 8.33 ms leaves the whole usable exposure range valid instead of clipping
// it at about 4 ms.
//
// TWO INDEPENDENT LABELS, DELIBERATELY. roi_phase_o carries the impulse
// sequence phase latched at the SAME trigger instant; roi_tlp_o carries the
// transmitted pixel resolved through the ring. They are derived from different
// signals by different routes and must agree. When they disagree, something is
// wrong and it is visible in one line of telemetry rather than inferred from
// the shape of a sweep two hours later.
//=============================================================================
module cam_roi_min #(
    parameter integer CLK_HZ        = 100_000_000,
    // 1 = configure the sensor for triggered global shutter. The trigger is
    // generated below from the projector's vsync; the sensor integrates for
    // exposure0 on its own, so the pulse only has to MARK the frame start.
    parameter integer TRIGGERED     = 1,
    parameter integer GL_EN_DEFAULT = 1,
    // Trigger high time in clk cycles. 10 us is far wider than the sensor needs
    // to sample the edge and far shorter than any usable frame period. It is
    // NOT the exposure -- exposure is register 201, set below.
    parameter integer TRIG_CY       = 1000,
    // PLAIN INTEGERS, DELIBERATELY. This module is instantiated from VHDL, and a
    // mixed-language generic map onto a vector parameter is a well-known way to
    // get a silently wrong constant out of Vivado. Widths are applied below.
    parameter integer EXPOSURE      = 13,         // 13 x 375 ns = 4.88 us
    parameter integer ROI_COL8      = 80,         // 80*8 = column 640
    parameter integer ROI_ROW8      = 64,         // 64*8 = row 512
    parameter integer EXT_CLK       = 1
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clk200_ext,
    input  wire        clk100_ext,

    output reg  [7:0]  led,
    output wire        usb_tx,
    input  wire        usb_rx,

    //---- the projector side --------------------------------------------------
    // ext_sync is vsync_Pos: the LEADING edge of the pulse, the same edge
    // impulse_gen advances its phase on. Driving both from one edge is what
    // makes delay 0 mean the frame boundary rather than six lines past it.
    input  wire        ext_sync,
    input  wire [23:0] ext_tlp,        // top-left pixel AS TRANSMITTED, RGB
    input  wire        ext_tlp_tog,    // toggles once per transmitted frame
    input  wire [2:0]  imp_phase_i,    // impulse sequence phase, already in clk

    //---- host registers ------------------------------------------------------
    input  wire [7:0]  roi_col8_i,
    input  wire [7:0]  roi_row8_i,
    input  wire [15:0] expo_uart_i,
    input  wire        expo_uart_we,
    input  wire [23:0] gldly_uart_i,
    input  wire        gldly_uart_we,

    //---- one result per camera frame, clk domain -----------------------------
    output reg  [9:0]  roi_mean_o  = 10'd0,
    output reg  [8:0]  roi_npx_o   = 9'd0,
    output reg  [15:0] roi_fcnt_o  = 16'd0,
    output reg         roi_blk_o   = 1'b0,
    output reg         roi_valid_o = 1'b0,
    output reg  [2:0]  roi_phase_o = 3'd0,
    output reg  [23:0] roi_tlp_o   = 24'd0,
    // THE TRIGGER ORDINAL. Counts what the FPGA ASKED FOR, where roi_fcnt_o
    // counts what the sensor DELIVERED. Consecutive lines must differ by
    // exactly one; the two counters disagreeing says which end lost the frame.
    output reg  [7:0]  roi_tcnt_o  = 8'd0,

    output wire [223:0] cam_stat_o,
    output reg          cam_stat_tog_o = 1'b0,

    //---- sensor pins ---------------------------------------------------------
    input  wire        cam_clkout_p, cam_clkout_n,
    input  wire [3:0]  cam_d_p,      cam_d_n,
    input  wire        cam_sync_p,   cam_sync_n,
    output wire        cam_sck,
    output wire        cam_mosi,
    output wire        cam_ss_n,
    input  wire        cam_miso,
    output wire        cam_reset_n,
    output wire        cam_clk_pll,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor
);
    // The integer parameters, given their real widths exactly once.
    localparam [15:0] EXPO_INIT = EXPOSURE;
    localparam [7:0]  COL8_INIT = ROI_COL8;
    localparam [7:0]  ROW8_INIT = ROI_ROW8;

    //-------------------------------------------------------------- reset
    reg [1:0] rstn_sync = 2'b00;
    always @(posedge clk) rstn_sync <= {rstn_sync[0], rst_n};
    reg [7:0] por = 8'd0;
    always @(posedge clk) if (!por[7]) por <= por + 8'd1;
    wire rst = !por[7] || !rstn_sync[1];

    //---------------------------------------------------------- clocking
    // Identical to cam_frame_ft's: the merged design already makes a 200 MHz
    // IDELAY reference and a buffered 100 MHz, so a second MMCM here would be
    // pure duplication on a part that is already tight for them.
    wire clk200, clk100, mmcm_locked;
    generate
    if (EXT_CLK != 0) begin : g_extclk
        assign clk200 = clk200_ext;
        assign clk100 = clk100_ext;
        // No MMCM means no LOCKED, and LOCKED gates the IDELAYCTRL reset. A
        // settle counter ON THE SUPPLIED CLOCK keeps the property that matters:
        // it cannot advance unless that clock is really running, and it is
        // synchronous, so the release cannot glitch.
        reg [11:0] ext_cnt = 12'd0;
        always @(posedge clk100 or negedge rst_n) begin
            if (!rst_n)            ext_cnt <= 12'd0;
            else if (!ext_cnt[11]) ext_cnt <= ext_cnt + 12'd1;
        end
        assign mmcm_locked = ext_cnt[11];
    end else begin : g_ownclk
        wire fb, fb_g, c200_raw, c100_raw;
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
        BUFG u_fb   (.I(fb),       .O(fb_g));
        BUFG u_c200 (.I(c200_raw), .O(clk200));
        BUFG u_c100 (.I(c100_raw), .O(clk100));
    end
    endgenerate

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

    //------------------------------------------------- exposure, runtime
    // Exposure was a build-time ROM constant, which cost one 13-minute
    // bitstream per point of a brightness-vs-exposure curve. cam_boot_stage1
    // rewrites register 201 on a pulse, so it costs one capture instead.
    reg [15:0] expo_cur = EXPO_INIT;
    reg        expo_req = 1'b0;
    always @(posedge clk) begin
        expo_req <= 1'b0;
        if (expo_uart_we) begin
            expo_cur <= expo_uart_i;
            expo_req <= 1'b1;       // both land together: expo_cur is the new
        end                         // value on the cycle expo_req is high
    end

    //--------------------------------------- sensor boot / SPI / reset
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;

    cam_boot_stage1 #(
        .CLK_HZ(CLK_HZ), .BAUD(115_200), .STOP_AT(45),
        .TRIGGERED(TRIGGERED), .TESTPAT(0), .EXPOSURE(EXPO_INIT)
    ) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .expo_req(expo_req), .expo_val(expo_cur),
        .led(boot_led), .usb_tx(usb_tx), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(),
        .cam_monitor(cam_monitor)
    );

    //------------------------------------------------- receive + align
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

    // IDELAYE2 EYE CENTRING IS NOT OPTIONAL at 720 Mbps. Without it one
    // isolated bit drops and it looks exactly like a bad solder joint.
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

    // Start the sensor streaming once the taps are parked and all lanes aligned.
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

    //---------------------------------------------------- sync decode
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

    //---------------------------------------------------- the ROI mean
    // Quasi-static placement, 2FF into wordclk. An ROI that moves mid-frame
    // only corrupts that frame, and npx says so when it does.
    reg [7:0] col8_s1 = COL8_INIT, col8_s2 = COL8_INIT;
    reg [7:0] row8_s1 = ROW8_INIT, row8_s2 = ROW8_INIT;
    always @(posedge wordclk) begin
        col8_s1 <= roi_col8_i; col8_s2 <= col8_s1;
        row8_s1 <= roi_row8_i; row8_s2 <= row8_s1;
    end

    wire [9:0]  roi_mean_w;
    wire [8:0]  roi_npx_w;
    wire [15:0] roi_fcnt_w;
    wire        roi_blk_w, roi_done_w;

    roi_mean u_roi (
        .wordclk(wordclk), .rst(wc_rst),
        .roi_col8(col8_s2), .roi_row8(row8_s2),
        .kvalid(kvalid), .kbase(kbase),
        .kp0(kp0), .kp1(kp1), .kp2(kp2), .kp3(kp3),
        .kp4(kp4), .kp5(kp5), .kp6(kp6), .kp7(kp7),
        .line_start(line_start), .frame_start(frame_start),
        .frame_end(frame_end), .in_black(in_black),
        .mean(roi_mean_w), .npx(roi_npx_w), .fcnt(roi_fcnt_w),
        .blk(roi_blk_w), .done(roi_done_w)
    );

    //=====================================================================
    // THE PROJECTOR'S VSYNC, AND WHAT IS COUNTED FROM IT
    //=====================================================================
    localparam [23:0] XS_TIMEOUT = 24'd10_000_000;   // 100 ms without an edge

    reg [2:0]  xs_s   = 3'b000;
    reg [23:0] xs_age = XS_TIMEOUT;
    reg [15:0] xs_cnt = 16'd0;
    reg        gl_en  = (GL_EN_DEFAULT != 0);
    wire       xs_rise = xs_s[1] & ~xs_s[2];
    wire       gl_live = gl_en && (xs_age != XS_TIMEOUT);

    always @(posedge clk) begin
        xs_s <= {xs_s[1:0], ext_sync};
        if (xs_rise) begin
            xs_age <= 24'd0;
            xs_cnt <= xs_cnt + 16'd1;
        end else if (xs_age != XS_TIMEOUT) begin
            xs_age <= xs_age + 24'd1;
        end
    end

    // MEASURED vsync period, 10 ns ticks. Not a commanded trigger period --
    // this build has no free-running mode to command -- so it is reported as
    // what it is: the interval between the edges actually being followed.
    reg [23:0] vs_run = 24'd0;
    reg [23:0] vs_per = 24'd0;
    always @(posedge clk) begin
        if (xs_rise) begin
            vs_per <= vs_run;
            vs_run <= 24'd0;
        end else if (vs_run != 24'hFFFFFF) begin
            vs_run <= vs_run + 24'd1;
        end
    end

    //=====================================================================
    // THE FRAME INDEX AND THE RING OF TRANSMITTED TOP-LEFT PIXELS
    //
    // vs_idx names the frame BEING DISPLAYED and is incremented on the vsync,
    // so it is already correct during the back porch -- which is exactly where
    // sampling the pixel value itself is not. The pixel arrives ~354 us later
    // and is written into the slot vs_idx names; the trigger records only the
    // index. See the header for the timing budget.
    //=====================================================================
    // FOUR DISCRETE REGISTERS, NOT AN ARRAY. Thirty-two flops either way, but an
    // array here would be inferred as distributed RAM, and the asynchronous CDC
    // constraint on this crossing has to NAME its destination cells. A RAM's
    // cell names are a synthesis detail; these are not. The same design carries
    // a note that a ram_style attribute was silently ignored elsewhere, so the
    // structure is chosen rather than requested.
    reg [1:0] vs_idx = 2'd0;
    reg [23:0] tlp_r0 = 24'd0, tlp_r1 = 24'd0, tlp_r2 = 24'd0, tlp_r3 = 24'd0;
    reg [2:0] tlp_cs = 3'b000;

    always @(posedge clk) begin
        tlp_cs <= {tlp_cs[1:0], ext_tlp_tog};
        if (xs_rise) vs_idx <= vs_idx + 2'd1;
        // Written whenever the pixel actually appears on the wire. vs_idx has
        // already advanced to this frame, so the slot is this frame's.
        if (tlp_cs[2] ^ tlp_cs[1]) begin
            case (vs_idx)
            2'd0:    tlp_r0 <= ext_tlp;
            2'd1:    tlp_r1 <= ext_tlp;
            2'd2:    tlp_r2 <= ext_tlp;
            default: tlp_r3 <= ext_tlp;
            endcase
        end
    end

    //=====================================================================
    // THE TRIGGER: ONE PER PROJECTED VSYNC, gl_dly AFTER IT
    //
    // The 32-entry queue this replaces existed to hold MULTIPLE triggers in
    // flight, which is only possible when the delay can exceed the frame
    // period. Here the delay is a position within one frame, so at most one
    // trigger is ever outstanding and the queue collapses to a counter.
    //
    // Fired on >= rather than ==. An equality test on a counter whose target
    // can be rewritten mid-count is skippable, and a skipped trigger here
    // costs a whole frame; `armed` still guarantees exactly one per vsync.
    //=====================================================================
    reg [23:0] gl_dly   = 24'd0;
    reg [23:0] vs_cnt   = 24'hFFFFFF;
    reg        armed    = 1'b0;
    reg        gl_fire  = 1'b0;
    reg [1:0]  trig_idx = 2'd0;
    reg [2:0]  trig_ph  = 3'd0;
    // Free-running ordinal of the trigger. Eight bits wraps every 256 frames --
    // 2.1 s at 120 Hz -- which is far longer than any gap worth diagnosing, and
    // a wrap is unambiguous because the host only ever compares CONSECUTIVE
    // lines. trig_n holds the ordinal OF THIS trigger, so it is captured before
    // the increment rather than after it.
    reg [7:0]  trig_seq = 8'd0;
    reg [7:0]  trig_n   = 8'd0;
    reg [15:0] gl_miss  = 16'd0;      // vsyncs that armed and never fired

    always @(posedge clk) begin
        if (gldly_uart_we) gl_dly <= gldly_uart_i;

        gl_fire <= 1'b0;
        if (xs_rise) begin
            // A vsync that arrives while still armed means the previous frame's
            // delay was never reached -- gl_dly is longer than the period.
            // Counted, not silently absorbed.
            if (armed && gl_miss != 16'hFFFF) gl_miss <= gl_miss + 16'd1;
            vs_cnt <= 24'd0;
            armed  <= gl_live;
        end else begin
            if (vs_cnt != 24'hFFFFFF) vs_cnt <= vs_cnt + 24'd1;
            if (armed && (vs_cnt >= gl_dly)) begin
                gl_fire  <= 1'b1;
                armed    <= 1'b0;
                // Both labels captured at the SAME instant, from different
                // sources. The index resolves to the transmitted pixel later;
                // the phase is already valid because impulse_gen advances it
                // on the vsync, not at active video.
                trig_idx <= vs_idx;
                trig_ph  <= imp_phase_i;
                trig_n   <= trig_seq;          // this trigger's ordinal...
                trig_seq <= trig_seq + 8'd1;   // ...then advance for the next
            end
        end
    end

    // The pulse only MARKS the frame start; the sensor integrates for
    // exposure0 by itself. Making exposure the pulse width was the old bug
    // that asked for 13.26 ms of work inside an 8.33 ms period.
    // Width the constant explicitly: an integer parameter is SIGNED, and a
    // signed/unsigned comparison against a 24-bit counter is a silent trap.
    localparam [23:0] TRIGW = TRIG_CY;
    reg [23:0] tcnt  = 24'hFFFFFF;
    reg        trig0 = 1'b0;
    always @(posedge clk) begin
        if (gl_fire)                   tcnt <= 24'd0;
        else if (tcnt != 24'hFFFFFF)   tcnt <= tcnt + 24'd1;
        trig0 <= (tcnt < TRIGW);
    end
    assign cam_trigger = {2'b00, (TRIGGERED != 0) ? trig0 : 1'b0};

    //=====================================================================
    // THE LABELS RIDE WITH THE FRAME
    //
    // trig_idx and trig_ph are stable from the trigger until the next one, so
    // they have been steady for at least exposure + sensor latency (>= 1.1 ms)
    // by the time frame_start arrives: a 2FF sync is ample, and sampling them
    // AT frame_start binds them to this frame's pixels rather than leaving
    // them in a register that the next trigger could overwrite first.
    //=====================================================================
    reg [1:0] tix_s1 = 2'd0, tix_s2 = 2'd0;
    reg [2:0] tph_s1 = 3'd0, tph_s2 = 3'd0;
    reg [7:0] tcn_s1 = 8'd0, tcn_s2 = 8'd0;
    reg [1:0] roi_ix_w = 2'd0;
    reg [2:0] roi_ph_w = 3'd0;
    reg [7:0] roi_tcn_w = 8'd0;
    always @(posedge wordclk) begin
        tix_s1 <= trig_idx; tix_s2 <= tix_s1;
        tph_s1 <= trig_ph;  tph_s2 <= tph_s1;
        tcn_s1 <= trig_n;   tcn_s2 <= tcn_s1;
        if (frame_start) begin
            roi_ix_w  <= tix_s2;
            roi_ph_w  <= tph_s2;
            roi_tcn_w <= tcn_s2;
        end
    end

    // wordclk -> clk. Toggle handshake on a payload held for a whole frame
    // period, five orders of magnitude longer than the two sync flops.
    //   [9:0] mean  [25:10] fcnt  [34:26] npx  [35] blk  [38:36] phase
    //   [40:39] tlp ring index  [48:41] trigger ordinal
    reg [48:0] roi_hold_w = 49'd0;
    reg        roi_tog_w  = 1'b0;
    always @(posedge wordclk) if (roi_done_w) begin
        roi_hold_w <= {roi_tcn_w, roi_ix_w, roi_ph_w, roi_blk_w,
                       roi_npx_w, roi_fcnt_w, roi_mean_w};
        roi_tog_w  <= ~roi_tog_w;
    end

    wire [23:0] tlp_sel = (roi_hold_w[40:39] == 2'd0) ? tlp_r0 :
                        (roi_hold_w[40:39] == 2'd1) ? tlp_r1 :
                        (roi_hold_w[40:39] == 2'd2) ? tlp_r2 : tlp_r3;

    reg [2:0] tog_s = 3'b000;
    always @(posedge clk) begin
        tog_s       <= {tog_s[1:0], roi_tog_w};
        roi_valid_o <= tog_s[2] ^ tog_s[1];
        if (tog_s[2] ^ tog_s[1]) begin
            roi_mean_o  <= roi_hold_w[9:0];
            roi_fcnt_o  <= roi_hold_w[25:10];
            roi_npx_o   <= roi_hold_w[34:26];
            roi_blk_o   <= roi_hold_w[35];
            roi_phase_o <= roi_hold_w[38:36];
            // THE PAIRING, RESOLVED HERE AND NOWHERE ELSE. The index travelled
            // with the frame; the pixel it names was transmitted ~5 ms ago and
            // will not be overwritten for another 27.
            roi_tlp_o   <= tlp_sel;
            roi_tcnt_o  <= roi_hold_w[48:41];
        end
    end

    //=====================================================================
    // STATUS -- the same bit layout cam_frame_ft published, so usb_link and
    // every host tool that reads 0x58..0x5F and 0x8E..0x90 keep working.
    //
    // THE DDR3 AND FT601 FIELDS READ ZERO BECAUSE THOSE SUBSYSTEMS ARE NOT
    // IN THIS BUILD. They are not "not yet measured" -- there is nothing to
    // measure. Reporting a plausible value there would be the one thing this
    // file exists to stop doing.
    //=====================================================================
    reg [223:0] cstat_r = 224'd0;
    assign cam_stat_o = cstat_r;

    reg [23:0] stat_div = 24'd0;
    always @(posedge clk) begin
        if (stat_div == 24'd9_999_999) begin        // ~10 Hz at 100 MHz
            stat_div <= 24'd0;
            cstat_r  <= { 8'd0,                          // spare
                          vs_per,                        // 0x8E..0x90
                          8'd0,                          // 0x5F  (no queue)
                          {7'd0, armed},                 // 0x5E  outstanding
                          {6'd0, gl_live, gl_en},        // 0x5D
                          gl_dly,                        // 0x5A..0x5C
                          xs_cnt,                        // 0x58..0x59
                          16'd0,                         // 0x48..0x49
                          24'd0,                         // 0x45..0x47
                          24'd0,                         // 0x42..0x44
                          expo_cur,
                          16'd0,                         // no DDR write timing
                          gl_miss,                       // was ldrop
                          8'd0,
                          3'd0, 1'b0, 1'b0, aligned, streaming, 1'b0 };
            cam_stat_tog_o <= ~cam_stat_tog_o;
        end else begin
            stat_div <= stat_div + 24'd1;
        end
    end

    //-------------------------------------------------------------- LEDs
    // Enough to tell "dead" from "running but not what you expected" with no
    // host attached: each stage of the chain gets a bit, in order.
    reg [23:0] hb = 24'd0;
    always @(posedge clk) hb <= hb + 24'd1;
    reg [3:0] fr_div = 4'd0;
    always @(posedge clk) if (roi_valid_o) fr_div <= fr_div + 4'd1;

    always @(posedge clk) begin
        led[0] <= mmcm_locked;
        led[1] <= idc_rdy;
        led[2] <= scan_done;
        led[3] <= aligned;
        led[4] <= streaming;
        led[5] <= fr_div[3];                     // frames arriving (~fps/16)
        led[6] <= (roi_npx_o == 9'd256);         // the ROI is on the sensor
        led[7] <= hb[23];
    end

    // Deliberately unused: the eye-scan reports are useful on the bench build
    // but nothing here consumes them.
    wire _unused = |{boot_led, lane_locked, lane_failed, line_start,
                     bt0, bt1, bt2, bt3, bts, bl0, bl1, bl2, bl3, bls,
                     clk100, 1'b0};

endmodule
