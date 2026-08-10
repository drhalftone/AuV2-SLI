`timescale 1ns/1ps
//=============================================================================
// cam_probe - diagnose a PYTHON 1300 that is not answering on SPI.
//
// cam_hello_core says FAIL with reg0=FFFF. That is the honest "nobody drove
// miso" reading, and it cannot tell these apart:
//
//     sensor unpowered | sensor stuck in reset | open SPI trace |
//     wrong FPGA ball  | bad solder joint
//
// This bitstream splits that space using two signals that DO NOT depend on the
// SPI bus working, so it keeps producing information even when SPI is dead.
//
//-----------------------------------------------------------------------------
// TEST 1 -- IS THE SENSOR POWERED AND DRIVING? (the monitor pull test)
//
// monitor0/1 are sensor OUTPUTS. In the hello bitstream they had no pull, so a
// floating input and a driven-high pin look identical. Here the XDC puts an
// internal PULLDOWN on both.
//
//     reads 0 -> the pin was floating; we learn nothing about the sensor
//     reads 1 -> something is actively driving it high against the pulldown,
//                and the only thing on that net is the sensor
//
// A 1 here means the sensor is POWERED AND ALIVE, proven without a single SPI
// transaction. That would move every remaining suspect onto the SPI path.
//
//-----------------------------------------------------------------------------
// TEST 2 -- DOES reset_n REACH THE SENSOR? (the reset toggle)
//
// Hold reset_n LOW, sample monitor. Hold it HIGH, sample monitor. If the two
// differ, then reset_n is connected AND the part on the other end responds to
// it -- again with no SPI involved. If they match, that is not proof of death
// (monitor may simply not track reset), but combined with Test 1 it narrows
// things a lot.
//
//-----------------------------------------------------------------------------
// TEST 3 -- MAKE THE SPI BUS VISIBLE TO A SCOPE
//
// cam_hello_core reads once every 500 ms: a ~30 us burst at 0.006 % duty. No
// DMM can see that and a scope needs a lucky trigger. Here the SPI phase fires
// transactions BACK-TO-BACK, so sck is a solid 1 MHz square wave for 400 ms out
// of every ~1.2 s -- trivial to catch, and a DMM on sck reads ~1.6 V instead of
// a hard rail. Probe sck/ss_n/mosi at the SENSOR end to find an open trace.
//
// It also records whether miso was EVER seen low and EVER seen high while ss_n
// was asserted, which distinguishes a stuck line from a line carrying data.
//=============================================================================
module cam_probe #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter integer PHASE_CY = CLK_HZ / 25    // 40 ms per phase
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output wire       cam_sck,
    output wire       cam_mosi,
    input  wire       cam_miso,
    output wire       cam_clk_pll,
    // inout, not output: Test 4 tri-states these and reads the board's own
    // pull resistors back through the same pads. See below.
    inout  wire       cam_ss_n,
    inout  wire       cam_reset_n,
    inout  wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    // Same rules as the hello bitstream: no sensor clock, no triggers, and the
    // SPI master is hard-wired to READ so no register can ever be written.
    assign cam_clk_pll = 1'b0;

    //-------------------------------------------------------------------------
    // TEST 4 -- IS THE CAMERA BOARD EVEN ON THE OTHER END OF THESE BALLS?
    //
    // Every sensor-driven pin now reads exactly what the FPGA's own pull says,
    // so nothing so far proves the board is mated, powered, or that the Pt pin
    // map is right -- and that pin map has never been bench-validated either.
    //
    // But the BOARD fits its own pulls on five of our outputs (README, "bank 14
    // / bank 35 split"): ss_n has a 10k PULL-UP (R3), reset_n a 10k PULL-DOWN
    // (R4), trigger0-2 10k PULL-DOWNs (R5/R6/R7). Tri-state all five with NO
    // internal pull and those resistors become a known reference:
    //
    //     pul=10  -> ss_n reads 1, reset_n and all three triggers read 0.
    //                The resistor network is present and powered, so the board
    //                IS mated, its 3.3 V rail is up, and these five balls really
    //                do land where pt_camera.xdc says. Pin map confirmed.
    //     pul=00  -> everything floats low: no board, or these balls go nowhere.
    //     anything else -> a specific pin disagrees; the hex says which.
    //
    // Tri-stating is inherently safe: Hi-Z with these pulls is exactly the
    // fail-safe state the board is designed to sit in before DONE.
    //-------------------------------------------------------------------------
    reg        oe;              // 1 = drive normally, 0 = release for sensing
    reg        ss_n_d, rst_n_d;
    wire       ss_n_q, rst_n_q;
    wire [2:0] trig_q;

    IOBUF u_ss  (.I(ss_n_d),  .T(~oe), .O(ss_n_q),  .IO(cam_ss_n));
    IOBUF u_rst (.I(rst_n_d), .T(~oe), .O(rst_n_q), .IO(cam_reset_n));
    genvar gi;
    generate
        for (gi = 0; gi < 3; gi = gi + 1) begin : g_trig
            IOBUF u_t (.I(1'b0), .T(~oe), .O(trig_q[gi]), .IO(cam_trigger[gi]));
        end
    endgenerate

    reg [4:0] pull_q = 5'd0;    // {ss_n, reset_n, trig[2:0]} as sensed

    reg         spi_start;
    wire [15:0] spi_rdata;
    wire        spi_busy, spi_done;

    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(1_000_000)) u_spi (
        .clk(clk), .rst(rst),
        .start(spi_start), .rw(1'b0), .addr(9'd0), .wdata(16'h0000),
        .rdata(spi_rdata), .busy(spi_busy), .done(spi_done),
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(spi_ss_n), .miso(cam_miso)
    );
    wire spi_ss_n;

    // miso liveness, sampled only while ss_n is asserted.
    reg miso_m, miso_s;
    reg saw_hi, saw_lo;
    always @(posedge clk) begin
        miso_m <= cam_miso;
        miso_s <= miso_m;
    end

    reg [1:0] mon_m, mon_s;
    always @(posedge clk) begin
        mon_m <= cam_monitor;
        mon_s <= mon_m;
    end

    localparam [2:0] P_RSTLO = 3'd0,   // reset_n low, sample monitor
                     P_RSTHI = 3'd1,   // reset_n high, sample monitor
                     P_SPI   = 3'd2,   // hammer SPI back-to-back
                     P_SENSE = 3'd3,   // tri-state, read the board's own pulls
                     P_RPT   = 3'd4;   // emit the line

    reg [2:0]  ph  = P_RSTLO;
    reg [26:0] tmr = 27'd0;

    reg [1:0]  mon_lo = 2'd0, mon_hi = 2'd0;
    reg [15:0] value  = 16'd0;
    reg        msg_go;

    wire phase_done = (tmr == PHASE_CY[26:0] - 27'd1);

    always @(posedge clk) begin
        spi_start <= 1'b0;
        msg_go    <= 1'b0;

        if (rst) begin
            ph      <= P_RSTLO;
            tmr     <= 27'd0;
            oe      <= 1'b1;
            ss_n_d  <= 1'b1;          // deasserted
            rst_n_d <= 1'b0;
            mon_lo  <= 2'd0;
            mon_hi  <= 2'd0;
            value   <= 16'd0;
            saw_hi  <= 1'b0;
            saw_lo  <= 1'b0;
            pull_q  <= 5'd0;
        end else begin
            // ss_n follows the SPI master whenever we are driving.
            ss_n_d <= spi_ss_n;

            // Sticky miso observation, only meaningful while selected AND while
            // we are actually driving the bus.
            if (oe && !spi_ss_n) begin
                if ( miso_s) saw_hi <= 1'b1;
                if (!miso_s) saw_lo <= 1'b1;
            end

            case (ph)

            P_RSTLO: begin
                oe      <= 1'b1;
                rst_n_d <= 1'b0;
                if (phase_done) begin
                    mon_lo <= mon_s;          // monitor with the sensor IN reset
                    tmr    <= 27'd0;
                    ph     <= P_RSTHI;
                end else tmr <= tmr + 27'd1;
            end

            P_RSTHI: begin
                oe      <= 1'b1;
                rst_n_d <= 1'b1;
                if (phase_done) begin
                    mon_hi <= mon_s;          // monitor with reset RELEASED
                    tmr    <= 27'd0;
                    ph     <= P_SPI;
                end else tmr <= tmr + 27'd1;
            end

            // Back-to-back transactions: re-arm the instant the master is free.
            // cam_spi_master's GAP state still enforces the datasheet's >= 2 sck
            // periods with ss_n high between uploads, so this stays legal.
            P_SPI: begin
                oe      <= 1'b1;
                rst_n_d <= 1'b1;
                if (!spi_busy) spi_start <= 1'b1;
                if (spi_done)  value     <= spi_rdata;
                if (phase_done) begin
                    tmr <= 27'd0;
                    ph  <= P_SENSE;
                end else tmr <= tmr + 27'd1;
            end

            // Release all five and let the BOARD's resistors decide. Sample at
            // the end of the phase so the nets have long settled -- 40 ms against
            // an RC of 10k x a few pF is nine orders of magnitude of margin.
            P_SENSE: begin
                oe <= 1'b0;
                if (phase_done) begin
                    pull_q <= {ss_n_q, rst_n_q, trig_q};
                    tmr    <= 27'd0;
                    ph     <= P_RPT;
                end else tmr <= tmr + 27'd1;
            end

            P_RPT: begin
                oe      <= 1'b1;
                rst_n_d <= 1'b1;
                msg_go  <= 1'b1;
                tmr     <= 27'd0;
                ph      <= P_RSTLO;
            end

            default: ph <= P_RSTLO;
            endcase
        end
    end

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7]   <= hb[25];                    // heartbeat
        led[6]   <= (pull_q == 5'b10000);      // board's pull network as expected
        led[5]   <= saw_lo;                    // miso seen LOW
        led[4]   <= saw_hi;                    // miso seen HIGH
        led[3]   <= (mon_lo != mon_hi);        // monitor tracks reset_n
        led[2:0] <= pull_q[4:2];               // ss_n, reset_n, trigger2 as sensed
    end

    //------------------------------------------------------------------- UART
    //   rlo=0 rhi=0 reg0=0000 miso=L- pul=10
    localparam integer MSG_LEN = 38;

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [5:0] idx = 6'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    always @(*) begin
        case (idx)
        6'd0 : ch = "r";  6'd1 : ch = "l";  6'd2 : ch = "o";  6'd3 : ch = "=";
        6'd4 : ch = hexd({2'b00, mon_lo});
        6'd5 : ch = " ";
        6'd6 : ch = "r";  6'd7 : ch = "h";  6'd8 : ch = "i";  6'd9 : ch = "=";
        6'd10: ch = hexd({2'b00, mon_hi});
        6'd11: ch = " ";
        6'd12: ch = "r";  6'd13: ch = "e";  6'd14: ch = "g";  6'd15: ch = "0";
        6'd16: ch = "=";
        6'd17: ch = hexd(value[15:12]);
        6'd18: ch = hexd(value[11:8]);
        6'd19: ch = hexd(value[7:4]);
        6'd20: ch = hexd(value[3:0]);
        6'd21: ch = " ";
        6'd22: ch = "m";  6'd23: ch = "i";  6'd24: ch = "s";  6'd25: ch = "o";
        6'd26: ch = "=";
        6'd27: ch = saw_lo ? "L" : "-";
        6'd28: ch = saw_hi ? "H" : "-";
        6'd29: ch = " ";
        6'd30: ch = "p";  6'd31: ch = "u";  6'd32: ch = "l";  6'd33: ch = "=";
        6'd34: ch = hexd({3'b000, pull_q[4]});
        6'd35: ch = hexd(pull_q[3:0]);
        6'd36: ch = 8'h0D;
        6'd37: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0;
            idx      <= 6'd0;
        end else if (!busy_msg) begin
            if (msg_go) begin
                busy_msg <= 1'b1;
                idx      <= 6'd0;
            end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;                    // latch the CURRENT idx's char
            if (idx == MSG_LEN[5:0] - 6'd1) begin
                busy_msg <= 1'b0;
                idx      <= 6'd0;
            end else idx <= idx + 6'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

endmodule
