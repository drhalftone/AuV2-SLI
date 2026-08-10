`timescale 1ns/1ps
//=============================================================================
// cam_pinwalk - a DMM instrument for the last unproven hop: board -> sensor.
//
// WHY THIS EXISTS. cam_probe proved everything up to the camera board: pul=10
// says the DF40 is seated, the board's 3.3 V rail is up, and ss_n / reset_n /
// trigger0-2 land on the balls pt_camera.xdc claims. But it is blind to exactly
// the wires that matter most -- mosi, sck, miso and clk_pll are the four bank-14
// signals with NO board pull, so there is nothing to sense against. Three of
// those four are the SPI bus.
//
// Those can only be checked with a meter at the sensor end, and this bitstream
// makes that a one-person job.
//
//-----------------------------------------------------------------------------
// HOW TO USE IT
//
// Every output sits at its INACTIVE level. Once every 2 s, exactly ONE signal
// flips to the opposite level, in rotation, and the matching LED lights. So:
//
//   1. Put the meter's black lead on GND and the red lead on ONE SENSOR PIN.
//   2. Watch. The pin should change level exactly once per 16 s cycle.
//   3. The LED that is lit WHEN IT CHANGES tells you which FPGA signal got there.
//
//   LED / step   signal      sensor pin    idle     driven
//   ----------   --------    ----------    ----     ------
//     0          mosi            2         0 V      3.3 V
//     1          sck             4         0 V      3.3 V
//     2          clk_pll        25         0 V      3.3 V
//     3          reset_n        46         0 V      3.3 V
//     4          ss_n           47         3.3 V    0 V      <- inverted
//     5          trigger0       41         0 V      3.3 V
//     6          trigger1       42         0 V      3.3 V
//     7          trigger2       43         0 V      3.3 V
//
// WHAT THE THREE OUTCOMES MEAN
//
//   changes on its OWN step        -> that wire is good, end to end.
//   never changes at all           -> OPEN between the DF40 and that sensor pin.
//   changes on SOMEONE ELSE'S step -> the two nets are BRIDGED, or the pin map
//                                     is wrong. The lit LED names the culprit.
//
// The third case is the one a continuity beep-test tends to miss, and it is the
// failure you just found by eye once already.
//
//-----------------------------------------------------------------------------
// SAFETY. Idle levels are the sensor's safe states, matching the board's own
// pull resistors: ss_n HIGH (deasserted), reset_n LOW (held in reset), triggers
// LOW (no exposures), sck/mosi/clk_pll LOW. During the ss_n step select goes
// active, but sck is static -- with no clock edge the sensor cannot shift
// anything in, so no transaction can occur. Nothing here writes a register:
// there is no SPI master in this design at all.
//=============================================================================
module cam_pinwalk #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer BAUD   =     115_200,
    parameter integer STEP_CY = 2 * CLK_HZ        // 2 s per signal
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output reg        cam_mosi,
    output reg        cam_sck,
    output reg        cam_clk_pll,
    output reg        cam_reset_n,
    output reg        cam_ss_n,
    output reg  [2:0] cam_trigger,
    // inout: step 8 drives miso BACKWARDS to test that wire. See below.
    inout  wire       cam_miso,
    input  wire [1:0] cam_monitor
);
    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    localparam [3:0] N_STEPS = 4'd9;       // 0..7 outputs, 8 = miso drive-back

    reg [3:0]  step = 4'd0;
    reg [27:0] tmr  = 28'd0;
    reg        tick;                       // 1-clk strobe at each step change

    always @(posedge clk) begin
        tick <= 1'b0;
        if (rst) begin
            step <= 4'd0;
            tmr  <= 28'd0;
        end else if (tmr == STEP_CY[27:0] - 28'd1) begin
            tmr  <= 28'd0;
            step <= (step == N_STEPS - 4'd1) ? 4'd0 : step + 4'd1;
            tick <= 1'b1;
        end else begin
            tmr <= tmr + 28'd1;
        end
    end

    // Idle = the sensor's safe level. The active step inverts exactly one.
    always @(posedge clk) begin
        cam_mosi    <= (step == 4'd0);
        cam_sck     <= (step == 4'd1);
        cam_clk_pll <= (step == 4'd2);
        cam_reset_n <= (step == 4'd3);
        cam_ss_n    <= (step != 4'd4);     // inverted: idles HIGH
        cam_trigger <= {(step == 4'd7), (step == 4'd6), (step == 4'd5)};
        // Step 8 lights ALL EIGHT so it cannot be mistaken for a one-hot step.
        led         <= (step == 4'd8) ? 8'hFF : (8'd1 << step[2:0]);
    end

    //-------------------------------------------------------------------------
    // STEP 8 -- DRIVE miso BACKWARDS, the only way to test that wire.
    //
    // miso is an FPGA INPUT, so the walk cannot exercise it: nothing drives it,
    // and a healthy sensor holds it Hi-Z whenever ss_n is deasserted. A meter on
    // sensor pin 3 therefore reads a floating ~0.1 V no matter what, which is
    // exactly the no-verdict result we got.
    //
    // So for two seconds we drive it from the FPGA end and look for 3.3 V at the
    // sensor pin. That proves the copper: FPGA ball AB18 -> J1 -> board -> pin 3.
    //
    // NO CONTENTION: the datasheet has the sensor's miso high-Z outside the data
    // phase of a read (CAMERA_SENSOR_PROTOCOL.md §1), ss_n is held HIGH for the
    // whole of this step, and there is no SPI master in this design to assert it.
    // DRIVE 4 in the XDC keeps the current low even if that assumption is wrong.
    //-------------------------------------------------------------------------
    wire miso_drive = (step == 4'd8);
    IOBUF u_miso (.I(1'b1), .T(~miso_drive), .O(), .IO(cam_miso));

    //------------------------------------------------------------------- UART
    //   step=3 reset_n  lvl=1
    localparam integer MSG_LEN = 23;

    // Eight chars per name, space-padded, so the formatter stays a mux.
    function [63:0] signame(input [3:0] s);
        case (s)
        4'd0: signame = "mosi    ";
        4'd1: signame = "sck     ";
        4'd2: signame = "clk_pll ";
        4'd3: signame = "reset_n ";
        4'd4: signame = "ss_n    ";
        4'd5: signame = "trigger0";
        4'd6: signame = "trigger1";
        4'd7: signame = "trigger2";
        4'd8: signame = "miso<-  ";     // driven backwards, all 8 LEDs lit
        default: signame = "?       ";
        endcase
    endfunction

    // The level the active signal is being driven to. Only ss_n goes low.
    wire active_lvl = (step != 4'd4);

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [4:0] idx = 5'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    wire [63:0] nm = signame(step);

    always @(*) begin
        case (idx)
        5'd0 : ch = "s";  5'd1 : ch = "t";  5'd2 : ch = "e";  5'd3 : ch = "p";
        5'd4 : ch = "=";
        5'd5 : ch = 8'h30 + {4'd0, step};
        5'd6 : ch = " ";
        // name[0..7], MSB-first out of the packed 64-bit literal
        5'd7 : ch = nm[63:56];
        5'd8 : ch = nm[55:48];
        5'd9 : ch = nm[47:40];
        5'd10: ch = nm[39:32];
        5'd11: ch = nm[31:24];
        5'd12: ch = nm[23:16];
        5'd13: ch = nm[15:8];
        5'd14: ch = nm[7:0];
        5'd15: ch = " ";
        5'd16: ch = "l";  5'd17: ch = "v";  5'd18: ch = "l";  5'd19: ch = "=";
        5'd20: ch = active_lvl ? "1" : "0";
        5'd21: ch = 8'h0D;
        5'd22: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0;
            idx      <= 5'd0;
        end else if (!busy_msg) begin
            if (tick) begin                // announce each new step once
                busy_msg <= 1'b1;
                idx      <= 5'd0;
            end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[4:0] - 5'd1) begin
                busy_msg <= 1'b0;
                idx      <= 5'd0;
            end else idx <= idx + 5'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

    // monitor is an input here and nothing reads it. Keep the port alive so the
    // pins stay constrained rather than falling to UNUSEDPIN.
    wire _unused = (|cam_monitor) | usb_rx;

endmodule
