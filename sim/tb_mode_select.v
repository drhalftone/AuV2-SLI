`timescale 1ns/1ps
//==============================================================================
// tb_mode_select - proves the PRIO[] reorder picks HIGHEST REFRESH -> HIGHEST
// PIXEL COUNT, and that the real Dell EDID still resolves to the same mode.
//
//   case 1: Dell U2417H real EDID (dumped from the board)  -> expect idx 2
//   case 2: 800x600@72 + 1024x768@70 only                  -> expect idx 6 (was 5)
//   case 3: 1280x720@60 + 1280x800@60 only                 -> expect idx 9 (was 8)
//==============================================================================
module tb_mode_select;
    reg clk = 0;
    always #5 clk = ~clk;              // 100 MHz

    reg         rst = 1, start = 0;
    wire [7:0]  edid_addr;
    reg  [7:0]  edid_data;
    wire        mode_valid;
    wire [3:0]  mode_idx;
    wire [11:0] o_hact, o_vact;
    wire [7:0]  o_refr;
    wire [16:0] o_pclk_khz;
    wire [12:0] o_supported;

    // ---- EDID RAM with the same 1-cycle registered latency as i2c_master_edid
    reg [7:0] mem [0:255];
    always @(posedge clk) edid_data <= mem[edid_addr];

    mode_select #(.CEIL_KHZ(85000)) dut (
        .clk(clk), .rst(rst), .start(start),
        .edid_addr(edid_addr), .edid_data(edid_data),
        .mode_valid(mode_valid), .mode_idx(mode_idx),
        .o_hact(o_hact), .o_vact(o_vact),
        .o_hfp(), .o_hsync(), .o_hbp(), .o_vfp(), .o_vsync(), .o_vbp(),
        .o_hpol(), .o_vpol(), .o_refr(o_refr), .o_pclk_khz(o_pclk_khz),
        .o_supported(o_supported)
    );

    integer i, fails;

    task clear_edid;
        begin for (i=0;i<256;i=i+1) mem[i] = 8'h00; end
    endtask

    // mark the 8 standard-timing slots unused (0x01,0x01), DTDs left all-zero
    task blank_std;
        begin for (i=38;i<54;i=i+1) mem[i] = 8'h01; end
    endtask

    task run(input [127:0] name, input [3:0] expect_idx);
        begin
            rst = 1; @(posedge clk); @(posedge clk); rst = 0;
            @(posedge clk); start = 1; @(posedge clk); start = 0;
            wait (mode_valid);
            @(posedge clk);
            $write("  %-34s -> idx %0d  (%0dx%0d @ %0d Hz, %0d kHz)  mask=%b",
                   name, mode_idx, o_hact, o_vact, o_refr, o_pclk_khz, o_supported);
            if (mode_idx === expect_idx) $display("   PASS");
            else begin
                $display("   *** FAIL (expected idx %0d) ***", expect_idx);
                fails = fails + 1;
            end
        end
    endtask

    initial begin
        fails = 0;

        // ---------------- case 1: the real Dell U2417H EDID ----------------
        clear_edid;
        // block 0, exactly as dumped over USB from the board
        mem[  0]=8'h00; mem[  1]=8'hFF; mem[  2]=8'hFF; mem[  3]=8'hFF;
        mem[  4]=8'hFF; mem[  5]=8'hFF; mem[  6]=8'hFF; mem[  7]=8'h00;
        mem[  8]=8'h10; mem[  9]=8'hAC; mem[ 10]=8'hE8; mem[ 11]=8'h40;
        mem[ 12]=8'h4C; mem[ 13]=8'h58; mem[ 14]=8'h56; mem[ 15]=8'h44;
        mem[ 16]=8'h1C; mem[ 17]=8'h1B; mem[ 18]=8'h01; mem[ 19]=8'h03;
        mem[ 20]=8'h80; mem[ 21]=8'h35; mem[ 22]=8'h1E; mem[ 23]=8'h78;
        mem[ 24]=8'hEA; mem[ 25]=8'hEE; mem[ 26]=8'h95; mem[ 27]=8'hA3;
        mem[ 28]=8'h54; mem[ 29]=8'h4C; mem[ 30]=8'h99; mem[ 31]=8'h26;
        mem[ 32]=8'h0F; mem[ 33]=8'h50; mem[ 34]=8'h54; mem[ 35]=8'hA5;
        mem[ 36]=8'h4B; mem[ 37]=8'h00; mem[ 38]=8'h71; mem[ 39]=8'h4F;
        mem[ 40]=8'h81; mem[ 41]=8'h80; mem[ 42]=8'hA9; mem[ 43]=8'h40;
        mem[ 44]=8'hD1; mem[ 45]=8'hC0; mem[ 46]=8'h01; mem[ 47]=8'h01;
        mem[ 48]=8'h01; mem[ 49]=8'h01; mem[ 50]=8'h01; mem[ 51]=8'h01;
        mem[ 52]=8'h01; mem[ 53]=8'h01; mem[ 54]=8'h02; mem[ 55]=8'h3A;
        mem[ 56]=8'h80; mem[ 57]=8'h18; mem[ 58]=8'h71; mem[ 59]=8'h38;
        mem[ 60]=8'h2D; mem[ 61]=8'h40; mem[ 62]=8'h58; mem[ 63]=8'h2C;
        mem[ 64]=8'h45; mem[ 65]=8'h00; mem[ 66]=8'h0F; mem[ 67]=8'h28;
        mem[ 68]=8'h21; mem[ 69]=8'h00; mem[ 70]=8'h00; mem[ 71]=8'h1E;
        mem[ 72]=8'h00; mem[ 73]=8'h00; mem[ 74]=8'h00; mem[ 75]=8'hFF;
        mem[ 76]=8'h00; mem[ 77]=8'h58; mem[ 78]=8'h56; mem[ 79]=8'h4E;
        mem[ 80]=8'h4E; mem[ 81]=8'h54; mem[ 82]=8'h37; mem[ 83]=8'h37;
        mem[ 84]=8'h45; mem[ 85]=8'h44; mem[ 86]=8'h56; mem[ 87]=8'h58;
        mem[ 88]=8'h4C; mem[ 89]=8'h0A; mem[ 90]=8'h00; mem[ 91]=8'h00;
        mem[ 92]=8'h00; mem[ 93]=8'hFC; mem[ 94]=8'h00; mem[ 95]=8'h44;
        mem[ 96]=8'h45; mem[ 97]=8'h4C; mem[ 98]=8'h4C; mem[ 99]=8'h20;
        mem[100]=8'h55; mem[101]=8'h32; mem[102]=8'h34; mem[103]=8'h31;
        mem[104]=8'h37; mem[105]=8'h48; mem[106]=8'h0A; mem[107]=8'h20;
        mem[108]=8'h00; mem[109]=8'h00; mem[110]=8'h00; mem[111]=8'hFD;
        mem[112]=8'h00; mem[113]=8'h32; mem[114]=8'h4B; mem[115]=8'h1E;
        mem[116]=8'h53; mem[117]=8'h11; mem[118]=8'h00; mem[119]=8'h0A;
        mem[120]=8'h20; mem[121]=8'h20; mem[122]=8'h20; mem[123]=8'h20;
        mem[124]=8'h20; mem[125]=8'h20; mem[126]=8'h01; mem[127]=8'hAF;
        run("DELL U2417H (real EDID)", 4'd2);

        // ------- case 2: only 800x600@72 (idx6) and 1024x768@70 (idx5) -------
        // byte36 bit7 -> idx6, bit2 -> idx5.  Old code took the lowest index (5,
        // 70 Hz).  Policy says 72 Hz must win.
        clear_edid; blank_std;
        mem[35] = 8'h00;
        mem[36] = 8'h84;              // b7 = 800x600@72, b2 = 1024x768@70
        mem[37] = 8'h00;
        run("72Hz(800x600) vs 70Hz(1024x768)", 4'd6);

        // ------- case 3: only 1280x720@60 (idx8) and 1280x800@60 (idx9) -------
        // Standard timings: (0x81,0xC0)=1280x720@60 16:9, (0x81,0x00)=1280x800@60
        // 16:10.  Same refresh -> more pixels (1280x800) must win.
        clear_edid; blank_std;
        mem[35] = 8'h00; mem[36] = 8'h00; mem[37] = 8'h00;
        mem[38] = 8'h81; mem[39] = 8'hC0;   // 1280x720@60
        mem[40] = 8'h81; mem[41] = 8'h00;   // 1280x800@60
        run("60Hz tie: 1280x720 vs 1280x800", 4'd9);

        $display("");
        if (fails == 0) $display("=== ALL PASS ===");
        else            $display("=== %0d FAILURE(S) ===", fails);
        $finish;
    end
endmodule
