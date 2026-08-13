`timescale 1ns/1ps
//=============================================================================
// ft_probe_bottom.v -- is the Ft+ actually WIRED to the bottom connector?
//
// cam_frame_ft's telemetry showed a free-running counter on ft_clk stuck at
// zero: the FT601 is not clocking the FPGA. The chip itself is fine -- it
// enumerates as a D3XX device and its EEPROM reads back exactly right (FIFOMode
// 245-sync, 1 channel, 100 MHz, USB 3.10). So either the clock pin is not
// connected, or nothing on that connector is.
//
// This distinguishes the two WITHOUT touching the board, by using a pin whose
// effect is observable from the host:
//
//   ft_reset (AA1) is the FT601's RESET_N. Drive it LOW and, IF that pin really
//   reaches the chip, the FT601 must fall off the USB bus -- createDeviceInfoList
//   drops to 0. Let it go and the device comes back.
//
// So the probe square-waves RESET_N at ~0.19 Hz (2.68 s per half) while printing
// the phase and an ft_clk activity counter on COM6. Then:
//
//   enumeration follows the phase  -> the connector is seated and AA1 lands on
//                                     the FT601; a dead ft_clk is then a narrow
//                                     clock-pin problem, not a mounting one
//   enumeration never changes      -> nothing on this connector reaches the Ft+
//                                     (not seated, or the board is on the other
//                                     connector), which no RTL change can fix
//
// Everything else is parked safe: WR#/OE#/RD# high (idle), DATA/BE tri-stated,
// WAKEUP# deasserted -- the FT601 is never asked to do anything during the test.
//=============================================================================
module ft_probe_bottom (
    input  wire        clk,          // 100 MHz board clock
    input  wire        rst_n,
    output reg  [7:0]  led,
    output wire        usb_tx,       // COM6, 1 Mbaud

    input  wire        ft_clk,
    inout  wire [31:0] ft_data,
    inout  wire [3:0]  ft_be,
    input  wire        ft_txe, ft_rxf,
    output wire        ft_wr, ft_oe, ft_rd,
    output wire        ft_wakeup, ft_reset
);
    assign ft_wr     = 1'b1;
    assign ft_oe     = 1'b1;
    assign ft_rd     = 1'b1;
    assign ft_data   = 32'bz;
    assign ft_be     = 4'bz;
    assign ft_wakeup = 1'b1;

    reg [1:0] rstn_s = 2'b00;
    always @(posedge clk) rstn_s <= {rstn_s[0], rst_n};
    wire rst = !rstn_s[1];

    // ~2.68 s per half period at 100 MHz: slow enough that a host polling loop
    // at 4 Hz cannot alias it, and slow enough for USB re-enumeration to finish.
    reg [28:0] phase_cnt = 29'd0;
    reg        rstn_out  = 1'b1;
    always @(posedge clk) begin
        phase_cnt <= phase_cnt + 29'd1;
        if (phase_cnt == 29'd268_435_455) rstn_out <= ~rstn_out;
    end
    assign ft_reset = rstn_out & rstn_s[1];

    // Activity counter in the ft_clk domain. No reset: if this ever moves, the
    // FT601 is clocking us. Sampled into clk for reporting -- tearing across the
    // byte does not matter, only whether it changes at all.
    reg [23:0] ftc = 24'd0;
    always @(posedge ft_clk) ftc <= ftc + 24'd1;
    reg [7:0] ftc_s1 = 8'd0, ftc_s2 = 8'd0;
    always @(posedge clk) begin ftc_s1 <= ftc[23:16]; ftc_s2 <= ftc_s1; end

    wire [11:0] stat = {rstn_out, ft_txe, ft_rxf, 1'b0, ftc_s2};

    reg [11:0] shold = 12'd0;
    reg [1:0]  nib   = 2'd0;
    reg [23:0] utick = 24'd0;
    reg [7:0]  ubyte = 8'd0;
    reg        usend = 1'b0;
    reg [1:0]  ust   = 2'd0;
    wire       ubusy;
    wire [3:0] n = shold[11 - nib*4 -: 4];

    always @(posedge clk) begin
        usend <= 1'b0;
        if (rst) begin ust <= 2'd0; utick <= 24'd0; nib <= 2'd0; end
        else case (ust)
        2'd0: begin
            utick <= utick + 24'd1;
            if (utick == 24'd10_000_000) begin
                utick <= 24'd0; shold <= stat; nib <= 2'd0; ust <= 2'd1;
            end
        end
        2'd1: if (!ubusy && !usend) begin
            ubyte <= (n < 4'd10) ? (8'd48 + {4'd0,n}) : (8'd55 + {4'd0,n});
            usend <= 1'b1;
            if (nib == 2'd2) ust <= 2'd2; else nib <= nib + 2'd1;
        end
        2'd2: if (!ubusy && !usend) begin ubyte <= 8'h0D; usend <= 1'b1; ust <= 2'd3; end
        2'd3: if (!ubusy && !usend) begin ubyte <= 8'h0A; usend <= 1'b1; ust <= 2'd0; end
        endcase
    end

    uart_tx #(.CLK_HZ(100_000_000), .BAUD(1_000_000)) u_uart (
        .clk(clk), .rst(rst), .data(ubyte), .send(usend), .tx(usb_tx), .busy(ubusy)
    );

    always @(posedge clk) led <= {rstn_out, 2'b00, ftc_s2[4:0]};
endmodule
