`timescale 1ns/1ps
//=============================================================================
// cam_syncdbg - count every sync-channel code as it arrives, independent of
// cam_sync_decode's state machine. Answers one question: does FE exist?
//
// THE PROBLEM. frame_start fires exactly once and line_start fires forever.
// cam_sync_decode leaves its line loop only on FE (0x32A), tested in
// S_LINE_WAIT -- a state we demonstrably pass through every line, since LS and
// LE are both being decoded. So either FE never arrives on the wire, or it
// arrives and something about the FSM misses it. Those need completely
// different fixes and inspection cannot tell them apart.
//
// This counts RAW sync_word matches with no FSM involved at all:
//
//   FE=0000 with LS/LE climbing  -> the sensor never sends FE. Not a decode bug:
//                                   look at the frame configuration, or accept
//                                   that frames must be delimited another way.
//   FE climbing                  -> FE is on the wire and cam_sync_decode is
//                                   missing it. A decode bug, and the counts
//                                   say how often it should have fired.
//
// OT counts words matching none of the eight codes -- mostly the 3-bit window-ID
// words that follow each frame-sync code, which are expected and not errors.
// A very large OT would suggest the sync channel is not decoding cleanly at all.
//
// Counters SATURATE at 0xFFFF rather than wrapping, so "never happened" stays
// visibly 0000 and cannot be confused with "wrapped back to a small number".
//
// UART:  FS=0001 FE=0000 LS=FFFF LE=FFFF IM=FFFF BL=0140 CR=FFFF TR=FFFF OT=FFFF
//=============================================================================
module cam_syncdbg #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =   1_000_000,
    parameter integer REP_CY = CLK_HZ / 2
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

    //--------------------------- boot to 41, stream after the eye is centred
    wire [7:0] boot_led;
    wire       streaming;
    reg        stream_go = 1'b0;
    cam_boot_stage1 #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .STOP_AT(41)) u_boot (
        .clk(clk), .rst_n(rst_n),
        .stream_go(stream_go), .streaming(streaming),
        .led(boot_led), .usb_tx(), .usb_rx(usb_rx),
        .cam_sck(cam_sck), .cam_mosi(cam_mosi), .cam_ss_n(cam_ss_n),
        .cam_miso(cam_miso), .cam_reset_n(cam_reset_n),
        .cam_clk_pll(cam_clk_pll), .cam_trigger(cam_trigger),
        .cam_monitor(cam_monitor)
    );

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
    wire [4:0] bt0,bt1,bt2,bt3,bts;
    wire [5:0] bl0,bl1,bl2,bl3,bls;
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

    reg [1:0] rdy_s = 2'b00;
    reg       fired = 1'b0;
    always @(posedge clk) begin
        stream_go <= 1'b0;
        rdy_s <= {rdy_s[0], (scan_done & aligned)};
        if (rst) fired <= 1'b0;
        else if (rdy_s[1] && !fired && !streaming) begin
            stream_go <= 1'b1; fired <= 1'b1;
        end
    end

    //--------------------------------------------------- THE COUNTERS
    localparam [9:0] SC_FS=10'h2AA, SC_FE=10'h32A, SC_LS=10'h0AA, SC_LE=10'h12A;
    localparam [9:0] SC_BL=10'h015, SC_IMG=10'h035, SC_CRC=10'h059, SC_TR=10'h3A6;

    reg [15:0] c_fs=0, c_fe=0, c_ls=0, c_le=0, c_im=0, c_bl=0, c_cr=0, c_tr=0, c_ot=0;

    // One-hot decode, then a saturating increment per counter. Written out
    // rather than via a macro: Vivado's preprocessor rejects a `define whose
    // body is a parenthesised statement in this position.
    wire h_fs = (sync_word == SC_FS);
    wire h_fe = (sync_word == SC_FE);
    wire h_ls = (sync_word == SC_LS);
    wire h_le = (sync_word == SC_LE);
    wire h_im = (sync_word == SC_IMG);
    wire h_bl = (sync_word == SC_BL);
    wire h_cr = (sync_word == SC_CRC);
    wire h_tr = (sync_word == SC_TR);
    wire h_ot = !(h_fs|h_fe|h_ls|h_le|h_im|h_bl|h_cr|h_tr);

    // Saturate rather than wrap: "never happened" must stay visibly 0000 and
    // must not be confusable with "wrapped round to a small number".
    always @(posedge wordclk) begin
        if (wc_rst) begin
            c_fs<=0; c_fe<=0; c_ls<=0; c_le<=0; c_im<=0;
            c_bl<=0; c_cr<=0; c_tr<=0; c_ot<=0;
        end else if (aligned) begin
            if (h_fs && c_fs != 16'hFFFF) c_fs <= c_fs + 16'd1;
            if (h_fe && c_fe != 16'hFFFF) c_fe <= c_fe + 16'd1;
            if (h_ls && c_ls != 16'hFFFF) c_ls <= c_ls + 16'd1;
            if (h_le && c_le != 16'hFFFF) c_le <= c_le + 16'd1;
            if (h_im && c_im != 16'hFFFF) c_im <= c_im + 16'd1;
            if (h_bl && c_bl != 16'hFFFF) c_bl <= c_bl + 16'd1;
            if (h_cr && c_cr != 16'hFFFF) c_cr <= c_cr + 16'd1;
            if (h_tr && c_tr != 16'hFFFF) c_tr <= c_tr + 16'd1;
            if (h_ot && c_ot != 16'hFFFF) c_ot <= c_ot + 16'd1;
        end
    end

    //------------------------------------------------------------------------
    // WHICH STATE IS THE DECODER IN WHEN FE ARRIVES?
    //
    // FE is proven to be on the wire (FE climbs with FS), and cam_sync_decode
    // still only ever fires frame_start once. It tests FE in exactly one state,
    // S_LINE_WAIT, so it must not be in that state when FE turns up. This is a
    // faithful copy of that FSM's TRANSITIONS -- no pixel path -- with a counter
    // per state recording where it was each time FE went past.
    //
    // Whichever counter climbs is the state that needs an FE test added, and it
    // names the fix directly.
    //------------------------------------------------------------------------
    localparam [2:0] Q_IDLE=0, Q_AFS=1, Q_LW=2, Q_ALS=3, Q_LINE=4, Q_LEND=5;
    reg [2:0] qst = Q_IDLE;

    always @(posedge wordclk) begin
        if (wc_rst) qst <= Q_IDLE;
        else if (aligned) begin
            case (qst)
            Q_IDLE: if (h_fs) qst <= Q_AFS;
            Q_AFS :           qst <= Q_LW;
            Q_LW  : if      (h_ls) qst <= Q_ALS;
                    else if (h_fe) qst <= Q_IDLE;
            Q_ALS :           qst <= Q_LINE;
            Q_LINE: if (h_le) qst <= Q_LEND;
            Q_LEND:           qst <= Q_LW;
            default:          qst <= Q_IDLE;
            endcase
        end
    end

    reg [15:0] fq0=0, fq1=0, fq2=0, fq3=0, fq4=0, fq5=0;
    always @(posedge wordclk) begin
        if (wc_rst) begin fq0<=0; fq1<=0; fq2<=0; fq3<=0; fq4<=0; fq5<=0; end
        else if (aligned && h_fe) begin
            case (qst)
            Q_IDLE: if (fq0 != 16'hFFFF) fq0 <= fq0 + 16'd1;
            Q_AFS : if (fq1 != 16'hFFFF) fq1 <= fq1 + 16'd1;
            Q_LW  : if (fq2 != 16'hFFFF) fq2 <= fq2 + 16'd1;
            Q_ALS : if (fq3 != 16'hFFFF) fq3 <= fq3 + 16'd1;
            Q_LINE: if (fq4 != 16'hFFFF) fq4 <= fq4 + 16'd1;
            Q_LEND: if (fq5 != 16'hFFFF) fq5 <= fq5 + 16'd1;
            endcase
        end
    end

    // Counters change slowly relative to a report; a plain sample is fine.
    reg [15:0] s_fs,s_fe,s_ls,s_le,s_im,s_bl,s_cr,s_tr,s_ot;
    always @(posedge clk) begin
        s_fs<=c_fs; s_fe<=c_fe; s_ls<=fq0; s_le<=fq1; s_im<=fq2;
        s_bl<=fq3; s_cr<=fq4; s_tr<=fq5; s_ot<=c_ot;
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
        led[7] <= hb[25]; led[6] <= aligned; led[5] <= scan_done;
        led[4] <= streaming; led[3] <= (s_fe != 16'd0); led[2:0] <= 3'd0;
    end

    //------------------------------------------------------------------- UART
    //  FS=0383 FE=0382 q0=.... q1=.... q2=.... q3=.... q4=.... q5=.... OT=FFFF
    //  q0=IDLE q1=AFTER_FS q2=LINE_WAIT q3=AFTER_LS q4=LINE q5=LINE_END
    localparam integer MSG_LEN = 83;   // 9 fields x 8 + 8 spaces + CRLF

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [6:0] idx = 7'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    // field f = idx/9, position within field = idx%9 -- but done as a lookup so
    // no dividers are inferred: fields are 9 chars ("XX=hhhh" + space).
    reg [15:0] fval;
    reg [7:0]  fc0, fc1;
    always @(*) begin
        case (idx)
        7'd0,7'd1,7'd2,7'd3,7'd4,7'd5,7'd6,7'd7,7'd8:       begin fval=s_fs; fc0="F"; fc1="S"; end
        7'd9,7'd10,7'd11,7'd12,7'd13,7'd14,7'd15,7'd16,7'd17: begin fval=s_fe; fc0="F"; fc1="E"; end
        7'd18,7'd19,7'd20,7'd21,7'd22,7'd23,7'd24,7'd25,7'd26: begin fval=s_ls; fc0="q"; fc1="0"; end
        7'd27,7'd28,7'd29,7'd30,7'd31,7'd32,7'd33,7'd34,7'd35: begin fval=s_le; fc0="q"; fc1="1"; end
        7'd36,7'd37,7'd38,7'd39,7'd40,7'd41,7'd42,7'd43,7'd44: begin fval=s_im; fc0="q"; fc1="2"; end
        7'd45,7'd46,7'd47,7'd48,7'd49,7'd50,7'd51,7'd52,7'd53: begin fval=s_bl; fc0="q"; fc1="3"; end
        7'd54,7'd55,7'd56,7'd57,7'd58,7'd59,7'd60,7'd61,7'd62: begin fval=s_cr; fc0="q"; fc1="4"; end
        7'd63,7'd64,7'd65,7'd66,7'd67,7'd68,7'd69,7'd70,7'd71: begin fval=s_tr; fc0="q"; fc1="5"; end
        default:                                              begin fval=s_ot; fc0="O"; fc1="T"; end
        endcase
    end

    reg [3:0] fpos;
    always @(*) begin
        if      (idx < 7'd9)  fpos = idx[3:0];
        else if (idx < 7'd18) fpos = idx[3:0] - 4'd9;
        else if (idx < 7'd27) fpos = idx - 7'd18;
        else if (idx < 7'd36) fpos = idx - 7'd27;
        else if (idx < 7'd45) fpos = idx - 7'd36;
        else if (idx < 7'd54) fpos = idx - 7'd45;
        else if (idx < 7'd63) fpos = idx - 7'd54;
        else if (idx < 7'd72) fpos = idx - 7'd63;
        else                  fpos = idx - 7'd72;
    end

    always @(*) begin
        if (idx == MSG_LEN[6:0] - 7'd2)      ch = 8'h0D;
        else if (idx == MSG_LEN[6:0] - 7'd1) ch = 8'h0A;
        else begin
            case (fpos)
            4'd0: ch = fc0;
            4'd1: ch = fc1;
            4'd2: ch = "=";
            4'd3: ch = hexd(fval[15:12]);
            4'd4: ch = hexd(fval[11:8]);
            4'd5: ch = hexd(fval[7:4]);
            4'd6: ch = hexd(fval[3:0]);
            default: ch = " ";
            endcase
        end
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin busy_msg <= 1'b0; idx <= 7'd0; end
        else if (!busy_msg) begin
            if (msg_go) begin busy_msg <= 1'b1; idx <= 7'd0; end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[6:0] - 7'd1) begin busy_msg <= 1'b0; idx <= 7'd0; end
            else idx <= idx + 7'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

endmodule
