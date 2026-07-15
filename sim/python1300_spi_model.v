`timescale 1ns/1ps
//==============================================================================
// python1300_spi_model - behavioral SPI slave for the onsemi PYTHON 1300.
//
// SHARED between tb_cam_spi_master (unit) and tb_cam_mailbox (integration). Keep it
// in one place: this model encodes two subtleties that are easy to get subtly wrong
// in two different ways if it is copy-pasted.
//
// Built from the datasheet (CAMERA_SENSOR_PROTOCOL.md §1/§2), preloaded with the REAL
// reset defaults -- so a passing chip-ID read here is the same transaction we run on
// hardware in task #5.
//
// THE TWO SUBTLETIES
//
//  1. miso is driven on the RISING edge, and the first data bit lands on cycle ELEVEN.
//     Cycle 10's rising edge captures the R/W bit; D15 appears on the NEXT one. Driving
//     D15 on cycle 10 shifts every read left by one bit (0x50D0 reads back as 0xA1A0).
//     Rising-edge drive is what Table 11's ts_miso = tsck/2 - 10 ns implies -- half a
//     period of setup, not a full one. Modelling it on the falling edge instead would
//     be MORE forgiving than the real part and would let a too-slow master pass here
//     and fail on the bench.
//
//  2. The address is sh_in[9:1], not sh_in[8:0]. After ten rising edges the shift
//     register holds {addr[8:0], rw}. Getting this wrong reads regs[addr<<1] -- which
//     still works for address 0 and fails everywhere else, so it hides behind a
//     passing chip-ID read.
//==============================================================================
module python1300_spi_model (
    input  wire sck,
    input  wire mosi,
    input  wire ss_n,
    output wire miso
);
    reg [15:0] regs [0:511];
    reg [25:0] sh_in;
    reg [15:0] dout;
    reg [8:0]  s_addr;
    reg        s_rw;
    integer    s_cnt;
    reg        miso_drv = 1'b0;
    reg        miso_oe  = 1'b0;
    reg        pll_arm  = 1'b0;   // set when reg 16 enables the PLL; arms the delayed lock

    assign miso = miso_oe ? miso_drv : 1'bz;

    integer i;
    initial begin
        for (i = 0; i < 512; i = i + 1) regs[i] = 16'h0000;
        // ---- REAL datasheet reset defaults ----
        regs[0]   = 16'h50D0;   // chip_id            (READ-ONLY status)
        regs[1]   = 16'h0001;   // resolution[9:8]=0 => PYTHON1300
        regs[2]   = 16'h0000;   // color=0 (mono), parallel=0 (LVDS)
        regs[8]   = 16'h0099;   // soft_reset_pll
        regs[9]   = 16'h0009;   // soft_reset_cgen
        regs[10]  = 16'h0999;   // soft_reset_analog
        regs[16]  = 16'h0004;   // PLL: bypass=1 at reset
        regs[24]  = 16'h0000;   // pll_lock
        regs[32]  = 16'h0004;   // clock gen: select_pll=1
        regs[112] = 16'h0000;   // LVDS power-down: ALL OFF at reset
        regs[116] = 16'h03A6;   // training pattern
        regs[117] = 16'h002A;   // frame sync marker
        regs[118] = 16'h0015;   // BL
        regs[119] = 16'h0035;   // IMG
        regs[125] = 16'h0059;   // CRC
        regs[126] = 16'h03A6;   // TR
        regs[192] = 16'h0000;   // sequencer
    end

    always @(negedge ss_n) begin
        s_cnt   = 0;
        sh_in   = 26'd0;
        miso_oe = 1'b0;
    end
    always @(posedge ss_n) miso_oe = 1'b0;   // release miso on deselect

    always @(posedge sck) if (!ss_n) begin
        sh_in = {sh_in[24:0], mosi};         // sensor samples mosi on the RISING edge
        s_cnt = s_cnt + 1;

        if (s_cnt == 10) begin
            s_addr = sh_in[9:1];             // {addr[8:0], rw} -- see subtlety 2
            s_rw   = sh_in[0];
            if (!s_rw) begin
                dout    = regs[s_addr];
                miso_oe = 1'b1;              // load now; first bit goes out next edge
                $display("[python] READ  reg %0d => 0x%04h", s_addr, regs[s_addr]);
            end
        end else if (s_cnt >= 11 && s_cnt <= 26 && miso_oe) begin
            miso_drv = dout[15];             // D15 on cycle 11 ... D0 on cycle 26
            dout     = {dout[14:0], 1'b0};
        end

        if (s_cnt == 26) begin
            s_addr = sh_in[25:17];
            s_rw   = sh_in[16];
            if (s_rw) begin
                if (s_addr == 9'd0) begin
                    // chip_id is a read-only Status register -- writes must not land.
                    $display("[python] NOTE: write to read-only reg 0 ignored");
                end else begin
                    regs[s_addr] = sh_in[15:0];
                    $display("[python] WRITE reg %0d <= 0x%04h", s_addr, sh_in[15:0]);
                    // PLL: reg 16 bit[1] = enable. The real PLL takes time to lock, then
                    // sets reg 24[0]. Arm a short delayed lock so a boot sequencer's poll
                    // loop actually iterates before it sees lock (exercises the retry path).
                    if (s_addr == 9'd16 && sh_in[1]) pll_arm = 1'b1;
                end
            end
        end
    end

    // Simulated PLL lock: assert reg 24[0] a few us after the PLL is enabled.
    always @(posedge pll_arm) begin
        #4000;                       // ~4 us lock time
        regs[24] = regs[24] | 16'h0001;
    end
endmodule
