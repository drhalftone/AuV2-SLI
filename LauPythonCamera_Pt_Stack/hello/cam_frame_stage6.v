`timescale 1ns/1ps
//=============================================================================
// cam_frame_stage6 - capture a whole-sensor image into block RAM and stream it
// to the PC. The first actual PICTURE off this camera.
//
// WHY 1280 x 64 AND NOT 1280 x 1024. Two limits, and the second is the binding
// one:
//
//   1. A full frame at 8 bits is 1,310,720 bytes against ~607 KB of block RAM
//      on the XC7A100T. It needs DDR3 + MIG, which is milestone #16.
//
//   2. THE REAL LIMIT: wordclk comes from a BUFR, a REGIONAL clock buffer, so
//      every BRAM it clocks must sit in that one clock region. A 1280 x 256
//      buffer (2.62 Mbit, ~72 BRAMs) does not fit and the placer rejects it
//      outright: "RAMBs driven by regional clock buffers need to be in the same
//      clock region as the buffers".
//
// 1280 x 64 is 81,920 bytes = 512 Kbit, about 15 BRAMs -- comfortably inside one
// region. Every SIXTEENTH line, so it still spans the full sensor height
// (64 x 16 = 1024) and the host stretches it back when rendering.
//
// To go bigger, the write side has to leave the BUFR domain: an async FIFO into
// the global 100 MHz clock, which is exactly the CDC bridge CAMERA_RTL_PLAN.md's
// streaming architecture specifies between cam_sync_decode and the MIG. That is
// the right next step for real frames, and it is why that FIFO exists in the
// plan rather than being an afterthought.
//
// WHY LINE COUNTING AND NOT frame_start. cam_sync_decode never decodes FE
// (0x32A), so frame_start fires exactly once -- see stage 5. line_start is
// perfectly healthy and fires every line, so capture is driven off that
// instead: count lines from the first one after alignment, store every fourth,
// stop after 1024. That deliberately does not depend on the broken frame
// boundary. The captured band may straddle a real frame edge; for a first
// picture that is a wrap in the image, not a failure.
//
// 1 Mbaud, not 115200. uart_tx divides CLK_HZ by BAUD, and 100 MHz / 1 MHz = 100
// exactly, so the rate is precise. 327,680 bytes at 115200 would take 28 s; at
// 1 Mbaud it is 3.3 s. The FT2232 handles it easily.
//
// WIRE FORMAT -- raw binary, length known in advance, so no escaping:
//     "FRAMESTART\n"  then exactly 327,680 raw bytes  then "\nFRAMEEND\n"
// The host counts bytes rather than looking for a terminator, so payload bytes
// that happen to spell the marker are harmless.
//
// After each dump the capture re-arms, so pointing the camera at something new
// and waiting a few seconds gives a fresh picture.
//
// LEDs
//   led[7] heartbeat   led[6] aligned   led[5] scan done
//   led[4] capturing   led[3] capture complete   led[2] streaming
//=============================================================================
module cam_frame_stage6 #(
    parameter integer CLK_HZ  = 100_000_000,
    parameter integer BAUD    =   1_000_000,   // 100 MHz / 1 MHz = 100 exactly
    parameter integer NCOL    = 1280,          // pixels per line
    parameter integer NROW    = 256,           // stored lines (every 4th)
    parameter integer WPL     = NCOL/8         // 64-bit words per line = 160
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
    localparam integer NWORDS = NROW * WPL;          // 256 * 160 = 40960
    localparam integer NBYTES = NCOL * NROW;         // 327680

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

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

    //--------------------------- boot to 41, stream only after the eye is set
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    // TRIGGERED = 1: the sensor now waits for us instead of free-running.
    // cam_trigger is deliberately left UNCONNECTED here -- cam_boot_stage1 ties
    // its copy to 3'b000, and this design has to drive trigger0 itself.
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(45), .TRIGGERED(1), .TESTPAT(1),
                       .EXPOSURE(16'h0FA0)) u_boot (   // 4000. 10000 clipped 53 %; 1250 broke capture entirely
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(),
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

    // start streaming once the taps are parked and all lanes have aligned
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

    //------------------------------------------------------ the frame buffer
    // 40960 x 64 bits = 2.62 Mbit. One kernel per write, exactly as
    // cam_line_buf does -- eight pixels to one address, one write port, which
    // is what lets it infer as BRAM at all (a per-pixel array needs eight write
    // ports and crashed synthesis; see cam_line_buf's header).
    wire [63:0] kword = { kp7[9:2], kp6[9:2], kp5[9:2], kp4[9:2],
                          kp3[9:2], kp2[9:2], kp1[9:2], kp0[9:2] };

    //--- ONE LINE in the wordclk (BUFR) domain: 160 x 64 bits, about one BRAM.
    // This is the whole trick. The BUFR is REGIONAL, so anything it clocks must
    // live in its clock region, and bank 13's region has nowhere near enough
    // BRAM for a frame. But it only needs to hold ONE LINE -- because we keep
    // one line in four, there is a three-line gap (~19 us) in which to copy it
    // out at leisure into a frame buffer clocked by the GLOBAL 100 MHz clock,
    // which can use BRAM anywhere on the die.
    //
    // That is the async bridge CAMERA_RTL_PLAN.md's streaming architecture puts
    // between cam_sync_decode and the MIG, in its simplest possible form: the
    // producer finishes a line entirely before the consumer starts reading it,
    // so a toggle handshake is sufficient and no gray-coded FIFO is needed.
    // DOUBLE BUFFERED, 2 x 160 words indexed {bank, word}.
    //
    // Single-buffered, the copy of the finished line raced the incoming one on
    // the same memory. The copy reads at 10 ns/word (100 MHz) while the writer
    // produces a kernel every 27.8 ns (2 wordclks), so the reader overtakes and
    // stays ahead -- but only from word 1 onward. At word 0 it loses: the copy
    // needs ~3 clk (2 for the tog_s CDC, 1 for the read) = ~30 ns, while the new
    // line writes lbuf[0] just 2 wordclks = 27.8 ns after the line start. Two
    // nanoseconds, every line, so columns 0-7 of every row came from the WRONG
    // line. Widening the buffer removes the race instead of trying to win it.
    (* ram_style = "block" *) reg [63:0] lbuf [0:511];

    reg [10:0] lcnt     = 11'd0;    // lines seen since capture armed (0..1023)
    reg [8:0]  cur_slot = 9'd0;     // row this line will occupy, if kept
    reg        keep_cur = 1'b0;     // this line is one of the kept ones
    reg        cap      = 1'b0;
    reg        cap_dn   = 1'b0;
    reg        lb_tog   = 1'b0;     // flips when a kept line is complete
    reg [8:0]  lb_slot  = 9'd0;     // its row; stable while lb_tog is stable
    reg        arm      = 1'b0;
    reg        dump_done = 1'b0;
    reg [1:0]  arm_s    = 2'b00;

    wire [10:0] lnext = lcnt + 11'd1;

    always @(posedge wordclk) begin
        arm_s <= {arm_s[0], arm};

        if (wc_rst) begin
            lcnt <= 11'd0; cur_slot <= 9'd0; keep_cur <= 1'b0;
            cap  <= 1'b0;  cap_dn   <= 1'b0; lb_tog   <= 1'b0;
        end else begin
            if (arm_s[1] && !cap && cap_dn) cap_dn <= 1'b0;

            // START ON frame_start, NOT on an arbitrary line.
            //
            // The first version of this armed on "the first line_start after
            // alignment", because at the time cam_sync_decode never produced a
            // usable frame_start -- FE was being missed. With FE fixed there IS
            // a real frame origin, and using it is what removes the arbitrary
            // vertical rotation in the captured image. Counting lines from
            // nowhere in particular is what put the bottom of the frame at the
            // top.
            //
            // lcnt starts at -1 (0x7FF) so the FIRST line_start after
            // frame_start advances it to 0 -- row 0 is genuinely the frame's
            // first image line, not its second.
            // A frame_start while ALREADY capturing means the frame ended --
            // finish, do not restart. The first version restarted, so if a frame
            // delivers fewer than 1024 line_starts the capture was retriggered
            // forever and never completed: no dump at all. Capture now spans
            // exactly one frame_start to the next, whatever its line count.
            //
            // END ON frame_end (FE), NOT on the next frame_start.
            //
            // Free-running, "the next FS" was a serviceable stand-in for "this
            // frame is over" -- but it ends the capture one whole frame late,
            // and anything still in flight when it finally arrives is lost. That
            // is the most likely reason the last 6 row-slots (~24 image lines)
            // came back empty. Triggered, it is worse than late: it never
            // arrives at all, because no further frame exists until we ask for
            // one, so waiting for FS would hang the capture forever.
            //
            // FE is the sensor saying the frame is done. Hand off the last line
            // and stop there.
            if (frame_end && cap) begin
                if (keep_cur) begin
                    lb_slot <= cur_slot;
                    lb_tog  <= ~lb_tog;
                end
                cap <= 1'b0; cap_dn <= 1'b1; keep_cur <= 1'b0;
            end else if (frame_start) begin
                if (!cap && !cap_dn) begin
                    cap      <= 1'b1;
                    // FS *IS* line 0's start -- measured, W0 == WL == 319, so the
                    // FS word is the first data word of row 0 and cam_sync_decode
                    // now pulses frame_start and line_start together on it. lcnt
                    // therefore starts at 0, not -1, and row 0 is kept immediately
                    // (0 mod 4 == 0). Starting at -1 and waiting for the next
                    // line_start would consume row 1's LS and put row 1 in slot 0.
                    lcnt     <= 11'd0;
                    keep_cur <= 1'b1;
                    cur_slot <= 9'd0;
                end
            end else if (line_start && cap) begin
                // the line that just ENDED is complete: hand it over
                if (keep_cur) begin
                    lb_slot <= cur_slot;
                    lb_tog  <= ~lb_tog;
                end
                if (lcnt == 11'd1023) begin
                    cap <= 1'b0; cap_dn <= 1'b1; keep_cur <= 1'b0;
                end else begin
                    lcnt     <= lnext;
                    keep_cur <= (lnext[1:0] == 2'b00);     // every 4th
                    cur_slot <= lnext[10:2];
                end
            end

            if (cap && keep_cur && kvalid) lbuf[{lb_tog, kbase[10:3]}] <= kword;
        end
    end

    //--- the frame buffer, in the GLOBAL clock domain (BRAM anywhere on the die)
    //
    // Pre-fill is 0xA5, not 0x00. Filling with zero cannot distinguish a pixel
    // that was never written from a pixel the sensor genuinely read as black --
    // both come back 0. 0xA5 is outside the observed image range, so any 0xA5 in
    // the capture is unambiguously a MISSING WRITE.
    //
    // ONE write port, address and data MUXED between "clear" and "copy". Writing
    // fmem from two places -- even in the same always block, even mutually
    // exclusive in time -- gives it two write addresses, and Vivado will not
    // infer a BRAM from that: it tried to build 110,080 distributed-RAM cells
    // against 19,000 available. A single port with a muxed address infers
    // cleanly.
    (* ram_style = "block" *) reg [63:0] fmem [0:NWORDS-1];

    reg [1:0]  tog_s   = 2'b00;
    reg        copying = 1'b0;
    reg [7:0]  ci      = 8'd0;      // read index into lbuf
    reg [7:0]  ci_d    = 8'd0;      // index whose data is in lb_rd now
    reg        wr_v    = 1'b0;
    reg [8:0]  c_slot  = 9'd0;
    reg        c_bank  = 1'b0;   // which lbuf bank the finished line is in
    reg [63:0] lb_rd;

    // c_slot*160 = c_slot*128 + c_slot*32, as shifts: no multiplier inferred.
    wire [15:0] fwaddr = {c_slot, 7'd0} + {c_slot, 5'd0} + {8'd0, ci_d};

    //------------------------------------------------------------------------
    // ZERO THE BUFFER BETWEEN CAPTURES.
    //
    // Without this, a row never written retains bytes from a PREVIOUS capture,
    // which had a different frame alignment -- so unwritten rows masquerade as
    // horizontally-displaced image data. That is almost certainly the band
    // across the top of the last capture, and it is the same trap as reading a
    // floating pin: stale data looks like a confident measurement.
    //
    // Cleared, an unwritten row is unambiguously BLACK, so the picture states
    // directly which rows are missing and how many.
    //
    // ONE always block owns fmem -- clear and copy are two branches of the same
    // writer, never two drivers. They cannot collide anyway: clearing runs after
    // a dump completes and finishes before the next capture is allowed to arm.
    //------------------------------------------------------------------------
    reg        clearing   = 1'b1;      // clear once at power-up as well
    reg        need_clear = 1'b0;
    reg [15:0] clr_a      = 16'd0;

    wire [15:0] fwe_a = clearing ? clr_a : fwaddr;
    wire [63:0] fwe_d = clearing ? 64'hA5A5A5A5A5A5A5A5 : lb_rd;   // sentinel, NOT zero
    wire        fwe   = clearing ? 1'b1
                                 : (wr_v && (ci_d < WPL[7:0]) && (c_slot < NROW[8:0]));
    always @(posedge clk) if (fwe) fmem[fwe_a] <= fwe_d;

    always @(posedge clk) begin
        arm   <= 1'b0;
        if (dump_done) need_clear <= 1'b1;
        tog_s <= {tog_s[0], lb_tog};
        lb_rd <= lbuf[{c_bank, ci}];   // 1-cycle read latency
        ci_d  <= ci;
        wr_v  <= copying;

        if (rst) begin
            copying <= 1'b0; ci <= 8'd0;
            clearing <= 1'b1; clr_a <= 16'd0; need_clear <= 1'b0;
        end else if (clearing) begin
            if (clr_a == NWORDS[15:0] - 16'd1) begin
                clearing <= 1'b0; clr_a <= 16'd0;
                arm      <= 1'b1;      // NOW the next capture may arm
            end else clr_a <= clr_a + 16'd1;
        end else begin
            if (need_clear && !copying) begin
                need_clear <= 1'b0;
                clearing   <= 1'b1;
                clr_a      <= 16'd0;
            end else if (!copying) begin
                if (tog_s[1] != tog_s[0]) begin      // a kept line is ready
                    copying <= 1'b1;
                    ci      <= 8'd0;
                    c_slot  <= lb_slot;
                    c_bank  <= tog_s[1];             // the bank it was written to
                end
            end else begin
                if (ci == WPL[7:0] + 8'd2) copying <= 1'b0;  // +2 flushes the pipe
                else ci <= ci + 8'd1;
            end
        end
    end

    //------------------------------------------------------- read + stream
    reg [18:0] baddr = 19'd0;        // byte address 0..327679
    wire [15:0] rword_a = baddr[18:3];
    reg  [63:0] rword;
    always @(posedge clk) rword <= fmem[rword_a];
    wire [7:0] rbyte = rword[ {baddr[2:0], 3'd0} +: 8 ];

    reg [1:0] dn_s    = 2'b00;
    reg       dn_prev = 1'b0;
    always @(posedge clk) begin
        dn_s    <= {dn_s[0], cap_dn};
        dn_prev <= dn_s[1];        // for the rising-edge dump trigger below
    end

    // Counters + a periodic status line. Without this, "no dump appeared" is
    // undiagnosable without another build-and-load cycle, and loading this
    // board is the slowest step in the loop.
    reg [15:0] fr_cnt = 16'd0, ln_cnt = 16'd0;
    always @(posedge wordclk) begin
        if (wc_rst) begin fr_cnt <= 16'd0; ln_cnt <= 16'd0; end
        else begin
            if (frame_start) fr_cnt <= fr_cnt + 16'd1;
            if (line_start)  ln_cnt <= ln_cnt + 16'd1;
        end
    end
    reg [15:0] fr_s, ln_s;
    reg [1:0]  cap_ss;
    always @(posedge clk) begin
        fr_s <= fr_cnt; ln_s <= ln_cnt; cap_ss <= {cap_ss[0], cap};
    end
    reg [27:0] stm = 28'd0;
    reg        st_go;
    always @(posedge clk) begin
        st_go <= 1'b0;
        if (rst) stm <= 28'd0;
        else if (stm == (CLK_HZ*2) - 1) begin stm <= 28'd0; st_go <= 1'b1; end
        else stm <= stm + 28'd1;
    end

    reg [7:0]  data_q = 8'h00;
    reg        send = 1'b0;
    wire       tx_busy;
    wire       tx_free = !tx_busy && !send;

    localparam [3:0] T_IDLE=0, T_HDR=1, T_SETA=2, T_LAT=3, T_BYTE=4,
                     T_FTR=5, T_DONE=6, T_ST=7;
    reg [3:0] ts = T_IDLE;
    // Sticky dump request. The rising edge of cap_dn is one cycle wide, and
    // T_IDLE is not always where the FSM is sitting -- the periodic status line
    // (T_ST) borrows it. A bare edge test therefore drops the request and no
    // dump ever happens. Latch it instead and consume it when T_IDLE is reached.
    reg       dump_req = 1'b0;
    reg [4:0] hi = 5'd0;

    // "FRAMESTART\n" = 11 chars, "\nFRAMEEND\n" = 10 chars
    reg [7:0] hdr_ch, ftr_ch;
    always @(*) begin
        case (hi)
        4'd0:hdr_ch="F"; 4'd1:hdr_ch="R"; 4'd2:hdr_ch="A"; 4'd3:hdr_ch="M";
        4'd4:hdr_ch="E"; 4'd5:hdr_ch="S"; 4'd6:hdr_ch="T"; 4'd7:hdr_ch="A";
        4'd8:hdr_ch="R"; 4'd9:hdr_ch="T"; default:hdr_ch=8'h0A;
        endcase
        case (hi)
        4'd0:ftr_ch=8'h0A; 4'd1:ftr_ch="F"; 4'd2:ftr_ch="R"; 4'd3:ftr_ch="A";
        4'd4:ftr_ch="M"; 4'd5:ftr_ch="E"; 4'd6:ftr_ch="E"; 4'd7:ftr_ch="N";
        4'd8:ftr_ch="D"; default:ftr_ch=8'h0A;
        endcase
    end

    reg [7:0] st_ch;
    always @(*) begin
        case (hi)
        5'd0 : st_ch="S"; 5'd1 : st_ch="T"; 5'd2 : st_ch=" ";
        5'd3 : st_ch="a"; 5'd4 : st_ch= aligned   ? "1":"0"; 5'd5 : st_ch=" ";
        5'd6 : st_ch="s"; 5'd7 : st_ch= scan_done ? "1":"0"; 5'd8 : st_ch=" ";
        5'd9 : st_ch="t"; 5'd10: st_ch= streaming ? "1":"0"; 5'd11: st_ch=" ";
        5'd12: st_ch="c"; 5'd13: st_ch= cap_ss[1] ? "1":"0"; 5'd14: st_ch=" ";
        5'd15: st_ch="d"; 5'd16: st_ch= dn_s[1]   ? "1":"0"; 5'd17: st_ch=" ";
        5'd18: st_ch="f";
        5'd19: st_ch=hexd(fr_s[15:12]); 5'd20: st_ch=hexd(fr_s[11:8]);
        5'd21: st_ch=hexd(fr_s[7:4]);   5'd22: st_ch=hexd(fr_s[3:0]);
        5'd23: st_ch=" "; 5'd24: st_ch="l";
        5'd25: st_ch=hexd(ln_s[15:12]); 5'd26: st_ch=hexd(ln_s[11:8]);
        5'd27: st_ch=hexd(ln_s[7:4]);   5'd28: st_ch=hexd(ln_s[3:0]);
        5'd29: st_ch=8'h0D; default: st_ch=8'h0A;
        endcase
    end

    always @(posedge clk) begin
        send      <= 1'b0;
        dump_done <= 1'b0;

        if (rst) begin
            ts <= T_IDLE; hi <= 5'd0; baddr <= 19'd0; dump_req <= 1'b0;
        end else begin
            // one request per completed capture
            if (dn_s[1] && !dn_prev) dump_req <= 1'b1;
            case (ts)
            // RISING EDGE, not level.
            //
            // This was `if (dn_s[1])`. cap_dn is not cleared until `arm`, and arm
            // only fires after the post-dump buffer clear completes -- so at
            // T_DONE cap_dn is still high and the dump restarted IMMEDIATELY,
            // then streamed for 3.3 s while the clear (410 us) and the whole next
            // capture wrote fmem underneath it.
            //
            // That is the moving corruption we chased for hours. The dumped frame
            // began with however much of the buffer the clear and capture had
            // reached by then -- so the garbage run had a different length every
            // time (107, 250, 627, 953 pixels observed), and its boundaries were
            // not 8-aligned because individual 64-bit words were read WHILE being
            // written, giving half-old, half-new words.
            //
            // One dump per capture: wait for cap_dn to go low and rise again.
            T_IDLE: if (dump_req) begin dump_req <= 1'b0; hi <= 5'd0; ts <= T_HDR; end
                    else if (st_go) begin hi <= 5'd0; ts <= T_ST; end

            T_HDR: if (tx_free) begin
                data_q <= hdr_ch; send <= 1'b1;
                if (hi == 5'd10) begin hi <= 5'd0; baddr <= 19'd0; ts <= T_SETA; end
                else hi <= hi + 5'd1;
            end

            T_SETA: ts <= T_LAT;        // rword_a already reflects baddr
            T_LAT:  ts <= T_BYTE;       // rword valid next clock

            T_BYTE: if (tx_free) begin
                data_q <= rbyte; send <= 1'b1;
                if (baddr == NBYTES[18:0] - 19'd1) begin hi <= 4'd0; ts <= T_FTR; end
                else begin baddr <= baddr + 19'd1; ts <= T_SETA; end
            end

            T_FTR: if (tx_free) begin
                data_q <= ftr_ch; send <= 1'b1;
                if (hi == 5'd9) begin hi <= 5'd0; ts <= T_DONE; end
                else hi <= hi + 5'd1;
            end

            // Signal completion. Re-arming happens only AFTER the buffer has been
            // cleared, so a new capture cannot be wiped mid-flight.
            T_DONE: begin dump_done <= 1'b1; ts <= T_IDLE; end

            // 31-char status line: ST a1 s1 t1 c0 d0 f1234 l5678
            T_ST: if (tx_free) begin
                data_q <= st_ch; send <= 1'b1;
                if (hi == 5'd30) begin hi <= 5'd0; ts <= T_IDLE; end
                else hi <= hi + 5'd1;
            end

            default: ts <= T_IDLE;
            endcase
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

    reg [1:0] cap_s;
    always @(posedge clk) cap_s <= {cap_s[0], cap};

    //------------------------------------------------------------------------
    // TRIGGER GENERATOR -- one rising edge on trigger0, one frame.
    //
    // Fires only when the whole pipeline is genuinely ready for a frame:
    //
    //   streaming   the sequencer is enabled (before this the sensor ignores
    //               trigger0 entirely, so an early pulse is simply lost)
    //   !clearing   the frame buffer pre-fill has finished
    //   !dn_s[1]    the previous capture has been dumped and re-armed
    //   !cap_s[1]   a capture is not already in progress
    //   ts==T_IDLE  the UART is not in the middle of a 3.3 s dump
    //
    // Tying it to readiness rather than to the `arm` pulse matters. arm fires
    // when the power-up clear completes, which is LONG before the eye scan and
    // alignment finish, so a trigger issued there would be swallowed by a
    // sensor whose sequencer is still disabled -- and since the next arm only
    // ever follows a dump, and a dump only follows a capture, that one lost
    // pulse would deadlock the design with no frame ever requested.
    //
    // tw free-runs while ready and wraps at 2^24 = 168 ms, so the pulse repeats
    // on its own if a frame never materialises: a missed trigger costs a sixth
    // of a second, not a hung board. A capture completes in ~10 ms, well inside
    // one wrap, so the retry never interrupts a frame in progress.
    //
    // The pulse is 2 us high, 10 us after becoming ready. Only the RISING edge
    // matters to the sensor; the falling edge has no effect (datasheet p14).
    //------------------------------------------------------------------------
    reg [23:0] tw      = 24'd0;
    reg        trig0_r = 1'b0;
    wire       trig_ready = streaming && !clearing && !dn_s[1] && !cap_s[1]
                            && (ts == T_IDLE);

    always @(posedge clk) begin
        if (rst || !trig_ready) begin
            tw      <= 24'd0;
            trig0_r <= 1'b0;
        end else begin
            tw      <= tw + 24'd1;
            trig0_r <= (tw >= 24'd1_000) && (tw < 24'd1_200);
        end
    end

    // trigger1/2 stay low -- only trigger0 is the exposure/readout sync input.
    assign cam_trigger = {2'b00, trig0_r};

    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;
    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= aligned;
        led[5]   <= scan_done;
        led[4]   <= cap_s[1];
        led[3]   <= dn_s[1];
        led[2]   <= streaming;
        led[1:0] <= 2'd0;
    end

endmodule
