`timescale 1ns/1ps
//=============================================================================
// cam_boot_stage1 - STAGE 1: give the sensor a real 72 MHz reference, run
// Avnet's SEQ01, and ask its PLL to lock.
//
// THE GATE: register 24 bit 0 goes high. That is the sensor's own PLL reporting
// lock, and it is the first thing this project has ever asked the part to DO
// rather than merely report. It proves clk_pll is electrically good, that the
// PLL configuration took, and that writes reach a block that acts on them.
//
// It also unblocks stage 2. cam_regdump showed registers 112 and 116-126 read
// 0x0000 and refuse writes while register 16 writes fine -- those registers are
// in the serializer/LVDS block, which is asleep until clock management is
// enabled. So the LVDS drivers cannot be powered up until this stage passes.
// Stage 2 was never independent of stage 1; that was an error in the plan.
//
//-----------------------------------------------------------------------------
// WHAT THIS DOES AND DOES NOT WRITE
//
// cam_boot_seq is instantiated with STOP_AT = 8: SEQ01's eight writes, then the
// PLL lock poll, then STOP. It does NOT run SEQ03/04/05, so:
//
//     register 112 is NOT written  -> LVDS drivers stay powered down
//     register 192 is NOT written  -> the sequencer stays disabled
//
// The sensor does not stream and drives nothing on bank 13. Stage 2 raises
// STOP_AT to 41, which adds the LVDS power-up but still leaves the sequencer
// off.
//
//-----------------------------------------------------------------------------
// THE 72 MHz CLOCK -- a real MMCM this time, not the /2 divider.
//
// CAMERA_SENSOR_PROTOCOL.md 4.1: fin = 72 MHz, duty 45-50-55 %, jitter <= 20 ps.
// MMCME2_BASE, D = 5, M = 54 -> VCO 1080 MHz, CLKOUT0 / 15 = 72.000 MHz EXACT,
// which is the arrangement CAMERA_RTL_PLAN.md task #2 specifies. Forwarded to
// the pin through ODDR + OBUF so the output is a clock-quality edge rather than
// fabric logic.
//
// The earlier 50 MHz divider was fine for asking "does anything change", but a
// PLL being asked to lock deserves the real reference.
//
// ORDERING: the MMCM free-runs from configuration and cam_boot_seq holds
// cam_reset_n low until it is triggered, so the reference is stable long before
// reset releases, and it is never stopped while the sensor is out of reset
// (protocol section 6).
//
//-----------------------------------------------------------------------------
// LEDs
//   led[7]  heartbeat
//   led[6]  MMCM locked (our 72 MHz is running)
//   led[5]  boot sequence reported READY
//   led[4]  boot sequence reported FAILED (chip-ID mismatch)
//   led[3]  PLL TIMEOUT -- the sensor never reported lock
//   led[2]  reg 24 bit 0 read back SET  <-- THE GATE
//
// UART:  bt=R pt=0 r16=0003 r24=0001
//   bt = B busy / R ready / F failed      pt = pll_timeout flag
//   r16 = PLL control readback (expect 0003 after SEQ01)
//   r24 = PLL lock register  (expect bit 0 SET)
//=============================================================================
module cam_boot_stage1 #(
    parameter integer CLK_HZ   = 100_000_000,
    parameter integer BAUD     =     115_200,
    parameter integer STOP_AT  = 8,             // SEQ01 + PLL poll only
    // 1 = configure the sensor for TRIGGERED global shutter, master mode, so it
    // emits one frame per rising edge on trigger0 instead of free-running.
    // Passed straight through to cam_boot_seq; see its header for register 192.
    parameter integer TRIGGERED = 0,
    // 1 = emit the sensor's built-in per-lane constant test pattern instead of
    // pixels. Requires STOP_AT = 45 (or 0) so rom[41..44] actually run.
    parameter integer TESTPAT   = 0,
    // Register 201 exposure0, passed through to cam_boot_seq.
    parameter [15:0]  EXPOSURE  = 16'h2710,
    // MMCM CLKOUT0 divide. 15.0 -> 1080/15 = 72.000 MHz, the sensor's nominal
    // reference. 30.0 -> 36.000 MHz, which HALVES the serialiser's bit rate to
    // 360 Mbps and doubles the data eye -- the margin test. 36 MHz sits inside
    // the 30-45 MHz reference band Avnet's driver handles explicitly.
    parameter real    PLL_DIV  = 15.000,
    parameter integer POLL_CY  = CLK_HZ / 2,
    // BUILD-TIME READOUT WINDOW: sensor ROI 0, which is the whole 1280 x 1024 array
    // by default. Anything else is written into ROI 0 (regs 256..258) once the boot
    // ROM has run -- before the stream starts, and again after every re-boot, since
    // a re-boot resets the sensor's registers. X0 and W in pixels, multiples of 8
    // (one kernel); Y0 and H in rows, Y0 even on the colour sensor. The caller must
    // size its frame to match: cam_frame_ft takes the same numbers as NCOL / NROW.
    parameter integer WIN_X0 = 0,
    parameter integer WIN_Y0 = 0,
    parameter integer WIN_W  = 1280,
    parameter integer WIN_H  = 1024
)(
    input  wire       clk,
    input  wire       rst_n,
    input  wire       stream_go,   // pulse: perform the deferred reg 192 write
    output reg        streaming,   // set once that write has landed
    // The boot ROM (CDS program included) has completed. Cleared by rst, so
    // after an opcode-6 re-boot it rises again exactly when the sensor's LVDS
    // outputs and training pattern are back -- which is when the receive path
    // can be re-centred. Unconnected in the bring-up harnesses.
    output wire       boot_ready,

    // RUNTIME EXPOSURE. Pulse expo_req with a value on expo_val and reg 201
    // (exposure0) is rewritten at the next opportunity. Exposure was previously
    // a build-time ROM constant, so a brightness-vs-exposure curve cost one
    // 13-minute bitstream per point; this makes it one capture.
    input  wire        expo_req,
    input  wire [15:0] expo_val,

    // RUNTIME READOUT WINDOW. win = 1 reads out only a 32 x 24 window around the
    // 16x16 ROI at (win_col8*8, win_row8*8) instead of the whole 1280 x 1024 array,
    // cutting a picture's readout from ~6 ms to tens of microseconds (datasheet
    // fps table: ~171 us fixed + ~1.2 us/row). The window is sensor ROI 1 and the
    // full frame stays in ROI 0, so switching is one write to roi_active (reg 195)
    // -- the switch the datasheet recommends -- and ROI 1 is only ever rewritten
    // while it is inactive. Left unconnected (tied 0) the window is never touched.
    //
    // WHY 32 x 24 AND NOT 16 x 16. Rows: the frame opens with black_lines (5, rom[11])
    // black rows, so the ROI sits at window rows 8..23 -- a whole number of roi_row8
    // steps, clear of the black rows for any black_lines <= 8. Columns: kernels
    // col8-1 .. col8+2, so the ROI's two kernels are never the first or last of a
    // line, the ones that carry the LS/LE framing words. Same sensor pixels as the
    // full-frame ROI at the same (col8, row8); the margin costs ~10 us per picture.
    input  wire        win,
    input  wire [7:0]  win_col8,
    input  wire [7:0]  win_row8,
    output wire        win_on,      // the sensor is reading out the window now

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output wire       cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    wire _unused = usb_rx | (|cam_monitor);

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    assign cam_trigger = 3'b000;

    //------------------------------------------------- 72.000 MHz for clk_pll
    wire clkfb, clkfb_bufg, clk72_raw, clk72, mmcm_locked;

    MMCME2_BASE #(
        .BANDWIDTH          ("OPTIMIZED"),
        .CLKIN1_PERIOD      (10.000),      // 100 MHz in
        .DIVCLK_DIVIDE      (5),           // D = 5   -> 20 MHz PFD
        .CLKFBOUT_MULT_F    (54.000),      // M = 54  -> VCO 1080 MHz
        .CLKOUT0_DIVIDE_F   (PLL_DIV),     // 15 -> 72.000 MHz, 30 -> 36.000 MHz
        .CLKOUT0_DUTY_CYCLE (0.500),       // spec is 45-50-55 %
        .STARTUP_WAIT       ("FALSE")
    ) u_mmcm (
        .CLKIN1   (clk),
        .CLKFBIN  (clkfb_bufg),
        .CLKFBOUT (clkfb),
        .CLKOUT0  (clk72_raw),
        .CLKOUT1  (), .CLKOUT2  (), .CLKOUT3 (), .CLKOUT4 (),
        .CLKOUT5  (), .CLKOUT6  (),
        .CLKOUT0B (), .CLKOUT1B (), .CLKOUT2B (), .CLKOUT3B (), .CLKFBOUTB (),
        .LOCKED   (mmcm_locked),
        .PWRDWN   (1'b0),
        .RST      (1'b0)
    );
    BUFG u_fb  (.I(clkfb),     .O(clkfb_bufg));
    BUFG u_c72 (.I(clk72_raw), .O(clk72));

    // ODDR forwarding: the supported way to get a clock off-chip.
    wire clk_pll_o;
    ODDR #(.DDR_CLK_EDGE("OPPOSITE_EDGE"), .INIT(1'b0), .SRTYPE("SYNC")) u_oddr (
        .Q(clk_pll_o), .C(clk72), .CE(1'b1), .D1(1'b1), .D2(1'b0),
        .R(1'b0), .S(1'b0)
    );
    OBUF u_obuf (.I(clk_pll_o), .O(cam_clk_pll));

    //-------------------------------------------------------------- SPI master
    wire        spi_start, spi_rw, spi_busy, spi_done;
    wire [8:0]  spi_addr;
    wire [15:0] spi_wdata, spi_rdata;

    // cam_boot_seq owns the bus while busy; afterwards the poller below does.
    wire        b_start, b_rw, b_busy, b_ready, b_failed, b_plltmo, b_resetn;
    wire [8:0]  b_addr;
    wire [15:0] b_wdata;

    reg         p_start;
    reg  [8:0]  p_addr;
    reg         p_wr    = 1'b0;
    reg  [15:0] p_wdata = 16'h0000;

    assign spi_start = b_busy ? b_start : p_start;
    assign spi_rw    = b_busy ? b_rw    : p_wr;
    assign spi_addr  = b_busy ? b_addr  : p_addr;
    assign spi_wdata = b_busy ? b_wdata : p_wdata;

    // DEFERRED STREAM START.
    //
    // With STOP_AT = 41 the boot stops one entry short of rom[41] = reg 192 =
    // 0x0801, the sequencer enable, so the sensor sits idle emitting the
    // training pattern. That idle is not incidental: the IDELAY eye scan needs
    // 256 CONSECUTIVE training words, and once the sensor streams, the training
    // pattern only appears in inter-line gaps. Scanning against live image data
    // finds nothing and parks the taps badly.
    //
    // So the caller scans and aligns first, then pulses stream_go, and this
    // performs that one remaining write. Ordering, not extra capability.
    reg stream_req = 1'b0;
    always @(posedge clk) begin
        if (rst)            stream_req <= 1'b0;
        else if (stream_go) stream_req <= 1'b1;
        else if (streaming) stream_req <= 1'b0;
    end

    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(1_000_000)) u_spi (
        .clk(clk), .rst(rst),
        .start(spi_start), .rw(spi_rw), .addr(spi_addr), .wdata(spi_wdata),
        .rdata(spi_rdata), .busy(spi_busy), .done(spi_done),
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(cam_miso)
    );

    //-------------------------------------------------------- boot sequencer
    reg go = 1'b0;
    reg started = 1'b0;

    // Trigger once, after the MMCM has locked so the sensor's reference is
    // already stable and clean before reset_n is released.
    always @(posedge clk) begin
        go <= 1'b0;
        if (rst) started <= 1'b0;
        else if (mmcm_locked && !started) begin
            go      <= 1'b1;
            started <= 1'b1;
        end
    end

    cam_boot_seq #(
        .CLK_HZ  (CLK_HZ),
        .STOP_AT (STOP_AT), .TRIGGERED (TRIGGERED), .EXPOSURE (EXPOSURE),
        .TESTPAT (TESTPAT)
    ) u_boot (
        .clk(clk), .rst(rst), .go(go),
        .busy(b_busy), .ready(b_ready), .failed(b_failed),
        .pll_timeout(b_plltmo), .reset_n(b_resetn),
        .spi_start(b_start), .spi_rw(b_rw), .spi_addr(b_addr),
        .spi_wdata(b_wdata),
        .spi_rdata(spi_rdata), .spi_busy(spi_busy), .spi_done(spi_done)
    );

    // Hold the sensor out of reset once the sequencer has let go of it.
    assign cam_reset_n = b_busy ? b_resetn : 1'b1;
    assign boot_ready  = b_ready;

    //------------------------------------------- read back reg 16 and reg 24
    localparam [3:0] P_WAIT = 4'd0, P_R16  = 4'd1, P_W16 = 4'd2,
                     P_R24  = 4'd3, P_W24  = 4'd4,
                     P_R112 = 4'd5, P_W112 = 4'd6,
                     P_RPT  = 4'd7, P_GAP  = 4'd8,
                     P_STRM = 4'd9, P_STRMW = 4'd10,
                     P_EXPO = 4'd11, P_EXPOW = 4'd12,
                     P_WIN  = 4'd13, P_WINW  = 4'd14;

    // Pending exposure write. This FSM already owns the SPI bus after boot (it
    // polls registers forever), so the write is injected into it rather than
    // adding a second master and an arbiter. It is taken in P_GAP, which is
    // where the FSM spends nearly all its time, so the latency is a few SPI
    // transactions at 1 MHz SCK -- tens of microseconds against a 6.86 ms frame.
    reg        expo_pend = 1'b0;
    reg [15:0] expo_hold = 16'd0;

    // Window geometry, clamped so the 4 x 24 kernel/row window stays on the array:
    // kernels 0..159, rows 0..1023, all bounds inclusive (reg 256's default x_end is
    // 159 for 160 kernels, and y_end 1023).
    wire [7:0]  wc  = (win_col8 < 8'd1) ? 8'd1 : (win_col8 > 8'd157) ? 8'd157 : win_col8;
    wire [7:0]  wr8 = (win_row8 < 8'd1) ? 8'd1 : (win_row8 > 8'd126) ? 8'd126 : win_row8;
    wire [12:0] w_ys = {2'b00, wr8, 3'b000} - 13'd8;
    wire [15:0] w_x  = {wc + 8'd2, wc - 8'd1};          // reg 259: {x_end, x_start}

    // What the sensor was last told. A difference from the inputs is a pending
    // update. A boot (rst) resets the sensor's registers to their defaults -- ROI 1
    // lost, ROI 0 active -- so rst records exactly that, and a window that is still
    // requested gets written again once the FSM is back in P_GAP.
    reg        app_win = 1'b0;
    reg [7:0]  app_col = 8'd0, app_row = 8'd0;
    wire       win_pend = (win != app_win) || (win && (wc != app_col || wr8 != app_row));
    reg [2:0]  wstep = 3'd0;
    reg [15:0] wx_l = 16'd0;
    reg [12:0] wy_l = 13'd0;
    reg        win_l = 1'b0;

    // The build-time window, as ROI 0 register values (all bounds inclusive).
    localparam        FIXWIN  = (WIN_X0 != 0) || (WIN_Y0 != 0) ||
                                (WIN_W != 1280) || (WIN_H != 1024);
    localparam [7:0]  FX_XS   = WIN_X0 / 8;
    localparam [7:0]  FX_XE   = (WIN_X0 + WIN_W) / 8 - 1;
    localparam [12:0] FX_YS   = WIN_Y0;
    localparam [12:0] FX_YE   = WIN_Y0 + WIN_H - 1;
    reg               fix_pend = FIXWIN;

    reg [3:0]  ps  = P_WAIT;
    reg [26:0] ptm = 27'd0;
    reg [15:0] r16 = 16'd0;
    reg [15:0] r24 = 16'd0;
    reg [15:0] r112 = 16'd0;   // 0x0007 once the LVDS drivers are powered up
    reg        msg_go;

    assign win_on = app_win && !win_pend && (ps != P_WIN) && (ps != P_WINW);

    always @(posedge clk) begin
        p_start <= 1'b0;
        msg_go  <= 1'b0;

        if (rst) begin
            ps  <= P_WAIT; ptm <= 27'd0; r16 <= 16'd0; r24 <= 16'd0; r112 <= 16'd0;
            p_wr <= 1'b0; p_wdata <= 16'd0; streaming <= 1'b0;
            expo_pend <= 1'b0; expo_hold <= 16'd0;
            app_win <= 1'b0; app_col <= wc; app_row <= wr8; wstep <= 3'd0;
            fix_pend <= FIXWIN;
        end else begin
            case (ps)
            P_WAIT: if (!b_busy && started) ps <= P_R16;
            P_R16:  if (!spi_busy) begin p_addr <= 9'd16; p_start <= 1'b1; ps <= P_W16; end
            P_W16:  if (spi_done) begin r16 <= spi_rdata; ps <= P_R24; end
            P_R24:  if (!spi_busy) begin p_addr <= 9'd24; p_start <= 1'b1; ps <= P_W24; end
            P_W24:  if (spi_done) begin r24 <= spi_rdata; ps <= P_R112; end
            P_R112: if (!spi_busy) begin p_addr <= 9'd112; p_start <= 1'b1; ps <= P_W112; end
            P_W112: if (spi_done) begin r112 <= spi_rdata; ps <= P_RPT; end
            P_RPT:  begin msg_go <= 1'b1; ptm <= 27'd0; ps <= P_GAP; end
            P_GAP:  if (expo_pend) begin
                        ps <= P_EXPO;          // exposure writes preempt polling
                    end else if (fix_pend) begin
                        // The build-time window goes in first: P_GAP is reached
                        // after the boot ROM and before the deferred stream start.
                        fix_pend <= 1'b0;
                        wstep    <= 3'd5;
                        ps       <= P_WIN;
                    end else if (win_pend) begin
                        // Snapshot the request, so an input that moves mid-sequence
                        // cannot mix two geometries; it is picked up next time.
                        win_l   <= win;
                        wx_l    <= w_x;
                        wy_l    <= w_ys;
                        app_win <= win; app_col <= wc; app_row <= wr8;
                        wstep   <= win ? 3'd0 : 3'd4;   // off: just re-select ROI 0
                        ps      <= P_WIN;
                    end else if (ptm == POLL_CY[26:0] - 27'd1) begin
                        ptm <= 27'd0;
                        ps  <= (stream_req && !streaming) ? P_STRM : P_R16;
                    end else ptm <= ptm + 27'd1;

            // reg 201 = exposure0. Datasheet Table 8: a reconfiguration takes
            // effect one frame later unless reg_seq_exposure_sync_mode is set,
            // so the frame that follows this write still shows the OLD value.
            // The sweep is monotonic precisely so that one-frame skew (and the
            // arbitrary capture phase) is identifiable in the data.
            P_EXPO:  if (!spi_busy) begin
                        p_addr  <= 9'd201;
                        p_wdata <= expo_hold;
                        p_wr    <= 1'b1;
                        p_start <= 1'b1;
                        ps      <= P_EXPOW;
                     end
            P_EXPOW: if (spi_done) begin
                        p_wr <= 1'b0;
                        expo_pend <= 1'b0;
                        ps <= P_GAP;
                     end
            // The window sequence. ROI 1 is deactivated before it is rewritten and
            // activated after, so the sensor never reads a half-written window:
            //   0: 195 <- 0001   ROI 0 (full frame) only
            //   1: 259 <- {x_end, x_start}
            //   2: 260 <- y_start
            //   3: 261 <- y_end
            //   4: 195 <- 0002 (window) or 0001 (full frame)
            // and, separately, the build-time window into ROI 0:
            //   5: 256 <- {x_end, x_start}   6: 257 <- y_start   7: 258 <- y_end
            P_WIN:   if (!spi_busy) begin
                        p_wr    <= 1'b1;
                        p_start <= 1'b1;
                        case (wstep)
                        3'd0: begin p_addr <= 9'd195; p_wdata <= 16'h0001; end
                        3'd1: begin p_addr <= 9'd259; p_wdata <= wx_l; end
                        3'd2: begin p_addr <= 9'd260; p_wdata <= {3'b000, wy_l}; end
                        3'd3: begin p_addr <= 9'd261; p_wdata <= {3'b000, wy_l + 13'd23}; end
                        3'd4: begin p_addr <= 9'd195;
                                    p_wdata <= win_l ? 16'h0002 : 16'h0001; end
                        3'd5: begin p_addr <= 9'd256; p_wdata <= {FX_XE, FX_XS}; end
                        3'd6: begin p_addr <= 9'd257; p_wdata <= {3'b000, FX_YS}; end
                        default: begin p_addr <= 9'd258; p_wdata <= {3'b000, FX_YE}; end
                        endcase
                        ps <= P_WINW;
                     end
            P_WINW:  if (spi_done) begin
                        p_wr <= 1'b0;
                        if (wstep == 3'd4 || wstep == 3'd7) ps <= P_GAP;
                        else begin wstep <= wstep + 3'd1; ps <= P_WIN; end
                     end
            // rom[41] of cam_boot_seq, performed here instead: enable the
            // sequencer and the sensor starts exposing and reading out.
            // MUST carry the mode bits. This used to write a bare 0x0801, which
            // clears 192[4] triggered_mode -- so the boot configured triggered
            // mode in cam_boot_seq's rom[31] and then this write immediately
            // took it away again, at the exact moment streaming started. The
            // sensor free-ran, frames kept arriving during the 3.3 s UART dump,
            // and later captures overwrote fmem while it was being read out:
            // one dumped frame ended up containing rows from several captures,
            // each landing on its own horizontal phase. That is the banding.
            P_STRM: if (!spi_busy) begin
                        p_wr <= 1'b1; p_addr <= 9'd192;
                        p_wdata <= 16'h0801 | (TRIGGERED ? 16'h0010 : 16'h0000);
                        p_start <= 1'b1; ps <= P_STRMW;
                    end
            P_STRMW: if (spi_done) begin
                        p_wr <= 1'b0; streaming <= 1'b1; ps <= P_R16;
                    end
            default: ps <= P_WAIT;
            endcase
            if (expo_req) begin
                expo_pend <= 1'b1;
                expo_hold <= expo_val;
            end
        end
    end

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= mmcm_locked;
        led[5]   <= b_ready;
        led[4]   <= b_failed;
        led[3]   <= b_plltmo;
        led[2]   <= r24[0];        // <-- THE GATE: sensor PLL reports lock
        led[1:0] <= 2'b00;
    end

    //------------------------------------------------------------------- UART
    //   bt=R pt=0 r16=0003 r24=0001 r112=0007
    localparam integer MSG_LEN = 39;

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [5:0] idx = 6'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    always @(*) begin
        // NOTE the width: idx and every literal here are 6 bits. MSG_LEN is 39,
        // and 5 bits cannot hold it -- MSG_LEN[4:0] wraps to 7, which truncated
        // the line to "bt=R pt" on the bench. Widen both together or neither.
        case (idx)
        6'd0 : ch = "b";  6'd1 : ch = "t";  6'd2 : ch = "=";
        6'd3 : ch = b_failed ? "F" : (b_ready ? "R" : "B");
        6'd4 : ch = " ";
        6'd5 : ch = "p";  6'd6 : ch = "t";  6'd7 : ch = "=";
        6'd8 : ch = b_plltmo ? "1" : "0";
        6'd9 : ch = " ";
        6'd10: ch = "r";  6'd11: ch = "1";  6'd12: ch = "6";  6'd13: ch = "=";
        6'd14: ch = hexd(r16[15:12]);
        6'd15: ch = hexd(r16[11:8]);
        6'd16: ch = hexd(r16[7:4]);
        6'd17: ch = hexd(r16[3:0]);
        6'd18: ch = " ";
        6'd19: ch = "r";  6'd20: ch = "2";  6'd21: ch = "4";  6'd22: ch = "=";
        6'd23: ch = hexd(r24[15:12]);
        6'd24: ch = hexd(r24[11:8]);
        6'd25: ch = hexd(r24[7:4]);
        6'd26: ch = hexd(r24[3:0]);
        6'd27: ch = " ";
        6'd28: ch = "r";  6'd29: ch = "1";  6'd30: ch = "1";  6'd31: ch = "2";
        6'd32: ch = "=";
        6'd33: ch = hexd(r112[15:12]);
        6'd34: ch = hexd(r112[11:8]);
        6'd35: ch = hexd(r112[7:4]);
        6'd36: ch = hexd(r112[3:0]);
        6'd37: ch = 8'h0D;
        6'd38: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0; idx <= 6'd0;
        end else if (!busy_msg) begin
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
