`timescale 1ns/1ps
//=============================================================================
// ddr_loop_ft.v -- CONCURRENT DDR3 write + read, isolated from everything else.
//
// cam_frame_ft's concurrent version builds clean (setup +0.082, hold +0.059,
// MIG instantiation identical to the working build, nothing driving the
// controller during calibration) and yet init_calib_complete NEVER ASSERTS.
// Reasoning about it failed, so this reproduces the question with the smallest
// design that can still ask it:
//
//   process A: generate a deterministic pixel pattern, write it into DDR3
//   process B: read those same words back and stream them over the Ft+
//
// Deliberately absent: camera, LVDS, IDELAY, boot sequencer, the wordclk domain
// and its CDC, the FT601 read path and the bidirectional bus. If THIS
// calibrates and streams, the concurrency itself is fine and the fault is in
// something cam_frame_ft adds. If it does not, the fault is in the arbiter or
// the FSM split, and it can be debugged here in a fraction of the build time.
//
// THE PATTERN IS THE POINT. Every 32-bit word is {2'b0, frame[5:0], index[23:0]},
// so the host can check each word against what it MUST be, rather than checking
// that two frames match each other. A bug that corrupts every frame identically
// would pass the weaker test and fail this one.
//
// Arbitration and fence are carried over unchanged from the camera version:
// WRITER PRIORITY on the single MIG command port (a dropped producer word is
// gone forever; a stalled reader catches up), and rf < wf_done so the reader
// never touches a frame the writer has not finished.
//=============================================================================
module ddr_loop_ft #(
    parameter integer NCOL    = 1280,
    parameter integer NROW    = 1024,
    parameter integer NFRAMES = 8,
    // Produce a word every GEN_DIV ui_clk cycles. 3 gives ~33 M words/s, close
    // to the camera's 36 M, so the DDR sees a realistic producer load rather
    // than a generator that simply races the memory.
    parameter integer GEN_DIV = 3
)(
    input  wire        clk,
    input  wire        rst_n,
    output reg  [7:0]  led,
    output wire        usb_tx,

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
    localparam integer NPIX   = NCOL * NROW;
    localparam integer NWORDS = NPIX / 8;          // 163,840 DDR words per frame
    localparam integer FBYTES = NPIX * 2;          // 2,621,440 bytes per frame
    localparam integer ASTEP  = 8;                 // BL8: consecutive words are 8 apart
    localparam [31:0]  MAGIC  = 32'h30494C53;      // "SLI0"
    localparam integer MAXOUT = 16;

    assign ft_wakeup = 1'b1;

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    // FT601 RESET_N must be PULSED after configuration, not tied to rst_n: the
    // chip can sit enumerated on USB while driving no ft_clk at all, and then
    // nothing moves. ~42 ms low, then release.
    reg [21:0] ftrst_cnt = 22'd0;
    reg        ftrst_rel = 1'b0;
    always @(posedge clk) begin
        if (!rstn_sync[1]) begin ftrst_cnt <= 22'd0; ftrst_rel <= 1'b0; end
        else if (ftrst_cnt != {22{1'b1}}) ftrst_cnt <= ftrst_cnt + 22'd1;
        else ftrst_rel <= 1'b1;
    end
    assign ft_reset = ftrst_rel;

    //---------------------------------------------------------------- clocks
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
    BUFG u_fb   (.I(fb),       .O(fb_g));
    BUFG u_c200 (.I(c200_raw), .O(clk200));
    BUFG u_c100 (.I(c100_raw), .O(clk100));

    //------------------------------------------------------------------ MIG
    wire        ui_clk, ui_rst, init_calib_complete;
    wire [27:0] app_addr;
    wire [2:0]  app_cmd;
    wire        app_en, app_rdy;
    wire [127:0] app_wdf_data;
    wire        app_wdf_end, app_wdf_wren, app_wdf_rdy;
    wire [127:0] app_rd_data;
    wire        app_rd_data_valid, app_rd_data_end;

    reg [1:0] mrstn_s = 2'b00;
    always @(posedge clk100) mrstn_s <= {mrstn_s[0], rst_n};
    wire mig_rst = !mrstn_s[1] || !mmcm_locked;      // ACTIVE HIGH per the .prj

    reg [127:0] wdata = 128'd0;
    reg [27:0]  waddr = 28'd0, raddr = 28'd0;
    reg         cmd_done = 1'b0, dat_done = 1'b0;

    localparam [1:0] W_WAIT=0, W_GEN=1, W_ISSUE=2, W_DONE=3;
    localparam [1:0] R_IDLE=0, R_HDR=1, R_RUN=2;
    reg [1:0] stw = W_WAIT;
    reg [1:0] str = R_IDLE;
    reg [17:0] wfw = 18'd0;          // words written within this frame
    reg [5:0]  wf = 6'd0;            // frame being written
    reg [5:0]  wf_done = 6'd0;       // frames FULLY written -- the fence
    reg [5:0]  rf = 6'd0;            // frame being read
    reg [17:0] rd_iss = 18'd0, rd_got = 18'd0;
    reg [4:0]  outst = 5'd0;
    reg [2:0]  hw = 3'd0;
    reg [31:0] frame_idx = 32'd0;

    wire ufifo_afull;

    wire w_req = (stw == W_ISSUE) && !cmd_done;
    wire r_req = (str == R_RUN) && (rd_iss != NWORDS[17:0])
                 && (outst < MAXOUT[4:0]) && !ufifo_afull;
    wire r_gnt = r_req && !w_req;        // WRITER PRIORITY
    wire r_ack = r_gnt && app_rdy;
    wire w_ack = w_req && app_rdy;

    assign app_addr     = w_req ? waddr : raddr;
    assign app_cmd      = w_req ? 3'd0  : 3'd1;
    assign app_en       = w_req || r_gnt;
    assign app_wdf_data = wdata;
    assign app_wdf_wren = (stw == W_ISSUE) && !dat_done;
    assign app_wdf_end  = (stw == W_ISSUE) && !dat_done;

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
        .sys_rst(mig_rst)
    );

    //------------------------------------------------ ui_clk -> ft_clk FIFO
    wire ufifo_full, ufifo_empty, ufifo_ovf;
    wire [127:0] ufifo_dout;
    reg  [127:0] ufifo_din = 128'd0;
    reg          ufifo_wr = 1'b0;
    wire         ufifo_rd;
    wire         ft_rst;

    cam_async_fifo #(.DW(128), .AW(8), .AFULL_MARGIN(MAXOUT + 8)) u_ufifo (
        .wr_clk(ui_clk), .wr_rst(ui_rst), .wr_en(ufifo_wr),
        .wr_data(ufifo_din), .full(ufifo_full), .afull(ufifo_afull),
        .overflow(ufifo_ovf),
        .rd_clk(ft_clk), .rd_rst(ft_rst), .rd_en(ufifo_rd),
        .rd_data(ufifo_dout), .empty(ufifo_empty)
    );

    //------------------------------------------------------- the two processes
    // PATTERN: every 32-bit word is {2'b0, frame[5:0], index[23:0]}. The host
    // checks each word against what it must be, so a fault that corrupts all
    // frames identically cannot hide.
    wire [23:0] base = {wfw, 2'b00};                 // wfw * 4
    wire [127:0] pattern = { {2'b00, wf, base + 24'd3},
                             {2'b00, wf, base + 24'd2},
                             {2'b00, wf, base + 24'd1},
                             {2'b00, wf, base + 24'd0} };

    reg [2:0] gdiv = 3'd0;
    wire gen_rdy = (gdiv == 3'd0);

    always @(posedge ui_clk) begin
        ufifo_wr <= 1'b0;

        // app_rd_data_valid cannot be back-pressured: take it or lose it.
        if (!ui_rst && (str == R_RUN) && app_rd_data_valid) begin
            ufifo_wr  <= 1'b1;
            ufifo_din <= app_rd_data;
            rd_got    <= rd_got + 18'd1;
        end

        if (ui_rst) begin
            stw <= W_WAIT; str <= R_IDLE;
            waddr <= 28'd0; raddr <= 28'd0;
            wfw <= 18'd0; wf <= 6'd0; wf_done <= 6'd0; rf <= 6'd0;
            cmd_done <= 1'b0; dat_done <= 1'b0; hw <= 3'd0;
            frame_idx <= 32'd0; rd_iss <= 18'd0; rd_got <= 18'd0;
            outst <= 5'd0; gdiv <= 3'd0;
        end else begin
            if (gdiv == GEN_DIV[2:0] - 3'd1) gdiv <= 3'd0;
            else                             gdiv <= gdiv + 3'd1;

            if (r_ack) begin
                rd_iss <= rd_iss + 18'd1;
                raddr  <= raddr + ASTEP;
                if (!app_rd_data_valid) outst <= outst + 5'd1;
            end else if (app_rd_data_valid && (str == R_RUN) && (outst != 5'd0)) begin
                outst <= outst - 5'd1;
            end

            //-------------------------------------------- process A: generate
            case (stw)
            W_WAIT: if (init_calib_complete) begin
                        stw <= W_GEN; waddr <= 28'd0;
                        wfw <= 18'd0; wf <= 6'd0; wf_done <= 6'd0;
                    end
            W_GEN: if (gen_rdy) begin
                       wdata <= pattern;
                       cmd_done <= 1'b0; dat_done <= 1'b0;
                       stw <= W_ISSUE;
                   end
            W_ISSUE: begin
                if (w_ack)                       cmd_done <= 1'b1;
                if (app_wdf_wren && app_wdf_rdy) dat_done <= 1'b1;
                if ((cmd_done || w_ack) &&
                    (dat_done || (app_wdf_wren && app_wdf_rdy))) begin
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    waddr <= waddr + ASTEP;
                    if (wfw == NWORDS[17:0] - 18'd1) begin
                        wfw     <= 18'd0;
                        wf_done <= wf_done + 6'd1;    // THIS frame is readable
                        if (wf == NFRAMES[5:0] - 6'd1) stw <= W_DONE;
                        else begin wf <= wf + 6'd1; stw <= W_GEN; end
                    end else begin
                        wfw <= wfw + 18'd1; stw <= W_GEN;
                    end
                end
            end
            W_DONE: ;                       // producer finished; reader carries on
            default: stw <= W_WAIT;
            endcase

            //---------------------------------------------- process B: stream
            case (str)
            R_IDLE: if (rf < wf_done) begin hw <= 3'd0; str <= R_HDR; end

            R_HDR: if (!ufifo_afull) begin
                ufifo_wr <= 1'b1;
                if (hw == 3'd0)
                    ufifo_din <= { {18'd0, NFRAMES[5:0], 2'd0, rf},
                                   {NROW[15:0], NCOL[15:0]},
                                   frame_idx, MAGIC };
                else
                    ufifo_din <= { ~MAGIC, 32'd2, FBYTES/4, FBYTES };
                if (hw == 3'd1) begin
                    hw <= 3'd0; rd_iss <= 18'd0; rd_got <= 18'd0; str <= R_RUN;
                end else hw <= hw + 3'd1;
            end

            R_RUN: if (rd_got == NWORDS[17:0]) begin
                frame_idx <= frame_idx + 32'd1;
                if (rf == NFRAMES[5:0] - 6'd1) begin
                    rf <= 6'd0; raddr <= 28'd0;
                end else rf <= rf + 6'd1;
                str <= R_IDLE;
            end
            default: str <= R_IDLE;
            endcase
        end
    end

    //---------------------------------------------------- FT601 write master
    reg [1:0] ftrst_s = 2'b11;
    always @(posedge ft_clk) ftrst_s <= {ftrst_s[0], ~rst_n};
    assign ft_rst = ftrst_s[1];

    // unpack 128 -> 4 x 32 ahead of a two-deep skid (keeps ft_txe out of the
    // FIFO-status cone; see ft601_sync_tx.v)
    reg [127:0] uw = 128'd0;
    reg [1:0]   uidx = 2'd0;
    reg         uvalid = 1'b0;
    wire        u_take;
    wire        upop = !ufifo_empty && (!uvalid || (u_take && uidx == 2'd3));
    assign ufifo_rd = upop;
    always @(posedge ft_clk) begin
        if (ft_rst) begin uvalid <= 1'b0; uidx <= 2'd0; end
        else if (upop) begin uw <= ufifo_dout; uidx <= 2'd0; uvalid <= 1'b1; end
        else if (u_take) begin
            if (uidx == 2'd3) uvalid <= 1'b0;
            else uidx <= uidx + 2'd1;
        end
    end
    wire [31:0] u_word = uw[uidx*32 +: 32];

    reg [31:0] b0 = 32'd0, b1 = 32'd0;
    reg [1:0]  cnt = 2'd0;
    wire       ft_adv;
    wire       pop = uvalid && (cnt != 2'd2);
    assign u_take = pop;
    always @(posedge ft_clk) begin
        if (ft_rst) cnt <= 2'd0;
        else case ({pop, ft_adv})
            2'b10: begin
                if (cnt == 2'd0) b0 <= u_word; else b1 <= u_word;
                cnt <= cnt + 2'd1;
            end
            2'b01: begin b0 <= b1; cnt <= cnt - 2'd1; end
            2'b11: begin
                if (cnt == 2'd1) b0 <= u_word;
                else begin b0 <= b1; b1 <= u_word; end
            end
            default: ;
        endcase
    end

    wire [31:0] ft_dout;
    wire [3:0]  ft_beout;
    wire        bus_oe;
    ft601_sync_tx u_ft (
        .clk(ft_clk), .rst(ft_rst),
        .ft_txe(ft_txe), .rx_hold(1'b0),      // TX only here: no control channel
        .ft_wr(ft_wr), .ft_oe(ft_oe), .ft_rd(ft_rd),
        .ft_dout(ft_dout), .ft_beout(ft_beout), .bus_oe(bus_oe),
        .s_word(b0), .s_valid(cnt != 2'd0), .s_adv(ft_adv)
    );
    assign ft_data = bus_oe ? ft_dout  : 32'bz;
    assign ft_be   = bus_oe ? ft_beout : 4'bz;

    //--------------------------------------------------------- COM6 telemetry
    // {stw, str, calib, ovf, empty, txe, wf_done, rf, frame_idx[15:0]} = 40 bits
    wire [39:0] stat = { stw, str, init_calib_complete, ufifo_ovf,
                         ufifo_empty, ft_txe, wf_done, rf, frame_idx[15:0],
                         4'd0 };
    reg [39:0] shold = 40'd0;
    reg [3:0]  nib = 4'd0;
    reg [23:0] utick = 24'd0;
    reg [7:0]  ubyte = 8'd0;
    reg        usend = 1'b0;
    reg [1:0]  ust = 2'd0;
    wire       ubusy;
    wire [3:0] n = shold[39 - nib*4 -: 4];

    always @(posedge ui_clk) begin
        usend <= 1'b0;
        if (ui_rst) begin ust <= 2'd0; utick <= 24'd0; nib <= 4'd0; end
        else case (ust)
        2'd0: begin
            utick <= utick + 24'd1;
            if (utick == 24'd10_000_000) begin
                utick <= 24'd0; shold <= stat; nib <= 4'd0; ust <= 2'd1;
            end
        end
        2'd1: if (!ubusy && !usend) begin
            ubyte <= (n < 4'd10) ? (8'd48 + {4'd0,n}) : (8'd55 + {4'd0,n});
            usend <= 1'b1;
            if (nib == 4'd9) ust <= 2'd2; else nib <= nib + 4'd1;
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
        led <= {hb[26], init_calib_complete, (stw == W_DONE), (str != R_IDLE),
                ufifo_ovf, ufifo_empty, wf_done[1:0]};
    end
endmodule
