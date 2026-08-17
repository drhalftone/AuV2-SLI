# AuV2-SLI Hardware Expansion Roadmap

_Last updated: 2026-08-17_

Forward-looking plan for stacking add-on boards on the Alchitry Au V2 SLI system,
and the **FPGA bank/pin allocation** needed so the current SLI design, a future
**Alchitry Ft+** (USB 3.0), and a future **MIPI CSI-2** camera board can all coexist.

> **Status:** planning only. Nothing here is fabricated. The DF40 stacking daughter
> board below is **fab-gated** on the `Au2.xdc` Bank-B remap being done and bench-tested
> (see *Risks & gates*).

---

## 1. Physical stack

Top → bottom:

```
  [ camera/config daughter board ]   ← future (this roadmap)
  [ Alchitry Br V2 ]                 ← breakout (GPIO + DF40 pass-through) — OPTIONAL, see below
  [ Alchitry Hd V2 ]                 ← HDMI
  [ Alchitry Au V2 ]                 ← Artix-7 FPGA (mainboard)
```

The daughter board is the **top** layer, so it only needs the connectors it taps
(no pass-through above it). It mates the **top sockets of whatever board is below it**,
so it carries DF40 **plugs (…DP) on its bottom side, facing down**.

**The Br is optional.** Every Alchitry board in the stack carries the *same* DF40
sites — a socket on top and a plug on the bottom at the identical Site A/B/C geometry,
with **pin = signal number** — so each site passes straight through the stack and the
boards can be ordered freely. Because the daughter board taps the DF40 sites directly
**and** provides its own breakout (JST-7 camera connectors + the SPDT config switches),
it duplicates the Br's only two jobs — GPIO breakout + DF40 pass-through. The Bank B
signals it needs (B27–B36) pass through untouched, so it can mate **directly onto the
Hd** and the **Br drops out of the stack entirely**. Keep the Br only if you still want
its 0.1″ header breakout for something else.

> **Caveat — as currently routed it is a *terminal* (top-only) board.** J1–J3 are all
> `…DP` plugs on the bottom (B.Cu); there are **no top-side `…DS` sockets**. So it can
> sit on top of anything (in place of the Br, or above the Br), but **nothing can stack
> above it**. To make it truly stack-order-independent (usable mid-stack with a board
> above it), add the matching top-side `DF40C-50DS` / `DF40C-80DS` sockets so the signals
> pass through it as well. See §5.4.

---

## 2. Current state

- **FPGA design (`Au2.xdc`)** uses **Bank A**:
  - **Bank A high (A45–A78):** HDMI TX/RX (TMDS pairs + I²C/CEC/HPD). _Fixed._
  - **Bank A low:** camera 1 + config (see remap table), plus camera 2 (`A24`, `A30`).
  - **Bank B: unused** (verified — nothing in the SLI design touches it).
- **`LauCameraTrigger_Alchitry_3xJST/`** — current camera-trigger breakout (ordered).
  DB-9 replaced by 3× on-board JST-7 (`SM07B-SRSS-TB`); SMD-only assembly; THT hand-soldered;
  Basler pull resistors left DNP (Alvium push-pull doesn't need them). Plugs into the Br's
  **0.1″ headers** — _not_ the DF40 stack.

The daughter board below is the evolution: connect to the Alchitry **DF40 stack connectors**
directly instead of the 0.1″ headers.

---

## 3. DF40 connector geometry (from `Br.step`)

Extracted from the Br V2 STEP assembly transforms (board frame, mm). Each site has a
socket on top and a plug on the bottom; board ≈ 1.44 mm thick (top Z ≈ +1.52, bottom ≈ −0.08).

| Site | X | Y | Top (socket) | Bottom (plug) | Bank / use |
|---|---|---|---|---|---|
| **A** | 16.5 | 41.0 | DF40C-50DS | DF40C-50DP | 50-pin: **power/special** (+3V3, VCC, JTAG, LEDs, analog) |
| **B** | 38.0 | 41.0 | DF40C-80DS | DF40C-80DP | 80-pin: **Bank A** |
| **C** | 38.0 | 4.0  | DF40C-80DS | DF40C-80DP | 80-pin: **Bank B** |

- **DF40 pin number = Alchitry signal number** (verified from Br schematic, both banks).
  GND on pins ≡ 1,2 (mod 6): 1,2,7,8,13,14,19,20,25,26,… per 80-pin connector.
- **+3V3** is on the **50-pin** (Site A) **odd pins 1–13**. **VCC** is on its even pins 2–16 —
  ⚠️ **a separate, higher rail; never use VCC for 3.3 V logic.** The 80-pin connectors carry
  **only signals + GND** (no +3V3).
- All connectors axis-aligned (no rotation). Bottom-side plugs mate face-to-face →
  **pin-1 is mirrored** (verify before fab).

---

## 4. FPGA bank allocation (for SLI + Ft+ + MIPI coexistence)

The Ft+ (FT601, 32-bit FIFO, ~42 IO) consumes the **low** pins of **both** banks. HDMI owns
Bank A high. The only region free of both is **Bank B high** → that's where the cameras/config
and MIPI go.

| Region | Pins | Owner |
|---|---|---|
| Bank A low | A1–A43 | **Ft+** — FT601 control (CLK/WR/RD/OE/TXE/RXF/WAKEUP/RESET) + BE0–3 + **D16–D31** |
| Bank A high | A45–A78 | **HDMI TX/RX** (current SLI) — passes through the Ft+ |
| Bank B low | B1–B26 | **Ft+** — FT601 **D0–D15** |
| Bank B high | B27–B78 | **camera/config daughter board** (B27–B36) + **future MIPI** (B39+) |

> Pin budget note: Ft+ (~42) + MIPI (~10) > one bank, but they split across Bank A low /
> Bank B low (Ft+) and Bank B high (MIPI + cameras), so it fits. Running **SLI + Ft+** at
> once **requires** the camera signals to move off Bank A low → the remap below.

---

## 5. Future board: DF40 stacking camera/config daughter board

### 5.1 Connectors it mates (bottom side, plugs, facing down) — ALL THREE for retention

Populate **all three** stack connectors so the board clicks in firmly (3-point mating;
two at Y=41, one at Y=4) onto the board below it — the **Br or, if the Br is omitted, the
Hd** (the sites are identical on both). Both 80-pin sites use the same part, so it's
2× `DF40C-80DP` + 1× `DF40C-50DP`.

| Site | X, Y | Part | Electrical role |
|---|---|---|---|
| A | 16.5, 41.0 | `DF40C-50DP` (50-pin) | **+3V3** (pin 1) + GND; rest NC. Power + mechanical. |
| B | 38.0, 41.0 | `DF40C-80DP` (80-pin, Bank A) | **Mechanical ONLY** — all Bank A *signal* pins **No-Connect** (HDMI + future Ft+ territory; must not route). GND pins may tie to the board ground plane. |
| C | 38.0, 4.0  | `DF40C-80DP` (80-pin, Bank B) | The 8 signals (B27–B36) + GND; rest NC. Signals + mechanical. |

> ⚠️ Site B is there purely so the board doesn't cantilever/rock loose. Routing any Bank A
> signal there would collide with HDMI / a future Ft+. Keep all its I/O pins NC.

### 5.4 Optional: make it fully stack-order-independent

As routed today the board is **terminal** — bottom-side `…DP` plugs only, so it must be the
topmost board. The whole Alchitry stack works because every board carries the same DF40 sites
**through** (socket on top + plug on bottom, pin = signal number); this board breaks that chain
because it has no top sockets.

To let it live anywhere in the stack (e.g. with an Ft+ or another add-on above it), add the
mating **top-side sockets** at the same Site A/B/C geometry — `DF40C-50DS` at A and `DF40C-80DS`
at B and C — and pass each pin straight through (top socket pin _n_ ↔ bottom plug pin _n_,
respecting the pin-1 mirror). That turns it into a true pass-through board at the cost of the
extra sockets and the through-routing. If it will only ever be on top, leave it terminal.

### 5.2 Signal remap — Bank A low → Bank B high

Verified against the official **Au V2** pinout (`AuV2Pin.kt`, `version = V2`). All target
pins confirmed free, LVCMOS33-capable, non-clock, and in the Ft+ pass-through range.

| Function | now (Bank A) | → new (Bank B) | FPGA ball | DF40 pin (Site C) | pull |
|---|---|---|---|---|---|
| trigger-ready (cam in) | A17 | **B27** | R11 | 27 | — |
| trigger (cam out) | A23 | **B28** | R16 | 28 | — |
| first-frame (cam out) | A29 | **B29** | R10 | 29 | — |
| mode / HDMI-switch (cam in) | A35 | **B30** | R15 | 30 | PULLDOWN |
| HvsV (scan orient) | A5 | **B33** | K5 | 33 | PULLDOWN |
| Blue enable | A6 | **B34** | N16 | 34 | PULLUP |
| Green enable | A11 | **B35** | E6 | 35 | PULLUP |
| Red enable | A12 | **B36** | M16 | 36 | PULLUP |

GND available at B31/B32 (and every ≡1,2 mod 6 pin). +3V3 from the 50-pin (Site A).

> This remap must be mirrored in **`Au2.xdc`** (reassign the 8 ports to balls
> R11/R16/R10/R15/K5/N16/E6/M16, keeping the pull settings above) for the board to function.

### 5.3 On-board circuitry
- **4× SPDT DIP switch** on the config lines **B33/B34/B35/B36** (HvsV/Blue/Green/Red):
  pole → signal, throws → **+3V3 / GND**. SPDT = break-before-make (can't short the rails);
  direct replacement for the old 3-pin jumpers.
- **Tie-high/low resistor positions** on the 4 camera lines **B27–B30**: one footprint to
  **+3V3**, one to **GND**, populate **at most one** ("high or low, not both").
  - Package: **1206** (large, hand-solderable).
  - ⚠️ B28/B29 are FPGA **outputs** (trigger/first-frame) — tie positions there are for bench
    use only; mark clearly so they aren't populated in normal operation.
- Camera connectors: carry over the 3× JST-7 (`SM07B-SRSS-TB`) approach from the 3xJST board
  if cameras attach here.

---

## 6. Alchitry Ft+ (USB 3.0) — **WORKING as of 2026-07-30**

No longer a future board. The FT601 datapath is written, built, flashed and
**byte-exact verified** on a Pt V2 + Ft+ — see [`ft_usb_video/`](ft_usb_video/).

- **FT601Q**, 32-bit 245-sync FIFO; ~**42 FPGA IO** (≈ a full bank's worth).
- **Measured ceiling: 348 MB/s** (2.79 Gbps) sustained — **87 % of the 400 MB/s
  theoretical**. Use 348 for budgeting. At 1280×1024 that is **212 fps packed 10-bit**
  / 265 fps 8-bit mono / **133 fps at 16 bpp** (10-in-16 padded), with the data verified
  byte-exact at that rate. Padding 10 bits into 16 costs 37 % of the frame rate for no
  extra information.
  > **Superseded 2026-08-17: "at a 120 fps operating point 16 bpp still fits, with
  > ~10 % margin" IS WRONG, and it was tried.** 348 MB/s is a BARE-LINK number — the
  > FT601 fed by a pattern generator, with no camera and no DDR. With the real
  > pipeline running, the same `ft_bench_async.py` measures **231.7 MB/s**, because
  > the reader is now competing with the capture writer for one MIG port. 16 bpp at
  > 120 Hz needs 314.6 MB/s and delivered **88 fps**, not 120.
  >
  > Sampled while saturated, *both* ends were at their limit: the DDR→USB FIFO ran
  > dry 32 % of the time **and** the FT601 back-pressured 40 %. Neither side
  > dominated, so no amount of tuning one of them closes a 36 % gap. **Budget
  > against ~232 MB/s end-to-end, not 348.**
  > **Superseded number:** this said 308 MB/s until 2026-07-31. That figure was our own
  > host-side memcpy, not the link — the reader allocated, zero-filled and copied a
  > fresh buffer every transfer. A zero-copy reader gets 348. Notably, **queue depth
  > made no difference to peak** (342 at depth 1); the win was entirely in removing the
  > memory churn. See `ft_usb_video/host/ft_bench_async.py`.
  >
  > 348 MB/s now *just* covers the sensor's full 210 fps packed 10-bit (344 MB/s
  > needed) — about 1 % margin. That is enough to say the link is no longer the hard
  > blocker, but too thin to design against without headroom.
- Verified with a closed-form byte-exact check, not just a throughput number:
  300/300 frames, every pixel, zero drops.
- Uses **Bank A low** (control + BE + D16–D31) and **Bank B low** (D0–D15); passes the high
  pins of both banks through (so HDMI on A45–A78 and the daughter board on B27+ survive).
- **Incompatible with the current SLI pinout** until the camera/config signals move to Bank B
  (Section 5.2). After the remap, SLI + Ft+ coexist.

> **Design lesson worth carrying to every other source-synchronous bus in this
> project.** The first working build streamed at full rate with zero dropped frames
> while silently corrupting `ft_data[31:16]`: the bus was driven combinationally
> *and* had no `set_output_delay`, so the interface was never in the timing graph
> and Vivado reported a clean WNS. Fix was an IOB-registered launch plus real
> constraints. **Throughput and drop counts cannot see this class of bug** — any bus
> another chip clocks in needs a registered launch, a real `set_output_delay`, and a
> byte-exact end-to-end check. Details in `ft_usb_video/README.md`.

---

## 6.5 Future: EXTERNAL HDMI SYNC — genlock the camera to the projector

**Status: not built. The FPGA half is small; the open question is a WIRE.**

### Why it is needed

The camera now runs at a locked **120.000 Hz** (§ `LauPythonCamera_Pt_Stack/README.md`),
paced by a counter on the FPGA's own 100 MHz crystal:

```verilog
trig_per = 833_333          // cycles of clk -> 8.33333 ms
trig0    = (tcnt < hi_cyc)  // 10 us pulse, marks the frame start
cam_trigger[0] = trig0      // sensor is in triggered mode, reg 192 bit 4
```

That gives the *right rate* but **not the right phase**. The projector's 120 Hz comes
from a different oscillator, so the camera and the display drift against each other
continuously. For structured light — where the whole point is knowing which pattern
was on screen during which exposure — that drift is the entire problem. A camera that
is merely *near* 120 Hz is not synchronised to anything.

### This is EXTENDING a proven mechanism, not inventing one

The Au V2 SLI design **already fires a camera-shutter pulse on VSYNC** — that is how the
Alvium/Basler cameras are triggered today (root [`README.md`](README.md), *Pass-through
with top-left-pixel trigger*: it samples the top-left pixel, and when it changes it
fires the shutter on the next VSYNC). VSYNC-locked triggering is established, working
behaviour in this project.

Two things are new for the PYTHON 1300:

1. **The pulse has to reach the Pt V2** (see the blocker below).
2. **A user-programmable phase shift.** The existing trigger fires *on* VSYNC with no
   adjustable delay. Structured light wants the exposure placed deliberately inside the
   display frame — after the panel has settled, or straddling a pattern change — so the
   delay becomes a runtime parameter rather than "immediately".

### What it does

Derive the trigger from the HDMI **vertical sync** instead of a free-running compare,
with a **user-programmable phase shift** so the exposure can be placed anywhere inside
the display frame.

```
    HDMI vsync  ──►  2FF sync  ──►  edge detect  ──►  [ delay = phase ]  ──►  trigger0
                                                            ▲
                                                   runtime opcode, 0..8.33 ms
```

### Effort: the logic is hours, the signal is the question

The trigger generator, the 10 µs pulse, and the runtime command channel **already
exist** — genlock only changes *what restarts the counter*. Roughly 25 lines:

```verilog
reg [2:0] vs_s;  always @(posedge clk) vs_s <= {vs_s[1:0], hdmi_vsync};
wire vs_edge = (vs_s[2:1] == 2'b01);                  // rising, 2FF-synced
if      (vs_edge)                      begin dcnt <= 0; armed <= 1; end
else if (armed && dcnt == trig_phase)  begin trig0 <= 1; armed <= 0; end
else                                         dcnt <= dcnt + 1;
```

Plus one new runtime opcode for `trig_phase` (20 bits covers a full frame at 100 MHz),
one pin, and one XDC line.

Three things to build in, none of them hard:

- **Watchdog fallback to free-running** if no vsync edge arrives for ~2 frame periods.
  Without it, unplugging HDMI stops the camera dead.
- **Budget check**: `phase + exposure + 6.86 ms readout` must fit inside the frame
  period. Enforce it host-side so it fails loudly instead of silently dropping frames.
- Vsync jitter passes straight through to the trigger. 2FF sampling at 100 MHz adds
  ≤ 10 ns against an 8.33 ms period — irrelevant.

### THE ACTUAL BLOCKER: vsync is not on this FPGA

**There is no HDMI in the camera design at all** — no vsync/TMDS in
`pt_ft_plus_bottom_timed.xdc`, and `cam_frame_ft`'s ports are camera LVDS + FT601 +
DDR3 only. The camera stack runs on the **Pt V2**; the HDMI passthrough work is on the
**Au V2**, a separate physical board.

So before any of the above matters, a sync signal has to physically reach the Pt V2 —
over the DF40 stacking connector or a flying lead. **That is a wiring/PCB decision, and
it gates the feature.** If the two boards stay separate, this needs a board revision;
if it can be routed on the existing stack, it is an afternoon.

**A shortcut worth considering first:** the Au V2 already *emits* a VSYNC-locked
shutter pulse on GPIO for the DB9 cameras. Feeding that existing output into the Pt V2
as `cam_trigger[0]` needs **no HDMI decoding on the Pt at all** — one wire and an edge
detect, instead of routing TMDS or a recovered vsync. The programmable phase could then
live on *either* board: on the Au (delay the existing pulse) or on the Pt (delay the
received edge). Deciding which board owns the phase is the first design question, and
it is cheaper to answer than the wiring one.

---

## 7. Future board: MIPI CSI-2 camera

> **Now has its own design roadmap → [`MIPI_CSI2_ROADMAP.md`](MIPI_CSI2_ROADMAP.md).**
> That document targets the **Alchitry Pt V2** (`XC7A100T-2FGG484I`) and carries the
> **package-verified** pin map, the bank-13 / 1.8 V VCCO plan, the soft-D-PHY + CSI-2 gateway
> design, and the XAPP894 front-end gate. The notes below are the original Au-centric reservation,
> kept for context — see the dedicated roadmap for the current plan.

- D-PHY receiver needs **differential pairs** on LVDS-capable pins (1 clock + 2–4 data lanes →
  ~6–10 pins).
- **Reserve Bank B high, B39–B54**, and especially the **clock-capable pins
  A41/A42/A47/A48 and B41/B42/B47/B48** — keep these free for the CSI-2 clock/lanes.
- The camera/config block sits at B27–B36, leaving B39+ clean for MIPI.

> **Pt V2 update:** on the Pt V2 these signals land in **bank 13** (the only stack bank that
> supports 1.8 V VCCO), with the clock lane on a verified **MRCC** pair. See
> [`MIPI_CSI2_ROADMAP.md`](MIPI_CSI2_ROADMAP.md) §3 for the confirmed ball map.

---

## 8. Risks & gates

**Before fabricating the daughter board:**
1. **`Au2.xdc` Bank-B remap done + bench-tested** — the board is wired to Bank B; today's
   bitstream still drives Bank A. They must match.
2. **Pin-1 mirroring** verified for the bottom-side, face-down DF40 plugs (0.4 mm pitch is
   unforgiving).
3. **+3V3 pin** on the 50-pin re-confirmed (odd pins 1–13); never wire VCC.
4. **3D mate** checked against `Br.step` (connector XY + orientation).
5. **0.4 mm DF40 = fine-pitch SMD** — needs stencil/reflow assembly, not hand soldering.

**Confirmed / de-risked:**
- Bank B is unused by the current SLI design (camera 2 is on Bank A: A24, A30).
- All 8 remap target pins are free, 3.3 V-capable, non-clock, Ft+ pass-through.
- DF40 connector placements and pin=number convention verified from `Br.step` + schematic.

---

## 9. Source references

- **DF40 connector placements:** extracted from `Br.step` assembly transforms (Site A/B/C above).
- **Au V2 pinout (ball ↔ A/B name):** `alchitry/Alchitry-Labs-V2` →
  `src/main/kotlin/com/alchitry/labs2/hardware/pinout/AuV2Pin.kt` (`version = V2`).
  (Note: the `AuPin.kt` in the same dir is **V1** — different balls; don't use it for V2.)
- **Br V2 schematic:** DF40 pin↔signal map (`BrSchematic.pdf`), pin = signal number.
- **Ft+ schematic:** FT601 bank usage (`FtPlusSchematic.pdf`), Bank A/B low consumed.
- **Current constraints:** `constrs_1/imports/RTL/Au2.xdc`.
