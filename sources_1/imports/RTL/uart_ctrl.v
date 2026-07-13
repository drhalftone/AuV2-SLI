`timescale 1ns/1ps
//==============================================================================
// uart_ctrl.v -- AuV2-SLI host control protocol (FT2232H ch.B, 0xA5 framed).
//
// Receive-side command engine that mirrors host/lauauboard.cpp byte-for-byte.
// Consumes a byte stream from uart_rx (data/valid) and produces a response
// stream to a SHARED uart_tx via a producer handshake (tx_data/tx_send/tx_busy,
// with `tx_active` telling the arbiter we own the line). All logic is in the
// clk domain (clk100); the table read ports are exposed for the pixel datapath
// (Stage 2) and the register controls (sli_ctrl) are quasi-static.
//
// Frames (SYNC = 0xA5; SYNC is NOT included in any checksum):
//   write  : A5 57 ADDR DATA CK         CK: (0x57+ADDR+DATA+CK)      == 0 mod256
//            -> 'K' ok / 'N' read-only|undefined / 'E' bad checksum
//   read   : A5 52 ADDR CK              CK: (0x52+ADDR+CK)           == 0 mod256
//            -> ADDR DATA CK2  with (ADDR+DATA+CK2)==0 ; or 'E' on bad request CK
//   upload : A5 5B TGT D[0..N-1] CK     CK: (TGT+sum(D)+CK)          == 0 mod256
//            N = 720 (TGT 0x00) / 1280 (TGT 0x01) / 256 (TGT 0x02)
//            -> 'K' ok / 'E' bad checksum|unknown target  (table committed only on 'K')
//   rdtbl  : A5 72 TGT CK               CK: (0x72+TGT+CK)            == 0 mod256
//            -> TGT D[0..N-1] CK2  with (TGT+sum(D)+CK2)==0 ; or 'E' on bad CK|target
//            (same N per TGT as upload; lets the host verify an uploaded table)
//            TGT 0x03 = EDID: 256 B of the display's captured EDID (READ-ONLY --
//            it is not an upload target; A5 5B 03 is rejected with 'E'). Always
//            256 B; if the display has no extension block, bytes 128..255 are
//            stale RAM -- byte 0x7E of block 0 is the authoritative ext count.
//
// Registers (read any address -> data, undefined reads return 0x00):
//   0x00 ID      = 0x48 'H'      (RO)        0x02 STATUS = live `led` byte (RO)
//   0x01 VERSION = 0x01          (RO)        0x06 FLAGS  = {.., usb_sw_en, lut_loaded} (RO)
//   0x10 PINS    = {eff_sw[3:0], phys_sw[3:0]}  (RO -- active vs physical R/G/B/orient)
//   0x13 SLICTRL = {7:sw_en, 6:mode_en, 5:mode_val, 3:R,2:G,1:B,0:orient}  (R/W)
//        sw_en   : USB drives R/G/B/orient instead of the physical SW[3:0] pins.
//        mode_en : USB drives the SLI pattern enable instead of the camera "mode" GPIO
//                  (C1_in[1]). That pin is PULLED LOW in the XDC ("default passthrough
//                  for colour-bar test"), so with no camera board attached pattern_gen
//                  is off and the vga colour bars just pass through. Set mode_en=1,
//                  mode_val=1 to turn the SLI fringes on over USB with no camera.
//        Note the corr transfer LUT (target 0x02) only shapes FRINGE pixels, so it has
//        no visible effect until the pattern generator is actually enabled.
//
//   Offline-mode decision (RO) -- what mode_select picked from the display's EDID,
//   and what it had to pick from. Without these the choice is unobservable: you can
//   only infer it by decoding the EDID by hand and measuring the frame rate.
//   0x20 MODE    = {7:valid, 6:edid_ok, 3..0:mode_idx}   curated-table index in use
//   0x21 REFR    = refresh in Hz
//   0x22 / 0x23  = h_active  lo / hi (12-bit)
//   0x24 / 0x25  = v_active  lo / hi (12-bit)
//   0x26 / 0x27 / 0x28 = pixel clock in kHz, lo / mid / hi (17-bit)
//   0x29 / 0x2A  = supported-mode mask, lo / hi (13-bit; bit i = table index i)
//==============================================================================
module uart_ctrl #(
    parameter [7:0] ID_MAGIC = 8'h48,    // 'H'
    parameter [7:0] VERSION  = 8'h01
)(
    input  wire        clk,
    input  wire        rst,

    // ---- from uart_rx ----
    input  wire [7:0]  rx_data,
    input  wire        rx_valid,

    // ---- to the shared uart_tx (via arbiter) ----
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output wire        tx_active,        // high while we own/need the TX line

    // ---- live status surfaced through registers ----
    input  wire [7:0]  led,    // reg 0x02 -- video/camera handshake byte
    input  wire [7:0]  pins,   // reg 0x10 -- {eff_sw[3:0], phys_sw[3:0]} (active vs physical switches)

    // ---- quasi-static control outputs (Stage 2: drive pixel_pipe) ----
    output reg  [7:0]  sli_ctrl,         // register 0x13
    output wire        sli_ctrl_en,      // = sli_ctrl[7]  (USB overrides switches)
    output wire        lut_loaded,       // a table has been uploaded since reset

    // ---- table read ports (Stage 2: read by the pattern datapath) ----
    // Defaulted in the VHDL component decl so the top can leave them open for now.
    input  wire [7:0]  corr_addr,  output reg [7:0] corr_dout,   // 256-entry correction
    input  wire [9:0]  lut_addr,   output reg [7:0] lut_dout,    // 720-entry row cosine
    input  wire [10:0] lutv_addr,  output reg [7:0] lutv_dout,   // 1280-entry col cosine

    // ---- captured-EDID read port (TGT_EDID) ----
    // Unlike the tables above, this RAM lives in edid_merge, so we DRIVE the
    // address outward and the data comes back registered one clock later --
    // the same latency as the local RAMs, so S_RTAB needs no special-casing.
    output wire [7:0]  edid_rd_addr,
    input  wire [7:0]  edid_rd_data,

    // ---- offline mode decision (regs 0x20..0x2A, read-only) ----
    // What mode_select chose from the display's EDID, and what it had to choose
    // from. Quasi-static: only changes when a new EDID is parsed. Caller supplies
    // these already in this clock domain.
    input  wire [3:0]  mode_idx_i,      // applied curated-table index
    input  wire        mode_valid_i,    // a mode has been picked
    input  wire        mode_edid_ok_i,  // display EDID block-0 checksum good
    input  wire [7:0]  mode_refr_i,     // refresh (Hz)
    input  wire [11:0] mode_hact_i,     // active pixels
    input  wire [11:0] mode_vact_i,     // active lines
    input  wire [16:0] mode_pclk_i,     // pixel clock (kHz)
    input  wire [12:0] mode_supp_i,     // 13-bit supported-mode mask

    // ---- radiometric transfer LUT, live in the PIXEL datapath ----
    // pattern_gen's LUT seam: it presents the raw cosine value and consumes the
    // linearized one in the SAME pipeline stage (pat_out = flash_d ? pat : lut_dout),
    // so this read must be COMBINATIONAL -- a registered BRAM read would apply each
    // pixel's correction to the next pixel. Async read => Vivado infers distributed
    // RAM (256x8 LUTRAM), which is what we want.
    //
    // Clock domains: written on clk (clk100) during an upload, read continuously from
    // pixel_clk. No handshake -- a table upload takes a few ms and tears the curve for
    // that window (a few pixels get a mix of old/new). Upload while not scanning.
    input  wire [7:0]  corr_pat_addr,
    output wire [7:0]  corr_pat_dout
);
    // ---- protocol constants ----
    localparam [7:0] SYNC = 8'hA5;
    localparam [7:0] OP_W = 8'h57, OP_R = 8'h52, OP_L = 8'h5B, OP_LR = 8'h72;  // W R upload read-table('r')
    localparam [7:0] ACK_K = 8'h4B, ACK_E = 8'h45, ACK_N = 8'h4E;
    localparam [7:0] TGT_LUT = 8'h00, TGT_LUTV = 8'h01, TGT_CORR = 8'h02;
    localparam [7:0] TGT_EDID = 8'h03;   // read-only: the display's captured EDID

    // ---- table RAMs (write-through during upload; read ports for Stage 2) ----
    reg [7:0] corr [0:255];
    reg [7:0] lut  [0:719];
    reg [7:0] lutv [0:1279];

    // corr powers up as IDENTITY (corr[i] = i), not zero. It is now live in the pixel
    // datapath (pattern_gen's radiometric transfer LUT), so an all-zero power-up would
    // black the pattern out until a table was uploaded. Identity == no correction.
    integer ci;
    initial for (ci = 0; ci < 256; ci = ci + 1) corr[ci] = ci[7:0];

    // Combinational (async) read into the pixel datapath -- see the port comment.
    assign corr_pat_dout = corr[corr_pat_addr];

    // ---- loaded flags ----
    reg corr_ld = 1'b0, lut_ld = 1'b0, lutv_ld = 1'b0;
    assign lut_loaded  = corr_ld | lut_ld | lutv_ld;
    assign sli_ctrl_en = sli_ctrl[7];

    // ---- readback (read-table) addressing ----
    // During a readback the table RAMs are addressed by rd_idx; otherwise by the
    // external Stage-2 read ports. UART byte time (~8680 clk) dwarfs the 1-cycle
    // BRAM read latency, so the byte is always settled before it is sent.
    reg [11:0] rd_idx   = 12'd0;
    reg        rb_active = 1'b0;
    reg [1:0]  rb_ph    = 2'd0;   // 0=target prologue, 1=data, 2=checksum

    // ---- register read mux ----
    function [7:0] regread;
        input [7:0] a;
        case (a)
            8'h00:   regread = ID_MAGIC;
            8'h01:   regread = VERSION;
            8'h02:   regread = led;
            8'h06:   regread = {6'b0, sli_ctrl[7], (corr_ld | lut_ld | lutv_ld)};
            8'h10:   regread = pins;
            8'h13:   regread = sli_ctrl;
            // ---- offline mode decision (read-only) ----
            8'h20:   regread = {mode_valid_i, mode_edid_ok_i, 2'b0, mode_idx_i};
            8'h21:   regread = mode_refr_i;
            8'h22:   regread = mode_hact_i[7:0];
            8'h23:   regread = {4'b0, mode_hact_i[11:8]};
            8'h24:   regread = mode_vact_i[7:0];
            8'h25:   regread = {4'b0, mode_vact_i[11:8]};
            8'h26:   regread = mode_pclk_i[7:0];
            8'h27:   regread = mode_pclk_i[15:8];
            8'h28:   regread = {7'b0, mode_pclk_i[16]};
            8'h29:   regread = mode_supp_i[7:0];
            8'h2A:   regread = {3'b0, mode_supp_i[12:8]};
            default: regread = 8'h00;
        endcase
    endfunction

    // ---- FSM ----
    localparam [3:0]
        S_SYNC  = 4'd0,  S_OP    = 4'd1,
        S_WADDR = 4'd2,  S_WDATA = 4'd3,  S_WCK = 4'd4,
        S_RADDR = 4'd5,  S_RCK   = 4'd6,
        S_UTGT  = 4'd7,  S_UDATA = 4'd8,  S_UCK = 4'd9,
        S_RESP  = 4'd10,
        S_RTGT  = 4'd11, S_RTCK  = 4'd12, S_RTAB = 4'd13;

    reg [3:0]  state = S_SYNC;
    reg [7:0]  addr, dbyte, sum8;
    reg [11:0] cnt, len;             // up to 1280
    reg [1:0]  tgt;                  // 0=lut 1=lutv 2=corr 3=edid

    // readback byte source (selected by target)
    wire [7:0] rb_dout = (tgt == 2'd3) ? edid_rd_data :
                         (tgt == 2'd2) ? corr_dout    :
                         (tgt == 2'd1) ? lutv_dout    : lut_dout;

    // EDID RAM address (external, in edid_merge). Only sampled while we are
    // streaming TGT_EDID; harmless otherwise -- it is a pure read port.
    assign edid_rd_addr = rd_idx[7:0];

    // response buffer (1 or 3 bytes)
    reg [7:0]  resp [0:2];
    reg [1:0]  resp_len, resp_idx;

    assign tx_active = (state == S_RESP) || (state == S_RTAB);

    // synchronous table read ports (kept always -> RAMs are not optimised away).
    // Address from rd_idx during a readback stream, else from the Stage-2 ports.
    wire [7:0]  corr_ra = rb_active ? rd_idx[7:0]  : corr_addr;
    wire [9:0]  lut_ra  = rb_active ? rd_idx[9:0]  : lut_addr;
    wire [10:0] lutv_ra = rb_active ? rd_idx[10:0] : lutv_addr;
    always @(posedge clk) begin
        corr_dout <= corr[corr_ra];
        lut_dout  <= lut[lut_ra];
        lutv_dout <= lutv[lutv_ra];
    end

    // checksum helpers (8-bit wraparound)
    wire [7:0] w_sum  = OP_W  + addr + dbyte + rx_data; // == 0 -> good write CK
    wire [7:0] r_sum  = OP_R  + addr + rx_data;         // == 0 -> good read CK
    wire [7:0] u_sum  = sum8  + rx_data;                // == 0 -> good upload CK
    wire [7:0] rt_sum = OP_LR + dbyte + rx_data;        // == 0 -> good read-table request CK
    wire [7:0] rd_data = regread(addr);
    wire [7:0] rd_ck   = (8'h00 - addr - rd_data);      // -(addr+data) mod 256

    integer i;
    always @(posedge clk) begin
        if (rst) begin
            state <= S_SYNC; tx_send <= 1'b0; sli_ctrl <= 8'h00;
            corr_ld <= 1'b0; lut_ld <= 1'b0; lutv_ld <= 1'b0;
            resp_len <= 2'd0; resp_idx <= 2'd0;
            rb_active <= 1'b0; rb_ph <= 2'd0; rd_idx <= 12'd0;
        end else begin
            tx_send <= 1'b0;                            // default: no TX strobe

            case (state)
                // ---- frame sync + opcode dispatch -------------------------
                S_SYNC: if (rx_valid && rx_data == SYNC) state <= S_OP;

                S_OP: if (rx_valid) begin
                    case (rx_data)
                        OP_W:  state <= S_WADDR;
                        OP_R:  state <= S_RADDR;
                        OP_L:  state <= S_UTGT;
                        OP_LR: state <= S_RTGT;
                        SYNC:    state <= S_OP;          // re-sync on a fresh A5
                        default: state <= S_SYNC;
                    endcase
                end

                // ---- write: A5 57 ADDR DATA CK ----------------------------
                S_WADDR: if (rx_valid) begin addr  <= rx_data; state <= S_WDATA; end
                S_WDATA: if (rx_valid) begin dbyte <= rx_data; state <= S_WCK;   end
                S_WCK:   if (rx_valid) begin
                    if (w_sum != 8'h00) begin            // bad checksum
                        resp[0] <= ACK_E; resp_len <= 2'd1;
                    end else if (addr == 8'h13) begin    // the writable register
                        sli_ctrl <= dbyte; resp[0] <= ACK_K; resp_len <= 2'd1;
                    end else begin                       // RO / undefined
                        resp[0] <= ACK_N; resp_len <= 2'd1;
                    end
                    resp_idx <= 2'd0; state <= S_RESP;
                end

                // ---- read: A5 52 ADDR CK ; reply ADDR DATA CK2 ------------
                S_RADDR: if (rx_valid) begin addr <= rx_data; state <= S_RCK; end
                S_RCK:   if (rx_valid) begin
                    if (r_sum != 8'h00) begin            // bad request checksum
                        resp[0] <= ACK_E; resp_len <= 2'd1;
                    end else begin
                        resp[0] <= addr; resp[1] <= rd_data; resp[2] <= rd_ck;
                        resp_len <= 2'd3;
                    end
                    resp_idx <= 2'd0; state <= S_RESP;
                end

                // ---- upload: A5 5B TGT D[0..N-1] CK ----------------------
                S_UTGT: if (rx_valid) begin
                    sum8 <= rx_data;                     // CK base = TARGET
                    cnt  <= 12'd0;
                    case (rx_data)
                        TGT_LUT:  begin tgt <= 2'd0; len <= 12'd720;  state <= S_UDATA; end
                        TGT_LUTV: begin tgt <= 2'd1; len <= 12'd1280; state <= S_UDATA; end
                        TGT_CORR: begin tgt <= 2'd2; len <= 12'd256;  state <= S_UDATA; end
                        default:  begin                  // unknown target -> 'E'
                            resp[0] <= ACK_E; resp_len <= 2'd1; resp_idx <= 2'd0;
                            state <= S_RESP;
                        end
                    endcase
                end
                S_UDATA: if (rx_valid) begin
                    case (tgt)
                        2'd0: lut [cnt[9:0]]  <= rx_data;
                        2'd1: lutv[cnt[10:0]] <= rx_data;
                        2'd2: corr[cnt[7:0]]  <= rx_data;
                    endcase
                    sum8 <= sum8 + rx_data;
                    if (cnt == len - 12'd1) state <= S_UCK;
                    else cnt <= cnt + 12'd1;
                end
                S_UCK: if (rx_valid) begin
                    if (u_sum == 8'h00) begin            // good -> commit (set loaded)
                        case (tgt)
                            2'd0: lut_ld  <= 1'b1;
                            2'd1: lutv_ld <= 1'b1;
                            2'd2: corr_ld <= 1'b1;
                        endcase
                        resp[0] <= ACK_K;
                    end else begin
                        resp[0] <= ACK_E;                // bad checksum
                    end
                    resp_len <= 2'd1; resp_idx <= 2'd0; state <= S_RESP;
                end

                // ---- read-table: A5 72 TGT CK ; reply TGT D[0..N-1] CK2 ---
                S_RTGT: if (rx_valid) begin
                    dbyte <= rx_data;                    // raw target (checksum + prologue)
                    case (rx_data)
                        TGT_LUT:  begin tgt <= 2'd0; len <= 12'd720;  end
                        TGT_LUTV: begin tgt <= 2'd1; len <= 12'd1280; end
                        TGT_CORR: begin tgt <= 2'd2; len <= 12'd256;  end
                        TGT_EDID: begin tgt <= 2'd3; len <= 12'd256;  end  // read-only
                        default:  begin tgt <= 2'd0; len <= 12'd0;    end  // unknown -> rejected
                    endcase
                    state <= S_RTCK;
                end
                S_RTCK: if (rx_valid) begin
                    if ((rt_sum != 8'h00) || (len == 12'd0)) begin   // bad CK or unknown target
                        resp[0] <= ACK_E; resp_len <= 2'd1; resp_idx <= 2'd0;
                        state <= S_RESP;
                    end else begin
                        sum8 <= dbyte;                   // checksum base = target
                        rd_idx <= 12'd0; rb_active <= 1'b1; rb_ph <= 2'd0;
                        state <= S_RTAB;
                    end
                end
                S_RTAB: if (!tx_busy && !tx_send) begin
                    case (rb_ph)
                        2'd0: begin                      // prologue: echo target
                            tx_data <= dbyte; tx_send <= 1'b1;
                            rd_idx <= 12'd0; rb_ph <= 2'd1;
                        end
                        2'd1: begin                      // stream data bytes from RAM
                            tx_data <= rb_dout; tx_send <= 1'b1;
                            sum8 <= sum8 + rb_dout;
                            if (rd_idx == len - 12'd1) rb_ph <= 2'd2;
                            else rd_idx <= rd_idx + 12'd1;
                        end
                        default: begin                   // epilogue: checksum byte, done
                            tx_data <= (8'h00 - sum8); tx_send <= 1'b1;
                            rb_active <= 1'b0; state <= S_SYNC;
                        end
                    endcase
                end

                // ---- response: push resp[0..len-1] to the shared uart_tx --
                S_RESP: begin
                    if (!tx_busy && !tx_send) begin
                        tx_data <= resp[resp_idx];
                        tx_send <= 1'b1;
                        if (resp_idx == resp_len - 2'd1) state <= S_SYNC;
                        else resp_idx <= resp_idx + 2'd1;
                    end
                end

                default: state <= S_SYNC;
            endcase
        end
    end
endmodule
