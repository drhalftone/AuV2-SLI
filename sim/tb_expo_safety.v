`timescale 1ns/1ps
//=============================================================================
// tb_expo_safety - cam_expo_safety must make a sensor wedge impossible.
//
// The module + a replica of cam_frame_ft's trigger generator and its decoder
// clamp / auto-shorten, against a sensor model that applies exposure0 ONE FRAME
// LATE and needs exposure + 78.8 us between triggers -- the worst edge measured
// on hardware 2026-10-07, flat from 60 to 120 Hz. Checked at EVERY trigger; the gate is
// "TOTAL VIOLATIONS: 0", plus "skipped 0" in scenario 6 (full rate at the limit).
//
//   1 free-run 120 Hz            5 display lost while genlocked at 15 ms
//   2 ask 8290 us at 120 Hz      6 genlocked 120 Hz at the limit, vsync jitter
//   3 genlock 60 Hz, 15 ms       7 opcode 2 shortens the period under it
//   4 sync off with 15 ms in effect
//
// Run (about a minute of wall time):
//   xvlog sim/tb_expo_safety.v LauPythonCamera_Pt_Stack/ddr/cam_expo_safety.v
//   xelab tb -s tbsim && xsim tbsim -R
//=============================================================================
module tb;
    reg clk = 0; always #5 clk = ~clk;              // 100 MHz
    reg rst = 1, streaming = 0;

    // ---- vsync source ----
    reg        vs_on = 0;
    reg [23:0] vs_per = 24'd1_666_667;               // 60 Hz
    reg [23:0] vs_jit = 0;                            // +/- applied alternately
    reg [23:0] vs_cnt = 0;
    reg        xs_rise = 0;
    reg        jit_ph = 0;
    always @(posedge clk) begin
        xs_rise <= 0;
        if (vs_on) begin
            if (vs_cnt >= (jit_ph ? vs_per + vs_jit : vs_per - vs_jit) - 1) begin
                vs_cnt <= 0; xs_rise <= 1; jit_ph <= ~jit_ph;
            end else vs_cnt <= vs_cnt + 1;
        end
    end

    // ---- genlock state (replica, delay 0, no FIFO needed for delay 0) ----
    localparam [23:0] XS_TIMEOUT = 24'd10_000_000;
    reg        gl_en = 0;
    reg [23:0] xs_age = XS_TIMEOUT;
    always @(posedge clk)
        if (xs_rise) xs_age <= 0;
        else if (xs_age != XS_TIMEOUT) xs_age <= xs_age + 1;
    wire gl_live = gl_en && (xs_age != XS_TIMEOUT);
    reg  gl_fire = 0;
    always @(posedge clk) gl_fire <= xs_rise && gl_live;

    // ---- DUT ----
    reg  [23:0] trig_per = 24'd833_333;               // 120 Hz
    reg  [15:0] expo_cur = 16'h0640;
    reg  [23:0] tcnt = 0;
    reg         trig0 = 0;
    wire [15:0] expo_lim;
    wire        since_ok, trig_start;
    cam_expo_safety #(.EXPOSURE(16'h0640)) dut (
        .clk(clk), .rst(rst), .streaming(streaming),
        .xs_rise(xs_rise), .xs_age(xs_age), .xs_lost(xs_age == XS_TIMEOUT),
        .gl_live(gl_live), .gl_div(1'b0), .trig_per(trig_per),
        .expo_cur(expo_cur), .trig0(trig0),
        .expo_lim(expo_lim), .since_ok(since_ok), .trig_start(trig_start));

    // ---- trigger generator: replica of cam_frame_ft ----
    localparam [23:0] HI = 24'd1000;
    integer skipped = 0;
    always @(posedge clk) begin
        if (rst || !streaming) begin
            tcnt <= 0; trig0 <= 0;
        end else if (gl_live) begin
            if (gl_fire && since_ok) tcnt <= 0;
            else begin
                if (gl_fire) skipped = skipped + 1;
                if (tcnt != 24'hFFFFFF) tcnt <= tcnt + 1;
            end
            trig0 <= (tcnt < HI);
        end else begin
            if (tcnt >= trig_per - 1) begin
                if (since_ok) tcnt <= 0;
            end else tcnt <= tcnt + 1;
            trig0 <= (tcnt < HI);
        end
    end

    // ---- decoder replica: clamp + auto-shorten ----
    reg        cmd = 0; reg [15:0] cmd_v = 0;
    integer    nshort = 0;
    always @(posedge clk) begin
        if (cmd) expo_cur <= (cmd_v > expo_lim) ? expo_lim : cmd_v;
        else if (streaming && trig_start && expo_cur > expo_lim) begin
            expo_cur <= expo_lim; nshort = nshort + 1;
        end
    end
    task send_expo(input [15:0] v); begin
        @(posedge clk) begin cmd <= 1; cmd_v <= v; end
        @(posedge clk) cmd <= 0;
    end endtask

    // ---- sensor model: exposure latched at a trigger takes effect NEXT trigger ----
    reg [15:0] s_pending = 16'h0640, s_inuse = 16'h0640;
    integer t_last = -1, ntrig = 0, nviol = 0, minslack = 1<<30, t_now = 0;
    integer last_int = 0;
    always @(posedge clk) begin
        t_now = t_now + 1;
        if (trig_start) begin
            if (t_last >= 0) begin
                // the frame triggered at t_last integrated for s_inuse(prev)
                if ((t_now - t_last) < ((s_inuse * 75) / 2 + 7880)) begin
                    nviol = nviol + 1;
                    $display("  VIOLATION t=%0d interval=%0d need=%0d (expo %0d)",
                             t_now, t_now - t_last, ((s_inuse * 75) / 2 + 7880), s_inuse);
                end
                if ((t_now - t_last) - (((s_inuse * 75) / 2 + 7880)) < minslack)
                    minslack = (t_now - t_last) - (((s_inuse * 75) / 2 + 7880));
                last_int = t_now - t_last;
            end
            // one frame late: this trigger uses what was pending at the PREVIOUS one
            s_inuse   = s_pending;
            s_pending = expo_cur;
            t_last = t_now; ntrig = ntrig + 1;
        end
    end

    task report(input [8*40-1:0] name); begin
        $display("%0s: triggers %0d  violations %0d  min slack %0d ticks  last interval %0d  skipped %0d  auto-shorten %0d  expo_cur %0d  expo_lim %0d",
                 name, ntrig, nviol, minslack, last_int, skipped, nshort, expo_cur, expo_lim);
        ntrig = 0; minslack = 1<<30; skipped = 0; nshort = 0;
    end endtask

    integer total_viol = 0;
    initial begin
        repeat (10) @(posedge clk); rst = 0;
        repeat (10) @(posedge clk); streaming = 1;

        // 1. free-running 120 Hz at 600 us: full rate, lim = 8243 us
        repeat (8 * 833_333) @(posedge clk);
        report("1 free-run 120Hz 600us      ");
        if (expo_lim != 16'd21982) $display("  expo_lim %0d, expected 21982 (8243 us)", expo_lim);

        // 2. the original fault: ask for 8290 us at 120 Hz -> clamped, full rate
        send_expo(16'd22107);
        repeat (8 * 833_333) @(posedge clk);
        report("2 ask 8290us @120Hz (clamp) ");

        // 3. genlock to 60 Hz, then a legal 15 ms exposure
        vs_on = 1; gl_en = 1;
        repeat (3 * 1_666_667) @(posedge clk);
        send_expo(16'd40000);                          // 15 ms
        repeat (8 * 1_666_667) @(posedge clk);
        report("3 genlock 60Hz 15ms         ");

        // 4. untick sync with 15 ms in effect -> must not wedge
        gl_en = 0;
        repeat (12 * 833_333) @(posedge clk);
        report("4 sync off w/ 15ms in flight");

        // 5. display unplugged while genlocked at 15 ms (no host involvement)
        gl_en = 1; repeat (4 * 1_666_667) @(posedge clk);
        send_expo(16'd40000); repeat (6 * 1_666_667) @(posedge clk);
        ntrig = 0;
        vs_on = 0;                                       // vsync stops
        repeat (20 * 833_333) @(posedge clk);
        report("5 display lost at 15ms      ");

        // 6. genlocked 120 Hz at the exact limit, vsync jitter +/- 20 ticks: no skips
        vs_per = 24'd833_333; vs_jit = 24'd20; vs_on = 1; gl_en = 1;
        repeat (4 * 833_333) @(posedge clk);
        send_expo(16'hFFFF);                             // clamps to the limit
        repeat (40 * 833_333) @(posedge clk);
        report("6 genlock 120Hz at lim, jit ");

        // 7. opcode 2 shortens the free-running period under the exposure
        gl_en = 0; vs_on = 0; repeat (3 * 833_333) @(posedge clk);
        send_expo(16'hFFFF); repeat (4 * 833_333) @(posedge clk);
        trig_per = 24'd500_000;                          // 200 Hz
        repeat (20 * 500_000) @(posedge clk);
        report("7 trig_per 120->200Hz       ");

        $display("TOTAL VIOLATIONS: %0d", nviol);
        $finish;
    end
endmodule
