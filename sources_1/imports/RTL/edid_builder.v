`timescale 1ns/1ps
//==============================================================================
// edid_builder - build a merged EDID for pass-through:
//     served modes = {display advertises} INTERSECT {FPGA can pass through}
//
//  Source : the display EDID already captured in i2c_master_edid (read via
//           rd_addr/rd_data, 1-cycle latency).
//  Sink   : edid_serve's double-buffered RAM (wr_*/commit).
//
//  FPGA-passable = pixel clock in [F_MIN_10K, F_MAX_10K] (10 kHz units), set by
//  the hdmi_input recovery MMCM lock window (default 60-120 MHz -> 6000..12000).
//  EDID has no "min pixel clock" field, so the low-clock floor is enforced by
//  REMOVING out-of-window modes from the established bitmap and standard slots.
//
//  v1 scope: filters established (B35-37) + standard (B38-53) timings, emits a
//  clean preferred DTD (from a survivor) + a range-limits cap + a name, bumps
//  identity (serial) for cache-defeat, recomputes the checksum. Copying in-window
//  *display DTDs* verbatim is a v2 enhancement (see EDID_MERGE_DESIGN.md).
//
//  CEA VICs: TV-style sinks often advertise 720p60 ONLY via the CEA-861 extension
//  Video Data Block (VIC 4), not in block-0 std/established timings. We walk the VDB
//  and, if VIC 4 is present, inject 1280x720@60 (std code 0x81C0) into a free standard
//  slot so it survives to the served EDID. (720p60 is the only in-window CEA mode
//  expressible as a standard timing; 720p50 / 1080p24-30 would need an injected DTD.)
//
//  DRAFT - unsimulated. See test plan in the design note.
//==============================================================================
module edid_builder #(
    parameter [15:0] F_MIN_10K = 16'd6000,    // 60.00 MHz (hdmi_input MMCM is x10: VCO=pixel*10>=600)
    parameter [15:0] F_MAX_10K = 16'd11000,   // 110.00 MHz (x10: VCO<=1440 -> pixel<=144, but the
                                              // x5 deserialiser is the real cap; 110 admits
                                              // 1280x1024@60 = 108 MHz with a little margin)
    // x10 lock band = 60-110 MHz. These masks MUST agree with F_MIN/F_MAX and with the
    // recovery MMCM's multiplier -- nothing checks them at runtime (see CAND below), so a
    // mode listed here that the MMCM cannot lock is offered to the PC and then black-screens.
    //   B36 bit3 = 1024x768@60 (65.0)  bit2 = 1024x768@70 (75.0)  bit1 = 1024x768@75 (78.75)
    //   Excluded: 640x480/720x400 (<60); 800x600@60/72/75 (40.0/50.0/49.5 -- DROPPED when the
    //             recovery MMCM went x15 -> x10 and the floor rose 40 -> 60 MHz);
    //             1280x1024@75 (135, over the ceiling).
    //   1280x1024@60 (108) cannot be expressed as an Established bit -- it is offered as a
    //   Standard Timing instead (CAND below).
    parameter [7:0]  EST_MASK35 = 8'h00,      // (was 0x01 = 800x600@60 -- now below the floor)
    parameter [7:0]  EST_MASK36 = 8'h0E,      // 1024x768@60/70/75 (b3,b2,b1)
    parameter [7:0]  EST_MASK37 = 8'h00
)(
    input  wire        clk,
    input  wire        rst,
    input  wire        start,        // 1-cycle: build now (i2c read done & sink present)
    // display EDID read-back (to i2c_master_edid)
    output reg  [7:0]  rd_addr,
    input  wire [7:0]  rd_data,
    // served EDID write port (to edid_serve)
    output reg  [7:0]  wr_addr,
    output reg  [7:0]  wr_data,
    output reg         wr_en,
    output reg         commit,
    output reg         busy,
    output reg         done
);
    //--------------------------------------------------------------------------
    // In-window standard-timing candidates: {byte1, byte2} EDID standard codes.
    // (pixel clocks are all in [60,120] MHz by construction, so membership in the
    // display's slots is the only runtime test needed.)
    //--------------------------------------------------------------------------
    localparam integer NCAND = 6;
    // Standard-timing codes we may offer the PC. There is NO runtime pixel-clock check --
    // an EDID Standard Timing carries only (Hactive, aspect, refresh), not a clock, so the
    // clock is implied by DMT/CVT and has to be known here. That is why this is a list.
    // EVERY ENTRY MUST SIT INSIDE [F_MIN_10K, F_MAX_10K] AND BE LOCKABLE BY THE RECOVERY
    // MMCM -- if it is not, the PC picks it and the screen goes black.
    // (The 800x600 entries were removed when the MMCM went x15 -> x10: at 40-50 MHz their
    //  VCO would be 400-500 MHz, under the 600 MHz minimum.)
    reg [15:0] CAND [0:NCAND-1];
    // higher index = higher pixel clock (used to pick the "best" survivor)
    initial begin
        CAND[0]  = 16'h6140; // 1024x768@60    65.00
        CAND[1]  = 16'h8100; // 1280x800@60    71.00 (RB)
        CAND[2]  = 16'h81C0; // 1280x720@60    74.25
        CAND[3]  = 16'h614A; // 1024x768@70    75.00
        CAND[4]  = 16'h614F; // 1024x768@75    78.75
        CAND[5]  = 16'h8180; // 1280x1024@60  108.00   <- 5:4 aspect (b1[7:6]=10)
    end

    //--------------------------------------------------------------------------
    // Base template, bytes 0..34 (header, identity, features, chromaticity).
    // Identity = CBC / product 0xF20A; serial is overwritten per build (cache-defeat).
    //--------------------------------------------------------------------------
    reg [7:0] TMPL [0:34];
    initial begin
        TMPL[0]=8'h00; TMPL[1]=8'hFF; TMPL[2]=8'hFF; TMPL[3]=8'hFF;
        TMPL[4]=8'hFF; TMPL[5]=8'hFF; TMPL[6]=8'hFF; TMPL[7]=8'h00;
        TMPL[8]=8'h0C; TMPL[9]=8'h43;            // mfg "CBC"
        TMPL[10]=8'h0A; TMPL[11]=8'hF2;          // product 0xF20A
        TMPL[12]=8'h00; TMPL[13]=8'h00; TMPL[14]=8'h00; TMPL[15]=8'h00; // serial (filled below)
        TMPL[16]=8'hFF; TMPL[17]=8'h11;          // week/year
        TMPL[18]=8'h01; TMPL[19]=8'h04;          // EDID 1.4
        TMPL[20]=8'hA2; TMPL[21]=8'h4F; TMPL[22]=8'h00; TMPL[23]=8'h78; TMPL[24]=8'h06; // 0xA2=HDMIa iface -> match the known-good static EDID (edid_rom byte20=0xA2) that passes video
        TMPL[25]=8'hEE; TMPL[26]=8'h91; TMPL[27]=8'hA3; TMPL[28]=8'h54; TMPL[29]=8'h4C;
        TMPL[30]=8'h99; TMPL[31]=8'h26; TMPL[32]=8'h0F; TMPL[33]=8'h50; TMPL[34]=8'h54;
    end

    // Preferred-DTD blob (bytes 54..71). Default = 1280x720@60 (verified). A full
    // build would select a blob matching the best survivor; v1 ships one and uses
    // it as the preferred whenever it is a survivor (else still emitted as a hint).
    // TODO: generate a per-candidate blob ROM with the Python EDID tool.
    reg [7:0] DTD_PREF [0:17];
    initial begin
        DTD_PREF[0]=8'h01; DTD_PREF[1]=8'h1D; DTD_PREF[2]=8'h00; DTD_PREF[3]=8'h72;
        DTD_PREF[4]=8'h51; DTD_PREF[5]=8'hD0; DTD_PREF[6]=8'h1E; DTD_PREF[7]=8'h20;
        DTD_PREF[8]=8'h6E; DTD_PREF[9]=8'h28; DTD_PREF[10]=8'h55; DTD_PREF[11]=8'h00;
        DTD_PREF[12]=8'h0F; DTD_PREF[13]=8'h48; DTD_PREF[14]=8'h42; DTD_PREF[15]=8'h00;
        DTD_PREF[16]=8'h00; DTD_PREF[17]=8'h1E;
    end

    // Range-limits descriptor (bytes 90..107): V 30-130, H 31-100k, max clk = F_MAX.
    // byte 9 of the descriptor = max pixel clock / 10 MHz (rounded up).
    wire [7:0] maxclk_byte = (F_MAX_10K + 16'd999) / 16'd1000; // 10kHz units -> *10MHz
    reg [7:0] RANGE [0:17];
    initial begin
        RANGE[0]=8'h00; RANGE[1]=8'h00; RANGE[2]=8'h00; RANGE[3]=8'hFD; RANGE[4]=8'h00;
        RANGE[5]=8'h1E; RANGE[6]=8'h82; RANGE[7]=8'h1F; RANGE[8]=8'h64; // 30-130V,31-100H
        RANGE[9]=8'h0C;  // placeholder; overwritten by maxclk_byte at stream time
        RANGE[10]=8'h01; RANGE[11]=8'h0A;
        RANGE[12]=8'h20; RANGE[13]=8'h20; RANGE[14]=8'h20; RANGE[15]=8'h20;
        RANGE[16]=8'h20; RANGE[17]=8'h20;
    end

    // Name descriptor (bytes 108..125): "FPGA-PT"
    reg [7:0] NAME [0:17];
    initial begin
        NAME[0]=8'h00; NAME[1]=8'h00; NAME[2]=8'h00; NAME[3]=8'hFC; NAME[4]=8'h00;
        NAME[5]="F"; NAME[6]="P"; NAME[7]="G"; NAME[8]="A"; NAME[9]="-";
        NAME[10]="P"; NAME[11]="T"; NAME[12]=8'h0A;
        NAME[13]=8'h20; NAME[14]=8'h20; NAME[15]=8'h20; NAME[16]=8'h20; NAME[17]=8'h20;
    end

    //--------------------------------------------------------------------------
    // Precompute registers
    //--------------------------------------------------------------------------
    reg [7:0]  est_out [0:2];        // filtered established bytes
    reg [7:0]  std_out [0:15];       // filtered standard slots (16 bytes)
    reg [3:0]  best_cand;            // index of highest-clk surviving candidate (NCAND=none)
    reg        have_survivor;
    reg [31:0] build_count;          // -> serial (identity bump per build)

    // CEA-861 extension VIC parsing (pick up 720p60 advertised only via the VDB)
    reg        ext_present;          // block-0 byte 126 (extension count) > 0
    reg        has_cea;              // block-1 byte 128 == 0x02 (CEA-861 tag)
    reg [7:0]  cea_dtd_off;          // block-1 byte 130 (DTD offset within the ext block)
    reg [7:0]  cea_p;                // data-block-collection walk pointer (abs addr)
    reg [4:0]  cea_len;              // current data block payload length
    reg [4:0]  cea_k;                // SVD index within the current Video Data Block
    reg        force_720p60;         // a VDB advertised VIC 4 (1280x720@60)
    integer    j2;
    reg        already720;
    reg [3:0]  freeslot;

    //--------------------------------------------------------------------------
    // FSM
    //--------------------------------------------------------------------------
    localparam S_IDLE   = 4'd0,
               S_EST_RD = 4'd1,  S_EST_WB = 4'd2,
               S_STD_RD = 4'd3,  S_STD_CMP= 4'd4,
               S_DESC   = 4'd5,
               S_STREAM = 4'd6,
               S_COMMIT = 4'd7,
               S_DONE   = 4'd8,
               // CEA-861 VDB walk to pick up VIC-only modes (e.g. 720p60)
               S_CEA0   = 4'd9,  S_CEA1    = 4'd10, S_CEA2   = 4'd11,
               S_CEAWK  = 4'd12, S_CEASVD  = 4'd13, S_CEAPL  = 4'd14;

    reg [3:0]  st;
    reg [4:0]  i;            // small loop index
    reg [1:0]  rdph;         // read pipeline phase (addr -> wait -> use)
    reg [7:0]  slot_hi;      // standard slot hi byte being compared
    reg [3:0]  cmp_j;        // candidate compare index
    reg [7:0]  out_idx;      // streaming byte address 0..127
    reg [15:0] csum;         // checksum accumulator

    // combinational: served byte for a given out_idx, EXCEPT byte 127 (checksum)
    function [7:0] served_byte(input [7:0] a);
        begin
            if (a <= 8'd34)        served_byte = TMPL[a];
            else if (a == 8'd35)   served_byte = est_out[0];
            else if (a == 8'd36)   served_byte = est_out[1];
            else if (a == 8'd37)   served_byte = est_out[2];
            else if (a <= 8'd53)   served_byte = std_out[a-8'd38];
            else if (a <= 8'd71)   served_byte = DTD_PREF[a-8'd54];
            else if (a <= 8'd89)   served_byte = 8'h00; // slot1 unused (in-window display DTDs live in the CEA ext, not block0)
            else if (a <= 8'd107) begin
                                   served_byte = (a==8'd99) ? maxclk_byte : RANGE[a-8'd90];
            end
            else if (a <= 8'd125)  served_byte = NAME[a-8'd108];
            else if (a == 8'd126)  served_byte = 8'h00; // no extension block
            else                   served_byte = 8'h00; // 127: checksum, handled in stream
        end
    endfunction

    integer k;
    always @(posedge clk) begin
        wr_en  <= 1'b0;
        commit <= 1'b0;
        done   <= 1'b0;
        if (rst) begin
            st <= S_IDLE; busy <= 1'b0; build_count <= 32'd1;
            rd_addr <= 8'd0; wr_addr <= 8'd0; wr_en <= 1'b0;
        end else case (st)
            //------------------------------------------------------------------
            S_IDLE: if (start) begin
                        busy <= 1'b1; have_survivor <= 1'b0; best_cand <= NCAND[3:0];
                        i <= 5'd0; rdph <= 2'd0; rd_addr <= 8'd35; st <= S_EST_RD;
                    end
            //----- established: read B35,36,37, AND with masks ----------------
            S_EST_RD: case (rdph)
                        2'd0: begin rd_addr <= 8'd35 + i[2:0]; rdph <= 2'd1; end
                        2'd1: rdph <= 2'd2;                     // rd_data valid next
                        2'd2: begin
                                case (i[1:0])
                                  2'd0: est_out[0] <= rd_data & EST_MASK35;
                                  2'd1: est_out[1] <= rd_data & EST_MASK36;
                                  default: est_out[2] <= rd_data & EST_MASK37;
                                endcase
                                if (i == 5'd2) begin i <= 5'd0; rdph <= 2'd0; st <= S_STD_RD; end
                                else begin i <= i + 1'b1; rdph <= 2'd0; end
                              end
                      endcase
            //----- standard: for each of 8 slots, keep if it matches a candidate
            //      i indexes slot 0..7; read hi=B38+2i, lo=B39+2i -----
            S_STD_RD: case (rdph)
                        2'd0: begin rd_addr <= 8'd38 + {i[2:0],1'b0}; rdph <= 2'd1; end
                        2'd1: rdph <= 2'd2;
                        2'd2: begin slot_hi <= rd_data; rd_addr <= 8'd39 + {i[2:0],1'b0};
                                    rdph <= 2'd3; end
                        2'd3: begin // rd_data = lo byte now; compare {slot_hi,lo}
                                    cmp_j <= 4'd0; st <= S_STD_CMP;
                              end
                      endcase
            S_STD_CMP: begin
                        // default: this slot unused unless a candidate matches
                        if (cmp_j == 4'd0) begin
                            std_out[{i[2:0],1'b0}]   <= 8'h01;
                            std_out[{i[2:0],1'b0}+1] <= 8'h01;
                        end
                        if ({slot_hi, rd_data} == CAND[cmp_j]) begin
                            std_out[{i[2:0],1'b0}]   <= slot_hi;
                            std_out[{i[2:0],1'b0}+1] <= rd_data;
                            have_survivor <= 1'b1;
                            if (!have_survivor || cmp_j > best_cand) best_cand <= cmp_j;
                        end
                        if (cmp_j == NCAND-1) begin
                            if (i == 5'd7) begin i <= 5'd0; rdph <= 2'd0; st <= S_CEA0; end
                            else begin i <= i + 1'b1; rdph <= 2'd0; st <= S_STD_RD; end
                        end else cmp_j <= cmp_j + 1'b1;
                       end
            //----- CEA-861 extension: walk the VDB, note any VIC-only modes -----
            S_CEA0: case (rdph)                                   // read block-0 byte 126 (ext count)
                      2'd0: begin rd_addr <= 8'd126; rdph <= 2'd1; end
                      2'd1: rdph <= 2'd2;
                      default: begin ext_present <= (rd_data != 8'd0); rdph <= 2'd0; st <= S_CEA1; end
                    endcase
            S_CEA1: case (rdph)                                   // read block-1 byte 128 (CEA tag)
                      2'd0: begin rd_addr <= 8'd128; rdph <= 2'd1; end
                      2'd1: rdph <= 2'd2;
                      default: begin has_cea <= (rd_data == 8'h02); rdph <= 2'd0; st <= S_CEA2; end
                    endcase
            S_CEA2: case (rdph)                                   // read block-1 byte 130 (DTD offset)
                      2'd0: begin rd_addr <= 8'd130; rdph <= 2'd1; end
                      2'd1: rdph <= 2'd2;
                      default: begin
                          cea_dtd_off <= rd_data; cea_p <= 8'd132; force_720p60 <= 1'b0; rdph <= 2'd0;
                          // no extension, not CEA, or no data-block collection (offset <= 4) -> skip
                          if (!ext_present || !has_cea || rd_data <= 8'd4) st <= S_CEAPL;
                          else st <= S_CEAWK;
                      end
                    endcase
            S_CEAWK: case (rdph)                                  // read a data-block header byte
                      2'd0: if (cea_p >= (8'd128 + cea_dtd_off)) st <= S_CEAPL;   // walked the whole collection
                            else begin rd_addr <= cea_p; rdph <= 2'd1; end
                      2'd1: rdph <= 2'd2;
                      default: begin
                          cea_len <= rd_data[4:0]; cea_k <= 5'd1; rdph <= 2'd0;
                          if (rd_data[7:5] == 3'd2) st <= S_CEASVD;               // tag 2 = Video Data Block
                          else begin cea_p <= cea_p + 8'd1 + {3'd0, rd_data[4:0]}; st <= S_CEAWK; end
                      end
                    endcase
            S_CEASVD: case (rdph)                                 // read one SVD (VIC) byte
                      2'd0: begin rd_addr <= cea_p + {3'd0, cea_k}; rdph <= 2'd1; end
                      2'd1: rdph <= 2'd2;
                      default: begin
                          if ((rd_data & 8'h7F) == 8'd4) force_720p60 <= 1'b1;    // VIC 4 = 1280x720@60
                          rdph <= 2'd0;
                          if (cea_k >= cea_len) begin
                              cea_p <= cea_p + 8'd1 + {3'd0, cea_len}; st <= S_CEAWK;
                          end else cea_k <= cea_k + 1'b1;
                      end
                    endcase
            S_CEAPL: begin                                        // inject 720p60 into a free std slot
                        if (force_720p60) begin
                            already720 = 1'b0; freeslot = 4'd8;
                            for (j2 = 0; j2 < 8; j2 = j2 + 1) begin
                                if (std_out[j2*2] == 8'h81 && std_out[j2*2+1] == 8'hC0)
                                    already720 = 1'b1;
                                else if (std_out[j2*2] == 8'h01 && std_out[j2*2+1] == 8'h01 && freeslot == 4'd8)
                                    freeslot = j2[3:0];
                            end
                            if (!already720 && freeslot != 4'd8) begin
                                std_out[freeslot*2]     <= 8'h81;
                                std_out[freeslot*2 + 1] <= 8'hC0;
                                have_survivor <= 1'b1;
                            end
                        end
                        st <= S_DESC;
                    end
            //----- descriptors are emitted from ROMs during streaming; nothing
            //      to precompute in v1 (preferred DTD = DTD_PREF blob). ---------
            S_DESC: begin
                        // write the per-build serial into the template (cache-defeat)
                        TMPL[12] <= build_count[7:0];
                        TMPL[13] <= build_count[15:8];
                        TMPL[14] <= build_count[23:16];
                        TMPL[15] <= build_count[31:24];
                        out_idx <= 8'd0; csum <= 16'd0; st <= S_STREAM;
                    end
            //----- stream 0..127 into edid_serve, accumulating the checksum -----
            S_STREAM: begin
                        wr_en   <= 1'b1;
                        wr_addr <= out_idx;
                        if (out_idx == 8'd127) begin
                            wr_data <= (8'd256 - csum[7:0]); // checksum: makes sum mod 256 = 0
                            st <= S_COMMIT;
                        end else begin
                            wr_data <= served_byte(out_idx);
                            csum    <= csum + served_byte(out_idx);
                            out_idx <= out_idx + 1'b1;
                        end
                    end
            //----- atomic swap to the freshly built bank -----
            S_COMMIT: begin commit <= 1'b1; build_count <= build_count + 1'b1; st <= S_DONE; end
            S_DONE:   begin busy <= 1'b0; done <= 1'b1; st <= S_IDLE; end
        endcase
    end
endmodule
