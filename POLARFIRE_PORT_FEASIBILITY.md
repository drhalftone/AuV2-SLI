# Porting AuV2-SLI to a Microchip PolarFire Video & Imaging Kit

**Question:** Can the Alchitry Au V2 (Artix-7) SLI design be moved to a Microchip
**PolarFire FPGA Video and Imaging Kit** (`MPF300-VIDEO-KIT`, device
`MPF300T-1FCG1152I`) to do **4K60 dual-HDMI passthrough/generation**, ingest a
camera over **MIPI CSI-2** to synchronize projection and capture for structured
light illumination (SLI), and **process pixels on-chip so the FPGA outputs only
phase video**?

**Short answer:** Yes to everything — but it is a *re-architecture*, not a port.
The kit is almost purpose-built for this. The ~35% of the Au code that touches the
HDMI PHY and clocking is the hard, FPGA-specific part, and on PolarFire you do not
port it — you *replace* it with Microchip's licensed video IP. The other ~60–65%
(the actual SLI value) carries over in spirit.

---

## 1. Does the kit physically do what we want?

| Requirement | PolarFire Video Kit | Verdict |
|---|---|---|
| Dual HDMI, 4K60 passthrough/generate | HDMI 2.0 (up to **4K60**) via 12.7 Gbps transceivers; Microchip ships production HDMI **RX** and **TX** IP (1.4/2.0/2.1) plus an HDMI **loopback** app note (AN4768) — literally passthrough | ✅ Supported |
| MIPI CSI-2 camera for SLI sync | Ships with a dual-camera daughtercard (2× Sony **IMX334**, 4-lane MIPI each, up to 1782 Mbps); Microchip provides MIPI CSI-2 **RX/TX** IP | ✅ Supported |
| Process pixels on-FPGA, output only phase video | MPF300T = 300K LE + ~924 DSP blocks + **4× 4 Gb DDR4** for frame buffering | ✅ Feasible (largest effort — see §5) |

The hardware is **not** the bottleneck. The MPF300T has the transceivers for 4K60
HDMI and the DDR4 bandwidth + DSP for the on-chip math.

**In-stream pixel processing is a shipped capability, not a research bet.** Microchip's
own **DG0849** demo runs **edge detection, picture-in-picture, and image enhancement**
(brightness / contrast / color balance) on a live video stream displayed over HDMI, with a
**DDR4 frame buffer** in the middle. Edge detection in particular *reads the pixel stream,
computes a per-pixel result, and emits a modified stream* — architecturally identical to
"replace the pixels with SLI patterns." Passthrough (AN4768) is just the degenerate
"process = nothing" case of the same pipeline. So the swap-pixels-in-the-middle function is
demonstrated by the vendor; what is new for us is only the *content* of the processing block
(`pixel_pipe` / `pattern_gen`). DG0849's processing path takes a **1080p HDMI-RX input** and
a **4K HDMI-TX output**; the 4K-in path (AN4768) is separately proven as passthrough — so the
exact "4K-in → modify → 4K-out" combination is integration of two shipped halves, not new
ground.

---

## 2. The architectural upgrade this represents

The Au design syncs an **external** camera over a 4-line GPIO trigger; the camera
ships images to the PC over USB, and a host Qt program does the 3-D reconstruction.
On PolarFire, pulling the camera **into the fabric over MIPI CSI-2** changes the
model fundamentally:

- The FPGA now both **generates the projector frames** and **receives the camera
  frames**, so synchronization becomes a tight internal closed loop instead of a
  GPIO handshake to an external box.
- Because both streams live on-chip, **phase computation can happen in fabric**
  (the "only output phase video" goal), dramatically cutting what crosses USB.

This is the right architecture for the stated goal — not just a bigger Au.

---

## 3. What ports, what gets replaced

Based on a file-level inventory of the Au design.

### Carries over (~60–65%) — the SLI IP
- `pixel_pipe.v` — pattern generation, top-left-pixel (TLP) trigger, frame pacing
- Index-map / start-frame ROMs (`indexMap.coe`, `indexMapV.coe`, `LUT.coe`,
  `LUT_V.coe`) — convert Xilinx `.coe` → Microchip `.mem`
- `video_timing_gen_rt.v`, `mode_timing_rom.v`, `mode_table.vh` — runtime video timing
- EDID build/parse/serve (`edid_builder.v`, `edid_merge.v`, `edid_serve.vhd`,
  `i2c_master_edid.v`) — generic I²C/DDC logic
- UART telemetry (`uart_tx.v`, `usb_status.v`, `status_line.v`, `edid_hex_dumper.v`)

All vendor-neutral Verilog/VHDL — recompiles in Libero with format tweaks.

### Replaced, not ported (~35%) — the PHY/clocking plumbing
- Hand-rolled TMDS front-end: `deserialiser_1_to_10.vhd` / `serialiser_10_to_1.vhd`
  (`ISERDESE2` / `OSERDESE2` / `IDELAYE2`), `TMDS_encoder.vhd` / `TMDS_decoder.vhd`
- Clock recovery: `MMCME2_ADV` + DRP (`drp_clkgen13.v`, `drp_recfg.v`)
- `clk_selector.v` (`BUFGMUX`), `ref_clk.xci` (Clk_Wiz), `Au2.xdc`

On PolarFire **none of this is hand-written** — Microchip's HDMI RX/TX IP +
transceivers + CCC/PLL provide it. You *delete* the hardest code rather than
translate it.

### New work that does not exist in the Au design at all
- MIPI CSI-2 RX integration + Bayer/ISP pipeline for the camera
- DDR4 frame buffering of the N phase-shifted captures
- On-fabric **phase extraction** (arctangent over the phase-stepped frames +
  unwrapping) — the genuine unknown. 4K60 real-time multi-frame phase math is
  doable on the MPF300T's DSPs/DDR4 but is a serious HLS/DSP effort and the place
  to run a feasibility spike before committing.

### How the live pixels are accessed (HDMI RX IP interface)
You **cannot read the RX IP's internals** — it ships as **encrypted/black-box** netlist
(free with Libero, not source-visible). You don't need to: the IP **hands you the decoded
pixels on a documented output bus**, in one of two selectable modes:

- **Native video** — 24-bit RGB + `hsync` + `vsync` + data-enable (active video) + pixel
  clock. Maps almost 1:1 onto the existing `pixel_pipe` ports.
- **AXI4-Stream video** — `tdata` (pixels), `tvalid`, `tuser` = start-of-frame,
  `tlast` = end-of-line, `tready`.

| Au `pixel_pipe` input | PolarFire RX IP native output |
|---|---|
| `in_red` / `in_green` / `in_blue` `[7:0]` | 24-bit RGB |
| `in_hsync` / `in_vsync` | hsync / vsync |
| `in_blank` | data-enable (inverted) |
| `vid_valid` | IP video-lock / valid status |

**The 4K pixels-per-clock detail.** The RX IP runs **1 pixel/clock up to 1080p60** but
**4 pixels/clock at 3840×2160@60** (594 MHz is not a fabric-friendly clock). At 4K the native
bus is therefore **4×24 = 96-bit RGB at ~148.5 MHz**, so `pixel_pipe` / `pattern_gen` must be
**widened to a 4-pixel-per-clock datapath** (4 parallel lanes; TLP and VSYNC sampling plus the
pattern addressing reworked to match). At 1080p (1 ppc) it is a near-drop-in; the 4-lane
widening is the real porting effort. AN4768's loopback wire (RX-output pixels → TX-input
pixels) is literally the insertion point — drop the SLI logic onto that bus.

### EDID: read the projector, merge, serve to the PC
The board **physically supports the full read-edit-serve path** the Au design relies on:
- **Serve EDID to the PC** — the HDMI **RX** side presents an EDID to the source over DDC and
  drives **HPD** (the IP supports DDC/E-DDC + HPD). Its *default* EDID is named "Microchip HDMI
  Display" (up to 1080p60), but the EDID is fully replaceable (see "runtime EDID override" below)
  — so serving a 4K-capable merged EDID is supported, not a limitation.
- **Read the projector's EDID** — the HDMI **TX** side has DDC-master access: on the native
  HDMI 2.0 TX (PI3HDX retimer) the DDC/SCL/SDA reach fabric for a soft I²C master; on the HDMI
  1.4 TX (ADV7511) the chip's built-in DDC master reads the sink EDID into its EDID memory
  (regs `0xC4` / `0x7E`), which fabric reads back over the control I²C.

**What ports cleanly:** the **parse / merge / build** logic (`edid_builder.v`, `edid_merge.v`
— CEA-861 VDB parse, intersect *projector modes ∩ supported window*) is pure vendor-neutral
byte manipulation → recompiles as-is. This is the genuinely valuable EDID IP and it carries
over untouched.

**Runtime EDID override — confirmed supported.** The Au does a *runtime, dynamic* merge (read
the projector → intersect → serve *that* to the PC), which needs the served EDID to be
**runtime-writable**. UG0863 confirms the RX IP does exactly this: it "**Supports
Reconfigurable EDID — Compile time and Run time**," selected by the **`Dynamic EDID Config`**
configurator parameter:

- **`Dynamic EDID Config = No`** — static: the EDID hex is loaded into the IP's EDID RAM
  (`PF_TPSRAM_C0_0`) at design-initialization (build-time only).
- **`Dynamic EDID Config = Yes`** — **runtime: the EDID is written over an AXI4-Lite interface**
  through three dedicated registers — `EDID_ADDR` (0x08, byte address), `EDID_DATA` (0x0C, byte
  value), `EDID_WEN` (0x04, write-enable) — clocking the merged EDID byte-by-byte into the EDID
  RAM. Microchip even ships reference C (`edid_load_fun.c`).

So the dynamic merge maps **directly** onto the IP — **no DDC-slave bypass needed**. The Au's
`edid_builder` / `edid_merge` produce the merged 128/256-byte blob exactly as today; the only
new glue is a small AXI4-Lite master that walks that blob into `EDID_ADDR`/`EDID_DATA`/`EDID_WEN`
(256 writes). On the plain MPF300 (no hard CPU) that master is either a **soft Mi-V RISC-V** core
or a **~20-line fabric FSM** — trivial. (One caveat from the guide: *if HDCP is enabled*, DDC
must go through CoreI2C IP — not relevant to SLI, which uses no HDCP.)

**Net:** EDID is no longer a friction point — read/parse/merge logic recompiles as-is, and the
serve side is a supported runtime-writable EDID RAM. The whole read-merge-serve loop ports.

---

## 4. Honest caveats

1. **It is a rewrite of the plumbing.** A working 4K60 passthrough + camera-sync +
   basic on-chip processing is realistically **months**, not weeks — and the
   Microchip HDMI/MIPI IP cores are **licensed** (budget the cost).
2. **On-chip phase processing at 4K60 is the long pole.** Everything else
   (passthrough, generation, camera ingest) has Microchip reference designs. The
   phase pipeline is yours to build and budget (DSP count, DDR4 bandwidth,
   latency). Prototype it at 1080p first.
3. **No DRP-style runtime clock reconfig.** The Au offline resolution-adaptive
   trick (retuning the MMCM over DRP) does not map directly; PolarFire handles
   multi-resolution differently — the HDMI IP supports 720p30 → 4K60 dynamically,
   so you lean on the IP rather than the `mode_table` retune.

**Bottom line:** A strong choice; everything described is achievable on this exact
kit. Budget it as "reuse the SLI brain, rebuild the I/O on Microchip IP, and invest
the real effort in the new on-chip camera + phase pipeline."

---

## 5. Phased migration plan

A de-risking order that gets a usable result at the end of each phase and pushes the
biggest unknown (on-chip phase math) to the end, after the I/O is proven.

### Phase 0 — Environment & bring-up (1–2 weeks)
- Stand up **Libero SoC** + license the needed IP (HDMI RX/TX, MIPI CSI-2 RX).
- Build and flash a stock Microchip example to confirm board, DDR4, and toolchain.
- Convert the portable Au sources into a Libero project; convert `.coe` → `.mem`;
  get the vendor-neutral RTL (`pixel_pipe`, timing gen, EDID, UART) to **synthesize**
  (not yet wired to real I/O).
- **Exit criterion:** clean synthesis of the portable core + a blinking/UART
  "hello" bitstream on the kit.

### Phase 1 — HDMI 4K60 loopback (passthrough) (3–5 weeks)
- Instantiate Microchip **HDMI RX IP → HDMI TX IP** per app note **AN4768**
  (loopback). Prove 4K60 in → out on real hardware.
- Re-insert the **EDID serve** path so the source negotiates the mode we want.
- Re-insert **top-left-pixel detection** and **UART telemetry** on the passthrough
  pixel stream (reuse `pixel_pipe` TLP logic + `usb_status`).
- **Exit criterion:** PC → FPGA → projector at 4K60, with TLP-change events and
  telemetry visible over UART. (This already replaces the Au's core passthrough
  function at 4× the resolution.)

### Phase 2 — Local SLI pattern generation (2–4 weeks)
- Drive HDMI **TX** from the on-chip **SLI pattern generator** (port `pixel_pipe`
  pattern path + index/LUT ROMs + `video_timing_gen_rt`) instead of the RX stream.
- Add the mode select (passthrough vs. generated) — internally now, not via a Cam2
  GPIO line.
- **Exit criterion:** FPGA generates phase-shifted fringe patterns to the projector
  at the target resolution/FPS, switchable with passthrough.

### Phase 3 — MIPI camera ingest + closed-loop sync (4–6 weeks)
- Bring in **MIPI CSI-2 RX IP** + Bayer/ISP for one IMX334; buffer frames in DDR4.
- Replace the 4-line GPIO handshake with an **internal** projector↔camera sync FSM:
  advance the pattern, fire capture, confirm frame-valid, repeat — all in fabric.
- Keep a GPIO trigger out as an option for external cameras / scopes.
- **Exit criterion:** each projected pattern frame has a corresponding captured
  camera frame in DDR4, synchronized on-chip; raw captures can be dumped over
  USB/UART for validation against the host Qt pipeline.

### Phase 4 — On-chip phase processing, output phase video (the long pole)
- Build the **phase-extraction pipeline**: arctangent over the N phase-stepped
  captures + (optional) unwrapping, streaming from DDR4.
- **Prototype at 1080p first**, measure DSP/LE/DDR4-bandwidth/latency, then scale
  toward 4K60.
- Output **phase video only** (over HDMI TX and/or USB) instead of raw frames.
- **Exit criterion:** FPGA emits a phase map per pattern set in real time; host load
  drops to final 3-D reconstruction only.

### Cross-cutting (every phase)
- Maintain a **bandwidth/resource budget** sheet (transceiver lane rates, DDR4 GB/s,
  DSP count, LE %) updated per phase — this is what tells us early if 4K60 phase
  processing fits the MPF300T or needs a larger part.
- Keep the host Qt reconstruction as the **golden reference** to validate each
  on-chip stage against.

---

## 6. Bill of materials / what else to buy

The kit is a development-board bundle — it covers FPGA bring-up and provides camera
sensors for prototyping, but not a complete SLI rig. Prices are indicative (USD);
confirm with the distributor.

### Already in the box (do not rebuy)
- MPF300T-1FCG1152I board, **2× IMX334** camera daughtercard (VIDEO-DC-DUALCAM),
  one HDMI cable, USB Mini-B cable, 12V/5A supply.
- **Embedded FlashPro programmer** (over the USB Mini-B) — no separate programmer
  needed.

### Price list

| Item | Notes | Approx. price |
|---|---|---|
| **PolarFire Video & Imaging Kit** (`MPF300-VIDEO-KIT-NS`) | The board + dual IMX334 cameras + cables/PSU | ≈ $2,000 (confirm) |
| **Libero SoC *Gold* license** | **Required** — MPF300T is a Gold-tier device; free Silver covers only small parts (MPF100T). Ask if a 1-yr Gold voucher is bundled with the kit (often is → $0). | ≈ $1,000 / yr |
| HDMI RX/TX IP + MIPI CSI-2 RX IP | **Free** with Libero (encrypted RTL) | $0 |
| **BenQ TK700 DLP projector** | Dual HDMI 2.0, 4K60-capable, low deterministic lag (16.67 ms @ 4K60 = 1 frame), also 1080p @ 120/240 Hz. *Caveats: XPR pixel-shift (not native 4K addressability); single-chip color-wheel — disable all image processing, use low-latency game mode.* [B&H listing](https://www.bhphotovideo.com/c/product/1686755-REG/benq_tk700_3200_lumen_4k_uhd.html) | **$1,499** |
| **2nd HDMI cable** (Premium High-Speed, 18 Gbps) | Passthrough = source→FPGA→projector needs two; kit ships one | ≈ $15–30 |
| **Global-shutter MIPI camera** (+ possible adapter) | *Likely* — bundled IMX334 is rolling-shutter; global shutter is usually preferred for fringe capture synced to projector frames. Skip if rolling shutter is acceptable for your scenes. | varies |
| **FMC MIPI CSI-2 RX card** (multi-port, FFC/ribbon) | *If using a ribbon-cable camera instead of the bundled J5 daughtercard* — a VITA 57.1 FMC LPC card with standard CSI FFC connectors (e.g. 4× 4-lane). **Must be RX-capable** (Microchip's VIDEO-DC-MIPITX is TX-only). See §11. Consumes the FMC slot. | ≈ $100–300 |
| **FMC GPIO breakout** (AMD **HW-FMC-XM105-G**) | *Only if driving an external camera over the 4-line GPIO trigger* (the Au DB9 model). The kit has **no 0.1″ GPIO header** — user GPIO is only on the FMC HPC connector (J14), so a breakout is required to reach the pins. See §10. Set VADJ to 3.3 V (J24/J25). NCNR. | ≈ $159 |
| Lenses (M12/CS-mount) for sensor board | Confirm whether included with the dualcam board | ≈ $20–100 |
| Host PC meeting Libero specs | Win 10/11 or RHEL/Ubuntu, ≥16–32 GB RAM, tens of GB disk | (likely owned) |
| HDCP IP | *Only* if passing through copy-protected content; needs NDA. Not needed for SLI. | skip |

**Must-buys beyond the kit:** Libero Gold license (or confirm bundled), the **BenQ
TK700** (or equivalent), and one more HDMI cable. **Most likely extra:** a
global-shutter camera (see §7) if SLI can't tolerate rolling shutter — the
**VIDEO-DC-SLVS / IMX530** card is the in-ecosystem option.

### Replacing the cameras
The kit is modular — the dual-IMX334 board is one of several imaging daughter cards:
- **VIDEO-DC-SLVS** — FMC card with a **Sony IMX530** sensor **+ lens**. The IMX530
  is a **24.5 MP global-shutter** sensor (Sony Pregius S). Catch: it uses
  **SLVS-EC** (transceiver-based, ≤4.7 Gbps/lane), **not MIPI CSI-2**, so it needs
  Microchip's **SLVS-EC receiver IP** instead of MIPI CSI-2 RX IP. This is the
  recommended SLI camera.
- Other Microchip cards: SDI, CoaXPress, USXGMII.
- **Own MIPI camera:** the dual-cam board mates through Microchip's MIPI CSI-2
  connector on the GPIO header (*not* a standard Raspberry-Pi CSI socket), so a
  third-party camera needs a connector/lane-mapping adapter + its own I²C sensor-init.

---

## 7. High-speed capture & camera ↔ VSYNC synchronization

### Why FPGA-side sync is precise
In this architecture the FPGA **owns the projector's VSYNC** — it generates the
video timing (pattern mode) or recovers it from HDMI (passthrough). VSYNC therefore
lives in the FPGA's own clock domain, so the camera trigger is derived from the
*same counter* that defines the frame:
- **Jitter ≈ one pixel-clock period** (ns), not the ms-scale uncertainty of a
  software/USB-triggered camera.
- Sensor trigger-to-exposure latency is a **fixed datasheet constant** — a calibrated
  offset, not jitter.

The FPGA controls three things precisely:
1. **Phase offset** — a programmable delay counter (in pixel clocks) between VSYNC and
   the trigger, to compensate the projector's display lag (TK700 ≈ 16.67 ms = 1 frame
   @ 4K60) so exposure lands on the frame *actually on screen*.
2. **Exposure start** — trigger pulse → global-shutter sensor exposes the whole frame
   at once.
3. **Exposure duration** — many global-shutter sensors (IMX530 incl.) support
   pulse-width-controlled integration, again locked to the video timebase.
4. **(Deepest)** the FPGA can also generate the camera's master clock (INCK) from the
   same PLL tree → camera and projector share a timebase → **zero long-term drift**.

**Global shutter is required:** IMX530 exposes the full frame simultaneously on the
trigger (frame-accurate, no skew). The bundled rolling-shutter **IMX334** smears the
readout across the projected pattern — OK for static scans, not for fast SLI.

### Multiple exposures per VSYNC (the "4 sub-frames" goal)
Goal: capture each of the **4 XPR sub-frames** the DLP flashes inside one 4K60 frame.

**Subwindowing / binning — yes, with large headroom.** IMX530 = 24.5 MP (5328×4608)
at ~106 fps full-frame; frame rate scales inversely with row count:
- **1080-row ROI** → ~106 × (4608/1080) ≈ **~450 fps** → 240 fps with margin.
- **2×2 binning** → ~2× fps *and* ~4× SNR (valuable for fringe contrast under light);
  keeps full FOV, unlike ROI (a central crop of this large sensor).
- Bandwidth: 1920×1080 × 240 fps × 10-bit ≈ **5 Gbps** — within SLVS-EC budget.
- Note: 24.5 MP is *more* sensor than a 240 fps 1080p-class job needs — you'll always
  ROI/bin it. A smaller/faster global-shutter sensor would do it full-frame but leaves
  the Microchip card.

**The XPR catch.** XPR decomposes **one 4K HDMI frame** into 4 half-pixel-shifted
1080p flashes *internally*. By default the 4 sub-frames are the same image shifted,
not 4 independent patterns. You *can* encode 4 distinct patterns via the 4K→sub-frame
sampling map, but that map **and** the sub-frame timing are the projector's
**undocumented black box** — the FPGA gets VSYNC only (no XPR-phase signal), and the
color wheel adds intra-sub-frame flicker. Feasible, but a reverse-engineering +
calibration project with real risk.

**Simpler path (recommended): 1080p @ 240 Hz native (XPR off).** The TK700 accepts
1080p240 at 4.16 ms lag (genuine low-latency frames). That yields **240 real,
independent 1080p patterns/sec**, captured **1:1 at 240 fps**, with the FPGA owning
every VSYNC — fully deterministic, no projector black box. Crucially, 4 patterns span
the **same 16.67 ms** window either way (4 frames @ 240 fps), so there is **no
motion-between-phases penalty** vs. the XPR-packing scheme. Pursue XPR-packing only
later, if 4K spatial resolution *per pattern* turns out to be needed.

### To confirm when building
- Exact IMX530 fps at the chosen ROI/binning (Sony/FRAMOS calculator) and whether
  **overlapped exposure/readout** is supported (needed to sustain 240 fps cleanly).
- That the **SLVS-EC RX IP** sustains the target line rate at the ROI.
- That TK700 **1080p240 is true per-frame display** (almost certainly, given the
  4.16 ms lag) before relying on it.
- IMX530 external-trigger registers + trigger latency (Sony datasheet, under NDA).

---

## 8. Host-PC connectivity & data-out options

### What the kit gives you natively
The MPF300-VIDEO-KIT has **no native PCIe, USB 3, or Ethernet** (the PolarFire
*Splash*/*Eval* kits do; the *Video* kit does not). Direct host links are:

| Interface | Direction | Use |
|---|---|---|
| **USB 2.0 Mini-B → onboard FlashPro5** | PC ↔ FPGA | **JTAG** (bitstream + debug) and a **USB-UART** (low-rate console/telemetry — the analog of the Au FT2232 status line). **Not** for image data. |
| **HDMI 2.0 RX** | PC → FPGA | PC GPU feeds video in (passthrough source) |
| **HDMI 2.0 TX** (+ ADV7511 HDMI 1.4 TX) | FPGA → PC/display | projector **or** an HDMI **capture card** on the PC |
| **CSI-2 TX connector** | FPGA → host | drive a downstream MIPI receiver (e.g. Raspberry Pi — see below) |
| **HPC FMC connector** | expansion | add a high-speed host link (USB3 / 10GbE) |

The single direct PC cable is the **USB Mini-B (JTAG + UART)** — bulk/processed data
must leave over **HDMI** or an added link.

### Data-out options for "output only phase video"

| Option | Hardware cost | Gateware / driver effort | Bandwidth | Notes |
|---|---|---|---|---|
| **HDMI-TX → 4K60 capture card** | ~$100–300 | **none** | up to 4K60 | Simplest; matches "phase-video-out" directly. **Best first choice.** |
| **CSI-2 TX → Raspberry Pi** (CM4/Pi 5 for 4 lanes) | ~$35–80 + lane adapter | FPGA CSI-2 framing + Pi **device-tree overlay + dummy/adapted sensor driver** | 2 lanes ≈ 2 Gbps; 4 lanes ≈ 4–6 Gbps | Turns the Pi into a full **Linux host** (network/storage/display + 3-D recon) on the rig. Well-trodden (Pi `bcm2835-unicam` + dummy sensor driver). Lane count must match exactly or no data. |
| **USB3 FMC** (FX3 DIY or HiTech Global) | ~$100–700 | **heavy** — write PolarFire-side GPIF/USB controller (no free Microchip USB3 device IP); ref designs are Xilinx-targeted | ~375–400 MB/s (FX3) | Marginal: fits 1080p RAW8 @ ≤120 fps, not 240 fps, not 4K. |
| **10 GbE FMC** (VIDEO-DC-USXGMII) | higher | medium (Microchip-supported IP) | ~1.25 GB/s | Most bandwidth for bulk *non-video* data to host. |

**CSI-2 TX → Raspberry Pi specifics:** the FPGA acts as a MIPI camera source into the
Pi's CSI-2 receiver. Needs (1) a connector/lane adapter (FPGA CSI-2 TX → Pi 15-pin/2-lane
or 22-pin/4-lane FFC), (2) valid CSI-2 framing (frame/line short packets + data type;
the Microchip CSI-2 TX IP packetizes), (3) Pi-side device-tree overlay + the kernel
**dummy sensor driver** (no I²C sensor emulation needed). Use a **4-lane CM4/Pi 5** for
high frame rates (≈1 Gbps/lane Unicam limit).

**Recommendation:** start with the **HDMI-TX → capture card** (zero gateware) to prove
the phase-video output; adopt **CSI-2 TX → Pi** if you want a cheap on-rig Linux host;
reach for **10 GbE FMC** only if you need bulk non-video data faster than USB3.

---

## 9. Alternative platforms & multi-HDMI

This project keeps bumping into two ceilings: (1) **I/O scarcity** on small boards and
(2) the **Artix-7 bandwidth ceiling** (no 4K). The platform ladder below maps options
against both.

### Platform ladder

| Platform | 4K HDMI passthrough | I/O headroom | On-chip host | Au RTL reuse | Notes |
|---|---|---|---|---|---|
| **Alchitry Au V2** (XC7A35T) | ❌ ~1080p (HR-bank TMDS) | ❌ 2 banks (~104 IO) | ❌ (Ft+ for USB3) | n/a (current) | current board; I/O-starved |
| **Alchitry Pt V2** (XC7A100T) | ❌ ~1080p | ✅ 4 banks (206 IO, two-sided) | ⚠️ GTP→PCIe (DIY) | ✅ same Vivado/RTL | fixes I/O wall + 3× fabric + 4× 6.25 Gbps GTP; **not** 4K |
| **AMD ZCU106** (XCZU7EV) | ✅ native dual HDMI, 4K (HDMI through GTH transceivers; retimers only) | ✅ 2× FMC | ✅ Arm A53 + PCIe/USB3/GbE | ✅ Xilinx→Xilinx | `EK-U1-ZCU106-G`, ~$3,234; camera via FMC (e.g. LI-IMX274) |
| **Microchip PolarFire Video Kit** (MPF300T) | ✅ 4K60 (RX IP often 4K30) | ✅ | ❌ no native USB3/PCIe | ❌ full primitive rewrite | bundles cameras; low power; see §1–8 |
| **Microchip PolarFire *SoC* Video Kit** (MPFS250T) | ✅ 4K60 (FPGA SerDes2) | ✅ + **mikroBUS GPIO** | ✅ **5× RISC-V (Linux) + GbE/USB2/PCIe** | ❌ rewrite (same IP as MPF300) | adds on-board host + easy GPIO; 250K LE (vs 300K); see §12 |

### Alchitry stacking facts (verified against pinout source)
- **Au V2 = 2 banks.** Hd V2 fills **Bank A** (HDMI on A45–A78); Ft+ fills **low Bank A
  (A3–A42) + low Bank B (B3–B24)**. Hd and Ft+ are **disjoint** (coexist), but Hd+Ft+
  consume 100% of Bank A — so the camera GPIO + switches (currently on A5/A6/A11/A12,
  which collide with the Ft+ control bus) **must move to free Bank B pins**. Mechanically
  Hd+Ft+ need an **Sp spacer**.
- **Pt V2 = two-sided.** The same Alchitry name maps to **different FPGA balls per side**
  (verified: Hd pin `A45` = ball **J4 / FPGA bank 35** on TOP vs **C19 / FPGA bank 16** on
  BOTTOM). So **two Hd V2 boards (one TOP, one BOTTOM) = four independent HDMI ports** on
  one Pt — no pin conflict. This is **not** possible on the Au V2.

### Multi-HDMI (1 input → 3 outputs)
- **Do not sync video over the Ft+/USB.** The FT601 is a USB *device* bridge (host-only,
  can't peer-to-peer), and USB's µs–ms non-deterministic latency cannot genlock HDMI
  (genlock needs a shared clock + ns-accurate VSYNC).
- **Best design: one FPGA, one clock domain.** Recover the input pixel clock once and drive
  all 3 output serializers from it + a shared VSYNC counter → outputs are **genlocked by
  construction**. On a **Pt V2 with two Hd V2 (top+bottom) = 4 HDMI ports**, this gives
  1-in/3-out at **≤1080p** with no inter-FPGA sync.
- **If multiple FPGAs are ever required** (e.g. 4K or >4 ports): genlock via a **shared
  clock fanout + a dedicated 1-wire sync** (GPIO/LVDS), and move pixel data over a **direct
  board-to-board parallel/LVDS link** — keep USB/Ft+ as the host link only.

### Host-link options recap
- **Ft+ (FT601 USB 3.0):** turnkey, FTDI D3XX driver, ~400 MB/s. Fits 720p120 / 1080p120.
- **Pt GTP → PCIe 2.0 / SFP:** ~2 GB/s but **DIY** — needs a breakout connector (no known
  Alchitry PCIe/SFP element) + heavy host-side DMA/driver work. The Pt has the I/O room to
  keep the Ft+, so it's an upgrade path, not a replacement.
- Neither Au nor Pt has native USB3; their onboard USB-C is the FT2232 (program + UART).

### MIPI on Alchitry (Artix-7)
Artix-7 has **no native MIPI D-PHY** — a MIPI element would need an external resistor
network (Xilinx XAPP894) or a bridge IC, practically capping it at ~2 lanes / ~1080p. The
Pt's extra I/O makes a MIPI element easier to *fit*, not easier to *build*. UltraScale+
(ZCU106) and PolarFire have proper MIPI/transceiver support.

---

## 10. External-camera GPIO access (FMC breakout)

The architecture in §2 pulls the camera **into the fabric over MIPI**. But the Au's simpler
model — an **external camera over a 4-line GPIO trigger** (trigger-out, first-frame-out,
mode-in, ready-in, via the Br V2 → DB9) — is still a valid fallback on PolarFire. Two parts to
it: the logic, and the physical pins.

### The logic ports cleanly
The 4-line protocol RTL (`cam_pace` debounce/reset, trigger / ready / mode / first-frame
handshake) is vendor-neutral — it recompiles in Libero and just needs pin assignments. No
redesign.

### The pins are the catch — there is no easy GPIO header
Unlike the Au (where the **Br V2** gave a literal 0.1″ GPIO header → DB9), the MPF300 Video Kit
exposes **no 0.1″ pin header and no PMOD** (UG0856). User I/O is:

| On-board I/O | Externally wireable? |
|---|---|
| **FMC HPC connector (J14)** — `HA0:12` + `LA0:33` ≈ 47 diff pairs (~90+ single-ended) + 8 XCVR lanes | ✅ the only route to arbitrary GPIO |
| **4** user/debug LEDs (FPGA-driven) + 12 power-rail status LEDs, 2 push-buttons (SW1/SW2), 4 DIP switches (SW6), reset (SW3) | ✗ tied to on-board parts, not broken out |

So it is the **opposite of the Au's problem**: I/O-*rich*, but locked behind one FMC connector.
To wire 4 camera lines you need an **FMC breakout card**:

| Option | Price | Notes |
|---|---|---|
| **AMD HW-FMC-XM105-G** (XM105 debug card) | ≈ **$159** | **Recommended.** HPC; multiple **0.1″ headers** (jumper-wire ready) + SMA/clock. Passive VITA 57.1 → vendor-neutral, works on J14. NCNR. |
| IAM Electronic FMC HPC→LPC Breakout | ≈ $234 | Passive; rows C/D/G/H → 1.27 mm pads + 2.54 mm grid (solder, not header). |
| IAM Electronic FMC (LPC) Breakout | ≈ $170 | LPC seats in the HPC slot; a 4-line trigger lives entirely in the LPC region, so this suffices. |

### Two gotchas
1. **Set VADJ/VCCIO to 3.3 V** (jumpers **J24 → 3V3**, **J25 → 3.3 V**) so the GPIO bank matches
   the camera's 3.3 V TTL lines — same level as the Au camera interface. Wrong VADJ → no level
   match.
2. **The board has one FMC slot — now three-way contended.** J14 is wanted by (a) this
   external-GPIO-camera breakout, (b) the §8 host-link cards (USB3 / 10 GbE FMC), and (c) the
   §11 FMC MIPI-camera card. Pick one. Using the **bundled J5 daughtercard** for the camera frees
   the FMC for a host link or GPIO breakout; putting the camera on the **FMC** (§11) consumes it.

---

## 11. Camera ingest over FMC (MIPI CSI-2)

The chosen camera path: use the FMC slot for a **multi-port MIPI CSI-2 RX card with standard
ribbon (FFC) connectors**, rather than the proprietary J5 daughtercard.

### Why FMC instead of J5
The CSI-2 RX port **J5** is a **Microchip-proprietary board-to-board mezzanine** (2× 4-lane MIPI
+ clk + I²C + power → Bank 2), built only to mate the bundled dual-IMX334 card. There is **no
off-the-shelf J5 → ribbon adapter** — adapting it directly means a custom mezzanine PCB. The FMC
route avoids that: a **VITA 57.1 FMC LPC MIPI card** presents standard CSI **FFC** connectors
(e.g. 4× 4-lane / RPi-style) and routes the D-PHY to FMC LA pairs.
> **Must be RX-capable.** Microchip's **VIDEO-DC-MIPITX is TX-only** (FPGA→Pi) — not for camera
> input. Use an RX card (e.g. the open-source CircuitValley FMC LPC MIPI card, or a 4-port equiv.).

### The IP — free, and not locked to J5
Microchip ships the **MIPI CSI-2 Receiver Decoder IP** (v5.1):

| Property | Value |
|---|---|
| Lanes | **1 / 2 / 4 / 8** (4-pixels-per-clock in 4-/8-lane mode) |
| PHY | D-PHY |
| Data types | RAW-8/10/12/14/16, RGB-888, embedded |
| Output | Native, **AXI4-Lite Video**, **AXI4-Stream Video** |
| Licensing | **Free** as encrypted Verilog (clear-text RTL is license-locked) |

**Why it runs over FMC, not just J5:** the IP does not do the D-PHY electrically — per UG it "must
be used in conjunction with the PolarFire **MIPI IOD Generic** interface blocks and a **PLL**." That
front-end is built from PolarFire's **`IOD Generic` (IOG) I/O primitives + PLL**, which are
**pin/bank-based** — instantiate them on the FMC LA pairs and the same decoder receives the camera
over the FMC. The bundled card merely happens to route to Bank 2; nothing forces that.

### The ingest chain (all Microchip-provided)
```
FMC MIPI card → [PF IOD Generic + PLL] → MIPI CSI-2 Rx Decoder → AXI4-Stream
                 (D-PHY front-end)         (free IP)               ↓
  CoreI2C (sensor init) ───────────────────────────────→ Bayer/CFA → image
                                                           pipeline → Video DMA → DDR4
```
- **PF_IOD Generic + PLL** — D-PHY RX front-end
- **CoreI2C** — per-sensor register init
- **MIPI Training Lite IP** — recommended for **>500 Mbps/lane**
- Bayer / image-enhancement / **Video DMA** — the DG0849 / reference-design blocks

For a **4-port** card: instantiate **one Rx Decoder per active camera** (or 8-lane mode for one
high-bandwidth sensor).

### Verify before committing
1. **MIPI-D-PHY-capable bank + VCCIO** on the FMC LA pins — D-PHY uses sub-LVDS-class I/O; confirm
   in the kit pinout that enough FMC LA pairs land on a bank the IOD Generic supports for MIPI (the
   FMC card supplies the termination). The one electrical check.
2. **Per-lane rate ceiling (IOD path).** `>500 Mbps` → add Training Lite IP; `≥1.5 Gbps` → de-skew
   packets unsupported. The IOD/GPIO D-PHY path is lower than the transceiver-based VIDEO-DC-MIPITX
   (2.5 Gbps/lane) — budget the sensor's HS line rate against it.

**Net:** camera-over-FMC is fully supported by free Microchip IP — same CSI-2 Rx Decoder, just with
its IOD-Generic front-end placed on FMC pins. The real per-sensor work is the I²C init + matching
the decoder to the sensor's format/lane count, not IP availability.

---

## 12. PolarFire SoC Video Kit (MPFS250) — alternative platform

A second board worth weighing: the **PolarFire SoC Video Kit** (`MPFS250-VIDEO-KIT`, device
**MPFS250TS-1FCG1152I**). Same FCG1152 package and the same video IP as the MPF300 kit, but it
adds a hardened **5-core RISC-V microprocessor subsystem (MSS)** running Linux, plus far more
on-board connectivity.

### What it adds over the MPF300 kit
| Feature | Why it matters here |
|---|---|
| **mikroBUS sockets** (J49/J50, 8-pin ea., FPGA I/O on Bank 1) | The **easy GPIO header the MPF300 kit lacks** — the 4-line camera trigger maps straight onto it (no FMC breakout). Plus the MikroElektronika **Click** ecosystem (isolation, level-shift, RS-422). |
| **RISC-V SoC** (4× U54 + 1× E51, Linux) | On-board host: control plane, sensor I²C init, EDID writes, and the **3-D reconstruction** can move on-board (see below). |
| **Gigabit Ethernet** (RJ45) | Native **data-out for phase video / results** — the MPF300 kit had none (§8). |
| **USB 2.0** (ULPI / USB3320), **PCIe Gen2 ×4**, **eMMC / microSD** | Host links + local storage, all native. |
| **CAN, SPI, multiple UARTs** | Control + telemetry (`status_line` UART ports over). |

Keeps everything the MPF300 kit had: **HDMI 2.0, dual-cam MIPI CSI-2 RX, FMC HPC (8 SerDes), DDR4
+ LPDDR4**. Trade-off: **250K LE vs 300K LE** (slightly less fabric for the §4 phase pipeline),
SoC/Linux bring-up complexity, and likely higher cost.

### HDMI 2.0 is FPGA-handled (Rx and Tx)
Confirmed in the board guide: **HDMI 2.0 Tx → Tx SerDes2** and **HDMI 2.0 Rx → Rx SerDes2** of the
PolarFire SoC — the **FPGA transceivers do the TMDS**. The external **PI3HDX1204B/E** parts are
**re-drivers/equalizers** (SerDes-CML ↔ HDMI levels), *not* HDMI receiver/transmitter ASICs, so
they don't touch the pixels. The pixel-access / in-stream-processing story holds fully (vs. the
MPF300 kit's secondary HDMI **1.4** TX, which *does* use a real ASIC, the ADV7511).

### What the RISC-V is for (if you use it)
Fabric stays the real-time datapath; the RISC-V absorbs the host PC + external controller:
- **Control plane** — configure IP over AXI4-Lite, write the merged EDID, run sensor I²C init,
  drive the scan-sequencing FSM at frame granularity.
- **Final 3-D reconstruction on-board** — fabric writes a **phase map** to DDR4 over the coherent
  **FIC**; the RISC-V does unwrapping / calibration / triangulation (your `host/` Qt C++ ports
  here). Branchy, non-real-time → CPU-friendly.
- **Data-out / services** — stream results over GbE/USB/PCIe, host a control API or web UI,
  log to microSD/eMMC.

**The dividing line:**
| Belongs in **fabric** (per-pixel, line-rate) | Belongs on **RISC-V** (per-frame or slower) |
|---|---|
| pattern replacement, TLP trigger, MIPI ingest, demosaic, arctan/phase math | config, sequencing, EDID/I²C writes, unwrap, calibration, triangulation, networking, UI |

> The U54s are **~600 MHz, in-order, no vector unit** — they **cannot** do per-pixel work at video
> rate (4K60 ≈ 5 CPU-cycles/pixel across all 4 cores). Keep pixels in fabric; even the board's
> "ML at the edge" demos run the NN in a **fabric NPU**, not on the RISC-V.

### Keeping the control plane in fabric instead (no-processor option)
The Au model — fabric FSMs + a UART host-command path — **ports directly**; the MSS is optional and
can be held in reset. `pixel_pipe`/`mode_select`/`cam_pace`, the UART register-control path (your
`usb-control-port` branch), and the EDID/I²C logic are all vendor-neutral RTL.

- **One adaptation:** where a Microchip IP expects *a processor* to write its AXI4-Lite config
  (HDMI **Dynamic EDID**, MIPI **sensor I²C init**), supply a small **fabric AXI4-Lite master FSM**
  (the ~20-line walker we use for EDID) — or drop a **soft Mi-V** into fabric for C convenience
  (still not the hard MSS).
- **SoC-specific caveat — DDR lives in the MSS.** On the MPF300 (plain FPGA) kit the DDR4
  controller is **fabric IP** → a pure-fabric design with frame buffering needs **no processor**.
  On the **SoC** kit the DDR controller is **inside the MSS**, so even fabric DDR access needs
  *minimal* MSS bring-up (not full Linux). *(Confirm in Libero.)* If you stay **Au-style** —
  on-the-fly patterns, no DDR, host PC does reconstruction — the MSS is **entirely unused** on
  either board.

### Strategic call
The SoC kit's value *is* the RISC-V host + GbE + mikroBUS. If the intent is **"fabric owns the FSM
+ host commands, like the Au,"** the **plain MPF300 kit is the cleaner match** (fabric DDR, no MSS
at all). Choose the SoC kit only if you want its **peripherals** (mikroBUS GPIO, native GbE) and/or
the **on-board reconstruction host** — even if the control plane stays in fabric.

---

## Sources
- [PolarFire Video & Imaging Kit](https://www.microchip.com/en-us/development-tool/mpf300-video-kit-ns)
- [UG0872 — PolarFire MPF300T Video Kit User Guide](https://www.mouser.com/datasheet/2/268/microsemi_polarfire_mpf300t_fpga_video_kit_user_gu-3420302.pdf)
- [Secure HDMI video pipelines with HDCP on PolarFire (4K60)](https://www.microchip.com/en-us/about/media-center/blog/2026/secure-hdmi-video-pipelines-with-hdcp-using-polarfire-fpgas)
- [HDMI TX IP User Guide](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/ip_cores/directcores/HDMI_TX_IP_UG.pdf)
- [HDMI RX IP User Guide (UG0863)](https://ww1.microchip.com/downloads/aemdocuments/documents/fpga/ProductDocuments/UserGuides/microsemi_hdmi_rx_ip_user_guide_ug0863_v1.pdf)
- [HDMI RX IP core tool page (native / AXI4-Stream, 1 vs 4 pixel mode, pre-programmed EDID)](https://www.microchip.com/en-us/products/fpgas-and-plds/ip-core-tools/hdmi-rx)
- [AN4768 — HDMI Loopback Design Application Note](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ApplicationNotes/ApplicationNotes/PolarFire_FPGA_HDMI_Loopback_Design_Application_Note_AN4768.pdf)
- [DG0849 — PolarFire 4K Dual Camera Video Kit Demo (edge detection / PiP / image enhancement)](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/polarfire_4k_dual_camera_video_kit_dg0849_v5.pdf)
- [ADV7511 Hardware User Guide (DDC master / sink-EDID read, regs 0xC4/0x7E)](https://www.analog.com/media/en/technical-documentation/user-guides/ADV7511_Hardware_Users_Guide.pdf)
- [PolarFire Transceiver User Guide](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/PolarFire_FPGA_and_PolarFire_SoC_FPGA_Transceiver_User_Guide_VB.pdf)
- [Libero SoC licensing](https://www.microchip.com/en-us/products/fpgas-and-plds/fpga-and-soc-design-tools/fpga/licensing)
- [BenQ TK700 specifications](https://www.benq.com/en-us/projector/gaming/tk700/spec.html)
- [BenQ TK700 at B&H Photo ($1,499)](https://www.bhphotovideo.com/c/product/1686755-REG/benq_tk700_3200_lumen_4k_uhd.html)
- [VIDEO-DC-SLVS (SLVS-EC / Sony IMX530)](https://www.microchip.com/en-us/development-tool/VIDEO-DC-SLVS)
- [Sony Pregius S IMX530 — 24.5 MP global shutter (FRAMOS)](https://framos.com/products/sensors/area-sensors/imx530aamj-es-24000/)
- [Receive FPGA MIPI CSI-2 Tx on Raspberry Pi (forum)](https://forums.raspberrypi.com/viewtopic.php?t=354794)
- [Raspberry Pi CSI-2 usage — Unicam + dummy sensor driver](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/camera/csi-2-usage.adoc)
- [HiTech Global USB3 SuperSpeed FMC module](https://www.hitechglobal.com/FMCModules/FMC_USB3.htm)
- [EZ-USB FX3 SuperSpeed (GPIF II ~400 MB/s)](https://cypress.com/products/ez-usb-fx3-superspeed-usb-30-peripheral-controller)
- [AMD ZCU106 Evaluation Kit (EK-U1-ZCU106-G)](https://www.amd.com/en/products/adaptive-socs-and-fpgas/evaluation-boards/zcu106.html)
- [ZCU106 HDMI Example Design (AMD Wiki)](https://xilinx-wiki.atlassian.net/wiki/spaces/A/pages/18842436)
- [Alchitry Pt V2](https://shop.alchitry.com/products/alchitry-pt)
- [Alchitry-Labs-V2 pinout source (PtV2TopPin / PtV2BottomPin)](https://github.com/alchitry/Alchitry-Labs-V2/tree/master/src/main/kotlin/com/alchitry/labs2/hardware/pinout)
- [Xilinx XAPP894 — D-PHY (MIPI) solutions on 7-series](https://www.xilinx.com/support/documentation/application_notes/xapp894-d-phy-solutions.pdf)
- [UG0856 — PolarFire FPGA Video Kit User Guide (board I/O: FMC HPC J14, LEDs, switches)](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/PolarFire_FPGA_Video_Kit_UG0856_V2.pdf)
- [AMD HW-FMC-XM105-G FMC XM105 Debug Card](https://www.xilinx.com/products/boards-and-kits/hw-fmc-xm105-g.html)
- [IAM Electronic FMC breakout / loopback modules](https://www.iamelectronic.com/shop/produkt/fpga-mezzanine-card-fmc-hpc-to-lpc-breakout-board/)
- [PolarFire MIPI CSI-2 Receiver Decoder IP User Guide (1/2/4/8 lanes, IOD Generic + PLL front-end)](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/ip_cores/directcores/MIPI_CSI2_Receiver_Decoder_IP_UG.pdf)
- [VIDEO-DC-MIPITX — MIPI Transmit FMC Card (TX-only; not for camera input)](https://www.microchip.com/en-us/development-tool/video-dc-mipitx)
- [CircuitValley FMC LPC MIPI CSI/DSI card (open-source, TX+RX, RPi-style FFC)](https://www.circuitvalley.com/2026/02/fmc-linux-mipi-csi-dsi-camera-pga-zynq-ultrascale-xilinx-fpga-camera-emulation.html)
- [PolarFire SoC Video Kit (MPFS250-VIDEO-KIT)](https://www.microchip.com/en-us/development-tool/mpfs250-video-kit)
- [PolarFire SoC FPGA Video Kit User Guide (mikroBUS J49/J50, HDMI 2.0 SerDes2, GbE/USB/PCIe)](https://ww1.microchip.com/downloads/aemDocuments/documents/FPGA/ProductDocuments/UserGuides/PolarFire_SoC_FPGA_Video_Kit_User_Guide.pdf)
- [polarfire-soc-video-kit-reference-design (Libero + Linux reference designs)](https://github.com/polarfire-soc/polarfire-soc-video-kit-reference-design)
