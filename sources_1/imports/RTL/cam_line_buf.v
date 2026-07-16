`timescale 1ns / 1ps
//=============================================================================
// cam_line_buf.v - one-line capture buffer for PYTHON 1300 bring-up.
//
// THE bring-up instrument (CAMERA_RTL_PLAN.md #11). Captures one image line from
// cam_sync_decode's kernel output into a BRAM, readable slowly over the 0xA5 UART
// (rdtbl TGT_CAM_LINE). This is how the receiver is proven against real silicon before any
// high-speed datapath exists: point the sensor at a known scene, grab a line, plot it.
//
// Why a LINE, not a frame: uart_ctrl's readback length is reg [11:0] (4095 bytes max) and
// the UART is ~11.5 kB/s. One 1280-pixel line at 8 bits is 1280 bytes -- fits, and reads in
// ~110 ms. A 1.3 MB frame does not fit and would take ~2 minutes. Frames need the Ft+.
//
// 8-bit truncation: stores pix[9:2] (the top 8 of 10). Enough to see a scene; the full
// 10-bit path is a streaming concern, not a bring-up one.
//
// WHICH line: the first IMAGE line of each frame (black-reference lines emit no kvalid, so
// they are skipped for free). Re-armed every frame_start, so a fresh read always reflects
// the latest frame's first line.
//
// The read port mirrors the TGT_EDID pattern exactly: the address is driven in from
// uart_ctrl and the data comes back REGISTERED one clock later, so uart_ctrl's S_RTAB
// streamer needs no special-casing. Read (rd_clk) and write (wordclk) are different domains;
// the BRAM is a simple dual-port and reads are only issued long after a frame is captured
// (the host asks for the line over a slow UART), so no CDC handshake is needed.
//=============================================================================
module cam_line_buf #(
    parameter integer DEPTH  = 1280,
    parameter integer ADDR_W = 11
)(
    // ---- write side: cam_sync_decode kernel output (wordclk domain) ----
    input  wire        wordclk,
    input  wire        frame_start,
    input  wire        line_start,
    input  wire        kvalid,
    input  wire [10:0] kbase,          // 0..1279 (11 bits) -- full-line column of kpix0
    input  wire [9:0]  kpix0, kpix1, kpix2, kpix3, kpix4, kpix5, kpix6, kpix7,

    // ---- read side: driven by uart_ctrl (rd_clk domain), TGT_EDID pattern ----
    input  wire              rd_clk,
    input  wire [ADDR_W-1:0] rd_addr,
    output reg  [7:0]        rd_data
);
    // The line is stored 8 pixels (one decode kernel) per 64-bit word. cam_sync_decode emits
    // kernels on 8-pixel boundaries (kbase = 0, 8, 16, ...), so all 8 pixels of a kernel land
    // in ONE word at kbase/8 -- a single write port per cycle. A per-pixel byte array (mem[8]
    // written at kbase+0..7 in one cycle) is correct in simulation but needs 8 write ports and
    // will NOT infer as a BRAM -- it crashed synthesis. Same capture behaviour, byte-exact.
    localparam integer WORDS = (DEPTH + 7) / 8;   // 160 words for DEPTH = 1280

    (* ram_style = "block" *) reg [63:0] mem [0:WORDS-1];

    // ---- capture the first image line of each frame ----
    reg capturing = 1'b0;
    reg done      = 1'b0;

    // 8-bit truncation (top 8 of 10), packed pixel s -> byte s of the word (little-endian).
    wire [63:0] kword = { kpix7[9:2], kpix6[9:2], kpix5[9:2], kpix4[9:2],
                          kpix3[9:2], kpix2[9:2], kpix1[9:2], kpix0[9:2] };
    wire [7:0]  kwaddr = kbase >> 3;              // kbase is 8-aligned (kcol += 8 per kernel)

    always @(posedge wordclk) begin
        if (frame_start) begin
            capturing <= 1'b0;
            done      <= 1'b0;
        end else begin
            // a new line boundary AFTER we started capturing ends the capture
            if (line_start && capturing) begin
                capturing <= 1'b0;
                done      <= 1'b1;
            end
            // kvalid only fires on image kernels, so the first one is in the first image
            // line; start capturing there and write its 8 pixels as one word at kbase/8.
            if (kvalid && !done) begin
                capturing <= 1'b1;
                if (kwaddr < WORDS) mem[kwaddr] <= kword;
            end
        end
    end

    // ---- registered read (1-cycle latency; TGT_EDID pattern) ----
    // Fetch the kernel word at rd_addr/8, select byte rd_addr[2:0], register the result --
    // identical latency and byte value to the old per-pixel mem[rd_addr].
    always @(posedge rd_clk)
        rd_data <= mem[rd_addr >> 3][rd_addr[2:0]*8 +: 8];

endmodule
