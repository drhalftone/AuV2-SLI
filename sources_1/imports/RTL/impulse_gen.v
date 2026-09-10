`timescale 1ns / 1ps
//==============================================================================
// impulse_gen.v -- the WHITE, K, K, K, K frame sequence for projector profiling.
//
// One frame of full white followed by four black frames, repeating. Drives the
// OFFLINE video output only; it does nothing in pass-through.
//
// WHY A FIVE-FRAME CYCLE AND NOT A STATIC FIELD. A repeating identical frame makes
// the projector's light output periodic with the frame period, and the projector's
// latency then aliases: half a frame behind is indistinguishable from half a frame
// ahead, or a frame and a half behind. Making one frame in five different gives the
// sequence a period of 5T and a unique origin, so the delay that lands the light in
// the camera's exposure window is unambiguous. See PROJECTOR_PROFILING_PLAN.md.
//
// THE PHASE OUTPUT IS THE POINT, NOT A DEBUG AID. The camera has to be triggered
// once per CYCLE and locked to the bright frame -- triggering every frame would
// average the white frame together with the four black ones and flatten the result.
// `phase0` is that trigger reference, and it exists because this FPGA generates both
// the pattern and the trigger, so it knows which frame is which. A separate
// projector and camera have no way to express that relationship at all.
//
// The counter advances on vsync so the sequence is locked to the emitted frame, not
// to a free-running timer that would drift against it.
//==============================================================================
module impulse_gen #(
    parameter integer CYCLE = 5           // 1 bright + (CYCLE-1) black
)(
    input  wire       pclk,               // pixel clock (offline output domain)
    input  wire       vsync_pos,          // active-high vsync, already polarity-corrected
    input  wire       en,                 // 0 = pass the incoming video through untouched
    input  wire [7:0] lvl,                // code driven on the bright frame (0x12)
    // Frames per sequence (0x11). 5 = BRIGHT,K,K,K,K; 2 = BRIGHT,K. A shorter cycle
    // sweeps far faster: the span to cover is cyc*T and the per-cycle trigger rate is
    // fps/cyc, so halving the cycle wins on both counts. Clamped to >= 2 -- a cycle of
    // 1 would be a permanently bright field with no dark reference at all.
    input  wire [2:0] cyc,
    // WHICH POSITION IN THE SEQUENCE IS BRIGHT. Default 0. Moving it is a measurement,
    // not a convenience: at delay 0 the ROI reads ~245 against a 158 floor, and that
    // elevation appears at the 4->0 boundary ONLY -- not at 2->3 or 3->4 -- so it is
    // tied to the bright frame rather than to blanking, stray light or a sensor offset,
    // all of which would show at every boundary. Putting the bright frame at position 1
    // moves the boundary that precedes it into the middle of the block, with a dark
    // frame reported either side. If the elevation follows the bright frame, it is the
    // projector; if it stays at position 0, it belongs to where the sequence starts.
    input  wire [2:0] bpos,
    // INVERT THE SEQUENCE: bright everywhere EXCEPT bpos, so KKRKK becomes RRKRR.
    //
    // This is a better instrument than the single flash, not merely its opposite.
    // A single bright frame measures the projector's emission by how far the
    // light spreads -- but at any usable level the peak RAILS the sensor, so the
    // shape between threshold and peak is lost and only the timing survives.
    //
    // A single DARK frame in a bright field measures the same edges from the
    // other side. The notch runs from the rail down towards the floor, so its
    // width and position are readable even when the surrounding field is fully
    // saturated: the information is in where the light STOPS, and saturation
    // does not blur that.
    input  wire       inv,

    output reg  [7:0] level,              // 8'hFF on the bright frame, else 8'h00
    output reg  [2:0] phase,              // 0 = the bright frame
    output reg        phase0              // level pulse: high for the whole bright frame
);
    reg vs_d = 1'b0;
    wire [2:0] cyc_eff = (cyc < 3'd2) ? CYCLE[2:0] : cyc;

    initial begin level = 8'h00; phase = 3'd0; phase0 = 1'b0; end

    always @(posedge pclk) begin
        vs_d <= vsync_pos;

        if (!en) begin
            // Held at the start of the cycle rather than left wherever it stopped,
            // so enabling it always begins on the bright frame and the host does not
            // have to guess the phase it is joining.
            phase  <= 3'd0;
            level  <= 8'h00;
            phase0 <= 1'b0;
        end else if (vsync_pos & ~vs_d) begin        // rising edge = new frame
            if (phase >= cyc_eff - 3'd1) phase <= 3'd0;
            else                         phase <= phase + 3'd1;
        end

        if (en) begin
            // XOR, so one comparator serves both senses.
            level  <= ((phase == bpos) ^ inv) ? lvl : 8'h00;
            // phase0 still marks the COMMANDED POSITION, not the bright one --
            // it names where in the sequence we are, and inverting which frames
            // are lit does not move that.
            phase0 <= (phase == bpos);
        end
    end
endmodule
