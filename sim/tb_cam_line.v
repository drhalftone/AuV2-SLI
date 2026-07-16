`timescale 1ns/1ps
//=============================================================================
// tb_cam_line - task #11 gate. cam_line_buf capture + UART rdtbl readback, bit-exact.
//
// Drives synthetic cam_sync_decode kernel output into cam_line_buf (the full decode chain
// is already proven bit-exact in tb_cam_decode, so this isolates the capture + readback),
// then reads the captured line back over the REAL 0xA5 UART with rdtbl TGT_CAM_LINE (0x04)
// and checks every byte and the streamed checksum.
//
// Uses a short line (CAM_LINE_LEN=LINE) so the 115200-baud readback sim stays fast; the RTL
// default is the real 1280.
//
// Run: xvlog -d SIM sim/tb_cam_line.v sim/python1300_spi_model.v \
//        sources_1/imports/RTL/{uart_ctrl,uart_rx,uart_tx,status_line,cam_line_buf,cam_spi_master}.v
//      xelab -R tb_cam_line
//=============================================================================
module tb_cam_line;
    localparam integer LINE = 32;                 // short line for a fast readback sim
    localparam integer CAM_AW = 6;                // ceil(log2(32))
    localparam [7:0] SYNC=8'hA5, OP_LR=8'h72, ACK_E=8'h45;
    localparam [7:0] TGT_CAM_LINE=8'h04;

    localparam integer CLK_HZ=100_000_000, BAUD=115200;
    localparam real BIT_NS = 1.0e9/BAUD;

    reg clk=0; always #5 clk=~clk;
    reg rst=1;

    // ---- UART wires ----
    reg  usb_rx=1'b1;
    wire c_tx_data_send;                          // (unused; uart_ctrl -> uart_tx directly)
    wire [7:0] uc_tx_data; wire uc_tx_send, uc_tx_active; wire u_busy;
    wire uart_tx_line;

    // ---- camera line-buffer read port from uart_ctrl ----
    wire [CAM_AW-1:0] cam_line_addr_full;
    wire [10:0]       cam_line_addr;
    wire [7:0]        cam_line_data;

    // ---- synthetic decoder kernel drive ----
    reg        wordclk=0; always #7 wordclk=~wordclk;   // ~71 MHz-ish, phase-independent
    reg        frame_start=0, line_start=0, kvalid=0;
    reg [10:0] kbase=0;                                // match cam_line_buf's 11-bit kbase port
    reg [9:0]  kp[0:7];

    // reference image line
    reg [9:0] src [0:LINE-1];

    cam_line_buf #(.DEPTH(LINE), .ADDR_W(11)) lbuf (
        .wordclk(wordclk), .frame_start(frame_start), .line_start(line_start),
        .kvalid(kvalid), .kbase(kbase),
        .kpix0(kp[0]),.kpix1(kp[1]),.kpix2(kp[2]),.kpix3(kp[3]),
        .kpix4(kp[4]),.kpix5(kp[5]),.kpix6(kp[6]),.kpix7(kp[7]),
        .rd_clk(clk), .rd_addr(cam_line_addr), .rd_data(cam_line_data)
    );

    wire [7:0] rx_data; wire rx_valid;
    uart_rx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) urx (.clk(clk),.rst(rst),.rx(usb_rx),.data(rx_data),.valid(rx_valid));

    uart_ctrl #(.CAM_LINE_LEN(LINE), .CAM_LINE_AW(11)) uc (
        .clk(clk), .rst(rst), .rx_data(rx_data), .rx_valid(rx_valid),
        .tx_data(uc_tx_data), .tx_send(uc_tx_send), .tx_busy(u_busy), .tx_active(uc_tx_active),
        .led(8'h0), .pins(8'h0),
        .sli_ctrl(), .sli_ctrl_en(), .lut_loaded(),
        .corr_addr(8'h0), .corr_dout(), .lut_addr(10'h0), .lut_dout(),
        .lutv_addr(11'h0), .lutv_dout(),
        .edid_rd_addr(), .edid_rd_data(8'h0),
        .cam_line_addr(cam_line_addr), .cam_line_data(cam_line_data),
        .mode_idx_i(4'h0), .mode_valid_i(1'b0), .mode_edid_ok_i(1'b0),
        .mode_refr_i(8'h0), .mode_hact_i(12'h0), .mode_vact_i(12'h0),
        .mode_pclk_i(17'h0), .mode_supp_i(14'h0),
        .corr_pat_addr(8'h0), .corr_pat_dout(), .mode_force(),
        .cam_spi_addr(), .cam_spi_rw(), .cam_spi_wdata(), .cam_spi_start(),
        .cam_spi_rdata(16'h0), .cam_spi_busy(1'b0), .cam_spi_done(1'b0),
        .cam_gpio(), .cam_gpio_in(8'h0)
    );

    uart_tx #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) utx (.clk(clk),.rst(rst),.data(uc_tx_data),.send(uc_tx_send),.tx(uart_tx_line),.busy(u_busy));

    integer errors=0, checks=0;

    //---- UART bit-bang ----
    task uart_send(input [7:0] b); integer k; begin
        usb_rx=0; #(BIT_NS);
        for (k=0;k<8;k=k+1) begin usb_rx=b[k]; #(BIT_NS); end
        usb_rx=1; #(BIT_NS);
    end endtask
    task uart_recv(output [7:0] b); integer k; begin
        @(negedge uart_tx_line); #(BIT_NS*1.5);
        for (k=0;k<8;k=k+1) begin b[k]=uart_tx_line; if(k<7) #(BIT_NS); end
        #(BIT_NS);
    end endtask

    integer i;
    reg [7:0] echo, ck, got, sum;

    //---- drive one image line into the buffer (kernel-parallel, like cam_sync_decode) ----
    task push_line;
        integer k;
    begin
        @(posedge wordclk); frame_start<=1; @(posedge wordclk); frame_start<=0;
        @(posedge wordclk); line_start<=1;  @(posedge wordclk); line_start<=0;
        for (k=0; k<LINE/8; k=k+1) begin
            @(posedge wordclk);
            kbase <= k*8;
            for (i=0;i<8;i=i+1) kp[i] <= src[k*8+i];
            kvalid <= 1'b1;
            @(posedge wordclk); kvalid <= 1'b0;
            @(posedge wordclk);                  // gap between kernels
        end
        @(posedge wordclk); line_start<=1;  @(posedge wordclk); line_start<=0;  // ends capture
        repeat (4) @(posedge wordclk);
    end
    endtask

    initial begin
        for (i=0;i<LINE;i=i+1) src[i] = ((i*13+5) & 10'h3FF);
        src[0]=10'h3FC; src[LINE-1]=10'h084;      // landmarks (8-bit truncation: 0xFF, 0x21)

        $display("=== tb_cam_line: capture %0d px, read back over rdtbl 0x04 ===", LINE);
        repeat (5) @(posedge clk); rst<=0; repeat (5) @(posedge clk);

        push_line;

        // ---- rdtbl: A5 72 04 CK ; reply 04 D[0..LINE-1] CK2 ----
        ck = 8'h00 - (OP_LR + TGT_CAM_LINE);
        uart_send(SYNC); uart_send(OP_LR); uart_send(TGT_CAM_LINE); uart_send(ck);

        uart_recv(echo);
        checks=checks+1;
        if (echo!==TGT_CAM_LINE) begin $display("*** FAIL: echo 0x%02h != target",echo); errors=errors+1; end

        sum = TGT_CAM_LINE;
        for (i=0;i<LINE;i=i+1) begin
            uart_recv(got);
            sum = sum + got;
            checks=checks+1;
            if (got !== src[i][9:2]) begin
                errors=errors+1;
                if (errors<=12) $display("*** FAIL: byte %0d: got 0x%02h exp 0x%02h", i, got, src[i][9:2]);
            end
        end
        uart_recv(ck);                            // streamed checksum: (target+sum(data)+ck)==0
        checks=checks+1;
        if (((sum+ck)&8'hFF)!==8'h00) begin $display("*** FAIL: bad stream checksum"); errors=errors+1; end
        else $display("    stream checksum OK");

        if (errors==0) $display("    PASS: all %0d bytes bit-exact over the UART", LINE);
        $display(""); $display("=== %0d checks, %0d errors ===", checks, errors);
        if (errors==0) $display("=== PASS ==="); else $display("=== FAIL ===");
        $finish;
    end
    initial begin #50_000_000; $display("*** FAIL: timeout"); $finish; end
endmodule
