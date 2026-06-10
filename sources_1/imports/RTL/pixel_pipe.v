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
    output reg [7:0] tlp_dbg,      // last sampled top-left red value (diagnostic, pipe INPUT)
    output reg [7:0] olp_dbg,      // same sample point on the pipe OUTPUT (diagnostic)
    output reg [7:0] trig_cnt_dbg  // free-running count of trigger pulses (diagnostic)
    );
    parameter V=2'b01; parameter B=2'b10; parameter O=2'b11; //states for Vsync, V back porch + 1st hysnc period, others.
    reg flag=1'b0; // flag for pattern change
    reg [1:0] S, N; // for primary FSM of states V B O




   // set or switch orienation (0 for H strips, and 1 for V strips)
   reg ori=1'b0;
   reg ori_reg;
   always@(posedge in_vsync) begin
        ori_reg<=ori;
        ori<= sw[0];
   end
   //
    // (Spatial pattern generation moved to the resolution-adaptive pattern_gen instance
    //  below; the fixed-period phase accumulators / index ROMs are gone.)
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
                olp_dbg  <= out_red;                    // same instant on the pipe OUTPUT (passthrough should == in_red)
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
    //--------------------------------------------------------------------------
    // Resolution-adaptive structured-light fringe generator (ported from MimasA7-SLI
    // pattern_gen.v). Measures the active region, computes b = ceil(F/288) and periods
    // 288b : 48b : 8b (1:6:36), and DDS-samples a master cosine -> the stripe frequency
    // SCALES with the display resolution (always spans the field). Driven by THIS module's
    // existing phase/freq/orientation sequencing (EXT_SEQ); enable=display_mode (SLI vs
    // pass-through); channel enables from sw[3:1]; frq==3 = flash block. lut_din->lut_dout
    // tied identity (radiometric/tone LUT is a future add via pattern_lut + UART RX).
    //--------------------------------------------------------------------------
    wire [7:0] pg_lut_din;
    wire [7:0] pg_r, pg_g, pg_b;
    pattern_gen #(
        .COS_AW(12), .FRAC(12), .AUTO_CYCLE(0), .EXT_SEQ(1), .RGB_RUNTIME(1)
    ) i_pattern_gen (
        .pixel_clk(clk),
        .raw_blank(in_blank), .raw_hsync(in_hsync), .raw_vsync(in_vsync),
        .raw_red(in_red), .raw_green(in_green), .raw_blue(in_blue),
        .orient(ori), .enable(display_mode),
        .chan_en({en_R, en_G, en_B}),
        .ext_frm(fra), .ext_frq(frq),
        .lut_din(pg_lut_din), .lut_dout(pg_lut_din),     // identity transfer (no tone LUT yet)
        .out_red(pg_r), .out_green(pg_g), .out_blue(pg_b),
        .dbg()
    );

    // pattern_gen output is ~2 pixel-clocks behind raw; delay sync/blank to match so the
    // active region stays aligned with the generated pixels.
    reg [1:0] hs_dl=2'b00, vs_dl=2'b00, bl_dl=2'b00;
    always@(posedge clk) begin
        hs_dl <= {hs_dl[0], in_hsync};
        vs_dl <= {vs_dl[0], vsync};
        bl_dl <= {bl_dl[0], in_blank};
    end
    assign out_red = pg_r; assign out_green = pg_g; assign out_blue = pg_b;
    assign out_hsync = hs_dl[1]; assign out_vsync = vs_dl[1]; assign out_blank = bl_dl[1];
endmodule
