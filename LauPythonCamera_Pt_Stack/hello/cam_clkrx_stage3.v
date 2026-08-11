`timescale 1ns/1ps
//=============================================================================
// cam_clkrx_stage3 - STAGE 3: recover the sensor's forwarded clock, and only
// that. No deserialiser, no alignment, no data.
//
// THE GATE: measure the recovered word clock and get 72.000 MHz.
//
// This is the first design to receive anything on bank 13, and it deliberately
// receives exactly ONE pair -- clock_out (sensor pins 7/8). If the number comes
// out right, then in one step we have proven:
//
//   * the clock_out pair's two hand-soldered joints
//   * the differential routing and DIFF_TERM
//   * that an SRCC pin really can drive BUFIO + BUFR (iocheck proved it places;
//     this proves it WORKS)
//   * the sensor's serialiser is running at the rate the PLL implies
//
// and if it comes out wrong, the fault is confined to one pair rather than
// hidden among five.
//
// WHY 72 MHz. The sensor sends a DDR bit clock at 360 MHz on clock_out. BUFIO
// carries it as the I/O clock; BUFR divides by 5 to give the word clock, and
// 360 / 5 = 72 MHz -- one word per 10 bits, DDR (CAMERA_SENSOR_PROTOCOL.md 4.0).
// Measuring the BUFR output rather than the raw bit clock keeps the counter in
// easy fabric territory.
//
// HOW IT IS MEASURED. A free-running counter in the wordclk domain is sampled
// over a window of exactly CLK_HZ/1000 cycles of the 100 MHz reference, i.e.
// 1 ms. The count is therefore the frequency in kHz directly: 72000 = 72.000 MHz
// (0x11940). Sampling uses a 2FF-synchronised toggle handshake, so the two
// asynchronous domains never share a multi-bit value mid-update.
//
//-----------------------------------------------------------------------------
// !! BANK 13 GOES LIVE HERE. CHECK VBSEL FIRST. !!
//
// LVDS_25 and DIFF_TERM both require bank 13 VCCO = 2.5 V, which is set by the
// camera board strapping the Pt's VBSEL_A/B pins HIGH -- hardware, not this
// file. Alchitry: "failing to set the tri-voltage pins correctly could damage
// the FPGA." Verify BRINGUP_DMM_CHECKLIST.md section 1 (R10 pad 2 and R11 pad 2
// both 3.23-3.33 V) before loading this bitstream.
//
// Every previous bitstream in hello/ used zero bank-13 pins precisely so this
// question could be deferred. It cannot be deferred any further.
//-----------------------------------------------------------------------------
//
// The sensor side is cam_boot_stage1 with STOP_AT = 41: PLL locked, clocks
// enabled, LVDS drivers powered, sequencer still DISABLED. The sensor is not
// streaming image data -- clock_out and the training pattern are running, which
// is all this stage needs.
//
// LEDs
//   led[7]  heartbeat
//   led[6]  MMCM locked
//   led[5]  boot READY
//   led[4]  sensor PLL locked (reg 24 bit 0)
//   led[3]  reg 112 = 0x0007, LVDS drivers on
//   led[2]  RECOVERED CLOCK PRESENT (count non-zero)
//   led[1]  RECOVERED CLOCK IN SPEC   <-- THE GATE (72000 +/- 1 %)
//
// UART:  wc=011940 ok=1 pll=1 lv=1
//   wc = word-clock frequency in kHz, HEX. 0x011940 = 72000 = 72.000 MHz.
//=============================================================================
module cam_clkrx_stage3 #(
    parameter integer CLK_HZ  = 100_000_000,
    parameter integer BAUD    =     115_200,
    parameter integer WIN_CY  = CLK_HZ / 1000,   // 1 ms -> count is kHz
    parameter integer EXP_KHZ = 72_000,
    parameter integer TOL_KHZ = 720,              // +/- 1 %
    parameter integer REP_N   = 500               // report every 500 windows = 2 Hz
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    // ---- bank 13, LVDS_25 + DIFF_TERM: the forwarded bit clock ----
    input  wire       cam_clkout_p,
    input  wire       cam_clkout_n,

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
        .CLK_HZ  (CLK_HZ),
        .BAUD    (BAUD),
        .STOP_AT (41)          // LVDS drivers on; sequencer still off
    ) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(1'b0), .streaming(),   // these stages never stream
        .led(boot_led),
        .usb_tx(),             // this design drives the UART itself
        .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );
    // boot_led carries the sensor-side status bits we want to re-display.
    wire boot_mmcm  = boot_led[6];
    wire boot_ready = boot_led[5];
    wire boot_pll   = boot_led[2];

    //------------------------------------------------- recover the word clock
    // IBUFDS -> BUFIO (360 MHz bit clock) and BUFR /5 (72 MHz word clock).
    // Nothing is deserialised here; we only need something countable.
    wire bitclk_raw, wordclk;
    IBUFDS u_ibufds (.I(cam_clkout_p), .IB(cam_clkout_n), .O(bitclk_raw));
    BUFR #(.BUFR_DIVIDE("5"), .SIM_DEVICE("7SERIES")) u_bufr (
        .I(bitclk_raw), .O(wordclk), .CE(1'b1), .CLR(1'b0)
    );

    // Free-running counter in the recovered domain.
    reg [23:0] wc_cnt = 24'd0;
    always @(posedge wordclk) wc_cnt <= wc_cnt + 24'd1;

    //--------------------------------------------------------- measure it
    // Toggle handshake: the wordclk domain never hands over a value that is
    // mid-update, and the clk domain never samples one.
    reg        win_tog = 1'b0;          // clk domain: flips at each window end
    reg [1:0]  tog_m   = 2'b00;         // wordclk domain: 2FF sync of win_tog
    reg [23:0] snap    = 24'd0;         // wordclk domain: latched at each flip
    reg [1:0]  snap_ack = 2'b00;        // clk domain: 2FF sync back

    always @(posedge wordclk) begin
        tog_m <= {tog_m[0], win_tog};
        if (tog_m[1] != tog_m[0]) snap <= wc_cnt;   // edge of win_tog
    end

    reg [23:0] win     = 24'd0;
    reg [23:0] prev    = 24'd0;
    reg [23:0] khz     = 24'd0;
    reg        have    = 1'b0;
    reg [8:0]  rep     = 9'd0;
    reg        msg_go;

    always @(posedge clk) begin
        msg_go   <= 1'b0;
        snap_ack <= {snap_ack[0], tog_m[1]};

        if (rst) begin
            win <= 24'd0; prev <= 24'd0; khz <= 24'd0; have <= 1'b0;
            win_tog <= 1'b0; rep <= 9'd0;
        end else if (win == WIN_CY[23:0] - 24'd1) begin
            win     <= 24'd0;
            win_tog <= ~win_tog;         // ask the wordclk domain to snapshot
            // `snap` is from the PREVIOUS window boundary, which is exactly one
            // window old and therefore the measurement we want.
            khz     <= snap - prev;
            prev    <= snap;
            have    <= 1'b1;
            // Measure every 1 ms, but REPORT twice a second: a 26-char line at
            // 115200 takes ~2.3 ms, so reporting every window would ask the UART
            // for more than it can carry and most lines would be dropped.
            if (rep == REP_N[8:0] - 9'd1) begin
                rep    <= 9'd0;
                msg_go <= 1'b1;
            end else rep <= rep + 9'd1;
        end else begin
            win <= win + 24'd1;
        end
    end

    wire in_spec = have &&
                   (khz >= (EXP_KHZ[23:0] - TOL_KHZ[23:0])) &&
                   (khz <= (EXP_KHZ[23:0] + TOL_KHZ[23:0]));
    wire present = have && (khz != 24'd0);

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7] <= hb[25];
        led[6] <= boot_mmcm;
        led[5] <= boot_ready;
        led[4] <= boot_pll;
        led[3] <= boot_led[1];      // spare status from the boot stage
        led[2] <= present;
        led[1] <= in_spec;          // <-- THE GATE
        led[0] <= 1'b0;
    end

    //------------------------------------------------------------------- UART
    //   wc=011940 ok=1 pll=1
    localparam integer MSG_LEN = 22;

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
        6'd0 : ch = "w";  6'd1 : ch = "c";  6'd2 : ch = "=";
        6'd3 : ch = hexd(khz[23:20]);
        6'd4 : ch = hexd(khz[19:16]);
        6'd5 : ch = hexd(khz[15:12]);
        6'd6 : ch = hexd(khz[11:8]);
        6'd7 : ch = hexd(khz[7:4]);
        6'd8 : ch = hexd(khz[3:0]);
        6'd9 : ch = " ";
        6'd10: ch = "o";  6'd11: ch = "k";  6'd12: ch = "=";
        6'd13: ch = in_spec ? "1" : "0";
        6'd14: ch = " ";
        6'd15: ch = "p";  6'd16: ch = "l";  6'd17: ch = "l";  6'd18: ch = "=";
        6'd19: ch = boot_pll ? "1" : "0";
        6'd20: ch = 8'h0D;
        6'd21: ch = 8'h0A;
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
