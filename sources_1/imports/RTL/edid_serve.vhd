-- edid_serve.vhd - RAM-backed HDMI DDC EDID slave (drop-in for edid_rom.vhd).
--
-- Identical I2C-slave protocol to edid_rom.vhd (responds to 0xA0/0xA1 reads and the
-- 0x60 segment-pointer write), but the served bytes come from a DOUBLE-BUFFERED RAM
-- instead of the compile-time edid_a/edid_b constants. A builder (edid_builder.v)
-- fills the idle bank over the wr_* port and pulses `commit`; the host always reads
-- the stable active bank, so it never sees a half-built block.
--
-- Single clock domain (clk). Distributed-RAM (async read) is fine here: the host's
-- DDC reads happen at ~100 kHz, far slower than clk.
--
--   bank layout : ram(0..127) = bank 0, ram(128..255) = bank 1   (128-byte blocks)
--   read  index : (active_bank*128) + addr_reg(6..0)             -> host
--   write index : ((not active_bank)*128) + wr_addr(6..0)        -> builder
--   commit      : 1-cycle pulse -> active_bank flips (atomic swap)
--   power-up    : bank 0 initialised to a safe default EDID (served before 1st build)

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

library UNISIM;
use UNISIM.VComponents.all;

entity edid_serve is
   port ( clk        : in    std_logic;
          -- builder write port (same clock domain)
          wr_en      : in    std_logic := '0';
          wr_addr    : in    std_logic_vector(7 downto 0) := (others => '0');
          wr_data    : in    std_logic_vector(7 downto 0) := (others => '0');
          commit     : in    std_logic := '0';   -- 1-cycle: swap to the freshly built bank
          -- debug read-back of the ACTIVE (served) bank, 1-cycle latency
          rdbg_addr  : in    std_logic_vector(7 downto 0) := (others => '0');
          rdbg_data  : out   std_logic_vector(7 downto 0) := (others => '0');
          -- DDC bus to the upstream HDMI source
          sclk_raw   : in    std_logic;
          sdat_raw   : inout std_logic := 'Z';
          edid_debug : out   std_logic_vector(2 downto 0) := (others => '0')
  );
end entity;

architecture Behavioral of edid_serve is

   type ram_t is array (0 to 255) of std_logic_vector(7 downto 0);

   -- Default served EDID (bank 0): the "Qishi-SLI" base block from edid_rom.vhd.
   -- Served until the builder writes a merged block and commits.
   signal ram : ram_t := (
      -- header
      0  => x"00", 1 => x"FF", 2 => x"FF", 3 => x"FF", 4 => x"FF", 5 => x"FF", 6 => x"FF", 7 => x"00",
      -- mfg "CBC", product 0xF207, serial 1, model/year, EDID 1.4
      8  => x"0C", 9 => x"43", 10 => x"07", 11 => x"F2", 12 => x"01", 13 => x"00", 14 => x"00", 15 => x"00",
      16 => x"FF", 17 => x"11", 18 => x"01", 19 => x"04",
      -- HDMI digital 8-bit, aspect/gamma, features
      20 => x"A2", 21 => x"4F", 22 => x"00", 23 => x"78", 24 => x"06",
      -- chromaticity
      25 => x"EE", 26 => x"91", 27 => x"A3", 28 => x"54", 29 => x"4C", 30 => x"99", 31 => x"26",
      32 => x"0F", 33 => x"50", 34 => x"54",
      -- established timings
      35 => x"AD", 36 => x"CE", 37 => x"00",
      -- standard timings
      38 => x"31", 39 => x"7C", 40 => x"4B", 41 => x"FC", 42 => x"45", 43 => x"7C", 44 => x"61", 45 => x"7C",
      46 => x"81", 47 => x"C0", 48 => x"81", 49 => x"00", 50 => x"95", 51 => x"00", 52 => x"A9", 53 => x"C0",
      -- DTD1 1280x720@60
      54 => x"01", 55 => x"1D", 56 => x"00", 57 => x"72", 58 => x"51", 59 => x"D0", 60 => x"1E", 61 => x"20",
      62 => x"6E", 63 => x"28", 64 => x"55", 65 => x"00", 66 => x"0F", 67 => x"48", 68 => x"42", 69 => x"00",
      70 => x"00", 71 => x"1E",
      -- DTD2 800x600@120
      72 => x"9D", 73 => x"1C", 74 => x"20", 75 => x"A0", 76 => x"30", 77 => x"58", 78 => x"24", 79 => x"20",
      80 => x"30", 81 => x"20", 82 => x"34", 83 => x"00", 84 => x"0F", 85 => x"48", 86 => x"42", 87 => x"00",
      88 => x"00", 89 => x"1A",
      -- DTD3 range limits
      90 => x"00", 91 => x"00", 92 => x"00", 93 => x"FD", 94 => x"00", 95 => x"1E", 96 => x"82", 97 => x"1F",
      98 => x"64", 99 => x"0C", 100 => x"01", 101 => x"0A", 102 => x"20", 103 => x"20", 104 => x"20",
      105 => x"20", 106 => x"20", 107 => x"20",
      -- DTD4 name "Qishi-SLI"
      108 => x"00", 109 => x"00", 110 => x"00", 111 => x"FC", 112 => x"00", 113 => x"51", 114 => x"69",
      115 => x"73", 116 => x"68", 117 => x"69", 118 => x"2D", 119 => x"53", 120 => x"4C", 121 => x"49",
      122 => x"0A", 123 => x"20", 124 => x"20", 125 => x"20",
      -- ext flag + checksum
      126 => x"00", 127 => x"BB",
      others => x"00");

   signal active_bank : std_logic := '0';

   -- combinational read of the served byte (active bank, low 128)
   function rd_byte(signal r : ram_t; bank : std_logic; idx : unsigned(7 downto 0))
      return std_logic_vector is
      variable base : integer;
   begin
      if bank = '1' then base := 128; else base := 0; end if;
      return r(base + to_integer(idx(6 downto 0)));
   end function;

   -- ----- DDC slave protocol (unchanged from edid_rom.vhd) -----
   signal sclk_delay  : std_logic_vector(2 downto 0);
   signal sdat_delay  : unsigned(6 downto 0);
   type t_state is ( state_idle, state_start,
                     state_dev7, state_dev6, state_dev5, state_dev4,
                     state_dev3, state_dev2, state_dev1, state_dev0,
                     state_ack_device_write,
                     state_addr7, state_addr6, state_addr5, state_addr4,
                     state_addr3, state_addr2, state_addr1, state_addr0, state_addr_ack,
                     state_selector_ack_device_write,
                     state_selector_addr7, state_selector_addr6, state_selector_addr5,
                     state_selector_addr4, state_selector_addr3, state_selector_addr2,
                     state_selector_addr1, state_selector_addr0, state_selector_addr_ack,
                     state_ack_device_read,
                     state_read7, state_read6, state_read5, state_read4,
                     state_read3, state_read2, state_read1, state_read0, state_read_ack);
   signal state           : t_state := state_idle;
   signal data_out_sr     : std_logic_vector(7 downto 0) := (others => '1');
   signal data_shift_reg  : std_logic_vector(7 downto 0) := (others => '0');
   signal addr_reg        : unsigned(7 downto 0) := (others => '0');
   signal selector_reg    : unsigned(7 downto 0) := (others => '0');
   signal sdat_input      : std_logic := '0';
   signal sdat_delay_last : std_logic := '0';
begin

   i_IOBUF: IOBUF
      generic map (DRIVE => 12, IOSTANDARD => "DEFAULT", SLEW => "SLOW")
      port map (O => sdat_input, IO => sdat_raw, I => '0',
                T => data_out_sr(data_out_sr'high));

   edid_debug(0) <= std_logic(sdat_delay(sdat_delay'high));
   edid_debug(1) <= sclk_raw;

   -- ----- builder write port + atomic bank swap -----
   write_proc : process(clk)
   begin
      if rising_edge(clk) then
         if wr_en = '1' then
            if active_bank = '1' then          -- write the INACTIVE (idle) bank
               ram(to_integer(unsigned(wr_addr(6 downto 0)))) <= wr_data;          -- bank 0
            else
               ram(128 + to_integer(unsigned(wr_addr(6 downto 0)))) <= wr_data;    -- bank 1
            end if;
         end if;
         if commit = '1' then
            active_bank <= not active_bank;     -- swap: host now reads the built bank
         end if;
         -- registered read-back of the active (served) bank
         if active_bank = '1' then
            rdbg_data <= ram(128 + to_integer(unsigned(rdbg_addr(6 downto 0))));
         else
            rdbg_data <= ram(to_integer(unsigned(rdbg_addr(6 downto 0))));
         end if;
      end if;
   end process;

   -- ----- DDC slave FSM (byte-for-byte the edid_rom.vhd logic; only the data
   --       source changed from get_edid() to rd_byte(ram, active_bank, ...)) -----
   process(clk)
   begin
      if rising_edge(clk) then
         if sclk_delay(1) = '1' and sclk_delay(0) = '1' and sdat_delay_last = '1' and sdat_delay(sdat_delay'high) = '0' then
            state <= state_start; edid_debug(2) <= '1';
         end if;
         if sclk_delay(1) = '1' and sclk_delay(0) = '1' and sdat_delay_last = '0' and sdat_delay(sdat_delay'high) = '1' then
            state <= state_idle; selector_reg <= (others => '0'); edid_debug(2) <= '0';
         end if;
         if sclk_delay(1) = '1' and sclk_delay(0) = '0' then
            data_shift_reg <= data_shift_reg(data_shift_reg'high-1 downto 0) & std_logic(sdat_delay(sdat_delay'high));
         end if;
         if sclk_delay(1) = '0' and sclk_delay(0) = '1' then
            data_out_sr <= data_out_sr(data_out_sr'high-1 downto 0) & '1';
            case state is
               when state_start            => state <= state_dev7;
               when state_dev7             => state <= state_dev6;
               when state_dev6             => state <= state_dev5;
               when state_dev5             => state <= state_dev4;
               when state_dev4             => state <= state_dev3;
               when state_dev3             => state <= state_dev2;
               when state_dev2             => state <= state_dev1;
               when state_dev1             => state <= state_dev0;
               when state_dev0             => if data_shift_reg = x"A1" then
                                                 state <= state_ack_device_read;
                                                 data_out_sr(data_out_sr'high) <= '0';
                                              elsif data_shift_reg = x"A0" then
                                                 state <= state_ack_device_write;
                                                 data_out_sr(data_out_sr'high) <= '0';
                                              elsif data_shift_reg = x"60" then
                                                 state <= state_selector_ack_device_write;
                                                 data_out_sr(data_out_sr'high) <= '0';
                                              else
                                                 state <= state_idle;
                                              end if;
               when state_ack_device_write => state <= state_addr7;
               when state_addr7            => state <= state_addr6;
               when state_addr6            => state <= state_addr5;
               when state_addr5            => state <= state_addr4;
               when state_addr4            => state <= state_addr3;
               when state_addr3            => state <= state_addr2;
               when state_addr2            => state <= state_addr1;
               when state_addr1            => state <= state_addr0;
               when state_addr0            => state <= state_addr_ack;
                                              addr_reg  <= unsigned(data_shift_reg);
                                              data_out_sr(data_out_sr'high) <= '0';
               when state_addr_ack         => state <= state_idle;
               when state_selector_ack_device_write => state <= state_selector_addr7;
               when state_selector_addr7   => state <= state_selector_addr6;
               when state_selector_addr6   => state <= state_selector_addr5;
               when state_selector_addr5   => state <= state_selector_addr4;
               when state_selector_addr4   => state <= state_selector_addr3;
               when state_selector_addr3   => state <= state_selector_addr2;
               when state_selector_addr2   => state <= state_selector_addr1;
               when state_selector_addr1   => state <= state_selector_addr0;
               when state_selector_addr0   => state <= state_selector_addr_ack;
                                              selector_reg <= unsigned(data_shift_reg(7 downto 0));
                                              data_out_sr(data_out_sr'high) <= '0';
               when state_selector_addr_ack => state <= state_idle;
               when state_ack_device_read  => state <= state_read7;
                                              data_out_sr <= rd_byte(ram, active_bank, addr_reg);
               when state_read7            => state <= state_read6;
               when state_read6            => state <= state_read5;
               when state_read5            => state <= state_read4;
               when state_read4            => state <= state_read3;
               when state_read3            => state <= state_read2;
               when state_read2            => state <= state_read1;
               when state_read1            => state <= state_read0;
               when state_read0            => state <= state_read_ack;
               when state_read_ack         => if sdat_delay(sdat_delay'high) = '0' then
                                                 state <= state_read7;
                                                 data_out_sr <= rd_byte(ram, active_bank, addr_reg + 1);
                                              else
                                                 state <= state_idle;
                                              end if;
                                              addr_reg <= addr_reg + 1;
               when others                 => state <= state_idle;
            end case;
         end if;
         sdat_delay_last <= sdat_delay(sdat_delay'high);
         sclk_delay <= sclk_raw & sclk_delay(sclk_delay'high downto 1);
         if sdat_input = '0' then
            if sdat_delay(sdat_delay'high) = '1' then sdat_delay <= sdat_delay - 1;
            else sdat_delay <= (others => '0'); end if;
         else
            if sdat_delay(sdat_delay'high) = '0' then sdat_delay <= sdat_delay + 1;
            else sdat_delay <= (others => '1'); end if;
         end if;
      end if;
   end process;
end architecture;
