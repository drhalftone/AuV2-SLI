`timescale 1ns/1ps
//=============================================================================
// cam_rxdbg - stage 4 with the raw deserialised word from every lane, instead
// of just lock/fail. Diagnoses WHY a lane will not align.
//
// cam_align reports fl=1 for a lane that tried all ten bitslip rotations
// without ever seeing the training word. That is a strong signal but a blunt
// one: it says "no valid data" without saying what arrived instead. The raw
// word says exactly what arrived, and the four failure modes look nothing alike:
//
//   rotation of 0x059  ->  P/N SWAPPED on that pair.
//                          0x3A6 = 11 1010 0110. Invert every bit -- which is
//                          precisely what a polarity swap does -- and you get
//                          00 0101 1001 = 0x059. cam_align searches the ten
//                          ROTATIONS of 0x3A6, and no rotation of a word equals
//                          a rotation of its complement, so an inverted pair
//                          fails all ten and looks identical to a dead lane.
//                          CAMERA_IO_MAP.md section 6 lists N->N / P->P polarity
//                          as "checked", but that was against the netlist, never
//                          against hardware.
//
//   0x000 or 0x3FF     ->  pair shorted together, or no signal at all: the
//                          receiver sees a constant, so every rotation is the
//                          same constant.
//
//   changing every read->  noise or a marginal joint. DC continuity can be
//                          perfect and still fail at 720 Mbps.
//
//   rotation of 0x3A6  ->  the data is fine and the ALIGNER is at fault --
//                          which would be our bug, not the board's.
//
// A locked lane should read a steady 0x3A6, since cam_align stops slipping once
// it locks. So the healthy lanes double as a control: if four lanes read 0x3A6
// and one reads something else, the something else is the whole story.
//
// SAMPLING. The words live in the 72 MHz recovered domain and change every word
// clock. A free-running counter there latches all five into holding registers
// once every ~14.5 ms, so what the 100 MHz side reads is stable for millions of
// cycles and cannot tear mid-word.
//
// UART:  d0=059 d1=3A6 d2=3A6 d3=3A6 sy=3A6
//=============================================================================
module cam_rxdbg #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter integer REP_CY = CLK_HZ / 2,       // report twice a second
    // 15.0 -> 72 MHz reference, 720 Mbps/lane (nominal).
    // 30.0 -> 36 MHz reference, 360 Mbps/lane -- the margin test: the data eye
    // doubles, so a lane that fails only for lack of margin will start locking.
    parameter real    PLL_DIV = 15.000
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
    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    wire [7:0] boot_led;
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(41), .PLL_DIV(PLL_DIV)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

    wire       wordclk;
    wire [9:0] d0_word, d1_word, d2_word, d3_word, sync_word;
    wire [4:0] bitslip, lane_locked, lane_failed;
    wire       aligned;

    cam_lvds_rx u_rx (
        .cam_clkout_p(cam_clkout_p), .cam_clkout_n(cam_clkout_n),
        .cam_d_p(cam_d_p), .cam_d_n(cam_d_n),
        .cam_sync_p(cam_sync_p), .cam_sync_n(cam_sync_n),
        .bitslip(bitslip), .wordclk(wordclk),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word)
    );

    reg [7:0] wc_rst_cnt = 8'd0;
    reg       wc_rst     = 1'b1;
    always @(posedge wordclk) begin
        if (wc_rst_cnt != 8'hFF) begin wc_rst_cnt <= wc_rst_cnt + 8'd1; wc_rst <= 1'b1; end
        else wc_rst <= 1'b0;
    end

    cam_align u_align (
        .wordclk(wordclk), .rst(wc_rst),
        .d0_word(d0_word), .d1_word(d1_word), .d2_word(d2_word),
        .d3_word(d3_word), .sync_word(sync_word),
        .bitslip(bitslip), .lane_locked(lane_locked),
        .aligned(aligned), .lane_failed(lane_failed)
    );

    //-------------------------------------------- hold the words, then cross
    reg [19:0] hold_div = 20'd0;
    reg [9:0]  h0 = 10'd0, h1 = 10'd0, h2 = 10'd0, h3 = 10'd0, hs = 10'd0;
    always @(posedge wordclk) begin
        hold_div <= hold_div + 20'd1;
        if (hold_div == 20'd0) begin
            h0 <= d0_word;  h1 <= d1_word;  h2 <= d2_word;
            h3 <= d3_word;  hs <= sync_word;
        end
    end

    // Stable for ~14.5 ms at a time, so a plain sample in the clk domain is safe.
    reg [9:0] s0, s1, s2, s3, ss;
    reg [4:0] lk_s, fl_s;
    always @(posedge clk) begin
        s0 <= h0;  s1 <= h1;  s2 <= h2;  s3 <= h3;  ss <= hs;
        lk_s <= lane_locked;  fl_s <= lane_failed;
    end

    reg [26:0] rtm = 27'd0;
    reg        msg_go;
    always @(posedge clk) begin
        msg_go <= 1'b0;
        if (rst) rtm <= 27'd0;
        else if (rtm == REP_CY[26:0] - 27'd1) begin rtm <= 27'd0; msg_go <= 1'b1; end
        else rtm <= rtm + 27'd1;
    end

    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;
    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= aligned;
        led[5]   <= 1'b0;
        led[4:0] <= lk_s;
    end

    //------------------------------------------------------------------- UART
    //   d0=059 d1=3A6 d2=3A6 d3=3A6 sy=3A6
    localparam integer MSG_LEN = 36;

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
        6'd0 : ch = "d";  6'd1 : ch = "0";  6'd2 : ch = "=";
        6'd3 : ch = hexd({2'b00, s0[9:8]});
        6'd4 : ch = hexd(s0[7:4]);
        6'd5 : ch = hexd(s0[3:0]);
        6'd6 : ch = " ";
        6'd7 : ch = "d";  6'd8 : ch = "1";  6'd9 : ch = "=";
        6'd10: ch = hexd({2'b00, s1[9:8]});
        6'd11: ch = hexd(s1[7:4]);
        6'd12: ch = hexd(s1[3:0]);
        6'd13: ch = " ";
        6'd14: ch = "d";  6'd15: ch = "2";  6'd16: ch = "=";
        6'd17: ch = hexd({2'b00, s2[9:8]});
        6'd18: ch = hexd(s2[7:4]);
        6'd19: ch = hexd(s2[3:0]);
        6'd20: ch = " ";
        6'd21: ch = "d";  6'd22: ch = "3";  6'd23: ch = "=";
        6'd24: ch = hexd({2'b00, s3[9:8]});
        6'd25: ch = hexd(s3[7:4]);
        6'd26: ch = hexd(s3[3:0]);
        6'd27: ch = " ";
        6'd28: ch = "s";  6'd29: ch = "y";  6'd30: ch = "=";
        6'd31: ch = hexd({2'b00, ss[9:8]});
        6'd32: ch = hexd(ss[7:4]);
        6'd33: ch = hexd(ss[3:0]);
        6'd34: ch = 8'h0D;
        6'd35: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin busy_msg <= 1'b0; idx <= 6'd0; end
        else if (!busy_msg) begin
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
