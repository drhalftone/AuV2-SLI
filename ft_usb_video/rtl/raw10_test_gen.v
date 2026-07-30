`timescale 1ns/1ps
//==============================================================================
// raw10_test_gen.v -- packed 10-bit test-pattern source for FT601 bandwidth tests.
//
// PURPOSE: measure the Ft+ (FT601Q) sustained rate at the density the real
// sensor produces -- 10 bits per pixel, packed with NO padding -- and verify the
// bytes arrive intact. This is a link test, not a video test, so it deliberately
// does NOT synthesize SLI fringes: sli_frame_gen.v's 4096-entry cosine ROM costs
// ~800 LUTs and, worse, makes a WEAK signal-integrity pattern. Adjacent cosine
// samples differ by a few counts, so the high data bits hardly ever toggle --
// which is precisely why the ft_data[31:16] setup-window corruption (see
// ft601_sync_tx.v) stayed invisible for so long.
//
// Instead every pixel is simply its own index:  pixel(y,x) = (y*1280 + x) & 0x3FF
// That sweeps all 1024 codes continuously, so EVERY data bit toggles at its
// maximum rate, and the host can predict any pixel in closed form.
//
//------------------------------------------------------------------------------
// PACKING (format code 3) -- MIPI CSI-2 RAW10, the standard "packed 10-bit"
//------------------------------------------------------------------------------
// 4 pixels per 5 bytes: four bytes of bits[9:2], then one byte holding the four
// [1:0] pairs. 16 pixels = 20 bytes = EXACTLY 5 x 32-bit words, and 1280 divides
// by 16, so lines pack with zero waste and no bit straddles a line boundary.
//
//   bytes  0..3 = p0[9:2] p1[9:2] p2[9:2] p3[9:2]
//   byte      4 = {p3[1:0], p2[1:0], p1[1:0], p0[1:0]}     (p0 in the low bits)
//   bytes  5..9 = same for p4..p7,  10..14 for p8..p11,  15..19 for p12..p15
//
// The FT601 emits each 32-bit word little-endian (byte 0 = word[7:0]), so the
// host sees that byte order verbatim and unpacks 5 bytes -> 4 pixels.
//
// Chosen over contiguous LSB-first bit packing because it is byte-aligned every
// 4 pixels (trivial numpy unpack) and matches what a CSI-2 receiver delivers --
// see MIPI_CSI2_ROADMAP.md. Both layouts are 10 bits/px, so the MEASURED RATE IS
// IDENTICAL; only the unpacker differs.
//
//   frame = 32 B header + 1280*1024*10/8 = 32 + 1,638,400 = 1,638,432 B
//   at the measured 309 MB/s link ceiling  ->  ~188 fps
//
// HEADER (8 words, same framing contract as sli_frame_gen.v):
//   [0] MAGIC 0x30494C53 ("SLI0")   [4] packed bytes/frame  = 1,638,400
//   [1] frame_index                 [5] words/frame         =   409,600
//   [2] {height,width} = 0x04000500 [6] format code         = 3
//   [3] bits per pixel = 10         [7] ~MAGIC 0xCFB6B3AC
//
// STREAM CONTRACT: identical to sli_frame_gen.v -- FWFT, `word` always valid,
// `adv` consumes it. Paced entirely by FT601 back-pressure, so measured FPS is a
// clean link-rate number.
//==============================================================================
module raw10_test_gen #(
    parameter integer WIDTH  = 1280,
    parameter integer HEIGHT = 1024
)(
    input  wire        clk,
    input  wire        rst,                  // synchronous, active-high
    input  wire        adv,                  // consume `word`, advance to next
    output wire [31:0] word,                 // current 32-bit stream word (FWFT)
    output wire        valid,                // always 1
    output wire [31:0] frame_index
);
    localparam integer PPG    = 16;                   // pixels per group
    localparam integer WPG    = 5;                    // words per group
    localparam integer GPL    = WIDTH / PPG;          // 80 groups per line
    localparam integer HDR_N  = 8;
    localparam [31:0]  MAGIC  = 32'h30494C53;

    // WIDTH must be a multiple of 16 or a group would straddle a line end.
    initial if (WIDTH % PPG != 0) begin
        $display("raw10_test_gen: WIDTH %0d is not a multiple of %0d", WIDTH, PPG);
        $finish;
    end

    reg        in_hdr    = 1'b1;
    reg [2:0]  hdr_idx   = 3'd0;
    reg [2:0]  ph        = 3'd0;              // word within group, 0..4
    reg [6:0]  gx        = 7'd0;              // group within line, 0..79
    reg [10:0] wy        = 11'd0;             // line, 0..1023
    reg [9:0]  pbase     = 10'd0;             // pixel index at group start, mod 1024
    reg [31:0] frame_idx = 32'd0;

    assign valid       = 1'b1;
    assign frame_index = frame_idx;

    // The 16 pixel values of the current group. Only the low 10 bits of the
    // frame-wide index matter, and 1280 mod 1024 = 256, so a 10-bit counter
    // bumped by 16 per group tracks (y*WIDTH + x) & 0x3FF exactly.
    wire [9:0] v [0:15];
    genvar j;
    generate
        for (j = 0; j < 16; j = j + 1) begin : g_pix
            assign v[j] = pbase + j[9:0];
        end
    endgenerate

    // The four [1:0] pairs of each 4-pixel sub-group, p0 in the low bits.
    wire [7:0] lsb0 = {v[3][1:0],  v[2][1:0],  v[1][1:0],  v[0][1:0]};
    wire [7:0] lsb1 = {v[7][1:0],  v[6][1:0],  v[5][1:0],  v[4][1:0]};
    wire [7:0] lsb2 = {v[11][1:0], v[10][1:0], v[9][1:0],  v[8][1:0]};
    wire [7:0] lsb3 = {v[15][1:0], v[14][1:0], v[13][1:0], v[12][1:0]};

    // Words are {byte3, byte2, byte1, byte0} -- the FT601 sends byte0 first.
    reg [31:0] pix_word;
    always @(*) begin
        case (ph)
            3'd0: pix_word = {v[3][9:2],  v[2][9:2],  v[1][9:2],  v[0][9:2]};
            3'd1: pix_word = {v[6][9:2],  v[5][9:2],  v[4][9:2],  lsb0      };
            3'd2: pix_word = {v[9][9:2],  v[8][9:2],  lsb1,       v[7][9:2] };
            3'd3: pix_word = {v[12][9:2], lsb2,       v[11][9:2], v[10][9:2]};
            default: pix_word = {lsb3,    v[15][9:2], v[14][9:2], v[13][9:2]};
        endcase
    end

    reg [31:0] hdr_word;
    always @(*) begin
        case (hdr_idx)
            3'd0: hdr_word = MAGIC;
            3'd1: hdr_word = frame_idx;
            3'd2: hdr_word = {HEIGHT[15:0], WIDTH[15:0]};
            3'd3: hdr_word = 32'd10;                          // bits per pixel
            3'd4: hdr_word = (WIDTH * HEIGHT * 10) / 8;       // packed bytes/frame
            3'd5: hdr_word = (WIDTH / PPG) * WPG * HEIGHT;    // words/frame
            3'd6: hdr_word = 32'd3;                           // format 3 = RAW10
            default: hdr_word = ~MAGIC;
        endcase
    end

    assign word = in_hdr ? hdr_word : pix_word;

    always @(posedge clk) begin
        if (rst) begin
            in_hdr    <= 1'b1;
            hdr_idx   <= 3'd0;
            ph        <= 3'd0;
            gx        <= 7'd0;
            wy        <= 11'd0;
            pbase     <= 10'd0;
            frame_idx <= 32'd0;
        end else if (adv) begin
            if (in_hdr) begin
                if (hdr_idx == HDR_N-1) begin
                    in_hdr  <= 1'b0;
                    hdr_idx <= 3'd0;
                    ph      <= 3'd0;
                    gx      <= 7'd0;
                    wy      <= 11'd0;
                    pbase   <= 10'd0;
                end else begin
                    hdr_idx <= hdr_idx + 3'd1;
                end
            end else if (ph == WPG-1) begin
                ph    <= 3'd0;
                pbase <= pbase + 10'd16;
                if (gx == GPL-1) begin
                    gx <= 7'd0;
                    if (wy == HEIGHT-1) begin
                        // end of frame -> emit the next header. pbase is reset
                        // here too; this assignment is last, so it overrides the
                        // +16 above rather than racing it.
                        wy        <= 11'd0;
                        in_hdr    <= 1'b1;
                        hdr_idx   <= 3'd0;
                        frame_idx <= frame_idx + 32'd1;
                        pbase     <= 10'd0;
                    end else begin
                        wy <= wy + 11'd1;
                    end
                end else begin
                    gx <= gx + 7'd1;
                end
            end else begin
                ph <= ph + 3'd1;
            end
        end
    end
endmodule
