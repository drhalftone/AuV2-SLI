`timescale 1ps/1ps   // MUST match XAPP888 mmcm_drp.v: TCQ=100 is a 100 ps clock-to-Q.
                     // Under 1ns/1ps it would be 100 ns (10 clk100 periods) and the
                     // FSM/DRP timing falls apart in sim (MMCM never re-locks).
//==============================================================================
// drp_recfg - 13-mode MMCM DRP reconfiguration controller (Phase D2).
//
//   Generalises the XAPP888 mmcm_drp 2-state controller to the full curated mode
//   table. The read-modify-write FSM is the proven XAPP888 one verbatim; the only
//   change is that the 23-register write sequence is driven by ONE combinational
//   ROM whose values come from the selected mode's M/D/O (looked up by mode_idx and
//   turned into DRP words by the same mmcm_drp_func.h functions, constant-folded
//   per mode). CLKOUT0=CLKOUT1=pixel(=x1), CLKOUT2=x5(=5*pixel); CLKOUT3..6 unused.
//
//   Per-mode integer M(mult) / D(divclk) / O0(pixel divide) / O2(x5 divide), in the
//   SAME index order as mode_table.vh (0=highest priority ... 12=640x480@60 failsafe):
//     idx mode          M   D  O0  O2     pixel
//      0  800x600@120   11  1  15   3   73.33 MHz
//      1  640x480@120   55  7  15   3   52.38
//      2  1024x768@75   59  5  15   3   78.67   <- fastest (MMCM power-up = this)
//      3  800x600@75    52  7  15   3   49.52
//      4  640x480@75    11  1  35   7   31.43
//      5  1024x768@70   15  2  10   2   75.00
//      6  800x600@72    10  1  20   4   50.00
//      7  640x480@72    11  1  35   7   31.43
//      8  1280x720@60   52  7  10   2   74.29
//      9  1280x800@60   32  3  15   3   71.11
//     10  1024x768@60   13  2  10   2   65.00
//     11  800x600@60     6  1  15   3   40.00
//     12  640x480@60    34  3  45   9   25.19  (failsafe)
//
//   Reconfig: present MODE_IDX, pulse SEN for 1 SCLK; SRDY strobes when re-locked.
//==============================================================================
module drp_recfg (
    input             SCLK,
    input             RST,
    input      [3:0]  MODE_IDX,
    input             SEN,
    output reg        SRDY,
    // to/from MMCME2_ADV
    input      [15:0] DO,
    input             DRDY,
    input             LOCKED,
    output reg        DWE,
    output reg        DEN,
    output reg [6:0]  DADDR,
    output reg [15:0] DI,
    output            DCLK,
    output reg        RST_MMCM
);
    localparam TCQ = 100;
    assign DCLK = SCLK;

    `include "mmcm_drp_func.h"

    // ---- FSM state encodings ----
    localparam RESTART=4'h1, WAIT_LOCK=4'h2, WAIT_SEN=4'h3, ADDRESS=4'h4,
               WAIT_A_DRDY=4'h5, BITMASK=4'h6, BITSET=4'h7, WRITE=4'h8, WAIT_DRDY=4'h9;
    localparam STATE_COUNT_CONST = 23;
    localparam [37:0] CNT1 = mmcm_count_calc(8'd1, 0, 50000);   // unused CLKOUT3..6 = /1

    // ---- declarations (all module-level state up front) ----
    reg [3:0]  sel = 4'd2;                 // selected mode; power-up = fastest (idx2)
    reg [37:0] selCLKOUT0, selCLKOUT2, selCLKFB, selDIVCLK;
    reg [39:0] selLOCK;
    reg [9:0]  selFILT;
    reg [38:0] rom_comb;
    reg [5:0]  rom_addr, next_rom_addr;
    reg [38:0] rom_do;
    reg [3:0]  current_state = RESTART, next_state = RESTART;
    reg [4:0]  state_count = STATE_COUNT_CONST, next_state_count = STATE_COUNT_CONST;
    reg        next_srdy, next_dwe, next_den, next_rst_mmcm;
    reg [6:0]  next_daddr;
    reg [15:0] next_di;

    // ---- per-mode DRP word bundles (function calls constant-folded per case arm) ----
    always @* begin
        case (sel)
        4'd0:  begin selCLKFB=mmcm_count_calc(8'd11,0,50000); selDIVCLK=mmcm_count_calc(8'd1,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd11); selFILT=mmcm_filter_lookup(8'd11,"OPTIMIZED"); end
        4'd1:  begin selCLKFB=mmcm_count_calc(8'd55,0,50000); selDIVCLK=mmcm_count_calc(8'd7,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd55); selFILT=mmcm_filter_lookup(8'd55,"OPTIMIZED"); end
        4'd2:  begin selCLKFB=mmcm_count_calc(8'd59,0,50000); selDIVCLK=mmcm_count_calc(8'd5,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd59); selFILT=mmcm_filter_lookup(8'd59,"OPTIMIZED"); end
        4'd3:  begin selCLKFB=mmcm_count_calc(8'd52,0,50000); selDIVCLK=mmcm_count_calc(8'd7,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd52); selFILT=mmcm_filter_lookup(8'd52,"OPTIMIZED"); end
        4'd4:  begin selCLKFB=mmcm_count_calc(8'd11,0,50000); selDIVCLK=mmcm_count_calc(8'd1,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd35,0,50000); selCLKOUT2=mmcm_count_calc(8'd7,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd11); selFILT=mmcm_filter_lookup(8'd11,"OPTIMIZED"); end
        4'd5:  begin selCLKFB=mmcm_count_calc(8'd15,0,50000); selDIVCLK=mmcm_count_calc(8'd2,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd10,0,50000); selCLKOUT2=mmcm_count_calc(8'd2,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd15); selFILT=mmcm_filter_lookup(8'd15,"OPTIMIZED"); end
        4'd6:  begin selCLKFB=mmcm_count_calc(8'd10,0,50000); selDIVCLK=mmcm_count_calc(8'd1,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd20,0,50000); selCLKOUT2=mmcm_count_calc(8'd4,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd10); selFILT=mmcm_filter_lookup(8'd10,"OPTIMIZED"); end
        4'd7:  begin selCLKFB=mmcm_count_calc(8'd11,0,50000); selDIVCLK=mmcm_count_calc(8'd1,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd35,0,50000); selCLKOUT2=mmcm_count_calc(8'd7,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd11); selFILT=mmcm_filter_lookup(8'd11,"OPTIMIZED"); end
        4'd8:  begin selCLKFB=mmcm_count_calc(8'd52,0,50000); selDIVCLK=mmcm_count_calc(8'd7,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd10,0,50000); selCLKOUT2=mmcm_count_calc(8'd2,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd52); selFILT=mmcm_filter_lookup(8'd52,"OPTIMIZED"); end
        4'd9:  begin selCLKFB=mmcm_count_calc(8'd32,0,50000); selDIVCLK=mmcm_count_calc(8'd3,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd32); selFILT=mmcm_filter_lookup(8'd32,"OPTIMIZED"); end
        4'd10: begin selCLKFB=mmcm_count_calc(8'd13,0,50000); selDIVCLK=mmcm_count_calc(8'd2,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd10,0,50000); selCLKOUT2=mmcm_count_calc(8'd2,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd13); selFILT=mmcm_filter_lookup(8'd13,"OPTIMIZED"); end
        4'd11: begin selCLKFB=mmcm_count_calc(8'd6,0,50000);  selDIVCLK=mmcm_count_calc(8'd1,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd15,0,50000); selCLKOUT2=mmcm_count_calc(8'd3,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd6);  selFILT=mmcm_filter_lookup(8'd6,"OPTIMIZED"); end
        default: begin // idx 12 failsafe 640x480@60
                     selCLKFB=mmcm_count_calc(8'd34,0,50000); selDIVCLK=mmcm_count_calc(8'd3,0,50000);
                     selCLKOUT0=mmcm_count_calc(8'd45,0,50000); selCLKOUT2=mmcm_count_calc(8'd9,0,50000);
                     selLOCK=mmcm_lock_lookup(8'd34); selFILT=mmcm_filter_lookup(8'd34,"OPTIMIZED"); end
        endcase
    end

    // ---- combinational 23-entry ROM (addr, mask, value), packing == XAPP888 ----
    always @* begin
        case (rom_addr)
        6'd0:  rom_comb = {7'h28, 16'h0000, 16'hFFFF};                 // power
        6'd1:  rom_comb = {7'h08, 16'h1000, selCLKOUT0[15:0]};         // CLKOUT0
        6'd2:  rom_comb = {7'h09, 16'hFC00, selCLKOUT0[31:16]};
        6'd3:  rom_comb = {7'h0A, 16'h1000, selCLKOUT0[15:0]};         // CLKOUT1 = CLKOUT0
        6'd4:  rom_comb = {7'h0B, 16'hFC00, selCLKOUT0[31:16]};
        6'd5:  rom_comb = {7'h0C, 16'h1000, selCLKOUT2[15:0]};         // CLKOUT2 (x5)
        6'd6:  rom_comb = {7'h0D, 16'hFC00, selCLKOUT2[31:16]};
        6'd7:  rom_comb = {7'h0E, 16'h1000, CNT1[15:0]};               // CLKOUT3 (/1)
        6'd8:  rom_comb = {7'h0F, 16'hFC00, CNT1[31:16]};
        6'd9:  rom_comb = {7'h10, 16'h1000, CNT1[15:0]};               // CLKOUT4
        6'd10: rom_comb = {7'h11, 16'hFC00, CNT1[31:16]};
        6'd11: rom_comb = {7'h06, 16'h1000, CNT1[15:0]};               // CLKOUT5
        6'd12: rom_comb = {7'h07, 16'hFC00, CNT1[31:16]};
        6'd13: rom_comb = {7'h12, 16'h1000, CNT1[15:0]};               // CLKOUT6
        6'd14: rom_comb = {7'h13, 16'hFC00, CNT1[31:16]};
        6'd15: rom_comb = {7'h16, 16'hC000, {2'h0, selDIVCLK[23:22], selDIVCLK[11:0]}}; // DIVCLK
        6'd16: rom_comb = {7'h14, 16'h1000, selCLKFB[15:0]};           // CLKFBOUT
        6'd17: rom_comb = {7'h15, 16'hFC00, selCLKFB[31:16]};
        6'd18: rom_comb = {7'h18, 16'hFC00, {6'h00, selLOCK[29:20]}};  // LOCK
        6'd19: rom_comb = {7'h19, 16'h8000, {1'b0, selLOCK[34:30], selLOCK[9:0]}};
        6'd20: rom_comb = {7'h1A, 16'h8000, {1'b0, selLOCK[39:35], selLOCK[19:10]}};
        6'd21: rom_comb = {7'h4E, 16'h66FF, selFILT[9],2'h0,selFILT[8:7],2'h0,selFILT[6],8'h00}; // FILT
        6'd22: rom_comb = {7'h4F, 16'h666F, selFILT[5],2'h0,selFILT[4:3],2'h0,selFILT[2:1],2'h0,selFILT[0],4'h0};
        default: rom_comb = {7'h00, 16'h0000, 16'h0000};
        endcase
    end

    always @(posedge SCLK) rom_do <= #TCQ rom_comb;

    // latch the requested mode when SEN is accepted
    always @(posedge SCLK)
        if (current_state == WAIT_SEN && SEN) sel <= #TCQ MODE_IDX;

    //--------------------------------------------------------------------------
    // XAPP888 read-modify-write FSM (verbatim; SADDR removed -> single sequence)
    //--------------------------------------------------------------------------
    always @(posedge SCLK) begin
        DADDR<=#TCQ next_daddr; DWE<=#TCQ next_dwe; DEN<=#TCQ next_den;
        RST_MMCM<=#TCQ next_rst_mmcm; DI<=#TCQ next_di; SRDY<=#TCQ next_srdy;
        rom_addr<=#TCQ next_rom_addr; state_count<=#TCQ next_state_count;
    end
    always @(posedge SCLK)
        if (RST) current_state <= #TCQ RESTART; else current_state <= #TCQ next_state;

    always @* begin
        next_srdy=1'b0; next_daddr=DADDR; next_dwe=1'b0; next_den=1'b0;
        next_rst_mmcm=RST_MMCM; next_di=DI; next_rom_addr=rom_addr; next_state_count=state_count;
        case (current_state)
        RESTART:   begin next_daddr=7'h00; next_di=16'h0000; next_rom_addr=6'h00;
                         next_rst_mmcm=1'b1; next_state=WAIT_LOCK; end
        WAIT_LOCK: begin next_rst_mmcm=1'b0; next_state_count=STATE_COUNT_CONST;
                         if (LOCKED) begin next_state=WAIT_SEN; next_srdy=1'b1; end
                         else next_state=WAIT_LOCK; end
        WAIT_SEN:  begin next_rom_addr=6'h00;            // single sequence; mode via `sel`
                         if (SEN) next_state=ADDRESS; else next_state=WAIT_SEN; end
        ADDRESS:   begin next_rst_mmcm=1'b1; next_den=1'b1; next_daddr=rom_do[38:32];
                         next_state=WAIT_A_DRDY; end
        WAIT_A_DRDY: if (DRDY) next_state=BITMASK; else next_state=WAIT_A_DRDY;
        BITMASK:   begin next_di=rom_do[31:16] & DO; next_state=BITSET; end
        BITSET:    begin next_di=rom_do[15:0] | DI; next_rom_addr=rom_addr+1'b1; next_state=WRITE; end
        WRITE:     begin next_dwe=1'b1; next_den=1'b1; next_state_count=state_count-1'b1;
                         next_state=WAIT_DRDY; end
        WAIT_DRDY: if (DRDY) begin
                         if (state_count > 0) next_state=ADDRESS; else next_state=WAIT_LOCK;
                     end else next_state=WAIT_DRDY;
        default:   next_state=RESTART;
        endcase
    end
endmodule
