`timescale 1ns/1ps
//==============================================================================
// ft_video_top.v -- FT601 USB-3 video throughput test, top level.
//
//   Alchitry Pt V2 (XC7A100T-2FGG484I)  +  Ft+ (FT601Q, bottom)
//
//   sli_frame_gen  --stream-->  ft601_sync_tx  --245 sync FIFO-->  FT601 -> USB3 -> PC
//
// Generates SLI cosine fringes at 1280x1024 (PYTHON-1300 raster) and streams them
// out the FT601 as fast as USB back-pressure allows. The host counts frames to
// report sustained MB/s and frames/second. Everything runs on ft_clk (100 MHz,
// sourced by the FT601) -- a single clock domain, so there is no CDC FIFO: the
// generator advances in lockstep with words the FT601 actually accepts.
//
// Port names and I/O standards come verbatim from Alchitry's own constraint files
// (pt_base.xdc + pt_ft_plus_bottom.xdc), the same ones the stack I/O check placed.
//==============================================================================
module ft_video_top #(
    // PIX_FMT selects the stream source. Set it at build time:
    //   vivado -mode batch -source run_ftvideo.tcl -tclargs 1   (or 3)
    //
    //   1 = sli_frame_gen  -- 8-bit mono SLI cosine fringes, 4 px/word.
    //       1,310,752 B/frame. A real *picture*; use it when you want to SEE the
    //       link work. Costs ~800 LUTs of cosine ROM.
    //   3 = raw10_test_gen -- packed 10-bit (MIPI RAW10), 16 px / 5 words.
    //       1,638,432 B/frame. A pixel-index counter, so every data bit toggles
    //       at full rate: a much harder signal-integrity test, and far cheaper.
    //       Use it to measure the link at real sensor density.
    //
    // Both saturate the FT601 (one word per clock), so the MB/s figure is the
    // same either way -- only bytes/frame, and therefore FPS, differ.
    parameter integer PIX_FMT = 1
)(
    // ---- Pt V2 base (pt_base.xdc) ----
    input  wire        clk,        // 100 MHz board oscillator (unused datapath; see below)
    input  wire        rst_n,      // active-low reset button
    output wire [7:0]  led,
    input  wire        usb_rx,
    output wire        usb_tx,

    // ---- Ft+ : FT601Q 32-bit 245-sync FIFO (pt_ft_plus_bottom.xdc) ----
    input  wire        ft_clk,     // 100 MHz FIFO clock, driven BY the FT601
    inout  wire [31:0] ft_data,
    inout  wire [3:0]  ft_be,
    input  wire        ft_rxf,     // RXF# (unused: TX-only test)
    input  wire        ft_txe,     // TXE#
    output wire        ft_oe,      // OE#
    output wire        ft_rd,      // RD#
    output wire        ft_wr,      // WR#
    output wire        ft_wakeup,  // WAKEUP#
    output wire        ft_reset    // RESET_N to the FT601
);
    // usb_rx is unused; usb_tx carries the debug telemetry (see debug_status below).

    // --- reset synchronizers ---
    // clk (onboard oscillator) domain reset -- for the always-alive telemetry path
    reg [2:0] rstc_sync = 3'b111;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rstc_sync <= 3'b111;
        else        rstc_sync <= {rstc_sync[1:0], 1'b0};
    end
    wire rst_clk = rstc_sync[2];

    // ft_clk domain reset (active-high `rst`)
    reg [2:0] rst_sync = 3'b111;
    always @(posedge ft_clk or negedge rst_n) begin
        if (!rst_n) rst_sync <= 3'b111;
        else        rst_sync <= {rst_sync[1:0], 1'b0};
    end
    wire rst = rst_sync[2];

    // FT601 static controls: keep it awake and out of reset while the board runs.
    assign ft_wakeup = 1'b1;         // WAKEUP# deasserted
    assign ft_reset  = rst_n;        // RESET_N: released once the board is out of reset

    // --- frame source ---
    wire [31:0] gen_word;
    wire        gen_valid;
    wire        gen_adv;
    wire [31:0] frame_index;

    // Both sources share the same FWFT contract, so the master is unaware which
    // one is wired in. Only the unselected generator's logic is elaborated away.
    generate
        if (PIX_FMT == 3) begin : g_raw10
            raw10_test_gen u_gen (
                .clk         (ft_clk),
                .rst         (rst),
                .adv         (gen_adv),
                .word        (gen_word),
                .valid       (gen_valid),
                .frame_index (frame_index)
            );
        end else begin : g_sli8
            sli_frame_gen u_gen (
                .clk         (ft_clk),
                .rst         (rst),
                .adv         (gen_adv),
                .word        (gen_word),
                .valid       (gen_valid),
                .frame_index (frame_index)
            );
        end
    endgenerate

    // --- FT601 245-sync-FIFO write master ---
    wire [31:0] ft_dout;
    wire [3:0]  ft_beout;
    wire        bus_oe;

    ft601_sync_tx u_tx (
        .clk      (ft_clk),
        .rst      (rst),
        .ft_txe   (ft_txe),
        .ft_wr    (ft_wr),
        .ft_oe    (ft_oe),
        .ft_rd    (ft_rd),
        .ft_dout  (ft_dout),
        .ft_beout (ft_beout),
        .bus_oe   (bus_oe),
        .s_word   (gen_word),
        .s_valid  (gen_valid),
        .s_adv    (gen_adv)
    );

    // TX-only: the FPGA drives the shared FT601 bus (OE# is held high inside the
    // master, so the FT601 never contends). bus_oe is a constant 1, so the tool
    // collapses the tristate to a plain OBUF -- which is what lets DATA/BE pack
    // into IOB output flops. Do NOT make bus_oe dynamic without re-checking the
    // IOB packing report; losing those flops reintroduces the setup-window bug
    // documented in ft601_sync_tx.v.
    assign ft_data = bus_oe ? ft_dout  : 32'bz;
    assign ft_be   = bus_oe ? ft_beout : 4'bz;

    // -------------------------------------------------------------------------
    // Diagnostic LED panel. Deliberately split across BOTH clock domains so the
    // LEDs localize a dead link instead of just going dark together:
    //   led[0] heartbeat off the Pt's OWN 100 MHz oscillator (clk) -> proves the
    //          bitstream is configured and running, independent of the FT601.
    //   led[2] heartbeat off ft_clk -> proves the FT601 is actually clocking us.
    //   led[3] ~TXE# -> the FT601 is granting writes (buffer has room).
    //   led[4] toggles every 2^23 accepted words -> a "data to PC" blink whose
    //          RATE tracks throughput (fast blink = fast stream, frozen = no data).
    //   led[7:5] coarse frame progress.
    // If only led[0] blinks, ft_clk is dead; if led[2] blinks but led[3] never
    // lights, TXE# is stuck high (host not draining / pipe not primed).
    // -------------------------------------------------------------------------
    reg [26:0] hb_board = 27'd0;                       // onboard-clock heartbeat
    always @(posedge clk) hb_board <= hb_board + 27'd1;

    reg [26:0] hb_ft = 27'd0;                          // ft_clk heartbeat
    always @(posedge ft_clk) hb_ft <= hb_ft + 27'd1;

    reg        ft_txe_q = 1'b1;                        // registered TXE#
    reg [31:0] word_ctr = 32'd0;                       // accepted-word counter
    always @(posedge ft_clk) begin
        ft_txe_q <= ft_txe;
        if (gen_adv) word_ctr <= word_ctr + 32'd1;     // one per word written to FT601
    end

    assign led = { frame_index[8:6],   // [7:5] coarse frame progress
                   word_ctr[23],       // [4]   DATA -> PC (blink rate ~ throughput)
                   ~ft_txe_q,          // [3]   FT601 accepting writes (TXE# low)
                   hb_ft[25],          // [2]   FT601 FIFO clock present
                   1'b0,               // [1]   (off)
                   hb_board[25] };     // [0]   FPGA alive (onboard oscillator)

    // -------------------------------------------------------------------------
    // Telemetry over the Pt's USB serial (COM). Runs in the onboard-clock domain
    // so it reports even when ft_clk is dead. ft_clk liveness is detected by
    // re-timing an ft_clk-domain toggling bit (hb_ft[3], ~12.5 MHz) into this
    // clock and running a ~84 ms watchdog: toggles seen => alive.
    // -------------------------------------------------------------------------
    reg s0 = 1'b0, s1 = 1'b0, s1d = 1'b0;
    always @(posedge clk) begin s0 <= hb_ft[3]; s1 <= s0; s1d <= s1; end
    reg [22:0] ft_wd = 23'd0;
    always @(posedge clk) begin
        if (s1 ^ s1d)      ft_wd <= 23'h7FFFFF;        // saw an ft_clk toggle -> reload
        else if (ft_wd != 0) ft_wd <= ft_wd - 23'd1;
    end
    wire ftclk_alive = (ft_wd != 23'd0);

    // ft_clk-domain counters sampled asynchronously for the report (cosmetic only)
    debug_status #(.CLK_HZ(100_000_000), .BAUD(115200), .PERIOD(50_000_000)) u_dbg (
        .clk         (clk),
        .rst         (rst_clk),
        .ftclk_alive (ftclk_alive),
        .txe         (ft_txe),
        .word_ctr    (word_ctr),
        .frame_index (frame_index),
        .tx          (usb_tx)
    );

endmodule
