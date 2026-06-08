--From Numato Official Repo
library IEEE;
use IEEE.STD_LOGIC_1164.ALL;
use IEEE.STD_LOGIC_UNSIGNED.ALL;

entity vga is
   generic (
      -- 1280x720 reduced-blanking @ ~120Hz (pixel clk 125 MHz) -- original offline mode
--      hRez       : natural := 1280;
--      hStartSync : natural := 1288;
--      hEndSync   : natural := 1320;
--      hMaxCount  : natural := 1360;
--      hsyncActive : std_logic := '1';
--      vRez       : natural := 720;
--      vStartSync : natural := 749;
--      vEndSync   : natural := 757;
--      vMaxCount  : natural := 763;
--      vsyncActive : std_logic := '1'

      -- 800x600 @ 60Hz (VESA DMT, pixel clk 40 MHz; both syncs positive)
      --   H: 800 active + 40 fp + 128 sync + 88 bp = 1056 total
      --   V: 600 active +  1 fp +   4 sync + 23 bp =  628 total
      --   40e6 / (1056*628) = 60.3 Hz
      hRez       : natural := 800;
      hStartSync : natural := 840;
      hEndSync   : natural := 968;
      hMaxCount  : natural := 1056;
      hsyncActive : std_logic := '1';

      vRez       : natural := 600;
      vStartSync : natural := 601;
      vEndSync   : natural := 605;
      vMaxCount  : natural := 628;
      vsyncActive : std_logic := '1'
   );

    Port ( pixelClock : in  STD_LOGIC;
           Red        : out STD_LOGIC_VECTOR (7 downto 0);
           Green      : out STD_LOGIC_VECTOR (7 downto 0);
           Blue       : out STD_LOGIC_VECTOR (7 downto 0);
           hSync      : out STD_LOGIC;
           vSync      : out STD_LOGIC;
           blank      : out STD_LOGIC);
end vga;

architecture Behavioral of vga is
   type reg is record
      hCounter : std_logic_vector(11 downto 0);
      vCounter : std_logic_vector(11 downto 0);

      red      : std_logic_vector(7 downto 0);
      green    : std_logic_vector(7 downto 0);
      blue     : std_logic_vector(7 downto 0);

      hSync    : std_logic;
      vSync    : std_logic;
      blank    : std_logic;
   end record;

   signal r : reg := ((others=>'0'), (others=>'0'),
                      (others=>'0'), (others=>'0'), (others=>'0'),
                      '0', '0', '0');
   signal n : reg;
begin
   -- Assign the outputs
   hSync <= r.hSync;
   vSync <= r.vSync;
   Red   <= r.red;
   Green <= r.green;
   Blue  <= r.blue;
   blank <= r.blank;

   process(r,n)
   begin
      n <= r;
      n.hSync <= not hSyncActive;
      n.vSync <= not vSyncActive;

      -- Count the lines and rows
      if r.hCounter = hMaxCount-1 then
         n.hCounter <= (others => '0');
         if r.vCounter = vMaxCount-1 then
            n.vCounter <= (others => '0');
         else
            n.vCounter <= r.vCounter+1;
         end if;
      else
         n.hCounter <= r.hCounter+1;
      end if;

      if r.hCounter  < hRez and r.vCounter  < vRez then
         -- MimasA7 testpattern.v: red = horizontal ramp, green = vertical ramp,
         -- blue = hpos xor vpos (XOR checker reveals geometry / tearing / dead channel)
         n.red   <= r.hCounter(7 downto 0);
         n.green <= r.vCounter(7 downto 0);
         n.blue  <= r.hCounter(7 downto 0) xor r.vCounter(7 downto 0);
         n.blank <= '0';
      else
         n.red   <= (others => '0');
         n.green <= (others => '0');
         n.blue  <= (others => '0');
         n.blank <= '1';
      end if;

      -- Are we in the hSync pulse?
      if r.hCounter >= hStartSync and r.hCounter < hEndSync then
         n.hSync <= hSyncActive;
      end if;

      -- Are we in the vSync pulse?
      if r.vCounter >= vStartSync and r.vCounter < vEndSync then
         n.vSync <= vSyncActive;
      end if;
   end process;

   process(pixelClock,n)
   begin
      if rising_edge(pixelClock)
      then
         r <= n;
      end if;
   end process;
end Behavioral;
