`timescale 1ns / 1ps
//=============================================================================
// cam_align.v - per-lane training / bitslip alignment for the PYTHON 1300 receiver.
//
// The iocheck stub tied BITSLIP to 0, so it had NO word alignment. This is the FSM that
// does it. Each of the 5 channels (4 data + sync) is aligned INDEPENDENTLY: during idle
// the sensor sends the training pattern 0x3A6 on every lane (data lanes: reg 116 default;
// sync lane: the TR code, also 0x3A6), so every lane trains on the same word.
//
// PER LANE: watch the deserialised word. If it already equals TRAIN for a few consecutive
// word clocks, that lane is locked. Otherwise pulse BITSLIP once, wait for the ISERDES to
// settle (a bitslip takes effect after a couple of CLKDIV cycles), and re-check. A 10-bit
// word has only 10 rotations, so if TRAIN has not appeared within a bounded number of
// slips the lane is declared FAILED rather than left silently emitting garbage.
//
// Only the WORD BOUNDARY is unknown, and bitslip is exactly the control for it: inter-pair
// skew on this board is picoseconds (all 7 pairs routed with zero intra-pair skew) and
// sub-bit, so IDELAYE2 deskew is NOT used here -- bitslip alone resolves the 10 rotations.
// If a real board ever shows a lane that cannot find a clean rotation, revisit with
// IDELAYE2 + IDELAYCTRL (200 MHz ref already exists for the HDMI IDELAYCTRL); see the note
// at the bottom. For now: bitslip only, and the alignment is proven in sim (task #9 TB) to
// lock from EVERY starting rotation.
//=============================================================================
module cam_align #(
    parameter [9:0] TRAIN     = 10'h3A6,   // datasheet reg 116 default / TR code
    parameter integer LOCK_CNT = 8,        // consecutive TRAIN words needed to declare lock
    parameter integer SETTLE   = 4,        // wordclks to wait after a bitslip pulse
    parameter integer MAX_SLIP = 20        // give up after this many slips (>= 10 rotations)
)(
    input  wire        wordclk,
    input  wire        rst,

    input  wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word,

    output wire [4:0]  bitslip,      // to cam_lvds_rx, {sync, d3, d2, d1, d0}
    output wire [4:0]  lane_locked,  // per-lane lock status
    output wire        aligned,      // all 5 locked
    output wire [4:0]  lane_failed   // per-lane: exhausted MAX_SLIP without locking
);
    // one aligner instance per lane, so the 5 run fully independently
    genvar i;
    wire [9:0] lw [0:4];
    assign lw[0] = d0_word;  assign lw[1] = d1_word;  assign lw[2] = d2_word;
    assign lw[3] = d3_word;  assign lw[4] = sync_word;

    generate
        for (i = 0; i < 5; i = i + 1) begin : g_lane
            reg        slip;
            reg        locked;
            reg        failed;
            reg [3:0]  good;        // consecutive TRAIN matches
            reg [3:0]  settle;      // countdown after a slip
            reg [7:0]  slips;       // total slips this lane

            localparam [1:0] A_CHECK = 2'd0, A_SLIP = 2'd1, A_WAIT = 2'd2, A_DONE = 2'd3;
            reg [1:0] st;

            always @(posedge wordclk) begin
                if (rst) begin
                    slip <= 1'b0; locked <= 1'b0; failed <= 1'b0;
                    good <= 4'd0; settle <= 4'd0; slips <= 8'd0; st <= A_CHECK;
                end else begin
                    slip <= 1'b0;                       // default: no slip pulse
                    case (st)
                        A_CHECK: begin
                            if (lw[i] == TRAIN) begin
                                if (good == LOCK_CNT[3:0] - 1) begin
                                    locked <= 1'b1; st <= A_DONE;
                                end else begin
                                    good <= good + 4'd1;
                                end
                            end else begin
                                // wrong rotation -- slip once, unless we are out of tries
                                good <= 4'd0;
                                if (slips >= MAX_SLIP[7:0]) begin
                                    failed <= 1'b1; st <= A_DONE;
                                end else begin
                                    slip   <= 1'b1;
                                    slips  <= slips + 8'd1;
                                    settle <= SETTLE[3:0];
                                    st     <= A_WAIT;
                                end
                            end
                        end
                        A_WAIT: begin
                            // let the bitslip take effect before believing the word again
                            if (settle == 0) st <= A_CHECK;
                            else settle <= settle - 4'd1;
                        end
                        // Once locked, HOLD. Re-alignment on link loss (re-arming if the
                        // word drifts off TRAIN during idle) is a hardware-bring-up refinement,
                        // not needed for the sim gate.
                        A_DONE: locked <= locked;
                        default: st <= A_CHECK;
                    endcase
                end
            end

            assign bitslip[i]     = slip;
            assign lane_locked[i] = locked;
            assign lane_failed[i] = failed;
        end
    endgenerate

    assign aligned = &lane_locked;

    // ---- IDELAYE2 note (deliberately not used) ------------------------------------------
    // If a fabbed board shows a lane that bitslip cannot settle (word oscillates between two
    // rotations, or Tccsk channel-to-channel skew of up to 50 ps lands the sample on a data
    // edge), add an IDELAYE2 per lane before the ISERDES and sweep the tap to centre the eye,
    // driven by an IDELAYCTRL on the existing 200 MHz ref (ref_clk CLKOUT0 -> clk200, already
    // feeding the HDMI-input IDELAYCTRL). That is a per-lane analog-ish deskew ON TOP of the
    // digital word alignment here -- not a replacement for it.
endmodule
