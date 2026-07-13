`timescale 1ns/1ps
//==============================================================================
// drp_clkgen13 - full 13-mode DRP pixel clock generator (Phase D2).
//   Wraps drp_recfg (13-mode controller) + MMCME2_ADV. Present MODE_IDX (0..12,
//   mode_table.vh order), pulse SEN; the MMCM retargets pixel/x1/x5 to that mode.
//   MMCM power-up params = idx 13 (1280x1024@60, 108 MHz -- the FASTEST mode) so
//   Vivado STA analyses the worst-case clock frequencies (x5 = 540 MHz).
//==============================================================================
module drp_clkgen13 (
    input  wire clk100,
    input  wire [3:0] mode_idx,
    input  wire sen,
    output wire srdy,
    output wire pixel_clk,
    output wire pixel_io_clk_x1,
    output wire pixel_io_clk_x5,
    output wire locked
);
    wire [6:0]  daddr;
    wire [15:0] di, do_drp;
    wire        dwe, den, drdy, dclk, rst_mmcm, mmcm_locked;
    wire        clkfb, clkfb_bufg, pix_raw, x1_raw, x5_raw;

    drp_recfg i_drp (
        .SCLK(clk100), .RST(1'b0), .MODE_IDX(mode_idx), .SEN(sen), .SRDY(srdy),
        .DO(do_drp), .DRDY(drdy), .LOCKED(mmcm_locked),
        .DWE(dwe), .DEN(den), .DADDR(daddr), .DI(di), .DCLK(dclk), .RST_MMCM(rst_mmcm)
    );

    // Power-up = idx 13 (1280x1024@60): M=54 D=5 O0=10 O1=10 O2=2 -> VCO 1080,
    // pix 108.000, x5 = 540 MHz.
    //
    // These parameters are what Vivado's STA analyses. They MUST track the FASTEST mode
    // the DRP can retune to, not the power-on-convenient one: if they said 78.67 MHz
    // (idx2, x5 = 393 MHz) while the design can be reconfigured to 108 MHz (x5 = 540 MHz),
    // timing would be signed off against a clock the hardware never actually runs at, and
    // the serializer paths would be unvalidated at their real worst case.
    MMCME2_ADV #(
        .BANDWIDTH("OPTIMIZED"), .CLKOUT4_CASCADE("FALSE"), .COMPENSATION("ZHOLD"),
        .STARTUP_WAIT("FALSE"), .DIVCLK_DIVIDE(5), .CLKFBOUT_MULT_F(54.000),
        .CLKFBOUT_PHASE(0.000), .CLKFBOUT_USE_FINE_PS("FALSE"),
        .CLKOUT0_DIVIDE_F(10.000), .CLKOUT0_PHASE(0.000), .CLKOUT0_DUTY_CYCLE(0.500),
        .CLKOUT0_USE_FINE_PS("FALSE"),
        .CLKOUT1_DIVIDE(10), .CLKOUT1_PHASE(0.000), .CLKOUT1_DUTY_CYCLE(0.500),
        .CLKOUT2_DIVIDE(2),  .CLKOUT2_PHASE(0.000), .CLKOUT2_DUTY_CYCLE(0.500),
        .CLKIN1_PERIOD(10.000), .REF_JITTER1(0.010)
    ) i_mmcm (
        .CLKFBOUT(clkfb), .CLKFBOUTB(),
        .CLKOUT0(pix_raw), .CLKOUT0B(), .CLKOUT1(x1_raw), .CLKOUT1B(),
        .CLKOUT2(x5_raw), .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(),
        .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .CLKFBIN(clkfb_bufg), .CLKIN1(clk100), .CLKIN2(1'b0), .CLKINSEL(1'b1),
        .DADDR(daddr), .DCLK(dclk), .DEN(den), .DI(di), .DO(do_drp), .DRDY(drdy), .DWE(dwe),
        .PSCLK(1'b0), .PSEN(1'b0), .PSINCDEC(1'b0), .PSDONE(),
        .LOCKED(mmcm_locked), .CLKINSTOPPED(), .CLKFBSTOPPED(), .PWRDWN(1'b0), .RST(rst_mmcm)
    );

    BUFG b_fb  (.I(clkfb),   .O(clkfb_bufg));
    // Au V2 adaptation: NO output BUFGs here. pixel/x1/x5 feed clk_selector's
    // BUFGMUX in the top, which provides the global buffering. Raw MMCM CLKOUT ->
    // BUFGMUX avoids the BUFG->BUFGMUX cascade (Place 30-120) an output BUFG causes.
    // (On the Mimas these drove the serializer directly, hence the original BUFGs.)
    assign pixel_clk       = pix_raw;
    assign pixel_io_clk_x1 = x1_raw;
    assign pixel_io_clk_x5 = x5_raw;
    assign locked = mmcm_locked;
endmodule
