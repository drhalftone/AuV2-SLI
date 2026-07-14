`timescale 1ns/1ps
//==============================================================================
// cam_spi_master - SPI master for the onsemi PYTHON 1300 (NOIP1SN1300A).
//
// PROTOCOL (datasheet p.23, Figure 22 + Table 11; see CAMERA_SENSOR_PROTOCOL.md §1)
//
//   ss_n  ‾‾\______________________________________________________/‾‾‾
//   sck     |<- 1 sck ->| A8 A7 .. A0 | R/W | D15 D14 .. D0 |<- 1 sck ->|
//
//   9-bit address (MSB first), then ONE R/W bit (1 = write, 0 = read),
//   then 16 data bits (MSB first).  26 sck cycles per transaction.
//
// THE EDGES ARE ASYMMETRIC. This is the part that gets written wrong.
//   - The SENSOR samples mosi on the RISING edge of sck.
//     => we must drive mosi on the FALLING edge.            (datasheet L1429-1432)
//   - WE must sample miso on the FALLING edge of sck.       (datasheet L1441-1444)
//     That is NOT what a textbook mode-0 master does for miso (it would sample on
//     the rising edge). Sampling on the wrong edge shifts read data by one bit and
//     looks like a wiring fault.
//
// We sample miso in the cycle we *command* sck low -- sck is still high at that
// instant, so this is the mid-bit point the datasheet's ts_miso (= tsck/2 - 10 ns)
// is specified around.
//
// TIMING (Table 11): tsck >= 100 ns (10 MHz max). tsssck and tsckss are each >= tsck,
// and consecutive transactions need >= 2 sck periods with ss_n HIGH between them.
// All three are enforced below by the LEAD / TRAIL / GAP states.
//
// SPEED. The sensor's max SPI rate scales with its input clock (datasheet L1449-1451),
// but SPI is asynchronous to the sensor's system clock -- it works with NO sensor clock
// running at all. That is what lets us read the chip ID before the sensor is configured.
// Default SCK_HZ is therefore deliberately slow: it removes the question entirely and
// costs nothing (a transaction is ~30 us).
//==============================================================================
module cam_spi_master #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer SCK_HZ =   1_000_000   // must stay <= 10_000_000
)(
    input  wire        clk,
    input  wire        rst,

    // ---- control (clk domain) ----
    input  wire        start,        // 1-clk strobe; ignored while busy
    input  wire        rw,           // 1 = write, 0 = read
    input  wire [8:0]  addr,         // 9-bit register address
    input  wire [15:0] wdata,
    output reg  [15:0] rdata,
    output reg         busy,
    output reg         done,         // 1-clk strobe: write complete / rdata valid

    // ---- SPI pins to the sensor (LVCMOS33) ----
    output reg         sck,
    output reg         mosi,
    output reg         ss_n,
    input  wire        miso
);
    // clocks per sck HALF period. sck period = 2*HALF.
    localparam integer HALF  = CLK_HZ / (2 * SCK_HZ);
    localparam integer NBITS = 26;               // 9 addr + 1 rw + 16 data

    localparam [2:0] S_IDLE  = 3'd0,
                     S_LEAD  = 3'd1,             // ss_n low, wait tsssck (1 sck)
                     S_HIGH  = 3'd2,             // sck high  (sensor samples mosi)
                     S_LOW   = 3'd3,             // sck low   (we shift; we sample miso)
                     S_TRAIL = 3'd4,             // hold ss_n low tsckss (1 sck)
                     S_GAP   = 3'd5;             // ss_n high >= 2 sck periods

    reg [2:0]  state;
    reg [15:0] div;                              // half-period counter
    reg [4:0]  bit_idx;                          // 0..25
    reg [25:0] txsr;                             // {addr[8:0], rw, wdata[15:0]}
    reg [15:0] rxsr;

    wire tick = (div == HALF[15:0] - 16'd1);

    // miso crosses from the sensor; 2FF sync. The 20 ns of latency is nothing next
    // to a half-period (500 ns at the default 1 MHz), so it cannot move the sample point.
    reg miso_m, miso_s;
    always @(posedge clk) begin
        miso_m <= miso;
        miso_s <= miso_m;
    end

    always @(posedge clk) begin
        if (rst) begin
            state   <= S_IDLE;
            sck     <= 1'b0;                     // CPOL = 0: sck idles LOW
            ss_n    <= 1'b1;
            mosi    <= 1'b0;
            busy    <= 1'b0;
            done    <= 1'b0;
            div     <= 16'd0;
            bit_idx <= 5'd0;
            rdata   <= 16'd0;
        end else begin
            done <= 1'b0;                        // default: 1-clk strobe

            case (state)

            //---------------------------------------------------------------- idle
            S_IDLE: begin
                sck  <= 1'b0;
                ss_n <= 1'b1;
                div  <= 16'd0;
                if (start) begin
                    txsr    <= {addr, rw, (rw ? wdata : 16'h0000)};
                    // mosi must already carry A8 before the first rising edge.
                    mosi    <= addr[8];
                    ss_n    <= 1'b0;
                    busy    <= 1'b1;
                    bit_idx <= 5'd0;
                    rxsr    <= 16'd0;
                    state   <= S_LEAD;
                end
            end

            //-------------------------------------------- tsssck: ss_n low -> first sck
            // One full sck period with sck low before clocking begins.
            S_LEAD: begin
                if (tick) begin
                    div <= 16'd0;
                    if (bit_idx == 5'd1) begin   // two half-periods = one sck period
                        bit_idx <= 5'd0;
                        state   <= S_HIGH;
                    end else begin
                        bit_idx <= bit_idx + 5'd1;
                    end
                end else begin
                    div <= div + 16'd1;
                end
            end

            //------------------------------------------------ sck HIGH half-period
            // Rising edge already happened; the sensor samples mosi on it.
            S_HIGH: begin
                sck <= 1'b1;
                if (tick) begin
                    div <= 16'd0;
                    // We are about to drive the FALLING edge. sck is still high, so
                    // miso is mid-bit and stable -- sample it now.
                    rxsr  <= {rxsr[14:0], miso_s};
                    sck   <= 1'b0;
                    state <= S_LOW;
                end else begin
                    div <= div + 16'd1;
                end
            end

            //------------------------------------------------- sck LOW half-period
            // Shift the next bit out on the falling edge, per the datasheet.
            S_LOW: begin
                sck <= 1'b0;
                if (tick) begin
                    div <= 16'd0;
                    if (bit_idx == NBITS[4:0] - 5'd1) begin
                        bit_idx <= 5'd0;
                        state   <= S_TRAIL;
                    end else begin
                        txsr    <= {txsr[24:0], 1'b0};
                        mosi    <= txsr[24];     // next bit, driven on the falling edge
                        bit_idx <= bit_idx + 5'd1;
                        state   <= S_HIGH;
                    end
                end else begin
                    div <= div + 16'd1;
                end
            end

            //------------------------------------ tsckss: last sck fall -> ss_n high
            S_TRAIL: begin
                sck <= 1'b0;
                if (tick) begin
                    div <= 16'd0;
                    if (bit_idx == 5'd1) begin   // one sck period
                        bit_idx <= 5'd0;
                        ss_n    <= 1'b1;
                        rdata   <= rxsr;         // last 16 samples = D15..D0
                        state   <= S_GAP;
                    end else begin
                        bit_idx <= bit_idx + 5'd1;
                    end
                end else begin
                    div <= div + 16'd1;
                end
            end

            //------------------------- >= 2 sck periods, ss_n HIGH, between transactions
            S_GAP: begin
                ss_n <= 1'b1;
                if (tick) begin
                    div <= 16'd0;
                    if (bit_idx == 5'd3) begin   // four half-periods = two sck periods
                        bit_idx <= 5'd0;
                        busy    <= 1'b0;
                        done    <= 1'b1;
                        state   <= S_IDLE;
                    end else begin
                        bit_idx <= bit_idx + 5'd1;
                    end
                end else begin
                    div <= div + 16'd1;
                end
            end

            default: state <= S_IDLE;
            endcase
        end
    end

`ifdef SIM
    initial begin
        if (SCK_HZ > 10_000_000)
            $fatal(1, "cam_spi_master: SCK_HZ %0d exceeds the PYTHON's 10 MHz max (Table 11)", SCK_HZ);
        if (HALF < 1)
            $fatal(1, "cam_spi_master: CLK_HZ/(2*SCK_HZ) rounds to 0");
    end
`endif

endmodule
