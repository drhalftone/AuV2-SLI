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
//
// Registers (read any address -> data, undefined reads return 0x00):
//   0x00 ID      = 0x48 'H'      (RO)        0x02 STATUS = live `led` byte (RO)
//   0x01 VERSION = 0x01          (RO)        0x06 FLAGS  = {.., usb_sw_en, lut_loaded} (RO)
//   0x13 SLICTRL = {7:sw_en,3:R,2:G,1:B,0:orient}  (R/W -- the one writable register)
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

    // ---- live status byte surfaced through register 0x02 ----
    input  wire [7:0]  led,

    // ---- quasi-static control outputs (Stage 2: drive pixel_pipe) ----
    output reg  [7:0]  sli_ctrl,         // register 0x13
    output wire        sli_ctrl_en,      // = sli_ctrl[7]  (USB overrides switches)
    output wire        lut_loaded,       // a table has been uploaded since reset

    // ---- table read ports (Stage 2: read by the pattern datapath) ----
    // Defaulted in the VHDL component decl so the top can leave them open for now.
    input  wire [7:0]  corr_addr,  output reg [7:0] corr_dout,   // 256-entry correction
    input  wire [9:0]  lut_addr,   output reg [7:0] lut_dout,    // 720-entry row cosine
    input  wire [10:0] lutv_addr,  output reg [7:0] lutv_dout    // 1280-entry col cosine
);
    // ---- protocol constants ----
    localparam [7:0] SYNC = 8'hA5;
    localparam [7:0] OP_W = 8'h57, OP_R = 8'h52, OP_L = 8'h5B;   // 'W' 'R' table
    localparam [7:0] ACK_K = 8'h4B, ACK_E = 8'h45, ACK_N = 8'h4E;
    localparam [7:0] TGT_LUT = 8'h00, TGT_LUTV = 8'h01, TGT_CORR = 8'h02;

    // ---- table RAMs (write-through during upload; read ports for Stage 2) ----
    reg [7:0] corr [0:255];
    reg [7:0] lut  [0:719];
    reg [7:0] lutv [0:1279];

    // ---- loaded flags ----
    reg corr_ld = 1'b0, lut_ld = 1'b0, lutv_ld = 1'b0;
    assign lut_loaded  = corr_ld | lut_ld | lutv_ld;
    assign sli_ctrl_en = sli_ctrl[7];

    // ---- register read mux ----
    function [7:0] regread;
        input [7:0] a;
        case (a)
            8'h00:   regread = ID_MAGIC;
            8'h01:   regread = VERSION;
            8'h02:   regread = led;
            8'h06:   regread = {6'b0, sli_ctrl[7], (corr_ld | lut_ld | lutv_ld)};
            8'h13:   regread = sli_ctrl;
            default: regread = 8'h00;
        endcase
    endfunction

    // ---- FSM ----
    localparam [3:0]
        S_SYNC  = 4'd0,  S_OP    = 4'd1,
        S_WADDR = 4'd2,  S_WDATA = 4'd3,  S_WCK = 4'd4,
        S_RADDR = 4'd5,  S_RCK   = 4'd6,
        S_UTGT  = 4'd7,  S_UDATA = 4'd8,  S_UCK = 4'd9,
        S_RESP  = 4'd10;

    reg [3:0]  state = S_SYNC;
    reg [7:0]  addr, dbyte, sum8;
    reg [11:0] cnt, len;             // up to 1280
    reg [1:0]  tgt;                  // 0=lut 1=lutv 2=corr

    // response buffer (1 or 3 bytes)
    reg [7:0]  resp [0:2];
    reg [1:0]  resp_len, resp_idx;

    assign tx_active = (state == S_RESP);

    // synchronous table read ports (kept always -> RAMs are not optimised away)
    always @(posedge clk) begin
        corr_dout <= corr[corr_addr];
        lut_dout  <= lut[lut_addr];
        lutv_dout <= lutv[lutv_addr];
    end

    // checksum helpers (8-bit wraparound)
    wire [7:0] w_sum = OP_W + addr + dbyte + rx_data;   // == 0 -> good write CK
    wire [7:0] r_sum = OP_R + addr + rx_data;           // == 0 -> good read CK
    wire [7:0] u_sum = sum8 + rx_data;                  // == 0 -> good upload CK
    wire [7:0] rd_data = regread(addr);
    wire [7:0] rd_ck   = (8'h00 - addr - rd_data);      // -(addr+data) mod 256

    integer i;
    always @(posedge clk) begin
        if (rst) begin
            state <= S_SYNC; tx_send <= 1'b0; sli_ctrl <= 8'h00;
            corr_ld <= 1'b0; lut_ld <= 1'b0; lutv_ld <= 1'b0;
            resp_len <= 2'd0; resp_idx <= 2'd0;
        end else begin
            tx_send <= 1'b0;                            // default: no TX strobe

            case (state)
                // ---- frame sync + opcode dispatch -------------------------
                S_SYNC: if (rx_valid && rx_data == SYNC) state <= S_OP;

                S_OP: if (rx_valid) begin
                    case (rx_data)
                        OP_W: state <= S_WADDR;
                        OP_R: state <= S_RADDR;
                        OP_L: state <= S_UTGT;
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
