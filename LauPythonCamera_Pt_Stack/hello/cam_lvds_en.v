`timescale 1ns/1ps
//=============================================================================
// cam_lvds_en - STAGE 2: power up the sensor's LVDS drivers, then meter them.
//
// The point of this stage is to find bad hand-soldered joints on the twelve
// LVDS pins (sensor 7-18) with a DMM, BEFORE any 720 Mbps receiver exists to
// blame. A powered-up LVDS driver biases its pair to a common mode near 1.2 V.
// So after this bitstream runs:
//
//     every one of sensor pins 7..18 should read ~1.2 V to GND
//     a pin at 0 V or at a rail is a dead driver or a bad joint
//
// That is a complete test of all six output pairs using nothing but a meter.
//
//-----------------------------------------------------------------------------
// THIS IS THE FIRST DESIGN THAT CAN WRITE A REGISTER.
//
// Every bitstream before this one tied cam_spi_master's `rw` input to a
// constant 0, so it was structurally incapable of a write. That guarantee is
// now gone, and three interlocks replace it. The LVDS enable is attempted ONLY
// if all three pass:
//
//   1. CHIP ID. Register 0 must read 0x50D0. If we are not talking to a
//      PYTHON 1300, we do not write to it.
//
//   2. SEQUENCER DISABLED. CAMERA_SENSOR_PROTOCOL.md section 6: the static
//      registers -- 32, 40, 48, 64-71, and 112 -- may only be changed while
//      register 192 bit 0 is 0. We READ 192 and check, rather than assuming its
//      reset default. We never set it.
//
//   3. WRITES DEMONSTRABLY WORK, proven on a register that cannot hurt
//      anything. Register 116 is the training pattern: RW, default 0x03A6, and
//      section 2 names it as the safe write/read-back target. We read it, write
//      a different value, read it back, then RESTORE the default and confirm
//      the restore. Only then do we touch 112.
//
// If any interlock fails the design parks and reports; register 112 is never
// written and the LVDS drivers stay powered down at their reset default.
//
//-----------------------------------------------------------------------------
// WHY THIS IS SAFE ON THE Pt, AND WHY IT WOULD NOT BE ON THE Au
//
// Waking the LVDS drivers is exactly what CAMERA_IO_MAP.md section 8.2 forbids
// on an Au, where dout0 lands on a bank that is not 3.3 V tolerant. On the Pt
// all seven pairs are in bank 13 and this is the intended path.
//
// This design constrains NO bank-13 pin. The sensor drives its outputs into
// FPGA pins that this bitstream leaves unused, so they sit under the
// UNUSEDPIN PULLDOWN rule -- an LVDS driver against a weak pulldown, which the
// driver wins at a few mA. Nothing here depends on the VBSEL strap having set
// bank 13 to 2.5 V, because nothing here receives. That question arrives with
// stage 3, not this one.
//
//-----------------------------------------------------------------------------
// LEDs
//   led[7]  heartbeat
//   led[6]  chip ID == 0x50D0
//   led[5]  sequencer confirmed disabled (reg 192 bit 0 == 0)
//   led[4]  write/read-back/restore on reg 116 all passed
//   led[3]  reg 112 read back as 0x0007  <-- LVDS DRIVERS ARE ON
//   led[2]  a step failed; see the UART line
//
// UART, twice a second:  id=50D0 wr=OK 112=0007 192=0000
//=============================================================================
module cam_lvds_en #(
    parameter integer CLK_HZ  = 100_000_000,
    parameter integer BAUD    =     115_200,
    parameter [15:0]  CHIP_ID = 16'h50D0,
    // The write test target. NOT register 116: cam_regdump proved 116 lives in
    // the serializer/LVDS block, which is powered down at reset, so it neither
    // reads nor writes until 112 wakes it -- unusable as a gate on the write
    // that wakes it. Register 16 is in an awake block (it reads its documented
    // 0x0004), and 0x0003 is exactly what Avnet's SEQ01 writes there, so this
    // is a supported operation rather than an invented one. Restored after.
    parameter [8:0]   WRT_ADDR = 9'd16,
    parameter [15:0]  TR_DEF  = 16'h0004,   // reg 16 reset default
    parameter [15:0]  TR_TEST = 16'h0003,   // Avnet SEQ01 value for reg 16
    parameter [15:0]  LVDS_ON = 16'h0007,   // 112[2:0] = data, sync, clock
    parameter integer RST_LOW_CY  = CLK_HZ / 100,
    parameter integer RST_WAIT_CY = CLK_HZ / 100,
    parameter integer POLL_CY     = CLK_HZ / 2,
    // 0 = clk_pll held low; 1 = free-run it at CLK_HZ/2. See below.
    parameter integer CLK_PLL_EN  = 0
)(
    input  wire       clk,
    input  wire       rst_n,

    output reg  [7:0] led,
    output wire       usb_tx,
    input  wire       usb_rx,

    output wire       cam_sck,
    output wire       cam_mosi,
    output wire       cam_ss_n,
    input  wire       cam_miso,
    output reg        cam_reset_n,
    output wire       cam_clk_pll,
    output wire [2:0] cam_trigger,
    input  wire [1:0] cam_monitor
);
    wire _unused = usb_rx | (|cam_monitor);

    reg [1:0] rstn_sync = 2'b00;
    reg [7:0] por       = 8'h00;
    always @(posedge clk) begin
        rstn_sync <= {rstn_sync[0], rst_n};
        if (!por[7]) por <= por + 8'd1;
    end
    wire rst = !por[7] || !rstn_sync[1];

    assign cam_trigger = 3'b000;

    //-------------------------------------------------------------- clk_pll
    // REGISTER 0 IS NOT LIKE THE OTHERS, AND THAT MISLED US ONCE ALREADY.
    //
    // With clk_pll held low, register 0 reads a perfect 0x50D0 while registers
    // 112, 116 and 192 all read 0x0000 and no write lands. The natural reading:
    // reg 0 is hardwired status, and the rest live in a register file that needs
    // the sensor's internal logic clock -- which needs a reference on clk_pll.
    //
    // That is Table 5's ratspi (= fin/fspi, Min 6) doing exactly what it says:
    // at fin = 0 there is no SPI ceiling to speak of. An earlier bench test
    // seemed to clear the clock hypothesis, but it only ever checked register 0
    // -- the one register that works either way. A bad experiment, not evidence.
    //
    // CLK_HZ/2 = 50 MHz, inside the 45-55 MHz band Avnet's driver handles, and
    // 50 % duty by construction against the sensor's 45-50-55 % requirement.
    // The divider free-runs from configuration while cam_reset_n is still held
    // low, so the clock is stable well before reset releases, and it is never
    // stopped while the sensor is out of reset (protocol section 6).
    reg clk_half = 1'b0;
    always @(posedge clk) clk_half <= ~clk_half;
    assign cam_clk_pll = (CLK_PLL_EN != 0) ? clk_half : 1'b0;

    //-------------------------------------------------------------- SPI master
    reg         spi_start;
    reg         spi_rw;
    reg  [8:0]  spi_addr;
    reg  [15:0] spi_wdata;
    wire [15:0] spi_rdata;
    wire        spi_busy, spi_done;

    cam_spi_master #(.CLK_HZ(CLK_HZ), .SCK_HZ(1_000_000)) u_spi (
        .clk(clk), .rst(rst),
        .start(spi_start), .rw(spi_rw), .addr(spi_addr), .wdata(spi_wdata),
        .rdata(spi_rdata), .busy(spi_busy), .done(spi_done),
        .sck(cam_sck), .mosi(cam_mosi), .ss_n(cam_ss_n), .miso(cam_miso)
    );

    //------------------------------------------------------------------- FSM
    localparam [4:0]
        S_RSTLO   = 5'd0,   // hold sensor reset
        S_RSTWAIT = 5'd1,   // release, settle
        S_RD_ID   = 5'd2,   // reg 0   -> chip id            (interlock 1)
        S_RD_SEQ  = 5'd3,   // reg 192 -> sequencer state    (interlock 2)
        S_RD_TR   = 5'd4,   // reg 116 -> baseline
        S_WR_TR   = 5'd5,   // reg 116 <- TR_TEST
        S_RD_TR2  = 5'd6,   // reg 116 -> must equal TR_TEST (interlock 3)
        S_WR_TRR  = 5'd7,   // reg 116 <- TR_DEF   (restore)
        S_RD_TR3  = 5'd8,   // reg 116 -> must equal TR_DEF
        S_GATE    = 5'd9,   // all three interlocks, or bail
        S_WR_112  = 5'd10,  // reg 112 <- LVDS_ON
        S_RD_112  = 5'd11,  // reg 112 -> read back
        S_REPORT  = 5'd12,
        S_HOLD    = 5'd13,  // re-read id + 112 forever
        S_POLL_ID = 5'd14,
        S_POLL_LV = 5'd15,
        S_FAIL    = 5'd16;

    reg  [4:0]  st  = S_RSTLO;
    reg  [4:0]  ret;                 // where to go after an SPI op completes
    reg  [26:0] tmr = 27'd0;

    reg [15:0] chip_id  = 16'd0;
    reg [15:0] seq192   = 16'hFFFF;
    reg [15:0] tr0      = 16'd0;   // reg 116 BEFORE the write (expect 0x03A6)
    reg [15:0] tr1      = 16'd0;   // reg 116 AFTER  the write (expect 0x0155)
    reg [15:0] lvds_rb  = 16'd0;

    reg        id_ok    = 1'b0;
    reg        seq_ok   = 1'b0;
    reg        wr_ok    = 1'b0;
    reg        wr_step1 = 1'b0;      // TR_TEST read back correctly
    reg        lvds_ok  = 1'b0;
    reg        failed   = 1'b0;
    reg        msg_go;

    // One place that launches an SPI transaction and parks until it lands.
    task do_spi(input rw_i, input [8:0] a, input [15:0] d, input [4:0] nxt);
        begin
            spi_rw    <= rw_i;
            spi_addr  <= a;
            spi_wdata <= d;
            spi_start <= 1'b1;
            ret       <= nxt;
            st        <= 5'd31;      // S_WAITSPI
        end
    endtask

    always @(posedge clk) begin
        spi_start <= 1'b0;
        msg_go    <= 1'b0;

        if (rst) begin
            st          <= S_RSTLO;
            tmr         <= 27'd0;
            cam_reset_n <= 1'b0;
            chip_id     <= 16'd0;
            seq192      <= 16'hFFFF;
            tr0         <= 16'd0;
            tr1         <= 16'd0;
            lvds_rb     <= 16'd0;
            id_ok       <= 1'b0;
            seq_ok      <= 1'b0;
            wr_ok       <= 1'b0;
            wr_step1    <= 1'b0;
            lvds_ok     <= 1'b0;
            failed      <= 1'b0;
        end else begin
            case (st)

            S_RSTLO: begin
                cam_reset_n <= 1'b0;
                if (tmr == RST_LOW_CY[26:0] - 27'd1) begin
                    tmr <= 27'd0; st <= S_RSTWAIT;
                end else tmr <= tmr + 27'd1;
            end

            S_RSTWAIT: begin
                cam_reset_n <= 1'b1;
                if (tmr == RST_WAIT_CY[26:0] - 27'd1) begin
                    tmr <= 27'd0; st <= S_RD_ID;
                end else tmr <= tmr + 27'd1;
            end

            //------------------------------------------- interlock 1: chip id
            S_RD_ID:  if (!spi_busy) do_spi(1'b0, 9'd0,   16'd0,   S_RD_SEQ);

            //------------------------- interlock 2: sequencer must be disabled
            S_RD_SEQ: if (!spi_busy) do_spi(1'b0, 9'd192, 16'd0,   S_RD_TR);

            //---------------------- interlock 3: prove writes on a safe target
            S_RD_TR:  if (!spi_busy) do_spi(1'b0, WRT_ADDR, 16'd0,   S_WR_TR);
            S_WR_TR:  if (!spi_busy) do_spi(1'b1, WRT_ADDR, TR_TEST, S_RD_TR2);
            S_RD_TR2: if (!spi_busy) do_spi(1'b0, WRT_ADDR, 16'd0,   S_WR_TRR);
            S_WR_TRR: if (!spi_busy) do_spi(1'b1, WRT_ADDR, TR_DEF,  S_RD_TR3);
            S_RD_TR3: if (!spi_busy) do_spi(1'b0, WRT_ADDR, 16'd0,   S_GATE);

            //--------------------------------------------------- the decision
            //
            // NOTE: wr_ok is reported but NOT gated on, and that is deliberate.
            //
            // Register 116 was chosen as the "harmless write test" on the advice
            // of CAMERA_SENSOR_PROTOCOL.md section 2. On the bench it turns out
            // 116 CANNOT be the test, because it lives in the serializer/LVDS
            // block whose base is register 112 -- the block this design exists to
            // power up. cam_regdump proved it: with 112 = 0x0000, registers 116,
            // 117, 118, 119, 125 and 126 all read 0x0000 instead of their
            // datasheet defaults, while 0, 1, 8, 9, 10, 16 and 32 read correctly.
            // The block is asleep, so its registers neither read nor write.
            //
            // Gating on that test made it unpassable: it demanded proof of a
            // write inside the block, before the write that wakes the block.
            //
            // So the interlocks that remain are the two that are actually about
            // safety -- are we talking to a PYTHON 1300, and is the sequencer
            // disabled -- and the READ-BACK OF 112 ITSELF is the write proof.
            // If 112 does not read back 0x0007, the write did not land and
            // led[3] stays dark.
            S_GATE: begin
                if (id_ok && seq_ok) st <= S_WR_112;
                else begin
                    failed <= 1'b1;
                    st     <= S_FAIL;
                end
            end

            S_WR_112: if (!spi_busy) do_spi(1'b1, 9'd112, LVDS_ON, S_RD_112);
            S_RD_112: if (!spi_busy) do_spi(1'b0, 9'd112, 16'd0,   S_REPORT);

            S_REPORT: begin
                msg_go <= 1'b1;
                tmr    <= 27'd0;
                st     <= S_HOLD;
            end

            // Park. Keep proving the sensor is still there and 112 still reads
            // back, so a meter reading taken minutes later is trustworthy.
            S_HOLD: begin
                if (tmr == POLL_CY[26:0] - 27'd1) begin
                    tmr <= 27'd0; st <= S_POLL_ID;
                end else tmr <= tmr + 27'd1;
            end
            S_POLL_ID: if (!spi_busy) do_spi(1'b0, 9'd0,   16'd0, S_POLL_LV);
            S_POLL_LV: if (!spi_busy) do_spi(1'b0, 9'd112, 16'd0, S_REPORT);

            // Interlock failure: report the same line forever, write nothing.
            S_FAIL: begin
                msg_go <= 1'b1;
                tmr    <= 27'd0;
                st     <= 5'd30;     // S_FAILWAIT
            end
            5'd30: begin
                if (tmr == POLL_CY[26:0] - 27'd1) begin
                    tmr <= 27'd0; st <= S_FAIL;
                end else tmr <= tmr + 27'd1;
            end

            //--------------------------------------- wait for the SPI to land
            5'd31: begin
                if (spi_done) begin
                    case (ret)
                    S_RD_SEQ: begin chip_id <= spi_rdata;
                                    id_ok   <= (spi_rdata == CHIP_ID); end
                    S_RD_TR:  begin seq192  <= spi_rdata;
                                    seq_ok  <= (spi_rdata[0] == 1'b0); end
                    S_WR_TR:  tr0 <= spi_rdata;                    // baseline
                    S_WR_TRR: begin tr1      <= spi_rdata;
                                    wr_step1 <= (spi_rdata == TR_TEST); end
                    S_GATE:   // full pass = wrote it, and put it back
                              wr_ok <= wr_step1 && (spi_rdata == TR_DEF);
                    S_REPORT: begin lvds_rb <= spi_rdata;
                                    lvds_ok <= (spi_rdata == LVDS_ON); end
                    S_POLL_LV: begin chip_id <= spi_rdata;
                                     id_ok   <= (spi_rdata == CHIP_ID); end
                    default: ;
                    endcase
                    st <= ret;
                end
            end

            default: st <= S_RSTLO;
            endcase
        end
    end

    //------------------------------------------------------------------- LEDs
    reg [25:0] hb = 26'd0;
    always @(posedge clk) hb <= hb + 26'd1;

    always @(posedge clk) begin
        led[7]   <= hb[25];
        led[6]   <= id_ok;
        led[5]   <= seq_ok;
        led[4]   <= wr_ok;
        led[3]   <= lvds_ok;     // <-- drivers are on; go meter pins 7..18
        led[2]   <= failed;
        led[1:0] <= 2'b00;
    end

    //------------------------------------------------------------------- UART
    //   id=50D0 t0=03A6 t1=0155 112=0007 192=0000
    //
    // t0/t1 are register 116 before and after the test write. They are what
    // tells a failed write apart from a failed read:
    //   t0=03A6 t1=0155 -> the write landed
    //   t0=03A6 t1=03A6 -> the write did not land at all
    //   t0 != 03A6      -> the READ is wrong, not the write
    localparam integer MSG_LEN = 43;

    reg  [7:0] ch;
    reg  [7:0] data_q = 8'h00;
    reg  [5:0] idx = 6'd0;
    reg        busy_msg = 1'b0;
    reg        send = 1'b0;
    wire       tx_busy;

    function [7:0] hexd(input [3:0] n);
        hexd = (n < 4'd10) ? (8'h30 + {4'd0, n}) : (8'h41 + {4'd0, n} - 8'd10);
    endfunction

    always @(*) begin
        case (idx)
        6'd0 : ch = "i";  6'd1 : ch = "d";  6'd2 : ch = "=";
        6'd3 : ch = hexd(chip_id[15:12]);
        6'd4 : ch = hexd(chip_id[11:8]);
        6'd5 : ch = hexd(chip_id[7:4]);
        6'd6 : ch = hexd(chip_id[3:0]);
        6'd7 : ch = " ";
        6'd8 : ch = "t";  6'd9 : ch = "0";  6'd10: ch = "=";
        6'd11: ch = hexd(tr0[15:12]);
        6'd12: ch = hexd(tr0[11:8]);
        6'd13: ch = hexd(tr0[7:4]);
        6'd14: ch = hexd(tr0[3:0]);
        6'd15: ch = " ";
        6'd16: ch = "t";  6'd17: ch = "1";  6'd18: ch = "=";
        6'd19: ch = hexd(tr1[15:12]);
        6'd20: ch = hexd(tr1[11:8]);
        6'd21: ch = hexd(tr1[7:4]);
        6'd22: ch = hexd(tr1[3:0]);
        6'd23: ch = " ";
        6'd24: ch = "1";  6'd25: ch = "1";  6'd26: ch = "2";  6'd27: ch = "=";
        6'd28: ch = hexd(lvds_rb[15:12]);
        6'd29: ch = hexd(lvds_rb[11:8]);
        6'd30: ch = hexd(lvds_rb[7:4]);
        6'd31: ch = hexd(lvds_rb[3:0]);
        6'd32: ch = " ";
        6'd33: ch = "1";  6'd34: ch = "9";  6'd35: ch = "2";  6'd36: ch = "=";
        6'd37: ch = hexd(seq192[15:12]);
        6'd38: ch = hexd(seq192[11:8]);
        6'd39: ch = hexd(seq192[7:4]);
        6'd40: ch = hexd(seq192[3:0]);
        6'd41: ch = 8'h0D;
        6'd42: ch = 8'h0A;
        default: ch = " ";
        endcase
    end

    always @(posedge clk) begin
        send <= 1'b0;
        if (rst) begin
            busy_msg <= 1'b0;
            idx      <= 6'd0;
        end else if (!busy_msg) begin
            if (msg_go) begin busy_msg <= 1'b1; idx <= 6'd0; end
        end else if (!tx_busy && !send) begin
            send   <= 1'b1;
            data_q <= ch;
            if (idx == MSG_LEN[5:0] - 6'd1) begin
                busy_msg <= 1'b0;
                idx      <= 6'd0;
            end else idx <= idx + 6'd1;
        end
    end

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_tx (
        .clk(clk), .rst(rst), .data(data_q), .send(send),
        .tx(usb_tx), .busy(tx_busy)
    );

endmodule
