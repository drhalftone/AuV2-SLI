`timescale 1ns/1ps
//=============================================================================
// ddr_bist.v - DDR3 -> PC, the first half of getting a WHOLE frame out.
//
// A full 1280 x 1024 8-bit frame is 1,310,720 bytes. The XC7A100T has ~607 KB
// of block RAM, so the entire focal plane cannot be buffered on-chip at all --
// it fits only in the Pt V2's 256 MB DDR3L, which holds ~195 such frames. This
// is CAMERA_RTL_PLAN.md milestone #16, and its gate is exactly this: put a
// known pattern in DDR, read it back out to the PC, and prove it byte-exact.
//
// Deliberately NO CAMERA. The pattern is generated on-chip, so a failure here
// is a DDR/MIG failure and cannot be confused with a sensor or LVDS problem.
// Wiring the camera in before this passes would mean debugging two unproven
// subsystems through each other, which is how the last two days went.
//
//   MT41K128M16XX-15E, 128M x 16 DDR3L, 800 MT/s, 1.35 V, bank 15
//   MIG native UI: 128-bit app_wdf_data / app_rd_data = 16 bytes per word
//   ui_clk = 100 MHz (PHY ratio 4:1 of the 400 MHz memory clock)
//
// ADDRESSING. app_addr counts in DDR3 words, and one 128-bit UI word is a BL8
// burst, so consecutive UI words are 8 apart. Stepping app_addr by 1 instead of
// 8 is the classic MIG mistake: every write lands inside the previous burst and
// you read back a frame that is one eighth of a frame, repeated.
//
// THE PATTERN is derived from the word index, so the host can predict every
// byte without being told what was written:
//
//     word i, byte j  ->  (i * 16 + j) & 0xFF
//
// which is simply a byte counter modulo 256 across the whole frame. Any address
// error shows up as a phase jump, exactly the way the sensor's test pattern
// exposed the horizontal seams.
//
// WIRE FORMAT matches the camera dump so the host tooling is shared:
//     "FRAMESTART\n"  then NBYTES raw bytes  then "\nFRAMEEND\n"
//
// LEDs
//   led[7] heartbeat   led[6] calib done   led[5] writing
//   led[4] streaming   led[3] pass (host-independent compare)   led[2] fail
//=============================================================================
module ddr_bist #(
    parameter integer CLK_HZ = 100_000_000,   // ui_clk, not the board clock
    parameter integer BAUD   =   1_000_000,
    parameter integer NCOL   = 1280,
    parameter integer NROW   = 1024
)(
    input  wire        clk,        // 100 MHz board clock
    input  wire        rst_n,

    output reg  [7:0]  led,
    output wire        usb_tx,
    input  wire        usb_rx,

    inout  wire [15:0] ddr3_dq,
    inout  wire [1:0]  ddr3_dqs_p,
    inout  wire [1:0]  ddr3_dqs_n,
    output wire [13:0] ddr3_addr,
    output wire [2:0]  ddr3_ba,
    output wire        ddr3_ras_n,
    output wire        ddr3_cas_n,
    output wire        ddr3_we_n,
    output wire        ddr3_reset_n,
    output wire [0:0]  ddr3_ck_p,
    output wire [0:0]  ddr3_ck_n,
    output wire [0:0]  ddr3_cke,
    output wire [0:0]  ddr3_cs_n,
    output wire [1:0]  ddr3_dm,
    output wire [0:0]  ddr3_odt
);
    localparam integer NBYTES = NCOL * NROW;          // 1,310,720
    localparam integer NWORDS = NBYTES / 16;          // 81,920 UI words
    localparam integer ASTEP  = 8;                    // BL8: app_addr per UI word

    wire _unused = usb_rx;

    //----------------------------------- both MIG clocks from ONE MMCM
    //
    // sys_clk_i comes from the MMCM, NOT straight off the pin. Driving the pin
    // into both this MMCM and the MIG's own internal PLL is a clock-capable-IO
    // conflict and the placer refuses it outright:
    //   [Place 30-172] Sub-optimal placement for a clock-capable IO pin and PLL
    //   [Place 30-99]  IO Clock Placer failed
    // The .prj sets SystemClock = "No Buffer", i.e. the MIG expects an already
    // buffered clock handed to it, which is exactly what CLKOUT1 is.
    //
    //   CLKIN 100 MHz, M = 10 -> VCO 1000 MHz
    //   CLKOUT0 / 5  = 200.000 MHz -> clk_ref_i (the MIG's IDELAYCTRL reference)
    //   CLKOUT1 / 10 = 100.000 MHz -> sys_clk_i (InputClkFreq in the .prj)
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

    reg [27:0]  r_addr = 28'd0;
    reg [2:0]   r_cmd  = 3'd0;
    reg [127:0] r_wdata = 128'd0;

    // THE HANDSHAKE. app_en must be HELD until app_en && app_rdy are true in the
    // SAME cycle; likewise app_wdf_wren against app_wdf_rdy. Registering them a
    // cycle after sampling rdy -- which the first version did, despite a comment
    // claiming otherwise -- silently drops every command issued while rdy has
    // gone low for a refresh or a bank conflict. The symptom was not a subtle
    // one: only 21 distinct 16-byte values existed across 4000 words, because
    // almost nothing was ever written and reads returned stale memory.
    //
    // Command and data are accepted INDEPENDENTLY, so each needs its own done
    // flag; they are not guaranteed to complete on the same cycle.
    reg cmd_done = 1'b0, dat_done = 1'b0;
    wire issuing = (st == S_WR) || (st == S_RDCMD);

    assign app_addr     = r_addr;
    assign app_cmd      = r_cmd;
    assign app_en       = issuing && !cmd_done;
    assign app_wdf_data = r_wdata;
    assign app_wdf_wren = (st == S_WR) && !dat_done;
    assign app_wdf_end  = (st == S_WR) && !dat_done;   // BL8 in 4:1: one beat

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
        // ACTIVE HIGH -- mig_pt_v2.prj says <SysResetPolarity>ACTIVE HIGH.
        // Wiring rst_n straight in held the MIG in reset permanently: calibration
        // never completed, the FSM sat in S_WAIT and not one byte was ever sent.
        // Also gated on MMCM lock, since sys_clk_i and clk_ref_i both come from
        // it and starting the controller before they are stable is meaningless.
        .sys_rst(mig_rst)
    );

    reg [1:0] rstn_s = 2'b00;
    always @(posedge clk100) rstn_s <= {rstn_s[0], rst_n};
    wire mig_rst = !rstn_s[1] || !mmcm_locked;

    // expected 128-bit word for index i: a plain byte counter mod 256
    function [127:0] patt(input [16:0] i);
        integer k;
        begin
            for (k = 0; k < 16; k = k + 1)
                patt[k*8 +: 8] = (i * 16 + k) & 8'hFF;
        end
    endfunction

    //---------------------------------------------------------------- the FSM
    localparam [3:0] S_WAIT=0, S_WR=1, S_WRW=2, S_RD=3, S_HDR=4, S_BYTE=5,
                     S_FTR=6, S_DONE=7, S_RDCMD=8, S_RDWAIT=9;
    reg [3:0]  st = S_WAIT;
    reg [16:0] widx = 17'd0;      // 0 .. NWORDS-1
    reg [127:0] rword = 128'd0;
    reg [3:0]  byi = 4'd0;
    reg        have = 1'b0;
    reg        fail = 1'b0;

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
        send  <= 1'b0;

        if (ui_rst) begin
            st <= S_WAIT; widx <= 17'd0; r_addr <= 28'd0; r_cmd <= 3'd0;
            byi <= 4'd0; have <= 1'b0; fail <= 1'b0; hi <= 5'd0;
        end else begin
            case (st)
            S_WAIT: if (init_calib_complete) begin
                        st <= S_WR; widx <= 17'd0; r_addr <= 28'd0;
                        r_cmd <= 3'd0; r_wdata <= patt(17'd0);
                        cmd_done <= 1'b0; dat_done <= 1'b0;
                    end

            // WRITE. Command and data have SEPARATE ready signals and either can
            // stall, so both must be held until each is accepted. Issuing the
            // command on app_rdy alone and assuming the data went with it is the
            // other classic MIG mistake.
            S_WR: begin
                if (app_en   && app_rdy)     cmd_done <= 1'b1;
                if (app_wdf_wren && app_wdf_rdy) dat_done <= 1'b1;
                if ((cmd_done || (app_en && app_rdy)) &&
                    (dat_done || (app_wdf_wren && app_wdf_rdy))) begin
                    cmd_done <= 1'b0; dat_done <= 1'b0;
                    st <= S_WRW;
                end
            end
            S_WRW: begin                          // advance to the next word
                if (widx == NWORDS[16:0] - 17'd1) begin
                    st <= S_RD; widx <= 17'd0; r_addr <= 28'd0; hi <= 5'd0;
                end else begin
                    widx    <= widx + 17'd1;
                    r_addr  <= r_addr + ASTEP;
                    r_wdata <= patt(widx + 17'd1);
                    st      <= S_WR;
                end
            end

            S_RD: begin
                hi <= 5'd0;
                st <= S_HDR;
            end

            S_HDR: if (tx_free) begin
                data_q <= hdr_ch; send <= 1'b1;
                if (hi == 5'd10) begin hi <= 5'd0; st <= S_RDCMD; byi <= 4'd0;
                                       have <= 1'b0; r_cmd <= 3'd1; end
                else hi <= hi + 5'd1;
            end

            // READ AND STREAM. One 128-bit word is fetched, then drained a byte
            // at a time into the UART; at 1 Mbaud a byte takes 10 us and a read
            // takes ~100 ns, so the UART is the bottleneck by three orders of
            // magnitude and no read FIFO is needed.
            // EXACTLY ONE read command per word. The first version asserted
            // r_en on every cycle app_rdy was high while waiting for data, so
            // each word issued a burst of identical reads; only the first was
            // captured and the rest arrived later, sliding the stream out of
            // step. It read correctly for 1079 words and then diverged, which is
            // what a queue slowly filling looks like.
            S_RDCMD: if (app_en && app_rdy) st <= S_RDWAIT;
            S_RDWAIT: if (app_rd_data_valid) begin
                rword <= app_rd_data;
                have  <= 1'b1;
                byi   <= 4'd0;
                if (app_rd_data != patt(widx)) fail <= 1'b1;
                st    <= S_BYTE;
            end

            S_BYTE: begin
                if (tx_free) begin
                    data_q <= rword[byi*8 +: 8];
                    send   <= 1'b1;
                    if (byi == 4'd15) begin
                        byi  <= 4'd0;
                        have <= 1'b0;
                        if (widx == NWORDS[16:0] - 17'd1) begin
                            hi <= 5'd0; st <= S_FTR;
                        end else begin
                            widx   <= widx + 17'd1;
                            r_addr <= r_addr + ASTEP;
                            st     <= S_RDCMD;
                        end
                    end else byi <= byi + 4'd1;
                end
            end

            S_FTR: if (tx_free) begin
                data_q <= ftr_ch; send <= 1'b1;
                if (hi == 5'd9) begin hi <= 5'd0; st <= S_DONE; end
                else hi <= hi + 5'd1;
            end

            S_DONE: begin                          // re-offer the same frame
                widx <= 17'd0; r_addr <= 28'd0; st <= S_HDR; hi <= 5'd0;
                have <= 1'b0;
            end
            default: st <= S_WAIT;
            endcase
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(ui_clk), .rst(ui_rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

    reg [26:0] hb = 27'd0;
    always @(posedge ui_clk) begin
        hb <= hb + 27'd1;
        led[7] <= hb[26];
        led[6] <= init_calib_complete;
        led[5] <= (st == S_WR) || (st == S_WRW);
        led[4] <= (st == S_BYTE);
        led[3] <= (st == S_DONE) && !fail;
        led[2] <= fail;
        led[1] <= mmcm_locked;
        led[0] <= 1'b0;
    end
endmodule
