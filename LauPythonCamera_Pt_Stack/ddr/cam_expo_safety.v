`timescale 1ns/1ps
//=============================================================================
// cam_expo_safety.v - the exposure limit, ENFORCED. Never wedge the sensor.
//
// An exposure longer than the trigger period minus the sensor's overhead
// WEDGES the PYTHON1300: it stops integrating, no kernel reaches the FIFO, and
// only reconfiguring the part (opcode 6, or a bitstream load) brings it back.
// MEASURED 2026-10-07: the live viewer's slider top of 8280 us at 120 Hz froze
// the sensor with its last integration at 8.29 ms. Publishing a limit (MAXEXP,
// reg 0x53) was not enough -- anything that did not read it could still walk
// off the cliff -- and the published limit was itself stale: its 44.1 us gap
// predates the CDS timing program, and 8279 us wedged too. The cliff was
// re-measured (usb_link.v has the numbers): the gap is 78 us, flat from 60 to
// 120 Hz. So cam_frame_ft enforces it in three layers, the arithmetic for all
// of which lives here:
//
//   1. CLAMP. Every exposure command is limited to expo_lim, the same formula
//      as MAXEXP: period - 80 us - 10 us, in 375 ns exposure units.
//   2. AUTO-SHORTEN. If the period shrinks under an exposure that was legal
//      when set -- sync unticked, the display unplugged (gl_live drops after
//      XS_TIMEOUT and the camera falls back to free-running), opcode 2 -- the
//      caller cuts the exposure to expo_lim at the next trig_start.
//   3. SPACING GUARD (since_ok). Neither of the above is instant: the write
//      waits for the SPI poller, and the sensor applies exposure0 ONE FRAME
//      LATE (datasheet Table 8). So the trigger generator may not fire until
//      the exposure that may still be in effect -- the larger of the last two
//      values, for three triggers after any change -- plus the 80 us gap has
//      elapsed since the previous trigger. A slower or skipped frame is
//      recoverable; a wedge is not.
//
// The guard uses the bare 80 us gap while the clamp also takes the 10 us
// margin, so at exactly expo_lim the guard has 10 us of slack against vsync
// jitter and never bites in steady state -- full rate is untouched.
//
// gl_div (one trigger per five vsyncs) is NOT period-clamped: its trigger
// spacing is five frames, and clamping to one would forbid exposures it can
// legally take. The spacing guard still covers it.
//=============================================================================
module cam_expo_safety #(
    parameter [15:0] EXPOSURE = 16'h0640        // power-on / boot-ROM exposure
)(
    input  wire        clk,                     // 100 MHz, 10 ns ticks
    input  wire        rst,
    input  wire        streaming,
    input  wire        xs_rise,                 // vsync edge strobe
    input  wire [23:0] xs_age,                  // ticks since the last vsync edge
    input  wire        xs_lost,                 // xs_age is saturated: no period
    input  wire        gl_live,                 // triggers come from vsync
    input  wire        gl_div,                  // one trigger per sequence
    input  wire [23:0] trig_per,                // free-running period, ticks
    input  wire [15:0] expo_cur,                // what the sensor was last told
    input  wire        trig0,                   // the trigger line itself
    output wire [15:0] expo_lim,                // longest legal exposure, units
    output wire        since_ok,                // the sensor can take a trigger
    output wire        trig_start               // 1-cycle strobe on trig0's rise
);
    localparam [23:0] EXP_GAP     = 24'd8000;   // 80 us, measured (usb_link GAP_TICKS)
    localparam [23:0] EXP_RESERVE = 24'd9000;   // + 10 us margin (usb_link RESERVE_TICKS)

    reg trig0_d = 1'b0;
    always @(posedge clk) trig0_d <= trig0;
    assign trig_start = trig0 & ~trig0_d;

    // Vsync period, measured edge to edge. xs_age counts from the last edge, so
    // its value AT an edge is the period; a saturated age means no period.
    reg [23:0] xs_per = 24'd0;
    always @(posedge clk)
        if (rst)          xs_per <= 24'd0;
        else if (xs_rise) xs_per <= xs_lost ? 24'd0 : xs_age + 24'd1;

    // ---- the limit, in exposure units (pipelined; it changes once a frame) ----
    wire        lim_gl  = gl_live && !gl_div;
    wire [23:0] lim_per = lim_gl ? xs_per : trig_per;
    reg         lim_ok_r = 1'b0, lim_ok_r2 = 1'b0;
    reg  [23:0] lim_use_r = 24'd0;
    reg  [43:0] lim_mul_r = 44'd0;
    always @(posedge clk) begin
        lim_ok_r  <= (lim_gl || !gl_live) && (lim_per != 24'd0);
        lim_use_r <= (lim_per > EXP_RESERVE) ? (lim_per - EXP_RESERVE) : 24'd0;
        // ticks -> 375 ns units is /37.5, done as x 27962 >> 20 (~1 ppm low, so
        // it rounds toward SAFE), exactly as usb_link does for MAXEXP.
        lim_mul_r <= lim_use_r * 44'd27962;
        lim_ok_r2 <= lim_ok_r;
    end
    wire [23:0] lim_full = lim_mul_r[43:20];
    assign expo_lim = !lim_ok_r2               ? 16'hFFFF
                    : (lim_full > 24'd65535)   ? 16'hFFFF
                    : (lim_full == 24'd0)      ? 16'd1
                    :                            lim_full[15:0];

    // ---- what the sensor may be integrating for, and the spacing it needs ----
    reg [15:0] expo_cur_d = EXPOSURE;
    reg [15:0] expo_prev  = EXPOSURE;           // the value it may still be using
    reg [1:0]  expo_lag   = 2'd0;               // triggers until expo_prev retires
    always @(posedge clk) begin
        expo_cur_d <= expo_cur;
        if (rst) begin
            expo_prev <= EXPOSURE;
            expo_lag  <= 2'd0;
        end else if (expo_cur != expo_cur_d) begin
            // Keep the LARGEST value still possibly in effect across back-to-back
            // changes, not merely the last one.
            expo_prev <= ((expo_lag != 2'd0) && (expo_prev > expo_cur_d))
                           ? expo_prev : expo_cur_d;
            expo_lag  <= 2'd3;
        end else if (trig_start && expo_lag != 2'd0) begin
            expo_lag  <= expo_lag - 2'd1;
        end
    end
    wire [15:0] expo_eff = ((expo_lag != 2'd0) && (expo_prev > expo_cur))
                             ? expo_prev : expo_cur;
    // EXTRA MARGIN IN A TRANSITION ONLY. The gap measured flat from 60 to 120 Hz,
    // but a transition -- an exposure in effect that the current period cannot
    // hold -- is exactly where the sensor would wedge, so there the guard adds
    // 1/256 of the exposure plus the 10 us margin. In steady state the clamp
    // already keeps 10 us of slack, the guard is never the tighter of the two,
    // and full rate is untouched.
    // `over` tolerates 8 units (3 us): genlocked, expo_lim follows the measured
    // vsync period and wobbles by a unit with jitter, so an exposure clamped at
    // the limit would otherwise read as over it on alternate frames and the
    // margin would skip triggers in perfectly legal running.
    //
    // PIPELINED, three stages. In one cycle -- mux, compare, multiply, add -- this
    // became the design's critical path at +0.059 ns. need_r guards spacings of
    // milliseconds, and an exposure write takes far longer than three clocks to
    // reach the sensor, so the latency costs nothing.
    reg  [15:0] eff_r  = EXPOSURE;
    reg         over_r = 1'b0, over_r2 = 1'b0;
    reg  [23:0] eff_ticks = 24'd0;
    reg  [23:0] need_r = 24'hFFFFFF;
    always @(posedge clk) begin
        eff_r     <= expo_eff;
        over_r    <= ({1'b0, expo_eff} > ({1'b0, expo_lim} + 17'd8));
        eff_ticks <= ({8'd0, eff_r} * 24'd75) >> 1;                 // units x 37.5
        over_r2   <= over_r;
        need_r    <= eff_ticks + EXP_GAP
                   + (over_r2 ? ((eff_ticks >> 8) + (EXP_RESERVE - EXP_GAP)) : 24'd0);
    end

    // Ticks since the trigger DECISION, saturating. trig0 rises two clocks after
    // the generator decides to fire, so the count restarts at 3 on that rise and
    // the next decision sees exactly the decision-to-decision time. Starts
    // saturated, so the first trigger is never held.
    reg [23:0] since_trig = 24'hFFFFFF;
    always @(posedge clk)
        if (rst || !streaming)               since_trig <= 24'hFFFFFF;
        else if (trig_start)                 since_trig <= 24'd3;
        else if (since_trig != 24'hFFFFFF)   since_trig <= since_trig + 24'd1;
    assign since_ok = (since_trig >= need_r);
endmodule
