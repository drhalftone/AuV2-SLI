`timescale 1ns/1ps
//==============================================================================
// edid_reader.v -- status telemetry over the FT2232H ch.B UART (usb_tx, P16).
//
// NOTE: in the EDID-MERGE build the DDC read/serve is owned by edid_merge, so this
// module no longer reads/dumps EDID -- it is now just the status-line reporter
// (~2x/second). It surfaces the board status byte (led), the hdmi_io decode debug
// (dbg), and the edid_merge state (mrg) over COM6.
//==============================================================================
module edid_reader #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer WIN    = 50_000_000      // ~0.5 s status window
)(
    input  wire clk100,
    input  wire [7:0] led,
    input  wire [7:0] dbg,
    input  wire [7:0] mrg,        // edid_merge status (dbg2[7:0])
    input  wire [7:0] tlp,        // sampled top-left red (diagnostic)
    input  wire [7:0] tcnt,       // trigger pulse count (diagnostic)
    output wire usb_tx
);
    // power-up reset
    reg [3:0] rstcnt = 4'd0;
    reg       rst    = 1'b1;
    always @(posedge clk100) begin
        if (rstcnt != 4'hF) begin rstcnt <= rstcnt + 4'h1; rst <= 1'b1; end
        else rst <= 1'b0;
    end

    // CDC sample + window + vsync(frame) counter
    reg [7:0] led_d0=0, led_s=0, dbg_d0=0, dbg_s=0, mrg_d0=0, mrg_s=0;
    reg [7:0] tlp_d0=0, tlp_s=0, tcnt_d0=0, tcnt_s=0;
    reg vs0=0, vs1=0, vs2=0;
    always @(posedge clk100) begin
        led_d0<=led; led_s<=led_d0; dbg_d0<=dbg; dbg_s<=dbg_d0; mrg_d0<=mrg; mrg_s<=mrg_d0;
        tlp_d0<=tlp; tlp_s<=tlp_d0; tcnt_d0<=tcnt; tcnt_s<=tcnt_d0;
        vs0<=led[7]; vs1<=vs0; vs2<=vs1;
    end
    wire vs_rise = vs1 & ~vs2;
    reg [31:0] win=0; reg [15:0] vs_run=0, vs_lat=0; reg stat_tick=0;
    always @(posedge clk100) begin
        stat_tick <= 1'b0;
        if (win >= WIN-1) begin win<=0; vs_lat<=vs_run; vs_run<=0; stat_tick<=1'b1; end
        else begin win<=win+1; if (vs_rise) vs_run<=vs_run+1'b1; end
    end

    // single UART producer: status_line -> uart_tx
    wire [7:0] s_data;
    wire       s_send, s_busy, u_busy;
    status_line i_stat (
        .clk(clk100), .go(stat_tick),
        .led_s(led_s), .dbg_s(dbg_s), .mrg(mrg_s), .tlp(tlp_s), .tcnt(tcnt_s), .vs_lat(vs_lat),
        .tx_data(s_data), .tx_send(s_send), .tx_busy(u_busy), .busy(s_busy)
    );
    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(115200)) i_utx (
        .clk(clk100), .rst(rst), .data(s_data), .send(s_send),
        .tx(usb_tx), .busy(u_busy)
    );
endmodule
