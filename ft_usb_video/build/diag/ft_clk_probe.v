`timescale 1ns/1ps
//==============================================================================
// ft_clk_probe.v -- non-invasive "where is the FT601 clock?" diagnostic.
//
// The ft_video design reports FTCLK=0: the FT601's 100 MHz CLKOUT is not
// reaching the FPGA on the pin the build assumed (H4, the TOP element map).
// FT601 chip config, the bitstream, and the H4 pin derivation are all verified
// correct, and the board still reads FTCLK=0 -- so the open question is purely
// PHYSICAL: on which connector does CLKOUT actually arrive?
//
// This design watches BOTH candidate clock pins at once, as INPUTS only:
//     ftclk_top  = H4   (Alchitry PtV2 TOP element map,  ft_clk element A41)
//     ftclk_bot  = D17  (Alchitry PtV2 BOTTOM element map, same A41)
// Each drives a tiny free-running counter; a toggling bit from each is re-timed
// into the always-alive onboard-oscillator domain and watch-dogged. Every ~0.5 s
// it prints, over the Pt's USB serial (COM):
//
//     TOP=x BOT=x\r\n     (1 = that pin is toggling, 0 = dead)
//
// It drives NOTHING on either element bus except RESET_N=1 / WAKEUP#=1 on both
// connectors (benign: just holds the FT601 out of reset / awake wherever it is).
// No ft_data / ft_wr / ft_oe are touched, so there is zero contention risk with
// anything else on the stack. Interpreting the result:
//     TOP=1        -> CLKOUT is on H4; the ft_video top build's pins are right,
//                     and the earlier FTCLK=0 was a transient/seating fluke.
//     BOT=1        -> CLKOUT is on D17; the board is effectively on the BOTTOM
//                     connector -> rebuild ft_video with the bottom map.
//     TOP=0 BOT=0  -> CLKOUT is on neither pin -> the FT601 isn't clocking at all
//                     (chip not truly in sync-FIFO / dead net) -> scope CLKOUT.
//==============================================================================
module ft_clk_probe (
    input  wire       clk,          // W19  onboard 100 MHz (always alive)
    input  wire       rst_n,        // N15  reset button (active low)
    output wire [7:0] led,

    output wire       usb_tx,       // AA21 COM telemetry

    // candidate FT601 clock inputs (inputs ONLY)
    input  wire       ftclk_top,    // H4   TOP element map
    input  wire       ftclk_bot,    // D17  BOTTOM element map

    // hold the FT601 out of reset / awake on BOTH connectors (benign constants)
    output wire       ft_reset_top, // AB21
    output wire       ft_wakeup_top,// AB22
    output wire       ft_reset_bot, // AA1
    output wire       ft_wakeup_bot // AB1
);
    assign ft_reset_top  = 1'b1;   // RESET_N deasserted
    assign ft_wakeup_top = 1'b1;   // WAKEUP#  deasserted
    assign ft_reset_bot  = 1'b1;
    assign ft_wakeup_bot = 1'b1;

    // --- onboard-clock reset sync ---
    reg [2:0] rsync = 3'b111;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rsync <= 3'b111;
        else        rsync <= {rsync[1:0], 1'b0};
    end
    wire rst = rsync[2];

    // --- free-running counters on each candidate clock (async, self-clocked) ---
    reg [23:0] ctop = 24'd0;
    always @(posedge ftclk_top) ctop <= ctop + 24'd1;   // toggles ctop[3] @ ftclk/16

    reg [23:0] cbot = 24'd0;
    always @(posedge ftclk_bot) cbot <= cbot + 24'd1;

    // --- re-time a toggling bit from each into the onboard clk, watchdog ~84 ms ---
    // TOP
    reg t0=1'b0, t1=1'b0, t1d=1'b0;
    always @(posedge clk) begin t0 <= ctop[3]; t1 <= t0; t1d <= t1; end
    reg [22:0] wd_top = 23'd0;
    always @(posedge clk) begin
        if (t1 ^ t1d)          wd_top <= 23'h7FFFFF;
        else if (wd_top != 0)  wd_top <= wd_top - 23'd1;
    end
    wire top_alive = (wd_top != 23'd0);

    // BOT
    reg b0=1'b0, b1=1'b0, b1d=1'b0;
    always @(posedge clk) begin b0 <= cbot[3]; b1 <= b0; b1d <= b1; end
    reg [22:0] wd_bot = 23'd0;
    always @(posedge clk) begin
        if (b1 ^ b1d)          wd_bot <= 23'h7FFFFF;
        else if (wd_bot != 0)  wd_bot <= wd_bot - 23'd1;
    end
    wire bot_alive = (wd_bot != 23'd0);

    // --- LEDs: [0] board heartbeat, [2] TOP clk alive, [3] BOT clk alive ---
    reg [26:0] hb = 27'd0;
    always @(posedge clk) hb <= hb + 27'd1;
    assign led = { 4'b0, bot_alive, top_alive, 1'b0, hb[25] };

    // -------------------------------------------------------------------------
    // Telemetry line:  "TOP=x BOT=x\r\n"  (13 bytes), every ~0.5 s.
    // -------------------------------------------------------------------------
    localparam integer LEN    = 13;
    localparam integer PERIOD = 50_000_000;   // 0.5 s @ 100 MHz

    reg  [7:0] ubyte;
    reg        usend;
    wire       ubusy;
    uart_tx #(.CLK_HZ(100_000_000), .BAUD(115200)) u_uart (
        .clk(clk), .rst(rst), .data(ubyte), .send(usend), .tx(usb_tx), .busy(ubusy)
    );

    reg top_lat, bot_lat;
    reg [7:0] mb;
    reg [3:0] pos;
    always @(*) begin
        case (pos)
            4'd0:  mb = "T";  4'd1:  mb = "O";  4'd2:  mb = "P";  4'd3: mb = "=";
            4'd4:  mb = top_lat ? "1" : "0";
            4'd5:  mb = " ";
            4'd6:  mb = "B";  4'd7:  mb = "O";  4'd8:  mb = "T";  4'd9: mb = "=";
            4'd10: mb = bot_lat ? "1" : "0";
            4'd11: mb = 8'h0D;
            default: mb = 8'h0A;   // pos 12
        endcase
    end

    reg [25:0] timer   = 26'd0;
    reg        sending = 1'b0;
    always @(posedge clk) begin
        if (rst) begin
            timer <= 0; pos <= 0; sending <= 0; usend <= 0;
        end else begin
            usend <= 1'b0;
            ubyte <= mb;
            if (!sending) begin
                if (timer == PERIOD-1) begin
                    timer   <= 0;
                    top_lat <= top_alive;
                    bot_lat <= bot_alive;
                    pos     <= 0;
                    sending <= 1'b1;
                end else begin
                    timer <= timer + 1'b1;
                end
            end else begin
                if (usend) begin
                    if (pos == LEN-1) sending <= 1'b0;
                    else              pos     <= pos + 1'b1;
                end else if (!ubusy) begin
                    usend <= 1'b1;
                end
            end
        end
    end
endmodule
