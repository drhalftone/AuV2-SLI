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
    parameter [15:0]  CHIP_ID     = 16'h50D0,
    // TRIGGERED GLOBAL SHUTTER, MASTER MODE (datasheet Table 28, register 192).
    //
    //   192[4] triggered_mode  0 = normal (free-running), 1 = triggered
    //   192[5] slave_mode      0 = master, 1 = slave        <-- left at 0
    //
    // In triggered master mode the pixel array is held in reset until a RISING
    // EDGE arrives on trigger0 (sensor pin 41); that edge starts integration and
    // then readout of exactly one frame. Exposure still comes from registers
    // 199-203, so every timing value we already validated stays as it is -- the
    // only thing that changes is WHO decides when a frame begins. A falling edge
    // does nothing, and an edge that arrives before the previous frame's
    // exposure + FOT has finished is ignored by the sensor (p14, p25).
    //
    // 192[6:1] is a STATIC readout parameter (Table 6): it may only be written
    // while 192[0] = 0. That is already how this ROM is shaped -- rom[31] writes
    // 192 with the mode bits and the sequencer OFF, and rom[41] sets bit 0 last
    // -- so enabling triggered mode is a bit in both words, not a new sequence.
    //
    // 0 keeps free-running behaviour, so Au2_SLI is unaffected.
    parameter integer TRIGGERED   = 0,
    // Register 201 = exposure0 (datasheet p59). Exposure time is
    // exposure0 x mult_timer / f_pll, where mult_timer is register 199 (= 27
    // here), so this is the one knob to turn for brightness. Avnet's value of
    // 10000 saturated 53 % of the frame on this bench; lower it until the
    // histogram stops clipping. Exposure is a DYNAMIC parameter (Table 8) --
    // safe to change without disabling the sequencer, one frame of latency.
    parameter [15:0]  EXPOSURE    = 16'h2710,
    // Stop after this many ROM entries, for STAGED bring-up. 0 = run the whole
    // sequence, which is the previous behaviour and the default, so existing
    // instantiations (Au2_SLI) are unaffected.
    //
    //    8 -> SEQ01 + the PLL lock poll, and nothing else. The sensor's PLL is
    //         asked to lock and we stop; no LVDS, no streaming.
    //   41 -> everything up to and including rom[40] (reg 112 = LVDS drivers on)
    //         but NOT the sequencer enable, so the sensor does not stream. This
    //         is the state the stage-2 DMM test and the IDELAY eye scan want.
    //   45 -> the same PLUS the test-pattern registers rom[41..44]. Use this
    //         with TESTPAT = 1; the sequencer still stays disabled.
    //    0 -> all 46, sequencer enabled, sensor streams.
    // BUILT-IN TEST PATTERN (datasheet p54-55, Data Block offset 128).
    //
    //   144[0] testpattern_en     insert synthesized test pattern
    //   144[1] inc_testpattern    1 = incrementing, 0 = constant
    //   144[3] frame_testpattern  1 = FRAMED (real sync codes still emitted)
    //   146    testpattern0/1 lsb   147  testpattern2/3 lsb   150  their msbs
    //
    // Each of the four datapaths emits its OWN constant, so every pixel in the
    // frame is a direct readout of which LVDS lane produced it. That converts
    // "does the image look right" -- unanswerable on a lens-less sensor staring
    // at a flat field -- into a byte-exact comparison.
    //
    // The DEFAULTS (0,1,2,3) are useless here: the pixel path keeps kpix[9:2],
    // and 0,1,2,3 >> 2 are all zero. These values survive the shift:
    //
    //   lane 0 = 0x040 -> 16     lane 2 = 0x0C0 -> 48
    //   lane 1 = 0x080 -> 32     lane 3 = 0x100 -> 64
    //
    // With our de-interleave the expected line is a 16-pixel repeat:
    //   even kernel  16 16 32 32 48 48 64 64
    //   odd kernel   64 64 48 48 32 32 16 16
    //
    // TESTPAT = 0 writes the datasheet defaults instead, which is a functional
    // no-op, so rom[41..44] are harmless for every existing instantiation.
    parameter integer TESTPAT     = 0,
    parameter integer STOP_AT     = 0
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
    localparam integer NROM = 46;
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
        rom[29] = {9'd201, EXPOSURE};        // exposure0 -- see EXPOSURE param
        rom[30] = {9'd200, 16'h411A};
        // mode bits, sequencer still OFF (192[0] = 0) -- see TRIGGERED above
        rom[31] = {9'd192, 16'h0800 | (TRIGGERED ? 16'h0010 : 16'h0000)};
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
        // ---- test pattern (defaults when TESTPAT = 0 -> no-op) ----
        rom[41] = {9'd144, TESTPAT ? 16'h0009 : 16'h0000};  // en + framed
        rom[42] = {9'd146, TESTPAT ? 16'h8040 : 16'h0100};  // lane1:lane0 lsb
        rom[43] = {9'd147, TESTPAT ? 16'h00C0 : 16'h0302};  // lane3:lane2 lsb
        rom[44] = {9'd150, TESTPAT ? 16'h0040 : 16'h0000};  // msbs: lane3 = 01
        // ---- enable the sequencer LAST ----
        rom[45] = {9'd192, 16'h0801 | (TRIGGERED ? 16'h0010 : 16'h0000)}; // rom[31] | bit0
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

    // Index of the last ROM entry this run will execute.
    localparam integer LAST_IDX = (STOP_AT == 0) ? (NROM - 1) : (STOP_AT - 1);

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
                        end else if (idx == LAST_IDX) begin
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
                        // The LAST_IDX test is repeated here, not just in S_WR_W: with
                        // STOP_AT = 8 the run ends ON the PLL poll, so without this the
                        // sequencer would fall through and start writing SEQ03.
                        if (spi_rdata[0]) begin
                            pll_done <= 1'b1;
                            if (idx == LAST_IDX) st <= S_DONE;
                            else begin idx <= idx + 6'd1; st <= S_WR; end
                        end else if (pll_cnt == PLL_TRIES[3:0] - 1) begin
                            // Avnet proceeds on timeout (its return is commented out). Flag it.
                            pll_timeout <= 1'b1; pll_done <= 1'b1;
                            if (idx == LAST_IDX) st <= S_DONE;
                            else begin idx <= idx + 6'd1; st <= S_WR; end
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
