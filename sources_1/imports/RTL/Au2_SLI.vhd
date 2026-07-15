-- Company: 
-- Engineer: Qihsi Hu 
-- Create Date: 12/05/2024 08:04:50 PM
-- Design Name: 
-- Module Name: hdmi_design
-- Description: top moudle

library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
library UNISIM;
use UNISIM.VComponents.all;
use IEEE.STD_LOGIC_ARITH.ALL;  -- optional, for older VHDL versions
use IEEE.STD_LOGIC_UNSIGNED.ALL; -- optional, for older VHDL versions
use IEEE.NUMERIC_STD.ALL;      -- Required for unsigned arithmetic

entity Au2_SLI is
    Port ( 
        clk100    : in STD_LOGIC;
        usb_tx    : out   STD_LOGIC;  -- FT2232H ch.B (COM port) TX: status telemetry + cmd replies
        usb_rx    : in    STD_LOGIC;  -- FT2232H ch.B (COM port) RX: 0xA5 host control protocol (P15)
        -- HDMI-OUT DDC/HPD (Hd V2 port 1, bank 35) for TX-side EDID reading
        hdmi_tx_scl : inout STD_LOGIC;  -- C7 (header A72)
        hdmi_tx_sda : inout STD_LOGIC;  -- C6 (header A70)
        hdmi_tx_hpd : in    STD_LOGIC;  -- B7 (header A78)
        led           : out   std_logic_vector(7 downto 0) :=(others => '0');
        newSW            : in    std_logic_vector(3 downto 0) :=(others => '0');
        -- four-line handsake signals for two camera interfaces
        C1_out : out    std_logic_vector(1 downto 0);
        C1_in : in    std_logic_vector(1 downto 0);
        C2_out : out    std_logic_vector(1 downto 0);
        C2_in : in    std_logic_vector(1 downto 0);
       -- VS    : out STD_LOGIC;
       -- fg : out STD_LOGIC;
       
     
        --HDMI input signals
        hdmi_rx_cec   : inout std_logic;
        hdmi_rx_hpa   : out   std_logic;
        hdmi_rx_scl   : in    std_logic;
        hdmi_rx_sda   : inout std_logic;
        hdmi_rx_clk_n : in    std_logic;
        hdmi_rx_clk_p : in    std_logic;
        hdmi_rx_n     : in    std_logic_vector(2 downto 0);
        hdmi_rx_p     : in    std_logic_vector(2 downto 0);

        --- HDMI out
--        hdmi_tx_cec   : inout std_logic;
        hdmi_tx_clk_n : out   std_logic;
        hdmi_tx_clk_p : out   std_logic;
--        hdmi_tx_hpd   : in    std_logic;
--        hdmi_tx_rscl  : inout std_logic;
--        hdmi_tx_rsda  : inout std_logic;
        hdmi_tx_p     : out   std_logic_vector(2 downto 0);
        hdmi_tx_n     : out   std_logic_vector(2 downto 0);

        -- ===== PYTHON 1300 camera element (top of the stack) =====
        -- CMOS control only -- LVCMOS33, element Bank A low, Au V2 banks 14/35.
        -- The LVDS pairs are DELIBERATELY ABSENT: on the Au they scatter across banks
        -- 14/15/34, and dout0 lands on bank 15 (VCCO 1.35 V, NOT 3.3 V tolerant). The
        -- sensor's LVDS drivers default to powered down (reg 112 = 0), so they never
        -- drive those pins -- provided nothing writes reg 112 on an Au build.
        -- See CAMERA_SENSOR_PROTOCOL.md §3.
        -- elem A5 -> M6 (14) | A3 -> N6 (14) | A10 -> L2 (35) | A4 -> P9 (14)
        -- elem A9 -> J1 (35) | A11/A12/A15 -> K1/L3/H1 (35) | A16/A17 -> K2/H2 (35)
        cam_sck     : out   std_logic;                     -- M6  bank 14
        cam_mosi    : out   std_logic;                     -- N6  bank 14
        cam_ss_n    : out   std_logic;                     -- L2  bank 35
        cam_miso    : in    std_logic := '0';              -- P9  bank 14
        cam_reset_n : out   std_logic;                     -- J1  bank 35
        cam_trigger : out   std_logic_vector(2 downto 0);  -- K1 / L3 / H1  bank 35
        cam_monitor : in    std_logic_vector(1 downto 0) := (others => '0')  -- K2 / H2  bank 35
    );
end Au2_SLI;

architecture Behavioral of Au2_SLI is
    component ref_clk is
    Port (
        clk_in    : in STD_LOGIC;    
        clk_out : out STD_LOGIC;
        clk10 : out STD_LOGIC;
        clk125 : out STD_LOGIC;
        clk625 : out STD_LOGIC
    );
    end component;
    component hdmi_io is
    Port ( 
        clk100    : in STD_LOGIC;
        clk200    : in STD_LOGIC;
        clk125 : in STD_LOGIC;
        clk625 : in STD_LOGIC;
        clk10 : in STD_LOGIC;
        -------------------------------
        -- Control signals
        -------------------------------
        clock_locked  : out std_logic;
        data_synced   : out std_logic;
        debug         : out std_logic_vector(7 downto 0);  
        sel : out std_logic; 
        -------------------------------
        --HDMI input signals
        -------------------------------
        hdmi_rx_cec   : inout std_logic;
        hdmi_rx_clk_n : in    std_logic;
        hdmi_rx_clk_p : in    std_logic;
        hdmi_rx_n     : in    std_logic_vector(2 downto 0);
        hdmi_rx_p     : in    std_logic_vector(2 downto 0);

        -------------
        -- HDMI out
        -------------
       -- hdmi_tx_cec   : inout std_logic;
        hdmi_tx_clk_n : out   std_logic;
        hdmi_tx_clk_p : out   std_logic;
       -- hdmi_tx_hpd   : in    std_logic;
       -- hdmi_tx_rscl  : inout std_logic;
       -- hdmi_tx_rsda  : inout std_logic;
        hdmi_tx_p     : out   std_logic_vector(2 downto 0);
        hdmi_tx_n     : out   std_logic_vector(2 downto 0);

        pixel_clk     : out std_logic;
        -------------------------------
        -- VGA data recovered from HDMI
        -------------------------------
        in_hdmi_detected : out std_logic;
        in_blank        : out std_logic;
        in_hsync        : out std_logic;
        in_vsync        : out std_logic;
        in_red          : out std_logic_vector(7 downto 0);
        in_green        : out std_logic_vector(7 downto 0);
        in_blue         : out std_logic_vector(7 downto 0);
        is_interlaced   : out std_logic;
        is_second_field : out std_logic;
            
        -------------------------------------
        -- Audio Levels
        -------------------------------------
        audio_channel : out std_logic_vector(2 downto 0);
        audio_de      : out std_logic;
        audio_sample  : out std_logic_vector(23 downto 0);
        
        -----------------------------------
        -- VGA data to be converted to HDMI
        -----------------------------------
        out_blank     : in  std_logic;
        out_hsync     : in  std_logic;
        out_vsync     : in  std_logic;
        out_red       : in  std_logic_vector(7 downto 0);
        out_green     : in  std_logic_vector(7 downto 0);
        out_blue      : in  std_logic_vector(7 downto 0);
        -----------------------------------
        -- For symbol dump or retransmit
        -----------------------------------
        symbol_sync  : out std_logic; -- indicates a fixed reference point in the frame.
        symbol_ch0   : out std_logic_vector(9 downto 0);
        symbol_ch1   : out std_logic_vector(9 downto 0);
        symbol_ch2   : out std_logic_vector(9 downto 0)
    );
    end component;
    signal clk200  : std_logic;
    signal clk10  : std_logic;
    signal clk125  : std_logic;
    signal clk625  : std_logic;
    signal pclk  : std_logic;
    signal VPolarity  : std_logic;
    signal symbol_sync  : std_logic;
    signal symbol_ch0   : std_logic_vector(9 downto 0);
    signal symbol_ch1   : std_logic_vector(9 downto 0);
    signal symbol_ch2   : std_logic_vector(9 downto 0);
    signal debug_pmod    :   std_logic_vector(7 downto 0) :=(others => '0');    
    signal sel: std_logic;
    
    component vga is
    Port ( pixelClock : in  STD_LOGIC;
           hRez        : in  STD_LOGIC_VECTOR(11 downto 0);
           hStartSync  : in  STD_LOGIC_VECTOR(11 downto 0);
           hEndSync    : in  STD_LOGIC_VECTOR(11 downto 0);
           hMaxCount   : in  STD_LOGIC_VECTOR(11 downto 0);
           hsyncActive : in  STD_LOGIC;
           vRez        : in  STD_LOGIC_VECTOR(11 downto 0);
           vStartSync  : in  STD_LOGIC_VECTOR(11 downto 0);
           vEndSync    : in  STD_LOGIC_VECTOR(11 downto 0);
           vMaxCount   : in  STD_LOGIC_VECTOR(11 downto 0);
           vsyncActive : in  STD_LOGIC;
           Red        : out STD_LOGIC_VECTOR (7 downto 0);
           Green      : out STD_LOGIC_VECTOR (7 downto 0);
           Blue       : out STD_LOGIC_VECTOR (7 downto 0);
           hSync      : out STD_LOGIC;
           vSync      : out STD_LOGIC;
           blank      : out STD_LOGIC);
    end component;
    
    component pixel_pipe is
        Port ( clk : in STD_LOGIC;  clk10 : in STD_LOGIC; 
        sw : in std_logic_vector(3 downto 0); -- switches
        trig    : out STD_LOGIC;  f_frm   : out STD_LOGIC; 
        mode    : in STD_LOGIC;  rdy   : in STD_LOGIC;
        vid_valid : in STD_LOGIC;
            ------------------
            in_blank  : in std_logic;
            in_hsync  : in std_logic;
            in_vsync  : in std_logic;
            vsync  : in std_logic;
            in_red    : in std_logic_vector(7 downto 0);
            in_green  : in std_logic_vector(7 downto 0);
            in_blue   : in std_logic_vector(7 downto 0);

            -------------------
            out_blank : out std_logic;
            out_hsync : out std_logic;
            out_vsync : out std_logic;
            out_red   : out std_logic_vector(7 downto 0);
            out_green : out std_logic_vector(7 downto 0);
            out_blue  : out std_logic_vector(7 downto 0);
            tlp_dbg      : out std_logic_vector(7 downto 0);
            olp_dbg      : out std_logic_vector(7 downto 0);
            trig_cnt_dbg : out std_logic_vector(7 downto 0);
            -- radiometric transfer LUT seam -> uart_ctrl's host-uploadable corr table
            lut_din      : out std_logic_vector(7 downto 0);
            lut_dout     : in  std_logic_vector(7 downto 0)
    );
    end component;

    -- pattern_gen's tone-LUT seam: raw cosine out, linearised value back, SAME cycle.
    signal pat_lut_din  : std_logic_vector(7 downto 0);
    signal pat_lut_dout : std_logic_vector(7 downto 0);

    signal not_C1in1 : std_logic;
    signal pixel_clk : std_logic;
    signal sel_buf  : std_logic;
    signal in_blank  : std_logic;
    signal in_blank_reg  : std_logic;
    signal in_hsync  : std_logic;
    signal in_vsync  : std_logic;
    signal in_vsync_reg  : std_logic;
    signal in_red    : std_logic_vector(7 downto 0);
    signal in_green  : std_logic_vector(7 downto 0);
    signal in_blue   : std_logic_vector(7 downto 0);
    signal local_blank  : std_logic;
    signal local_hsync  : std_logic;
    signal local_vsync  : std_logic;
    signal local_red    : std_logic_vector(7 downto 0);
    signal local_green  : std_logic_vector(7 downto 0);
    signal local_blue   : std_logic_vector(7 downto 0);
    signal blank  : std_logic;
    signal hsync  : std_logic;
    signal vsync  : std_logic;
    signal vsync_Pos  : std_logic;
    signal vsync_reg  : std_logic;
    signal vsync_dur  : std_logic_vector(7 downto 0);
    signal red    : std_logic_vector(7 downto 0);
    signal green  : std_logic_vector(7 downto 0);
    signal blue   : std_logic_vector(7 downto 0);
    signal is_interlaced   : std_logic;
    signal is_second_field : std_logic;
    signal out_blank : std_logic;
    signal out_hsync : std_logic;
    signal out_vsync : std_logic;
    signal out_red   : std_logic_vector(7 downto 0);
    signal out_green : std_logic_vector(7 downto 0);
    signal out_blue  : std_logic_vector(7 downto 0);
    signal rdy_buf : std_logic;
    signal mode_buf : std_logic;
    signal audio_channel : std_logic_vector(2 downto 0);
    signal audio_de      : std_logic;
    signal audio_sample  : std_logic_vector(23 downto 0);
    signal trig : std_logic;
    signal f_frm : std_logic;
    signal debug : std_logic_vector(7 downto 0);
    signal led_i : std_logic_vector(7 downto 0);  -- mirror of the LED status byte for telemetry
    signal vid_valid : std_logic;  -- HDMI input validly decoding (symbol_sync & pll_locked)
    signal tlp_val   : std_logic_vector(7 downto 0);  -- sampled top-left red, pipe INPUT (diagnostic)
    signal olp_val   : std_logic_vector(7 downto 0);  -- sampled top-left red, pipe OUTPUT (diagnostic)
    signal trig_cnt  : std_logic_vector(7 downto 0);  -- trigger pulse count (diagnostic)

    -- "Sign of life" idle animation: slides one LED when no frames are running.
    signal led_out   : std_logic_vector(7 downto 0);  -- final LED bus (status or idle slider)
    signal connected : std_logic;  -- 1 = real HDMI input decoding OR output monitor present
    component led_idle_anim is
        Port ( clk100     : in  STD_LOGIC;
               connected  : in  STD_LOGIC;
               status_led : in  STD_LOGIC_VECTOR(7 downto 0);
               led_out    : out STD_LOGIC_VECTOR(7 downto 0) );
    end component;

    -- Bidirectional USB-serial subsystem: status telemetry (as edid_reader) PLUS the
    -- 0xA5 host control protocol on usb_rx. Stage-2 taps (sli_ctrl / table read ports)
    -- are defaulted so they may be left open until the pixel datapath is wired up.
    component usb_link is
        Port ( clk100 : in  STD_LOGIC;
               led    : in  STD_LOGIC_VECTOR(7 downto 0);
               dbg    : in  STD_LOGIC_VECTOR(7 downto 0);
               mrg    : in  STD_LOGIC_VECTOR(7 downto 0);
               tlp    : in  STD_LOGIC_VECTOR(7 downto 0);
               tcnt   : in  STD_LOGIC_VECTOR(7 downto 0);
               olp    : in  STD_LOGIC_VECTOR(7 downto 0);
               usb_rx : in  STD_LOGIC;
               usb_tx : out STD_LOGIC;
               phys_sw : in STD_LOGIC_VECTOR(3 downto 0) := (others => '0');
               eff_sw  : in STD_LOGIC_VECTOR(3 downto 0) := (others => '0');
               sli_ctrl    : out STD_LOGIC_VECTOR(7 downto 0);
               sli_ctrl_en : out STD_LOGIC;
               lut_loaded  : out STD_LOGIC;
               corr_addr : in  STD_LOGIC_VECTOR(7 downto 0)  := (others => '0');
               corr_dout : out STD_LOGIC_VECTOR(7 downto 0);
               lut_addr  : in  STD_LOGIC_VECTOR(9 downto 0)  := (others => '0');
               lut_dout  : out STD_LOGIC_VECTOR(7 downto 0);
               lutv_addr : in  STD_LOGIC_VECTOR(10 downto 0) := (others => '0');
               lutv_dout : out STD_LOGIC_VECTOR(7 downto 0);
               -- captured-EDID read port -> edid_merge's 3rd port (rdtbl TGT_EDID)
               edid_rd_addr : out STD_LOGIC_VECTOR(7 downto 0);
               edid_rd_data : in  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
               -- captured camera-line read port (rdtbl TGT_CAM_LINE). No receiver on the
               -- Au, so the top ties cam_line_data to 0 -- the target reads back zeros.
               cam_line_addr : out STD_LOGIC_VECTOR(10 downto 0);
               cam_line_data : in  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
               -- radiometric transfer LUT read by pattern_gen (combinational)
               corr_pat_addr : in  STD_LOGIC_VECTOR(7 downto 0) := (others => '0');
               corr_pat_dout : out STD_LOGIC_VECTOR(7 downto 0);
               -- offline mode decision (regs 0x20..0x2A), already in clk100
               mode_idx_i     : in STD_LOGIC_VECTOR(3 downto 0)  := (others => '0');
               mode_valid_i   : in STD_LOGIC                     := '0';
               mode_edid_ok_i : in STD_LOGIC                     := '0';
               mode_refr_i    : in STD_LOGIC_VECTOR(7 downto 0)  := (others => '0');
               mode_hact_i    : in STD_LOGIC_VECTOR(11 downto 0) := (others => '0');
               mode_vact_i    : in STD_LOGIC_VECTOR(11 downto 0) := (others => '0');
               mode_pclk_i    : in STD_LOGIC_VECTOR(16 downto 0) := (others => '0');
               mode_supp_i    : in STD_LOGIC_VECTOR(13 downto 0) := (others => '0');
               mode_force     : out STD_LOGIC_VECTOR(7 downto 0);
               -- PYTHON 1300 camera (regs 0x30..0x38). The SPI master lives inside
               -- usb_link, so these are the sensor's physical pins.
               cam_sck     : out STD_LOGIC;
               cam_mosi    : out STD_LOGIC;
               cam_ss_n    : out STD_LOGIC;
               cam_miso    : in  STD_LOGIC := '0';
               cam_reset_n : out STD_LOGIC;
               cam_trigger : out STD_LOGIC_VECTOR(2 downto 0);
               cam_monitor : in  STD_LOGIC_VECTOR(1 downto 0) := (others => '0') );
    end component;

    -- host EDID dump: usb_link drives the address, edid_merge returns the byte
    signal edid_host_addr : std_logic_vector(7 downto 0);
    signal edid_host_data : std_logic_vector(7 downto 0);

    component edid_merge is
        Port ( clk100       : in    STD_LOGIC;
               rst          : in    STD_LOGIC;
               hdmi_tx_rscl : inout STD_LOGIC;
               hdmi_tx_rsda : inout STD_LOGIC;
               hdmi_tx_hpd  : in    STD_LOGIC;
               hdmi_rx_scl  : in    STD_LOGIC;
               hdmi_rx_sda  : inout STD_LOGIC;
               hdmi_rx_hpa  : out   STD_LOGIC;
               dbg          : out   STD_LOGIC_VECTOR(5 downto 0);
               dbg2         : out   STD_LOGIC_VECTOR(15 downto 0);
               mode_rd_addr : in    STD_LOGIC_VECTOR(7 downto 0);
               mode_rd_data : out   STD_LOGIC_VECTOR(7 downto 0);
               host_rd_addr : in    STD_LOGIC_VECTOR(7 downto 0);
               host_rd_data : out   STD_LOGIC_VECTOR(7 downto 0);
               edid_ok      : out   STD_LOGIC );
    end component;

    -- EDID -> best supported curated mode (runs on the slow clk10; combinationally
    -- too deep for 100 MHz). Reads the display EDID via edid_merge's 2nd read port.
    component mode_select is
        generic ( CEIL_KHZ : integer := 85000 );
        port ( clk : in std_logic; rst : in std_logic; start : in std_logic;
               edid_addr  : out std_logic_vector(7 downto 0);
               edid_data  : in  std_logic_vector(7 downto 0);
               mode_valid : out std_logic;
               mode_idx   : out std_logic_vector(3 downto 0);
               o_hact : out std_logic_vector(11 downto 0); o_vact : out std_logic_vector(11 downto 0);
               o_hfp  : out std_logic_vector(11 downto 0); o_hsync: out std_logic_vector(11 downto 0);
               o_hbp  : out std_logic_vector(11 downto 0); o_vfp  : out std_logic_vector(11 downto 0);
               o_vsync: out std_logic_vector(11 downto 0); o_vbp  : out std_logic_vector(11 downto 0);
               o_hpol : out std_logic; o_vpol : out std_logic;
               o_refr : out std_logic_vector(7 downto 0);
               o_pclk_khz : out std_logic_vector(16 downto 0);
               o_supported: out std_logic_vector(13 downto 0) );   -- 14 modes as of idx13
    end component;

    signal merge_dbg2 : std_logic_vector(15 downto 0);
    signal merge_dbg  : std_logic_vector(7 downto 0);

    -- USB SLI-control override (register 0x13) of the physical newSW switch pins.
    -- 0x13 = {7:sw_en, 3:R_en, 2:G_en, 1:B_en, 0:orient}; bits [3:0] map 1:1 to
    -- pixel_pipe's sw / newSW. When sw_en=1 the USB value drives the datapath.
    signal sli_ctrl_bus  : std_logic_vector(7 downto 0);   -- reg 0x13 (clk100 domain)
    signal sli_ctrl_en_w : std_logic;                      -- = sli_ctrl_bus(7)
    -- 2FF sync -> pixel_clk: {sw_en, mode_en, mode_val, R,G,B, orient} (reg 0x13 bits 7,6,5,3..0)
    signal sli_sw_p0, sli_sw_p1 : std_logic_vector(6 downto 0);
    signal effective_sw  : std_logic_vector(3 downto 0);   -- USB override or physical newSW
    signal por        : std_logic := '1';
    signal por_cnt    : integer range 0 to 15 := 0;

    -- Reconfigurable OFFLINE output clock generator (replaces ref_clk's static
    -- clk125/clk625 for the output path). See OUTPUT_CLK_EDID_DESIGN.md.
    -- Proven Mimas A7 13-mode DRP pixel-clock generator (ported; M/D/O table was
    -- computed for a 100 MHz input = same as the Au V2, so it ports unchanged).
    component drp_clkgen13 is
        port ( clk100   : in  std_logic;
               mode_idx : in  std_logic_vector(3 downto 0);
               sen      : in  std_logic;
               srdy     : out std_logic;
               pixel_clk       : out std_logic;
               pixel_io_clk_x1 : out std_logic;
               pixel_io_clk_x5 : out std_logic;
               locked   : out std_logic );
    end component;

    -- Per-mode video geometry ROM (same curated table as drp_clkgen13's M/D/O).
    component mode_timing_rom is
        port ( mode_idx : in  std_logic_vector(3 downto 0);
               h_active : out std_logic_vector(11 downto 0);
               h_fp     : out std_logic_vector(11 downto 0);
               h_sync   : out std_logic_vector(11 downto 0);
               h_bp     : out std_logic_vector(11 downto 0);
               v_active : out std_logic_vector(11 downto 0);
               v_fp     : out std_logic_vector(11 downto 0);
               v_sync   : out std_logic_vector(11 downto 0);
               v_bp     : out std_logic_vector(11 downto 0);
               h_pol    : out std_logic;
               v_pol    : out std_logic;
               pclk_khz : out std_logic_vector(16 downto 0);
               refr     : out std_logic_vector(7 downto 0) );
    end component;

    signal offline_pix, offline_pix_x1, offline_pix_x5 : std_logic;
    signal clk125_u, clk625_u : std_logic;     -- ref_clk's old offline clocks, now unused
    signal clkgen_srdy, clkgen_locked : std_logic;
    signal clkgen_sen : std_logic := '0';

    -- ===== Phase 2 step 2: offline mode index driven by the projector's EDID =====
    signal ms_start, ms_valid, edid_ok : std_logic;
    signal ms_idx        : std_logic_vector(3 downto 0);
    signal mode_rd_addr  : std_logic_vector(7 downto 0);
    signal mode_rd_data  : std_logic_vector(7 downto 0);
    signal edid_ok_s0, edid_ok_s1 : std_logic := '0';
    signal applied_idx : std_logic_vector(3 downto 0) := "0010";  -- default idx 2 (MMCM power-up)
    signal apply_arm   : std_logic := '0';
    signal sen_tgl     : std_logic := '0';
    signal ms_ptmr     : std_logic_vector(19 downto 0) := (others => '0');
    -- CDC of the picked index + apply into the clk100 / DRP domain
    signal idx_s0, idx_s1 : std_logic_vector(3 downto 0) := "0010";
    signal sen_s          : std_logic_vector(2 downto 0) := "000";
    signal clkgen_mode_idx : std_logic_vector(3 downto 0);  -- = idx_s1 (drives drp + timing rom)

    -- mode_timing_rom outputs + conversion to vga's start/end/max format.
    signal mt_hact, mt_hfp, mt_hs, mt_hbp : std_logic_vector(11 downto 0);
    signal mt_vact, mt_vfp, mt_vs, mt_vbp : std_logic_vector(11 downto 0);
    signal mt_hpol, mt_vpol : std_logic;
    signal mt_pclk : std_logic_vector(16 downto 0);
    signal mt_refr : std_logic_vector(7 downto 0);

    -- Mode decision exposed to the host (uart_ctrl regs 0x20..0x2A). mode_select runs
    -- on clk10, so its supported mask / valid flag are 2FF-synced into clk100 here.
    -- Quasi-static: they only change when a new EDID is parsed (~0.1 s cadence), so a
    -- torn sample is not a concern for a diagnostic read.
    signal ms_supported : std_logic_vector(13 downto 0);
    signal mode_bus_s0, mode_bus_s1 : std_logic_vector(15 downto 0) := (others => '0');

    -- MODEFORCE (reg 0x14): {7:force_en, 3..0:idx}. Pins the offline mode, overriding the
    -- EDID pick -- the only way to reach a mode the EDID would never steer to.
    signal mode_force_bus : std_logic_vector(7 downto 0);
    signal mode_idx_f, mode_idx_f_q : std_logic_vector(3 downto 0) := "0010";
    signal vg_hStart, vg_hEnd, vg_hMax : std_logic_vector(11 downto 0);
    signal vg_vStart, vg_vEnd, vg_vMax : std_logic_vector(11 downto 0);
begin
    debug_pmod <= debug;
    -- Power-up reset for the EDID merge unit
    process(clk100) begin
        if rising_edge(clk100) then
            if por_cnt /= 15 then por_cnt <= por_cnt + 1; por <= '1';
            else por <= '0'; end if;
        end if;
    end process;

    -- Status telemetry over usb_tx: led status byte + hdmi_io decode dbg + edid_merge state.
    -- led_i bit layout: 7=vsync 6=hsync 5=VPolarity 4=sel 3=mode 2=rdy 1=f_frm 0=trig
    led_i <= vsync & hsync & VPolarity & sel & C1_in(1) & C1_in(0) & f_frm & trig;
    vid_valid <= debug(3) and debug(2);  -- symbol_sync AND pll_locked (passthrough decode valid)
    -- usb_link replaces edid_reader: identical status telemetry on usb_tx, plus the
    -- 0xA5 host control protocol on usb_rx. Stage-2 control/table taps left open for now.
    i_usb_link: usb_link port map (
        clk100 => clk100, led => led_i, dbg => debug, mrg => merge_dbg,
        tlp => tlp_val, tcnt => trig_cnt, olp => olp_val,
        usb_rx => usb_rx, usb_tx => usb_tx,
        phys_sw => newSW, eff_sw => effective_sw,
        sli_ctrl => sli_ctrl_bus, sli_ctrl_en => sli_ctrl_en_w, lut_loaded => open,
        -- Stage-2 table taps. corr is LIVE: pattern_gen reads it as its radiometric
        -- transfer LUT via corr_pat_* below. The lut/lutv (720/1280-entry row/column
        -- cosine) targets remain uploadable + readable over USB but have NO consumer --
        -- pattern_gen is DDS-based with its own internal master cosine and does not need
        -- them; they are vestigial from the old indexMap/LUT ROM design.
        corr_addr => "00000000",    corr_dout => open,
        lut_addr  => "0000000000",  lut_dout  => open,
        lutv_addr => "00000000000", lutv_dout => open,
        corr_pat_addr => pat_lut_din, corr_pat_dout => pat_lut_dout,
        edid_rd_addr => edid_host_addr, edid_rd_data => edid_host_data,
        -- camera line readback: no receiver on the Au, so tie the data in to 0.
        cam_line_addr => open, cam_line_data => "00000000",
        -- offline mode decision. clkgen_mode_idx is the APPLIED index (already the
        -- clk100-synced one that drives the clock + timing), so the host reads the
        -- mode actually in use, not a candidate mode_select may not have applied yet.
        mode_idx_i     => clkgen_mode_idx,
        mode_valid_i   => mode_bus_s1(15),
        mode_edid_ok_i => mode_bus_s1(14),
        mode_refr_i    => mt_refr,
        mode_hact_i    => mt_hact,
        mode_vact_i    => mt_vact,
        mode_pclk_i    => mt_pclk,
        mode_supp_i    => mode_bus_s1(13 downto 0),
        mode_force     => mode_force_bus,
        -- PYTHON 1300 camera element (top-side stack board). SPI mailbox on regs
        -- 0x30..0x36, discrete pins on 0x37/0x38. cam_reset_n comes out of reset LOW,
        -- so the sensor stays held in reset until the host deliberately releases it.
        cam_sck     => cam_sck,
        cam_mosi    => cam_mosi,
        cam_ss_n    => cam_ss_n,
        cam_miso    => cam_miso,
        cam_reset_n => cam_reset_n,
        cam_trigger => cam_trigger,
        cam_monitor => cam_monitor );

    -- Dynamic EDID merge: read the HDMI-OUT display's EDID over its DDC, serve the
    -- intersection {display modes} INTERSECT {60-77MHz passthrough window} to the PC,
    -- and drive hdmi_rx_hpa with the cache-defeat HPD pulse so Windows re-reads.
    i_edid_merge: edid_merge port map (
        clk100       => clk100,
        rst          => por,
        hdmi_tx_rscl => hdmi_tx_scl,
        hdmi_tx_rsda => hdmi_tx_sda,
        hdmi_tx_hpd  => hdmi_tx_hpd,
        hdmi_rx_scl  => hdmi_rx_scl,
        hdmi_rx_sda  => hdmi_rx_sda,
        hdmi_rx_hpa  => hdmi_rx_hpa,
        dbg          => open,
        dbg2         => merge_dbg2,
        mode_rd_addr => mode_rd_addr,
        mode_rd_data => mode_rd_data,
        host_rd_addr => edid_host_addr,
        host_rd_data => edid_host_data,
        edid_ok      => edid_ok );
    merge_dbg <= merge_dbg2(7 downto 0);
    -- LED bus: normal status byte (led_i) when video is live, else the idle
    -- "sign of life" slider. led_i bit layout matches the old per-bit mapping:
    --   7=vsync 6=hsync 5=VPolarity 4=sel 3=mode(C1_in1) 2=rdy(C1_in0) 1=f_frm 0=trig
    -- "Connected" = a real HDMI input is decoding OR a display is present on the
    -- HDMI-OUT (its EDID/HPD). Both are 0 with nothing attached, even while the
    -- offline pattern generator free-runs -- so the idle slider stays steady.
    connected <= vid_valid or merge_dbg2(2);   -- dbg2(2) = edid_merge monitor_present
    i_led_idle: led_idle_anim port map (
        clk100     => clk100,
        connected  => connected,
        status_led => led_i,
        led_out    => led_out );
    led <= led_out;

    C1_out(0)  <= trig; 
    C1_out(1)  <= f_frm;

    
    C2_out(0)  <= trig; C2_out(1)  <= f_frm;
    
 

    
i_hdmi_io: hdmi_io port map ( 
        clk100        => clk100,
         clk200        => clk200,
         clk10 => clk10,
         clk125        => clk125,
         clk625        => clk625,
        ---------------------
        -- Control signals
        ---------------------
        clock_locked     => open,
        data_synced      => open,
        debug            => debug,
        sel => sel, 
        ---------------------
        -- HDMI input signals
        ---------------------
        hdmi_rx_cec   => hdmi_rx_cec,
        hdmi_rx_clk_n => hdmi_rx_clk_n,
        hdmi_rx_clk_p => hdmi_rx_clk_p,
        hdmi_rx_p     => hdmi_rx_p,
        hdmi_rx_n     => hdmi_rx_n,

        ----------------------
        -- HDMI output signals
        ----------------------
     --   hdmi_tx_cec   => hdmi_tx_cec,
        hdmi_tx_clk_n => hdmi_tx_clk_n,
        hdmi_tx_clk_p => hdmi_tx_clk_p,
--        hdmi_tx_hpd   => hdmi_tx_hpd,
--        hdmi_tx_rscl  => hdmi_tx_rscl,
--        hdmi_tx_rsda  => hdmi_tx_rsda,
        hdmi_tx_p     => hdmi_tx_p,
        hdmi_tx_n     => hdmi_tx_n,     

        
        pixel_clk => pixel_clk,
        -------------------------------
        -- VGA data recovered from HDMI
        -------------------------------
        in_blank        => in_blank,
        in_hsync        => in_hsync,
        in_vsync        => in_vsync,
        in_red          => in_red,
        in_green        => in_green,
        in_blue         => in_blue,
        is_interlaced   => is_interlaced,
        is_second_field => is_second_field,

        -----------------------------------
        -- For symbol dump or retransmit
        -----------------------------------
        audio_channel => audio_channel,
        audio_de      => audio_de,
        audio_sample  => audio_sample,
        
        -----------------------------------
        -- VGA data to be converted to HDMI
        -----------------------------------
        out_blank => out_blank,
        out_hsync => out_hsync,
        out_vsync => out_vsync,
        out_red   => out_red,
        out_green => out_green,
        out_blue  => out_blue,
        
        symbol_sync  => symbol_sync, 
        symbol_ch0   => symbol_ch0,
        symbol_ch1   => symbol_ch1,
        symbol_ch2   => symbol_ch2
    );
 --------------------------------------------
  --   a 200MHz clock for the IDELAY reference
 --------------------------------------------
ref_clk_pll : ref_clk
    port map (
        clk_in  => clk100,
        clk_out => clk200,
        clk125 => clk125_u, clk625 => clk625_u,   -- old static offline clocks: unused now
        clk10 => clk10
    );

    -- OFFLINE pixel/serializer clocks now come from the reconfigurable generator.
    clk125 <= offline_pix;        -- offline pixel + word clock (clk_selector I0)
    clk625 <= offline_pix_x5;     -- offline 5x serializer clock (clk_selector I0)

    clkgen_mode_idx <= mode_idx_f;   -- EDID pick or MODEFORCE override; drives clock + timing ROM

i_drp_clkgen13 : drp_clkgen13 port map (
        clk100   => clk100,
        mode_idx => clkgen_mode_idx,
        sen      => clkgen_sen,
        srdy     => clkgen_srdy,
        pixel_clk       => offline_pix,
        pixel_io_clk_x1 => offline_pix_x1,
        pixel_io_clk_x5 => offline_pix_x5,
        locked   => clkgen_locked );

i_mode_select : mode_select generic map ( CEIL_KHZ => 85000 )
    port map (
        clk => clk10, rst => por, start => ms_start,
        edid_addr => mode_rd_addr, edid_data => mode_rd_data,
        mode_valid => ms_valid, mode_idx => ms_idx,
        o_hact=>open, o_vact=>open, o_hfp=>open, o_hsync=>open, o_hbp=>open,
        o_vfp=>open, o_vsync=>open, o_vbp=>open, o_hpol=>open, o_vpol=>open,
        o_refr=>open, o_pclk_khz=>open, o_supported=>ms_supported );

    -- 2FF sync the quasi-static mode decision clk10 -> clk100 for the host registers.
    process(clk100) begin
        if rising_edge(clk100) then
            mode_bus_s0 <= ms_valid & edid_ok & ms_supported;
            mode_bus_s1 <= mode_bus_s0;
        end if;
    end process;

    -- EDID picker controller (clk10): periodically re-parse the projector EDID; when a
    -- NEW valid mode is picked (and the block-0 checksum is good), latch it and arm an
    -- apply. The SEN edge is raised one clk10 cycle AFTER the index is latched, so the
    -- clk100-synced index is settled before drp_recfg samples it on the SEN edge.
    process(clk10) begin
        if rising_edge(clk10) then
            ms_start   <= '0';
            edid_ok_s0 <= edid_ok; edid_ok_s1 <= edid_ok_s0;   -- 2FF sync edid_ok
            if ms_ptmr = x"F423F" then                          -- ~0.1 s at 10 MHz
                ms_ptmr <= (others => '0'); ms_start <= '1';
            else
                ms_ptmr <= ms_ptmr + 1;
            end if;
            if apply_arm = '1' then
                apply_arm <= '0';
                sen_tgl   <= not sen_tgl;                       -- raise cross-domain apply edge
            elsif ms_valid = '1' and edid_ok_s1 = '1' and ms_idx /= applied_idx then
                applied_idx <= ms_idx;                          -- set index first
                apply_arm   <= '1';                             -- toggle SEN next cycle
            end if;
        end if;
    end process;

    -- CDC into the clk100 / DRP domain: quasi-static index (2FF), then the MODEFORCE
    -- override, then a SEN pulse on ANY change of the resulting index.
    --
    -- SEN used to be derived from sen_tgl (an edge raised by the clk10 EDID-apply logic).
    -- That only fires for the EDID path, so a host-forced index would have retargeted the
    -- timing ROM while leaving the MMCM on the OLD pixel clock -- 1280x1024 geometry
    -- clocked at 78 MHz. Trigger the retune off the applied index itself instead, so both
    -- sources reconfigure the clock. (sen_tgl is now unused.)
    process(clk100) begin
        if rising_edge(clk100) then
            idx_s0 <= applied_idx; idx_s1 <= idx_s0;

            if mode_force_bus(7) = '1' then                     -- 0x14 force_en
                mode_idx_f <= mode_force_bus(3 downto 0);
            else
                mode_idx_f <= idx_s1;                           -- EDID pick
            end if;
            mode_idx_f_q <= mode_idx_f;

            -- 1-clk100 SEN pulse, raised the cycle AFTER the index settles.
            if mode_idx_f /= mode_idx_f_q then clkgen_sen <= '1';
            else                               clkgen_sen <= '0';
            end if;
        end if;
    end process;

 --------------------------------------------
  --   Offline geometry from the curated table (same index as the clock),
  --   converted to the vga generator's start/end/max format.
 --------------------------------------------
i_mode_timing : mode_timing_rom port map (
        mode_idx => clkgen_mode_idx,
        h_active => mt_hact, h_fp => mt_hfp, h_sync => mt_hs, h_bp => mt_hbp,
        v_active => mt_vact, v_fp => mt_vfp, v_sync => mt_vs, v_bp => mt_vbp,
        h_pol => mt_hpol, v_pol => mt_vpol, pclk_khz => mt_pclk, refr => mt_refr );

    vg_hStart <= mt_hact + mt_hfp;
    vg_hEnd   <= mt_hact + mt_hfp + mt_hs;
    vg_hMax   <= mt_hact + mt_hfp + mt_hs + mt_hbp;
    vg_vStart <= mt_vact + mt_vfp;
    vg_vEnd   <= mt_vact + mt_vfp + mt_vs;
    vg_vMax   <= mt_vact + mt_vfp + mt_vs + mt_vbp;

 --------------------------------------------
  --   Create the output VGA pattern with the offline clock + table geometry
 --------------------------------------------
i_DVID_input: vga port map(
     pixelClock => pixel_clk,
           hRez        => mt_hact,  hStartSync => vg_hStart,
           hEndSync    => vg_hEnd,  hMaxCount  => vg_hMax,  hsyncActive => mt_hpol,
           vRez        => mt_vact,  vStartSync => vg_vStart,
           vEndSync    => vg_vEnd,  vMaxCount  => vg_vMax,  vsyncActive => mt_vpol,
           Red       =>local_red,
           Green     =>local_green,
           Blue     =>local_blue,
           hSync    =>local_hsync,
           vSync     =>local_vsync,
           blank     =>local_blank
);

 --------------------------------------------
  --   Pixel-wise alteration
 --------------------------------------------
-- Robust VSYNC polarity: during ACTIVE video (blank=0) vsync sits at its INACTIVE
-- level (stable, many cycles/frame, never the sync pulse). Sample it as VPolarity and
-- XOR to get a clean active-high vsync_Pos.
vsync_Pos <= vsync xor VPolarity;
process(pixel_clk)
begin
    if rising_edge(pixel_clk) then
        rdy_buf    <= C1_in(0);
        -- SLI pattern enable. C1_in(1) is the camera board's "mode" GPIO and is PULLED
        -- LOW in the XDC ("default passthrough for color-bar test"), so with no camera
        -- board attached pattern_gen is disabled and the vga colour bars just pass
        -- through. Reg 0x13 bit6 (mode_en) lets the host force it: bit5 (mode_val) then
        -- drives display_mode instead of the pin -- so the SLI fringes (and the corr
        -- transfer LUT that shapes them) can be exercised over USB with no camera.
        mode_buf   <= sli_sw_p1(4) when sli_sw_p1(5) = '1' else C1_in(1);   -- mode_val / mode_en
        sel_buf<=sel;
        -- REGISTERED offline/passthrough mux (was 6 concurrent combinational muxes).
        -- sel_buf was a high-fanout select at the head of a long combinational path into
        -- pixel_pipe (indexMapV ROM address + TLP compare) -> 81 setup violations at the
        -- fast offline modes (78.67 MHz: 1024x768@75, 800x600@120). Registering the mux
        -- outputs breaks that path; the uniform 1-pixel datapath delay is absorbed in
        -- blanking (all of blank/sync/rgb shift together, so framing stays consistent).
        if sel_buf = '1' then
            blank <= in_blank; vsync <= in_vsync; hsync <= in_hsync;
            red   <= in_red;   green <= in_green; blue  <= in_blue;
        else
            blank <= local_blank; vsync <= local_vsync; hsync <= local_hsync;
            red   <= local_red;   green <= local_green; blue  <= local_blue;
        end if;
        in_vsync_reg  <= in_vsync;
        in_blank_reg  <= in_blank;
        if (blank = '0') then        -- active video: capture vsync's inactive level
            VPolarity <= vsync;
        end if;
        -- 2FF sync the quasi-static USB SLI-control bits clk100 -> pixel_clk.
        -- (clk100<->pixel_clk are async per set_clock_groups, so no extra timing exception.)
        -- packing, MSB..LSB:  6=sw_en  5=mode_en  4=mode_val  3..0=R,G,B,orient
        sli_sw_p0 <= sli_ctrl_bus(7) & sli_ctrl_bus(6) & sli_ctrl_bus(5) & sli_ctrl_bus(3 downto 0);
        sli_sw_p1 <= sli_sw_p0;
    end if;
end process;

-- USB override (reg 0x13 bit7 = sw_en) selects USB R/G/B/orient over the physical switches.
effective_sw <= sli_sw_p1(3 downto 0) when sli_sw_p1(6) = '1' else newSW;

i_processing: pixel_pipe Port map (
        clk => pixel_clk, clk10 => clk10,
        sw =>effective_sw,
        trig =>trig, f_frm=> f_frm, 
        mode=>mode_buf, rdy=> rdy_buf ,
        vid_valid => vid_valid,
        --
        in_blank        => blank,
        in_hsync        => hsync,
        in_vsync        => vsync_Pos, -- for postion tracking and camera control
        vsync => vsync,
        in_red          => red,
        in_green        => green,
        in_blue         => blue,    
        out_blank => out_blank,
        out_hsync => out_hsync,
        out_vsync => out_vsync,
        out_red   => out_red,
        out_green => out_green,
        out_blue  => out_blue,
        tlp_dbg      => tlp_val,
        olp_dbg      => olp_val,
        trig_cnt_dbg => trig_cnt,
        lut_din  => pat_lut_din,
        lut_dout => pat_lut_dout
    );
--Vs  <= out_vsync; 
    -- Swap to this if you want to capture the HDMI symbols
    -- and send them up the RS232 port
    --rs232_tx <= '1';   
    
end Behavioral;