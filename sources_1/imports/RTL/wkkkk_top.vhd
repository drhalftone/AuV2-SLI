--==============================================================================
-- wkkkk_top.vhd -- the smallest thing that projects WHITE,K,K,K,K.
--
-- HDMI OUTPUT ONLY. There is no receiver, no EDID, no mode selection, no SLI
-- pattern generator, no transfer LUT, no camera, no DDR3, no Ft+ and no UART.
-- The FPGA generates one fixed video mode and one fixed five-frame sequence, and
-- that is the entire design.
--
-- WHY A SEPARATE TOP INSTEAD OF STRIPPING Au2_SLI. Everything removed here is
-- something that could not then explain a bad measurement. The pass-through mux,
-- the fringe generator and the radiometric LUT all sit in the colour path of the
-- full design; the HDMI receiver brings a second, unrelated clock domain and the
-- asynchronous crossings that come with it. For a bring-up piece whose whole job
-- is "did the projector see WHITE,K,K,K,K", none of that should be able to
-- participate. What is not built cannot be the reason.
--
--   clk100 -> drp_clkgen13 (mode 0) -> pixel_clk, x1, x5
--          -> vga (timing)          -> hsync / vsync / blank
--          -> impulse_gen           -> WHITE on phase 0, black on 1..4
--          -> DVID_output           -> TMDS -> OBUFDS
--
-- THE MODE IS 800x600@120, curated index 0. Geometry comes from mode_timing_rom
-- rather than being typed in here, so mode_table.vh stays the single source of
-- truth -- a hand-copied front porch that drifts from the table is a bug that
-- looks like a projector fault.
--
-- 960 x 636 x 120 Hz = 73.267 MHz, which is the table's 73270 kHz. That is NOT
-- generatable from 100 MHz by a static MMCM (M/D would need 7.327, and M moves in
-- steps of 0.125), which is exactly why the clock comes from the DRP generator
-- rather than a plain PLL. Do not "simplify" it into a static MMCM.
--
-- SEN IS PULSED ONCE, AFTER RESET. drp_clkgen13 powers up configured for idx 13
-- (108 MHz, the fastest mode) so that static timing analysis signs off against
-- the worst-case serialiser clock. It must be retargeted to idx 0 at boot or the
-- output runs at the wrong rate entirely.
--==============================================================================
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.NUMERIC_STD.ALL;
library UNISIM;
use UNISIM.VComponents.all;

entity wkkkk_top is
    generic (
        MODE_IDX : integer := 0;      -- 0 = 800x600@120, mode_table.vh order
        CYCLE    : integer := 5       -- 1 white frame + 4 black
    );
    port (
        clk100        : in  STD_LOGIC;
        led           : out STD_LOGIC_VECTOR(7 downto 0);
        hdmi_tx_clk_p : out STD_LOGIC;
        hdmi_tx_clk_n : out STD_LOGIC;
        hdmi_tx_p     : out STD_LOGIC_VECTOR(2 downto 0);
        hdmi_tx_n     : out STD_LOGIC_VECTOR(2 downto 0)
    );
end wkkkk_top;

architecture rtl of wkkkk_top is

    component drp_clkgen13 is
        port ( clk100 : in std_logic;
               mode_idx : in std_logic_vector(3 downto 0);
               sen : in std_logic;
               srdy : out std_logic;
               pixel_clk : out std_logic;
               pixel_io_clk_x1 : out std_logic;
               pixel_io_clk_x5 : out std_logic;
               locked : out std_logic );
    end component;

    component mode_timing_rom is
        port ( mode_idx : in std_logic_vector(3 downto 0);
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

    component vga is
        Port ( pixelClock : in  STD_LOGIC;
               hRez, hStartSync, hEndSync, hMaxCount : in STD_LOGIC_VECTOR(11 downto 0);
               hsyncActive : in STD_LOGIC;
               vRez, vStartSync, vEndSync, vMaxCount : in STD_LOGIC_VECTOR(11 downto 0);
               vsyncActive : in STD_LOGIC;
               Red, Green, Blue : out STD_LOGIC_VECTOR(7 downto 0);
               hSync, vSync, blank : out STD_LOGIC );
    end component;

    component impulse_gen is
        generic ( CYCLE : integer );
        port ( pclk : in std_logic; vsync_pos : in std_logic; en : in std_logic;
               level : out std_logic_vector(7 downto 0);
               phase : out std_logic_vector(2 downto 0);
               phase0 : out std_logic );
    end component;

    component DVID_output is
        Port ( pixel_clk, pixel_io_clk_x1, pixel_io_clk_x5, data_valid : in std_logic;
               vga_red, vga_green, vga_blue : in std_logic_vector(7 downto 0);
               vga_blank, vga_hsync, vga_vsync : in std_logic;
               outclk_p : out std_logic;
               tmds_out_clk, tmds_out_ch0, tmds_out_ch1, tmds_out_ch2 : out std_logic );
    end component;

    constant IDX : std_logic_vector(3 downto 0) :=
        std_logic_vector(to_unsigned(MODE_IDX, 4));

    signal pixel_clk, pix_x1, pix_x5, clk_locked, srdy : std_logic;
    signal sen        : std_logic := '0';
    signal boot_cnt   : unsigned(19 downto 0) := (others => '0');
    signal sen_done   : std_logic := '0';

    signal mt_hact, mt_hfp, mt_hs, mt_hbp : std_logic_vector(11 downto 0);
    signal mt_vact, mt_vfp, mt_vs, mt_vbp : std_logic_vector(11 downto 0);
    signal mt_hpol, mt_vpol : std_logic;
    signal vg_hStart, vg_hEnd, vg_hMax : std_logic_vector(11 downto 0);
    signal vg_vStart, vg_vEnd, vg_vMax : std_logic_vector(11 downto 0);

    signal l_red, l_green, l_blue : std_logic_vector(7 downto 0);
    signal l_hsync, l_vsync, l_blank : std_logic;
    signal vsync_pos : std_logic;
    signal imp_level : std_logic_vector(7 downto 0);
    signal imp_phase : std_logic_vector(2 downto 0);
    signal imp_ph0   : std_logic;

    signal tmds_clk, tmds_c0, tmds_c1, tmds_c2 : std_logic;
    signal heartbeat : unsigned(25 downto 0) := (others => '0');
begin

    ----------------------------------------------------------------------------
    -- Clock: retarget the DRP generator to MODE_IDX once, shortly after power-up.
    -- The delay is not superstition -- drp_recfg must see a stable clk100 and the
    -- MMCM must have had a chance to lock at its power-up parameters before the
    -- reconfiguration sequence is started.
    ----------------------------------------------------------------------------
    process(clk100)
    begin
        if rising_edge(clk100) then
            sen <= '0';
            if sen_done = '0' then
                if boot_cnt = to_unsigned(1000000, 20) then   -- ~10 ms at 100 MHz
                    sen      <= '1';
                    sen_done <= '1';
                else
                    boot_cnt <= boot_cnt + 1;
                end if;
            end if;
            heartbeat <= heartbeat + 1;
        end if;
    end process;

    i_clk : drp_clkgen13 port map (
        clk100 => clk100, mode_idx => IDX, sen => sen, srdy => srdy,
        pixel_clk => pixel_clk, pixel_io_clk_x1 => pix_x1,
        pixel_io_clk_x5 => pix_x5, locked => clk_locked );

    ----------------------------------------------------------------------------
    -- Geometry from the curated table, converted to vga's start/end/max form.
    ----------------------------------------------------------------------------
    i_rom : mode_timing_rom port map (
        mode_idx => IDX,
        h_active => mt_hact, h_fp => mt_hfp, h_sync => mt_hs, h_bp => mt_hbp,
        v_active => mt_vact, v_fp => mt_vfp, v_sync => mt_vs, v_bp => mt_vbp,
        h_pol => mt_hpol, v_pol => mt_vpol, pclk_khz => open, refr => open );

    vg_hStart <= std_logic_vector(unsigned(mt_hact) + unsigned(mt_hfp));
    vg_hEnd   <= std_logic_vector(unsigned(mt_hact) + unsigned(mt_hfp) + unsigned(mt_hs));
    vg_hMax   <= std_logic_vector(unsigned(mt_hact) + unsigned(mt_hfp) + unsigned(mt_hs) + unsigned(mt_hbp));
    vg_vStart <= std_logic_vector(unsigned(mt_vact) + unsigned(mt_vfp));
    vg_vEnd   <= std_logic_vector(unsigned(mt_vact) + unsigned(mt_vfp) + unsigned(mt_vs));
    vg_vMax   <= std_logic_vector(unsigned(mt_vact) + unsigned(mt_vfp) + unsigned(mt_vs) + unsigned(mt_vbp));

    i_vga : vga port map (
        pixelClock => pixel_clk,
        hRez => mt_hact, hStartSync => vg_hStart, hEndSync => vg_hEnd,
        hMaxCount => vg_hMax, hsyncActive => mt_hpol,
        vRez => mt_vact, vStartSync => vg_vStart, vEndSync => vg_vEnd,
        vMaxCount => vg_vMax, vsyncActive => mt_vpol,
        Red => l_red, Green => l_green, Blue => l_blue,
        hSync => l_hsync, vSync => l_vsync, blank => l_blank );

    ----------------------------------------------------------------------------
    -- The sequence. vga drives vsync at its ACTIVE level during the pulse, and
    -- 800x600@120 has NEGATIVE vsync polarity (mode_table VPOL = 0), so the raw
    -- pin idles high and pulses low. impulse_gen counts RISING edges, so the raw
    -- signal must be normalised to active-high first or the frame counter would
    -- advance on the wrong edge -- and on a negative-polarity mode that is a
    -- silent off-by-one in the sequence, not an obvious failure.
    ----------------------------------------------------------------------------
    vsync_pos <= l_vsync xor (not mt_vpol);

    i_imp : impulse_gen generic map ( CYCLE => CYCLE )
        port map ( pclk => pixel_clk, vsync_pos => vsync_pos, en => '1',
                   level => imp_level, phase => imp_phase, phase0 => imp_ph0 );

    ----------------------------------------------------------------------------
    -- TMDS out. The colour is the sequence level on all three channels; sync and
    -- blanking come straight from the timing generator, untouched.
    ----------------------------------------------------------------------------
    i_dvid : DVID_output port map (
        pixel_clk => pixel_clk, pixel_io_clk_x1 => pix_x1, pixel_io_clk_x5 => pix_x5,
        data_valid => clk_locked,
        vga_red => imp_level, vga_green => imp_level, vga_blue => imp_level,
        vga_blank => l_blank, vga_hsync => l_hsync, vga_vsync => l_vsync,
        outclk_p => open,
        tmds_out_clk => tmds_clk, tmds_out_ch0 => tmds_c0,
        tmds_out_ch1 => tmds_c1, tmds_out_ch2 => tmds_c2 );

    o_clk : OBUFDS port map ( O => hdmi_tx_clk_p, OB => hdmi_tx_clk_n, I => tmds_clk );
    o_d0  : OBUFDS port map ( O => hdmi_tx_p(0), OB => hdmi_tx_n(0), I => tmds_c0 );
    o_d1  : OBUFDS port map ( O => hdmi_tx_p(1), OB => hdmi_tx_n(1), I => tmds_c1 );
    o_d2  : OBUFDS port map ( O => hdmi_tx_p(2), OB => hdmi_tx_n(2), I => tmds_c2 );

    ----------------------------------------------------------------------------
    -- LEDs: enough to tell "dead" from "running but not what you expected"
    --   0 clock locked      1 DRP retune acknowledged   2 heartbeat on clk100
    --   3 white frame       6..4 sequence phase         7 vsync
    ----------------------------------------------------------------------------
    led(0) <= clk_locked;
    led(1) <= sen_done;
    led(2) <= heartbeat(25);
    led(3) <= imp_ph0;
    led(6 downto 4) <= imp_phase;
    led(7) <= vsync_pos;

end rtl;
