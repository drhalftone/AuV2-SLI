`timescale 1ns / 1ps
//=============================================================================
// cam_boot_seq.v - PYTHON 1300 power-up sequencer (ROM-driven).
//
// Walks onsemi's documented startup flow through cam_spi_master, so the sensor actually
// STREAMS. The register values are Avnet's published PYTHON-1300 sequence
// (docs/reference/onsemi_python_sw.c, SENSOR_INIT_SEQ00..06), cross-checked against the
// datasheet -- see CAMERA_SENSOR_PROTOCOL.md §7 and task #6/#13.
//
// FLOW (Avnet SEQ00..SEQ06, PLL mode):
//   1. Pulse reset_n low then release (reset generator, block offset 8).
//   2. Read chip ID (reg 0), require 0x50D0.
//   3. SEQ01 -- clock management part 1 (8 writes), enables the PLL.
//   4. SEQ02 -- poll the PLL lock bit (reg 24[0]) until set (bounded).
//   5. SEQ03 -- clock management part 2 (3 writes).
//   6. SEQ04 -- required register upload (21 writes; the "reserved" values onsemi withheld).
//   7. SEQ05 -- soft power-up (9 writes). *** reg 112 = 0x0007 powers up the LVDS drivers. ***
//   8. Enable sequencer: reg 192 = 0x0801 (0x0800 from SEQ04, plus bit 0).
//
// >>> PT ONLY. Step 7 writes reg 112 = 0x0007, powering the LVDS drivers. On the Au that
// >>> would drive dout0 onto the 1.35 V bank-15 pins (not 3.3 V tolerant). DO NOT
// >>> instantiate or trigger this on an Au build. See CAMERA_IO_MAP.md §8.2.
//
// TWO deviations from Avnet's PYTHON-1300-C sequence, both traceable:
//   - MONOCHROME: their SEQ01 writes reg 2 = 0x0001 (Color). Our NOIP1SN1300A-SN is mono,
//     so reg 2 = 0x0000 (datasheet reg 2[0]: 0 = Monochrome). ROM entry 0 below.
//   - CLOCKING: none. We adopted the PLL mode their sequence already uses.
//
// The FPGA-side ISERDES/decoder reset that Avnet's SEQ06 also does is handled by our
// cam_lvds_rx (CLKDIV-synchronous reset) and cam_align (bitslip) instead -- not here.
//
// Wait durations are parameters so simulation runs fast; the defaults are the real values
// at 100 MHz (10 us reset-low, 20 us reset-high, ~100 ms between PLL polls).
//=============================================================================
module cam_boot_seq #(
    parameter integer CLK_HZ      = 100_000_000,
    parameter integer T_RST_LOW   = 1_000,      // 10 us  @100 MHz
    parameter integer T_RST_HIGH  = 2_000,      // 20 us
    parameter integer T_PLL_POLL  = 10_000_000, // 100 ms between PLL-lock reads
    parameter integer PLL_TRIES   = 10,
    parameter [15:0]  CHIP_ID     = 16'h50D0
)(
    input  wire        clk,
    input  wire        rst,
    input  wire        go,           // pulse to start the sequence
    output reg         busy,
    output reg         ready,        // sensor booted and streaming
    output reg         failed,       // chip-ID mismatch
    output reg         pll_timeout,  // PLL never locked (Avnet proceeds anyway; flagged)
    output reg         reset_n,      // sensor reset_n (driven while booting)

    // cam_spi_master interface (this block owns it while busy)
    output reg         spi_start,
    output reg         spi_rw,       // 1 = write, 0 = read
    output reg  [8:0]  spi_addr,
    output reg  [15:0] spi_wdata,
    input  wire [15:0] spi_rdata,
    input  wire        spi_busy,
    input  wire        spi_done
);
    localparam integer NROM = 42;
    localparam integer PLL_AT = 8;    // poll PLL after the first 8 writes (end of SEQ01)

    // ROM: {addr[8:0], data[15:0]} = SEQ01(8) + SEQ03(3) + SEQ04(21) + SEQ05(9) + enable(1)
    reg [24:0] rom [0:NROM-1];
    initial begin
        // ---- SEQ01: clock management part 1 (PLL mode) ----
        rom[0]  = {9'd2,   16'h0000};   // MONO (Avnet: 0x0001 Color)
        rom[1]  = {9'd32,  16'h3004};
        rom[2]  = {9'd20,  16'h0000};
        rom[3]  = {9'd17,  16'h2113};
        rom[4]  = {9'd26,  16'h2280};
        rom[5]  = {9'd27,  16'h3D2D};
        rom[6]  = {9'd8,   16'h0000};
        rom[7]  = {9'd16,  16'h0003};   // enable PLL
        // ---- (PLL lock poll happens here) ----
        // ---- SEQ03: clock management part 2 ----
        rom[8]  = {9'd9,   16'h0000};
        rom[9]  = {9'd32,  16'h3006};
        rom[10] = {9'd34,  16'h0001};
        // ---- SEQ04: required register upload ----
        rom[11] = {9'd197, 16'h0205};
        rom[12] = {9'd224, 16'h3E5E};
        rom[13] = {9'd207, 16'h0000};
        rom[14] = {9'd129, 16'h8001};
        rom[15] = {9'd128, 16'h4714};
        rom[16] = {9'd204, 16'h01E3};
        rom[17] = {9'd41,  16'h085A};
        rom[18] = {9'd42,  16'h0011};
        rom[19] = {9'd65,  16'h288B};
        rom[20] = {9'd211, 16'h0E49};
        rom[21] = {9'd43,  16'h0008};
        rom[22] = {9'd70,  16'h1111};
        rom[23] = {9'd67,  16'h0554};
        rom[24] = {9'd66,  16'h53C6};
        rom[25] = {9'd68,  16'h0085};
        rom[26] = {9'd215, 16'h0107};
        rom[27] = {9'd194, 16'h0221};
        rom[28] = {9'd199, 16'h001B};
        rom[29] = {9'd201, 16'h2710};
        rom[30] = {9'd200, 16'h411A};
        rom[31] = {9'd192, 16'h0800};
        // ---- SEQ05: soft power-up ----
        rom[32] = {9'd32,  16'h3007};
        rom[33] = {9'd10,  16'h0000};
        rom[34] = {9'd64,  16'h0001};
        rom[35] = {9'd72,  16'h2227};
        rom[36] = {9'd42,  16'h0013};
        rom[37] = {9'd40,  16'h0003};
        rom[38] = {9'd48,  16'h0001};
        rom[39] = {9'd112, 16'h0007};   // *** LVDS drivers ON -- PT ONLY ***
        rom[40] = {9'd128, 16'h4714};
        // ---- enable sequencer ----
        rom[41] = {9'd192, 16'h0801};   // 0x0800 (from rom[31]) | bit0
    end

    localparam [3:0]
        S_IDLE    = 4'd0,  S_RST_LOW = 4'd1,  S_RST_HI  = 4'd2,
        S_CID_RD  = 4'd3,  S_CID_W   = 4'd4,  S_CID_CHK = 4'd5,
        S_WR      = 4'd6,  S_WR_W    = 4'd7,
        S_PLL_RD  = 4'd8,  S_PLL_W   = 4'd9,  S_PLL_CHK = 4'd10, S_PLL_WAIT = 4'd11,
        S_DONE    = 4'd12, S_FAIL    = 4'd13;

    reg [3:0]  st;
    reg [5:0]  idx;
    reg [3:0]  pll_cnt;
    reg [23:0] wait_cnt;
    reg        pll_done;      // set once the PLL poll (after idx 7) has been satisfied/skipped

    wire [8:0]  rom_addr = rom[idx][24:16];
    wire [15:0] rom_data = rom[idx][15:0];

    always @(posedge clk) begin
        if (rst) begin
            st <= S_IDLE; busy <= 1'b0; ready <= 1'b0; failed <= 1'b0; pll_timeout <= 1'b0;
            reset_n <= 1'b0;                 // hold the sensor in reset until we boot it
            spi_start <= 1'b0; spi_rw <= 1'b0; spi_addr <= 9'd0; spi_wdata <= 16'd0;
            idx <= 6'd0; pll_cnt <= 4'd0; wait_cnt <= 24'd0; pll_done <= 1'b0;
        end else begin
            spi_start <= 1'b0;               // default: no strobe

            case (st)
                S_IDLE: begin
                    // ready/failed/pll_timeout are STICKY: they hold the last boot's outcome
                    // until the next GO. `go` is a 1-clock strobe, so S_DONE/S_FAIL fall back
                    // here immediately -- clearing them here unconditionally (as before) made
                    // the result observable for only ~1 clk, and a host polling reg 0x39 over
                    // the slow UART would always read 0x00 (success looked like "never booted").
                    // Clear only when a new boot actually starts.
                    if (go) begin
                        ready <= 1'b0; failed <= 1'b0; pll_timeout <= 1'b0;
                        busy <= 1'b1; reset_n <= 1'b0; wait_cnt <= T_RST_LOW[23:0];
                        idx <= 6'd0; pll_cnt <= 4'd0; pll_done <= 1'b0;
                        st <= S_RST_LOW;
                    end
                end

                // ---- reset pulse ----
                S_RST_LOW: if (wait_cnt == 0) begin
                        reset_n <= 1'b1; wait_cnt <= T_RST_HIGH[23:0]; st <= S_RST_HI;
                    end else wait_cnt <= wait_cnt - 24'd1;
                S_RST_HI:  if (wait_cnt == 0) st <= S_CID_RD;
                           else wait_cnt <= wait_cnt - 24'd1;

                // ---- chip-ID read + check ----
                S_CID_RD: if (!spi_busy) begin
                        spi_rw <= 1'b0; spi_addr <= 9'd0; spi_start <= 1'b1; st <= S_CID_W;
                    end
                S_CID_W: if (spi_done) st <= S_CID_CHK;
                S_CID_CHK: if (spi_rdata == CHIP_ID) begin idx <= 6'd0; st <= S_WR; end
                           else begin failed <= 1'b1; st <= S_FAIL; end

                // ---- walk the ROM ----
                S_WR: if (!spi_busy) begin
                        spi_rw <= 1'b1; spi_addr <= rom_addr; spi_wdata <= rom_data;
                        spi_start <= 1'b1; st <= S_WR_W;
                    end
                S_WR_W: if (spi_done) begin
                        // after the last SEQ01 write (idx 7), poll the PLL before continuing
                        if ((idx == PLL_AT - 1) && !pll_done) begin
                            pll_cnt <= 4'd0; st <= S_PLL_RD;
                        end else if (idx == NROM - 1) begin
                            st <= S_DONE;
                        end else begin
                            idx <= idx + 6'd1; st <= S_WR;
                        end
                    end

                // ---- PLL lock poll (reg 24[0]) ----
                S_PLL_RD: if (!spi_busy) begin
                        spi_rw <= 1'b0; spi_addr <= 9'd24; spi_start <= 1'b1; st <= S_PLL_W;
                    end
                S_PLL_W: if (spi_done) st <= S_PLL_CHK;
                S_PLL_CHK: begin
                        if (spi_rdata[0]) begin
                            pll_done <= 1'b1; idx <= idx + 6'd1; st <= S_WR;
                        end else if (pll_cnt == PLL_TRIES[3:0] - 1) begin
                            // Avnet proceeds on timeout (its return is commented out). Flag it.
                            pll_timeout <= 1'b1; pll_done <= 1'b1;
                            idx <= idx + 6'd1; st <= S_WR;
                        end else begin
                            pll_cnt <= pll_cnt + 4'd1; wait_cnt <= T_PLL_POLL[23:0];
                            st <= S_PLL_WAIT;
                        end
                    end
                S_PLL_WAIT: if (wait_cnt == 0) st <= S_PLL_RD;
                            else wait_cnt <= wait_cnt - 24'd1;

                S_DONE: begin busy <= 1'b0; ready <= 1'b1;
                        if (!go) st <= S_IDLE; end   // stay ready until re-triggered
                S_FAIL: begin busy <= 1'b0;
                        if (!go) st <= S_IDLE; end

                default: st <= S_IDLE;
            endcase
        end
    end
endmodule
