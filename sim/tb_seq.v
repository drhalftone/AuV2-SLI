`timescale 1ns/1ps
// tb_seq.v -- pixel_pipe's FPGA-run fringe sequence: free-run, order table, window.
//
//   cd sim && xvlog ../sources_1/imports/RTL/pixel_pipe.v ../sources_1/imports/RTL/pattern_gen.v tb_seq.v
//          && xelab tb_seq -s tb_seq && xsim tb_seq -R
//
// A tiny 40x30 raster. Checks, per frame:
//   - free-run advances one slot per frame with no rdy edges,
//   - the tag (first active pixel) is {frq, order[slot]},
//   - with the window on, every active pixel outside it is black (except the tag),
//     and pixels inside it are the fringe.
module tb_seq;
    localparam HA = 40, HT = 60, VA = 30, VT = 36;
    reg clk = 0;
    always #5 clk = ~clk;

    reg [11:0] hc = 0, vc = 0;
    wire active = (hc < HA) && (vc < VA);
    wire hs = (hc >= HA + 5) && (hc < HA + 10);
    wire vs = (vc >= VA + 2) && (vc < VA + 4);
    always @(posedge clk) begin
        hc <= (hc == HT - 1) ? 12'd0 : hc + 12'd1;
        if (hc == HT - 1) vc <= (vc == VT - 1) ? 12'd0 : vc + 12'd1;
    end

    reg  [63:0] seq_cfg;
    wire [7:0]  o_r, o_g, o_b, lut;
    wire        o_bl, o_hs, o_vs;
    pixel_pipe dut (
        .clk(clk), .clk10(1'b0), .sw(4'b1110),
        .in_red(8'd0), .in_green(8'd0), .in_blue(8'd0),
        .in_blank(~active), .in_vsync(vs), .in_hsync(hs), .vsync(vs),
        .trig(), .f_frm(), .mode(1'b1), .rdy(1'b0), .frq_hold(1'b0),
        .seq_cfg(seq_cfg), .vid_valid(1'b1),
        .out_red(o_r), .out_green(o_g), .out_blue(o_b),
        .out_hsync(o_hs), .out_vsync(o_vs), .out_blank(o_bl),
        .tlp_dbg(), .tlp_tog(), .olp_dbg(), .trig_cnt_dbg(),
        .lut_din(lut), .lut_dout(lut));

    // output-side raster position, from the pipe's own blank
    reg [11:0] ox = 0, oy = 0;
    reg ob_q = 1, ov_q = 0;
    reg [7:0] tag;
    reg       got_tag;
    integer   outside_lit, inside_lit, inside_px, frame;
    always @(posedge clk) begin
        ob_q <= o_bl; ov_q <= o_vs;
        if (o_bl) ox <= 0; else ox <= ox + 1;
        if (o_vs && !ov_q) oy <= 0;
        else if (o_bl && !ob_q) oy <= oy + 1;
        if (!o_bl) begin
            if (!got_tag) begin tag <= o_r; got_tag <= 1; end
            else if (ox >= 8 && ox < 20 && oy >= 4 && oy < 16) begin
                inside_px = inside_px + 1;
                if (o_r != 0) inside_lit = inside_lit + 1;
            end else if (o_r != 0) outside_lit = outside_lit + 1;
        end
        if (o_vs && !ov_q) begin
            if (frame > 0)
                $display("frame %2d  win=%0d free=%0d  tag=%2d (frq %0d phase %0d)  inside lit %3d/%3d  outside lit %4d",
                         frame, seq_cfg[1], seq_cfg[0], tag, tag[4:3], tag[2:0],
                         inside_lit, inside_px, outside_lit);
            frame = frame + 1;
            got_tag <= 0; outside_lit = 0; inside_lit = 0; inside_px = 0;
        end
    end

    initial begin
        frame = 0; got_tag = 0; outside_lit = 0; inside_lit = 0; inside_px = 0;
        // free-run on, window off, identity order, window (8,4)-(20,16) px
        seq_cfg = {8'd4, 8'd1, 8'd5, 8'd2, 24'hFAC688, 8'h01};
        repeat (12 * VT * HT) @(posedge clk);
        // Bayer order, window on
        seq_cfg = {8'd4, 8'd1, 8'd5, 8'd2, 24'hEE9CA0, 8'h03};
        repeat (12 * VT * HT) @(posedge clk);
        // free-run off: must hold still with no rdy
        seq_cfg = {8'd4, 8'd1, 8'd5, 8'd2, 24'hEE9CA0, 8'h02};
        repeat (4 * VT * HT) @(posedge clk);
        $finish;
    end
endmodule
