`timescale 1ns/1ps
//=============================================================================
// cam_eye_scan - sweep the IDELAY taps, measure each lane's eye, and park every
// lane at the centre of its own widest passing window.
//
// THE IDEA. At each of the 32 delay taps, ask every lane: "are you decoding the
// training pattern right now?" Sweep the whole range and each lane ends up with
// a 32-bit mask of which taps work. The widest run of consecutive 1s in that
// mask IS the data eye, measured in taps; its centre is where the ISERDES should
// sample. Per lane, independently, because each pair has its own flight time.
//
// This also produces the number nobody has had so far: eye WIDTH in taps. At
// ~78 ps per tap (200 MHz REFCLK), a lane passing over 14 taps has roughly
// 1.1 ns of margin; a lane passing over 2 taps is one temperature change from
// failing. "Locked" is a boolean; this is a measurement.
//
//-----------------------------------------------------------------------------
// WHY THE PASS TEST IS "ANY ROTATION", NOT "== 0x3A6"
//
// Word alignment (bitslip) and sampling phase (IDELAY) are independent unknowns,
// and trying to solve both at once makes a mess: at a perfectly good tap the
// word can still be a ROTATION of the training pattern, because the word
// boundary has not been found yet, and a naive "== 0x3A6" test would score that
// tap as failing.
//
// So the scan accepts ANY of the ten rotations of 0x3A6. That is exactly the
// question "did these ten bits arrive intact", independent of where the word
// boundary sits. Bitslip is then left to cam_align AFTERWARDS, once the taps are
// already centred -- at which point it is aligning clean data instead of
// hunting through a marginal eye.
//
// The ten rotations are precomputed below. Note 0x1BA among them: that is the
// value the bench was reading on d0 (as 0x1B8, one bit short) -- a real rotation
// of the training word with its isolated bit lost.
//=============================================================================
module cam_eye_scan #(
    parameter integer N_TAPS  = 32,
    parameter integer SETTLE  = 64,    // wordclks after a tap load
    parameter integer SAMPLES = 256    // consecutive good words required
)(
    input  wire        wordclk,
    input  wire        rst,

    input  wire [9:0]  d0_word, d1_word, d2_word, d3_word, sync_word,

    output reg  [24:0] tap_val,        // {sync, d3, d2, d1, d0}, 5 bits each
    output reg         tap_ld,
    output reg         scan_done,
    output reg         align_rst,      // hold cam_align off until taps are set

    // per-lane results, valid once scan_done
    output reg  [4:0]  best_tap0, best_tap1, best_tap2, best_tap3, best_taps,
    output reg  [5:0]  best_len0, best_len1, best_len2, best_len3, best_lens
);
    // The ten rotations of the training word 0x3A6 = 11 1010 0110.
    function is_train(input [9:0] w);
        is_train = (w == 10'h3A6) || (w == 10'h34D) || (w == 10'h29B) ||
                   (w == 10'h137) || (w == 10'h26E) || (w == 10'h0DD) ||
                   (w == 10'h1BA) || (w == 10'h374) || (w == 10'h2E9) ||
                   (w == 10'h1D3);
    endfunction

    wire [4:0] good_now = { is_train(sync_word), is_train(d3_word),
                            is_train(d2_word),   is_train(d1_word),
                            is_train(d0_word) };

    reg [31:0] mask [0:4];             // per lane: which taps decoded cleanly
    reg [4:0]  tap;
    reg [15:0] cnt;
    reg [4:0]  ok;                     // lanes still clean at this tap

    localparam [2:0] S_INIT = 3'd0, S_LOAD = 3'd1, S_SETTLE = 3'd2,
                     S_MEAS = 3'd3, S_NEXT = 3'd4,
                     S_ANA  = 3'd5, S_APPLY = 3'd6, S_DONE = 3'd7;
    reg [2:0] st;

    // analysis scratch
    reg [2:0] al;                      // lane under analysis
    reg [5:0] at;                      // tap position under analysis
    reg [5:0] cur_len, bst_len;
    reg [5:0] cur_start, bst_start;

    integer k;

    always @(posedge wordclk) begin
        tap_ld <= 1'b0;

        if (rst) begin
            st        <= S_INIT;
            tap       <= 5'd0;
            cnt       <= 16'd0;
            ok        <= 5'b11111;
            tap_val   <= 25'd0;
            scan_done <= 1'b0;
            align_rst <= 1'b1;
            al        <= 3'd0;
            at        <= 6'd0;
            for (k = 0; k < 5; k = k + 1) mask[k] <= 32'd0;
            best_tap0 <= 5'd0; best_tap1 <= 5'd0; best_tap2 <= 5'd0;
            best_tap3 <= 5'd0; best_taps <= 5'd0;
            best_len0 <= 6'd0; best_len1 <= 6'd0; best_len2 <= 6'd0;
            best_len3 <= 6'd0; best_lens <= 6'd0;
        end else begin
            case (st)

            S_INIT: begin
                align_rst <= 1'b1;      // cam_align stays off for the whole scan
                tap       <= 5'd0;
                st        <= S_LOAD;
            end

            // Put every lane at the same tap for this pass of the sweep.
            S_LOAD: begin
                tap_val <= {5{tap}};
                tap_ld  <= 1'b1;
                cnt     <= 16'd0;
                st      <= S_SETTLE;
            end

            S_SETTLE: if (cnt == SETTLE[15:0] - 16'd1) begin
                          cnt <= 16'd0;
                          ok  <= 5'b11111;
                          st  <= S_MEAS;
                      end else cnt <= cnt + 16'd1;

            // A lane keeps its bit only if EVERY sample in the window decodes.
            // One bad word in 256 disqualifies the tap -- which is the point,
            // since the failure we are chasing is intermittent.
            S_MEAS: begin
                ok <= ok & good_now;
                if (cnt == SAMPLES[15:0] - 16'd1) begin
                    cnt <= 16'd0;
                    st  <= S_NEXT;
                end else cnt <= cnt + 16'd1;
            end

            S_NEXT: begin
                for (k = 0; k < 5; k = k + 1)
                    mask[k][tap] <= ok[k];
                if (tap == N_TAPS[4:0] - 5'd1) begin
                    al  <= 3'd0;
                    at  <= 6'd0;
                    cur_len <= 6'd0; bst_len <= 6'd0;
                    cur_start <= 6'd0; bst_start <= 6'd0;
                    st  <= S_ANA;
                end else begin
                    tap <= tap + 5'd1;
                    st  <= S_LOAD;
                end
            end

            // Longest run of consecutive passing taps, one lane at a time.
            S_ANA: begin
                if (at == N_TAPS[5:0]) begin
                    // end of this lane: close out any run still open, then store
                    case (al)
                    3'd0: begin best_len0 <= bst_len;
                                best_tap0 <= bst_start[4:0] + bst_len[4:1]; end
                    3'd1: begin best_len1 <= bst_len;
                                best_tap1 <= bst_start[4:0] + bst_len[4:1]; end
                    3'd2: begin best_len2 <= bst_len;
                                best_tap2 <= bst_start[4:0] + bst_len[4:1]; end
                    3'd3: begin best_len3 <= bst_len;
                                best_tap3 <= bst_start[4:0] + bst_len[4:1]; end
                    3'd4: begin best_lens <= bst_len;
                                best_taps <= bst_start[4:0] + bst_len[4:1]; end
                    endcase
                    if (al == 3'd4) st <= S_APPLY;
                    else begin
                        al        <= al + 3'd1;
                        at        <= 6'd0;
                        cur_len   <= 6'd0; bst_len   <= 6'd0;
                        cur_start <= 6'd0; bst_start <= 6'd0;
                    end
                end else begin
                    if (mask[al][at[4:0]]) begin
                        if (cur_len == 6'd0) cur_start <= at;
                        cur_len <= cur_len + 6'd1;
                        if (cur_len + 6'd1 > bst_len) begin
                            bst_len   <= cur_len + 6'd1;
                            bst_start <= (cur_len == 6'd0) ? at : cur_start;
                        end
                    end else begin
                        cur_len <= 6'd0;
                    end
                    at <= at + 6'd1;
                end
            end

            // Park each lane at the centre of its own eye, then let cam_align
            // find the word boundary on data that is now sampled cleanly.
            S_APPLY: begin
                tap_val <= {best_taps, best_tap3, best_tap2, best_tap1, best_tap0};
                tap_ld  <= 1'b1;
                cnt     <= 16'd0;
                st      <= S_DONE;
            end

            S_DONE: begin
                if (cnt == SETTLE[15:0] - 16'd1) begin
                    align_rst <= 1'b0;      // release the aligner
                    scan_done <= 1'b1;
                end else cnt <= cnt + 16'd1;
            end

            default: st <= S_INIT;
            endcase
        end
    end
endmodule
