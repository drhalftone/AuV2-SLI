`timescale 1ns/1ps
//==============================================================================
// ft601_sync_tx.v -- FT601Q "245 Synchronous FIFO" WRITE master (FPGA -> host).
//
// The Alchitry Ft+ carries an FTDI FT601Q USB-3 bridge. In 245-Synchronous-FIFO
// mode the FT601 is the CLOCK SOURCE (ft_clk, 100 MHz) and the FPGA is the FIFO
// MASTER. This module implements the WRITE (transmit) side only -- the direction
// that matters for streaming camera/video data OUT to the PC.
//
// Protocol (write path), all signals active-LOW, all sampled on posedge ft_clk:
//   * TXE#  (ft_txe)  -- FT601 output. LOW => the USB TX buffer has room; the
//                        master may present a word this cycle.
//   * WR#   (ft_wr)   -- master output. A 32-bit word on DATA/BE is accepted by
//                        the FT601 on every rising edge where WR#==0 AND TXE#==0.
//   * OE#   (ft_oe)   -- master output. Held HIGH: the FT601 never drives the bus
//                        (we do TX only), so the FPGA owns DATA/BE outright.
//   * RD#   (ft_rd)   -- master output. Held HIGH: we never read.
//   * BE    (ft_be)   -- byte-enable. All four bytes valid => 0xF on every write.
//
// The correctness trick: ft_wr is driven purely combinationally from ft_txe
// (ft_wr = ~ready, ready = ~ft_txe & s_valid). Because the FT samples WR# and TXE#
// on the SAME edge, tying them together guarantees the accept condition can never
// disagree between the two chips -- a word is taken exactly when TXE# was low, and
// the source is advanced by the identical term (s_adv = ready). No skid buffer,
// no dropped/duplicated words. DATA is the source's current word, so the source
// must present first-word-fall-through (FWFT) data: s_word is valid whenever
// s_valid is high, and s_adv=1 means "that word was consumed, present the next."
//
// This is a pad-to-pad path (ft_txe pad -> 1 LUT -> ft_wr pad). At 100 MHz on the
// Artix-7 it closes comfortably; the top-level XDC adds the FT601 set_input_delay
// / set_output_delay so the tool checks it against the datasheet window.
//==============================================================================
module ft601_sync_tx (
    input  wire        clk,          // = ft_clk (100 MHz, from the FT601)

    // ---- FT601 245-sync-FIFO bus (active-low controls) ----
    input  wire        ft_txe,       // TXE#  : low = room to write
    output wire        ft_wr,        // WR#   : low = write this cycle
    output wire        ft_oe,        // OE#   : held high (TX only)
    output wire        ft_rd,        // RD#   : held high (TX only)
    output wire [31:0] ft_dout,      // data to the FT601 (top tristates onto the bus)
    output wire        bus_oe,       // 1 => FPGA drives DATA/BE (top-level tristates)

    // ---- stream source (FWFT: s_word valid while s_valid; s_adv pops it) ----
    input  wire [31:0] s_word,
    input  wire        s_valid,
    output wire        s_adv
);
    // TX only: we always own the bus, never let the FT601 drive it, never read.
    // The shared bus (DATA + BE) is tristated at the top level from bus_oe; BE is a
    // constant 0xF (all bytes valid) so it need not come through this module.
    assign ft_oe  = 1'b1;
    assign ft_rd  = 1'b1;
    assign bus_oe = 1'b1;
    assign ft_dout = s_word;

    // Accept a word this cycle iff the FT has room AND the source has data.
    wire ready = ~ft_txe & s_valid;
    assign ft_wr = ~ready;   // WR# low exactly when we transfer
    assign s_adv =  ready;   // and the source advances by the same term
endmodule
