`timescale 1ns/1ps
//=============================================================================
// cam_regdump - read a set of registers with KNOWN non-zero reset defaults and
// print every one, so we can tell an addressing fault from an honest zero.
//
// THE PROBLEM THIS EXISTS TO SOLVE. Register 0 reads a perfect 0x50D0 while
// registers 112, 116 and 192 all read 0x0000. That is consistent with two very
// different stories:
//
//   (a) addressing is fine, and those registers genuinely contain 0
//   (b) addressing is BROKEN, and register 0 is the one address that cannot
//       show it -- 0 is all-zero bits, so it survives a bit shift, an extra
//       sck edge, and a stuck-low mosi identically
//
// python1300_spi_model.v warns about exactly this: an address off-by-one
// "still works for address 0 and fails everywhere else, so it hides behind a
// passing chip-ID read."
//
// Reading registers whose reset defaults are non-zero separates them. If reg 8
// comes back 0x0099 and reg 126 comes back 0x03A6, addressing works and the
// zeros are real. If EVERY non-zero-default register reads 0x0000, addressing
// is broken and the chip-ID pass was a false positive all along.
//
// Defaults below are from CAMERA_SENSOR_PROTOCOL.md and python1300_spi_model.v:
//
//     reg   8  = 0x0099     soft_reset_pll
//     reg   9  = 0x0009     soft_reset_cgen
//     reg  10  = 0x0999     soft_reset_analog
//     reg  16  = 0x0004     PLL, bypass = 1 at reset
//     reg  32  = 0x0004     clock gen, select_pll = 1
//     reg 117  = 0x002A     frame sync marker
//     reg 118  = 0x0015     BL  code
//     reg 119  = 0x0035     IMG code
//     reg 125  = 0x0059     CRC code
//     reg 126  = 0x03A6     TR  code
//
// Registers 1, 20, 24, 112 and 192 are included too; those legitimately read 0
// or near 0 at reset, so they are context rather than evidence.
//
// READ-ONLY. rw is tied to a constant 0: this design cannot write a register,
// so it is safe to run at any point and cannot disturb sensor state.
//
// SCK_HZ is a parameter. If addressing turns out to be marginal rather than
// broken, rebuilding at 100 kHz is the next experiment -- signal integrity on a
// hand-soldered sck or mosi would show up as a rate dependence.
//
// UART, one line per register, whole sweep every ~2 s:
//     r008=0099
//     r126=03A6
//=============================================================================
module cam_regdump #(
    parameter integer CLK_HZ  = 100_000_000,
    parameter integer BAUD    =     115_200,
    parameter integer SCK_HZ  =   1_000_000,
    parameter integer RST_LOW_CY  = CLK_HZ / 100,
    parameter integer RST_WAIT_CY = CLK_HZ / 100,
    parameter integer GAP_CY      = CLK_HZ * 2,
    parameter integer CLK_PLL_EN  = 1        // harmless, and one less variable
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output reg        cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    wire _unused = usb_rx | (|cam_monitor);

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    assign cam_trigger = 3'b000;

    reg clk_half = 1'b0;
    always @(posedge clk) clk_half <= ~clk_half;
    assign cam_clk_pll = (CLK_PLL_EN != 0) ? clk_half : 1'b0;

    //---------------------------------------------------------- register list
    localparam integer N_REGS = 17;

    // {addr[8:0], hundreds, tens, ones} -- decimal digits carried alongside so
    // the printer stays a mux and the label matches the datasheet's numbering.
    function [20:0] regentry(input [4:0] i);
        case (i)
        5'd0 : regentry = {9'd0,   4'd0, 4'd0, 4'd0};
        5'd1 : regentry = {9'd1,   4'd0, 4'd0, 4'd1};
        5'd2 : regentry = {9'd8,   4'd0, 4'd0, 4'd8};
        5'd3 : regentry = {9'd9,   4'd0, 4'd0, 4'd9};
        5'd4 : regentry = {9'd10,  4'd0, 4'd1, 4'd0};
        5'd5 : regentry = {9'd16,  4'd0, 4'd1, 4'd6};
        5'd6 : regentry = {9'd20,  4'd0, 4'd2, 4'd0};
        5'd7 : regentry = {9'd24,  4'd0, 4'd2, 4'd4};
        5'd8 : regentry = {9'd32,  4'd0, 4'd3, 4'd2};
        5'd9 : regentry = {9'd112, 4'd1, 4'd1, 4'd2};
        5'd10: regentry = {9'd116, 4'd1, 4'd1, 4'd6};
        5'd11: regentry = {9'd117, 4'd1, 4'd1, 4'd7};
        5'd12: regentry = {9'd118, 4'd1, 4'd1, 4'd8};
        5'd13: regentry = {9'd119, 4'd1, 4'd1, 4'd9};
        5'd14: regentry = {9'd125, 4'd1, 4'd2, 4'd5};
        5'd15: regentry = {9'd126, 4'd1, 4'd2, 4'd6};
        5'd16: regentry = {9'd192, 4'd1, 4'd9, 4'd2};
        default: regentry = 21'd0;
        endcase
    endfunction

    reg  [4:0] ri = 5'd0;
    wire [20:0] entry = regentry(ri);
    wire [8:0]  cur_addr = entry[20:12];

    // Every target read is paired with a read of register 0 taken immediately
    // before it. Register 0 is the known-good reference: if it degrades in step
    // with the target, the sensor is dropping off the bus after N transactions.
    // If it stays 0x50D0 while the target goes FFFF, the fault really is
    // address-dependent. One line shows both, back to back.
    reg        rd_ref = 1'b1;
    wire [8:0] spi_a  = rd_ref ? 9'd0 : cur_addr;

    //-------------------------------------------------------------- SPI master
    reg         spi_start;
    wire [15:0] spi_rdata;
    wire        spi_busy, spi_done;

    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(SCK_HZ)) u_spi (
        .clk(clk), .rst(rst),
        .start(spi_start), .rw(1'b0),          // READ ONLY, structurally
        .addr(spi_a), .wdata(16'h0000),
        .rdata(spi_rdata), .busy(spi_busy), .done(spi_done),
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(cam_miso)
    );

    //------------------------------------------------------------------- FSM
    localparam [2:0] S_RSTLO = 3'd0, S_RSTWAIT = 3'd1, S_RD = 3'd2,
                     S_WAIT  = 3'd3, S_EMIT    = 3'd4, S_NEXT = 3'd5,
                     S_GAP   = 3'd6;

    reg [2:0]  st  = S_RSTLO;
    reg [27:0] tmr = 28'd0;
    reg [15:0] val = 16'd0;
    reg [15:0] ref0 = 16'd0;     // register 0, read immediately before val
    reg        msg_go;
    reg        any_nz = 1'b0;    // any register other than 0 read non-zero

    always @(posedge clk) begin
        spi_start <= 1'b0;
        msg_go    <= 1'b0;

        if (rst) begin
            st          <= S_RSTLO;
            tmr         <= 28'd0;
            cam_reset_n <= 1'b0;
            ri          <= 5'd0;
            val         <= 16'd0;
            ref0        <= 16'd0;
            rd_ref      <= 1'b1;
            any_nz      <= 1'b0;
        end else begin
            case (st)
            S_RSTLO: begin
                cam_reset_n <= 1'b0;
                if (tmr == RST_LOW_CY[27:0] - 28'd1) begin tmr <= 28'd0; st <= S_RSTWAIT; end
                else tmr <= tmr + 28'd1;
            end
            S_RSTWAIT: begin
                cam_reset_n <= 1'b1;
                if (tmr == RST_WAIT_CY[27:0] - 28'd1) begin tmr <= 28'd0; st <= S_RD; end
                else tmr <= tmr + 28'd1;
            end
            S_RD: if (!spi_busy) begin spi_start <= 1'b1; st <= S_WAIT; end
            S_WAIT: if (spi_done) begin
                if (rd_ref) begin
                    ref0   <= spi_rdata;     // the reference read
                    rd_ref <= 1'b0;
                    st     <= S_RD;          // now the target, back to back
                end else begin
                    val    <= spi_rdata;
                    rd_ref <= 1'b1;
                    if (ri != 5'd0 && spi_rdata != 16'd0) any_nz <= 1'b1;
                    st     <= S_EMIT;
                end
            end
            S_EMIT: begin msg_go <= 1'b1; st <= S_NEXT; end
            S_NEXT: if (!busy_msg && !msg_go) begin
                if (ri == N_REGS[4:0] - 5'd1) begin
                    ri  <= 5'd0;
                    tmr <= 28'd0;
                    st  <= S_GAP;
                end else begin
                    ri <= ri + 5'd1;
                    st <= S_RD;
                end
            end
            S_GAP: begin
                if (tmr == GAP_CY[27:0] - 28'd1) begin tmr <= 28'd0; st <= S_RD; end
                else tmr <= tmr + 28'd1;
            end
            default: st <= S_RSTLO;
            endcase
        end
    end

    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;
    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= any_nz;          // lit = at least one non-zero register read
        led[5:0] <= 6'd0;
    end

    //------------------------------------------------------------------- UART
    //   r000=50D0 r118=FFFF
    localparam integer MSG_LEN = 21;

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [4:0] idx = 5'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    always @(*) begin
        case (idx)
        // the reference: register 0, read immediately before the target
        5'd0 : ch = "r";
        5'd1 : ch = "0";  5'd2 : ch = "0";  5'd3 : ch = "0";
        5'd4 : ch = "=";
        5'd5 : ch = hexd(ref0[15:12]);
        5'd6 : ch = hexd(ref0[11:8]);
        5'd7 : ch = hexd(ref0[7:4]);
        5'd8 : ch = hexd(ref0[3:0]);
        5'd9 : ch = " ";
        // the target
        5'd10: ch = "r";
        5'd11: ch = 8'h30 + {4'd0, entry[11:8]};   // hundreds
        5'd12: ch = 8'h30 + {4'd0, entry[7:4]};    // tens
        5'd13: ch = 8'h30 + {4'd0, entry[3:0]};    // ones
        5'd14: ch = "=";
        5'd15: ch = hexd(val[15:12]);
        5'd16: ch = hexd(val[11:8]);
        5'd17: ch = hexd(val[7:4]);
        5'd18: ch = hexd(val[3:0]);
        5'd19: ch = 8'h0D;
        5'd20: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0;
            idx      <= 5'd0;
        end else if (!busy_msg) begin
            if (msg_go) begin busy_msg <= 1'b1; idx <= 5'd0; end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[4:0] - 5'd1) begin
                busy_msg <= 1'b0;
                idx      <= 5'd0;
            end else idx <= idx + 4'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

endmodule
