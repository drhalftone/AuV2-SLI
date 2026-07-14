`timescale 1ns/1ps
//=============================================================================
// python1300_lvds_model - behavioral bit-level LVDS transmitter for the PYTHON 1300.
//
// Emits what the real sensor emits so the whole receiver front end (ISERDES, bitslip,
// sync decode, de-interleave -- tasks #8/#9/#10) can be developed with NO Pt hardware.
//
// This is a BIT-LEVEL model. osrf/ovc's sim_python.v drives 32-bit words directly and so
// cannot exercise deserialisation or word alignment; this serialises real DDR bits.
//
// WHAT IS FROM THE DATASHEET (authoritative):
//   - 10-bit words, DDR, 720 Mbps/lane, 4 data + 1 sync, forwarded 360 MHz clock_out.
//   - Bit order MSB first.                                    (datasheet Figure 36, L2573)
//   - Sync framing codes and the training pattern 0x3A6.      (CAMERA_SENSOR_PROTOCOL.md §5)
//   - Line structure: TR... | LS ID IMG..IMG LE ID CRC | TR...  (Figure 34)
//   - Pixel ordering: kernels of 8 pixels; each channel carries 2 pixels of a kernel;
//     EVEN kernels ascending (px 0..7), ODD kernels descending (px 7..0).  (Figure 36, L2555)
//
// WHAT IS A MODELLING CHOICE (localised, and the one thing to confirm at task #12):
//   The exact cross-channel/temporal pairing inside a kernel. Figure 36 fixes that
//   channel c carries kernel positions {2c, 2c+1} and the even/odd reversal; this model
//   implements that in kernel_word() ONLY. The receiver's de-interleave (task #10) inverts
//   THIS function, so the loopback is bit-exact by construction, and if real silicon pairs
//   differently, kernel_word() and the decoder's inverse are the single matched pair to fix.
//   osrf/ovc's UNSWAP_KERNELS encodes the same figure and is the independent cross-check.
//
// TIMING. Data bits change at k*BIT; clock_out edges are offset BIT/2 so each edge lands
// in the centre of a data bit -- the realistic BUFIO eye-centre relationship, and it makes
// behavioral edge-sampling race-free. BIT = 1.389 ns (720 Mbps). clock_out toggles once
// per bit => period 2*BIT = 2.778 ns = 360 MHz, DDR.
//
// COLD START. While !enable the sensor is unconfigured: clock_out and all lanes idle low,
// exactly as before its PLL locks. The receiver must come up from this silence. Assert
// enable to begin (the model then streams training until go_frame is pulsed).
//=============================================================================
module python1300_lvds_model #(
    parameter integer COLS      = 32,       // pixels per line (multiple of 8)
    parameter integer ROWS      = 8,
    parameter integer BLACK_ROWS = 2,       // BL reference lines before the image
    parameter real    BIT_NS    = 1.389,    // 720 Mbps
    // per-lane transmit skew in ns, lane order {sync, d3, d2, d1, d0}. Default 0.
    parameter real    SKEW_D0   = 0.0,
    parameter real    SKEW_D1   = 0.0,
    parameter real    SKEW_D2   = 0.0,
    parameter real    SKEW_D3   = 0.0,
    parameter real    SKEW_SYNC = 0.0
)(
    input  wire        enable,      // 0 = sensor unclocked/idle (cold start)
    input  wire        go_frame,    // pulse (>=1 word) to emit one frame, then back to training
    output reg         busy,        // high while a frame is streaming

    // LVDS out (to the receiver under test)
    output wire        clock_out_p, clock_out_n,
    output wire [3:0]  d_p, d_n,
    output wire        sync_p, sync_n
);
    //-------------------------------------------------------------- sync codes (10-bit)
    // {type[9:7], marker[6:0]=0x2A} for frame-sync; data-class codes are full 10-bit.
    localparam [9:0] SC_FS  = 10'h2AA;  // frame start
    localparam [9:0] SC_FE  = 10'h32A;  // frame end
    localparam [9:0] SC_LS  = 10'h0AA;  // line start
    localparam [9:0] SC_LE  = 10'h12A;  // line end
    localparam [9:0] SC_BL  = 10'h015;  // black pixel data
    localparam [9:0] SC_IMG = 10'h035;  // valid image data
    localparam [9:0] SC_CRC = 10'h059;  // CRC on the data lanes this cycle
    localparam [9:0] SC_TR  = 10'h3A6;  // training
    localparam [9:0] TRAIN  = 10'h3A6;  // data-lane training pattern (reg 116 default)
    localparam [9:0] IDLE_WIN = 10'h000; // window-ID word after a frame-sync code

    //-------------------------------------------------------------- image memory
    // Filled by the testbench before pulsing go_frame. Spatial order: img[row*COLS+col].
    reg [9:0] img [0:ROWS*COLS-1];

    //-------------------------------------------------------------- serialiser state
    reg [9:0] w [0:4];              // current word per lane: 0..3 = data, 4 = sync
    reg [3:0] draw;                 // raw data-lane bits
    reg       sraw;                 // raw sync bit
    integer   bitno;
    reg       cout;

    // clock_out: toggles every BIT, offset by BIT/2 so its edges sit in each bit's centre.
    initial begin
        cout = 1'b0;
        wait (enable);
        #(BIT_NS/2.0);
        forever begin
            if (!enable) begin cout = 1'b0; wait (enable); #(BIT_NS/2.0); end
            cout = ~cout;
            #(BIT_NS);
        end
    end

    // differential outputs, with per-lane transmit skew
    assign #(SKEW_D0)   d_p[0] = draw[0];   assign d_n[0] = ~d_p[0];
    assign #(SKEW_D1)   d_p[1] = draw[1];   assign d_n[1] = ~d_p[1];
    assign #(SKEW_D2)   d_p[2] = draw[2];   assign d_n[2] = ~d_p[2];
    assign #(SKEW_D3)   d_p[3] = draw[3];   assign d_n[3] = ~d_p[3];
    assign #(SKEW_SYNC) sync_p = sraw;      assign sync_n = ~sync_p;
    assign clock_out_p = cout;              assign clock_out_n = ~cout;

    //-------------------------------------------------------------- word stream
    // A tiny FIFO of pending words the sequencer fills; the serialiser drains it. When
    // empty, the serialiser emits training on every lane (idle), which is what the sensor
    // does between frames and is what bitslip locks onto.
    localparam integer QD = 4096;
    reg [9:0] q_d0 [0:QD-1], q_d1 [0:QD-1], q_d2 [0:QD-1], q_d3 [0:QD-1], q_sy [0:QD-1];
    integer   q_head, q_tail;

    task push_word(input [9:0] a0, a1, a2, a3, sy);
    begin
        q_d0[q_tail]=a0; q_d1[q_tail]=a1; q_d2[q_tail]=a2; q_d3[q_tail]=a3; q_sy[q_tail]=sy;
        q_tail = (q_tail + 1) % QD;
    end
    endtask

    // Figure 36 kernel mapping -- THE localised ordering. kbase = index of pixel 0 of the
    // kernel within the line; ki = kernel index (parity picks ascending/descending).
    // Returns the two words (A then B) that channel-carry this 8-pixel kernel.
    // wordA lanes = kernel positions {0,2,4,6}; wordB lanes = {1,3,5,7}.
    task kernel_words(input integer row, input integer kbase, input integer ki,
                      output [9:0] a0,a1,a2,a3, output [9:0] b0,b1,b2,b3);
        reg [9:0] p [0:7];        // kernel positions 0..7 mapped to spatial pixels
        integer i;
    begin
        for (i = 0; i < 8; i = i + 1) begin
            // even kernel: position i = spatial pixel i; odd kernel: reversed
            if (ki[0] == 1'b0) p[i] = img[row*COLS + kbase + i];
            else               p[i] = img[row*COLS + kbase + (7 - i)];
        end
        // channel c carries positions {2c, 2c+1}: first word = even positions, second = odd
        a0=p[0]; a1=p[2]; a2=p[4]; a3=p[6];
        b0=p[1]; b1=p[3]; b2=p[5]; b3=p[7];
    end
    endtask

    integer r, k, kb, ki2;
    reg [9:0] a0,a1,a2,a3,b0,b1,b2,b3;

    // Build one whole frame into the queue.
    task build_frame;
        integer nker;
    begin
        nker = COLS / 8;
        // Frame start, then its window-ID word (sync carries codes; data lanes idle=training)
        push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_FS);
        push_word(TRAIN,TRAIN,TRAIN,TRAIN, IDLE_WIN);

        // BLACK reference lines first (datasheet: black lines precede the image; OVC's FSM
        // waits for them). Same structure as image lines but the sync class is BL.
        for (r = 0; r < BLACK_ROWS; r = r + 1) begin
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_LS);
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, IDLE_WIN);
            for (k = 0; k < nker; k = k + 1) begin
                // black lines carry defined-but-irrelevant data; send 0, class BL
                push_word(10'h0,10'h0,10'h0,10'h0, SC_BL);
                push_word(10'h0,10'h0,10'h0,10'h0, SC_BL);
            end
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_LE);
            push_word(10'h0,10'h0,10'h0,10'h0,  SC_CRC);
        end

        // IMAGE lines
        for (r = 0; r < ROWS; r = r + 1) begin
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_LS);
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, IDLE_WIN);
            for (k = 0; k < nker; k = k + 1) begin
                kb = k * 8;
                kernel_words(r, kb, k, a0,a1,a2,a3, b0,b1,b2,b3);
                push_word(a0,a1,a2,a3, SC_IMG);   // word A: kernel positions 0,2,4,6
                push_word(b0,b1,b2,b3, SC_IMG);   // word B: kernel positions 1,3,5,7
            end
            push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_LE);
            push_word(10'h0,10'h0,10'h0,10'h0,  SC_CRC);   // CRC value not modelled; class marked
        end

        push_word(TRAIN,TRAIN,TRAIN,TRAIN, SC_FE);
        push_word(TRAIN,TRAIN,TRAIN,TRAIN, IDLE_WIN);
    end
    endtask

    // Sequencer: on go_frame, build a frame; otherwise idle. Runs in word-time.
    reg frame_pending;
    initial begin
        q_head = 0; q_tail = 0; busy = 1'b0; frame_pending = 1'b0;
    end
    always @(posedge go_frame) frame_pending <= 1'b1;

    //-------------------------------------------------------------- the serialiser
    // Loads a word set (from the queue, or training if empty) then shifts 10 bits MSB first.
    task load_next;
    begin
        if (q_head != q_tail) begin
            w[0]=q_d0[q_head]; w[1]=q_d1[q_head]; w[2]=q_d2[q_head];
            w[3]=q_d3[q_head]; w[4]=q_sy[q_head];
            q_head = (q_head + 1) % QD;
            busy = 1'b1;
        end else begin
            w[0]=TRAIN; w[1]=TRAIN; w[2]=TRAIN; w[3]=TRAIN; w[4]=SC_TR;   // idle = training
            busy = 1'b0;
            // safe point to enqueue a new frame: only when the queue has drained
            if (frame_pending) begin frame_pending <= 1'b0; build_frame; end
        end
    end
    endtask

    initial begin
        draw = 4'b0000; sraw = 1'b0; bitno = 0;
        wait (enable);
        load_next;
        forever begin
            // MSB first
            draw[0] = w[0][9 - bitno];
            draw[1] = w[1][9 - bitno];
            draw[2] = w[2][9 - bitno];
            draw[3] = w[3][9 - bitno];
            sraw    = w[4][9 - bitno];
            #(BIT_NS);
            bitno = bitno + 1;
            if (bitno == 10) begin bitno = 0; load_next; end
            if (!enable) begin
                draw = 4'b0; sraw = 1'b0; bitno = 0;
                wait (enable); load_next;
            end
        end
    end
endmodule
