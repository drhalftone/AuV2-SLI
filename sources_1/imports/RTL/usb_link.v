`timescale 1ns/1ps
//==============================================================================
// usb_link.v -- bidirectional USB-serial subsystem for AuV2-SLI (FT2232H ch.B).
//
// Drop-in replacement for edid_reader: same status-telemetry inputs and the
// same usb_tx output (TX behaviour is byte-for-byte identical -- it still
// instantiates status_line + uart_tx), PLUS:
//   * usb_rx (P15) feeding uart_rx + uart_ctrl (the 0xA5 host command engine), and
//   * a 1-bit priority arbiter that shares the single uart_tx between the status
//     line and command replies. Command replies WIN (host is half-duplex and
//     waits on them); a status line may pause mid-line while a reply goes out,
//     then resumes -- harmless for the CRLF-terminated telemetry.
//
// Stage-2 taps (sli_ctrl, lut_loaded, table read ports) are exposed for the
// pixel datapath; they are defaulted in the VHDL component so the top can leave
// them open until the datapath is wired up.
//==============================================================================
module usb_link #(
    parameter integer CLK_HZ = 100_000_000,
    parameter integer WIN    = 50_000_000      // ~0.5 s status window
)(
    input  wire        clk100,
    input  wire [7:0]  led,
    input  wire [7:0]  dbg,
    input  wire [7:0]  mrg,
    input  wire [7:0]  tlp,
    input  wire [7:0]  tcnt,
    input  wire [7:0]  olp,
    input  wire        usb_rx,
    output wire        usb_tx,

    // ---- pin-state readback (reg 0x10): physical switches + post-override value ----
    input  wire [3:0]  phys_sw,          // raw newSW pins {R,G,B,orient} (async)
    input  wire [3:0]  eff_sw,           // effective_sw after the 0x13 override (pixel_clk)

    // ---- Stage-2 control / table taps (safe to leave open) ----
    output wire [7:0]  sli_ctrl,
    output wire        sli_ctrl_en,
    output wire        lut_loaded,
    input  wire [7:0]  corr_addr,  output wire [7:0] corr_dout,
    input  wire [9:0]  lut_addr,   output wire [7:0] lut_dout,
    input  wire [10:0] lutv_addr,  output wire [7:0] lutv_dout,

    // ---- captured-EDID read port (rdtbl TGT_EDID) -> edid_merge's 3rd port ----
    output wire [7:0]  edid_rd_addr,
    input  wire [7:0]  edid_rd_data
);
    // ---- power-up reset ----
    reg [3:0] rstcnt = 4'd0;
    reg       rst    = 1'b1;
    always @(posedge clk100) begin
        if (rstcnt != 4'hF) begin rstcnt <= rstcnt + 4'h1; rst <= 1'b1; end
        else rst <= 1'b0;
    end

    // ---- CDC sample of the async status inputs into clk100 (as in edid_reader) ----
    reg [7:0] led_d0=0, led_s=0, dbg_d0=0, dbg_s=0, mrg_d0=0, mrg_s=0;
    reg [7:0] tlp_d0=0, tlp_s=0, tcnt_d0=0, tcnt_s=0, olp_d0=0, olp_s=0;
    reg vs0=0, vs1=0, vs2=0;
    always @(posedge clk100) begin
        led_d0<=led; led_s<=led_d0; dbg_d0<=dbg; dbg_s<=dbg_d0; mrg_d0<=mrg; mrg_s<=mrg_d0;
        tlp_d0<=tlp; tlp_s<=tlp_d0; tcnt_d0<=tcnt; tcnt_s<=tcnt_d0; olp_d0<=olp; olp_s<=olp_d0;
        vs0<=led[7]; vs1<=vs0; vs2<=vs1;
    end
    wire vs_rise = vs1 & ~vs2;

    // 2FF sync of the quasi-static switch/override bits into clk100 (reg 0x10).
    reg [3:0] psw0=0, psw1=0, esw0=0, esw1=0;
    always @(posedge clk100) begin
        psw0 <= phys_sw; psw1 <= psw0;
        esw0 <= eff_sw;  esw1 <= esw0;
    end

    // ---- status window + per-window vsync (frame) counter ----
    reg [31:0] win = 0; reg [15:0] vs_run = 0, vs_lat = 0; reg stat_tick = 0;
    always @(posedge clk100) begin
        stat_tick <= 1'b0;
        if (win >= WIN-1) begin win<=0; vs_lat<=vs_run; vs_run<=0; stat_tick<=1'b1; end
        else begin win<=win+1; if (vs_rise) vs_run<=vs_run+1'b1; end
    end

    // ---- producers ----
    wire [7:0] s_data;  wire s_send, s_busy;        // status_line producer
    wire [7:0] c_data;  wire c_send, c_active;      // uart_ctrl producer
    wire       u_busy;                              // shared uart_tx busy

    // ---- 1-bit priority arbiter (ctrl wins). Switch only between bytes (~u_busy). ----
    reg owner = 1'b0;                               // 0 = status, 1 = ctrl
    always @(posedge clk100) begin
        if (rst)        owner <= 1'b0;
        else if (!u_busy) owner <= c_active;        // re-evaluate each idle-between-bytes
    end
    wire        s_tx_busy = owner ? 1'b1   : u_busy;   // back-pressure non-owner
    wire        c_tx_busy = owner ? u_busy : 1'b1;
    wire [7:0]  tx_data   = owner ? c_data : s_data;
    wire        tx_send   = owner ? c_send : s_send;
    wire        s_go      = stat_tick & ~c_active & ~owner;   // don't start a line if ctrl is busy

    // ---- status line (telemetry) ----
    status_line i_stat (
        .clk(clk100), .go(s_go),
        .led_s(led_s), .dbg_s(dbg_s), .mrg(mrg_s), .tlp(tlp_s), .tcnt(tcnt_s), .olp(olp_s), .vs_lat(vs_lat),
        .tx_data(s_data), .tx_send(s_send), .tx_busy(s_tx_busy), .busy(s_busy)
    );

    // ---- receive + command engine ----
    wire [7:0] rx_data;  wire rx_valid;
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(115200)) i_urx (
        .clk(clk100), .rst(rst), .rx(usb_rx), .data(rx_data), .valid(rx_valid)
    );
    uart_ctrl i_ctrl (
        .clk(clk100), .rst(rst),
        .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(c_data), .tx_send(c_send), .tx_busy(c_tx_busy), .tx_active(c_active),
        .led(led_s), .pins({esw1, psw1}),
        .sli_ctrl(sli_ctrl), .sli_ctrl_en(sli_ctrl_en), .lut_loaded(lut_loaded),
        .corr_addr(corr_addr), .corr_dout(corr_dout),
        .lut_addr(lut_addr),   .lut_dout(lut_dout),
        .lutv_addr(lutv_addr), .lutv_dout(lutv_dout),
        .edid_rd_addr(edid_rd_addr), .edid_rd_data(edid_rd_data)
    );

    // ---- shared transmitter ----
    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(115200)) i_utx (
        .clk(clk100), .rst(rst), .data(tx_data), .send(tx_send),
        .tx(usb_tx), .busy(u_busy)
    );
endmodule
