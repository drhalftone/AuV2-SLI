`timescale 1ns / 1ps
//============================================================================
// led_idle_anim.v -- "sign of life" idle animation muxed over the status LEDs.
//
// Shows the real status byte while something is actually CONNECTED, and a single
// LED sliding led0 -> led7 when the board is idle (no project, no incoming HDMI,
// no display).
//
// IMPORTANT: idle is gated on a real CONNECTION, not on "are frames happening."
// With nothing attached the offline pattern generator still free-runs at ~75 fps,
// so a frame-rate detector flips to the status LEDs every time the clock selector
// hunts between offline and (false) passthrough -- the flicker we observed over
// the USB telemetry. The honest "we are connected / working" signal is:
//
//     connected = vid_valid  OR  monitor_present
//       vid_valid       = real HDMI input decoding (symbol_sync AND pll_locked)
//       monitor_present = output display's HPD/EDID (edid_merge dbg2[2])
//
// Both are 0 when nothing is plugged in (regardless of the offline free-run), so
// the slider stays rock-steady; either one going high pins the LEDs to status.
// A short debounce keeps a momentary glitch on `connected` from flipping the mux.
//
//   slider step = 2^DIVBITS / 100 MHz   (DIVBITS=24 -> ~168 ms/step, ~6 Hz)
//   debounce    = 2^DBBITS  / 100 MHz   (DBBITS=23  -> ~84 ms steady before a flip)
//
// `connected` is built from quasi-static / other-domain bits; a 3-FF synchronizer
// plus the debounce makes the crossing safe (already cut by the design's
// set_clock_groups -asynchronous).
//============================================================================
module led_idle_anim #(
    parameter integer DIVBITS = 24,
    parameter integer DBBITS  = 23
)(
    input  wire       clk100,
    input  wire       connected,    // 1 = HDMI input decoding OR output monitor present
    input  wire [7:0] status_led,   // normal status byte (led_i: 7=vsync..0=trig)
    output wire [7:0] led_out
);
    // ---- CDC: synchronize `connected` into clk100 ----
    reg c0 = 1'b0, c1 = 1'b0, c2 = 1'b0;
    always @(posedge clk100) begin
        c0 <= connected; c1 <= c0; c2 <= c1;
    end

    // ---- debounce: commit `present` only after c2 holds the opposite state ----
    reg              present = 1'b0;          // 0 = idle (slider), 1 = connected (status)
    reg [DBBITS:0]   db      = {(DBBITS+1){1'b0}};
    always @(posedge clk100) begin
        if (c2 == present)        db <= {(DBBITS+1){1'b0}};   // already matches -> reset
        else if (!db[DBBITS])     db <= db + 1'b1;            // differs -> time it
        else begin present <= c2; db <= {(DBBITS+1){1'b0}}; end  // held -> commit flip
    end

    // ---- free-running slider ----
    reg [DIVBITS-1:0] div = {DIVBITS{1'b0}};
    reg [2:0]         pos = 3'd0;
    always @(posedge clk100) begin
        div <= div + 1'b1;
        if (div == {DIVBITS{1'b0}}) pos <= pos + 3'd1;
    end
    wire [7:0] slide = 8'b0000_0001 << pos;

    assign led_out = present ? status_led : slide;
endmodule
