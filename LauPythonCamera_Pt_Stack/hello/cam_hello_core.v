`timescale 1ns/1ps
//=============================================================================
// pt_cam_hello - "is the PYTHON 1300 actually there?" in one bitstream.
//
// This is CAMERA_RTL_PLAN.md milestone #5, the hardware gate, as a STANDALONE
// design: reset the sensor, read register 0 over SPI, and check it against the
// chip ID 0x50D0 (CAMERA_SENSOR_PROTOCOL.md §2).
//
// WHY STANDALONE AND NOT Au2_SLI. The full design reaches the same register
// through the UART mailbox, but it also brings up HDMI, an MMCM tree, the LVDS
// receiver and the boot sequencer. If the read fails in there, the failure has
// a hundred possible causes. Here it has almost none: a counter, an SPI shift
// register, six pins. That is the entire point of a bring-up bitstream.
//
// WHY THIS WORKS WITH THE SENSOR ESSENTIALLY UNPOWERED-UP. The PYTHON's SPI is
// asynchronous to its system clock (§1), so it answers with NO sensor clock
// running, no PLL, and no configuration. Register 0 is read-only status, so
// the transaction needs none of the NDA register upload either (§7).
//
// WHAT IT DELIBERATELY DOES NOT DO
//   - It never writes ANY register. Read-only, so it cannot put the sensor in
//     a bad state, and register 112 (LVDS power-up) stays at its 0 default.
//   - It never drives clk_pll. Held low: no clock is needed, and never starting
//     one means we can never violate "the sensor must be in reset BEFORE the
//     clock input stops" (§6).
//   - It touches no bank-13 pin. The LVDS pins are not in this design at all,
//     so the 2.5 V VBSEL strap is irrelevant to whether this bitstream is safe.
//
// READING THE LEDs (left..right = led[7]..led[0] on the Pt)
//   led[7]  HEARTBEAT  ~1 Hz. Bitstream loaded and clocking. Always blinks.
//   led[6]  PASS       solid = the last read returned 0x50D0.
//   led[5]  FAIL       solid = the last read returned something else.
//   led[4]  MISO LIVE  miso has been seen BOTH high and low. Dark = the line
//                      never moved: open circuit, no sensor power, or wrong pin.
//                      This is what separates "no connection" from "bad data".
//   led[3:0]           low nibble of the last value read (0x0 on a good part).
//
// The UART (115200 8N1 on the Pt's onboard FT2232) prints the whole story twice
// a second, which is what you actually want on the bench:
//
//   reg0=50D0 mon=0 PASS
//   reg0=0000 mon=0 FAIL      <- miso stuck low  (led[4] dark)
//   reg0=FFFF mon=3 FAIL      <- miso stuck high (led[4] dark)
//=============================================================================
module cam_hello_core #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter [15:0]  CHIP_ID = 16'h50D0,       // datasheet Table 28, reg 0
    // Bring-up delays, in clk cycles. Parameters only so the testbench can
    // shrink them; the defaults are what goes on the board.
    parameter integer RST_LOW_CY  = CLK_HZ / 100,   // 10 ms sensor reset low
    parameter integer RST_WAIT_CY = CLK_HZ / 100,   // 10 ms settle after release
    parameter integer POLL_CY     = CLK_HZ / 2,     // re-read twice a second
    // 0 = hold clk_pll low (the original assumption); 1 = free-run it at
    // CLK_HZ/2. See the clk_pll block below for why this is in question.
    parameter integer CLK_PLL_EN  = 0
)(
    input  wire       clk,          // 100 MHz
    input  wire       rst_n_in,     // async active-low reset; tie 1'b1 if the
                                    // board has no user reset pin (the Au V2)

    output reg  [7:0] led,
    output wire       usb_tx,

    // ---- PYTHON 1300 single-ended control, LVCMOS33 (CAMERA_IO_MAP.md §4) ----
    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output reg        cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    //------------------------------------------------------------------ reset
    // Power-on reset, then the external reset. rst_n_in may cross in from a pin,
    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n_in};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    assign cam_trigger = 3'b000;   // sequencer disabled; we never enable it

    //-------------------------------------------------------------- clk_pll
    // THE CONTRADICTION THIS PARAMETER EXISTS TO SETTLE.
    //
    // CAMERA_SENSOR_PROTOCOL.md §1 says the SPI is "asynchronous to the sensor's
    // system clock -- so SPI works with no sensor clock running at all", and the
    // whole Au bring-up path was built on that. But §4.0 of the same document
    // records the datasheet's ratspi table as fin/6. A maximum SPI rate that is
    // PROPORTIONAL TO fin means the SPI block is clocked by fin -- and at fin = 0
    // the ceiling is 0. Those two statements cannot both be true.
    //
    // Every published reference design free-runs this clock: Avnet's reads the
    // chip ID 20 us after releasing reset_n with no sensor configuration at all,
    // exactly as we do, but with vita_refclk always running. Holding it low is
    // the one thing we do that they do not.
    //
    // CLK_HZ/2 = 50 MHz, inside the 45-55 MHz refclk band Avnet's driver handles
    // explicitly, and a /2 divider is 50 % duty by construction against the
    // sensor's 45-50-55 % requirement.
    //
    // ORDERING IS SAFE. The divider free-runs from configuration while
    // cam_reset_n is still held low for RST_LOW_CY, so the clock is stable long
    // before reset is released, and we never stop it while the sensor is out of
    // reset -- the condition §6 warns causes "high peak currents".
    reg clk_half = 1'b0;
    always @(posedge clk) clk_half <= ~clk_half;
    assign cam_clk_pll = (CLK_PLL_EN != 0) ? clk_half : 1'b0;

    //-------------------------------------------------------------- SPI master
    reg         spi_start;
    wire [15:0] spi_rdata;
    wire        spi_busy, spi_done;

    cam_spi_master #(
        .CLK_HZ (CLK_HZ),
        .SCK_HZ (1_000_000)          // 1 MHz; the sensor's max is 10 MHz
    ) u_spi (
        .clk    (clk),
        .rst    (rst),
        .start  (spi_start),
        .rw     (1'b0),              // READ. This design never writes.
        .addr   (9'd0),              // register 0 = chip_id
        .wdata  (16'h0000),
        .rdata  (spi_rdata),
        .busy   (spi_busy),
        .done   (spi_done),
        .sck    (cam_sck),
        .mosi   (cam_mosi),
        .ss_n   (cam_ss_n),
        .miso   (cam_miso)
    );

    //------------------------------------------------------- miso liveness
    // Sticky "we saw a 0" and "we saw a 1", sampled only while ss_n is low --
    // outside the transaction miso is high-Z and whatever the board's pull does
    // is not evidence of anything.
    reg miso_m, miso_s;
    reg saw_hi = 1'b0, saw_lo = 1'b0;
    always @(posedge clk) begin
        miso_m <= cam_miso;
        miso_s <= miso_m;
        if (rst) begin
            saw_hi <= 1'b0;
            saw_lo <= 1'b0;
        end else if (!cam_ss_n) begin
            if ( miso_s) saw_hi <= 1'b1;
            if (!miso_s) saw_lo <= 1'b1;
        end
    end
    wire miso_live = saw_hi & saw_lo;

    //---------------------------------------------------------------- timings
    localparam [2:0] S_RSTLOW = 3'd0,
                     S_RSTWAIT= 3'd1,
                     S_START  = 3'd2,
                     S_WAIT   = 3'd3,
                     S_REPORT = 3'd4,
                     S_HOLD   = 3'd5;

    reg [2:0]  state = S_RSTLOW;
    reg [26:0] tmr   = 27'd0;

    reg [15:0] value    = 16'h0000;   // last value read from register 0
    reg        have_val = 1'b0;
    reg        pass     = 1'b0;

    reg        msg_go;                // 1-clk strobe: start a UART line

    always @(posedge clk) begin
        spi_start <= 1'b0;
        msg_go    <= 1'b0;

        if (rst) begin
            state       <= S_RSTLOW;
            tmr         <= 27'd0;
            cam_reset_n <= 1'b0;      // hold the sensor in reset
            value       <= 16'h0000;
            have_val    <= 1'b0;
            pass        <= 1'b0;
        end else begin
            case (state)

            // Hold reset_n low. On a cold start the rails are already up long
            // before DONE, so this is about a clean, known reset edge, not about
            // the power sequence (that is the board's supervisors' job).
            S_RSTLOW: begin
                cam_reset_n <= 1'b0;
                if (tmr == RST_LOW_CY[26:0] - 27'd1) begin
                    tmr   <= 27'd0;
                    state <= S_RSTWAIT;
                end else tmr <= tmr + 27'd1;
            end

            // Release reset and let the sensor settle before talking to it.
            S_RSTWAIT: begin
                cam_reset_n <= 1'b1;
                if (tmr == RST_WAIT_CY[26:0] - 27'd1) begin
                    tmr   <= 27'd0;
                    state <= S_START;
                end else tmr <= tmr + 27'd1;
            end

            S_START: begin
                if (!spi_busy) begin
                    spi_start <= 1'b1;
                    state     <= S_WAIT;
                end
            end

            S_WAIT: begin
                if (spi_done) begin
                    value    <= spi_rdata;
                    have_val <= 1'b1;
                    pass     <= (spi_rdata == CHIP_ID);
                    state    <= S_REPORT;
                end
            end

            S_REPORT: begin
                msg_go <= 1'b1;       // value/pass are already registered
                tmr    <= 27'd0;
                state  <= S_HOLD;
            end

            // Wait out the poll period, then read again. Re-reading forever means
            // a marginal connection shows up as a flickering PASS rather than a
            // one-shot result you have to trust.
            S_HOLD: begin
                if (tmr == POLL_CY[26:0] - 27'd1) begin
                    tmr   <= 27'd0;
                    state <= S_START;
                end else tmr <= tmr + 27'd1;
            end

            default: state <= S_RSTLOW;
            endcase
        end
    end

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7]   <= hb[25];                    // ~1.5 Hz heartbeat
        led[6]   <=  pass & have_val;
        led[5]   <= ~pass & have_val;
        led[4]   <= miso_live;
        led[3:0] <= value[3:0];
    end

    //------------------------------------------------------------------- UART
    // Fixed 22-character line, so the "formatter" is a mux, not a printf.
    //   0123456789...
    //   reg0=50D0 mon=0 PASS\n
    localparam integer MSG_LEN = 22;

    reg  [7:0] ch;                    // combinational: the char AT idx
    reg  [7:0] data_q = 8'h00;        // registered copy handed to uart_tx
    reg  [4:0] idx  = 5'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    always @(*) begin
        case (idx)
        5'd0 : ch = "r";
        5'd1 : ch = "e";
        5'd2 : ch = "g";
        5'd3 : ch = "0";
        5'd4 : ch = "=";
        5'd5 : ch = hexd(value[15:12]);
        5'd6 : ch = hexd(value[11:8]);
        5'd7 : ch = hexd(value[7:4]);
        5'd8 : ch = hexd(value[3:0]);
        5'd9 : ch = " ";
        5'd10: ch = "m";
        5'd11: ch = "o";
        5'd12: ch = "n";
        5'd13: ch = "=";
        5'd14: ch = hexd({2'b00, cam_monitor});
        5'd15: ch = " ";
        5'd16: ch = pass ? "P" : "F";
        5'd17: ch = "A";
        5'd18: ch = pass ? "S" : "I";
        5'd19: ch = pass ? "S" : "L";
        5'd20: ch = 8'h0D;
        5'd21: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0;
            idx      <= 5'd0;
        end else if (!busy_msg) begin
            // Drop the line if the previous one somehow has not finished. At
            // 500 ms per poll and ~1.9 ms per line that cannot happen, but a
            // dropped line beats a jammed formatter.
            if (msg_go) begin
                busy_msg <= 1'b1;
                idx      <= 5'd0;
            end
        end else if (!tx_busy && !send) begin
            // Latch the char for the CURRENT idx in the same edge that advances
            // idx -- uart_tx samples `data` the cycle after `send`, by which
            // time the combinational `ch` has already moved on.
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[4:0] - 5'd1) begin
                busy_msg <= 1'b0;
                idx      <= 5'd0;
            end else begin
                idx <= idx + 5'd1;
            end
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk  (clk),
        .rst  (rst),
        .data (data_q),
        .send (send),
        .tx   (usb_tx),
        .busy (tx_busy)
    );

endmodule
