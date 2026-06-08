`timescale 1ns/1ps
//==============================================================================
// mode_timing_rom - per-mode video geometry lookup (Phase D2).
//
//   Combinational ROM over the SAME curated table as mode_select (mode_table.vh),
//   indexed by mode_idx (0..12, priority order). Emits the geometry that feeds
//   video_timing_gen_rt, so the generated timing always matches the mode whose
//   pixel clock drp_clkgen13 produces for the same index. Single source of truth:
//   both this and mode_select include mode_table.vh.
//
//   The initial-block array init is the same pattern mode_select.v uses (HW-proven
//   in Phase C); Vivado infers it as a distributed ROM.
//==============================================================================
module mode_timing_rom (
    input  wire [3:0]  mode_idx,
    output reg  [11:0] h_active,
    output reg  [11:0] h_fp,
    output reg  [11:0] h_sync,
    output reg  [11:0] h_bp,
    output reg  [11:0] v_active,
    output reg  [11:0] v_fp,
    output reg  [11:0] v_sync,
    output reg  [11:0] v_bp,
    output reg         h_pol,
    output reg         v_pol,
    output reg  [16:0] pclk_khz       // pixel clock for this mode (debug / cross-check)
);
    // Table storage (mode_table.vh's MROW macro writes every one of these).
    reg [11:0] T_HACT [0:12], T_VACT [0:12];
    reg [11:0] T_HFP  [0:12], T_HSYNC[0:12], T_HBP[0:12];
    reg [11:0] T_VFP  [0:12], T_VSYNC[0:12], T_VBP[0:12];
    reg [7:0]  T_REFR [0:12];
    reg [16:0] T_PCLK [0:12];
    reg        T_HPOL [0:12], T_VPOL[0:12];

    initial begin
        `include "mode_table.vh"
    end

    always @* begin
        h_active = T_HACT [mode_idx];
        h_fp     = T_HFP  [mode_idx];
        h_sync   = T_HSYNC[mode_idx];
        h_bp     = T_HBP  [mode_idx];
        v_active = T_VACT [mode_idx];
        v_fp     = T_VFP  [mode_idx];
        v_sync   = T_VSYNC[mode_idx];
        v_bp     = T_VBP  [mode_idx];
        h_pol    = T_HPOL [mode_idx];
        v_pol    = T_VPOL [mode_idx];
        pclk_khz = T_PCLK [mode_idx];
    end
endmodule
