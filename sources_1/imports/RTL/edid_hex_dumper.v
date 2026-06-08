`timescale 1ns/1ps
//==============================================================================
// edid_hex_dumper - on `go`, streams `len` bytes of the captured EDID over the
// UART as ASCII hex: a banner, 16 bytes per line, then a 1-char status line
// ('O' = block-0 checksum OK, 'X' = bad checksum, 'N' = bus NACK / no EDID).
// Reads the EDID through the i2c master's debug port (rd_addr/rd_data).
//==============================================================================
module edid_hex_dumper (
    input  wire        clk,
    input  wire        rst,
    input  wire        go,            // 1-cycle pulse to start a dump
    input  wire [8:0]  len,           // 128 or 256
    input  wire        chk0_ok,
    input  wire        nack_err,
    // EDID read-back port (to i2c_master_edid)
    output reg  [7:0]  rd_addr,
    input  wire [7:0]  rd_data,
    // UART tx handshake
    output reg  [7:0]  tx_data,
    output reg         tx_send,
    input  wire        tx_busy,
    output reg         busy
);
    // banner: "\r\nEDID:\r" then a trailing LF (9 chars total)
    localparam integer HLEN = 8;
    function [7:0] hdr(input [3:0] i);
        case (i)
            0: hdr = 8'h0D; 1: hdr = 8'h0A;
            2: hdr = "E"; 3: hdr = "D"; 4: hdr = "I"; 5: hdr = "D"; 6: hdr = ":";
            default: hdr = 8'h0D;          // i==7 -> CR; HDRLF then emits LF
        endcase
    endfunction

    function [7:0] hexc(input [3:0] n);
        hexc = (n < 4'd10) ? (8'h30 + n) : (8'h41 + n - 4'd10);  // 0-9 / A-F
    endfunction

    localparam S_IDLE  = 5'd0,  S_HDR    = 5'd1,  S_HDRLF = 5'd2,
               S_SET   = 5'd3,  S_RDWAIT = 5'd4,
               S_HI    = 5'd5,  S_LO     = 5'd6,  S_SEP   = 5'd7,
               S_POST  = 5'd8,  S_LF2    = 5'd9,  S_ADV   = 5'd10,
               S_SCR   = 5'd11, S_SLF    = 5'd12, S_SCH   = 5'd13, S_SEND = 5'd14,
               S_PUT   = 5'd15, S_PUT2   = 5'd16;

    reg [4:0] st, ret;     // ret = state to resume after a PUT
    reg [3:0] hc;          // header char index
    reg [8:0] idx;         // byte index
    reg [4:0] col;         // column 0..15
    reg [7:0] ch;          // char pending for PUT

    initial begin st = S_IDLE; busy = 1'b0; tx_send = 1'b0; rd_addr = 0; end

    always @(posedge clk) begin
        tx_send <= 1'b0;
        if (rst) begin
            st <= S_IDLE; busy <= 1'b0;
        end else case (st)
            S_IDLE: if (go) begin busy<=1'b1; hc<=0; idx<=0; col<=0; st<=S_HDR; end
                    else busy <= 1'b0;

            // ----- banner -----
            S_HDR:   if (hc < HLEN) begin ch<=hdr(hc); hc<=hc+1'b1; ret<=S_HDR; st<=S_PUT; end
                     else st <= S_HDRLF;
            S_HDRLF: begin ch<=8'h0A; ret<=S_SET; st<=S_PUT; end

            // ----- per-byte: "HH " -----
            S_SET:    begin rd_addr <= idx[7:0]; st <= S_RDWAIT; end
            S_RDWAIT: st <= S_HI;                       // rd_data valid next cycle
            S_HI:  begin ch<=hexc(rd_data[7:4]); ret<=S_LO;   st<=S_PUT; end
            S_LO:  begin ch<=hexc(rd_data[3:0]); ret<=S_SEP;  st<=S_PUT; end
            S_SEP: begin ch<=8'h20;              ret<=S_POST; st<=S_PUT; end

            // end-of-byte: emit CRLF after every 16th byte, then advance
            S_POST: if (col == 5'd15) begin ch<=8'h0D; ret<=S_LF2; st<=S_PUT; end
                    else st <= S_ADV;
            S_LF2:  begin ch<=8'h0A; ret<=S_ADV; st<=S_PUT; end
            S_ADV:  begin
                        col <= (col == 5'd15) ? 5'd0 : (col + 1'b1);
                        idx <= idx + 1'b1;
                        st  <= ((idx + 1) >= len) ? S_SCR : S_SET;
                    end

            // ----- status line: "\r\n" + O/X/N + "\n" -----
            S_SCR:  begin ch<=8'h0D; ret<=S_SLF;  st<=S_PUT; end
            S_SLF:  begin ch<=8'h0A; ret<=S_SCH;  st<=S_PUT; end
            S_SCH:  begin ch <= nack_err ? "N" : (chk0_ok ? "O" : "X");
                          ret<=S_SEND; st<=S_PUT; end
            S_SEND: begin ch<=8'h0A; ret<=S_IDLE; st<=S_PUT; busy<=1'b0; end

            // ----- putc: emit `ch`, then resume at `ret` -----
            S_PUT:  if (!tx_busy) begin tx_data<=ch; tx_send<=1'b1; st<=S_PUT2; end
            S_PUT2: st <= ret;
        endcase
    end
endmodule
