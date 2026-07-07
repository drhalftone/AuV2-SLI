`timescale 1ns/1ps
//==============================================================================
// edid_merge - self-contained EDID pass-through merge unit.
//
//   * Reads the OUTPUT display's EDID over its DDC (TX I2C master, open-drain).
//   * Filters it to {display modes} INTERSECT {FPGA pass-through window} and
//     serves the merged block to the host on the INPUT DDC (edid_serve slave).
//   * Drives the host hot-plug (hdmi_rx_hpa): waits for the first build before
//     asserting; pulses the HPD low-window on an output-display plug/unplug AND
//     on any change in merged EDID content (e.g. a projector that swaps its
//     warm-up placeholder EDID for its real one) so the host re-reads the freshly
//     merged EDID without needing a manual replug.
//
//  Encapsulates i2c_master_edid + edid_builder + edid_serve + the controller so
//  the (VHDL) pass-through top only needs to drop in one instance and wire the
//  HDMI DDC / hot-plug pins. No UART here (the top keeps its status reporter).
//==============================================================================
module edid_merge (
    input  wire       clk100,
    input  wire       rst,
    // OUTPUT-connector DDC (read the display's EDID) + hot-plug
    inout  wire       hdmi_tx_rscl,
    inout  wire       hdmi_tx_rsda,
    input  wire       hdmi_tx_hpd,
    // INPUT-connector DDC (present merged EDID to host) + hot-plug assert
    input  wire       hdmi_rx_scl,
    inout  wire       hdmi_rx_sda,
    output reg        hdmi_rx_hpa,
    // debug taps for LEDs
    output wire [5:0] dbg,
    // wide debug for UART telemetry (see assign at end of module)
    output wire [15:0] dbg2,
    // 2nd read port into the captured display EDID (for the offline mode picker)
    input  wire [7:0] mode_rd_addr,
    output wire [7:0] mode_rd_data,
    output wire       edid_ok        // display EDID block-0 checksum good
);
    //--------------------------------------------------------------------------
    // Synchronize / debounce the output-display hot-plug
    //--------------------------------------------------------------------------
    reg hpd_s0, hpd_s1;
    always @(posedge clk100) begin hpd_s0 <= hdmi_tx_hpd; hpd_s1 <= hpd_s0; end
    wire hpd = hpd_s1;

    reg [19:0] hpd_dbc = 0;
    reg        tx_hpd_db = 1'b0, tx_hpd_prev = 1'b0;
    always @(posedge clk100) begin
        if (hpd == tx_hpd_db)                hpd_dbc <= 0;
        else if (hpd_dbc == 20'hFFFFF) begin tx_hpd_db <= hpd; hpd_dbc <= 0; end
        else                                 hpd_dbc <= hpd_dbc + 1'b1;
        tx_hpd_prev <= tx_hpd_db;
    end
    wire hpd_rise = tx_hpd_db & ~tx_hpd_prev;

    //--------------------------------------------------------------------------
    // TX I2C master (open-drain) - reads the display EDID
    //--------------------------------------------------------------------------
    wire scl_oe, sda_oe;
    assign hdmi_tx_rscl = scl_oe ? 1'b0 : 1'bz;
    assign hdmi_tx_rsda = sda_oe ? 1'b0 : 1'bz;

    reg        i2c_start;
    wire       i2c_busy, i2c_done, nack_err, chk0_ok;
    wire [8:0] edid_len;
    wire [7:0] mem_rd_data;
    wire [7:0] bld_rd_addr;

    i2c_master_edid #(.CLK_HZ(100_000_000), .SCL_HZ(100_000)) u_i2c (
        .clk(clk100), .rst(rst), .start(i2c_start),
        .scl_i(hdmi_tx_rscl), .scl_oe(scl_oe),
        .sda_i(hdmi_tx_rsda), .sda_oe(sda_oe),
        .busy(i2c_busy), .done(i2c_done), .nack_err(nack_err),
        .edid_len(edid_len), .chk0_ok(chk0_ok),
        .rd_addr(bld_rd_addr), .rd_data(mem_rd_data),
        .rd_addr2(mode_rd_addr), .rd_data2(mode_rd_data));

    assign edid_ok = chk0_ok;   // expose EDID-valid to the offline mode picker

    //--------------------------------------------------------------------------
    // Builder + RAM-backed serve slave
    //--------------------------------------------------------------------------
    reg        build_go;
    wire [7:0] bld_wr_addr, bld_wr_data;
    wire       bld_wr_en, bld_commit, bld_busy, bld_done;

    edid_builder u_build (
        .clk(clk100), .rst(rst), .start(build_go),
        .rd_addr(bld_rd_addr), .rd_data(mem_rd_data),
        .wr_addr(bld_wr_addr), .wr_data(bld_wr_data), .wr_en(bld_wr_en),
        .commit(bld_commit), .busy(bld_busy), .done(bld_done));

    edid_serve u_serve (
        .clk(clk100),
        .wr_en(bld_wr_en), .wr_addr(bld_wr_addr), .wr_data(bld_wr_data),
        .commit(bld_commit),
        .rdbg_addr(8'd0), .rdbg_data(),
        .sclk_raw(hdmi_rx_scl), .sdat_raw(hdmi_rx_sda), .edid_debug());

    //--------------------------------------------------------------------------
    // Auto trigger ~10 ms after reset
    //--------------------------------------------------------------------------
    reg [19:0] auto_cnt; reg auto_done;
    always @(posedge clk100) begin
        if (rst) begin auto_cnt <= 0; auto_done <= 1'b0; end
        else if (!auto_done) begin
            if (auto_cnt == 20'hFFFFF) auto_done <= 1'b1;
            else auto_cnt <= auto_cnt + 1'b1;
        end
    end
    wire auto_pulse = (!auto_done) && (auto_cnt == 20'hFFFFE);

    //--------------------------------------------------------------------------
    // Periodic probe (~0.5 s). This board does NOT sense the output HPD pin, so
    // we detect the monitor by whether its DDC actually answers: re-read the
    // display EDID on a timer; a clean (non-NACK) read = monitor present, a NACK
    // = no monitor / unplugged.
    //--------------------------------------------------------------------------
    reg [25:0] probe_cnt = 0; reg probe_pulse;
    always @(posedge clk100) begin
        probe_pulse <= 1'b0;
        if (rst) probe_cnt <= 0;
        else if (probe_cnt == 26'd50_000_000) begin probe_cnt <= 0; probe_pulse <= 1'b1; end
        else probe_cnt <= probe_cnt + 1'b1;
    end

    //--------------------------------------------------------------------------
    // Controller: probe -> read display EDID -> build/merge -> commit.
    // monitor_present reflects the DDC read result (NOT the dead HPD pin).
    //--------------------------------------------------------------------------
    reg        read_req, built_valid, monitor_present;
    localparam C_IDLE=2'd0, C_RD=2'd1, C_BUILD=2'd2;
    reg [1:0]  cstate;
    always @(posedge clk100) begin
        i2c_start <= 1'b0; build_go <= 1'b0;
        if (rst) begin
            cstate<=C_IDLE; read_req<=1'b0; built_valid<=1'b0; monitor_present<=1'b0;
        end else begin
            if (auto_pulse || probe_pulse) read_req <= 1'b1;
            case (cstate)
                C_IDLE: if (read_req && !i2c_busy && !bld_busy) begin
                            read_req <= 1'b0;
                            i2c_start <= 1'b1; cstate <= C_RD;   // always attempt the DDC read
                        end
                C_RD:   if (i2c_done) begin
                            if (!nack_err) begin            // display answered -> present
                                monitor_present <= 1'b1;
                                build_go <= 1'b1; cstate <= C_BUILD;
                            end else begin                  // no answer -> no monitor
                                monitor_present <= 1'b0;
                                cstate <= C_IDLE;
                            end
                        end
                C_BUILD: if (bld_done) begin built_valid <= 1'b1; cstate <= C_IDLE; end
            endcase
        end
    end

    //--------------------------------------------------------------------------
    // Detect a CHANGE in the merged EDID *content* (not just monitor presence).
    // A projector powered up alongside this board (e.g. off the projector's own
    // USB) commonly serves a placeholder / safe-mode EDID during warm-up and only
    // switches to its real EDID a few seconds later. The 0.5 s probe rebuilds and
    // re-commits with the corrected modes, but monitor_present never toggles, so
    // the presence-only HPD rule below never re-pulses HPD -- the host keeps the
    // stale (wrong) merged block until the user physically replugs. Here we hash
    // the bytes the builder writes into the served bank and, when a rebuild yields
    // a different block, pulse edid_changed to force the HPD low-window (host
    // re-reads the corrected EDID automatically).
    //
    // The builder stamps an incrementing per-build serial into bytes 12..15 and a
    // fresh checksum into byte 127 on EVERY build (edid_builder.v cache-defeat), so
    // those bytes always differ build-to-build -- exclude them or the signature
    // would change every 0.5 s and drop HPD continuously.
    //--------------------------------------------------------------------------
    wire wr_volatile = (bld_wr_addr >= 8'd12 && bld_wr_addr <= 8'd15) ||
                       (bld_wr_addr == 8'd127);
    reg [15:0] edid_sig;        // rolling signature of the build in progress
    reg [15:0] edid_sig_prev;   // signature of the last committed build
    reg        edid_sig_valid;  // a prior signature exists to compare against
    reg        edid_changed;    // 1-cycle: served EDID content differs from last
    always @(posedge clk100) begin
        edid_changed <= 1'b0;
        if (rst) begin
            edid_sig <= 16'h0; edid_sig_prev <= 16'h0; edid_sig_valid <= 1'b0;
        end else begin
            if (build_go)                        // new build -> clear accumulator
                edid_sig <= 16'h0;
            else if (bld_wr_en && !wr_volatile)  // order-sensitive rotate-xor of content bytes
                edid_sig <= {edid_sig[14:0], edid_sig[15]} ^ {8'h00, bld_wr_data};
            if (bld_done) begin                  // build finished -> compare vs previous
                if (edid_sig_valid && (edid_sig != edid_sig_prev))
                    edid_changed <= 1'b1;
                edid_sig_prev  <= edid_sig;
                edid_sig_valid <= 1'b1;
            end
        end
    end

    //--------------------------------------------------------------------------
    // Host hot-plug (HPD to the source). Three rules:
    //   1. Only assert HPD high when there is actually an OUTPUT monitor to send
    //      video to (tx_hpd_db) AND a merged EDID has been built (built_valid).
    //      No monitor -> hold HPD low so the host never thinks a sink exists.
    //   2. On any change of that "sink present" condition (including first boot),
    //      hold HPD low for ~500 ms before re-asserting, so the source's >100 ms
    //      unplug-debounce trips and it fully re-reads EDID / re-negotiates.
    //   3. On a change in merged EDID *content* while a sink is present (edid_changed,
    //      e.g. projector finished warming up), do the same low-window pulse so the
    //      host re-reads the corrected block without a manual replug.
    //--------------------------------------------------------------------------
    localparam [25:0] HPD_LOW = 26'd50_000_000;       // ~500 ms guaranteed low window
    reg  [25:0] hpd_cnt = HPD_LOW;
    reg         present_q = 1'b0;
    wire        sink_present = monitor_present & built_valid; // DDC answered AND EDID built

    always @(posedge clk100) begin
        if (rst) begin
            hdmi_rx_hpa <= 1'b0; hpd_cnt <= HPD_LOW; present_q <= 1'b0;
        end else begin
            present_q <= sink_present;
            if ((sink_present != present_q) || edid_changed) begin // presence OR content changed -> restart low window
                hdmi_rx_hpa <= 1'b0; hpd_cnt <= HPD_LOW;
            end else if (!sink_present) begin       // no monitor -> stay disconnected
                hdmi_rx_hpa <= 1'b0; hpd_cnt <= HPD_LOW;
            end else if (hpd_cnt != 0) begin        // monitor present, serving low window
                hdmi_rx_hpa <= 1'b0; hpd_cnt <= hpd_cnt - 1'b1;
            end else begin                          // monitor present + settled -> connect
                hdmi_rx_hpa <= 1'b1;
            end
        end
    end

    assign dbg = {built_valid, bld_busy, i2c_busy, nack_err, chk0_ok, monitor_present};

    // UART "M=" field bit map:
    //   [1:0]  cstate     0=IDLE 1=RD(reading display EDID) 2=BUILD(merging)
    //   [2]    monitor_present (DDC answered)
    //   [3]    nack_err   (last DDC read got no ACK = no monitor)
    //   [4]    chk0_ok    (display EDID block-0 checksum good)
    //   [5]    i2c_busy
    //   [6]    bld_busy   (builder running)
    //   [7]    built_valid(merged EDID committed at least once)
    //   [8]    hdmi_rx_hpa(hot-plug asserted to the PC)
    //   [15:9] edid_len[8:2]  (display EDID bytes read, /4 -> 0..64 means 0..256)
    assign dbg2 = {edid_len[8:2], hdmi_rx_hpa, built_valid, bld_busy, i2c_busy,
                   chk0_ok, nack_err, monitor_present, cstate};
endmodule
