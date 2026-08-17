# PC ↔ FPGA USB communications — complete inventory

_Compiled 2026-08-17 from source, ahead of merging the HDMI/SLI and camera designs._

Every message exchanged with the PC, across both USB ports, for both functions.

**Port A** = Pt V2 onboard FT2232H ch.B → host `COM6`. Present in every build.
**Port B** = Ft+ element board FT601Q → host D3XX, SuperSpeed. Only when the Ft+ is stacked.

`→` = PC to FPGA · `←` = FPGA to PC

| # | Port | Dir | Build | Function | Transaction | Wire format | Reply | Rate / size | Notes |
|---|---|---|---|---|---|---|---|---|---|
| 1 | A | ← | SLI | HDMI | Status telemetry | ASCII `S=s V=v T=t F=f M=m R=r N=nnnn L=hh D=hh G=hh P=hh C=hh O=hh` | — | ~2 lines/s, 115200 | `N`=VSYNC edges/window, `D`=symbol-sync/PLL-lock, `P`/`O`=datapath red |
| 2 | A | → | SLI | both | Write register | `A5 57 ADDR DATA CK` | `K` ok / `N` RO/undef / `E` bad CK | 5 B | `CK` sums payload to 0 mod 256; `A5` excluded |
| 3 | A | → | SLI | both | Read register | `A5 52 ADDR CK` | `ADDR DATA CK2` | 4 B | replies interleave with telemetry; command replies win |
| 4 | A | → | SLI | HDMI | Upload table | `A5 5B TGT D[0..N-1] CK` | `K` / `E` | up to 1283 B | `TGT 0x03` rejected (read-only) |
| 5 | A | → | SLI | HDMI | Read table | `A5 72 TGT CK` | `TGT D[0..N-1] CK2` | up to 1282 B | |
| 6 | A | ↔ | SLI | HDMI | Table `0x00` row LUT | via #4 / #5 | | 720 B | |
| 7 | A | ↔ | SLI | HDMI | Table `0x01` column LUT | via #4 / #5 | | 1280 B | |
| 8 | A | ↔ | SLI | HDMI | Table `0x02` intensity correction | via #4 / #5 | | 256 B | |
| 9 | A | ← | SLI | HDMI | Table `0x03` display EDID | via #5 only | | 256 B | **read-only**; captured off the HDMI *output* DDC |
| 10 | A | ← | SLI | HDMI | Reg `0x00` ID | via #3 | `0x48` | 1 B | proves the control bitstream is loaded |
| 11 | A | ← | SLI | HDMI | Reg `0x01` VERSION | via #3 | `0x01` | 1 B | protocol version |
| 12 | A | ← | SLI | HDMI | Reg `0x02` STATUS | via #3 | `{vsync,hsync,VPol,sel,mode,rdy,f_frm,trig}` | 1 B | same byte as the LEDs |
| 13 | A | ← | SLI | HDMI | Reg `0x06` FLAGS | via #3 | `{…, usb_sw_en, lut_loaded}` | 1 B | |
| 14 | A | ← | SLI | HDMI | Reg `0x10` PINS | via #3 | `{eff_sw[3:0], phys_sw[3:0]}` | 1 B | active vs physical switches |
| 15 | A | ↔ | SLI | HDMI | Reg `0x13` SLICTRL | via #2 / #3 | `{sw_en,mode_en,mode_val,R,G,B,orient}` | 1 B | overrides switch pins and camera `mode` GPIO |
| 16 | A | ↔ | SLI | HDMI | Reg `0x14` MODEFORCE | via #2 / #3 | `{force_en, idx[3:0]}` | 1 B | pins offline mode, overriding the EDID pick |
| 17 | A | ↔ | SLI | HDMI | Reg `0x15` LINKCTL | via #2 / #3 | `{secs×2[5:0], proj, host}` | 1 B | self-timed HDMI disconnect pulse |
| 18 | A | ← | SLI | HDMI | Reg `0x20` MODE | via #3 | `{valid, edid_ok, mode_idx[3:0]}` | 1 B | curated-table index in use |
| 19 | A | ← | SLI | HDMI | Reg `0x21` REFR | via #3 | refresh Hz | 1 B | |
| 20 | A | ← | SLI | HDMI | Reg `0x22`–`0x23` HACT | via #3 | active pixels, lo/hi | 2 B | 12-bit |
| 21 | A | ← | SLI | HDMI | Reg `0x24`–`0x25` VACT | via #3 | active lines, lo/hi | 2 B | 12-bit |
| 22 | A | ← | SLI | HDMI | Reg `0x26`–`0x28` PCLK | via #3 | pixel clock kHz, lo/mid/hi | 3 B | 17-bit |
| 23 | A | ← | SLI | HDMI | Reg `0x29`–`0x2A` SUPP | via #3 | supported-mode mask | 2 B | 14-bit, bit *i* = table index *i* |
| 24 | A | ↔ | SLI | camera | Reg `0x30`–`0x36` CAM_SPI | via #2 / #3 | addr, `{rw,addr[8]}`, wdata×2, go/status, rdata×2 | 7 B | PYTHON 1300 SPI mailbox, 9-bit addr / 16-bit data |
| 25 | A | ↔ | SLI | camera | Reg `0x37` CAM_GPIO | via #2 / #3 | `{reset_n, trigger[2:0]}` | 1 B | resets to `0x00` — sensor held in reset |
| 26 | A | ← | SLI | camera | Reg `0x38` CAM_MON | via #3 | `{monitor[1:0]}` | 1 B | |
| 27 | A | ↔ | SLI | camera | Reg `0x39` CAM_BOOT | via #2 / #3 | W = start; R = `{ready,busy,failed,pll_timeout}` | 1 B | ROM-driven sensor boot; owns SPI + reset while busy |
| 28 | A | ← | camera | camera | Status word | 32 hex chars + CRLF, 128 bits MSB-first | — | ~10/s, **1 Mbaud** | **TX only, no receive path.** Fields below |
| 29 | B | ← | camera | camera | Frame stream, pipe `0x82` | 32 B header + payload, contiguous | — | ~120/s, 1,638,432 B each | Header fields below |
| 30 | B | → | camera | camera | Cmd op `1` exposure0 | `[31:28]=1, [15:0]=units` | none | 4 B | units of 375 ns; **clamp ≤ 8280 µs at 120 Hz** |
| 31 | B | → | camera | camera | Cmd op `2` trigger period | `[31:28]=2, [23:0]=cycles` | none | 4 B | 100 MHz cycles; must be > 1000 |
| 32 | B | → | camera | camera | Cmd op `3` re-arm capture | `[31:28]=3` | none | 4 B | scan mode; in LIVE it only resets the ring |
| 33 | B | → | camera | camera | Cmd op `4` frames per scan | `[31:28]=4, [5:0]=n` | none | 4 B | 1..`MAXF` |

### Row 28 — camera status word fields (128 bits, MSB first)

| bits | field | meaning |
|---|---|---|
| 127:125 | `stw[2:0]` | writer FSM state |
| 124 | reader busy | reader is streaming a frame |
| 123 | `init_calib_complete` | MIG calibrated |
| 122 | `aligned` | LVDS word alignment locked |
| 121 | `streaming` | sensor configured and streaming |
| 120 | `cap` | capture armed |
| 119 | `cfifo_ovf` | camera→DDR FIFO overflowed (sticky) |
| 118 | `ufifo_ovf` | DDR→USB FIFO overflowed (sticky) |
| 117 | `ufifo_empty` | DDR→USB FIFO drained |
| 116 | `txe` | FT601 back-pressuring |
| 115:88 | `wtot` | wordclk cycles over a 24-frame window |
| 87:68 | `wmin` | shortest frame interval in the window |
| 67:48 | `wmax` | longest frame interval in the window |
| 47:32 | `expo_cur` | exposure0 applied, 375 ns units |
| 31:16 | `cmd_count` | commands received — **the only ack for rows 30–33** |
| 15:12 | `rx_dbg` | FT601 receive-path debug |
| 11:6 | `nframes` | frames per scan |
| 5:2 | `cal_retry` | MIG calibration retries |
| 1 | `LIVE` | ring-buffer live mode compiled in |
| 0 | `CONCURRENT` | concurrent capture/playback compiled in |

### Row 29 — frame header (8 little-endian uint32)

| word | contents |
|---|---|
| `h[0]` | `MAGIC` = `0x30494C53` |
| `h[1]` | `frame_idx` — free-running, proves freshness |
| `h[2]` | `{NROW[15:0], NCOL[15:0]}` |
| `h[3]` | `{2'b0, ldrop[15:0], nframes[5:0], 2'b0, slot[5:0]}` |
| `h[4]` | `FBYTES` — payload length (1,638,400 packed 10-bit) |
| `h[5]` | `FBYTES/4` |
| `h[6]` | payload format: `2` = 10-bit in u16, `3` = dense packed 10-bit |
| `h[7]` | `~MAGIC` — validates the header against payload aliasing |

---

## What the merge must reconcile

| # | Issue | Detail |
|---|---|---|
| 1 | **`usb_tx` (AA21)** | The only contested pin in the system. Rows 1–27 (115200, bidirectional) vs row 28 (1 Mbaud, TX-only). One wire. |
| 2 | Two camera boot sequencers | Row 27 (host-initiated) vs `cam_boot_stage1` (automatic at power-up). Both own SPI + `cam_reset_n`. |
| 3 | Two LVDS receiver chains | Both designs instantiate one; collapse to a single instance. |
| 4 | Clocking / CMT budget | HDMI MMCMs + camera MMCM + MIG + the `BUFR` regional clock, against 6 CMTs on the 100T. |
| 5 | Rows 30–33 have no reply path | Fire-and-forget on port B; the only ack is `cmd_count` on **port A** — an existing cross-port dependency. |

**Not an issue — pins and area.** DDR3's 48 pins collide with nothing; the HDMI design's 67
pins and the Ft+'s 45 are disjoint but for `usb_tx`; the 34 pins HDMI shares with the camera
are the *same signals*. Combined ≈ 35% of the 100T's fabric, ~55% of its I/O.

## Constraints any plan must respect

| Constraint | Consequence |
|---|---|
| Exposure ≤ 8280 µs at 120 Hz | Past it the rate halves; at 8300 µs delivery stops. Over-exposing **wedges the sensor** — restoring a valid exposure does not recover it, nor does re-arm. Needs an FPGA reconfigure. Any host that sets exposure must clamp. |
| Port B goes silent when the pipeline wedges | Row 28 is the only window into that state. Camera status must not live *only* on port B. |
| Row 2–27 is the Qt application's transport | `lauauboard.cpp` drives the whole 3-D reconstruction host. Breaking it is not a local change. |
