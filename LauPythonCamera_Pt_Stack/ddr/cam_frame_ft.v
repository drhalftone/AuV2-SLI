`timescale 1ns/1ps
//=============================================================================
// cam_frame_ft.v - camera -> DDR3 -> FT601 USB 3.0. Milestone #17.
//
// Same capture path as cam_frame_ddr.v; the UART is replaced by the Ft+.
//
//     UART   1 Mbaud  ~100 kB/s   13.1 s per frame
//     FT601  measured  348 MB/s   ~3.8 ms per frame
//
// THE Ft+ IS ON THE BOTTOM of the Pt, camera on top. That is not cosmetic: the
// Ft+ TOP pinout collides with ELEVEN camera pins -- cam_sck, cam_mosi,
// cam_miso, cam_ss_n, cam_reset_n, cam_clk_pll, cam_trigger[0..2] and
// cam_monitor[0..1] all land on FT601 signals. Bottom-mounted, the two pin sets
// are disjoint. Build this ONLY with the bottom constraints.
//
// THREE CLOCK DOMAINS, all unrelated:
//   wordclk  72 MHz, BUFR   -- the sensor
//   ui_clk  100 MHz, MIG PLL -- DDR3
//   ft_clk  100 MHz, sourced BY THE FT601 itself
// ft_clk is not ours and does not exist until the FT601 is up, so the DDR->USB
// hand-off needs its own async FIFO exactly as the sensor->DDR one does.
//
// WHY THE BUS IS IOB-REGISTERED AND CONSTRAINED. An earlier FT601 test here
// measured 192 fps with zero dropped frames while silently corrupting
// ft_data[31:16] -- the far-bank half missed the FT601's 1 ns setup window while
// the near half made it. An unconstrained path is not a failing path: the tool
// reported WNS >= 0 because it never timed the bus at all. ft601_sync_tx.v
// launches from IOB flops and the bottom XDC now carries the same
// set_output_delay/set_input_delay block the top one gained. VERIFY BYTES, never
// throughput -- throughput looked perfect while half the bus was wrong.
//
// FRAMING matches sli_frame_gen so the existing host tools lock on unchanged:
//   [0] 0x30494C53 "SLI0"   [1] frame index   [2] {height,width}
//   [3] 0                   [4] bytes/frame   [5] pixel words/frame
//   [6] format = 1          [7] ~MAGIC
//=============================================================================
module cam_frame_ft #(
    parameter integer NCOL = 1280,
    parameter integer NROW = 1024,
    parameter [15:0]  EXPOSURE = 16'h0640,
    parameter integer NFRAMES  = 24,          // burst length; <=31 (fcnt is 5 bits)
    // TRIGGERED GLOBAL SHUTTER. 0 = free-running (the configuration that has
    // always produced sane pixels), 1 = 192[4] set and the array held in reset
    // until a rising edge on trigger0.
    parameter integer TRIGGERED = 0,
    // Trigger period in MICROSECONDS, and the whole point of the experiment.
    //
    // Every previous attempt at triggered mode fired trigger0 ONCE, seconds after
    // configuration, and every frame came back 100% saturated at every exposure
    // value tried. The array is documented as sitting in reset until trigger0,
    // but the evidence says it integrates for that entire idle. Triggering
    // CONTINUOUSLY bounds integration to one trigger period no matter what the
    // array does while waiting, so if the idle is the cause, this looks like
    // free-running -- and if it still saturates, the cause is elsewhere.
    //
    // Default 7500 us = 133 Hz, comfortably longer than the measured 6.860 ms
    // readout so a trigger never lands inside the previous frame (the sensor
    // ignores those, p14/p25).
    parameter integer TRIG_US   = 7500,
    // Trigger period in CLOCK CYCLES, overriding TRIG_US when non-zero. 120 Hz
    // is 8.33333 ms = 833,333 cycles of the 100 MHz clock, which is not a whole
    // number of microseconds -- so an exact rate cannot be expressed in TRIG_US.
    // 833,333 gives 120.00005 Hz, i.e. 0.4 ppm from the counter. Absolute
    // accuracy is then set by the board crystal (tens of ppm), not by us.
    parameter integer TRIG_CY   = 0,
    // 0 = reader waits for capture to COMPLETE (old sequential behaviour, new
    //     two-FSM structure) -- isolates the FSM split from the arbitration
    // 1 = reader released as soon as a frame is finished (the real goal)
    parameter integer CONCURRENT = 1,
    // LIVE MODE: stream continuously, camera -> FIFO -> USB, BYPASSING DDR.
    //
    // Scan mode captures N frames into DDR and replays them; live mode never
    // stores. The camera produces 314 MB/s and the link carries ~325, so it
    // fits, and it sidesteps the ring-buffer hazard where a continuous writer
    // laps the reader. The cost is no elasticity: if the host stalls, pixels are
    // gone, because the sensor will not wait.
    //
    // Build-time rather than a runtime command on purpose -- switching modes
    // mid-stream would need the change to land on a frame boundary in three
    // clock domains at once, and that is a separate problem from proving live
    // streaming works at all.
    // LIVE = 1: continuous video through a DDR3 RING BUFFER.
    //
    // The first attempt streamed camera -> FIFO -> USB with DDR bypassed and
    // could never work: the sensor reads out in a 4.55 ms burst at 576 MB/s
    // against a 325 MB/s link, and the FIFO holds 0.6% of a frame. DDR is not a
    // convenience here, it is the elasticity that absorbs that burst.
    //
    // So: the writer runs forever, cycling NSLOT frame slots, and the reader
    // always jumps to the NEWEST completed frame rather than working through a
    // queue -- for a live view, the freshest picture is the point and old frames
    // are worthless. The writer SKIPS a frame rather than overwrite the slot the
    // reader is using, so a slow reader costs dropped frames, never a torn one.
    parameter integer LIVE = 0,
    parameter integer NSLOT = 4,          // ring depth; 4 x 2.62 MB = 10.5 MB
    // 1 = step exposure0 across the burst to measure brightness vs exposure.
    parameter integer EXPO_SWEEP = 0,
    // Frames to let pass before arming the capture. The FIRST frame after the
    // trigger train starts is 100% saturated -- it integrated through the idle
    // between the sequencer switching on and the first trigger -- and skipping
    // it is what makes an 8-frame sweep usable rather than 7/8 usable.
    parameter integer SKIP_FRAMES = 4,
    // EXTERNAL CLOCKING. 0 = make clk200/clk100 from an internal MMCM (the
    // standalone build, unchanged). 1 = take them from the parent.
    //
    // The merged HDMI+camera design already generates both -- ref_clk_pll makes
    // the 200 MHz IDELAY reference and clk100_g the buffered 100 MHz -- so the
    // internal MMCM there is pure duplication, and duplication is expensive: the
    // 100T has SIX MMCMs and the merged design wants exactly six. Freeing one
    // also relieves clock-region pressure, which is what the merged build's
    // remaining timing violation is actually about.
    parameter integer EXT_CLK = 0
)(
    input  wire        clk,
    input  wire        rst_n,
    // Only used when EXT_CLK != 0. With EXT_CLK = 0 they have no load and are
    // trimmed, so the standalone build gains no pins and needs no constraints.
    input  wire        clk200_ext,
    input  wire        clk100_ext,
    output reg  [7:0]  led,
    output wire        usb_tx,        // 1 Mbaud status on the Pt's own USB (COM6)

    // M4: the same status this module already assembles for its own UART, handed
    // to the parent so a MERGED build can report camera health on Port A without
    // taking Port A away from the SLI control plane. See cstat_r below.
    output wire [63:0] cam_stat_o,
    output wire        cam_stat_tog_o,

    input  wire        cam_clkout_p, cam_clkout_n,
    input  wire [3:0]  cam_d_p,      cam_d_n,
    input  wire        cam_sync_p,   cam_sync_n,
    output wire        cam_sck, cam_mosi, cam_ss_n,
    input  wire        cam_miso,
    output wire        cam_reset_n, cam_clk_pll,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor,

    input  wire        ft_clk,
    inout  wire [31:0] ft_data,
    inout  wire [3:0]  ft_be,
    input  wire        ft_txe, ft_rxf,
    output wire        ft_wr, ft_oe, ft_rd,
    output wire        ft_wakeup, ft_reset,

    inout  wire [15:0] ddr3_dq,
    inout  wire [1:0]  ddr3_dqs_p, ddr3_dqs_n,
    output wire [13:0] ddr3_addr,
    output wire [2:0]  ddr3_ba,
    output wire        ddr3_ras_n, ddr3_cas_n, ddr3_we_n, ddr3_reset_n,
    output wire [0:0]  ddr3_ck_p, ddr3_ck_n, ddr3_cke, ddr3_cs_n,
    output wire [1:0]  ddr3_dm,
    output wire [0:0]  ddr3_odt
);
    // 10 BITS PER PIXEL, carried as 16-bit little-endian with the value in the
    // low 10 bits and the top 6 zero. The sensor runs in 10-bit mode -- the LVDS
    // words are 10 bits wide, which is why the sync codes are values like 0x2AA --
    // and cam_sync_decode already recovers all ten. The previous packing threw the
    // bottom two away with kp[9:2].
    //
    // 8 pixels x 16 bits = 128 bits = EXACTLY one MIG native word, so one decoded
    // kernel is now one DDR write. That is not just tidy, it deletes the two-read
    // W_LO/W_HI pairing that was silently corrupting every captured frame (see the
    // cfifo_rd note below).
    // DENSE 10-BIT PACKING. The sensor is 10-bit and the payload used to ship it
    // in 16-bit words, so 37.5% of every byte moved -- through DDR and over USB --
    // was zero padding. At 120 Hz that padding is the difference between fitting
    // and not: 16 bpp needs 314.6 MB/s where the link delivers ~232, while packed
    // needs 196.6 MB/s. Packing is LOSSLESS; nothing is thrown away.
    //
    // The ratio is what makes it tractable: 8 pixels x 10 bits = 80 bits, so
    // 8 kernels = 640 bits = EXACTLY five 128-bit DDR words, and a frame is
    // 20,480 such groups with nothing left over.
    localparam integer NPIX   = NCOL * NROW;      // 1,310,720 pixels
    localparam integer NKERN  = NPIX / 8;         // 163,840 kernels per frame
    localparam integer NWORDS = (NKERN * 5) / 8;  // 102,400 DDR words per frame
    localparam integer FBYTES = (NPIX * 10) / 8;  // 1,638,400 bytes/frame at 10 bpp
    localparam integer NTOT   = NWORDS * NFRAMES; //   819,200 words = 13 MB of 256
    localparam integer ASTEP  = 8;
    localparam [31:0]  MAGIC  = 32'h30494C53;   // "SLI0" -- a video frame
    // M5: control replies share the IN pipe with frames, so every packet on the
    // wire is now "32-byte header + payload" and the MAGIC says which kind.
    // Distinct value, and h[7] still carries its inverse, so a host that loses
    // its place can resynchronise rather than drift.
    localparam [31:0]  RMAGIC = 32'h31494C53;   // "SLI1" -- a control reply
    localparam integer RBYTES = 16;             // one 128-bit payload word

    assign ft_wakeup = 1'b1;

    // PULSE THE FT601's RESET_N AFTER CONFIGURATION -- do not just tie it to rst_n.
    //
    // ft_reset used to be `assign ft_reset = rst_n`, which never pulses: rst_n
    // sits high across a bitstream load, so the FT601 is only ever reset when
    // somebody presses the board button. The chip can sit enumerated on USB --
    // D3XX lists it, its EEPROM reads back correctly -- while not driving ft_clk
    // at all, and then NOTHING moves: the ft_clk domain has no clock, the DDR->USB
    // FIFO fills to exactly its 1024-word depth and the host's read blocks
    // forever with zero bytes. That is what happened here, and it was only
    // diagnosed by ft_probe_bottom.v, which square-waves RESET_N -- the FT601
    // came back clocking afterwards and this design suddenly worked, having not
    // changed at all.
    //
    // ~42 ms low at 100 MHz, then release. The FT601 re-enumerates afterwards
    // (about a second), so a host tool should retry its open -- ft_video_grab.py
    // already does.
    reg [21:0] ftrst_cnt = 22'd0;
    reg        ftrst_rel = 1'b0;
    always @(posedge clk) begin
        if (!rstn_sync[1]) begin
            ftrst_cnt <= 22'd0;
            ftrst_rel <= 1'b0;
        end else if (ftrst_cnt != {22{1'b1}}) begin
            ftrst_cnt <= ftrst_cnt + 22'd1;
        end else begin
            ftrst_rel <= 1'b1;
        end
    end
    assign ft_reset = ftrst_rel;

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    wire clk200, clk100, mmcm_locked;
    generate
    if (EXT_CLK != 0) begin : g_extclk
        // Parent supplies both clocks, already on global buffers.
        assign clk200 = clk200_ext;
        assign clk100 = clk100_ext;

        // NO MMCM MEANS NO LOCKED, AND LOCKED IS LOAD-BEARING HERE: it gates the
        // IDELAYCTRL reset and the MIG reset, and a glitch on the latter aborts
        // DDR calibration -- the exact fault that made one build never calibrate
        // and an identical one always calibrate, differing only in placement.
        //
        // Substituting a settle counter ON THE SUPPLIED CLOCK preserves the
        // property that actually matters. The counter cannot advance unless that
        // clock is really running, so "locked" still means "the clock exists and
        // has been stable a while" -- and it is synchronous to clk100, so the
        // release cannot glitch the way an asynchronous LOCKED did.
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
        BUFG u_fb (.I(fb), .O(fb_g));
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

    //--------------------------------------------------- camera front end
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    cam_boot_stage1 #(.CLK_HZ(100_000_000), .BAUD(1_000_000), .STOP_AT(45),
                      .TRIGGERED(TRIGGERED), .EXPOSURE(EXPOSURE)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .expo_req(expo_req), .expo_val(expo_val),
        .led(boot_led), .usb_tx(), .usb_rx(1'b1),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(),
        .cam_monitor(cam_monitor)
    );

    // TRIGGER GENERATOR. cam_boot_stage1 ties its own cam_trigger to 3'b000, so
    // the trigger line has to be driven here or triggered mode can never fire.
    //
    // Held low until `streaming` -- the sequencer is only switched on by the last
    // ROM write (192 bit 0), and a trigger before that is simply lost. Starting
    // the pulse train at `streaming` also makes the pre-first-trigger idle as
    // short as the design allows, which is exactly the variable under test.
    //
    // The frame-period counter measures the interval between frame_starts, so in
    // triggered mode it reports the TRIGGER period. That is a free cross-check
    // that the trigger is really driving acquisition rather than the sensor
    // free-running underneath us: expect 8 * TRIG_US, not 8 * 6.860 ms.
    localparam integer TRIG_PER = (TRIG_CY != 0) ? TRIG_CY : (TRIG_US * 100);

    // PULSE-WIDTH SWEEP ACROSS THE BURST -- eight exposure points, ONE build.
    //
    // Continuous triggering at 7.5 ms came back 100% saturated with exposure0 =
    // 1600 (600 us), and the size of the error is the clue: integrating for the
    // whole 7.5 ms interval instead of 600 us is 12.5x, which lands exactly on
    // the 1023 ceiling given the measured pedestal ~151 and signal ~63. So
    // exposure0 looks ignored in triggered mode, and something about the trigger
    // interval sets integration instead.
    //
    // If the TRIGGER PULSE WIDTH is what sets it -- the "expose while the trigger
    // is asserted" behaviour common on other sensors -- then brightness tracks
    // the width. The burst captures 8 consecutive frames, so cycling the width
    // over 8 triggers gets all eight points from a single capture rather than
    // eight 13-minute builds:
    //
    //     50, 100, 200, 400, 800, 1600, 3200, 6400 us   (a 128x span)
    //
    // The capture arms on an arbitrary trigger, so the eight frames may come out
    // ROTATED relative to this list. That is why the widths increase
    // monotonically -- a rotation is obvious in the resulting brightness ramp,
    // and the wrap point identifies the phase. Reading:
    //
    //   brightness tracks width          -> pulse width IS the exposure control
    //   brightness tracks (period-width) -> integration runs while trigger is low
    //   all eight identical              -> neither; integration is the whole
    //                                       interval and width is irrelevant
    // THE SWEEP TABLE IS AN EXPERIMENT RIG AND MUST NOT RUN IN A REAL CAPTURE.
    //
    // It was never gated on EXPO_SWEEP, so it drove the trigger in EVERY
    // triggered build. tidx advances once per trigger period, cycling the pulse
    // width 50 -> 6400 us every eight frames, and at the top of the table the
    // trigger is held HIGH for 6.4 ms. The sensor also needs 6.86 ms to read
    // out, so that asks for 13.26 ms of work inside an 8.33 ms period: it
    // cannot comply. The result looked baffling from outside -- frame_start
    // arriving at a perfect 120.000 Hz while not one pixel kernel ever reached
    // the FIFO, so no frame completed and nothing reached USB.
    //
    // It also meant exposure was set by PULSE WIDTH, so exposure0 (opcode 1,
    // runtime-settable) had no effect in triggered mode at all.
    //
    // For a real capture the trigger only has to MARK THE FRAME START; the
    // sensor integrates for exposure0 on its own. 10 us is far wider than the
    // sensor needs to sample the edge and far shorter than any usable period.
    reg [2:0]  tidx    = 3'd0;
    reg [23:0] hi_cyc  = 24'd1_000;
    always @(*) begin
        if (EXPO_SWEEP == 0) hi_cyc = 24'd1_000;   // 10 us -- frame start marker
        else case (tidx)
        3'd0: hi_cyc = 24'd5_000;      //   50 us
        3'd1: hi_cyc = 24'd10_000;     //  100 us
        3'd2: hi_cyc = 24'd20_000;     //  200 us
        3'd3: hi_cyc = 24'd40_000;     //  400 us
        3'd4: hi_cyc = 24'd80_000;     //  800 us
        3'd5: hi_cyc = 24'd160_000;    // 1600 us
        3'd6: hi_cyc = 24'd320_000;    // 3200 us
        default: hi_cyc = 24'd640_000; // 6400 us
        endcase
    end

    reg [23:0] trig_per = TRIG_PER[23:0];   // runtime-settable, opcode 2
    reg [23:0] tcnt  = 24'd0;
    reg        trig0 = 1'b0;
    always @(posedge clk) begin
        if (rst || !streaming) begin
            tcnt  <= 24'd0;
            trig0 <= 1'b0;
            tidx  <= 3'd0;
        end else begin
            if (tcnt == trig_per - 24'd1) begin
                tcnt <= 24'd0;
                // Only the sweep advances the width index. Left ungated, this
                // changed the exposure on every frame of every triggered build.
                if (EXPO_SWEEP != 0) tidx <= tidx + 3'd1;
            end else begin
                tcnt <= tcnt + 24'd1;
            end
            trig0 <= (tcnt < hi_cyc);
        end
    end
    assign cam_trigger = {2'b00, (TRIGGERED != 0) ? trig0 : 1'b0};

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
        .wordclk(wordclk), .d0_word(d0_word), .d1_word(d1_word),
        .d2_word(d2_word), .d3_word(d3_word), .sync_word(sync_word)
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

    // EXPOSURE SWEEP ACROSS THE BURST -- brightness vs exposure time in ONE
    // capture instead of one bitstream per point.
    //
    // exposure0 units are mult_timer/f_pll = 27/72 MHz = 375 ns, so the table
    // below spans 37.5 us to 4.80 ms. All of it fits inside the 6.86 ms frame.
    // Free-running at 1600 (600 us) measures ~63 counts of signal over a ~151
    // pedestal, so this should sweep from barely-above-pedestal to ~8x that.
    //
    // frame_end lives in wordclk and the SPI writer in clk, so the request
    // crosses as a toggle + 2FF + edge detect. The value is registered alongside
    // the request: taking it combinationally from the index would send the NEXT
    // table entry, since the index advances on the same edge.
    reg  fs_tog = 1'b0;
    always @(posedge wordclk) if (frame_start) fs_tog <= ~fs_tog;
    reg [2:0] fs_s = 3'b000;
    always @(posedge ui_clk) fs_s <= {fs_s[1:0], fs_tog};
    wire fs_pulse = fs_s[2] ^ fs_s[1];      // frame_start, in ui_clk

    reg  fe_tog = 1'b0;
    always @(posedge wordclk) if (frame_end) fe_tog <= ~fe_tog;
    reg [2:0] fe_s = 3'b000;
    always @(posedge clk) fe_s <= {fe_s[1:0], fe_tog};
    wire fe_pulse = fe_s[2] ^ fe_s[1];

    reg  [2:0]  eidx     = 3'd0;
    reg  [15:0] expo_val = 16'd0;
    reg         expo_req = 1'b0;
    reg  [15:0] etab;
    always @(*) begin
        case (eidx)
        3'd0: etab = 16'd100;      //   37.5 us
        3'd1: etab = 16'd200;      //   75.0 us
        3'd2: etab = 16'd400;      //  150.0 us
        3'd3: etab = 16'd800;      //  300.0 us
        3'd4: etab = 16'd1600;     //  600.0 us  (the free-running reference)
        3'd5: etab = 16'd3200;     //  1.200 ms
        3'd6: etab = 16'd6400;     //  2.400 ms
        default: etab = 16'd12800; //  4.800 ms
        endcase
    end
    reg [15:0] expo_cur = EXPOSURE;         // what the sensor was last told
    reg        rearm_tog = 1'b0;
    reg        rpl_tog   = 1'b0;   // M5: toggled on opcode 5

    // FRAMES PER SCAN, runtime-settable (opcode 4). NFRAMES is only the power-on
    // default now. Bounded at MAXF because the burst has to fit DDR3 and the
    // address range: 63 frames x 2.62 MB = 165 MB of 256, and r_addr stays inside
    // 28 bits. A zero would make the write phase never terminate, so 0 is
    // rejected rather than clamped silently.
    localparam integer MAXF = 63;
    reg [5:0] nframes_r = NFRAMES[5:0];
    always @(posedge clk) begin
        expo_req <= 1'b0;
        if (rst) begin
            eidx <= 3'd0;
            expo_cur <= EXPOSURE;
            trig_per <= TRIG_PER[23:0];
            nframes_r <= NFRAMES[5:0];
        end else if ((EXPO_SWEEP != 0) && streaming && fe_pulse) begin
            expo_val <= etab;
            expo_req <= 1'b1;
            expo_cur <= etab;
            eidx     <= eidx + 3'd1;
        end else if (cw_pulse) begin
            case (cw_ft[31:28])
            4'd1: begin
                expo_val <= cw_ft[15:0];
                expo_req <= 1'b1;
                expo_cur <= cw_ft[15:0];
            end
            4'd2: if (cw_ft[23:0] > 24'd1000) trig_per <= cw_ft[23:0];
            4'd3: rearm_tog <= ~rearm_tog;
            // M5: opcode 5 asks for a status reply on the IN pipe.
            4'd5: rpl_tog <= ~rpl_tog;
            4'd4: if (cw_ft[5:0] != 6'd0 && cw_ft[5:0] <= MAXF[5:0])
                      nframes_r <= cw_ft[5:0];
            default: ;
            endcase
        end
    end

    // Re-arm crosses into both capture domains. Without it a new exposure changes
    // nothing the host can see: DDR still holds the burst captured at the old
    // value, and the capture is one-shot per bitstream load.
    reg [5:0] nf_w1 = 6'd0, nf_w2 = 6'd0;
    always @(posedge wordclk) begin nf_w1 <= nframes_r; nf_w2 <= nf_w1; end
    reg [5:0] nf_u1 = 6'd0, nf_u2 = 6'd0;
    always @(posedge ui_clk)  begin nf_u1 <= nframes_r; nf_u2 <= nf_u1; end

    reg [2:0] ra_w = 3'b000;
    always @(posedge wordclk) ra_w <= {ra_w[1:0], rearm_tog};
    wire rearm_w = ra_w[2] ^ ra_w[1];
    reg [2:0] ra_u = 3'b000;
    always @(posedge ui_clk) ra_u <= {ra_u[1:0], rearm_tog};
    wire rearm_u = ra_u[2] ^ ra_u[1];
    reg [2:0] rp_u = 3'b000;
    always @(posedge ui_clk) rp_u <= {rp_u[1:0], rpl_tog};
    wire rpl_u = rp_u[2] ^ rp_u[1];

    wire [9:0]  kp0,kp1,kp2,kp3,kp4,kp5,kp6,kp7;
    wire [10:0] kbase;
    wire        kvalid, line_start, frame_start, frame_end, in_black;
    cam_sync_decode u_dec (
        .wordclk(wordclk), .rst(wc_rst), .aligned(aligned),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .kpix0(kp0), .kpix1(kp1), .kpix2(kp2), .kpix3(kp3),
        .kpix4(kp4), .kpix5(kp5), .kpix6(kp6), .kpix7(kp7),
        .kbase(kbase), .kvalid(kvalid), .line_start(line_start),
        .frame_start(frame_start), .frame_end(frame_end), .in_black(in_black)
    );
    // ONE KERNEL, PACKED: 8 pixels x 10 bits = 80 bits = 10 bytes.
    //
    // Layout is the conventional packed-10 tiling -- four pixels in five bytes:
    // four bytes of the high 8 bits, then one byte holding the four pairs of low
    // bits, least-significant pixel first. Chosen over a raw bit-shift because it
    // makes the HOST side a vectorised numpy reshape rather than a bit-crawl, and
    // 4 px / 5 B tiles exactly into the 5-word group (64 px = 80 B).
    //
    // Byte order matters and follows what the old format established: kp0 sat in
    // the LOW 16 bits and the host read it first, so byte 0 of the stream is the
    // LOW byte here too. Verilog concatenation is MSB-first, so the first byte
    // out is the RIGHTMOST term.
    wire [7:0] pa0 = kp0[9:2], pa1 = kp1[9:2], pa2 = kp2[9:2], pa3 = kp3[9:2];
    wire [7:0] pa4 = { kp3[1:0], kp2[1:0], kp1[1:0], kp0[1:0] };
    wire [7:0] pb0 = kp4[9:2], pb1 = kp5[9:2], pb2 = kp6[9:2], pb3 = kp7[9:2];
    wire [7:0] pb4 = { kp7[1:0], kp6[1:0], kp5[1:0], kp4[1:0] };
    wire [79:0] kpk = { pb4, pb3, pb2, pb1, pb0, pa4, pa3, pa2, pa1, pa0 };

    // ARM THE CAPTURE ONLY WHEN THE DRAIN IS ALREADY RUNNING.
    //
    // cap used to start on the first frame_start after alignment, but the ui_clk
    // FSM sits in W_WAIT until init_calib_complete. Any pixel that arrives in
    // that window is written into a FIFO nobody is reading, and the telemetry
    // caught it: cfifo_ovf was SET on the very first run. This capture is
    // single-shot -- exactly NWORDS*2 words enter and the write FSM counts out
    // exactly that many -- so a dropped word does not merely blemish the image,
    // it shifts every pixel after it and leaves the FSM waiting forever for a
    // remainder that will never come. Gating on the FSM actually being in W_LO
    // makes the overflow flag mean something too: with no writes possible before
    // the arm, a set flag is now a real mid-frame drop and not a startup artifact.
    reg [1:0] icc_w = 2'b00;
    always @(posedge wordclk) icc_w <= {icc_w[0], init_calib_complete};
    reg [1:0] arm_s = 2'b00;
    // In scan mode the capture may only arm once the DDR writer is draining
    // (stw == W_LO). In LIVE mode the writer never runs at all, so gating on it
    // would leave cap permanently disarmed, the cfifo empty, and the live path
    // emitting nothing but zero-padded frames -- with ldrop climbing and no
    // obvious cause. Live arms on calibration alone.
    always @(posedge wordclk)
        arm_s <= {arm_s[0], (LIVE != 0) ? icc_w[1] : (stw == W_RUN)};

    // BURST CAPTURE -- NFRAMES consecutive frames, each bounded to exactly NKERN
    // kernels, then stop.
    //
    // THE BOUND IS NOT OPTIONAL. The sensor emits MORE than one frame's worth of
    // kernels per frame: cam_sync_decode accumulates black reference lines (SC_BL)
    // as well as image lines, so a frame delivers 1024 image rows plus however many
    // black rows the PYTHON1300 is configured for. Single-shot capture got away
    // with it -- the write FSM counted out its NWORDS and simply stopped draining,
    // and the surplus overflowed into the sticky cfifo_ovf flag that has read 1
    // since the first run. Across a BURST that surplus is fatal: it is still in the
    // FIFO when the next frame starts, so every frame after the first is offset by
    // a growing number of words.
    //
    // Counting kernels from each frame_start and dropping everything past NKERN
    // makes each frame independently self-aligning, and makes cfifo_ovf mean what
    // it says again -- a set flag is now a real mid-frame drop.
    //
    // frame_start can coincide with kvalid: the framing words carry pixels (the
    // cause of the 16-pixel black bar), so FS itself delivers a kernel and the
    // reload value is 1, not 0. arm_now covers the same case on the very first
    // frame, when cap has not yet been registered high.
    reg [17:0] kcnt = 18'd0;
    reg [5:0]  fcnt = 6'd0;
    reg [5:0]  nf_cap = 6'd0;              // latched at arm; cannot move mid-burst
    reg        cap  = 1'b0, cap_dn = 1'b0;

    reg [3:0] skipc = 4'd0;
    always @(posedge wordclk) begin
        if (wc_rst) skipc <= 4'd0;
        else if (frame_end && skipc != SKIP_FRAMES[3:0]) skipc <= skipc + 4'd1;
    end
    wire arm_now = frame_start && !cap && !cap_dn && arm_s[1]
                   && (skipc == SKIP_FRAMES[3:0]);
    wire cap_act = cap || arm_now;

    // THE FIRST KERNEL OF A FRAME MUST NOT BE JUDGED BY THE PREVIOUS FRAME'S
    // COUNT. kcnt saturates at NKERN every single frame -- the sensor delivers
    // exactly NKERN image kernels plus black rows -- and it is only RELOADED at
    // the clock edge. So on the frame_start cycle the guard below still saw the
    // OLD, saturated kcnt and suppressed the write, while the reload below
    // asserted kcnt = 1 as though that kernel had been taken. Every frame was
    // one kernel -- eight pixels -- short, and the count claimed otherwise.
    //
    // On its own that is 8 px/frame; at 120 Hz it walks a whole row every 1.3 s.
    // It is not the largest term here (a cfifo overflow loses far more) but it
    // is a permanent, silent, systematic deficit, and the SOF resync downstream
    // cannot correct a count that lies about what it wrote.
    wire        kreload  = arm_now || (cap && frame_start);
    wire [17:0] kcnt_eff = kreload ? 18'd0 : kcnt;
    wire cap_wr  = cap_act && kvalid && (kcnt_eff != NKERN[17:0]);

    // START-OF-FRAME TAG, travelling WITH the kernel.
    //
    // The writer on the far side of the cfifo needs to know where a frame
    // begins. It cannot use frame_start: that crosses to ui_clk through a
    // toggle and two flip-flops in ~3 cycles, while the kernel it refers to is
    // queued behind up to 2047 entries of FIFO. The pulse overtakes its own
    // data and would cut frames in the wrong place -- which is exactly why the
    // retired FIFO-path resync could not simply be moved over.
    //
    // So the boundary rides IN the data. One extra bit, set on the first kernel
    // actually written in each frame, is unambiguous no matter how deep the
    // FIFO is or how long the writer stalls.
    wire sof_tag = cap_wr && (kcnt_eff == 18'd0);

    always @(posedge wordclk) begin
        if (wc_rst || rearm_w) begin
            cap <= 1'b0; cap_dn <= 1'b0; kcnt <= 18'd0; fcnt <= 6'd0;
        end else if (arm_now) begin
            cap    <= 1'b1;
            nf_cap <= nf_w2;
            kcnt   <= kvalid ? 18'd1 : 18'd0;
        end else if (cap) begin
            if (frame_start)                        kcnt <= kvalid ? 18'd1 : 18'd0;
            else if (kvalid && kcnt != NKERN[17:0]) kcnt <= kcnt + 18'd1;

            if (frame_end) begin
                if (LIVE != 0) begin
                    fcnt <= 6'd0;          // live: stay armed, capture forever
                end else if (fcnt == nf_cap - 6'd1) begin
                    cap <= 1'b0; cap_dn <= 1'b1;      // burst complete, one shot
                end else fcnt <= fcnt + 6'd1;
            end
        end
    end

    // FRAME-RATE MONITOR -- every interval, continuously, in windows of NWIN.
    //
    // Measures the wordclk cycles between successive frame_starts and, over each
    // window of NWIN frames, accumulates the total and tracks the MIN and MAX
    // single interval. A mean alone cannot tell a steady 120 Hz from one that
    // drops a frame and runs the rest early, so min/max is the part that makes
    // this a check rather than an assertion: for a clean run they must equal the
    // mean to within a cycle or two.
    //
    // Continuous windows mean the benchmark repeats forever -- read COM6 for as
    // long as you like and every line is another independent NWIN-frame run.
    // This is deliberately NOT tied to the DDR capture: frame timing is a
    // property of the sensor and trigger, and re-arming a one-shot capture would
    // limit the benchmark to a single window per bitstream load.
    //
    // wordclk is the sensor's recovered 72.000 MHz, so
    //     fps = NWIN * 72e6 / wtot
    localparam integer NWIN = 24;

    reg [19:0] icnt = 20'd0;                       // cycles since last frame_start
    reg [27:0] wtot = 28'd0;
    reg [19:0] wmin = 20'hFFFFF, wmax = 20'd0;
    reg [4:0]  wn   = 5'd0;
    reg        wgo  = 1'b0;                        // first frame_start has no interval
    reg [27:0] wtot_l = 28'd0;                     // latched, stable for a whole window
    reg [19:0] wmin_l = 20'd0, wmax_l = 20'd0;

    wire [19:0] iv    = icnt + 20'd1;              // exact interval, no off-by-one
    wire [27:0] tot_n = wtot + {8'd0, iv};
    wire [19:0] min_n = (iv < wmin) ? iv : wmin;
    wire [19:0] max_n = (iv > wmax) ? iv : wmax;

    always @(posedge wordclk) begin
        if (wc_rst) begin
            icnt <= 20'd0; wtot <= 28'd0; wmin <= 20'hFFFFF; wmax <= 20'd0;
            wn <= 5'd0; wgo <= 1'b0;
            wtot_l <= 28'd0; wmin_l <= 20'd0; wmax_l <= 20'd0;
        end else if (frame_start) begin
            icnt <= 20'd0;
            if (!wgo) begin
                wgo <= 1'b1;
            end else if (wn == NWIN[4:0] - 5'd1) begin
                wtot_l <= tot_n; wmin_l <= min_n; wmax_l <= max_n;
                wtot <= 28'd0; wmin <= 20'hFFFFF; wmax <= 20'd0; wn <= 5'd0;
            end else begin
                wtot <= tot_n; wmin <= min_n; wmax <= max_n; wn <= wn + 5'd1;
            end
        end else begin
            icnt <= icnt + 20'd1;
        end
    end

    //------------------------------------------------- 80-bit -> 128-bit packer
    //
    // Kernels are 80 bits, DDR words are 128, so words come out on 5 of every 8
    // kernels. A holding register accumulates and spills whenever 128 bits are
    // available; it never backs up, because one kernel can complete at most one
    // word (80 < 128) and kernels arrive only every other wordclk.
    //
    // 207 bits is the true worst case: 127 left over plus a fresh 80.
    //
    // This also RELIEVES the cfifo rather than loading it. The FIFO now takes 5
    // writes per 8 kernels instead of 8, so its 1024 entries cover ~45 us of
    // sensor burst instead of 28 -- useful, given the LUTRAM finding that its
    // depth cannot be increased.
    // IT IS A GROUP BUILDER, NOT A BIT SHIFTER, and that distinction is what
    // makes it fit. The obvious version -- a 208-bit accumulator spilling via
    // `<< pk_n` -- synthesises a 208-bit VARIABLE BARREL SHIFTER, and it would
    // not place: this design already spends 2789 LUTs on distributed-RAM FIFOs
    // (see the cfifo note), and the shifter drove demand for LUTRAM-capable
    // slices to 202% of the device. Every shift below is by a CONSTANT, which
    // costs wiring rather than logic.
    //
    // Fill one 640-bit group over 8 kernels, then drain it as five words while
    // the next group fills. A group completes every 16 wordclks (kernels arrive
    // every other cycle) and drains in 5, so the two never collide.
    //
    // shreg_extract = "no" keeps these out of SRLs -- they would land back in
    // the very SLICEMs the FIFOs need.
    (* shreg_extract = "no" *) reg [639:0] gbuf = 640'd0;   // filling
    (* shreg_extract = "no" *) reg [639:0] gout = 640'd0;   // draining
    reg [2:0] gcnt  = 3'd0;         // kernels gathered into gbuf
    reg [2:0] gw    = 3'd0;         // words left to emit from gout
    reg       gsof  = 1'b0;         // gout's first word begins a frame
    reg       gsofp = 1'b0;         // gbuf begins a frame

    // Kernel 0 must end up in the LOW bits so it leaves first. Inserting at the
    // top and shifting down by a fixed 80 puts it there after 8 kernels.
    wire [639:0] gnext = {kpk, gbuf[639:80]};

    wire         pk_emit = (gw != 3'd0);
    wire [128:0] pk_word = {gsof, gout[127:0]};

    always @(posedge wordclk) begin
        if (wc_rst) begin
            gbuf <= 640'd0; gout <= 640'd0;
            gcnt <= 3'd0; gw <= 3'd0; gsof <= 1'b0; gsofp <= 1'b0;
        end else begin
            if (gw != 3'd0) begin
                gout <= {128'd0, gout[639:128]};
                gw   <= gw - 3'd1;
                gsof <= 1'b0;              // only the first word carries SOF
            end
            if (cap_wr) begin
                gbuf <= gnext;
                if (sof_tag) begin
                    // A FRAME ALWAYS STARTS A FRESH GROUP. NKERN is a multiple
                    // of 8 so an intact frame ends on a group boundary anyway --
                    // but if kernels were lost, carrying the residue would shift
                    // every pixel of the next frame by part of a word. Restarting
                    // the count keeps each frame independently aligned, the same
                    // rule the DDR writer enforces one level up. The stale low
                    // bits of gbuf are shifted out by the next seven kernels.
                    gcnt  <= 3'd1;
                    gsofp <= 1'b1;
                end else if (gcnt == 3'd7) begin
                    gcnt  <= 3'd0;
                    gout  <= gnext;        // hand the finished group to the drain
                    gw    <= 3'd5;
                    gsof  <= gsofp;
                    gsofp <= 1'b0;
                end else begin
                    gcnt <= gcnt + 3'd1;
                end
            end
        end
    end

    wire cfifo_full, cfifo_empty, cfifo_ovf;
    // 129 bits: {sof_tag, kernel}. See sof_tag above -- the frame boundary has
    // to travel with the data, not alongside it.
    wire [128:0] cfifo_dout;
    // cfifo_rd MUST be combinational, and this was a real, measured bug.
    //
    // cam_async_fifo is first-word-fall-through: rd_data is the head whenever
    // !empty, and rd_en pops it at the clock edge -- so the consumer must SAMPLE
    // AND POP IN THE SAME CYCLE. cfifo_rd used to be a register assigned inside
    // the clocked block, so the pop landed one cycle after the sample. W_LO and
    // W_HI therefore both latched the SAME kernel into the two halves of the DDR
    // word, and the following kernel was popped without ever being used.
    //
    // Every frame captured through the DDR path before this was really 640 columns
    // stretched to 1280 in 8-pixel blocks. It was invisible because there is no
    // lens and the scene is featureless, and ddr_bist could not catch it because
    // that test drives r_wdata from an internal counter and never goes through the
    // FIFO. Proved after the fact on saved frames: 81920/81920 16-byte groups had
    // low 8 == high 8, against 0.02% for the same test at a shifted alignment.
    //
    // AW 8 -> 10 as well. One kernel is now one DDR word, so the drain is 2 ui_clk
    // cycles per kernel instead of 3 per two, and 1024 entries covers a full DDR3
    // refresh stall (tRFC ~260 ns) with room to spare.
    //
    // DEPTH STAYS AT 1024, AND THE REASON IS WORTH RECORDING.
    //
    // 1024 entries is 28 us of camera data: enough for a refresh stall, not for
    // a sustained shortfall across the sensor's 4.55 ms readout burst -- which
    // is what was actually happening. The writer's old two-cycle handshake
    // capped it at 50 M kernels/s against a 36 M/s burst, so any MIG stall
    // pushed it under and the rest of the readout was dropped. Measured effect:
    // delivered frames sat hundreds of rows out of registration, each at its
    // own offset.
    //
    // AW 10 -> 11 was tried as margin and FAILED TO PLACE: 239% of the device's
    // LUTRAM-capable slices. This FIFO is built from distributed RAM, not block
    // RAM, and the `ram_style = "block"` attribute on its memory is INEFFECTIVE
    // -- first-word-fall-through is implemented with a COMBINATIONAL read
    // (assign rd_data = mem[...]) and block RAM has no asynchronous read port,
    // so the tool has no choice. Deepening this FIFO costs LUTs quadratically
    // and is not available without restructuring it around a registered read.
    //
    // It does not need to be: depth only buys time against a rate deficit, and
    // the single-cycle writer below removes the deficit itself -- 100 M
    // kernels/s against 36 M/s needed, a 2.8x margin where there was 1.39x.
    //
    // AW 10 -> 9, and the BINDING LIMIT IS GEOMETRIC, not logical. wordclk comes
    // off a BUFR -- a REGIONAL buffer spanning ONE clock region -- so this RAM is
    // confined to that region's ~600 LUTRAM-capable slices. At 1K x 129 it wants
    // 688 RAM64M on its own, and the packer's registers share the region, so the
    // placer reported 1148% of the constrained region and gave up.
    //
    // Packing already paid for the halving: an entry now holds 1.6 kernels
    // rather than 1, so 512 entries buffer 819 kernels (~23 us of sensor burst)
    // against the 1024 kernels (~28 us) of the pre-packing design. Near enough
    // the same elasticity for half the silicon, and the single-cycle writer
    // needs far less of it than the two-cycle one did.
    wire cfifo_rd;
    cam_async_fifo #(.DW(129), .AW(9)) u_cfifo (
        .wr_clk(wordclk), .wr_rst(wc_rst), .wr_en(pk_emit),
        .wr_data(pk_word), .full(cfifo_full), .overflow(cfifo_ovf),
        .rd_clk(ui_clk), .rd_rst(ui_rst), .rd_en(cfifo_rd),
        .rd_data(cfifo_dout), .empty(cfifo_empty)
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

    reg [27:0]  waddr = 28'd0;      // writer pointer
    reg [27:0]  raddr = 28'd0;      // reader pointer, chases the writer
    // r_wdata is gone: app_wdf_data is driven straight from the FIFO head, which
    // FWFT holds stable until the pop. The staging register was the second cycle.
    reg         cmd_done = 1'b0, dat_done = 1'b0;

    // CONCURRENT CAPTURE AND PLAYBACK.
    //
    // These were one FSM in sequence: write all N frames, then replay them. A
    // scan cost capture PLUS download -- 200 ms + 182 ms at 24 frames. Run
    // together, the download trails capture by one frame and a scan finishes in
    // ~205 ms.
    //
    // The MIG has ONE command port and both sides need it. THE WRITER ALWAYS
    // WINS: a dropped camera word is gone forever, a stalled reader merely
    // catches up. Never invert this.
    //
    // The reader may only touch frames the writer has FINISHED (rf < wf_done).
    // A frame read mid-write is a torn image that still looks like a photograph
    // -- the failure mode this project keeps producing. After capture wf_done
    // sticks at nf_run, so every frame stays available and the reader loops the
    // set forever, exactly as before.
    // W_LO/W_ISSUE (pop in one cycle, issue in the next) became a single W_RUN
    // that pops AND issues in the SAME cycle, refilling from the FIFO on the
    // completion cycle. The old split cost two ui_clk cycles per kernel, capping
    // the writer at 50 M kernels/s against a 36 M/s sensor burst -- a 39% margin
    // that any MIG stall ate, after which the cfifo (28 us deep) overflowed and
    // the rest of the readout was lost. cam_async_fifo's own header assumed a
    // 100 M/s drain; the two-cycle handshake quietly halved it.
    //
    // W_PAD finishes a slot whose frame arrived short, so the host always gets
    // exactly NWORDS words per frame.
    localparam [2:0] W_WAIT=0, W_RUN=1, W_PAD=2, W_DONE=3, W_NEXT=4;
    localparam [1:0] R_IDLE=0, R_HDR=1, R_RUN=2, R_REPLY=3;
    // live path: idle -> header -> stream a frame -> (pad on underrun)
    localparam [1:0] L_IDLE=0, L_HDR=1, L_RUN=2, L_PAD=3;
    reg [1:0]  stl   = L_IDLE;
    reg [17:0] lwidx = 18'd0;
    reg [31:0] lidx  = 32'd0;
    reg [15:0] ldrop = 16'd0;      // frames abandoned, reported in telemetry
    reg [2:0] stw = W_WAIT;         // writer
    reg [1:0] str = R_IDLE;         // reader
    reg [17:0] wfw = 18'd0;         // words written within the current frame
    reg [5:0]  wf  = 6'd0;          // frame being written
    reg [5:0]  wf_done = 6'd0;      // frames FULLY written -- the fence
    reg [5:0]  rf  = 6'd0;          // frame being read
    // ---- ring-buffer state (LIVE mode)
    reg        w_acc   = 1'b1;      // capturing this frame, or discarding it
    reg        r_busy  = 1'b0;      // reader owns r_slot right now
    reg [2:0]  w_slot  = 3'd0;      // slot being written
    reg [2:0]  r_slot  = 3'd0;      // slot being read
    reg [15:0] w_skip  = 16'd0;     // frames discarded because the reader was on
                                    // that slot -- reported, never silent
    reg [5:0]  wf_seen = 6'd0;      // wf_done value the reader last served
    reg        rpl_pend = 1'b0;     // M5: a control reply is waiting for a boundary
    reg [31:0] rpl_seq  = 32'd0;    // so the host can spot a lost reply
    wire [2:0] w_next = (w_slot == NSLOT[2:0] - 3'd1) ? 3'd0 : w_slot + 3'd1;

    // Playback issues reads speculatively; the write phase is unchanged.
    reg  [17:0] rd_iss = 18'd0, rd_got = 18'd0;
    reg  [4:0]  outst  = 5'd0;
    reg  [23:0] kdrop  = 24'd0;     // surplus kernels discarded to realign

    //-------------------------------------------------- SOF-based framing
    // cam_async_fifo is first-word-fall-through, so the head is visible BEFORE
    // it is popped. That is what makes this work without a holding register:
    // the writer can look at the incoming kernel's SOF tag and decide what the
    // word means before committing to it.
    //
    // Three cases, and they are the whole realignment rule:
    //   wfw == 0 and NOT a frame start -> surplus (black rows, or the tail of a
    //        frame we skipped). Discard it. A slot must BEGIN on a frame start.
    //   wfw != 0 and IS a frame start  -> the frame in progress came up short.
    //        Pad the slot out rather than emit a short frame: a padded frame is
    //        visibly wrong for one frame, a short one desynchronises the host's
    //        parser permanently.
    //   otherwise -> the word belongs in the slot.
    //
    // This is what the DDR path never had. The retired FIFO path resynchronised
    // on fs_pulse, but fs_pulse cannot be reused here: it crosses to ui_clk in
    // ~3 cycles while its kernel is still queued behind the FIFO, so it cuts in
    // the wrong place. The tag rides with the data instead.
    wire head_ok  = !cfifo_empty;
    wire head_sof = cfifo_dout[128];
    wire w_drop   = (stw == W_RUN) && head_ok && (wfw == 18'd0) && !head_sof;
    wire w_short  = (stw == W_RUN) && head_ok && (wfw != 18'd0) &&  head_sof;
    wire w_take   = (stw == W_RUN) && head_ok && !w_drop && !w_short;
    // LIVE skips a frame rather than overwrite the slot the reader is on. A
    // skipped frame still walks the counters, it just never touches DDR.
    wire w_ddr    = (LIVE == 0) || w_acc;
    wire w_padw   = (stw == W_PAD);

    wire w_req = (((w_take && w_ddr) || w_padw)) && !cmd_done;
    wire w_wrq = (((w_take && w_ddr) || w_padw)) && !dat_done;
    wire r_req = (str == R_RUN) && (rd_iss != NWORDS[17:0])
                 && (outst < MAXOUT[4:0]) && !ufifo_afull;
    // Writer priority. r_ack must NOT count a grant that went to the writer,
    // or the reader would advance its pointer on a cycle it did not get.
    wire r_gnt = r_req && !w_req;
    wire r_ack = r_gnt && app_rdy;
    wire w_ack = w_req && app_rdy;
    // Command and data are accepted independently, so both are tracked; a word
    // that never goes to DDR (a skipped frame) completes immediately.
    wire w_cmd_ok = cmd_done || w_ack;
    wire w_dat_ok = dat_done || (w_wrq && app_wdf_rdy);
    wire w_fire   = w_padw ? (w_cmd_ok && w_dat_ok)
                           : (w_take && (!w_ddr || (w_cmd_ok && w_dat_ok)));
    // Slot base addresses. ASTEP is the BL8 stride, so a slot spans
    // NWORDS * ASTEP; 4 slots is 5.2 M, well inside the 28-bit address space.
    wire [27:0] w_base = w_slot * (NWORDS[27:0] * ASTEP);
    wire [27:0] r_base = r_slot * (NWORDS[27:0] * ASTEP);
    wire [27:0] w_eff  = (LIVE != 0) ? (w_base + waddr) : waddr;
    wire [27:0] r_eff  = (LIVE != 0) ? (r_base + raddr) : raddr;

    assign app_addr     = w_req ? w_eff : r_eff;
    assign app_cmd      = w_req ? 3'd0  : 3'd1;   // 0 = write, 1 = read
    assign app_en       = w_req || r_gnt;
    // Straight off the FIFO head -- no r_wdata staging register. FWFT holds the
    // head stable until it is popped, and the pop happens on the completion
    // cycle, so the data is valid for exactly as long as the MIG needs it. This
    // is what removes the second cycle per kernel.
    assign app_wdf_data = w_padw ? 128'd0 : cfifo_dout[127:0];
    assign app_wdf_wren = w_wrq;
    assign app_wdf_end  = w_wrq;

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
        .sys_rst(mig_rst)                    // ACTIVE HIGH per mig_pt_v2.prj
    );
    // MIG RESET: CLEAN, SYNCHRONOUS, WITH A CALIBRATION WATCHDOG.
    //
    // This was `!mrstn_s[1] || !mmcm_locked` -- mmcm_locked is ASYNCHRONOUS to
    // clk100 and was combined combinationally into sys_rst, so whether the
    // release glitched depended on routing delay. That matches the observed
    // failure exactly: cam_frame_ft_cc2.bin never calibrates and
    // cam_frame_ft_con.bin always does, same logic, different placement, each
    // reproducible. A glitch on sys_rst during calibration aborts it, and the
    // symptom is precisely init_calib_complete never asserting.
    //
    // Now: hold reset while unlocked or the button is pressed, then release from
    // a single register 128 clk100 cycles after lock. No combinational term, so
    // no glitch path.
    //
    // WATCHDOG. Because the mechanism above is inferred rather than proven, a
    // hang is also made self-healing: if calibration has not completed ~650 ms
    // after release, pulse sys_rst and try again. Whatever the cause, a retry
    // beats a board that silently does nothing -- and cal_retry in the telemetry
    // says whether it ever fired, so a marginal build cannot hide behind it.
    reg [1:0] mrstn_s = 2'b00;
    always @(posedge clk100) mrstn_s <= {mrstn_s[0], rst_n};

    reg [7:0]  rstcnt   = 8'd0;
    reg [25:0] calwdt   = 26'd0;
    reg [3:0]  cal_retry = 4'd0;
    reg        mig_rst_r = 1'b1;
    always @(posedge clk100) begin
        if (!mmcm_locked || !mrstn_s[1]) begin
            rstcnt <= 8'd0; calwdt <= 26'd0; mig_rst_r <= 1'b1;
        end else if (!rstcnt[7]) begin
            rstcnt <= rstcnt + 8'd1;          // 128 cycles of clean assertion
            mig_rst_r <= 1'b1;
        end else begin
            mig_rst_r <= 1'b0;                // released
            if (!init_calib_complete) begin
                if (calwdt == 26'd65_000_000) begin
                    rstcnt <= 8'd0;           // retry: re-assert and count again
                    calwdt <= 26'd0;
                    if (cal_retry != 4'hF) cal_retry <= cal_retry + 4'd1;
                end else calwdt <= calwdt + 26'd1;
            end else calwdt <= 26'd0;
        end
    end
    wire mig_rst = mig_rst_r;

    //--------------------- ui_clk -> ft_clk: the USB side of the crossing
    // 128 BITS WIDE, and that is the throughput fix.
    //
    // Playback used to read ONE DDR word, WAIT for it, then push it as 4 x 32
    // bits before issuing the next: a full DDR3 read round trip per 16 bytes.
    // At ~30 ui_clk of read latency that is ~53 MB/s, which is exactly the 51
    // MB/s measured -- while the same FT601 does 348 MB/s when fed directly.
    //
    // Now a returning 128-bit word is written in ONE cycle and the ft_clk side
    // unpacks it into 4 words, so reads can be issued many-deep and the latency
    // is amortised instead of paid per word.
    //
    // MAXOUT reads may be in flight. Their data MUST be accepted the cycle the
    // MIG presents it -- there is no back-pressure on app_rd_data_valid -- so
    // AFULL_MARGIN must exceed MAXOUT, and issuing stops on afull.
    localparam integer MAXOUT = 16;

    wire ufifo_full, ufifo_afull, ufifo_aempty, ufifo_empty, ufifo_ovf;
    wire [127:0] ufifo_dout;
    reg  [127:0] ufifo_din = 128'd0;
    reg          ufifo_wr = 1'b0;
    wire         ufifo_rd;

    cam_async_fifo #(.DW(128), .AW(8), .AFULL_MARGIN(MAXOUT + 8)) u_ufifo (
        .wr_clk(ui_clk), .wr_rst(ui_rst), .wr_en(ufifo_wr),
        .wr_data(ufifo_din), .full(ufifo_full), .afull(ufifo_afull),
        .aempty(ufifo_aempty),
        .overflow(ufifo_ovf),
        .rd_clk(ft_clk), .rd_rst(ft_rst), .rd_en(ufifo_rd),
        .rd_data(ufifo_dout), .empty(ufifo_empty)
    );

    reg [5:0]  nf_run = 6'd0;             // frames in the burst actually captured
    reg [2:0]  hw = 3'd0;                 // header word index
    reg [31:0] frame_idx = 32'd0;

    always @(posedge ui_clk) begin
        ufifo_wr <= 1'b0;

        // app_rd_data_valid is not back-pressurable: take it the cycle it is
        // presented or lose it. Guarded by AFULL_MARGIN > MAXOUT.
        if (!ui_rst && (str == R_RUN) && app_rd_data_valid) begin
            ufifo_wr  <= 1'b1;
            ufifo_din <= app_rd_data;
            rd_got    <= rd_got + 18'd1;
        end

        if (ui_rst) begin
            stw <= W_WAIT; str <= R_IDLE;
            waddr <= 28'd0; raddr <= 28'd0;
            wfw <= 18'd0; wf <= 6'd0; wf_done <= 6'd0; rf <= 6'd0;
            nf_run <= 6'd0; cmd_done <= 1'b0; dat_done <= 1'b0; hw <= 3'd0;
            frame_idx <= 32'd0; rd_iss <= 18'd0; rd_got <= 18'd0; outst <= 5'd0;
            rpl_pend <= 1'b0; rpl_seq <= 32'd0;
        end else if (rearm_u) begin
            // New scan: rewind BOTH sides. wf_done back to 0 re-arms the fence,
            // otherwise the reader would immediately serve frames left from the
            // previous scan before any new data had landed.
            stw <= W_RUN; str <= R_IDLE;
            waddr <= 28'd0; raddr <= 28'd0;
            wfw <= 18'd0; wf <= 6'd0; wf_done <= 6'd0; rf <= 6'd0;
            nf_run <= nf_u2; cmd_done <= 1'b0; dat_done <= 1'b0;
            rd_iss <= 18'd0; rd_got <= 18'd0; outst <= 5'd0;
        end else begin
            // outstanding-read accounting, on the READER's grants only
            if (r_ack) begin
                rd_iss <= rd_iss + 18'd1;
                raddr  <= raddr + ASTEP;
                if (!app_rd_data_valid) outst <= outst + 5'd1;
            end else if (app_rd_data_valid && (str == R_RUN) && (outst != 5'd0)) begin
                outst <= outst - 5'd1;
            end

            //---------------------------------------------------------- writer
            case (stw)
            W_WAIT: if (init_calib_complete) begin
                        stw <= W_RUN; waddr <= 28'd0;
                        wfw <= 18'd0; wf <= 6'd0; wf_done <= 6'd0;
                        nf_run <= nf_u2;
                        w_slot <= 3'd0; w_acc <= 1'b1;
                    end
            // ONE KERNEL, ONE DDR WORD, ONE CYCLE. app_wdf_data comes straight
            // off the FIFO head and cfifo_rd asserts combinationally on the
            // completion cycle, so the next kernel is fetched in the very cycle
            // the current one retires -- no idle pop cycle between writes.
            //
            // A DISCARDED frame (LIVE, reader owns the next slot) still walks
            // the counters and still drains the FIFO; it just never touches
            // DDR. Dropping a frame must cost nothing downstream.
            W_RUN: begin
                if (w_drop) begin
                    // Surplus kernel at a slot boundary -- popped combinationally
                    // above. This is the realignment actually happening, so it
                    // is counted rather than silent.
                    if (kdrop != 24'hFFFFFF) kdrop <= kdrop + 24'd1;
                end else if (w_short) begin
                    // A frame start arrived mid-slot: the frame under way lost
                    // kernels. Never emit a short frame.
                    if (ldrop != 16'hFFFF) ldrop <= ldrop + 16'd1;
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    if (w_ddr) stw <= W_PAD;    // finish the slot with zeros
                    else       stw <= W_NEXT;   // nothing was written; move on
                end else if (w_fire) begin
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    if (w_ddr)
                        waddr <= (wfw == NWORDS[17:0] - 18'd1) ? 28'd0
                                                              : waddr + ASTEP;
                    if (wfw == NWORDS[17:0] - 18'd1) begin
                        wfw <= 18'd0;
                        // Only a frame that actually reached DDR is readable.
                        if (w_ddr) wf_done <= wf_done + 6'd1;
                        if (LIVE != 0) stw <= W_NEXT;
                        else if (wf == nf_run - 6'd1) stw <= W_DONE;
                        else wf <= wf + 6'd1;
                    end else wfw <= wfw + 18'd1;
                end else begin
                    if (w_ack)                cmd_done <= 1'b1;
                    if (w_wrq && app_wdf_rdy) dat_done <= 1'b1;
                end
            end

            // Pad the remainder of a short frame's slot with zeros. The host
            // then always sees exactly NWORDS words per frame and its parser
            // stays locked; ring_check's "padded" count makes these visible.
            W_PAD: begin
                if (w_fire) begin
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    waddr <= (wfw == NWORDS[17:0] - 18'd1) ? 28'd0
                                                          : waddr + ASTEP;
                    if (wfw == NWORDS[17:0] - 18'd1) begin
                        wfw     <= 18'd0;
                        wf_done <= wf_done + 6'd1;
                        if (LIVE != 0) stw <= W_NEXT;
                        else if (wf == nf_run - 6'd1) stw <= W_DONE;
                        else begin wf <= wf + 6'd1; stw <= W_RUN; end
                    end else wfw <= wfw + 18'd1;
                end else begin
                    if (w_ack)                cmd_done <= 1'b1;
                    if (w_wrq && app_wdf_rdy) dat_done <= 1'b1;
                end
            end
            W_DONE: ;                     // capture complete; reader carries on

            // Choose the next slot and decide whether to keep the frame. The
            // ONLY rule that matters: never write the slot the reader is
            // currently streaming out of. Everything else is bookkeeping.
            W_NEXT: begin
                w_slot <= w_next;
                // THE RULE: never write the slot the reader is streaming from.
                // w_acc was declared and read but never assigned in the first
                // draft, so it sat at 1 permanently and the writer would have
                // overwritten a frame mid-read -- producing a torn image that
                // still looks like a photograph, which is the failure mode this
                // project keeps generating.
                w_acc  <= !(r_busy && (w_next == r_slot));
                if (r_busy && (w_next == r_slot) && (w_skip != 16'hFFFF))
                    w_skip <= w_skip + 16'd1;
                stw    <= W_RUN;
            end
            default: stw <= W_WAIT;
            endcase

            //------------------------------------------------ live path (LIVE=1)
            //
            // Alignment is the whole problem. Each frame contributes exactly
            // NWORDS kernels, so the word count alone would drift permanently
            // the first time a word is lost -- the next frame's pixels would be
            // used to finish this one and every frame after would be torn.
            //
            // So: resync on frame_start, and if the frame cannot be completed,
            // PAD the output to NWORDS with zeros rather than emitting a short
            // frame. A padded frame is visibly wrong in one frame; a short one
            // desynchronises the host's parser for good. ldrop counts them.
            // The FIFO-only live path is retired: LIVE now streams through the
            // DDR ring above. Kept disabled rather than deleted because its
            // failure -- 576 MB/s burst into a 325 MB/s pipe -- is the reason the
            // ring exists.
            if (1'b0) begin
                case (stl)
                // DO NOT START A FRAME WE CANNOT FINISH.
                //
                // The first version started whenever the FIFO was merely not
                // almost-full, then discovered mid-frame that the host could not
                // keep up and padded the rest -- with a host sustaining 157 MB/s
                // against the camera's 314, that meant EVERY frame arrived
                // padded and not one was usable.
                //
                // The FIFO holds far less than a frame, so a drained FIFO at
                // frame_start is the proxy for "the sink kept pace with the last
                // frame". Skipping cleanly gives fewer, WHOLE frames; the
                // alternative gives a full frame rate of ruined ones.
                L_IDLE: if (fs_pulse) begin
                            if (ufifo_aempty) begin
                                hw <= 3'd0; lwidx <= 18'd0; stl <= L_HDR;
                            end else if (ldrop != 16'hFFFF) begin
                                ldrop <= ldrop + 16'd1;   // skipped, not torn
                            end
                        end

                L_HDR: if (!ufifo_afull) begin
                    ufifo_wr <= 1'b1;
                    if (hw == 3'd0)
                        ufifo_din <= { {18'd0, 6'd1, 2'd0, 6'd0},
                                       {NROW[15:0], NCOL[15:0]},
                                       lidx, MAGIC };
                    else
                        ufifo_din <= { ~MAGIC, 32'd2, FBYTES/4, FBYTES };
                    if (hw == 3'd1) begin hw <= 3'd0; stl <= L_RUN; end
                    else hw <= hw + 3'd1;
                end

                L_RUN: begin
                    // a frame_start arriving mid-frame means words were lost
                    if (fs_pulse) begin
                        ldrop <= ldrop + 16'd1;
                        stl   <= L_PAD;
                    end else if (!cfifo_empty && !ufifo_afull) begin
                        ufifo_wr  <= 1'b1;
                        ufifo_din <= cfifo_dout[127:0];   // cfifo is 129b now
                        if (lwidx == NWORDS[17:0] - 18'd1) begin
                            lidx <= lidx + 32'd1;
                            stl  <= L_IDLE;
                        end else lwidx <= lwidx + 18'd1;
                    end
                end

                L_PAD: if (!ufifo_afull) begin
                    ufifo_wr  <= 1'b1;
                    ufifo_din <= 128'd0;
                    if (lwidx == NWORDS[17:0] - 18'd1) begin
                        lidx <= lidx + 32'd1;
                        lwidx <= 18'd0;
                        stl <= L_IDLE;
                    end else lwidx <= lwidx + 18'd1;
                end
                default: stl <= L_IDLE;
                endcase
            end

            //---------------------------------------------------------- reader
            case (str)
            // rf < wf_done IS the safety argument. During capture it holds the
            // reader behind the writer; afterwards wf_done == nf_run so every
            // frame is available and the reader loops indefinitely.
            // SHORTCUT EXPERIMENT. With CONCURRENT = 0 the reader additionally
            // waits for the writer to reach W_DONE, so this is the OLD sequential
            // behaviour carried by the NEW two-FSM structure. That separates two
            // things the failing build conflated:
            //
            //   calibrates and streams -> the FSM split is fine and the fault is
            //                             in concurrent access / the arbiter
            //   still no calibration   -> the split itself is implicated, with
            //                             no concurrency involved at all
            //
            // init_calib_complete never asserting is upstream of everything the
            // reader does, so if merely restructuring the FSM breaks it, the
            // cause is not the arbitration.
            // LIVE: jump to the NEWEST complete frame, do not queue. Old
            // frames are worthless in a live view, and serving them in order
            // would make the display lag further behind the longer it ran.
            // r_busy tells the writer which slot is off-limits.
            // M5: A REPLY IS SENT ONLY HERE, AT A FRAME BOUNDARY.
            //
            // R_IDLE is the one place the reader is between frames, so injecting
            // here means a frame is never interrupted and the host's fast path --
            // read a header, then blindly consume FBYTES -- is untouched. That
            // matters: bytes/frame-size is how the true delivered rate is
            // measured, and mid-frame insertion would break it.
            //
            // Checked BEFORE the new-frame test on purpose. If the camera stops,
            // the reader sits in R_IDLE forever, and that is exactly when control
            // must still answer -- a reply path gated on frames existing would go
            // silent in the one situation that needs it.
            R_IDLE: if (rpl_pend) begin
                        hw <= 3'd0; str <= R_REPLY;
                    end else if (LIVE != 0) begin
                        if (wf_done != wf_seen) begin
                            wf_seen <= wf_done;
                            r_slot  <= (w_slot == 3'd0) ? (NSLOT[2:0] - 3'd1)
                                                        : (w_slot - 3'd1);
                            r_busy  <= 1'b1;
                            raddr   <= 28'd0;
                            hw <= 3'd0; str <= R_HDR;
                        end
                    end else if ((rf < wf_done) &&
                        ((CONCURRENT != 0) || (stw == W_DONE))) begin
                        hw <= 3'd0; str <= R_HDR;
                    end

            // header as 2 x 128-bit entries, little-endian word order. Word 3
            // carries the slot and the scan length, so a scan is self-describing.
            R_HDR: if (!ufifo_afull) begin
                ufifo_wr <= 1'b1;
                // The 18 spare bits now carry ldrop -- the count of frames that
                // arrived short and were padded. Putting it in the HEADER rather
                // than only on the status UART means the host can attribute a
                // bad frame to a real drop instead of guessing, and a run of
                // frames with a static ldrop is positive proof that no kernels
                // were lost during it. cfifo_ovf on its own is sticky and cannot
                // distinguish "overflowed once at power-up" from "overflowing
                // continuously", which is exactly the ambiguity that made this
                // fault hard to pin down.
                if (hw == 3'd0)
                    ufifo_din <= { {2'd0, ldrop, (LIVE != 0) ? 6'd1 : nf_run,
                                   2'd0, (LIVE != 0) ? {3'd0, r_slot} : rf},
                                   {NROW[15:0], NCOL[15:0]},
                                   frame_idx, MAGIC };
                else
                    // Format 3 = DENSE PACKED 10-BIT (4 px in 5 bytes). Format 2
                    // was 10-bit right-aligned in 16-bit words. The host MUST
                    // switch on this rather than assume, or it will read packed
                    // bytes as u16 and produce a convincing-looking wrong image.
                    ufifo_din <= { ~MAGIC, 32'd3, FBYTES/4, FBYTES };
                if (hw == 3'd1) begin
                    hw <= 3'd0;
                    rd_iss <= 18'd0; rd_got <= 18'd0;
                    str <= R_RUN;
                end else hw <= hw + 3'd1;
            end

            // M5: reply packet -- same shape as a frame header so the host's
            // parser stays uniform: read 32 bytes, switch on the magic, consume
            // the length the header states.
            R_REPLY: if (!ufifo_afull) begin
                ufifo_wr <= 1'b1;
                case (hw)
                3'd0: ufifo_din <= { 32'd0, 32'd0, rpl_seq, RMAGIC };
                3'd1: ufifo_din <= { ~RMAGIC, 32'd1, RBYTES/4, RBYTES };
                // Payload: the same status the register map serves at 0x3A..0x41,
                // plus a fixed marker so a host can confirm it is talking to this
                // design and not mis-parsing payload as a header.
                default: ufifo_din <= { 32'd0, 32'h00000048, cstat_r };
                endcase
                if (hw == 3'd2) begin
                    hw <= 3'd0;
                    rpl_pend <= 1'b0;
                    rpl_seq  <= rpl_seq + 32'd1;
                    str <= R_IDLE;
                end else hw <= hw + 3'd1;
            end

            // Reads issued up to MAXOUT deep; the frame ends when rd_got reaches
            // NWORDS, i.e. when the DATA is back -- not when the last read was
            // merely issued.
            R_RUN: if (rd_got == NWORDS[17:0]) begin
                frame_idx <= frame_idx + 32'd1;
                if (LIVE != 0) begin
                    r_busy <= 1'b0;                  // slot released
                    raddr  <= 28'd0;
                end else if (rf == nf_run - 6'd1) begin
                    rf <= 6'd0; raddr <= 28'd0;      // wrap to frame 0
                end else begin
                    rf <= rf + 6'd1;
                end
                str <= R_IDLE;
            end

            default: str <= R_IDLE;
            endcase

            // M5: latch the request AFTER the case above. A request that lands in
            // the same cycle a reply is retired must survive -- assigning it here
            // means the set wins over the clear, so the next one is never dropped.
            // A request raised mid-frame simply waits for R_IDLE.
            if (rpl_u) rpl_pend <= 1'b1;
        end
    end

    // sample-and-pop in the same cycle -- FWFT requires it
    // sample-and-pop in the same cycle -- FWFT requires it
    // Both modes pop in W_RUN -- the ring writer discards a skipped frame by
    // popping without writing DDR, so the cfifo drains either way. Leaving this
    // pointed at the retired FIFO path would have starved the ring writer and
    // stalled capture completely.
    //
    // Two pops, and the distinction is the realignment: w_drop retires a
    // surplus kernel at a slot boundary WITHOUT writing it, and w_take && w_fire
    // retires a kernel that was written. Popping on the completion cycle rather
    // than a cycle later is what makes the writer single-cycle.
    assign cfifo_rd = w_drop || (w_take && w_fire);

    //------------------------------------------------- the FT601 write master
    reg [1:0] ftrst_s = 2'b11;
    always @(posedge ft_clk) ftrst_s <= {ftrst_s[0], ~rst_n};
    wire ft_rst = ftrst_s[1];

    // TWO-DEEP SKID, and it is a timing fix, not tidiness.
    //
    // Feeding ft601_sync_tx directly from the FIFO status put an 11-bit gray-code
    // comparison in the same logic cone as ft_txe, which the FT601 may present up
    // to 7.0 ns after the clock edge -- under 3 ns for everything downstream. Two
    // successive versions failed there:
    //     ft_txe -> u_ft/wr_n_pad_reg_rep/D   at -0.148
    //     ft_txe -> hold_word_reg[4]/CE       at -0.080
    // The second was a one-deep holding register whose pop term was
    // !empty && (!valid || adv) -- `empty` and `ft_txe` combined, so the late
    // signal had to wait behind the wide comparison.
    //
    // With two entries the pop decision depends only on OCCUPANCY:
    //
    //     pop = !empty && (cnt != 2)
    //
    // and `empty` is out of the ft_txe cone completely. ft_txe now reaches only
    // cnt/b0/b1 enables, through a single level. Throughput is unchanged: with the
    // FT601 taking a word every cycle, cnt settles at 1 and pop fires every cycle
    // (cnt=2 -> consume -> cnt=1 -> pop+consume -> cnt=1 ...).
    //
    // b0 is the head presented to the FT601. Losing a word here is not a blemish;
    // it shifts every pixel after it, so the skid is written as an explicit
    // push/pop case split rather than trusting a shift register.
    // Unpack one 128-bit FIFO entry into four 32-bit words. FWFT: u_word is
    // valid while u_valid, and u_take consumes it.
    reg [127:0] uw    = 128'd0;
    reg [1:0]   uidx  = 2'd0;
    reg         uvalid = 1'b0;
    wire        u_take;
    wire        upop = !ufifo_empty && (!uvalid || (u_take && uidx == 2'd3));
    assign ufifo_rd = upop;
    always @(posedge ft_clk) begin
        if (ft_rst) begin
            uvalid <= 1'b0; uidx <= 2'd0;
        end else if (upop) begin
            uw <= ufifo_dout; uidx <= 2'd0; uvalid <= 1'b1;
        end else if (u_take) begin
            if (uidx == 2'd3) uvalid <= 1'b0;
            else uidx <= uidx + 2'd1;
        end
    end
    wire [31:0] u_word = uw[uidx*32 +: 32];

    reg [31:0] b0 = 32'd0, b1 = 32'd0;
    reg [1:0]  cnt = 2'd0;                 // 0, 1 or 2 words held
    wire       ft_adv;                     // the FT601 took the head this cycle
    wire       pop = uvalid && (cnt != 2'd2);
    assign u_take = pop;

    always @(posedge ft_clk) begin
        if (ft_rst) begin
            cnt <= 2'd0;
        end else begin
            case ({pop, ft_adv})
            2'b10: begin                                   // push only
                if (cnt == 2'd0) b0 <= u_word;
                else             b1 <= u_word;
                cnt <= cnt + 2'd1;
            end
            2'b01: begin                                   // the head was taken
                b0  <= b1;
                cnt <= cnt - 2'd1;
            end
            2'b11: begin                                   // taken and refilled
                if (cnt == 2'd1) b0 <= u_word;             // head out, new head in
                else begin b0 <= b1; b1 <= u_word; end
            end
            default: ;
            endcase
        end
    end

    // ---- host -> FPGA control channel, over the Ft+ itself.
    //
    // The Pt's COM6 UART is bring-up scaffolding; the delivered system reaches
    // this board ONLY through the Ft+, so commands arrive on the FT601 OUT pipe
    // (0x02) and the DATA bus becomes genuinely bidirectional. ft601_sync_rx
    // owns OE#/RD#/bus_oe and gates the TX with rx_hold during read windows.
    wire        rx_hold, bus_oe;
    wire [31:0] cmd_word;
    wire        cmd_valid;
    wire [15:0] cmd_count;
    wire [3:0]  rx_dbg;
    ft601_sync_rx u_ftrx (
        .clk(ft_clk), .rst(ft_rst),
        .ft_rxf(ft_rxf), .ft_din(ft_data),
        .ft_oe(ft_oe), .ft_rd(ft_rd),
        .bus_oe(bus_oe), .rx_hold(rx_hold),
        .cmd_word(cmd_word), .cmd_valid(cmd_valid), .cmd_count(cmd_count),
        .dbg(rx_dbg)
    );

    // Commands are single 32-bit words: [31:28] opcode, [27:0] payload. One word
    // per command means the decoder is stateless -- a truncated USB transfer
    // cannot strand it half-way through a command.
    //   1 = exposure0 (payload[15:0])   2 = trigger period, clk cycles
    //   3 = re-arm the burst capture
    reg [31:0] cw_ft = 32'd0;
    reg        cw_tog = 1'b0;
    always @(posedge ft_clk) if (cmd_valid) begin
        cw_ft <= cmd_word; cw_tog <= ~cw_tog;
    end
    reg [2:0] cw_s = 3'b000;
    always @(posedge clk) cw_s <= {cw_s[1:0], cw_tog};
    wire cw_pulse = cw_s[2] ^ cw_s[1];       // cw_ft is stable well before this

    wire [31:0] ft_dout;
    wire [3:0]  ft_beout;
    ft601_sync_tx u_ft (
        .clk(ft_clk), .rst(ft_rst),
        .ft_txe(ft_txe), .ft_wr(ft_wr), .ft_oe(), .ft_rd(),
        .rx_hold(rx_hold),
        .ft_dout(ft_dout), .ft_beout(ft_beout), .bus_oe(),
        .s_word(b0), .s_valid(cnt != 2'd0), .s_adv(ft_adv)
    );
    // REPLICATE THE OUTPUT ENABLE, ONE FLOP PER PAD.
    //
    // ft601_sync_tx.v warned not to make bus_oe dynamic without re-checking
    // timing, and this is the bill. As a constant it was not a timing path at
    // all; as a register it is one signal driving 36 tri-state buffers spread
    // over two banks, it cannot pack into the IOB T flops, and Vivado times it
    // against the same set_output_delay as the data. It failed at -2.983 on
    // every ft_data bit.
    //
    // A dedicated flop per pad packs into each IOB's T register, so the enable
    // leaves from the pad itself. EQUIVALENT_REGISTER_REMOVAL="NO" stops the
    // tool merging them back into one and silently undoing it -- the same guard
    // the WR# replica needed.
    //
    // Costs one cycle of enable latency, which the 3-cycle turnaround on each
    // edge of the read window already covers.
    (* IOB = "TRUE", EQUIVALENT_REGISTER_REMOVAL = "NO" *)
    reg [31:0] doe = 32'hFFFFFFFF;
    (* IOB = "TRUE", EQUIVALENT_REGISTER_REMOVAL = "NO" *)
    reg [3:0]  boe = 4'hF;
    always @(posedge ft_clk) begin
        doe <= {32{bus_oe}};
        boe <= {4{bus_oe}};
    end

    genvar gi;
    generate
        for (gi = 0; gi < 32; gi = gi + 1) begin : g_ftdata
            assign ft_data[gi] = doe[gi] ? ft_dout[gi] : 1'bz;
        end
        for (gi = 0; gi < 4; gi = gi + 1) begin : g_ftbe
            assign ft_be[gi] = boe[gi] ? ft_beout[gi] : 1'bz;
        end
    endgenerate

    reg [1:0] cap_s = 2'b00, covf_s = 2'b00, uovf_s = 2'b00;
    always @(posedge ui_clk) begin
        cap_s  <= {cap_s[0], cap};
        covf_s <= {covf_s[0], cfifo_ovf};
        uovf_s <= {uovf_s[0], ufifo_ovf};
    end
    //---------------------------------------------------------------------
    // STATUS OVER THE Pt's OWN USB (COM6, 1 Mbaud).
    //
    // The FT601 read blocked forever with zero bytes, and with no UART in this
    // build the only evidence was eight LEDs I cannot see from here. The FSM has
    // several states that stall silently and look identical from outside:
    // W_LO/W_HI wait on the camera FIFO, so ONE dropped word (a cfifo overflow)
    // means the frame never completes and P_HDR is never reached -- no bytes,
    // forever. This prints the state instead of guessing.
    //
    // ftw counts words actually handed to the FT601, ftc free-runs on ft_clk:
    // if ftc is stuck the FT601 is not clocking us at all, which is a different
    // fault entirely from "clocking but never accepting".
    reg [23:0] ftw = 24'd0, ftc = 24'd0;
    always @(posedge ft_clk) begin
        ftc <= ftc + 24'd1;
        if (ft_adv) ftw <= ftw + 24'd1;
    end
    reg [7:0] ftw_s1=0, ftw_s2=0, ftc_s1=0, ftc_s2=0;
    reg [1:0] txe_s = 2'b00;
    reg [27:0] wtot_s1 = 28'd0, wtot_s2 = 28'd0;
    reg [19:0] wmin_s1 = 20'd0, wmin_s2 = 20'd0;
    reg [19:0] wmax_s1 = 20'd0, wmax_s2 = 20'd0;
    always @(posedge ui_clk) begin
        ftw_s1 <= ftw[23:16]; ftw_s2 <= ftw_s1;
        ftc_s1 <= ftc[23:16]; ftc_s2 <= ftc_s1;
        txe_s  <= {txe_s[0], ft_txe};
        // each latched value is stable for a whole window (~200 ms), so a
        // plain 2FF sync is safe on all of them -- no gray coding needed
        wtot_s1 <= wtot_l; wtot_s2 <= wtot_s1;
        wmin_s1 <= wmin_l; wmin_s2 <= wmin_s1;
        wmax_s1 <= wmax_l; wmax_s2 <= wmax_s1;
    end

    // 4 + 8 + 28 + 20 + 20 + 16 + 16 = 112 bits = 28 hex chars.
    // expo_cur and cmd_count are the acknowledgement path: the host sees its
    // command take effect in the telemetry it is already reading, so the control
    // channel needs no reply direction of its own.
    reg [15:0] ccnt_s1 = 16'd0, ccnt_s2 = 16'd0;
    reg [3:0]  rxd_s1 = 4'd0, rxd_s2 = 4'd0;
    reg [15:0] ldrop_s1 = 16'd0, ldrop_s2 = 16'd0;
    always @(posedge ui_clk) begin
        ccnt_s1 <= cmd_count; ccnt_s2 <= ccnt_s1;
        rxd_s1  <= rx_dbg;    rxd_s2  <= rxd_s1;
        ldrop_s1 <= ldrop;    ldrop_s2 <= ldrop_s1;
    end
    // + rx_dbg{RXF#-ever-low, state} + frames-per-scan = 128 bits, 32 chars
    reg [5:0] nfr_s1 = 6'd0, nfr_s2 = 6'd0;
    always @(posedge ui_clk) begin nfr_s1 <= nframes_r; nfr_s2 <= nfr_s1; end
    wire [127:0] stat = { stw[2:0], (str != R_IDLE), init_calib_complete, aligned, streaming, cap_s[1],
                          covf_s[1], uovf_s[1], ufifo_empty, txe_s[1],
                          wtot_s2, wmin_s2, wmax_s2, expo_cur, ccnt_s2,
                          rxd_s2, nfr_s2, cal_retry,
                          (LIVE != 0), (CONCURRENT != 0) };

    // ---- M4: parent-visible status snapshot -----------------------------
    //
    // Latched on the SAME ~10 Hz tick as the UART's own snapshot, so it is stable
    // for ~200 ms, and published with a TOGGLE rather than raw. A 64-bit vector
    // crossing clock domains bit-by-bit can TEAR -- some bits from the old value,
    // some from the new -- and a torn status word is worse than none, because it
    // reads as a plausible state that never existed. The toggle lets the parent
    // capture only when the whole word is known stable.
    reg [63:0] cstat_r   = 64'd0;
    reg        cstat_tog = 1'b0;
    assign cam_stat_o     = cstat_r;
    assign cam_stat_tog_o = cstat_tog;

    reg [127:0] shold = 128'd0;
    reg [4:0]  nib   = 5'd0;
    reg [23:0] utick = 24'd0;
    reg [7:0]  ubyte = 8'd0;
    reg        usend = 1'b0;
    reg [1:0]  ust   = 2'd0;
    wire       ubusy;
    wire [3:0] n = shold[127 - nib*4 -: 4];

    always @(posedge ui_clk) begin
        usend <= 1'b0;
        if (ui_rst) begin ust <= 2'd0; utick <= 24'd0; nib <= 5'd0; end
        else case (ust)
        2'd0: begin
            utick <= utick + 24'd1;
            if (utick == 24'd10_000_000) begin           // ~10 Hz
                utick <= 24'd0; shold <= stat; nib <= 5'd0; ust <= 2'd1;
                // byte 0 (reg 0x3A) is the "is it alive" byte, byte 1 the
                // "is it healthy" byte -- the two a human reads first.
                cstat_r <= { expo_cur,
                             wmin_s2[19:4],
                             ldrop_s2,
                             covf_s[1], uovf_s[1], ufifo_empty, txe_s[1], 4'b0,
                             stw[2:0], (str != R_IDLE),
                             init_calib_complete, aligned, streaming, cap_s[1] };
                cstat_tog <= ~cstat_tog;
            end
        end
        2'd1: if (!ubusy && !usend) begin
            ubyte <= (n < 4'd10) ? (8'd48 + {4'd0,n}) : (8'd55 + {4'd0,n});
            usend <= 1'b1;
            if (nib == 5'd31) ust <= 2'd2; else nib <= nib + 5'd1;
        end
        2'd2: if (!ubusy && !usend) begin ubyte <= 8'h0D; usend <= 1'b1; ust <= 2'd3; end
        2'd3: if (!ubusy && !usend) begin ubyte <= 8'h0A; usend <= 1'b1; ust <= 2'd0; end
        endcase
    end

    uart_tx #(.CLK_HZ(100_000_000), .BAUD(1_000_000)) u_uart (
        .clk(ui_clk), .rst(ui_rst), .data(ubyte), .send(usend),
        .tx(usb_tx), .busy(ubusy)
    );

    reg [26:0] hb = 27'd0;
    always @(posedge ui_clk) begin
        hb <= hb + 27'd1;
        led[7] <= hb[26];
        led[6] <= init_calib_complete;
        led[5] <= aligned;
        led[4] <= cap_s[1];
        led[3] <= (str != R_IDLE);        // streaming to USB
        led[2] <= streaming;
        led[1] <= covf_s[1];              // sensor->DDR FIFO overflowed
        led[0] <= uovf_s[1];              // DDR->USB FIFO overflowed
    end
endmodule
