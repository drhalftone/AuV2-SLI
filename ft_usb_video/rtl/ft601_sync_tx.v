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
//------------------------------------------------------------------------------
// WHY THE OUTPUTS ARE REGISTERED (2026-07-30 -- this was a real, measured bug)
//------------------------------------------------------------------------------
// The first version drove the bus combinationally: `assign ft_dout = s_word`,
// straight from the generator's cosine ROM + phase adders to the pads. It moved
// data at full rate and the host saw zero dropped frames -- but a byte-exact
// check against the RTL model (host/ft_video_snap.py) showed:
//
//     errors by byte lane (x mod 4):  {0: 0, 1: 0, 2: 11419, 3: 25120}
//
// ft_data[15:0] was PERFECT; ft_data[31:16] was corrupt. Those upper 16 bits are
// the ones placed in the far bank (G4/H3/G3/P5/P4/P6/N5/M6/M5/L5/L4/K6/J6/E2/
// D2/M3) -- a longer launch path, so they missed the FT601's ~1 ns setup window
// while the near half made it. WR# (E3) is in that same far bank but never
// glitched: with s_valid tied high it goes low once and stays low, so it has no
// per-cycle edge to violate. DATA toggles every cycle, and DATA is what broke.
//
// Two things were wrong and both are fixed:
//   1. The bus is now launched from IOB flip-flops (IOB="TRUE"), so clock-to-out
//      is a fixed Tco + a short pad route instead of ROM-lookup + adder + routing.
//   2. The top-level XDC now actually carries the set_output_delay /
//      set_input_delay this header always claimed it did. It did NOT -- the
//      interface was completely unconstrained, so the tool never checked, never
//      warned, and happily shipped a bus that misses setup on half its bits.
//
// Registering the outputs costs one cycle of latency, which means WR# can no
// longer be a combinational echo of TXE#. The handshake below restores exactness.
//
//------------------------------------------------------------------------------
// THE REGISTERED HANDSHAKE (no word ever lost or duplicated)
//------------------------------------------------------------------------------
// The output register holds the word currently being PRESENTED to the FT601.
// At each rising edge we evaluate, from the very same signals the FT601 uses at
// that edge, whether the presented word was taken:
//
//     accepted = ~wr_n_q & ~ft_txe
//
// wr_n_q is the WR# level that was driven throughout the cycle now ending, and
// ft_txe is TXE# at this edge -- so master and bridge decide identically and can
// never disagree. If accepted, we latch the next source word (and pop the
// source). If NOT accepted -- TXE# went high, USB buffer full -- we simply hold,
// re-presenting the same word until it is taken. That hold IS the skid buffer;
// no extra storage is needed because the FWFT source has not been popped yet.
//
// Data still leaves at one word per clock while TXE# stays low, so the measured
// FPS remains a clean link-rate number.
//==============================================================================
module ft601_sync_tx (
    input  wire        clk,          // = ft_clk (100 MHz, from the FT601)
    input  wire        rst,          // synchronous, active-high

    // ---- FT601 245-sync-FIFO bus (active-low controls) ----
    input  wire        ft_txe,       // TXE#  : low = room to write
    output wire        ft_wr,        // WR#   : low = write this cycle
    output wire        ft_oe,        // OE#   : held high (TX only)
    output wire        ft_rd,        // RD#   : held high (TX only)
    output wire [31:0] ft_dout,      // data to the FT601 (top drives the bus)
    output wire [3:0]  ft_beout,     // byte enables, registered alongside data
    output wire        bus_oe,       // 1 => FPGA drives DATA/BE

    // ---- stream source (FWFT: s_word valid while s_valid; s_adv pops it) ----
    input  wire [31:0] s_word,
    input  wire        s_valid,
    output wire        s_adv
);
    // Launch everything the FT601 samples per-cycle from IOB flops. The IOB
    // attribute is only a request, so build/run_ftvideo.tcl inspects the PLACED
    // design and hard-fails unless all 33 (32 data + WR#) landed in OLOGIC sites.
    (* IOB = "TRUE" *) reg [31:0] dout_q   = 32'd0;

    // WR# is a single IOB flop. An earlier version kept a second, fabric-visible
    // copy (wr_n_int) because an IOB-packed register drives the pad only and its
    // Q cannot be read back -- and `accepted` needed to read WR#. The replica is
    // gone because reading WR# back was never necessary:
    //
    //     valid_q  <= next_valid        and        wr_n_pad <= ~next_valid
    //
    // so valid_q === ~wr_n_int identically, every cycle, including out of reset
    // (rst drives valid_q=0 and WR#=1). Expressing the handshake in terms of
    // valid_q -- which IS fabric-visible -- is the same function with one less
    // flop, and it is also a TIMING FIX. TXE# is constrained at 7.0 ns
    // clock-to-out, leaving under 3 ns of the 10 ns period for everything from
    // the pad to WR#'s setup. Routed through wr_n_int the chain was
    // ft_txe -> accepted -> load -> next_valid -> wr_n_pad/D and it failed at
    // -0.148, then -0.260 after unrelated logic moved the placement. Substituted:
    //
    //     accepted   = valid_q & ~ft_txe
    //     load       = ~valid_q | ~ft_txe
    //     next_valid = (valid_q & ft_txe) | s_valid
    //
    // one LUT3 from {valid_q, ft_txe, s_valid} straight into the IOB flop. Same
    // protocol, same hold-and-retry, no marginal path. Do not reintroduce a
    // WR#-readback form: it re-creates the violation, and negative slack on this
    // bus is what silently corrupted ft_data[31:16] once already.
    (* IOB = "TRUE" *) reg wr_n_pad = 1'b1;

    reg valid_q = 1'b0;                     // dout_q holds a real word

    // TX only: we always own the bus, never let the FT601 drive it, never read.
    // OE#/RD#/BE are static levels -- they never change after reset, so they have
    // no per-cycle edge to violate and need no IOB flop (unlike DATA, which
    // toggles every clock and is exactly what missed setup before this rewrite).
    assign ft_oe    = 1'b1;
    assign ft_rd    = 1'b1;
    assign bus_oe   = 1'b1;
    assign ft_beout = 4'hF;                 // all four bytes always valid

    // Was the word presented during the cycle now ending actually taken? Both
    // terms are evaluated at this edge -- exactly as the FT601 evaluates them.
    // valid_q is the presented-word flag, identical to ~WR# as driven.
    wire accepted = valid_q & ~ft_txe;

    // The output register is free when its word was taken (or never held one).
    wire load = ~valid_q | ~ft_txe;

    // WR# low whenever a valid word will be presented during the next cycle.
    // When the word was NOT taken (valid_q & ft_txe) it is re-presented, so
    // next_valid stays 1 -- that hold is the skid buffer.
    wire next_valid = (valid_q & ft_txe) | s_valid;

    // Pop the source in the same cycle we latch its word into the output reg.
    assign s_adv = load & s_valid;

    always @(posedge clk) begin
        if (rst) begin
            dout_q   <= 32'd0;
            valid_q  <= 1'b0;
            wr_n_pad <= 1'b1;
        end else begin
            if (load) dout_q <= s_word;
            valid_q  <= next_valid;
            wr_n_pad <= ~next_valid;
        end
    end

    assign ft_dout = dout_q;
    assign ft_wr   = wr_n_pad;
endmodule
