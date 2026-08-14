`timescale 1ns/1ps
//==============================================================================
// ft601_sync_rx.v -- FT601Q "245 Synchronous FIFO" READ master (host -> FPGA).
//
// The control channel. The Pt's own COM6 UART is bring-up scaffolding and will
// not exist in the delivered system -- the board is reached ONLY through the
// Ft+ -- so host commands have to arrive over the FT601's OUT pipe (0x02).
//
// Protocol (read path), all active-LOW, all sampled on posedge ft_clk:
//   * RXF# (ft_rxf) -- FT601 output. LOW => at least one word is waiting.
//   * OE#  (ft_oe)  -- master output. LOW hands the DATA bus to the FT601. The
//                      FPGA must tri-state before this, and must not drive again
//                      until after OE# is released.
//   * RD#  (ft_rd)  -- master output. LOW captures one word per clock while
//                      RXF# is also low.
//
// BUS TURNAROUND IS THE WHOLE PROBLEM. Until now bus_oe was a hard constant 1
// and ft601_sync_tx.v says in as many words not to make it dynamic without
// re-checking timing -- a constant lets the tool pack the launch flops into the
// IOBs, which is half of the fix for the 2026-07-30 corruption where
// ft_data[31:16] was silently wrong. So this module never touches the TX path's
// registers. It gates the TX instead:
//
//   rx_hold -> ft601_sync_tx makes TXE# look deasserted, so the TX HOLDS its
//              current word and re-presents it later, and drives WR# high from
//              its own IOB flop. No word is lost, and no output register is
//              muxed outside the IOB.
//
// TURNAROUND_CY of dead cycles are inserted on both edges of the read window so
// the FPGA's drivers and the FT601's never overlap.
//
// Commands are single 32-bit words: [31:28] opcode, [27:0] payload. One word per
// command keeps the reader stateless -- there is no framing to lose sync on, and
// a truncated USB transfer cannot leave the decoder half-way through a command.
//==============================================================================
module ft601_sync_rx #(
    parameter integer TURNAROUND_CY = 3
)(
    input  wire        clk,              // = ft_clk
    input  wire        rst,

    input  wire        ft_rxf,           // RXF# : low = a word is waiting
    input  wire [31:0] ft_din,           // the shared bus, as an input
    // OE# and RD# ARE sampled by the FT601 -- unlike the tri-state enable, these
    // are protocol signals with a real setup window, so they get IOB flops
    // rather than a relaxed constraint. Driven from fabric they missed by
    // 0.985 ns once the pipelined read logic pushed placement away from the
    // pads; from an IOB flop clock-to-out is a fixed Tco plus a pad route.
    // Neither is ever read back in the FSM, so packing costs nothing.
    (* IOB = "TRUE" *) output reg ft_oe, // OE#  : low = FT601 drives DATA
    (* IOB = "TRUE" *) output reg ft_rd, // RD#  : low = capture this cycle

    output reg         bus_oe,           // 1 = FPGA may drive DATA/BE
    output reg         rx_hold,          // 1 = TX must idle (see above)

    output reg  [31:0] cmd_word,
    output reg         cmd_valid,        // one pulse per received word
    output reg  [15:0] cmd_count,        // sticky: words ever received
    output wire [3:0]  dbg               // {RXF#-ever-low, state[2:0]} for telemetry
);
    localparam [2:0] S_IDLE = 3'd0, S_REL = 3'd1, S_OE = 3'd2,
                     S_RD   = 3'd3, S_END = 3'd4, S_BACK = 3'd5,
                     S_CAP  = 3'd6;
    localparam [3:0] RD_LAT = 4'd2;    // RD# low -> data valid on the bus

    reg [2:0] st = S_IDLE;
    reg [3:0] dly = 4'd0;
    reg       rxf_seen = 1'b0;          // sticky: RXF# was ever observed low
    assign dbg = {rxf_seen, st};

    always @(posedge clk) begin
        cmd_valid <= 1'b0;

        if (rst) begin
            st <= S_IDLE; dly <= 4'd0; rxf_seen <= 1'b0;
            ft_oe <= 1'b1; ft_rd <= 1'b1;
            bus_oe <= 1'b1; rx_hold <= 1'b0;
            cmd_word <= 32'd0; cmd_count <= 16'd0;
        end else begin
            case (st)
            // Idle: FPGA owns the bus, TX streams. RXF# low means the host has
            // sent something.
            S_IDLE: begin
                ft_oe <= 1'b1; ft_rd <= 1'b1; bus_oe <= 1'b1;
                if (!ft_rxf) begin
                    rxf_seen <= 1'b1;
                    rx_hold <= 1'b1;          // ask the TX to stand down first
                    dly <= TURNAROUND_CY[3:0];
                    st  <= S_REL;
                end
            end

            // WR# MUST BE HIGH BEFORE THE BUS IS TRI-STATED, and this ordering
            // was wrong in the first version: bus_oe dropped on entry to this
            // state while rx_hold only reached the TX's WR# flop a cycle later,
            // so for one cycle ft_data floated with WR# still low and the FT601
            // latched a garbage word. It showed up as header spacing growing
            // past the exact 2,621,472 and throughput halving -- words inserted
            // into a stream that had been byte-exact.
            //
            // So: hold here with the bus still DRIVEN until rx_hold has taken
            // effect, and only then release.
            S_REL: begin
                if (dly == 4'd0) begin
                    bus_oe <= 1'b0;
                    dly <= TURNAROUND_CY[3:0];
                    st  <= S_OE;
                end else dly <= dly - 4'd1;
            end

            // OE# first, RD# strictly later -- the FT601 needs OE# asserted
            // before it will drive, and RD# in the same cycle would sample the
            // bus while it is still floating.
            S_OE: begin
                ft_oe <= 1'b0;
                if (dly == 4'd0) begin dly <= RD_LAT; st <= S_RD; end
                else dly <= dly - 4'd1;
            end

            // DO NOT RE-TEST RXF# HERE. The first version captured only while
            // RXF# was still low, and the FT601 deasserts it as soon as OE# is
            // taken -- so every window exited immediately without reading, the
            // FSM looped forever on a still-pending word, and the only visible
            // symptoms were cmd_count stuck at 0 and TX bandwidth halved by the
            // constant turnaround. RXF# is the doorbell, not a data-valid flag.
            //
            // Instead: hold RD# low for the pipeline latency, then take exactly
            // ONE word. Commands are one word each, so a window per command is
            // enough, and if more are queued RXF# is still low and the next
            // window follows immediately.
            S_RD: begin
                ft_rd <= 1'b0;
                if (dly == 4'd0) st <= S_CAP;
                else dly <= dly - 4'd1;
            end

            S_CAP: begin
                cmd_word  <= ft_din;
                cmd_valid <= 1'b1;
                cmd_count <= cmd_count + 16'd1;
                ft_rd     <= 1'b1;
                dly       <= TURNAROUND_CY[3:0];
                st        <= S_END;
            end

            S_END: begin
                ft_rd <= 1'b1;
                ft_oe <= 1'b1;
                if (dly == 4'd0) begin dly <= TURNAROUND_CY[3:0]; st <= S_BACK; end
                else dly <= dly - 4'd1;
            end

            // Take the bus back, then release the TX. Same ordering argument as
            // S_REL, in the other direction.
            S_BACK: begin
                bus_oe <= 1'b1;
                if (dly == 4'd0) begin rx_hold <= 1'b0; st <= S_IDLE; end
                else dly <= dly - 4'd1;
            end

            default: st <= S_IDLE;
            endcase
        end
    end
endmodule
