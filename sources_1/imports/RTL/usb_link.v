`timescale 1ns/1ps
//==============================================================================
// usb_link.v -- bidirectional USB-serial subsystem for AuV2-SLI (FT2232H ch.B).
//
// Drop-in replacement for edid_reader: same status-telemetry inputs and the
// same usb_tx output (TX behaviour is byte-for-byte identical -- it still
// instantiates status_line + uart_tx), PLUS:
//   * usb_rx (P15) feeding uart_rx + uart_ctrl (the 0xA5 host command engine), and
//   * a 1-bit priority arbiter that shares the single uart_tx between the status
//     line and command replies. Command replies WIN (host is half-duplex and
//     waits on them); a status line may pause mid-line while a reply goes out,
//     then resumes -- harmless for the CRLF-terminated telemetry.
//
// Stage-2 taps (sli_ctrl, lut_loaded, table read ports) are exposed for the
// pixel datapath; they are defaulted in the VHDL component so the top can leave
// them open until the datapath is wired up.
//==============================================================================
module usb_link #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer WIN    = 50_000_000      // ~0.5 s status window
)(
    input  wire        clk100,
    input  wire [7:0]  led,
    input  wire [7:0]  dbg,
    input  wire [7:0]  mrg,
    input  wire [7:0]  tlp,
    input  wire [7:0]  tcnt,
    input  wire [7:0]  olp,
    input  wire        usb_rx,
    output wire        usb_tx,
    // G0: the vsync whose PERIOD is measured at 0x4A..0x52. Deliberately a
    // separate port from led[7]: that one carries `vsync` and feeds the N=
    // telemetry count, but genlock locks to out_vsync -- what is SENT to the
    // projector -- and measuring the wrong one would give a plausible number
    // for a signal nothing is locked to.
    input  wire        vs_meas,
    // Incoming HDMI timing from video_meas at the top level. Measured in the
    // PIXEL domain, so it cannot be produced here -- this module only forwards
    // it to uart_ctrl, the same way it forwards its own vs_per_w.
    input  wire [55:0] rx_meas,
    input  wire [17:0] rx_pixkhz,
    input  wire [7:0]  rx_diag,
    input  wire [7:0]  vfifo_rep,
    input  wire [31:0] ctl_diag,
    input  wire [31:0] vdp_diag,
    input  wire [31:0] pre_diag,
    input  wire [31:0] gb_diag,
    input  wire [31:0] evt_diag,
    input  wire [31:0] hpd_diag,
    input  wire [55:0] out_meas,
    input  wire [17:0] out_pixkhz,

    // ---- pin-state readback (reg 0x10): physical switches + post-override value ----
    input  wire [3:0]  phys_sw,          // raw newSW pins {R,G,B,orient} (async)
    input  wire [3:0]  eff_sw,           // effective_sw after the 0x13 override (pixel_clk)

    // ---- Stage-2 control / table taps (safe to leave open) ----
    output wire [7:0]  sli_ctrl,
    output wire        sli_ctrl_en,
    output wire        lut_loaded,
    input  wire [7:0]  corr_addr,  output wire [7:0] corr_dout,
    input  wire [9:0]  lut_addr,   output wire [7:0] lut_dout,
    input  wire [10:0] lutv_addr,  output wire [7:0] lutv_dout,

    // ---- captured-EDID read port (rdtbl TGT_EDID) -> edid_merge's 3rd port ----
    output wire [7:0]  edid_rd_addr,
    input  wire [7:0]  edid_rd_data,
    output wire [7:0]  esrv_rd_addr,
    input  wire [7:0]  esrv_rd_data,

    // ---- captured camera-line read port (rdtbl TGT_CAM_LINE) -> cam_line_buf ----
    // On the Au (no LVDS receiver) tie cam_line_data to 0 at the top; the target still
    // works and reads zeros. On the Pt this connects to cam_line_buf's read port.
    output wire [10:0] cam_line_addr,
    input  wire [7:0]  cam_line_data,

    // ---- offline mode decision (regs 0x20..0x2A). Caller supplies these already
    // synced into clk100; they are quasi-static (change only on a new EDID parse).
    input  wire [3:0]  mode_idx_i,
    input  wire        mode_valid_i,
    input  wire        mode_edid_ok_i,
    input  wire [7:0]  mode_refr_i,
    input  wire [11:0] mode_hact_i,
    input  wire [11:0] mode_vact_i,
    input  wire [16:0] mode_pclk_i,
    input  wire [13:0] mode_supp_i,

    // ---- radiometric transfer LUT into the pixel datapath (combinational) ----
    // pattern_gen presents the raw cosine on corr_pat_addr and consumes the corrected
    // value on corr_pat_dout in the SAME pipeline stage, so this path is async by design.
    input  wire [7:0]  corr_pat_addr,
    output wire [7:0]  corr_pat_dout,

    // ---- MODEFORCE (reg 0x14): {7:force_en, 3..0:idx} ----
    output wire [7:0]  mode_force,

    // ---- LINKCTL (reg 0x15): self-timed host/projector disconnect pulse ----
    output wire        link_drop_host,   // force hdmi_rx_hpa low (host re-negotiates)
    output wire        link_drop_proj,   // tristate TMDS out (projector loses signal)

    // reg 0x16 CAMSIM: host-driven camera-ready, {7:rdy_en, 0:rdy_val}
    output wire [7:0]  cam_sim,

    // ---- PYTHON 1300 camera (regs 0x30..0x38) ----
    // The SPI master lives in here, right next to the control plane that drives it,
    // so the top level only ever sees the sensor's physical pins.
    output wire        cam_sck,
    output wire        cam_mosi,
    output wire        cam_ss_n,
    input  wire        cam_miso,
    output wire        cam_reset_n,
    output wire [2:0]  cam_trigger,
    input  wire [1:0]  cam_monitor,
    input  wire [223:0] cam_stat_i,      // M4 0x3A..0x41, G0 timestamps 0x42..0x49,
                                         // G1 sync count 0x58..0x59, G3 delay 0x5A..0x5D
    // M6a: a SECOND source of 0xA5 bytes, arriving over the FT601 instead of the
    // UART. Merged below rather than muxed: the two are alternative transports
    // for one protocol, never both mid-command at once, and the receiver already
    // resynchronises on SYNC plus an inter-byte timeout if they ever collide.
    input  wire [7:0]  rx2_data,
    input  wire        rx2_valid,
    // M6b: the reply direction for those bytes. rpl_full is real backpressure --
    // it paces uart_ctrl's producer handshake directly.
    output wire [7:0]  rpl_byte,
    output wire        rpl_we,
    input  wire        rpl_full
);
    // ---- power-up reset ----
    reg [3:0] rstcnt = 4'd0;
    reg       rst    = 1'b1;
    always @(posedge clk100) begin
        if (rstcnt != 4'hF) begin rstcnt <= rstcnt + 4'h1; rst <= 1'b1; end
        else rst <= 1'b0;
    end

    // ---- CDC sample of the async status inputs into clk100 (as in edid_reader) ----
    reg [7:0] led_d0=0, led_s=0, dbg_d0=0, dbg_s=0, mrg_d0=0, mrg_s=0;
    reg [7:0] tlp_d0=0, tlp_s=0, tcnt_d0=0, tcnt_s=0, olp_d0=0, olp_s=0;
    reg vs0=0, vs1=0, vs2=0;
    always @(posedge clk100) begin
        led_d0<=led; led_s<=led_d0; dbg_d0<=dbg; dbg_s<=dbg_d0; mrg_d0<=mrg; mrg_s<=mrg_d0;
        tlp_d0<=tlp; tlp_s<=tlp_d0; tcnt_d0<=tcnt; tcnt_s<=tcnt_d0; olp_d0<=olp; olp_s<=olp_d0;
        vs0<=led[7]; vs1<=vs0; vs2<=vs1;
    end
    wire vs_rise = vs1 & ~vs2;

    // Separate 2FF sync + edge for the measured vsync (pixel clock -> clk100).
    reg vm0=0, vm1=0, vm2=0;
    always @(posedge clk100) begin vm0<=vs_meas; vm1<=vm0; vm2<=vm1; end
    wire vm_rise = vm1 & ~vm2;

    // 2FF sync of the quasi-static switch/override bits into clk100 (reg 0x10).
    reg [3:0] psw0=0, psw1=0, esw0=0, esw1=0;
    always @(posedge clk100) begin
        psw0 <= phys_sw; psw1 <= psw0;
        esw0 <= eff_sw;  esw1 <= esw0;
    end

    // ---- status window + per-window vsync (frame) counter ----
    reg [31:0] win = 0; reg [15:0] vs_run = 0, vs_lat = 0; reg stat_tick = 0;
    always @(posedge clk100) begin
        stat_tick <= 1'b0;
        if (win >= WIN-1) begin win<=0; vs_lat<=vs_run; vs_run<=0; stat_tick<=1'b1; end
        else begin win<=win+1; if (vs_rise) vs_run<=vs_run+1'b1; end
    end

    //---- vsync PERIOD and its jitter (genlock G0) --------------------------
    // vs_lat above is an edge COUNT per window -- the `N=` field. It answers
    // "is vsync alive" and nothing else: at 120 Hz it reads 51 or 52 and
    // dithers, so it cannot resolve a period to better than ~2%, and it cannot
    // see jitter at all.
    //
    // Genlock needs the period itself, and needs to know how much it moves.
    // This is the master clock the camera would be locked to, so its stability
    // IS the achievable lock quality -- there is no point specifying a 5 us
    // trigger delay against a master that wanders by more than that.
    //
    // Counted at 100 MHz: 10 ns resolution, and 24 bits reaches 167 ms so every
    // rate from 6 Hz upward fits. Saturating rather than wrapping, because a
    // wrapped period reads as a plausible short one.
    //
    // ARMED on the first edge so the first interval -- which is measured from
    // reset, not from an edge -- is discarded rather than published as a
    // spuriously short period.
    //
    // min/max accumulate over one status window and reset with it, so each read
    // is a fresh window rather than a high-water mark that only ever widens.
    // All three values are latched together at the tick: read as three separate
    // bytes over a UART, an unlatched counter would tear and produce a period
    // that never occurred.
    reg [23:0] vsp_cnt = 24'd0, vsp_last = 24'd0;
    reg [23:0] vsp_min = 24'hFFFFFF, vsp_max = 24'd0;
    reg [23:0] vsp_last_p = 24'd0, vsp_min_p = 24'd0, vsp_max_p = 24'd0;
    reg        vsp_armed = 1'b0;
    always @(posedge clk100) begin
        if (vm_rise) begin
            vsp_cnt <= 24'd0;
            if (vsp_armed) begin
                vsp_last <= vsp_cnt;
                if (vsp_cnt < vsp_min) vsp_min <= vsp_cnt;
                if (vsp_cnt > vsp_max) vsp_max <= vsp_cnt;
            end
            vsp_armed <= 1'b1;
        end else if (vsp_cnt != 24'hFFFFFF) begin
            vsp_cnt <= vsp_cnt + 24'd1;
        end
        if (stat_tick) begin
            vsp_last_p <= vsp_last;
            vsp_min_p  <= vsp_min;
            vsp_max_p  <= vsp_max;
            vsp_min <= 24'hFFFFFF;
            vsp_max <= 24'd0;
        end
    end
    wire [71:0] vs_per_w = {vsp_max_p, vsp_min_p, vsp_last_p};

    //---- MAX USABLE EXPOSURE for the CURRENT frame rate (regs 0x53..0x57) ---
    // "What is the longest exposure I can ask for right now?" answered by the
    // FPGA, from the vsync period it just measured and the sensor overhead that
    // was measured on this silicon.
    //
    //     max_exposure = vsync_period - 44.1 us (gap) - 10 us (margin)
    //
    // THE 44.1 us IS MEASURED, NOT FROM THE DATASHEET. Sweeping the trigger
    // period against exposure gave min_period = exposure + 44.1 us with a slope
    // of 1.0015 over a 6.0..8.0 ms span -- a constant, to within the 5 us search
    // resolution. At 120 Hz this formula returns 8279 us against the clamp of
    // 8280 us that was previously found by walking into the cliff. Reproducing a
    // known-good number is what makes it trustworthy.
    //
    // IT MUST FAIL SAFE, and that is the whole point of per_ok. vsp_last_p
    // SATURATES at 0xFFFFFF when no vsync edges arrive. Fed naively into the
    // subtraction that reports a 167 ms "maximum exposure" -- and commanding an
    // exposure longer than the frame period WEDGES the sensor until the FPGA is
    // reconfigured. A feature whose job is to prevent that must not become the
    // thing that causes it. So the period has to be inside a plausible band or
    // the answer is reported invalid and zero.
    //
    // THE EXPOSURE REGISTER RUNS OUT BEFORE THE FRAME DOES at low rates. Opcode
    // 1 takes 16 bits, so the largest settable exposure is 65535 x 375 ns =
    // 24.576 ms. Below ~40.7 Hz the binding limit is the REGISTER, not the frame
    // period, and the reply says which -- otherwise a user at 30 Hz is told
    // 88755 and writes a silently truncated value.
    //
    // Reported in EXPOSURE REGISTER UNITS so the host writes it straight back
    // with opcode 1: no conversion, no rounding at the one boundary where a
    // rounding error wedges the part.
    localparam [23:0] GAP_TICKS     = 24'd4410;      // 44.1 us, measured
    localparam [23:0] MARGIN_TICKS  = 24'd1000;      // 10 us
    localparam [23:0] RESERVE_TICKS = GAP_TICKS + MARGIN_TICKS;
    localparam [23:0] PER_MIN = 24'd200_000;         // 2 ms  -> 500 Hz
    localparam [23:0] PER_MAX = 24'd8_000_000;       // 80 ms -> 12.5 Hz

    // PIPELINED, three stages. As a single combinational chain -- subtract, then a
    // 24x15 multiply, then compare -- this took WNS from +0.082 ns to +0.014 ns.
    // 14 ps is not margin. The value is read over a 115200 baud serial link and
    // only changes once per status window, so spending three clocks on it costs
    // literally nothing and hands the slack back.
    // WHICH PERIOD THE CAMERA IS ACTUALLY TRIGGERED AT.
    //
    // This used vsp_last_p -- the VSYNC period -- unconditionally, which is right
    // only when the camera is genlocked to the display. Free-running, the sensor is
    // triggered by trig_per (opcode 2) and the vsync period says nothing about its
    // budget. At a 60 Hz display with the camera free-running at 120 Hz the old
    // answer was 16641 us against a real ceiling near 8279 us, and WRITING IT WEDGES
    // THE SENSOR.
    //
    // host/max_exposure.py compensated by comparing the two periods host-side and
    // warning. That inference broke the moment genlock existed: the camera period
    // register is 16 bits holding period/16 and cannot represent anything below
    // 68.67 Hz, so a genlocked 60 Hz reads as 475 Hz, the tool declared NOT GENLOCKED,
    // and recommended 5461 units where 44297 was available -- safe direction, but 8x
    // the light budget thrown away.
    //
    // Decide it here instead, where both periods and the genlock state are known for
    // certain. gl_live (NOT gl_en) is the right selector: it is whether ext_sync is
    // actually ARRIVING, so a display that goes away falls back with the answer.
    // Both are already in 10 ns ticks, so no conversion.
    wire        gl_live_s  = cam_stat_i[169];
    wire [23:0] trig_per_s = cam_stat_i[215:192];
    wire [23:0] eff_per    = gl_live_s ? vsp_last_p : trig_per_s;

    reg        per_ok_r = 1'b0;
    reg [23:0] usable_r = 24'd0;
    reg [43:0] mul_r    = 44'd0;
    reg        src_r    = 1'b0;
    always @(posedge clk100) begin
        per_ok_r <= (eff_per >= PER_MIN) && (eff_per <= PER_MAX);
        usable_r <= (eff_per > RESERVE_TICKS)
                      ? (eff_per - RESERVE_TICKS) : 24'd0;
        mul_r    <= usable_r * 44'd27962;
        src_r    <= gl_live_s;
    end
    wire        per_ok  = per_ok_r;
    wire [23:0] usable  = usable_r;
    // ticks (10 ns) -> exposure units (375 ns) is a divide by 37.5, done as
    // x 2/75 ~= x 27962 >> 20. 27962*75/2 = 1048575 against 2^20 = 1048576, so
    // the error is about 1 ppm -- far below the 375 ns quantisation itself.
    wire [23:0] mexp_full = mul_r[43:20];
    wire        mexp_rlim = per_ok && (mexp_full > 24'd65535);
    wire [15:0] mexp      = (!per_ok)  ? 16'd0
                          : (mexp_rlim ? 16'hFFFF : mexp_full[15:0]);
    // The reserve is published too: if the FPGA says "max is X" and the host
    // writes X, a wrong reserve wedges the sensor. Better inspectable than
    // buried in a bitstream.
    // src: 1 = computed from the VSYNC period (genlocked), 0 = from trig_per
    // (free-running). Published so the host READS which clock the answer describes
    // instead of deducing it from two registers that can disagree.
    wire [39:0] maxexp_w = {RESERVE_TICKS[15:0], per_ok, mexp_rlim, src_r, 5'd0, mexp};

    // ---- producers ----
    wire [7:0] s_data;  wire s_send, s_busy;        // status_line producer
    wire [7:0] c_data;  wire c_send, c_active;      // uart_ctrl producer
    wire       u_busy;                              // shared uart_tx busy

    // ---- received bytes, from EITHER transport ----
    // Declared here rather than beside uart_rx because the arbiter and the reply
    // routing below both depend on which transport a command arrived on.
    wire [7:0] rx_uart;  wire rx_uvalid;
    // Serial byte OR FT601 byte -- whichever arrives. The UART wins a same-cycle
    // tie, which cannot happen in practice and costs nothing to define.
    wire [7:0] rx_data  = rx_uvalid ? rx_uart : rx2_data;
    wire       rx_valid = rx_uvalid | rx2_valid;

    // ---- M6b: send the reply back the way the command came in -------------
    //
    // Every response byte uart_ctrl produces -- short ACKs from S_RESP and
    // 1,280-byte table readbacks from S_RTAB alike -- leaves through one producer
    // handshake, so this is the only place that needs to know about two
    // transports. uart_ctrl itself is untouched.
    //
    // ROUTING BY SOURCE IS WHAT MAKES THE TABLE CASE WORK. The handshake is paced
    // by tx_busy, and the UART is 115200 baud -- 87 us per byte. Feed a table
    // readback through it and the reply takes 111 ms. Pacing a USB3-originated
    // reply on the FIFO instead makes the same readback tens of microseconds,
    // while a serial-originated one still goes out the UART at UART speed, which
    // is what a serial client wants.
    //
    // SYNC is the latch point because it is the one byte that unambiguously
    // starts a command, and every reply belongs to the command before it.
    reg src_ft = 1'b0;
    always @(posedge clk100) begin
        if (rst) src_ft <= 1'b0;
        else if (rx_valid && rx_data == 8'hA5) src_ft <= ~rx_uvalid;
    end

    // ---- 1-bit priority arbiter (ctrl wins). Switch only between bytes (~u_busy). ----
    reg owner = 1'b0;                               // 0 = status, 1 = ctrl
    always @(posedge clk100) begin
        if (rst)        owner <= 1'b0;
        // c_active must not claim the UART for a reply that is going out over the
        // FT601 instead -- see src_ft below.
        else if (!u_busy) owner <= c_active & ~src_ft;
    end
    assign rpl_byte = c_data;
    assign rpl_we   = c_send & src_ft;

    wire        s_tx_busy = owner ? 1'b1   : u_busy;   // back-pressure non-owner
    // When the command came in over the FT601 the control engine is paced by the
    // reply FIFO and never touches the arbiter, so status telemetry keeps
    // flowing on the serial port the whole time.
    wire        c_tx_busy = src_ft ? rpl_full : (owner ? u_busy : 1'b1);
    wire [7:0]  tx_data   = owner ? c_data : s_data;
    wire        tx_send   = owner ? (c_send & ~src_ft) : s_send;
    wire        s_go      = stat_tick & ~c_active & ~owner;   // don't start a line if ctrl is busy

    // ---- status line (telemetry) ----
    status_line i_stat (
        .clk(clk100), .go(s_go),
        .led_s(led_s), .dbg_s(dbg_s), .mrg(mrg_s), .tlp(tlp_s), .tcnt(tcnt_s), .olp(olp_s), .vs_lat(vs_lat),
        .tx_data(s_data), .tx_send(s_send), .tx_busy(s_tx_busy), .busy(s_busy)
    );

    // ---- receive + command engine ----
    // rx_uart / rx_uvalid / rx_data / rx_valid are declared above, with the
    // reply routing that depends on them.
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(115200)) i_urx (
        .clk(clk100), .rst(rst), .rx(usb_rx), .data(rx_uart), .valid(rx_uvalid)
    );
    //---------------------------------------------------------------- PYTHON 1300
    // Declared BEFORE i_ctrl: Verilog would otherwise implicitly declare each of these
    // as a 1-bit wire at first use in the port map, and the real declaration below
    // would then collide with it.
    wire [8:0]  cam_spi_addr;
    wire        cam_spi_rw;
    wire [15:0] cam_spi_wdata;
    wire        cam_spi_start;
    wire [15:0] cam_spi_rdata;
    wire        cam_spi_busy, cam_spi_done;
    wire [7:0]  cam_gpio;

    // monitor pins are asynchronous to clk100 -- 2FF sync before a register read sees them.
    reg [1:0] mon_d0 = 2'd0, mon_s = 2'd0;
    always @(posedge clk100) begin mon_d0 <= cam_monitor; mon_s <= mon_d0; end
    wire [7:0] cam_gpio_in = {6'b0, mon_s};

    // ---- boot sequencer (task #6) + SPI-master ownership arbitration (task #12) ----
    // cam_boot_seq drives the ROM register upload. While it is busy it OWNS the SPI master
    // and cam_reset_n; otherwise the host mailbox (uart_ctrl, reg 0x30..0x37) does. The
    // sensor's SPI outputs (rdata/busy/done) fan out to both requesters -- harmless, since
    // the host is expected to idle during boot (it polls reg 0x39, not the SPI mailbox).
    wire        boot_go, boot_busy, boot_ready, boot_failed, boot_pll_timeout, boot_reset_n;
    wire        boot_spi_start, boot_spi_rw;
    wire [8:0]  boot_spi_addr;
    wire [15:0] boot_spi_wdata;

    wire        spim_start = boot_busy ? boot_spi_start : cam_spi_start;
    wire        spim_rw    = boot_busy ? boot_spi_rw    : cam_spi_rw;
    wire [8:0]  spim_addr  = boot_busy ? boot_spi_addr  : cam_spi_addr;
    wire [15:0] spim_wdata = boot_busy ? boot_spi_wdata : cam_spi_wdata;

    // 1 MHz sck. The datasheet allows 10 MHz, but the sensor's max SPI rate scales with
    // its input clock -- which is NOT running during first bring-up. 1 MHz sidesteps the
    // question entirely and costs ~30 us per transaction: nothing next to the 115200-baud
    // UART frame that carries the request.
    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(1_000_000)) i_cam_spi (
        .clk(clk100), .rst(rst),
        .start(spim_start), .rw(spim_rw),
        .addr(spim_addr),   .wdata(spim_wdata),
        .rdata(cam_spi_rdata), .busy(cam_spi_busy), .done(cam_spi_done),
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(cam_miso)
    );

    cam_boot_seq #(.CLK_HZ(CLK_HZ)) i_boot (
        .clk(clk100), .rst(rst), .go(boot_go),
        .busy(boot_busy), .ready(boot_ready),
        .failed(boot_failed), .pll_timeout(boot_pll_timeout),
        .reset_n(boot_reset_n),
        .spi_start(boot_spi_start), .spi_rw(boot_spi_rw),
        .spi_addr(boot_spi_addr),   .spi_wdata(boot_spi_wdata),
        .spi_rdata(cam_spi_rdata),  .spi_busy(cam_spi_busy), .spi_done(cam_spi_done)
    );

    // reg 0x37 = {reset_n, 4'b0, trigger[2:0]}. It resets to 0x00, so reset_n = 0 and the
    // sensor is HELD IN RESET until the host releases it -- matching the board's external
    // pulldown, which holds the part in reset through the whole FPGA configuration window.
    // While the boot sequencer is busy IT drives reset_n (it pulses the sensor as part of
    // the ROM flow); otherwise reg 0x37 bit 7 does.
    assign cam_reset_n = boot_busy ? boot_reset_n : cam_gpio[7];
    assign cam_trigger = cam_gpio[2:0];

    uart_ctrl #(.CLK_HZ(CLK_HZ)) i_ctrl (
        .clk(clk100), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(c_data), .tx_send(c_send), .tx_busy(c_tx_busy), .tx_active(c_active),
        .led(led_s), .pins({esw1, psw1}),
        .sli_ctrl(sli_ctrl),
        .cam_sim(cam_sim), .sli_ctrl_en(sli_ctrl_en), .lut_loaded(lut_loaded),
        .corr_addr(corr_addr), .corr_dout(corr_dout),
        .lut_addr(lut_addr),   .lut_dout(lut_dout),
        .lutv_addr(lutv_addr), .lutv_dout(lutv_dout),
        .edid_rd_addr(edid_rd_addr), .edid_rd_data(edid_rd_data),
        .esrv_rd_addr(esrv_rd_addr), .esrv_rd_data(esrv_rd_data),
        .cam_line_addr(cam_line_addr), .cam_line_data(cam_line_data),
        .mode_idx_i(mode_idx_i), .mode_valid_i(mode_valid_i),
        .mode_edid_ok_i(mode_edid_ok_i), .mode_refr_i(mode_refr_i),
        .mode_hact_i(mode_hact_i), .mode_vact_i(mode_vact_i),
        .mode_pclk_i(mode_pclk_i), .mode_supp_i(mode_supp_i),
        .corr_pat_addr(corr_pat_addr), .corr_pat_dout(corr_pat_dout),
        .mode_force(mode_force),
        .link_drop_host(link_drop_host), .link_drop_proj(link_drop_proj),
        // ---- PYTHON 1300 mailbox ----
        .cam_spi_addr(cam_spi_addr), .cam_spi_rw(cam_spi_rw),
        .cam_spi_wdata(cam_spi_wdata), .cam_spi_start(cam_spi_start),
        .cam_stat_i(cam_stat_i),
        .vs_per_i(vs_per_w),
        .rx_meas_i(rx_meas),
        .rx_pixkhz_i(rx_pixkhz),
        .rx_diag_i(rx_diag),
        .vfifo_rep_i(vfifo_rep),
        .ctl_diag_i(ctl_diag),
        .vdp_diag_i(vdp_diag),
        .pre_diag_i(pre_diag),
        .gb_diag_i(gb_diag),
        .evt_diag_i(evt_diag),
        .hpd_diag_i(hpd_diag),
        .out_meas_i(out_meas),
        .out_pixkhz_i(out_pixkhz),
        .maxexp_i(maxexp_w),
        .cam_spi_rdata(cam_spi_rdata), .cam_spi_busy(cam_spi_busy),
        .cam_spi_done(cam_spi_done),
        .cam_gpio(cam_gpio), .cam_gpio_in(cam_gpio_in),
        // ---- boot sequencer control (reg 0x39) ----
        .cam_boot_go(boot_go),
        .cam_boot_stat({boot_ready, boot_busy, boot_failed, boot_pll_timeout})
    );

    // ---- shared transmitter ----
    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(115200)) i_utx (
        .clk(clk100), .rst(rst), .data(tx_data), .send(tx_send),
        .tx(usb_tx), .busy(u_busy)
    );
endmodule
