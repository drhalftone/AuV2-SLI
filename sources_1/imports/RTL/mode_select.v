`timescale 1ns/1ps
//==============================================================================
// mode_select - device-agnostic EDID -> best generatable mode (Phase C, C1).
//
//   Reads the connected display's EDID (via i2c_master_edid's rd_addr/rd_data
//   read-back port, or a TB RAM), determines which curated table modes it
//   supports, and picks the best by HIGHEST REFRESH -> HIGHEST PIXEL COUNT under
//   the 85 MHz ceiling.
//
//   The pick walks the PRIO[] list (see below), NOT the raw table index: mode_idx
//   is a shared key into mode_table.vh AND drp_recfg's per-mode MMCM settings, so
//   the table cannot be re-sorted to encode priority. Every table index has DRP
//   settings in drp_recfg, so whatever is picked is always generatable.
//   Empty intersection -> failsafe (index 12).
//
//   C1 parses the BASE block only: Established timings (B35-37), Standard timings
//   (B38-53), and the four Detailed Timing Descriptors (B54-125). The CEA-861
//   extension (DTDs + VIC SVDs) is C2.
//
//   Matching:
//     - Established: fixed bit -> table-index map.
//     - Standard:    (Hactive, aspect->Vactive, refresh=field+60) exact-match.
//     - DTD:         (Hactive, Vactive) match + NEAREST pixel clock (disambiguates
//                    e.g. 800x600 @72=50.0 vs @75=49.5 MHz; a tolerance window
//                    would over-mark). Interlaced descriptors are skipped.
//   Empty intersection -> 640x480@60 failsafe (table index 12).
//==============================================================================
module mode_select #(
    parameter integer CEIL_KHZ = 85000
)(
    input  wire        clk,
    input  wire        rst,
    input  wire        start,          // 1-cycle pulse: EDID ready, begin parse
    // EDID byte read-back (drive an i2c_master_edid or a TB RAM; 1-cycle latency)
    output reg  [7:0]  edid_addr,
    input  wire [7:0]  edid_data,
    // chosen mode (full timing)
    output reg         mode_valid,
    output reg  [3:0]  mode_idx,
    output reg  [11:0] o_hact, o_vact,
    output reg  [11:0] o_hfp, o_hsync, o_hbp,
    output reg  [11:0] o_vfp, o_vsync, o_vbp,
    output reg         o_hpol, o_vpol,
    output reg  [7:0]  o_refr,        // selected refresh rate (Hz)
    output reg  [16:0] o_pclk_khz,
    output reg  [12:0] o_supported    // debug: the supported mask
);
    localparam integer NMODE = 13;
    localparam [3:0]   FAILSAFE = 4'd12;

    // ---- selection priority: HIGHEST REFRESH -> HIGHEST PIXEL COUNT ----
    // PRIO[p] = table index to try at priority p (p=0 is tried first).
    //
    // Priority is deliberately NOT the table index. mode_idx is a shared key into
    // mode_table.vh (geometry) AND drp_recfg's per-mode MMCM M/D/O settings, so
    // re-sorting the table to express priority would hand each mode another mode's
    // pixel clock. Keeping the index binding fixed and ordering here decouples the
    // two -- and every index is a mode drp_recfg can actually generate, so a pick
    // is always clockable.
    //
    // The old "table is priority-sorted, take the lowest index" scheme had drifted
    // out of order twice: idx5 (1024x768@70) outranked the 72 Hz modes, and idx8
    // (1280x720@60) outranked idx9 (1280x800@60) despite 100k fewer pixels.
    reg [3:0] PRIO [0:NMODE-1];
    initial begin
        PRIO[0]  = 4'd0;    // 800x600@120   480,000 px
        PRIO[1]  = 4'd1;    // 640x480@120   307,200
        PRIO[2]  = 4'd2;    // 1024x768@75   786,432
        PRIO[3]  = 4'd3;    // 800x600@75    480,000
        PRIO[4]  = 4'd4;    // 640x480@75    307,200
        PRIO[5]  = 4'd6;    // 800x600@72    480,000   (72 Hz now beats 70 Hz)
        PRIO[6]  = 4'd7;    // 640x480@72    307,200
        PRIO[7]  = 4'd5;    // 1024x768@70   786,432
        PRIO[8]  = 4'd9;    // 1280x800@60 1,024,000   (more pixels wins the 60 Hz tie)
        PRIO[9]  = 4'd8;    // 1280x720@60   921,600
        PRIO[10] = 4'd10;   // 1024x768@60   786,432
        PRIO[11] = 4'd11;   // 800x600@60    480,000
        PRIO[12] = 4'd12;   // 640x480@60    307,200  (failsafe)
    end

    // ---- curated table (indexed by mode_idx; NOT in priority order -- see PRIO) ----
    reg [11:0] T_HACT [0:NMODE-1];
    reg [11:0] T_VACT [0:NMODE-1];
    reg [7:0]  T_REFR [0:NMODE-1];
    reg [16:0] T_PCLK [0:NMODE-1];
    reg [11:0] T_HFP  [0:NMODE-1];
    reg [11:0] T_HSYNC[0:NMODE-1];
    reg [11:0] T_HBP  [0:NMODE-1];
    reg [11:0] T_VFP  [0:NMODE-1];
    reg [11:0] T_VSYNC[0:NMODE-1];
    reg [11:0] T_VBP  [0:NMODE-1];
    reg        T_HPOL [0:NMODE-1];
    reg        T_VPOL [0:NMODE-1];
    initial begin
        `include "mode_table.vh"
    end

    // ---- captured EDID bytes 35..125 (established + standard + 4 DTDs) ----
    localparam integer A_LO = 35, A_HI = 125;
    reg [7:0] edb [0:A_HI-A_LO];        // 91 bytes

    reg [12:0] supported;
    integer i;

    // ---- FSM ----
    localparam S_IDLE=3'd0, S_SWEEP=3'd1, S_ESTAB=3'd2, S_STD=3'd3, S_DTD=3'd4, S_PICK=3'd5;
    reg [2:0]  st;
    reg [7:0]  saddr;        // sweep address
    reg [1:0]  sub;          // sweep sub-step (addr/wait/capture)
    reg [3:0]  k;            // standard-timing index 0..7 / DTD index 0..3
    reg [3:0]  chosen;

    // ------- combinational decode of the current Standard-timing entry k -------
    wire [7:0] sb0 = edb[(8'd38-A_LO) + {k,1'b0}];   // 38 + 2k
    wire [7:0] sb1 = edb[(8'd39-A_LO) + {k,1'b0}];   // 39 + 2k
    wire [11:0] s_hact   = ({4'd0, sb0} + 12'd31) << 3;        // (b0+31)*8
    wire [1:0]  s_aspect = sb1[7:6];
    wire [7:0]  s_refr   = {2'd0, sb1[5:0]} + 8'd60;
    wire [19:0] s_h20    = {8'd0, s_hact};
    wire [11:0] s_vact   = (s_aspect==2'b00) ? ((s_h20*20'd5) >> 3) :  // 16:10
                           (s_aspect==2'b01) ? ((s_h20*20'd3) >> 2) :  // 4:3
                           (s_aspect==2'b11) ? ((s_h20*20'd9) >> 4) :  // 16:9
                                                12'hFFF;               // 5:4 (not in table)
    wire        s_valid  = ~((sb0==8'h01)&&(sb1==8'h01)) && (sb0!=8'h00);

    // ------- combinational decode of the current DTD d (=k) ---------------------
    wire [7:0] dbase = (8'd54-A_LO) + ({4'd0,k}*8'd18);    // 19 + 18k
    wire [7:0] d0  = edb[dbase],      d1  = edb[dbase+8'd1];
    wire [7:0] d2  = edb[dbase+8'd2], d3  = edb[dbase+8'd3], d4 = edb[dbase+8'd4];
    wire [7:0] d5  = edb[dbase+8'd5], d6  = edb[dbase+8'd6], d7 = edb[dbase+8'd7];
    wire [7:0] d17 = edb[dbase+8'd17];
    wire [15:0] d_pclk10  = {d1, d0};                 // EDID 10kHz units
    wire [16:0] d_pclk    = d_pclk10 * 17'd10;        // -> kHz
    wire [11:0] d_hact    = {d4[7:4], d2};
    wire [11:0] d_vact    = {d7[7:4], d5};
    wire        d_valid   = (d_pclk10 != 16'd0) && ~d17[7];   // pclk!=0 (real DTD) & not interlaced

    // DTD nearest-pclk match among (Hact,Vact)-matching entries
    reg [16:0] bestdiff, diff_i;
    reg [3:0]  besti;
    reg        bestok;
    always @* begin
        bestdiff = 17'h1FFFF; besti = 4'd0; bestok = 1'b0;
        for (i=0;i<NMODE;i=i+1) begin
            if (d_hact==T_HACT[i] && d_vact==T_VACT[i]) begin
                diff_i = (T_PCLK[i] > d_pclk) ? (T_PCLK[i]-d_pclk) : (d_pclk-T_PCLK[i]);
                if (diff_i < bestdiff) begin bestdiff = diff_i; besti = i[3:0]; bestok = 1'b1; end
            end
        end
    end
    wire dtd_accept = d_valid && bestok && (bestdiff < (d_pclk >> 2));   // within 25%

    always @(posedge clk) begin
        if (rst) begin
            st <= S_IDLE; mode_valid <= 1'b0; supported <= 13'd0;
        end else case (st)
        // ----------------------------------------------------------------
        S_IDLE: if (start) begin
                    supported <= 13'd0; saddr <= A_LO[7:0]; sub <= 2'd0;
                    mode_valid <= 1'b0; st <= S_SWEEP;
                end
        // ---- copy EDID bytes 35..125 into edb (3 cycles/byte: addr/wait/capture)
        S_SWEEP: case (sub)
                    2'd0: begin edid_addr <= saddr; sub <= 2'd1; end
                    2'd1: sub <= 2'd2;
                    2'd2: begin
                        edb[saddr - A_LO[7:0]] <= edid_data;
                        if (saddr == A_HI[7:0]) begin st <= S_ESTAB; end
                        else begin saddr <= saddr + 8'd1; sub <= 2'd0; end
                    end
                 endcase
        // ---- Established timings: fixed bit -> table-index map
        S_ESTAB: begin
                    // byte 35 (edb[0]): b5=640x480@60(12) b3=640x480@72(7) b2=640x480@75(4) b0=800x600@60(11)
                    if (edb[0][5]) supported[12] <= 1'b1;
                    if (edb[0][3]) supported[7]  <= 1'b1;
                    if (edb[0][2]) supported[4]  <= 1'b1;
                    if (edb[0][0]) supported[11] <= 1'b1;
                    // byte 36 (edb[1]): b7=800x600@72(6) b6=800x600@75(3) b3=1024x768@60(10)
                    //                   b2=1024x768@70(5) b1=1024x768@75(2)
                    if (edb[1][7]) supported[6]  <= 1'b1;
                    if (edb[1][6]) supported[3]  <= 1'b1;
                    if (edb[1][3]) supported[10] <= 1'b1;
                    if (edb[1][2]) supported[5]  <= 1'b1;
                    if (edb[1][1]) supported[2]  <= 1'b1;
                    // byte 37: none of its modes are in the curated table
                    k <= 4'd0; st <= S_STD;
                 end
        // ---- Standard timings: 8 entries, exact (Hact,Vact,refresh) match
        S_STD: begin
                    if (s_valid)
                        for (i=0;i<NMODE;i=i+1)
                            if (T_HACT[i]==s_hact && T_VACT[i]==s_vact && T_REFR[i]==s_refr)
                                supported[i] <= 1'b1;
                    if (k == 4'd7) begin k <= 4'd0; st <= S_DTD; end
                    else k <= k + 4'd1;
               end
        // ---- Detailed timings: 4 descriptors, (Hact,Vact)+nearest-pclk match
        S_DTD: begin
                    if (dtd_accept) supported[besti] <= 1'b1;
                    if (k == 4'd3) st <= S_PICK;
                    else k <= k + 4'd1;
               end
        // ---- pick the best supported mode by PRIO order, else failsafe.
        // Walk priority positions high->low so the last write (p=0, the highest
        // priority) wins. The pclk ceiling still gates every candidate.
        S_PICK: begin
                    chosen = FAILSAFE;
                    for (i=NMODE-1;i>=0;i=i-1)
                        if (supported[PRIO[i]] && (T_PCLK[PRIO[i]] <= CEIL_KHZ[16:0]))
                            chosen = PRIO[i];
                    o_supported <= supported;
                    mode_idx   <= chosen;
                    o_hact <= T_HACT[chosen]; o_vact <= T_VACT[chosen];
                    o_hfp  <= T_HFP[chosen];  o_hsync<= T_HSYNC[chosen]; o_hbp <= T_HBP[chosen];
                    o_vfp  <= T_VFP[chosen];  o_vsync<= T_VSYNC[chosen]; o_vbp <= T_VBP[chosen];
                    o_hpol <= T_HPOL[chosen]; o_vpol <= T_VPOL[chosen];
                    o_refr <= T_REFR[chosen];
                    o_pclk_khz <= T_PCLK[chosen];
                    mode_valid <= 1'b1;
                    st <= S_IDLE;
               end
        default: st <= S_IDLE;
        endcase
    end
endmodule
