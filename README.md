# AuV2-SLI — an FPGA structured-light scanner with the camera inside it

A **BYOP (Bring Your Own Projector) machine-vision camera**: an Alchitry **Pt V2**
(Xilinx Artix-7 **XC7A100T-2**) sits inline on HDMI between a PC and a projector, and carries
an **ON Semi PYTHON 1300** global-shutter sensor on a stacked daughter board. One bitstream
generates the structured-light patterns, drives the projector, triggers the sensor at a
commanded delay from the projected vsync, and streams 10-bit frames to the host over an
**Alchitry Ft+** (FT601Q) USB 3.0 link.

The FPGA can:

- **Pass HDMI through** from a PC to the projector, triggering the camera on each new frame.
- **Replace the video with on-board SLI fringes** — resolution-adaptive, generated on the fly.
- **Run fully offline, with no HDMI source at all** — it reads the projector's EDID and drives
  it at a mode the projector actually advertises, up to **1280×1024@60** or **800×600@120**.
- **Genlock capture to projection** — camera triggers are issued a programmable delay after the
  projected vsync, and **stack**, so patterns advance at the full HDMI frame rate instead of at
  (exposure + delay).
- **Stream to the PC over USB 3** — 1280×1024 × 10 bit, byte-exact, up to 120 fps sustained.

> The HDMI datapath is adapted from [hamsternz/Artix-7-HDMI-processing](https://github.com/hamsternz/Artix-7-HDMI-processing) (MIT).
> Original SLI design by Qishi Hu. This fork adds the EDID-driven offline output, the adaptive
> pass-through clocking, the CEA-aware EDID merge, the PYTHON 1300 receive chain, the Ft+ USB 3
> image path, and genlock.

---

## 0. Quick orientation — what this repo is, on one screen

| | |
|---|---|
| **FPGA** | Alchitry **Pt V2**, `xc7a100tfgg484-2` (the original Au V2 / XC7A35T build still exists — `build.tcl`) |
| **Top level** | `sources_1/imports/RTL/Au2_SLI.vhd` (VHDL, instantiating the Verilog subsystems) |
| **Current build** | `build_merged.tcl` → `build_merged/Au2_SLI.{bit,bin}` — HDMI/SLI **+** camera **+** Ft+ |
| **Constraints** | `constrs_1/imports/RTL/Au2_pt.xdc` + `pt_ftplus_merged.xdc` |
| **Camera** | ON Semi **PYTHON 1300** (`NOIP1SN1300A-QTI`), 1280×1024, global shutter, 4-lane LVDS @ 720 Mbps |
| **Camera board** | `LauPythonCamera_Pt_Stack/` — the KiCad board **and** its Verilog (`ddr/`) |
| **Image path** | Alchitry **Ft+** (FT601Q) USB 3.0 — ~325 MB/s measured, 132 fps at 10-bit |
| **Control path** | the same Ft+ (opcode-0 tunnel), **and** a 115200 UART on Port A (COM6) kept as an independent witness |
| **Frame buffer** | DDR3 via MIG — **mandatory**, the sensor reads out in a 4.55 ms burst at 576 MB/s |

**Two control links, on purpose.** Everything the host needs is reachable over the Ft+. Port A's
UART is kept because *the frame stream goes silent exactly when the camera fails* — the link that
can still answer in that state is the one worth having.

### Where to look

| Document | For |
|---|---|
| [`USB_COMMAND_REFERENCE.md`](USB_COMMAND_REFERENCE.md) | **Every get and put** — 11 writable registers, ~120 readable, 6 table targets, the Ft+ opcodes |
| [`FTPLUS_API.md`](FTPLUS_API.md) | The Ft+ control API, marked R / W / R-W |
| [`FRAME_HEADER_PLAN.md`](FRAME_HEADER_PLAN.md) | Per-frame header design (format 3 → 4): pairing, timestamps, `pat_period`. **§1a: the ROI mean now rides in word 1** |
| [`GENLOCK_MILESTONES.md`](GENLOCK_MILESTONES.md) | Locking exposure to the projected frame — G0…G3 |
| [`MERGE_MILESTONES.md`](MERGE_MILESTONES.md) | How HDMI and camera were merged onto one FPGA — M0…M7 |
| [`PT_PASSTHROUGH_DEBUG.md`](PT_PASSTHROUGH_DEBUG.md) | Pass-through bring-up on the Pt, and the bug that broke 640×480 for 16 months |
| [`CAMERA_RTL_PLAN.md`](CAMERA_RTL_PLAN.md) / [`CAMERA_RTL_REVIEW.md`](CAMERA_RTL_REVIEW.md) / [`CAMERA_SENSOR_PROTOCOL.md`](CAMERA_SENSOR_PROTOCOL.md) | The camera receive chain: plan, review, datasheet constants |
| [`PC_INTERFACE_INVENTORY.md`](PC_INTERFACE_INVENTORY.md) | Complete PC ↔ FPGA comms inventory |
| [`ROADMAP.md`](ROADMAP.md) / [`MIPI_CSI2_ROADMAP.md`](MIPI_CSI2_ROADMAP.md) / [`POLARFIRE_PORT_FEASIBILITY.md`](POLARFIRE_PORT_FEASIBILITY.md) | Board stack, a MIPI CSI-2 receiver, a PolarFire port |

---

## 1. RTL module map

Paths relative to `sources_1/imports/RTL/` unless noted.

### 1.1 Top level and clocking

| Module | Role |
|---|---|
| `Au2_SLI.vhd` | **Top.** Instantiates everything; owns mode selection, the DRP retune, `ext_sync`, the boot retune one-shot |
| `clk_selector.v` | Chooses between the recovered pass-through clock and the offline generated clock (`sel`) |
| `drp_clkgen13.v` | The **offline** output-clock MMCM — 13 curated modes, retuned over DRP |
| `drp_recfg.v` | DRP write sequencer for `drp_clkgen13` (per-mode M/D/O words) |
| `rx_drp_recfg.v` | DRP sequencer for the **recovery** MMCM (pass-through side) |
| `rx_freq_band.v` | Picks the recovery-MMCM band from the **measured** input frequency — this is what makes pass-through adaptive rather than fixed-×15 |

Clock domains: `clk100` (control / EDID / USB), the pixel clock and its ×5 (`pixel_io_clk_x5`),
`wordclk` (72 MHz, recovered from the sensor's LVDS), and MIG's `ui_clk`.

### 1.2 HDMI receive (pass-through)

| Module | Role |
|---|---|
| `hdmi_io.vhd` | Wrapper for the input and output halves of the HDMI stream |
| `hdmi_input.vhd` | Decoded-stream assembly: control periods, data islands, sync extraction |
| `input_channel.vhd` | One TMDS channel: deserialise → align → decode (Mike Field) |
| `deserialiser_1_to_10.vhd` | ISERDESE2 1:10 |
| `alignment_detect.vhd` | **Bitslip search.** `signal_quality` accumulates `x"100000"` per error and trips at bit 27 |
| `tmds_decoder.vhd` | 10b → 8b TMDS decode |
| `video_meas.v` | Measures the **incoming** timing: h/v active, frame period, validity (regs `0x60`–`0x6A`) |
| `video_phase_fifo.v` | Elastic phase-compensation buffer across the recovered-clock boundary |

> **`alignment_detect.vhd` is the one to remember.** A dropped hex digit (`x"000100"` instead of
> `x"100000"`, introduced 2025-04-17) made the bitslip search **4096× too slow** — 31 ms per tap
> instead of 7.6 µs. Fast pixel clocks still converged; **640×480 never did.** Restoring the
> upstream constant fixed 640×480@60 and @75 pass-through.

### 1.3 HDMI transmit

| Module | Role |
|---|---|
| `dvid_output.vhd` | DVI/HDMI output assembly (guard bands, preambles, control codes) |
| `tmds_encoder.vhd` | 8b → 10b TMDS encode |
| `serialiser_10_to_1.vhd` | OSERDESE2 10:1 — **the ceiling**: ×5 must stay under ~600 MHz |
| `video_timing_gen_rt.v` | **Runtime-configurable** timing generator for offline mode |
| `mode_timing_rom.v` | Per-mode video geometry lookup, keyed by `mode_idx` |
| `mode_table.vh` | The 14 curated modes (§4) |

### 1.4 EDID

| Module | Role |
|---|---|
| `i2c_master_edid.v` | Bit-banged I²C master; reads the **projector's** EDID off the output DDC |
| `edid_merge.v` | Self-contained merge unit + hot-plug ownership; re-probes every ~0.5 s |
| `edid_builder.v` | Builds the merged block served to the PC (monitor name `FPGA-PT`) |
| `edid_serve.vhd` | RAM-backed DDC **slave** on the input connector |
| `mode_select.v` | Parses the display EDID → supported-mode mask → the pick (regs `0x20`–`0x2A`) |

> `edid_fall` (reg `0x82`) counting **exactly 2/s is healthy** — it is the 0.5 s monitor-presence
> probe, not a fault. This was misdiagnosed twice.

### 1.5 Pattern generation

| Module | Role |
|---|---|
| `pattern_gen.v` | **Resolution-adaptive fringe DDS** (§5) |
| `pixel_pipe.v` | Pattern/pass-through mux, top-left-pixel trigger detection, the ready-paced GPIO handshake |
| `vga.vhd` | The offline test pattern — nested colour squares (`h`, `v`, `h xor v`) |

### 1.6 Camera — PYTHON 1300

RTL under `sources_1/imports/RTL/` and `LauPythonCamera_Pt_Stack/ddr/`.

| Module | Role |
|---|---|
| `cam_lvds_rx.v` | 1:10 LVDS receiver, PLL mode, per-lane IDELAYE2 |
| `cam_align.v` | Per-lane training / bitslip alignment |
| `cam_sync_decode.v` | Sync-channel decode + 4-lane de-interleave (proven bit-exact against the OVC reference) |
| `cam_spi_master.v` | 9-bit-addr / 16-bit-data SPI to the sensor |
| `cam_boot_seq.v` | ROM-driven power-up sequencer |
| `cam_cds_rom.v` | The CDS / sequencer-program upload payload |
| `cam_line_buf.v` | One-line capture buffer (bring-up) |
| `cam_async_fifo.v` | Dual-clock gray-pointer FIFO, **FWFT** |
| `cam_reply_fifo.v` | Control-plane reply bytes, `clk100` → `ui_clk` |
| `ddr/cam_frame_ft.v` | **The datapath.** Camera → DDR3 → FT601. Owns exposure, trigger period, re-arm, frame count, the genlock delay engine, and `cam_stat_o` |
| `ddr/cam_frame_ddr.v`, `ddr/ddr_bist.v`, `ddr/ddr_loop_ft.v`, `ddr/ft_probe_bottom.v` | Bring-up harnesses, kept as isolation tests |

> **The DDR3 is not optional.** The sensor reads a frame out in a **4.55 ms burst at 576 MB/s**
> against a 325 MB/s link. Live video cannot bypass the buffer, and lowering the frame rate does
> not help — the *burst* rate is what exceeds the link.

> `cam_async_fifo`'s `ram_style="block"` is **ineffective** (combinational FWFT read): 0 BRAMs are
> used. FIFO depth is not an available remedy.

### 1.7 Host link

| Module | Role |
|---|---|
| `uart_rx.v` / `uart_tx.v` | 8N1, LSB first |
| `uart_ctrl.v` | The `0xA5` control protocol — register file, table upload/readback |
| `status_line.v` | The one-line telemetry stream |
| `usb_link.v` | Arbitrates telemetry vs. command replies; **derives the max usable exposure** (§7.4) |
| `led_idle_anim.v` | "Sign of life" idle animation over the status LEDs |

---

## 2. Operating modes

### 2.1 Pass-through with top-left-pixel (TLP) trigger
The PC plays the patterns; the FPGA forwards the video unchanged, samples the **top-left pixel** of
each frame, and fires a camera pulse on the next VSYNC when that value changes.

### 2.2 Pass-through with on-board SLI patterns
The FPGA **replaces** the incoming frames with locally generated fringes. Orientation (`SW[0]`),
per-channel enables (`SW[3:1]`) and the frame/frequency sequencing drive it at runtime; all four
switches are overridable over USB (`SLICTRL`, reg `0x13`).

### 2.3 Offline (EDID-driven) — no HDMI source required
With nothing on the input, the FPGA generates everything from its own 100 MHz oscillator. The
output pixel clock **and** the video timing are reconfigured at runtime to match the projector's
EDID: a dedicated `MMCME2_ADV` is retuned over DRP and `video_timing_gen_rt` is loaded from the
curated table.

> **Boot retune one-shot.** On power-up the DRP retune pulse used to be issued before the MMCM had
> locked, and was discarded — a blank screen on every cold boot. `Au2_SLI.vhd` now re-issues
> `clkgen_sen` once at **1 s** after configuration (`boot_cnt = 100_000_000` on `clk100`).
> `boot_cnt` is declared `integer` deliberately: both `STD_LOGIC_ARITH` and `NUMERIC_STD` are in
> scope in that file, so an `unsigned` would be ambiguous.

---

## 3. Genlock — the reason the FPGA is inline

`ext_sync` is the **output** vsync (`Au2_SLI.vhd`: `ext_sync => out_vsync`) delivered into
`cam_frame_ft.v`. Camera triggers are issued a programmable delay after it.

**Triggers stack.** Because the delay and the exposure are both known exactly, the delay engine is a
*fire-time queue*, not a wait-for-ready handshake:

```verilog
localparam [23:0] XS_TIMEOUT = 24'd10_000_000;   // 100 ms with no vsync -> fall back
localparam integer GLQ_AW    = 5;                // 32 outstanding triggers
reg [23:0] gl_now;                               // free-running 10 ns time base
reg [23:0] glq [0:31];                           // absolute fire-times
wire [23:0] gl_late = gl_now - glq[glq_rd];      // top bit CLEAR = deadline passed
wire gl_push = xs_rise && gl_live && !glq_full;
wire gl_pop  = !glq_empty && !gl_late[23];
```

So with a delay longer than a frame, **patterns advance at the full HDMI rate** — the capture of
pattern *k* simply arrives while pattern *k + floor(D/T)* is on screen. That pairing offset is
`floor(D/T)`; read it from reg `0x5E` (outstanding). **Do not assume it** — an offset wrong by one
yields a phase map that looks plausible and is wrong everywhere.

- `gl_en` is what was **commanded**; `gl_live` is whether `ext_sync` is actually arriving. They
  differ exactly when the display has gone away and the trigger has fallen back to free-running.
- Set it over the Ft+: **opcode 7**, `{4'd7, gl_en, 3'b0, delay[23:0]}`, delay in 10 ns ticks.
- Read it back over the UART: `0x5A`–`0x5C` delay, `0x5D` `{gl_live, gl_en}`, `0x5E` outstanding,
  `0x5F` FIFO overflows.

> **Sync must be held across the whole control period.** `hdmi_input.vhd` used to clear
> `raw_vsync`/`raw_hsync` alongside `raw_blank`, so on the 8 of 14 modes with **negative** vsync
> polarity `ext_sync` fired once per *line*. Sync is now assigned only from `ch0_ctl`.

**Now proven, photometrically** — see 3.1. Nothing on the board sees photons, so this was measured
by sweeping the genlock delay across a whole frame and reading the camera's own ROI mean.

### 3.1 Where the light actually is — measured on an Optoma ML750ST

Swept with `host/sli_frame_sweep.py`: white SLI fringes (finest octet) cycling one per frame, the
genlock delay walked across the entire frame period in **1 µs steps, 8326 points**, ROI mean at each.
Projector at **120 Hz**, which on this model forces **3D mode**. Frame period 8325.8 µs.

| | µs |
|---|---|
| first light | **573** |
| last light | **6682** |
| span | **6109** |
| floor / peak | 191 / 890 ADU |

**Use these settings to scan:**

| | value |
|---|---|
| genlock delay | **540 µs** (`0x5A`–`0x5C`, 10 ns ticks → 54000) |
| exposure | **6175 µs** → **16467** units of 375 ns (`0x1A`/`0x1B`) |

That window is `573 − 33` to `6682 + 33`. The 33 µs is not timing jitter: the sensor's effective
exposure floor is somewhere between 20 and 400 µs, so the measured edges are smeared by the sampling
aperture and the true transitions can sit ~30 µs either side. The window closes at 6715 µs, leaving
**1611 µs clear** before the next frame, so widening the margin is free.

What the sweep shows, and why each part matters:

- **The edges are sharp.** Moving the detection threshold from 5 % to 20 % of full scale shifts the
  onset 573 → 576 and the offset 6682 → 6678. These are hard transitions, not ramps, so the delay
  does not need trimming by eye.
- **Five dark gaps sit inside the window** — 284, 153, 151, 160 and 285 µs. A single exposure has to
  span all of them; there is no shorter window that catches the light.
- **A weak shoulder runs 112–573 µs**, peaking 32 ADU over the floor. It is **0.59 % of the frame's
  light** and not worth 460 µs of exposure. Everything outside 573–6682 together is under 1.2 %.
- **Between bursts the signal returns to ~210 ADU over a 191 floor.** Under a *solid* white field the
  same projector never drops below 722 anywhere in the frame. The high "background" in solid-field
  measurements belongs to the content on the screen, not to LEDs that cannot switch off — which is
  why this measurement had to be made with fringes up, not with a flat field.

**Probably true of other Optoma DLP projectors, but do not assume it.** The structure here — bit-plane
PWM in flat-topped rectangles, field-sequential RGB, sub-field groups separated by ~150–285 µs — comes
from the DisplayLink/DLP controller family rather than from this chassis, so the shape should carry
across models. The *numbers* will not: they depend on frame rate, on 3D vs 2D mode, and on the
brightness/colour mode selected. At 60 Hz the frame is 16.7 ms and none of the above applies.

Re-measuring a different projector is two commands and about 40 minutes:

```bash
python -u host/sli_frame_sweep.py COM6 --rgb rgb --octet 2 --orient 0 \
       --step 1 --expo 1 --per 2 --verify-every 50 --live --close \
       --out sli_frameH_white_1us.csv --plot sli_frameH_white_1us.png
python host/plot_white_window.py          # recomputes every number and draws the window
```

`plot_white_window.py` derives the floor, the noise, both onsets, the gaps and the recommended delay
and exposure from the CSV — nothing is written into it — and prints the register values.

> **Two traps when re-measuring.** Use **fringes, not a solid field**: a solid field lifts the floor by
> hundreds of ADU and buries the edges. And check `0x5D` reports genlock live before trusting a sweep —
> a `--ram` bitstream does not survive a power cycle, and the board comes back running its flash image
> with none of this in it.

---

## 4. The curated mode table (`mode_table.vh`)

| idx | Mode | Pixel clock | H pol | V pol |
|---|---|---|---|---|
| 0 | 800×600@120 (CVT-RB) | 73.270 MHz | + | − |
| 1 | 640×480@120 (CVT-RB) | 52.420 MHz | + | − |
| 2 | 1024×768@75 (DMT) | 78.750 MHz | + | + |
| 3 | 800×600@75 | 49.500 MHz | + | + |
| 4 | 640×480@75 | 31.500 MHz | − | − |
| 5 | 1024×768@70 | 75.000 MHz | − | − |
| 6 | 800×600@72 | 50.000 MHz | + | + |
| 7 | 640×480@72 | 31.500 MHz | − | − |
| 8 | 1280×720@60 (CEA/DMT) | 74.250 MHz | + | + |
| 9 | 1280×800@60 (CVT-RB) | 71.110 MHz | + | − |
| 10 | 1024×768@60 | 65.000 MHz | − | − |
| 11 | 800×600@60 | 40.000 MHz | + | + |
| 12 | **640×480@60 — FAILSAFE** | 25.175 MHz | − | − |
| 13 | 1280×1024@60 (DMT) | 108.000 MHz | + | + |

**Selection order is `PRIO[] = {0,1,2,3,4,6,7,5,13,9,8,10,11,12}`** — highest refresh, then highest
pixel count.

> Priority is **deliberately not** the table index. `mode_idx` is a shared key into *both*
> `mode_table.vh` geometry **and** `drp_recfg`'s per-mode MMCM words, so re-sorting the table to
> express priority would hand each mode another mode's pixel clock. `mode_select` walks an explicit
> `PRIO[]` list instead; [`sim/tb_mode_select.v`](sim/tb_mode_select.v) covers it.

The EDID is a **filter, not a source** — a display's preferred timing is *matched against* this
table, never adopted. No match → idx 12.

Pin a mode with `MODEFORCE` (reg `0x14`; `0x8D` = force idx 13).

---

## 5. `pattern_gen.v` — resolution-adaptive fringes

The fringe period is solved **per mode at runtime** from the active size:

```
F     = active size along the varying axis   (orient=0 -> Vactive, orient=1 -> Hactive)
b     = ceil(F / 288)
P_lo  = 288b      P_mid = 48b      P_hi = 8b        (exact 1 : 6 : 36)
```

so the same `frq` index is a **different spatial frequency at every resolution**, and `P_lo` is
re-solved on a mode change. A scan spanning a mode change silently changes fringe period unless the
period travels with the data — which is why `pat_period` is in the planned frame header.

`frq == 3` is **not a fringe**: it selects a flat full-field flashing block for texture/albedo
capture. Sequence: 3 frequencies × 8 phases = 24 frames, plus the flash entries.

**The `corr` table is live in the pixel path.** `pattern_gen` presents its raw cosine to a 256-entry
transfer LUT and uses the result for every **fringe** pixel: `out = corr[cos]`. It powers up
identity, so leaving it unwritten changes nothing. FLASH pixels bypass it and stay true
`0x00`/`0xFF`. The lookup is **combinational by necessity** — a registered BRAM read would apply
each pixel's correction to the *next* pixel — so it synthesises as 256×8 LUTRAM.

`host/upload_corr.py --selftest` proves the LUT reaches the pixels: it uploads a constant curve and
watches the pipe-**output** top-left sample (`O=`) collapse onto that constant while the
pipe-**input** sample (`P=`) does not move.

> **One table, all three channels.** `pattern_gen` computes `pat_out` once and hands the same value
> to red, green and blue (`out_red <= rgb_sel[2] ? pat_out : 0`, and the same for the others). It can
> linearise a grey ramp; it cannot give the three channels *different* values for the same input.
> Doing that is the point of the plan in [14](#14-planned--per-channel-radiometric-calibration), and
> it is the one RTL change that plan needs.

> **`LUT` (0x00, 720 B) and `LUT_V` (0x01, 1280 B) have no consumer** — vestigial from the old
> `indexMap`/ROM design. They upload and read back fine and do nothing.

---

## 6. HDMI clocking, EDID merge, and the two ceilings

### Pass-through
`rx_freq_band.v` measures the incoming frequency and selects the recovery MMCM's band; the
multiplier **follows the input** rather than being fixed at ×15. `BANDWIDTH = HIGH`, so it tracks
the GPU's spread-spectrum clock.

**Verified working:** 640×480@60/@75, 800×600@60, 1024×768@60/70/75, 1280×720@60, 1280×800@60.

> **800×600 pass-through was advertised by arithmetic and never tested** until 2026-08-30; it ran
> the MMCM VCO at its rated minimum. `test_modecycle.py` measures only the **input**, so it reports
> success on a mode the projector never displays. Trust an eye on the projector, not that tool.

> **1280×1024 pass-through was attempted and dropped.** Recovering 108 MHz and re-serialising at
> 540 MHz never held sync — the recovered clock carries the GPU's spread spectrum, and a
> jitter-cleaner MMCM only reached "mostly perfect". Offline SXGA (clean clock, no domain crossing)
> is the supported 1280×1024 route.

### Served EDID
The FPGA reads the **projector's** EDID over the output DDC and serves the PC a **merged** EDID
(monitor name `FPGA-PT`) = `{projector's modes} ∩ {pass-through window}`, with a **60 MHz floor** so
only modes pass-through can actually hold are offered. The merge reads block-0 timings **and** the
CEA-861 extension's Video Data Block, so a TV-style sink advertising 720p60 only as VIC 4 is still
picked up.

### Hot-plug / EDID re-read
The board owns the host HPD line: held **low** until a projector is detected and a merged EDID
built, then asserted. It also **re-pulses HPD (~500 ms low) whenever the merged EDID content
changes** — many projectors serve a placeholder EDID during warm-up, and this makes their real modes
appear without unplugging the PC's cable. Per-build serial and checksum bytes are excluded from the
change detector, so it fires only on real mode changes.

`LINKCTL` (reg `0x15`) forces a self-timed disconnect: **dropping the host HPD is the only reliable
way to force the board offline** — a host-side detach cannot do it.

### Offline ceiling
The output serialiser sets it: **1280×1024@60 (108 MHz, ×5 = 540 MHz) is the top offline mode**,
HW-verified on this −2 part. **1080p60 is NOT reachable** (×5 = 742.5 MHz, over the ~600 MHz
OSERDES/BUFG ceiling; the HDMI I/O is on 3.3 V TMDS HR banks, ~1.2 Gb/s/ch).

---

## 7. The host interface

### 7.1 Two transports, one protocol

The `0xA5` framed protocol is identical on both links.

| Op | Request | Reply |
|---|---|---|
| Write reg | `A5 57 ADDR DATA CK` | `K` ok / `N` read-only or undefined / `E` bad CK |
| Read reg | `A5 52 ADDR CK` | `ADDR DATA CK2` |
| Upload table | `A5 5B TGT D[0..N-1] CK` | `K` / `E` |
| Read table | `A5 72 TGT CK` | `TGT D[0..N-1] CK2` |

`SYNC 0xA5` is never part of a checksum; `CK` drives the running payload sum to 0 mod 256.

**Over the UART** (COM6, 115200 8N1) the bytes go on the wire directly.

**Over the Ft+** they are tunnelled as **opcode-0 words**, three bytes at a time with an explicit
count: `{4'd0, count[3:0], byte2, byte1, byte0}`. They cannot be packed raw — the top nibble of
every OUT word is a camera opcode, so a protocol byte landing at `0x1?` would fire opcode 1 and
silently rewrite the exposure.

Replies come back **interleaved with video** on the IN pipe, with the same 32-byte header shape as a
frame: magic `"SLI1"` (frames are `"SLI0"`), format 4. **A reply is a transport chunk, not a
message** — one reply can split across two packets and two can share one, so the host reassembles a
byte stream and lets the `0xA5` framing do its own work. Replies leave only at **frame boundaries**:
at 120 Hz that is up to ~8.3 ms of latency, by construction. Size timeouts in milliseconds.

### 7.2 Ft+ opcodes (top nibble of each 32-bit OUT word)

| Op | Payload | Effect |
|---|---|---|
| 0 | `{count[3:0], b2, b1, b0}` | **`0xA5` protocol tunnel** — see above |
| 1 | `[15:0]` | Set exposure, in **375 ns** units |
| 2 | `[23:0]` | Set the free-running trigger period (10 ns ticks); ignored if ≤ 1000 |
| 3 | — | **Re-arm** the capture |
| 4 | `[5:0]` | Frames per run (1…`MAXF`) |
| 5 | — | Request a status reply on the IN pipe |
| 7 | `{gl_en, 3'b0, delay[23:0]}` | **Genlock**: enable + vsync→trigger delay in 10 ns ticks |
| 8 | *(planned)* | Arm an epoch reset on the next `ext_sync` — see `FRAME_HEADER_PLAN.md` |

> Opcode 6 is unassigned. Opcode 3 (**re-arm**) is easy to forget and its absence is invisible: a new
> exposure changes nothing the host can see, because DDR still holds the burst captured at the old
> value and capture is one-shot per bitstream load.

### 7.3 Register map

**Writable (11):** `0x13` SLICTRL, `0x14` MODEFORCE, `0x15` LINKCTL, `0x16` CAMSIM,
`0x30`–`0x34` CAM_SPI (addr / `{rw,addr[8]}` / wdata lo / wdata hi / GO), `0x37` CAM_GPIO,
`0x39` CAM_BOOT. Everything else answers `N` to a write.

| Addr | Name | Acc | Meaning |
|---|---|---|---|
| `0x00` | ID | R | constant `0x48` (`'H'`) — the control bitstream is loaded |
| `0x01` | VERSION | R | protocol version (`0x01`) |
| `0x02` | STATUS | R | `{vsync, hsync, VPol, sel, mode, rdy, f_frm, trig}` (same as the LEDs) |
| `0x06` | FLAGS | R | `{…, usb_sw_en, lut_loaded}` |
| `0x10` | PINS | R | `{eff_sw[3:0], phys_sw[3:0]}` — active vs. physical switch pins |
| `0x13` | SLICTRL | R/W | `{7:sw_en, 6:mode_en, 5:mode_val, 3:R, 2:G, 1:B, 0:orient}` |
| `0x14` | MODEFORCE | R/W | `{7:force_en, 3..0:idx}` — pin the **offline** mode (`0x8D` = idx 13) |
| `0x15` | LINKCTL | R/W | `{7..2:secs×2, 1:proj, 0:host}` self-timed disconnect; `0x41` = host-drop 8 s |
| `0x16` | CAMSIM | R/W | host-driven camera-ready, to run the pipeline with no camera |
| `0x20` | MODE | R | `{7:valid, 6:edid_ok, 3..0:mode_idx}` — the index **in use** |
| `0x21` | REFR | R | refresh rate, Hz |
| `0x22`–`0x23` | HACT | R | active pixels, 12-bit |
| `0x24`–`0x25` | VACT | R | active lines, 12-bit |
| `0x26`–`0x28` | PCLK | R | pixel clock in kHz, 17-bit |
| `0x29`–`0x2A` | SUPP | R | 14-bit supported-mode mask (bit *i* = table index *i*) |
| `0x30`–`0x36` | CAM_SPI | R/W | PYTHON 1300 SPI mailbox (`0x34` GO/busy, `0x35`/`0x36` rdata) |
| `0x37` | CAM_GPIO | R/W | `{7:reset_n, 2..0:trigger}` — resets to `0x00` (sensor held in reset) |
| `0x38` | CAM_MON | R | `{1..0:monitor}` |
| `0x39` | CAM_BOOT | R/W | boot sequencer; W starts it (`N` if busy), R = `{ready, busy, failed, pll_timeout}` |
| `0x3A` | CAM_STAT0 | R | `{stw[2:0], rd_busy, calib, aligned, streaming, cap}` — "is it alive" |
| `0x3B` | CAM_STAT1 | R | `{cfifo_ovf, ufifo_ovf, ufifo_empty, txe, 0000}` — "is it healthy" |
| `0x3C`–`0x3D` | LDROP | R | padded frames; **static == none lost** |
| `0x3E`–`0x3F` | CAM_PER | R | frame period **÷ 16**, in 72 MHz wordclk cycles |
| `0x40`–`0x41` | EXPO0 | R | exposure, 375 ns units |
| `0x42`–`0x47` | TRIG→INT | R | trigger → integration start, **min** then **max**, 10 ns units |
| `0x48`–`0x49` | INT_LEN | R | last integration length, 160 ns units |
| `0x4A`–`0x52` | VS_PER | R | vsync period: last / min / max, 10 ns units |
| `0x53`–`0x57` | MAXEXP | R | max usable exposure — §7.4 |
| `0x58`–`0x59` | XS_CNT | R | `ext_sync` edge count, free-running, wraps |
| `0x5A`–`0x5C` | GL_DLY | R | genlock delay, 10 ns ticks |
| `0x5D` | GL_STAT | R | `{6'b0, gl_live, gl_en}` |
| `0x5E` | GL_OUT | R | triggers outstanding = the pairing offset |
| `0x5F` | GL_OVF | R | genlock FIFO overflows (saturates) |
| `0x60`–`0x6A` | RX_MEAS | R | **incoming** HDMI: h/v active, period, `{vid_valid, meas_ok}`, recovered pclk kHz |
| `0x6B`–`0x6C` | RX_DIAG | R | decode diagnostics; phase-FIFO re-primes |
| `0x6D`–`0x80` | CTL/VDP/PRE/GB/EVT | R | control-period, video-data-period, preamble, guard-band and event counters |
| `0x81`–`0x82` | HPD_DIAG | R | HPD-to-PC falling edges; `edid_ok` falling edges |
| `0x83`–`0x8D` | OUT_MEAS | R | **transmitted** timing: h/v active, period, valid, pclk kHz |
| `0x8E`–`0x90` | TRIG_PER | R | the free-running trigger period in effect |

**Read `0x67` first.** If `meas_ok` is 0 the other six read 0 *on purpose* — a stale resolution that
looks live is worse than no answer.

**Min and max are the point** for `0x42`–`0x47` and `0x4A`–`0x52`. The datasheet gives no
trigger-to-exposure delay at all and says integration snaps to a line boundary (~5.7 µs) whenever it
begins during a readout — which in pipelined mode is always. `max − min` **is** that jitter,
measured; a single sample could not show it. The window resets every status tick (~10 Hz), so each
read describes a fresh window rather than an ever-widening high-water mark.

**Table targets** (op `0x5B` upload / `0x72` read):

| TGT | Name | Size | Acc | Contents |
|---|---|---|---|---|
| `0x00` | LUT | 720 B | R/W | vestigial — **no consumer** |
| `0x01` | LUT_V | 1280 B | R/W | vestigial — **no consumer** |
| `0x02` | CORR | 256 B | R/W | the radiometric transfer LUT, live in the pixel path (§5) |
| `0x03` | EDID | 256 B | **R** | the EDID the **display** sent us — what `mode_select` decided from |
| `0x04` | CAM_LINE | 1280 B | **R** | one captured camera line (bring-up) |
| `0x05` | EDID_SRV | 256 B | **R** | the **merged** EDID we serve to the PC |

`0x03` vs. `0x05` is the pair worth knowing: one is what the projector claims, the other is what the
PC is told. `host/offline_mode.py` reads both.

> An EDID readback is always 256 bytes; if the display has no extension block, bytes 128–255 are
> stale RAM. Byte `0x7E` of block 0 is the authoritative extension count.

### 7.4 Max usable exposure — the FPGA works it out itself

`usb_link.v` derives how long the sensor can integrate at whatever rate it is *actually* running:

```verilog
wire        gl_live_s  = cam_stat_i[169];
wire [23:0] trig_per_s = cam_stat_i[215:192];
wire [23:0] eff_per    = gl_live_s ? vsp_last_p : trig_per_s;   // vsync period, or free-run period
```

Reserve = `GAP_TICKS` 4410 (44.1 µs) + `MARGIN_TICKS` 1000 = **54.1 µs**, then scaled from 10 ns into
375 ns register units by `×27962 >> 20`.

- `0x53`/`0x54` — max exposure **in exposure-register units**; write it straight back with opcode 1.
- `0x55` — `{7: valid, 6: limited by the 16-bit register rather than by the frame period}`.
- `0x56`/`0x57` — the reserve subtracted, in 10 ns ticks.

**`valid = 0` means do not guess.** Commanding an exposure longer than the frame period wedges the
sensor until the FPGA is reconfigured — the exact failure this register exists to prevent.

> **Trap:** `0x3E`/`0x3F` holds the camera period **÷ 16** in 16 bits and therefore cannot represent
> anything below **68.67 Hz**. A genlocked 59.9 Hz and 6.16 Hz produce the same reading. Infer
> nothing about genlock from it — read `0x5D`. `host/max_exposure.py` still infers, and is wrong
> below 68.67 Hz.

### 7.5 Telemetry (COM6, 115200 8N1, ~2 lines/s)

```
S=sel V=pol T=trig F=frm M=mode R=rdy N=vsync L=hh D=hh G=hh P=hh C=hh O=hh
```

`S` = pass-through(1)/offline(0), `D` = decode debug (symbol-sync / PLL-lock / sel), `P`/`O` =
recovered vs. pipe-output top-left red, `N` = VSYNC edges per window.

> `N` is an **edge count** and dithers between 51 and 52 at 120 Hz — it cannot resolve the period
> better than ~2% and cannot see jitter at all. Use `0x4A`–`0x52` for that.

---

## 8. Host tools

### 8.1 Over the Ft+ — `ft_usb_video/host/`

Needs `ftd3xx`; only **one** process may hold the D3XX handle at a time.

> **There is exactly one viewer: `cam_live.py`.** Because only one process can hold the D3XX handle,
> a second viewer could only ever be the wrong one to have open. `ft_video_grab.py` used to default
> to a controls-free PySide6 window; that was removed 2026-09-01 and it is now headless. Nothing in
> the repo needs PySide6.

| Tool | Does |
|---|---|
| `cam_live.py` | **The live viewer — the only one in this repo.** tkinter; exposure slider, plus a second row with a *sync to projector* checkbox and a trigger-delay slider. The exposure ceiling tracks the measured rate. Draws the **ROI box** and shows the fabric's ROI mean beside the host's own mean of the same 16×16 pixels — the box goes red when they disagree. `--roi-col8/--roi-row8` if the ROI has been moved off its default |
| `cam_ctl.py` | Exposure, frame rate and re-arm from the command line |
| `campack.py` | Frame geometry, header parsing and 10-bit unpacking — **shared** by the other tools |
| `cam_rate_bench.py` | Frame rate over repeated 24-frame runs |
| `ft_cam_burst.py` | An N-frame burst captured into DDR and streamed out |
| `ft_cam_verify.py` | Byte-exactness of the FT601 bus, plus a PNG |
| `drift_check.py` | Frame-to-frame alignment drift, in kernels |
| `row_align_check.py` | Are delivered frames aligned *with each other*? (row profiles) |
| `ring_check.py` | Frames must be clean, fresh, and cost no lost kernels |
| `scan_latency.py` | Re-arm → time until a complete **new** scan arrives |
| `expo_check.py` | Exposure is settable at runtime; measures the response |
| `ddr_loop_check.py` | Concurrent DDR3 write+read loopback, byte by byte |
| `test_campack.py` | Round-trips the packed-10 layout against a model of the RTL packer |
| `test_m5_reply.py` | Does the link answer back without damaging the frame stream? |
| `ft_video_grab.py` | Throughput meter, **headless** — framed FPS/MB-s, or `--raw` unframed. Also the home of `FtDevice` / `FrameAssembler` / `unpack_raw10`, which the other tools import |
| `ft_bench_async.py`, `ft_diag_rows.py` | Zero-copy throughput sweep; per-column corruption forensics |

> **`ft_video_snap.py` validates the SYNTHETIC SLI pattern**, not the camera. Pointed at real camera
> data it reports "0 frames matching, worst pixel error 1023" and writes a random-looking PNG. That
> is the tool answering a different question. Use `cam_live.py` to judge real output.

### 8.2 Over the Ft+ control plane — `host/`

`ftlink.py` (the transport class), `test_m6a_ctlpath.py`, `test_m6a_under_load.py`,
`test_m6b_reply.py`, `test_m6c_silicon.py`, `dbg_m6b_packet.py`.

### 8.3 Over the UART — `host/`

| Tool | Does |
|---|---|
| `test_silicon.py` | The bring-up test — **15/15** |
| `test_protocol.py` | Offline cross-check of the framing |
| `dump_edid.py` | Dump and decode the display's EDID as the FPGA captured it |
| `read_mode.py` | Which offline mode was picked, and what it had to choose from |
| `force_mode.py` | Pin the offline mode over `MODEFORCE` |
| `offline_mode.py` | Inspect both EDIDs and drive the offline mode |
| `rx_timing.py` | What the source is actually sending |
| `read_cam_status.py` | The camera datapath status (`0x3A`–`0x41`) |
| `max_exposure.py` | How long the camera can expose at the current rate *(see the 68.67 Hz trap)* |
| `measure_vsync_period.py` | Vsync period and jitter (genlock G0) |
| `measure_trigger_latency.py` | Trigger → integration start, and its jitter |
| `measure_exposure_gap.py` | Is the blind window between exposures constant? |
| `upload_corr.py` | Upload the radiometric LUT and prove it is live (`--selftest`) |
| `test_modecycle.py` | Cycle the PC's HDMI modes and check the FPGA follows *(measures the INPUT only)* |
| `test_3b_cameraidle.py`, `test_3c_camerawedge.py`, `test_3d_modechange.py` | Merge isolation: the camera must not disturb HDMI, and vice versa |
| `soak.py`, `stress_attack.py`, `usb_speed.py` | Long-run stability, hostile input, USB 3 confirmation |
| `lauauboard.{h,cpp}` | The C++ host-side implementation of the protocol |
| **Photometric — needs the `WITH_CAM=2` profiling build** | |
| `frame_sweep.py` | Sweep the genlock delay across the impulse sequence, one point per camera frame |
| `level_delay.py` | One solid colour at several levels, swept across the frame |
| `level_sweep.py`, `light_profile.py` | Level ramp at a fixed delay; where in the frame a level's light appears |
| `sli_background.py` | LED drive across an SLI scan — `--octet` loops one set of eight, `--scope` shows every reading unaveraged |
| `sli_frame_sweep.py` | **The projector timing sweep.** A whole frame at 1 µs with the fringes cycling — this is what 3.1 was measured with |
| `plot_white_window.py` | Draws that sweep with the emission window, gaps and recommended delay/exposure, all recomputed from the CSV |
| `roi_scope.py`, `tlp_check.py`, `hdmi_ramp.py` | Rolling ROI scope; transmitted-pixel check; drive the projector from the PC's HDMI |
| `align_roi.py` | Find the projector pixels that land on the camera ROI — shrinking white square, exposure held under 600 ADU *(step 1 of [14.2](#142-measuring-the-three-responses--a-square-with-a-matched-background))* |
| `roi_integral.py` | One frame's light at the ROI summed from 30 µs slices, for levels a single exposure would clip |
| `tone_analysis.py` | Floor-align a tone sweep's traces to the first-measured one (ICP, y-translation) so drift in the dark floor stops swamping the light, and classify each slice as light or no light; also imported live by `tone_sweep.py` |
| `tone_sweep.py` | The 16-level tone-curve sweep for one colour filter: white square through a LUT, bisection level order, median of 6 per slice, live floor-aligned plot. Report: `ml750st_report.html` |

> After a board reset, `edid_merge` needs a few seconds to finish reading the DDC. Until it does,
> `edid_ok` is 0 and `SUPP` is empty while `MODE` still reads the power-up default — a half-state
> that looks like a failed pick. `read_mode.py` waits for `edid_ok`.

---

## 9. Building and flashing

**Vivado 2025.2.1** lives at `C:\AMDDesignTools\2025.2.1\Vivado\bin\vivado.bat` (**not**
`C:\Xilinx`, and not on `PATH`).

```
vivado -mode batch -source build_merged.tcl    # -> build_merged/Au2_SLI.{bit,bin}   THE CURRENT DESIGN
vivado -mode batch -source build_pt_hdmi.tcl   # the HDMI/SLI half alone on the Pt V2
vivado -mode batch -source build.tcl           # the original Au V2 / XC7A35T design
vivado -mode batch -source program.tcl         # volatile JTAG load
```

| Script | Part | Contents |
|---|---|---|
| `build_merged.tcl` | XC7A100T (Pt V2) | **Current** — HDMI/SLI + PYTHON 1300 + Ft+ + MIG |
| `build_pt_hdmi.tcl` | XC7A100T | HDMI/SLI only — faster turnaround for pass-through work |
| `build_pt.tcl` | XC7A100T | **Stale** — predates M2, no camera datapath |
| `build.tcl` | XC7A35T (Au V2) | The original design; still builds |

Do not run two builds concurrently — they share an out-of-tree IP generation directory.

**Flashing** with the Alchitry loader **2.0.52+** (not the old `C:\Program Files\Alchitry` copy):

```
alchitry.exe load --bin build_merged/Au2_SLI.bin --board PtV2 --ram     # volatile, for testing
alchitry.exe load --bin build_merged/Au2_SLI.bin --board PtV2 --flash   # persistent
```

`AlchitryFlasher/AlchitryFlasher.cmd` is a one-click Windows flasher for the released
`Bitstream/Au2_SLI.bin` (SHA-256 checked).

**Simulation is available** — `xvhdl` / `xvlog` / `xelab` / `xsim` are installed.
`sim/tb_mode_select.v` covers the mode pick; `sim/tb_hdmi_loopback.v` + `sim/run_hdmi_tb.sh` cover
the HDMI path.

> **Vivado on this host crashes for tool reasons roughly half the time** — `[Designutils 12-1097]`,
> `EXCEPTION_ACCESS_VIOLATION`, bare exit 116 / 127. Retrying the same script normally succeeds.
> Note also that silent one-digit file corruption has been observed on this machine. **Run memtest
> before trusting an artifact.**

---

## 10. Traps this project has already paid for

| Trap | Why it bites |
|---|---|
| **Comments lie; the code does not** | `pre_diag`'s header claims preamble counts but the code counts vsync/hsync rises. `vdp_diag[31:16]` is labelled `in_dvid` and counts `in_adp`. `gb_diag[31:16]` is labelled `vdp_prefix_seen` and counts `vdp_guardband_detect`. Five wrong conclusions in one session came from believing headers |
| **`test_modecycle.py` measures the input** | It reports success on modes the projector never displays |
| **`edid_fall` at exactly 2/s is healthy** | It is the 0.5 s DDC presence probe |
| **`0x3E`/`0x3F` wraps below 68.67 Hz** | Period ÷ 16 in 16 bits. Never infer genlock from it |
| **FWFT FIFO + `rd <= !empty`** | Duplicates the last item every burst; a doubled stream fails exactly like a dead one |
| **Sensor damage at the top of the frame** | Permanent, from a soldering-era fault. Not an LVDS/link bug — do not chase it |
| **720 Mbps LVDS needs IDELAYE2** | Without eye-centring the isolated bit drops and it looks exactly like bad solder |
| **The FT601 can enumerate and not clock** | It can read an EEPROM back perfectly while driving no `ft_clk`. Pulse `RESET_N` |
| **The FT601 can stream fast and lie** | A build measured 192 fps / 0 drops while corrupting `ft_data[31:16]`. An unconstrained source-synchronous bus is invisible to timing *and* to throughput tests. **Verify bytes** |
| **Build scripts can silently drop generics** | Two experiments were invalidated this way. Check the generics actually reached synthesis |
| **PowerShell here-docs mangle git messages** | `git commit -m @'…'@` fails with pathspec errors. Write the message to a file and use `git commit -F` |
| **The RX input self-oscillates with no cable** | `sel`/offline hunts and `symbol_sync` is fooled; `pll_locked` staying 0 is the honest signal |

---

## 11. Pins, LEDs and switches

The Pt V2 pinout is in `constrs_1/imports/RTL/Au2_pt.xdc` (+ `pt_ftplus_merged.xdc` for the Ft+);
the camera board's connector map is in [`CAMERA_IO_MAP.md`](CAMERA_IO_MAP.md).

### Config switches

| Switch | Function |
|---|---|
| `SW[3]` | Red channel enable |
| `SW[2]` | Green channel enable |
| `SW[1]` | Blue channel enable |
| `SW[0]` | 0 = vertical stripes, 1 = horizontal |

All four are overridable over USB via `SLICTRL` (`0x13`) with bit 7 set:
`effective_sw = sw_en ? usb_value : SW`. `PINS` (`0x10`) shows both nibbles, so you can confirm the
override took and read live switch state at any time.

> **Next board rev:** make all four switches uniform (all pull-up / default-high, switch → GND). That
> needs the HvsV switch rewired from +3V3 to GND **on the PCB** — it is not an FPGA-only change —
> plus the HvsV logic inverted.

### LEDs

| LED | Indication |
|---|---|
| 7 | VSYNC |
| 6 | HSYNC |
| 5 | VSYNC polarity (1 = positive) |
| 4 | HDMI-Rx clock detected (off in offline mode) |
| 3 | 0 = SLI pattern, 1 = desktop display |
| 2 | Camera trigger ready |
| 1 | Current frame is the first of the pattern |
| 0 | Trigger output |

`led_idle_anim.v` runs a slider animation when nothing is connected — a deliberate sign of life.

### Legacy DB-9 camera GPIO (Au V2 / Br V2 era, still in `pixel_pipe.v`)

A 4-line handshake per camera: **trigger** out, **mode** in, **first-frame** out, **ready** in.
`pixel_pipe.v` drives `C1_out(0)` / `C2_out(0)`; Cam2 mirrors Cam1's outputs but only **Cam1's**
ready and mode inputs are wired, so Cam2's readiness does not gate the capture rate. This path is
independent of the PYTHON 1300 trigger, which comes from `cam_frame_ft.v`.

`pixel_pipe.v` uses a 1-deep `pending` with fresh-ready arming and a `GLITCH_CYC = 10000` de-glitch
(commit `ae8fa1e`).

> **Deferred TODO:** port the MimasA7 `cam_pace.v` edge-paced trigger over the AuV2 `rdy_cnt`
> accumulation, splitting split-mode's two jobs.

> `pattern_gen`'s enable comes from the camera board's `mode` GPIO (`C1_in[1]`), which the XDC
> **pulls low** — with no camera attached the generator is off and the offline test pattern passes
> straight through. Set `0x13` `mode_en` + `mode_val` to force the SLI fringes on with no camera
> board.

---

## 12. Boards in this repo

| Folder | What it is |
|---|---|
| [`LauPythonCamera_Pt_Stack/`](LauPythonCamera_Pt_Stack/) | **The current camera** — PYTHON 1300 daughter board for the Pt V2, *and* its Verilog under `ddr/` |
| [`LauCameraTrigger_Alchitry/`](LauCameraTrigger_Alchitry/) | The original Br V2 → DB-9 trigger breakout, plus the Basler / Alvium wiring guides |
| [`LauCameraTrigger_Alchitry_3xJST/`](LauCameraTrigger_Alchitry_3xJST/) | The same, with 3× on-board JST-7 connectors |
| [`LauCameraTrigger_Alchitry_Stack/`](LauCameraTrigger_Alchitry_Stack/) | DF40 stacking version; SPDT config switches, trigger broadcast to 3 cameras |
| [`LauMipiCamera_Alchitry_Stack/`](LauMipiCamera_Alchitry_Stack/) | MIPI CSI-2 board (WIP scaffold) — open in KiCad and run ERC/DRC before relying on it |

Camera-wiring guides under `LauCameraTrigger_Alchitry/`: Basler ACE USB 3.0, Allied Vision Alvium
1800, a three-camera DB-9 harness, the on-board JST-7 variant, and the Vimba FPGA timing guide.

> **rev-1 `LauCameraTrigger` had a floating GND island** at the DIP switch (SW1 / caps / tie-low not
> bonded to DF40 GND). rev-1 boards are bodged; fixed in layout, pending fab verification.

---

## 13. Repository layout

```
├── README.md                        # this file
├── USB_COMMAND_REFERENCE.md         # every get and put, both transports
├── FTPLUS_API.md                    # the Ft+ control API
├── FRAME_HEADER_PLAN.md             # per-frame header design (format 3 -> 4)
├── GENLOCK_MILESTONES.md            # G0..G3 — exposure locked to the projected frame
├── MERGE_MILESTONES.md              # M0..M7 — HDMI + camera on one FPGA
├── PT_PASSTHROUGH_DEBUG.md          # pass-through bring-up on the Pt
├── PC_INTERFACE_INVENTORY.md        # complete PC <-> FPGA comms inventory
├── PC_INTERFACE_PLAN.md             # diagnostics on the Pt, everything else on the Ft
├── CAMERA_RTL_PLAN.md               # PYTHON 1300 bring-up plan + gates
├── CAMERA_RTL_REVIEW.md             # fresh-eyes receiver-chain review (P1-P4)
├── CAMERA_SENSOR_PROTOCOL.md        # datasheet-cited protocol constants
├── CAMERA_IO_MAP.md                 # camera board I/O pin map
├── CAMERA_POWER_DESIGN.md           # camera board power design
├── CAMERA_POWER_SIMULATION.md       # camera board power simulation
├── CAMERA_CALIBRATION_NOTES.md      # two-point calibration — where it could live
├── ROADMAP.md                       # hardware expansion (DF40 stacking, Ft+, MIPI)
├── MIPI_CSI2_ROADMAP.md             # custom MIPI CSI-2 receiver plan
├── POLARFIRE_PORT_FEASIBILITY.md    # porting to a Microchip PolarFire kit
├── build_merged.tcl                 # THE CURRENT BUILD  (Pt V2: HDMI + camera + Ft+)
├── build_pt_hdmi.tcl                # HDMI/SLI half alone on the Pt V2
├── build_pt.tcl                     # stale Pt build (pre-M2)
├── build.tcl                        # original Au V2 / XC7A35T build
├── program.tcl                      # volatile JTAG load
├── sources_1/                       # HDL (sources_1/imports/RTL) + IP
├── constrs_1/                       # XDC — Au2.xdc (Au V2), Au2_pt.xdc + pt_ftplus_merged.xdc (Pt)
├── sim/                             # xsim testbenches
├── host/                            # UART + Ft+ control-plane tools, and the C++ host class
├── ft_usb_video/                    # the USB 3 image path — viewer, benches, verifiers
├── press/                           # press release (LaTeX + PDF) and the HTML reference pages
├── Bitstream/                       # released bitstream (Au2_SLI.bin)
├── AlchitryFlasher/                 # one-click Windows flasher
├── Matlab/                          # legacy pattern-generation scripts
├── build_scripts/                   # historical project-mode scripts (superseded)
├── Lau*_Alchitry*/ , LauPythonCamera_Pt_Stack/   # KiCad boards
└── LICENSE
```

## 14. Planned — per-channel radiometric calibration

**The problem.** The projector does not display the RGB it is sent. It gamut-maps: measured on the
ML750ST, a pure green command drives the **red** LED at 8-10 % of full red, while a pure red command
drives green at 0.2 %. A 660 nm interference filter held in the beam while projecting green shows a
clearly modulated red image — the crosstalk is not subtle and it is not symmetric. So a grey SLI
fringe does not produce three equal channel intensities on the screen, and the three channels'
phase maps disagree by however much they differ.

**The goal.** A LUT that takes the 8-bit greyscale fringe value and emits **three different** 8-bit
values, chosen so that what actually lands on the screen is equal across R, G and B once the
projector has finished manipulating it.

### 14.1 What has to be built

| | |
|---|---|
| Mount | The filter-carrying lens cap (`--filter-dia`, `3dmodels/projector_lens_cap_filter.step`). Drops a 50 mm filter into the beam between projector and sensor, gravity-held. Costs 4.70 mm of extra standoff, so absolute levels shift and every sweep in this campaign must use it. |
| Filters | Three interference filters near each LED's **peak**, not merely in its band. The 660 nm one on hand may sit on the red LED's tail rather than its peak — that costs signal, not correctness, but check it before trusting small differences. |
| RTL | `corr` becomes **three** 256-entry tables, or one 256 x 24. Today `pat_out` is a single value fed to all three channels, so this is the blocking change. The lookup must stay combinational (a registered read applies each pixel's correction to the next one). |
| Host | Upload three tables instead of one; extend `upload_corr.py` and its self-test. |

### 14.2 Measuring the three responses — a square with a matched background

Nothing here needs full-frame capture — the 16x16 ROI is enough, because the quantity wanted is a
*transfer curve*, not an image. The curve is measured with a **small square of known level** placed
on exactly the projector pixels the ROI sees, so every reading belongs to one commanded value.

**Why the square alone is not enough.** The sensor is bare, 17 mm in front of the lens, so the ROI
also collects light from a wide ring of projector pixels *around* the ones it is aligned to. A square
on a black screen therefore sits in a darker surround than the same pixels do during a real SLI
scan, and its curve would be measured at the wrong background. The fix is to project **white
everywhere except a black hole around the ROI**, and size the hole so the background the ROI sees
equals the background of the high-frequency SLI sequence. Measuring that SLI background is part of
the procedure, not a one-off.

#### Procedure

> **Do not move anything once step 1 has started.** The projector ROI, the SLI background and the
> hole size all depend on where the bare sensor sits relative to the projector lens. Bumping or
> repositioning the camera, the projector or the mount, or swapping the mount, invalidates all
> three. This has happened: after the camera and projector were physically repositioned, the
> background level visibly changed. If anything moves, **start over from step 1**; do not carry
> old numbers forward. Changing the filter does not move the ROI, but it does mean redoing steps 2–4.

0. **Set up.** Fit the filter mount and insert one filter. The PC drives 800x600@120 over HDMI with
   the FPGA passing it through (SLICTL `0x00`, ROICTL bit 7 `imp_en` **off** — it replaces the HDMI
   picture). After any display mode change, give the projector a minute before trusting a level:
   a black screen at the full-window exposure slid from 662 to 515 ADU over 70 s after Windows
   switched 1280x720@60 back to 800x600@120, whereas a content change settles within 0.2 s.

1. **Find the projector ROI** — the projector pixels that land on the camera ROI.

       python -u host/align_roi.py COM6 --set-mode --live

   A white square on black, starting at 256 px and halving to 16 px, keeps the brightest tile at each
   size. Any reading over `--ceiling` (600 ADU) or any saturated pixel shortens the exposure and
   restarts that size. On this rig four runs agreed within 8 px: centre **(388, 502)**, used as a
   32x32 square at (372, 486).

2. **Measure the SLI background** — the target level.

       python -u host/sli_background.py COM6 --octet 2 --delay 8000 --expo 5 --scope --live

   Octet 2 is the highest-frequency set (horizontal, white), frozen and repeating. The probe is a
   ~5 µs exposure at 8000 µs, after the last light of the frame
   ([3.1](#31-where-the-light-actually-is--measured-on-an-optoma-ml750st)). The target is the
   **mean over the eight phases**. With the 440 nm filter the eight phases read 158.9–162.9,
   average **160.8 ADU**. That 4 ADU swing is light from fringe pixels near the ROI and changes with
   phase, which is why the target is the eight-phase average and not any one pattern. *Why a
   pattern-dependent level exists after the last measured light at all is not yet explained.*

3. **Size the hole** — full white, black square hole centred on the projector ROI, same 8000 µs /
   5 µs probe. Grow the hole until the ROI mean equals the target from step 2. With the 440 nm
   filter:

   | Full-white screen, black hole | White left | ROI mean |
   |---|---|---|
   | none | 100 % | 180.0 |
   | 32x32 | 99.8 % | 180.0 |
   | 64x64 | 99.1 % | 179.6 |
   | 128x128 | 96.6 % | 175.0 |
   | 160x160 | 94.7 % | 169.6 |
   | 176x176 | 93.5 % | 163.9 |
   | **184x184** | **92.9 %** | **160.3** (target 160.8) |
   | 192x192 | 92.3 % | 156.5 |

   The response is steep between 128 and 192 px — step in 8 px there. *This step was done by hand
   (a live reader switching scenes); there is no tool for it in `host/` yet.*

4. **Measure the curve.** Keep the white surround and hole, put the test square at the projector ROI
   inside the hole, switch to the full emission-window exposure (540 µs delay, 6175 µs), and step the
   square's level 0..255. *Not yet checked: whether white surround + square fits in 10 bits at that
   exposure. If it clips, sum 30 µs slices with `roi_integral.py` instead of one long exposure.*

5. **Repeat steps 2–4 for each filter.** The target and the hole size are specific to the filter.
   If the camera, projector or mount has moved at any point, go back to step 1 instead.

The numbers quoted in these steps and in the table below are from one positioning of the rig
(Sep 15, 2026). They show the size of the effects, but they are **not** values to reuse; every
setup measures its own.

That yields, per channel, intensity against commanded level — three curves that will not agree in
gain, in offset, or in shape.

#### What the procedure rests on — measured, 440 nm filter, 8000 µs / 4.88 µs probe

| Projected | Mean picture level | ROI mean |
|---|---|---|
| black | 0 % | 153.9 |
| white 32x32 square at the ROI (blue square: 153.9) | 0.2 % | 154.0 |
| square + white band over the top 150 px | 25 % | 154.0 |
| square + white band over the top 300 px | 50 % | 154.0 |
| high-frequency fringes, eight-phase average | ~50 % | 160.8 |
| full white | 100 % | 180.0 |

- **The average picture level does not set the background.** Half the screen white, kept away from
  the ROI, adds nothing, yet the fringes at the same average add 7 ADU and full white adds 26.
- **The added light is local.** Blacking out only 7 % of the screen around the ROI removes most of
  it. It comes mainly from a **ring 64–96 px from the ROI centre**; the centre 64x64 contributes
  almost nothing (hole 32 → 64 changes 0.4 ADU; 128 → 192 changes 18.5).
- **A small square does not disturb the background.** Black and a 32x32 square read the same, so
  the square's level can be stepped without moving the background under it.

### 14.3 Turning three curves into a LUT

Invert each curve so that a given grey input produces the same *relative* intensity in all three
channels. Then verify by re-measuring: the three sinusoids should come back with matching amplitude
and offset, and the residual is the honest error bar on the correction.

> **Equalise shape, not absolute radiance.** Each measurement is scaled by that filter's
> transmission times the sensor's QE at that wavelength (PYTHON 1300: ~50 / 60 / 57 % at 450 / 550 /
> 630 nm), and those factors are not known well enough to compare channels absolutely. What matters
> for SLI is that each channel's intensity is the *same linear function* of the commanded value, so
> the sinusoid is undistorted and the phase agrees. Absolute colour balance is a separate question
> and does not affect the phase map.

### 14.4 The limit this approach has

Three independent LUTs correct the **diagonal**. They cannot correct crosstalk, because the moment
the correction sends R != G != B the input leaves the grey axis and the projector's colour transform
applies in full — including the green-into-red term. Expect a residual of roughly the crosstalk
magnitude, order 8-10 %.

Removing that needs the full 3x3: drive R, G and B **one at a time** at several levels, measure all
three filtered channels each time, and build the matrix that maps commanded RGB to emitted RGB.
Invert it, apply it ahead of the per-channel LUTs. Worth measuring the residual after 14.3 before
deciding whether the matrix is needed — if the phase maps agree well enough, it is not.

### 14.5 How we will know it worked

- The three fitted sinusoids agree in amplitude and offset across filters, to a stated tolerance.
- The per-channel phase maps agree; the spread between them is the figure of merit.
- The correction is checked at **several grey levels**, not just full scale. The projector's level
  response has a knee near 79-83 on all three channels, so a correction fitted only at the top will
  not hold at the bottom.

---

## 15. Planned — an EDID filter that generalises to any projector

**The symptom.** Plug a PC into the HDMI input and it is offered nine modes,
topping out at 75 Hz, even when the projector's own EDID advertises
800&times;600@120 and 1024&times;768@120. Every 120 Hz measurement in this repo was
taken through the **offline** path, which reads the sink's EDID directly and never
consults the merged block — which is why this stayed invisible until a PC was
plugged in on 2026-09-11.

**The cause is a category error, not a missing mode.** `edid_builder.v` filters
the two timing sections by different means:

| section | how it is filtered | generalises? |
|---|---|---|
| established (B35-37) | three bitmasks, hand-computed from the pixel-clock window | yes — the masks cover the whole established bitmap |
| standard (B38-53) | membership in `CAND[]`, a hand-typed list of 9 codes | **no** |

The policy is about **pixel clock** — keep what pass-through can carry, a window of
25.00 to 90.00 MHz measured on hardware. The standard-timing test asks about
**identity** instead: is this one of the nine codes someone approved. Identity does
not generalise to a sink nobody has plugged in yet. The module says as much:

> F_MIN_10K does NOT filter this list — it is only used for the max-clock byte at
> maxclk_byte below. The list is hand-curated, so it has to be pruned to match the
> floor by hand too.

800&times;600@120 is 73.27 MHz. It satisfies the policy and was dropped anyway,
because the policy is not enforced on that path. Commit `f754c20` even wrote down
that it "would be a HEALTHY pass-through mode" while removing the 800&times;600
60/72/75 entries — and did not add it. It was added by hand on 2026-09-11, which is
the practice this section exists to end.

### 15.1 The fix: test the clock, not the identity

Replace `CAND[]` with the **whole VESA DMT table** as a ROM of
`{standard-code, pixel-clock}` — about 40 entries — and change the test from "is
this code in my list" to "look up its clock, keep it if `F_MIN <= clk <= F_MAX`".

That turns curation into data. A projector nobody has seen advertises
1360&times;768@60; it is in DMT, its clock is 85.5 MHz, it is in window, it passes.
Nobody edits anything. And the window is then stated once and enforced identically
by both timing paths, which is what makes it auditable.

**Why tabulate the clock rather than compute it.** A standard timing code carries
only resolution, aspect and refresh. The blanking — and so the clock — depends on
which timing standard the source applies, and the ambiguity is real: `CAND[4]`
already documents 1280&times;800@60 as 71.00 MHz reduced-blanking against 83.5
under plain CVT. A formula has to guess which the source will choose; a DMT lookup
does not. For codes genuinely outside DMT, compute the **non-reduced CVT** clock
and gate on that: it is the higher figure, so erring toward it errs safe.

**The RTL cost is small.** This runs once per EDID read, not per pixel, so there is
no timing pressure — a ROM and a sequential compare, which is close to what the
existing state machine already does, with a magnitude test in place of an equality
test and more entries to walk.

### 15.2 Two things worth doing with it

**Raise `F_MAX_10K` to what the hardware actually supports.** 90 MHz was margin
chosen under the old fixed &times;15 recovery MMCM. `rx_freq_band` +
`rx_drp_recfg` now retune per band — that is what let the floor drop 60 -> 25 — so
the ceiling deserves the same treatment. The real limit is the output serialiser's
~600 MHz, about 120 MHz of pixel clock. Every megahertz of window is modes that
stop needing exclusion at all, 1024&times;768@120 (115.5 MHz) among them.

**Keep the Monitor Range Limits descriptor consistent.** It already derives its
max-clock byte from `F_MAX_10K`, so it follows automatically — and it is the
standards-sanctioned backstop for any mode a source synthesises rather than picks
off the list.

### 15.3 What cannot be automated

The window itself. 25-90 MHz came from eyes-on hardware — black screens at VCO
600, twitching at 743, solid at 975 and above — and moving it needs the same. But
that is a number measured occasionally and written down once, not a list
maintained per projector. That distinction is the whole point of this section.

> **Do not widen the window by arithmetic alone.** Commit `8790569` did exactly
> that, lowering the floor 60 -> 40 MHz on the reasoning that VCO >= 600 was now
> reachable, and added three 800&times;600 entries on that basis. They black-screened
> and had to be removed again in `f754c20`. Its own summary of the lesson: 800&times;600
> pass-through was "advertised by calculation and never verified, on either board."

---

## Licensing

The HDMI pass-through foundation is adapted from
[hamsternz/Artix-7-HDMI-processing](https://github.com/hamsternz/Artix-7-HDMI-processing) (MIT).
