# Ft+ control API — everything the host can read and write over USB 3

_Current as of 2026-08-24._ Read from the RTL, not from memory: register decode in
[`uart_ctrl.v`](sources_1/imports/RTL/uart_ctrl.v), opcode decode in
[`cam_frame_ft.v`](LauPythonCamera_Pt_Stack/ddr/cam_frame_ft.v).

Supersedes the Ft+ half of [`PC_INTERFACE_INVENTORY.md`](PC_INTERFACE_INVENTORY.md),
which was compiled 2026-08-17 — before M6 put the control plane on the Ft+ at all.

Host implementation: [`host/ftlink.py`](host/ftlink.py).

**Access column:** **R** = read only · **W** = write only · **R/W** = both.

---

## Everything at a glance

**Only these can be WRITTEN.** Everything else on the board is read-only.

| Parameter | Access | Where | Units / encoding |
|---|---|---|---|
| Exposure | **W** | opcode 1 | 375 ns — read back at `0x40`/`0x41` |
| Trigger period | **W** | opcode 2 | 10 ns — observed at `0x3E`/`0x3F` |
| Re-arm capture | **W** | opcode 3 | toggle |
| Frames per scan | **W** | opcode 4 | 1…63 |
| Request status reply | **W** | opcode 5 | toggle |
| Camera idle / reset hold | **W** | opcode 6 | ms, self-timed — observed at `0x3A` bit 1 |
| SLI control + switch override | **R/W** | reg `0x13` | bitfield |
| Offline mode force | **R/W** | reg `0x14` | bitfield |
| HDMI disconnect pulse | **R/W** | reg `0x15` | half-seconds |
| Row cosine LUT | **R/W** | table `0x00` | 720 B |
| Column cosine LUT | **R/W** | table `0x01` | 1280 B |
| Radiometric transfer LUT | **R/W** | table `0x02` | 256 B |

Note the asymmetry: the **camera** is controlled by write-only opcodes with
readback through *different* registers, while the **HDMI/SLI** side uses genuine
read/write registers. They are two protocols sharing one pipe.

---

## The two pipes, and the two kinds of traffic on each

**OUT pipe `0x02`** carries 32-bit little-endian words. The top nibble is a
**camera opcode**, so protocol bytes cannot be sent raw — a byte landing at `0x1?`
in that position would fire opcode 1 and silently rewrite the exposure. Hence:

| Opcode | Carries | Sent by |
|---|---|---|
| `0` | three `0xA5` protocol bytes + count: `{4'd0, count[3:0], b2, b1, b0}` | `FtLink.send_bytes()` |
| `1`–`6` | camera commands, one raw word each | `FtLink.send_word()` |

**IN pipe `0x82`** carries packets with a common 32-byte header, so one parser
handles both:

| `w0` magic | `w6` format | Payload |
|---|---|---|
| `SLI0` `0x30494C53` | 3 | **Video frame**, 1,638,400 B, packed-10 (4 px in 5 bytes) |
| `SLI1` `0x31494C53` | 4 | **Control reply bytes** — a transport chunk, not a message |
| `SLI1` | 1 | Status reply (opcode 5) |

> A reply is a **transport chunk**. The FPGA emits whatever is queued at a frame
> boundary, so one protocol reply can split across two packets and two replies can
> share one. Reassemble a byte stream and let the `0xA5` framing do its own work.

---

## Camera opcodes — all WRITE ONLY

Payload is `word[27:0]`. These go **straight to `cam_frame_ft`**; they are not part
of the `0xA5` protocol and return no acknowledgement. Where a value can be read
back at all, it is through a different register — listed below.

| Opcode | Access | Payload | Parameter | Units | Read back at | Notes |
|---|---|---|---|---|---|---|
| `1` | **W** | `[15:0]` | Exposure | 375 ns | `0x40`/`0x41` | Lands **one frame late** unless `reg_seq_exposure_sync_mode` is set (datasheet p20) |
| `2` | **W** | `[23:0]` | Trigger period | 10 ns | `0x3E`/`0x3F` (measured) | Rejected if ≤ 1000. **Below the ~5840 µs readout floor it wedges the sensor** |
| `3` | **W** | — | Re-arm capture | — | — | Toggle |
| `4` | **W** | `[5:0]` | Frames per scan | frames | — | 1…63; 0 rejected |
| `5` | **W** | — | Request status reply | — | arrives on IN pipe | Toggle |
| `6` | **W** | `[27]` en, `[15:0]` ms | Camera idle / hold in reset | ms | `0x3A` bit 1 (`streaming`) | Self-timed; `ms=0` latches. Added for M3/3b |
| `7`–`15` | — | — | *free* | | | |

---

## Registers — `0xA5` protocol

    read   A5 52 ADDR CK            -> ADDR DATA CK2
    write  A5 57 ADDR DATA CK       -> 'K' ok / 'N' read-only / 'E' bad checksum

`CK` sums the payload to 0 mod 256; `A5` is excluded.

### Identity and top-level state

| Addr | Access | Parameter | Encoding |
|---|---|---|---|
| `0x00` | **R** | ID | `0x48` `'H'` |
| `0x01` | **R** | Version | `0x01` |
| `0x02` | **R** | STATUS | `{vsync, hsync, VPol, sel, mode, rdy, f_frm, trig}` |
| `0x06` | **R** | FLAGS | `{…, usb_sw_en, lut_loaded}` |
| `0x10` | **R** | PINS | `{eff_sw[3:0], phys_sw[3:0]}` — active vs physical switches |

### HDMI / SLI control — the only genuinely read/write registers

| Addr | Access | Parameter | Encoding |
|---|---|---|---|
| `0x13` | **R/W** | SLICTRL | `{7:sw_en, 6:mode_en, 5:mode_val, 3:R, 2:G, 1:B, 0:orient}` |
| `0x14` | **R/W** | MODEFORCE | `{7:force_en, 3:0:idx}` — pin the offline mode, overriding the EDID pick |
| `0x15` | **R/W** | LINKCTL | `{7:2:secs×2, 1:proj, 0:host}` — self-timed HDMI disconnect, 0.5…31.5 s. Reads back `{proj_active, host_active, remaining}` |

### Offline mode decision

What `mode_select` chose from the display's EDID, and what it had to choose from.

| Addr | Access | Parameter | Units |
|---|---|---|---|
| `0x20` | **R** | MODE `{7:valid, 6:edid_ok, 3:0:idx}` | — |
| `0x21` | **R** | Refresh rate | Hz |
| `0x22`/`0x23` | **R** | h_active | pixels (12-bit) |
| `0x24`/`0x25` | **R** | v_active | lines (12-bit) |
| `0x26`/`0x27`/`0x28` | **R** | Pixel clock | kHz (17-bit) |
| `0x29`/`0x2A` | **R** | Supported-mode mask | bit *i* = table index *i* |

### ⚠️ `0x30`–`0x39` — camera SPI mailbox, **DEAD SINCE M2**

| Addr | Access | Parameter | Reality |
|---|---|---|---|
| `0x30`–`0x34` | ~~R/W~~ | SPI addr / rw / wdata / GO | **reaches nothing** |
| `0x35`/`0x36` | ~~R~~ | SPI rdata | **meaningless** |
| `0x37` | ~~R/W~~ | `{reset_n, trigger[2:0]}` | **reaches nothing** |
| `0x38` | **R** | `cam_monitor` pins, live | genuinely connected |
| `0x39` | ~~R/W~~ | Boot sequencer GO / status | **reaches nothing** |

**These accept writes, report busy/done, and return data. None of it reaches the
sensor.** M2 moved sensor ownership into `cam_frame_ft`, and `Au2_SLI.vhd` wires
`cam_sck`, `cam_mosi`, `cam_ss_n`, `cam_reset_n` and `cam_trigger` to `open` —
two drivers on one pin is an elaboration error, so the automatic boot sequencer
won. `cam_miso` *is* still connected, which makes it worse: a transaction looks
like it worked and returns a plausible value that means nothing.

Do not use. Live camera state is at `0x3A`–`0x49`.

### Camera datapath status

| Addr | Access | Parameter | Units / encoding |
|---|---|---|---|
| `0x3A` | **R** | Datapath flags | `{stw[2:0], rd_busy, calib, aligned, streaming, cap}` — healthy is `0x3F` |
| `0x3B` | **R** | FIFO / FT601 flags | `{cfifo_ovf, ufifo_ovf, ufifo_empty, txe, 0000}` |
| `0x3C`/`0x3D` | **R** | **ldrop** — frames arriving short and padded | count; **static means none lost** |
| `0x3E`/`0x3F` | **R** | Camera frame period ÷ 16 | 72 MHz wordclk cycles → µs = `v×16/72` |
| `0x40`/`0x41` | **R** | Exposure currently applied | 375 ns units |

### Sensor timing, measured in fabric *(added 2026-08-24)*

From `monitor0`, which carries **Integration Time** (`monitor_select = 0x1`,
sensor reg 192[13:11] — already set by the boot ROM's `0x0801`).

| Addr | Access | Parameter | Units |
|---|---|---|---|
| `0x42`–`0x44` | **R** | Trigger → integration start, **minimum** over the window | 10 ns |
| `0x45`–`0x47` | **R** | Same, **maximum** | 10 ns |
| `0x48`/`0x49` | **R** | Last integration length | 160 ns |

`max − min` is the exposure-start **jitter**. Measured: 0.01 µs below ~2775 µs
exposure, ~5.3 µs above it.

### Display timing, measured in fabric *(added 2026-08-24)*

Period of **`out_vsync`** — what is sent to the projector, not the incoming source.

| Addr | Access | Parameter | Units |
|---|---|---|---|
| `0x4A`–`0x4C` | **R** | vsync period, last | 10 ns |
| `0x4D`–`0x4F` | **R** | Minimum over the window | 10 ns |
| `0x50`–`0x52` | **R** | Maximum over the window | 10 ns |

`max − min` is the master's jitter. Measured offline: **10 ns**, the measurement
floor.

> `N=` in the Port A telemetry is an edge **count** per window, not a period. It
> dithers between 51 and 52 and cannot resolve better than ~2%.

### Max usable exposure *(added 2026-08-24)*

`max_exposure = vsync_period − 44.1 µs (measured sensor gap) − 10 µs (margin)`

| Addr | Access | Parameter | Units |
|---|---|---|---|
| `0x53`/`0x54` | **R** | **Max exposure** | **exposure register units** — write straight back with opcode 1 |
| `0x55` | **R** | `{7: valid, 6: limited by register range}` | — |
| `0x56`/`0x57` | **R** | Reserve actually subtracted | 10 ns |

- **`valid = 0` means refuse, not default.** An implausible vsync period yields no
  answer, deliberately: an exposure longer than the frame period wedges the sensor
  until reconfigure, which is the failure this exists to prevent.
- **`reg_limited`**: below ~40.7 Hz the 16-bit exposure register (65535 × 375 ns =
  24.576 ms) runs out before the frame period does.
- **It answers for the HDMI rate.** Until genlock exists the camera free-runs on
  `TRIG_CY`, so compare against `0x3E`/`0x3F` before applying — see
  `host/max_exposure.py`.

---

## Tables

    upload  A5 5B TGT D[0..N-1] CK   -> 'K' / 'E'
    read    A5 72 TGT CK             -> TGT D[0..N-1] CK2

| TGT | Access | Table | Bytes |
|---|---|---|---|
| `0x00` | **R/W** | Row cosine LUT | 720 |
| `0x01` | **R/W** | Column cosine LUT | 1280 |
| `0x02` | **R/W** | Radiometric transfer (corr) | 256 |
| `0x03` | **R** | Captured display EDID | 256 — upload rejected with `'E'` |
| `0x04` | **R** | Captured camera line | 1280 |

---

## Frame header — 8 little-endian `uint32`

| Word | Meaning |
|---|---|
| `w0` | magic — `SLI0` frame, `SLI1` reply |
| `w1` | sequence |
| `w2` | frame index (frames) / true byte count (replies) |
| `w4` | **payload bytes to skip** — padded to a whole 128-bit word |
| `w6` | **format** — 2 = 10-bit in u16, 3 = packed-10, 4 = control reply, 1 = status |
| `w7` | `~magic` |

> **Read the format from `w6`, never assume it.** Reading packed bytes as `uint16`
> does not fail loudly — it produces a plausible wrong image.

---

## Known limitation

**The reply path goes silent when the frame stream stops.** Commands still arrive
and the FPGA builds the reply — `0x3B` shows `ufifo_EMPTY=0`, `0x3A` shows
`rd_busy=1` — but no bytes reach the host, and a 900-byte backlog does not flush
it. So **anything that stops the camera also stops Ft+ readback**, including
opcode 6 and a wedged sensor.

Port A remains the independent witness for exactly these cases. M6c's "Port A is
now TX-only" is retracted until this is fixed.
