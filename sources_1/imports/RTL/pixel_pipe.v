`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:
// Engineer: Qihsi Hu
//
// Create Date: 12/05/2024 08:04:50 PM
// Design Name:
// Module Name: pixel_pipe
// Project Name:
// Target Devices:
// Tool Versions:
// Description: process 8-bit RGB pixels
//
// Dependencies:
//
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
//
//////////////////////////////////////////////////////////////////////////////////


module pixel_pipe(
    input clk, clk10,
    input [3:0] sw, // 3,2,1 for enable/disable RGB. 0 for vertical/horizontal strips
    input [7:0] in_green,
    input [7:0] in_blue,
    input [7:0] in_red,
    input  in_blank,
    input  in_vsync,
    input in_hsync,
    input  vsync,
    output reg trig,
     output wire f_frm,
     input mode, rdy,
     input vid_valid,   // 1 = HDMI input validly decoding; gates the passthrough TLP trigger

    output [7:0] out_red,
    output [7:0] out_green,
    output [7:0] out_blue,
    output  out_hsync,
    output out_vsync,
    output out_blank,
    output reg [7:0] tlp_dbg,      // last sampled top-left red value (diagnostic)
    output reg [7:0] trig_cnt_dbg  // free-running count of trigger pulses (diagnostic)
    );
    parameter V=2'b01; parameter B=2'b10; parameter O=2'b11; //states for Vsync, V back porch + 1st hysnc period, others.
    reg flag=1'b0; // flag for pattern change
    reg [1:0] S, N; // for primary FSM of states V B O
    reg [7:0] LUT [0:719]; reg [7:0] LUT_V [0:1279];
    wire [7:0] fbyte; // LUT file output bytes




   // set or switch orienation (0 for H strips, and 1 for V strips)
   reg ori=1'b0;
   reg ori_reg;
   always@(posedge in_vsync) begin
        ori_reg<=ori;
        ori<= sw[0];
   end
   //
    //row index tracker
    reg [9:0] row=10'd0;
    always@(posedge in_hsync) begin
        if(S==O) row<=row+10'd1; //exclude the first hysnc of the frame
        else row<=10'd0;
    end
    // HSYNC backporch counter
    reg [7:0] HB=8'd0;
    always@(posedge clk) begin
        if(in_hsync) HB<=8'd0;
        else if (in_blank) HB<=HB+8'd1;
        else HB<= 8'd0;
    end
    //col index tracker
    reg [10:0] col= 11'd0;
    reg in_blank_reg;
//    always@(posedge clk) begin
//        in_blank_reg <=in_blank;
//        if(in_hsync) col<=0; //exclude the first hysnc of the frame
//        //else if (in_blank) begin if (HB==8'd219) col<=11'b1; else col<=11'b0; end
//        else if (in_blank) begin if (HB==8'd39) col<=11'b1; else col<=11'b0; end
//        else col<=col+11'd1;
//    end
    always@(posedge clk) begin
        if(in_blank) col<=11'd0;
        else col<= col+11'd1;
    end
    //frame index counter with a slow motion feature that holds each frame for 32 frames
    reg[1:0] frq=2'd0; reg[2:0] fra=3'd0; // spatial frquency index, frame index
    reg hold=1'b0; reg [3:0] rdy_cnt =4'h0; reg rdy_reg; reg vsync_reg;
    reg display_mode;
     always@(posedge clk) begin
        rdy_reg<=rdy; // buffer GPIO in a relaxed pace, may not work well if pulse come during V blank period
        vsync_reg<=in_vsync;

        if (~in_vsync && vsync_reg) display_mode <= mode;
    end
    //count rising edges of camera-ready GPIO input (line 4 / rdy).
    //Each rdy rising edge that occurs while mode is high queues exactly one
    //trigger; each vsync consumes one. Entering local mode (mode going high) does
    //NOT itself trigger - we wait for a genuine rdy rising edge that arrives after
    //mode is already high. If rdy is held high across the mode change there is no
    //rising edge, so no trigger is sent.
    always@(posedge clk) begin
        if (~mode) rdy_cnt<=4'h0;                                       // mode low: clear pending triggers
        else case ({(in_vsync && ~vsync_reg), (rdy && ~rdy_reg)})
            2'b01: rdy_cnt <= rdy_cnt + 4'h1;                           // rdy rising edge: queue a trigger
            2'b10: rdy_cnt <= (rdy_cnt==4'h0) ? 4'h0 : rdy_cnt - 4'h1;  // vsync: consume one queued trigger
            default: rdy_cnt <= rdy_cnt;                                // both (rare) or neither: no change
        endcase
    end

    always@(posedge clk) begin  //// examine rdy counter at rising edge of vsync
        if(mode==1'b0) begin frq<=2'd0; fra<=3'd0; hold<=1'b1; end
        else if (in_vsync && ~vsync_reg) begin
            if (ori ^ ori_reg) begin frq<=2'd0; fra<=3'd0; hold<=1'b1; end
            else if  (rdy_cnt!= 4'h0)   begin //when edge counter is non-zero
                     fra<=fra+3'd1; hold<=1'b0;
                     if(fra==3'd7) begin
                        frq<=frq+2'd1;
                     end
            end
            else begin
                fra<=fra; hold<=1'b1; frq<=frq;
            end
        end
    end
    //index mapping; find the correspoding index in the input LUT, according to current row,frq, and fra
    wire [9:0] index;//target index
    indexMap MAP(.a({frq,fra,row}), .qspo(index), .clk(clk));

    // On-the-fly VERTICAL phase -- replaces the 917 KB indexMapV ROM.
    //   ROM held indexV = (fra*160 + offset*(col+1)) mod 1280, offset = 1/6/30 per frq
    //   (see indexMapping.m). A phase accumulator reproduces that sequence CYCLE-FOR-CYCLE
    //   (incl. the ROM's 1-clk latency): reset to fra*160 during blank, +offset each active
    //   pixel, wrap at 1280. One add + one compare per pixel -> trivially fast (fixes the
    //   78.67 MHz timing), frees the ROM, and works at any width (no fixed-size table).
    wire [4:0]  voff       = (frq==2'd2) ? 5'd30 : (frq==2'd1) ? 5'd6 : 5'd1;
    wire [10:0] vbase      = fra * 8'd160;            // fra*160  (fra<=7 -> <=1120 < 1280)
    reg  [11:0] phaseV     = 12'd0;
    wire [11:0] phaseV_adv = phaseV + voff;
    always @(posedge clk) begin
        if (in_blank)                    phaseV <= {1'b0, vbase};
        else if (phaseV_adv >= 12'd1280) phaseV <= phaseV_adv - 12'd1280;
        else                             phaseV <= phaseV_adv;
    end
    wire [10:0] indexV = phaseV[10:0];
    //top-left pixel detection
    reg [7:0] TL; //-the top left pixel of current frame
    //FSM
    always@(posedge clk) begin
        case(S)
            V: N<= in_vsync?V:B; // vsync period
            B: N<= in_blank?B:O; // V back porch
            O: N<= in_vsync?V:O; //other
            default: N<=O;
        endcase
    end
    //set flag (passthrough Mode #1): sample the top-left red a few pixels INTO active
    //video (the blank->active edge pixel is unstable), and only when the HDMI input is
    //validly decoding (vid_valid). A genuine TLP change sets flag; flag persists until
    //the next sample. (Was: sampled the unstable edge pixel every B->O with no valid
    //gate -> jittered -> spurious triggers on static content.)
    localparam [7:0] TLP_THRESH = 8'd4;   // ignore TLP changes smaller than this (rejects GPU +-1 LSB dither)
    wire [7:0] tlp_absdiff = (in_red >= TL) ? (in_red - TL) : (TL - in_red);
    reg       smp_pend = 1'b0;
    reg [3:0] smp_dly  = 4'd0;
    always@(posedge clk) begin
        S<=N;
        if((S==B)&&(N==O)) begin
            smp_pend <= 1'b1; smp_dly <= 4'd4;          // arm: sample ~5th active pixel
        end else if (smp_pend) begin
            if (smp_dly != 4'd0) smp_dly <= smp_dly - 4'd1;
            else begin
                smp_pend <= 1'b0;
                tlp_dbg  <= in_red;                     // capture the sampled TLP every frame (diagnostic)
                if (vid_valid) begin
                    // trigger only on a change bigger than TLP_THRESH (rejects +-1 LSB
                    // GPU dither on a "static" pixel; real pattern changes are large)
                    if (tlp_absdiff >= TLP_THRESH) begin flag <= 1'b1; TL <= in_red; end
                    else flag <= 1'b0;                  // sub-threshold (dither) -> ignore, keep TL
                end else flag <= 1'b0;                  // input not valid -> never trigger
            end
        end
    end

    //trigger pulse: SLI mode = per-frame ~hold (unchanged); passthrough = one clean
    //fixed-width pulse on a valid TLP change (not a vsync-wide level).
    localparam [11:0] TRIG_W = 12'd1024;                // ~26us @40MHz / ~14us @74MHz
    reg [11:0] trig_cnt = 12'd0;
    reg        s_v_d = 1'b0;
    always@(posedge clk) begin
        s_v_d <= (S==V);
        if (mode) begin
            trig_cnt <= 12'd0;
            trig <= (S==V) ? ~hold : 1'b0;              // SLI handshake trigger (unchanged)
        end else if ((S==V) && !s_v_d && flag) begin
            trig <= 1'b1; trig_cnt <= TRIG_W;           // passthrough: start the pulse
        end else if (trig_cnt != 12'd0) begin
            trig <= 1'b1; trig_cnt <= trig_cnt - 12'd1; // hold the pulse
        end else begin
            trig <= 1'b0;
        end
    end
    // diagnostic: count trigger pulse rising edges (wraps at 256)
    reg trig_q = 1'b0;
    always@(posedge clk) begin
        trig_q <= trig;
        if (trig & ~trig_q) trig_cnt_dbg <= trig_cnt_dbg + 8'd1;
    end
    assign f_frm = (fra==3'd0)&&(frq==3'b0);


    //connect the pipe
    //assign out_red =in_red; assign out_green =in_green;  assign out_blue =in_blue;
    //flashing sequence for frq==2'b11 => BWBWBWBW

    //buffer channel enable input
    reg en_R,en_G,en_B;
    always@(posedge in_vsync) begin
        en_R<=sw[3]; en_G<=sw[2]; en_B<=sw[1];
    end
    //get pixel values from LUT and LUT_V
    wire [7:0] pix; wire [7:0] pixV;
    LUT LUT_ins (.a(index),.spo(pix));
    LUT_V LUTV_ins (.a(indexV),.spo(pixV));

   assign out_red = display_mode? (in_blank? in_red: en_R?(frq==2'b11 ? (fra[0]?8'hFF:8'h00) : (ori?pix:pixV)  ): 8'h00 ): in_red ;
   assign out_green = display_mode?  (in_blank? in_green: en_G?(frq==2'b11 ? (fra[0]?8'hFF:8'h00) : (ori?pix:pixV)  ): 8'h00 ) : in_green ;
   assign out_blue = display_mode? (in_blank? in_blue: en_B?(frq==2'b11 ? (fra[0]?8'hFF:8'h00) : (ori?pix:pixV)  ): 8'h00 ):in_blue ;
    assign out_hsync =in_hsync; assign out_vsync =vsync;  assign out_blank =in_blank;
endmodule
