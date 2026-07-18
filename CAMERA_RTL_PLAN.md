# PYTHON 1300 — FPGA Bring-Up Plan

The staged plan for getting the onsemi PYTHON 1300 (NOIP1SN1300A) talking to the FPGA.
**Every step has a test gate that must pass before the next one starts.**

Companion documents:
- [`CAMERA_SENSOR_PROTOCOL.md`](CAMERA_SENSOR_PROTOCOL.md) — every sensor constant, cited to the datasheet
- [`CAMERA_IO_MAP.md`](CAMERA_IO_MAP.md) — pin maps (§1–§7 Pt V2, **§8 Au V2**)
- [`CAMERA_POWER_DESIGN.md`](CAMERA_POWER_DESIGN.md) — the power tree

---

## The shape of it: two tracks, run in parallel

```
   #1 datasheet constants  (done)
        |
        +---- TRACK A: SPI ------------------> runs on the Au V2 you already have
        |     #2 #3 #4 #5* #6 #14
        |
        +---- TRACK B: LVDS receiver --------> pure simulation, NO hardware at all
              #7 #8 #9 #10 #11
                                  \
                                   +--> #12* real pixels  (needs the Pt V2)
   * = hardware gate
```

**Track A runs on the Au.** The sensor's SPI is *asynchronous to its system clock* — it needs no
clock, no PLL, no LVDS, and no configuration (`CAMERA_SENSOR_PROTOCOL.md` §1). So an Au V2 can talk
to it, and **one chip-ID read proves the power tree, the DF40 pin map, the stack pass-through, and
our RTL, all at once** — see `CAMERA_IO_MAP.md` §8.4 for exactly what that does and does not cover.

**Track B needs no hardware.** The hard part of this job is not the I/O — it is training/bitslip,
sync-channel framing, and the de-interleave. All of it can be built and proven against a behavioral
model, which is what keeps Pt availability off the critical path.

---

## Track A — SPI (Au V2)

| # | Milestone | Test gate | Status |
|---|---|---|---|
| **1** | Pin down every protocol constant from the datasheet | No constant appears in RTL that is not traceable to a citation | ✅ `CAMERA_SENSOR_PROTOCOL.md` |
| **2** | `cam_spi_master.v` | Self-checking TB at the max legal 10 MHz | ✅ **686 checks, 0 errors** |
| **3** | SPI mailbox on the `0xA5` UART control plane | Real 115200 bytes in → chip ID out | ✅ **89 checks, 0 errors** |
| **4** | `cam_au2.xdc` (Au V2) + build a bitstream | Synth + impl clean, timing met, DRC clean | ✅ **WNS +2.106 ns; pins verified by readback** |
| **6** | Sensor boot sequencer (ROM-driven register upload) | Sensor reports PLL lock + ready | ✅ **12 checks, 0 errors** (`cam_boot_seq.v`) |
| **5** | 🔴 **HARDWARE GATE — read chip ID `0x50D0`** | The correct value comes back | ⬜ **needs the Au on the bench** |
| **14** | Settle the `clk_pll` 20 ps jitter spec | We know the number, its units, and whether our clock clears it | ⬜ a number to look up / measure |

### Milestone 5 is the one that matters

It is the whole "confirm the PCB, demonstrate working HDL" goal in a single transaction.
**Before powering up:**

1. **Do not populate the `VBSEL_A` strap resistor** on an Au build (`CAMERA_IO_MAP.md` §8.3).
2. **Meter the power tree first, with no FPGA involved** — `vdd_18` must come up *before* `vdd_33`,
   and `vdd_pix` must sit in 3.25–3.35 V. This is the part of the board that was rebuilt repeatedly
   and has only ever been validated in SPICE.
3. Run the 30-second pass-through check in `CAMERA_IO_MAP.md` §7.
4. **Never write sensor register 112 on an Au build** — that powers up the LVDS drivers, and
   `dout0±` lands on the Au's 1.35 V bank 15 (`CAMERA_IO_MAP.md` §8.2).

---

## Track B — the LVDS receiver (simulation, then Pt V2)

| # | Milestone | Test gate | Status |
|---|---|---|---|
| **7** | Behavioral PYTHON LVDS transmitter model (**bit-level**) | Golden decoder recovers a known image | ✅ **258 checks, 0 errors** (`python1300_lvds_model.v`) |
| **8** | `cam_lvds_rx.v` — ISERDES receiver | Deserialised words on all 5 channels; cold start works | ✅ proven in the full chain below |
| **9** | Per-lane training / bitslip alignment FSM | Every lane locks, over many seeds of random skew + bit phase | ✅ **locks from 4 phases + 0.9 ns skew** (`cam_align.v`) |
| **10** | Sync decoder + 4-lane de-interleave | Known image recovered **bit-exact** | ✅ **256 px bit-exact** (`cam_sync_decode.v`) |
| **11** | Line-capture buffer readable over UART | Known line out, checksum correct | ✅ **34 checks, 0 errors** (`cam_line_buf.v`) |
| **12** | 🔴 **HARDWARE GATE — capture a real line of pixels (Pt V2)** | Sane pixels from a known scene | 🟡 **RTL integrated; builds clean on the Pt (WNS +2.023 ns) — needs the bench** |

> The full receive chain — `python1300_lvds_model → cam_lvds_rx → cam_align → cam_sync_decode`
> — recovers a 32×8 test image **bit-exact** in `tb_cam_decode` (258 checks, 0 errors). The chain
> (plus `cam_line_buf` + `cam_boot_seq`) is now **assembled in `Au2_SLI` on the Pt and builds clean**
> (`build_pt.tcl`; commit `3dad19d`). What remains for #12 is the bench test — see §"Pt integration".

### The traps already identified

1. **The de-interleave is NOT a mod-4 split.** It is an 8-pixel kernel with an *alternating parity
   swap* — the sensor's ADC column-sequencer ordering. A naive `pixel[i] → lane[i mod 4]` produces a
   scrambled image that still *looks* like an image. See `CAMERA_SENSOR_PROTOCOL.md` §8.3.
2. **`iocheck/pt_camera_rx.v` is a placement proof, not a receiver.** Its `lvds_clock_in` output is a
   startup deadlock, and we no longer drive that pin at all (PLL mode). The file carries a warning
   header. Do not paste it forward.
3. **A full frame will never fit the UART.** `uart_ctrl`'s `len` is `reg [11:0]` (4095 bytes max) and
   the link is 11.5 kB/s — a 1.3 MB frame would take ~2 minutes. One *line* (1280 B) fits exactly and
   is a fine bring-up instrument. Frames need the Ft+.
4. **"Proven in simulation" ≠ synthesizable — hit during integration.** `cam_line_buf` wrote all 8
   kernel pixels to 8 computed addresses in one cycle. Correct in sim, but that is 8 write ports to one
   BRAM; it *crashed* Vivado synth (`EXCEPTION_ACCESS_VIOLATION` in RAM inference). Fix: store the line
   8 pixels per 64-bit word at `kbase/8` (`kbase` is 8-aligned) — one write port, infers as BRAM.
   `tb_cam_line` re-run byte-exact (34/0). Any sim-only module is suspect until it has been through synth.
5. **`clk100` was never on a global buffer — latent, not camera-specific.** It feeds MMCMs directly, so
   Vivado's auto-inserter skips it; the unbuffered net was tolerable only while the fabric stayed
   compact. Adding `cam_line_buf` (clocked on `clk100`, placed by the bank-13 pins) stretched it across
   the die → **−17 ns skew, timing blown**. Fix: put the whole `sys_clk_pin` domain (fabric + every MMCM
   `CLKIN`) on one explicit BUFG (`clk100_g` in `Au2_SLI.vhd`). Recovered to WNS +2.023 ns.

---

## Pt integration (task #12)

The RTL assembly is **done and builds clean on the Pt** (`build_pt.tcl`, commit `3dad19d`;
synth+impl clean, DRC 0 errors, setup WNS +2.023 ns, hold WHS +0.047 ns). Steps 1–4 below are
complete; **step 5, the bench test, is all that remains** — and it needs the physical Pt board.

1. ✅ **Port `Au2_SLI` to the Pt V2** (`XC7A100T-FGG484`) — task #15. From-scratch re-pin in
   `constrs_1/imports/RTL/Au2_pt.xdc`: every `PACKAGE_PIN` in `Au2.xdc` is invalid on the Pt (different
   die *and* package); HDMI on the Hd's connectors (TX = port 1, RX = port 2), USB on the onboard
   FT2232. Built as Phase 1 (commit `896eb0b`).
2. ✅ **72 MHz MMCM** for `cam_clk_pll` — a `MMCME2_BASE` (D=5, M=54 → VCO 1080 → /15 = 72.000 MHz
   exact), forwarded to the CMOS pin via ODDR+OBUF. **Still open:** its jitter vs the sensor's ≤20 ps
   `clk_pll` spec is unverified (task #14). Not gating — this build does not power the sensor.
3. ✅ **Chain instantiated** `cam_lvds_rx → cam_align → cam_sync_decode → cam_line_buf` on the
   bank-13 LVDS pins, with `cam_line_buf`'s read port wired to `usb_link`'s `cam_line_addr` /
   `cam_line_data` (was tied to 0 on the Au). `cam_lvds_rx` recovers its own 72 MHz word clock
   (BUFIO + BUFR /5) — no IDELAY / clk200 reference needed. `cam_align`/`cam_sync_decode` share a
   wordclk-domain reset counter (`cam_wc_rst`).
4. ✅ **`cam_boot_seq` instantiated with arbitration**, entirely inside `usb_link`:
   - `cam_spi_master` control is MUXed to `cam_boot_seq` while `boot.busy`, else the host mailbox.
   - `cam_reset_n` is MUXed to `cam_boot_seq` while booting, else the mailbox's reg `0x37` bit 7.
   - Host trigger register `0x39` added to `uart_ctrl`: write = go (dropped 'N' if busy),
     read = `{ready, busy, failed, pll_timeout}`. The Qt tool fires it after the chip-ID read.
5. ⬜ **Bench test**: boot the sensor, let the lanes train, capture one line, `readCameraLine()` in the
   Qt tool, plot it. Cover the lens → near-black; bright field → flat; a hard edge → a step in the
   expected column. **A de-interleave error shows as a period-4 comb** — look for it specifically.
   This gate is the first time the LVDS pin plan, `DIFF_TERM`, the SRCC/BUFIO choice, the even-row
   routing, and the within-kernel channel pairing are tested against reality rather than Vivado.

---

## Deliberately NOT in this plan (yet)

These are what you need to **stream** for SLI, not what you need to get the sensor **working**. They
come after #12.

- **The FT601 / Ft+ USB3 datapath — it does not exist in the RTL at all.** Nothing in
  `sources_1/imports/RTL/` speaks FT601; the `0xA5` control plane is a **115200-baud UART**. The
  245.8 MB/s bandwidth budget in the board README is a budget against hardware that is not yet
  implemented. This is its own subproject.
- **10-bit packing.** At the sensor's full 210 fps, packed 10-bit is ~344 MB/s — essentially *at* the
  Ft+'s measured 350 MB/s. The naive thing (10 bits sitting in a 16-bit word) is **393 MB/s and
  busts the ceiling**, so the packer is load-bearing, not an optimisation. **← but see the burst-capture
  spec below: in grab-to-DDR mode the packer is NOT needed, because you never stream at the sensor's
  instantaneous rate.**
- **Locking exposure to the projected pattern.** Drive `trigger[0]` from the outgoing HDMI frame
  timing so each exposure is locked to a projected pattern. This is the entire reason the FPGA sits
  inline on HDMI. **← now specified concretely below (the trigger generator, task #18).**

---

## Streaming subsystem — burst capture to DDR (the SLI capture mode)

**This is the target streaming architecture.** It supersedes the vague "FT601 is its own subproject"
note above with a concrete, sized spec.

### The requirement

> **Grab up to 32 frames at 16 bits/pixel (unpacked), at 120 Hz synchronised to the HDMI projector,
> into the Pt V2's DDR3L — then stream the frames out to the PC as fast as the Ft+ allows.**

This is a **burst-then-drain** flow, not a continuous stream: capture fills DDR at the trigger-locked
rate; drain empties it at the USB ceiling. The two phases are sequential ("grab, *then* stream").

### The numbers — why this fits comfortably

| Quantity | Value | Note |
|---|---|---|
| Frame geometry | 1280 × 1024 = 1,310,720 px | image lines only |
| **Bytes/frame** | **2.5 MiB** (2,621,440 B) | 16-bit/pixel, 10 valid bits + 6 spare — *exactly* 2.5 MiB |
| **32 frames in DDR** | **80 MiB** | **~31 % of the 256 MB DDR** — room to spare (DDR holds ~102 such frames) |
| Capture window | 32 / 120 Hz = **0.267 s** | one exposure per HDMI frame at 120 Hz |
| DDR **write** rate (capture) | 1.31 Mpx × 120 × 2 B = **314.6 MB/s** | trivial vs DDR's ~1.6 GB/s (≈20 %) |
| DDR **read** rate (drain) | Ft+ limited = **~350 MB/s** | "as fast as possible" = USB-bound |
| Drain time | 80 MiB / 350 MB/s = **0.24 s** | |
| **Full grab-then-stream cycle** | **~0.5 s** + arm/handshake | |

### Why 16-bit unpacked is the *right* call here (not a compromise)

The "packer is load-bearing" warning above applies only to **continuous** streaming at the sensor's
instantaneous rate. **Burst-to-DDR removes that constraint entirely:**

- The sensor writes **into DDR**, not into USB — 314.6 MB/s against a 1.6 GB/s controller. Format
  doesn't threaten any ceiling.
- The drain reads DDR at the USB rate regardless of pixel format; unpacked just means moving 80 MiB
  instead of ~64 MiB — **0.24 s vs 0.19 s.** A 50 ms difference on a half-second cycle.
- In exchange, the host gets **byte-aligned, trivially-parsed** 16-bit little-endian pixels. No
  unpacker on the PC side, no 16-px/5-word LCM bookkeeping in the FPGA. **The packer subproject is
  deleted for this mode.**

Store each pixel **LSB-justified** in the 16-bit word (raw value 0–1023, top 6 bits zero), row-major,
1280 cols × 1024 rows.

### Architecture

```
 cam_sync_decode        cap ctrl        write FIFO      ┌─────────┐   read FIFO       FT601 master
 ───────────────      ───────────      ────────────     │  MIG    │  ────────────    ──────────────
  4 px/clk, 10-bit ─► cam_capture ─►  async BRAM   ─►   │  DDR3L  │  async BRAM  ─►  FT245-sync FSM ─► PC
  @ 72 MHz wordclk    (arm, count      72→ui_clk        │ 256 MB  │  ui_clk→100      32-bit @ 100 MHz
                       32 triggers,                      │ 80 MiB  │
                       write N slots)   ┌── trigger[0] ◄─┤ used    │
                                        │                └─────────┘
 outgoing HDMI vsync ─► trig gen ───────┘
 (projector frame, 120 Hz)
```

Three clock domains: **`cam_wordclk` (72 MHz)** write side, **MIG `ui_clk` (~200 MHz)** in the middle,
**`ft_clk` (100 MHz, from the FT601)** drain side. Two small BRAM async FIFOs bridge into and out of
the MIG — DDR is a *store*, not a low-latency FIFO, so these don't go away.

### DDR memory map

| Region | Base | Size | Contents |
|---|---|---|---|
| Frame slots 0–31 | `0x0000_0000` + i × `0x0028_0000` | 2.5 MiB each | one captured frame, row-major 16-bit |
| (spare) | `0x0500_0000`+ | ~176 MiB free | future: dark/gain calibration maps, more frames |

A small **per-frame header** (frame index, the HDMI frame number the trigger fired on, capture-valid
flag) lets the host verify ordering and detect a missed trigger. TBD: header in-band (prepended to
each slot) vs a separate 32-entry table read over the `0xA5` UART.

### New milestones

| # | Milestone | Test gate |
|---|---|---|
| **16** | **MIG / DDR3L bring-up** — instantiate the Xilinx DDR3 controller on bank 15, confirm the Alchitry timing preset (width, MT/s, ui_clk) | Write/read a known pattern across all 256 MB; BIST clean |
| **17** | **FT601 master** (`ft601_master.v`) — FT245 synchronous FIFO FSM | Loop a counter to the PC at ≥ 300 MB/s sustained on the real Ft+ |
| **18** | **Trigger generator** — derive `trigger[0]` from the outgoing HDMI vsync | Scope: one exposure pulse per projected frame, correct phase |
| **19** | **`cam_capture` FSM** — arm on host command, count 32 trigger-locked frames, write N slots to DDR via the write FIFO | Sim: 32 model frames land in the right slots, byte-exact |
| **20** | **Drain FSM** — read slots 0..N-1 → read FIFO → FT601 | 80 MiB out to the PC, byte-exact against what was captured |
| **21** | **Host control block** — arm / frame-count / status / drain-start registers on the `0xA5` plane (or a sibling) + a Qt "grab 32" button | End-to-end: press button → 32 frames on disk in ~0.5 s |
| **22** | 🔴 **HARDWARE GATE** — real 32-frame burst, projector-synced, streamed to PC | Frames decode to real images, ordered, no dropped triggers |

**Longest pole is still the FT601 master (#17)** — the only block with zero existing code and a real
hardware protocol. Recommend building #16 and #17 in parallel (both are independent of the camera
front end), proving each with a synthetic source before wiring `cam_capture` between them.

### Open decisions (sensible defaults chosen; flag to change)

1. **Capture vs drain overlap** — spec'd as *sequential* (grab all 32, then stream), matching "grab
   then stream". Overlapping them (drain slot i while capturing slot i+k) is possible later and would
   hide the drain time behind capture, but adds DDR read/write contention and FSM complexity.
2. **Frame count** — parameter `N_FRAMES`, 1–32; 32 is the spec max. DDR could hold ~102, so 32 leaves
   headroom for calibration maps.
3. **Pixel justification** — LSB-justified (0–1023). Change to MSB-justified (×64) only if the host
   wants a full-scale 16-bit value.
4. **Header placement** — in-band per slot vs a side table (see above). TBD.
