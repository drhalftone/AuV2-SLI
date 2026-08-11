`timescale 1ns/1ps
//=============================================================================
// cam_rx_stage4 - STAGE 4: deserialise all five channels and align each one
// independently. Per-lane lock is the answer to "did my hand-soldering hold?"
//
// THE GATE: lk=1F -- all five lanes locked to the training word 0x3A6.
//
// This is the stage that localises a bad LVDS joint. Each of the five channels
// (d0..d3 + sync) trains on its own, so the result is not pass/fail for the
// whole link -- it names the lane:
//
//     lk=1F   all five locked. The receive path works end to end.
//     lk=1D   lane d1 never locked -> its pair is the suspect (sensor 11/12)
//     lk=0F   sync never locked    -> sensor 17/18
//     lk=00   nothing locked, but stage 3 said the clock is good -> look at
//             the training pattern being emitted, not the joints
//
// fl= is the companion: a lane that exhausted MAX_SLIP without finding a clean
// rotation, i.e. it tried all ten word boundaries and none produced 0x3A6.
// A lane that is FAILED rather than merely unlocked has a real signal problem.
//
// WHY THE TRAINING PATTERN IS THERE TO FIND. The boot runs with STOP_AT = 41:
// PLL locked, clocks enabled, LVDS drivers powered, but the SEQUENCER STILL
// DISABLED. An idle PYTHON sends the training word continuously on all five
// channels -- 0x3A6 on the data lanes (reg 116 default) and the TR code, also
// 0x3A6, on sync (CAMERA_SENSOR_PROTOCOL.md 5.1). So the alignment target is
// present precisely because the sensor is NOT streaming yet. Stage 4 is easier
// before stage 5, not after.
//
// THE FALLBACK IF A DATA LANE IS BAD. Register 32[5:4] selects 4, 2 or 1 LVDS
// channels. Only clock_out and sync are mandatory; d1..d3 are individually
// expendable at reduced frame rate. So lk=1D is a setback, not a wall.
//
//-----------------------------------------------------------------------------
// BANK 13: this design uses all SIX input pairs. VBSEL must be at 2.5 V --
// verified 2026-08-11, VBSEL_A 2.983 V / VBSEL_B 3.278 V.
//
// Note what is NOT here: lvds_clock_in (sensor 23/24) is never driven. We are
// in PLL mode, so the FPGA supplies 72 MHz CMOS on clk_pll and the sensor
// multiplies by 5 internally. See CAMERA_SENSOR_PROTOCOL.md 4.
//
// LEDs -- the per-lane map is the point:
//   led[7]  heartbeat
//   led[6]  ALIGNED: all five lanes locked   <-- THE GATE
//   led[5]  recovered word clock in spec (72 MHz +/- 1 %)
//   led[4]  sync  lane locked   (sensor 17/18)
//   led[3]  d3    lane locked   (sensor 15/16)
//   led[2]  d2    lane locked   (sensor 13/14)
//   led[1]  d1    lane locked   (sensor 11/12)
//   led[0]  d0    lane locked   (sensor  9/10)
//
// UART:  lk=1F fl=00 al=1 wc=011940
//=============================================================================
module cam_rx_stage4 #(
    parameter integer CLK_HZ  = 100_000_000,
    parameter integer BAUD    =     115_200,
    parameter integer WIN_CY  = CLK_HZ / 1000,   // 1 ms -> count is kHz
    parameter integer EXP_KHZ = 72_000,
    parameter integer TOL_KHZ = 720,
    parameter integer REP_N   = 500
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    // ---- bank 13, LVDS_25 + DIFF_TERM: six input pairs ----
    input  wire       cam_clkout_p, cam_clkout_n,
    input  wire [3:0] cam_d_p,      cam_d_n,
    input  wire       cam_sync_p,   cam_sync_n,

    // ---- single-ended control, banks 14/35 ----
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

    //--------------------------------------------- sensor side: boot to STOP 41
    wire [7:0] boot_led;
    cam_boot_stage1 #(
        .CLK_HZ (CLK_HZ), .BAUD (BAUD), .STOP_AT (41)
    ) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(1'b0), .streaming(),   // these stages never stream
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

    //---------------------------------------------------- receiver + alignment
    wire        wordclk;
    wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word;
    wire [4:0]  bitslip, lane_locked, lane_failed;
    wire        aligned;

    cam_lvds_rx u_rx (
        .cam_clkout_p(cam_clkout_p), .cam_clkout_n(cam_clkout_n),
        .cam_d_p(cam_d_p), .cam_d_n(cam_d_n),
        .cam_sync_p(cam_sync_p), .cam_sync_n(cam_sync_n),
        .bitslip(bitslip),
        .wordclk(wordclk),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word)
    );

    // cam_align lives in the recovered domain, so its reset must be released
    // there. The sensor's clock does not exist until its PLL locks, so this
    // counter simply does not advance until wordclk starts -- which is exactly
    // the behaviour we want on a cold start.
    reg [7:0] wc_rst_cnt = 8'd0;
    reg       wc_rst     = 1'b1;
    always @(posedge wordclk) begin
        if (wc_rst_cnt != 8'hFF) begin
            wc_rst_cnt <= wc_rst_cnt + 8'd1;
            wc_rst     <= 1'b1;
        end else wc_rst <= 1'b0;
    end

    cam_align u_align (
        .wordclk(wordclk), .rst(wc_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .bitslip(bitslip), .lane_locked(lane_locked),
        .aligned(aligned), .lane_failed(lane_failed)
    );

    //------------------------------------------ status across to the clk domain
    // Slow-changing status bits; a 2FF synchroniser per bit is sufficient and
    // the bits are independent, so a skewed sample is at worst one report stale.
    reg [4:0] lk_m, lk_s, fl_m, fl_s;
    reg [1:0] al_sync;
    always @(posedge clk) begin
        lk_m <= lane_locked;  lk_s <= lk_m;
        fl_m <= lane_failed;  fl_s <= fl_m;
        al_sync <= {al_sync[0], aligned};
    end

    //------------------------------------------------- measure the word clock
    reg [23:0] wc_cnt = 24'd0;
    always @(posedge wordclk) wc_cnt <= wc_cnt + 24'd1;

    reg        win_tog = 1'b0;
    reg [1:0]  tog_m   = 2'b00;
    reg [23:0] snap    = 24'd0;
    always @(posedge wordclk) begin
        tog_m <= {tog_m[0], win_tog};
        if (tog_m[1] != tog_m[0]) snap <= wc_cnt;
    end

    reg [23:0] win = 24'd0, prev = 24'd0, khz = 24'd0;
    reg [8:0]  rep = 9'd0;
    reg        have = 1'b0;
    reg        msg_go;

    always @(posedge clk) begin
        msg_go <= 1'b0;
        if (rst) begin
            win <= 24'd0; prev <= 24'd0; khz <= 24'd0;
            have <= 1'b0; win_tog <= 1'b0; rep <= 9'd0;
        end else if (win == WIN_CY[23:0] - 24'd1) begin
            win     <= 24'd0;
            win_tog <= ~win_tog;
            khz     <= snap - prev;
            prev    <= snap;
            have    <= 1'b1;
            if (rep == REP_N[8:0] - 9'd1) begin rep <= 9'd0; msg_go <= 1'b1; end
            else rep <= rep + 9'd1;
        end else win <= win + 24'd1;
    end

    wire wc_ok = have &&
                 (khz >= (EXP_KHZ[23:0] - TOL_KHZ[23:0])) &&
                 (khz <= (EXP_KHZ[23:0] + TOL_KHZ[23:0]));

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= al_sync[1];
        led[5]   <= wc_ok;
        led[4:0] <= lk_s;          // {sync, d3, d2, d1, d0}
    end

    //------------------------------------------------------------------- UART
    //   lk=1F fl=00 al=1 wc=011940
    localparam integer MSG_LEN = 28;

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
        case (idx)
        6'd0 : ch = "l";  6'd1 : ch = "k";  6'd2 : ch = "=";
        6'd3 : ch = hexd({3'b000, lk_s[4]});
        6'd4 : ch = hexd(lk_s[3:0]);
        6'd5 : ch = " ";
        6'd6 : ch = "f";  6'd7 : ch = "l";  6'd8 : ch = "=";
        6'd9 : ch = hexd({3'b000, fl_s[4]});
        6'd10: ch = hexd(fl_s[3:0]);
        6'd11: ch = " ";
        6'd12: ch = "a";  6'd13: ch = "l";  6'd14: ch = "=";
        6'd15: ch = al_sync[1] ? "1" : "0";
        6'd16: ch = " ";
        6'd17: ch = "w";  6'd18: ch = "c";  6'd19: ch = "=";
        6'd20: ch = hexd(khz[23:20]);
        6'd21: ch = hexd(khz[19:16]);
        6'd22: ch = hexd(khz[15:12]);
        6'd23: ch = hexd(khz[11:8]);
        6'd24: ch = hexd(khz[7:4]);
        6'd25: ch = hexd(khz[3:0]);
        6'd26: ch = 8'h0D;
        6'd27: ch = 8'h0A;
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
