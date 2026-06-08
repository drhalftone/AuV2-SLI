`timescale 1ns/1ps
//==============================================================================
// i2c_master_edid - bit-banged I2C master that reads an HDMI/DVI sink's EDID
// over the DDC channel (EEPROM at device address 0xA0).
//
//   * Open-drain: the module only ever drives the bus LOW (scl_oe/sda_oe=1) or
//     releases it (=0). External DDC pull-ups provide the HIGH level. The actual
//     tri-state IOBUFs live in the top level.
//   * Honors clock-stretching: when the master releases SCL it waits until the
//     bus actually reads high before advancing.
//   * Reads block 0 (128 B). If the extension flag (byte 126) is non-zero it
//     also reads block 1 (offset 0x80, another 128 B), giving up to 256 B.
//   * Captured bytes land in an internal RAM, read back via rd_addr/rd_data.
//==============================================================================
module i2c_master_edid #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer SCL_HZ = 100_000
)(
    input  wire        clk,
    input  wire        rst,        // sync, active high
    input  wire        start,      // 1-cycle pulse: begin a full EDID read
    // open-drain bus (tri-state performed in top level)
    input  wire        scl_i,
    output reg         scl_oe,     // 1 = pull SCL low, 0 = release
    input  wire        sda_i,
    output reg         sda_oe,     // 1 = pull SDA low, 0 = release
    // status
    output reg         busy,
    output reg         done,       // 1-cycle pulse when a read finishes
    output reg         nack_err,   // latched: a device byte was NACKed
    output reg [8:0]   edid_len,   // 128 or 256
    output reg         chk0_ok,    // block-0 checksum valid (sum mod 256 == 0)
    // debug read-back port
    input  wire [7:0]  rd_addr,
    output reg  [7:0]  rd_data
);
    localparam integer QUARTER = CLK_HZ / (SCL_HZ * 4);

    // ---- captured EDID storage ----
    reg [7:0] mem [0:255];
    always @(posedge clk) rd_data <= mem[rd_addr];

    //--------------------------------------------------------------------------
    // Quarter-bit timebase. Free-runs while `active`. qstrobe marks the end of a
    // quarter; while SCL is released it is gated on the bus actually being high.
    //--------------------------------------------------------------------------
    reg         active;
    reg [15:0]  divcnt;
    reg         qstrobe;
    wire        stretch_ok = scl_oe ? 1'b1 : scl_i;  // releasing? wait for high

    always @(posedge clk) begin
        qstrobe <= 1'b0;
        if (!active) begin
            divcnt <= 0;
        end else if (divcnt == QUARTER-1) begin
            if (stretch_ok) begin divcnt <= 0; qstrobe <= 1'b1; end
        end else begin
            divcnt <= divcnt + 1'b1;
        end
    end

    //--------------------------------------------------------------------------
    // Bit engine. One of START / STOP / WR / RD per invocation (4 quarters).
    //--------------------------------------------------------------------------
    localparam OP_START = 2'd0, OP_STOP = 2'd1, OP_WR = 2'd2, OP_RD = 2'd3;

    reg [1:0] bop;
    reg       bin;          // bit to write (WR) or ack level (RD-ack)
    reg       bgo;          // 1-cycle launch
    reg       bbusy;
    reg       bdone;        // 1-cycle complete
    reg       bout;         // sampled bus bit (RD)
    reg [1:0] bq;           // quarter index 0..3

    always @(posedge clk) begin
        bdone <= 1'b0;
        if (rst) begin
            bbusy <= 1'b0; scl_oe <= 1'b0; sda_oe <= 1'b0; bq <= 0;
        end else if (!bbusy) begin
            if (bgo) begin bbusy <= 1'b1; bq <= 2'd0; end
        end else if (qstrobe) begin
            case (bop)
            //----- START: SDA 1->0 while SCL high, then SCL low -----
            OP_START: case (bq)
                2'd0: begin sda_oe <= 1'b0; scl_oe <= 1'b0; end // both released (high)
                2'd1: begin sda_oe <= 1'b1; end                 // SDA low (start)
                2'd2: begin scl_oe <= 1'b1; end                 // SCL low
                2'd3: begin bbusy  <= 1'b0; bdone <= 1'b1; end
                endcase
            //----- STOP: SDA 0->1 while SCL high -----
            OP_STOP: case (bq)
                2'd0: begin sda_oe <= 1'b1; scl_oe <= 1'b1; end // SDA low, SCL low
                2'd1: begin scl_oe <= 1'b0; end                 // SCL high
                2'd2: begin sda_oe <= 1'b0; end                 // SDA high (stop)
                2'd3: begin bbusy  <= 1'b0; bdone <= 1'b1; end
                endcase
            //----- WR: drive bit while SCL low, pulse SCL high -----
            OP_WR: case (bq)
                2'd0: begin scl_oe <= 1'b1; sda_oe <= ~bin; end // setup data (drive 0 if bin=0)
                2'd1: begin scl_oe <= 1'b0; end                 // SCL high
                2'd2: begin end                                 // SCL high (hold)
                2'd3: begin scl_oe <= 1'b1; bbusy <= 1'b0; bdone <= 1'b1; end
                endcase
            //----- RD: release SDA, pulse SCL high, sample mid-high -----
            OP_RD: case (bq)
                2'd0: begin scl_oe <= 1'b1; sda_oe <= 1'b0; end // release SDA to listen
                2'd1: begin scl_oe <= 1'b0; end                 // SCL high
                2'd2: begin bout   <= sda_i; end                // sample
                2'd3: begin scl_oe <= 1'b1; bbusy <= 1'b0; bdone <= 1'b1; end
                endcase
            endcase
            bq <= bq + 1'b1;
        end
    end

    //--------------------------------------------------------------------------
    // Byte / transaction engine.
    //--------------------------------------------------------------------------
    localparam S_IDLE   = 5'd0,
               S_ST1    = 5'd1,  S_DEVW  = 5'd2,  S_ACK1 = 5'd3,
               S_OFF    = 5'd4,  S_ACK2  = 5'd5,
               S_ST2    = 5'd6,  S_DEVR  = 5'd7,  S_ACK3 = 5'd8,
               S_DATA   = 5'd9,  S_DACK  = 5'd10,
               S_STOP   = 5'd11, S_BLK   = 5'd12, S_FIN  = 5'd13;

    reg [4:0] st;
    reg [7:0] shft;        // shift register for WR/RD
    reg [3:0] bidx;        // bit counter 0..8
    reg       waiting;     // a bit op is in flight
    reg [7:0] byte_cnt;    // bytes read this block 0..127
    reg       second_blk;  // currently reading block 1
    reg [8:0] sum;         // running checksum of block 0
    reg       ext_present; // latched: block 0 byte 126 (extension flag) != 0

    // launch a bit op (op,b) once; clears automatically
    task launch(input [1:0] op, input b);
        begin bop <= op; bin <= b; bgo <= 1'b1; waiting <= 1'b1; end
    endtask

    always @(posedge clk) begin
        bgo  <= 1'b0;
        done <= 1'b0;
        if (rst) begin
            st <= S_IDLE; active <= 1'b0; busy <= 1'b0; waiting <= 1'b0;
            nack_err <= 1'b0; chk0_ok <= 1'b0; edid_len <= 9'd0;
        end else case (st)
            //------------------------------------------------------------
            S_IDLE: if (start) begin
                        busy <= 1'b1; active <= 1'b1; nack_err <= 1'b0;
                        chk0_ok <= 1'b0; second_blk <= 1'b0; sum <= 9'd0;
                        ext_present <= 1'b0; st <= S_ST1;
                    end else begin
                        active <= 1'b0; busy <= 1'b0;
                    end
            //----- START -----
            S_ST1: if (!waiting) launch(OP_START, 1'b0);
                   else if (bdone) begin waiting<=1'b0; shft<=8'hA0; bidx<=0; st<=S_DEVW; end
            //----- write device addr 0xA0 (write) -----
            S_DEVW: if (!waiting) begin
                        if (bidx < 8) launch(OP_WR, shft[7]);
                        else st <= S_ACK1;
                    end else if (bdone) begin
                        waiting<=1'b0; shft<={shft[6:0],1'b0}; bidx<=bidx+1'b1;
                    end
            S_ACK1: if (!waiting) launch(OP_RD, 1'b1);          // read slave ACK
                    else if (bdone) begin
                        waiting<=1'b0;
                        if (bout) begin nack_err<=1'b1; st<=S_STOP; end // NACK
                        else begin
                            // word offset: 0x00 for block0, 0x80 for block1
                            shft <= second_blk ? 8'h80 : 8'h00; bidx<=0; st<=S_OFF;
                        end
                    end
            //----- write word offset -----
            S_OFF: if (!waiting) begin
                        if (bidx < 8) launch(OP_WR, shft[7]);
                        else st <= S_ACK2;
                   end else if (bdone) begin
                        waiting<=1'b0; shft<={shft[6:0],1'b0}; bidx<=bidx+1'b1;
                   end
            S_ACK2: if (!waiting) launch(OP_RD, 1'b1);
                    else if (bdone) begin
                        waiting<=1'b0;
                        if (bout) begin nack_err<=1'b1; st<=S_STOP; end
                        else st<=S_ST2;
                    end
            //----- repeated START -----
            S_ST2: if (!waiting) launch(OP_START, 1'b0);
                   else if (bdone) begin waiting<=1'b0; shft<=8'hA1; bidx<=0; st<=S_DEVR; end
            //----- write device addr 0xA1 (read) -----
            S_DEVR: if (!waiting) begin
                        if (bidx < 8) launch(OP_WR, shft[7]);
                        else st <= S_ACK3;
                    end else if (bdone) begin
                        waiting<=1'b0; shft<={shft[6:0],1'b0}; bidx<=bidx+1'b1;
                    end
            S_ACK3: if (!waiting) launch(OP_RD, 1'b1);
                    else if (bdone) begin
                        waiting<=1'b0;
                        if (bout) begin nack_err<=1'b1; st<=S_STOP; end
                        else begin byte_cnt<=0; bidx<=0; st<=S_DATA; end
                    end
            //----- read a data byte (MSB first) -----
            S_DATA: if (!waiting) begin
                        if (bidx < 8) launch(OP_RD, 1'b1);
                        else st <= S_DACK;
                    end else if (bdone) begin
                        waiting<=1'b0; shft<={shft[6:0],bout}; bidx<=bidx+1'b1;
                    end
            //----- master ACK (more) / NACK (last byte of block) -----
            S_DACK: if (!waiting) launch(OP_WR, (byte_cnt==8'd127) ? 1'b1 : 1'b0);
                    else if (bdone) begin
                        waiting<=1'b0;
                        // store byte (block0 -> 0..127, block1 -> 128..255)
                        mem[{second_blk, byte_cnt[6:0]}] <= shft;
                        if (!second_blk) begin
                            sum <= sum + shft;                  // block-0 checksum
                            if (byte_cnt == 8'd126) ext_present <= (shft != 8'd0);
                        end
                        if (byte_cnt == 8'd127) st <= S_STOP;
                        else begin byte_cnt <= byte_cnt + 1'b1; bidx<=0; st<=S_DATA; end
                    end
            //----- STOP -----
            S_STOP: if (!waiting) launch(OP_STOP, 1'b0);
                    else if (bdone) begin waiting<=1'b0; st<=S_BLK; end
            //----- decide whether to read block 1 -----
            S_BLK: begin
                        if (nack_err) begin
                            edid_len <= 9'd0; st <= S_FIN;
                        end else if (!second_blk) begin
                            chk0_ok  <= (sum[7:0]==8'd0);
                            edid_len <= 9'd128;
                            if (ext_present) begin        // extension block present
                                second_blk <= 1'b1; st <= S_ST1;
                            end else st <= S_FIN;
                        end else begin
                            edid_len <= 9'd256; st <= S_FIN;
                        end
                    end
            //----- finished -----
            S_FIN: begin
                        active <= 1'b0; busy <= 1'b0; done <= 1'b1; st <= S_IDLE;
                    end
        endcase
    end
endmodule
