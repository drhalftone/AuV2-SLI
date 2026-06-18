# AuV2-SLI Hardware Expansion Roadmap

_Last updated: 2026-06-18_

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
  [ Alchitry Br V2 ]                 ← breakout (GPIO + DF40 pass-through)
  [ Alchitry Hd V2 ]                 ← HDMI
  [ Alchitry Au V2 ]                 ← Artix-7 FPGA (mainboard)
```

The daughter board is the **top** layer, so it only needs the connectors it taps
(no pass-through above it). It mates the **Br's top sockets**, so it carries DF40
**plugs (…DP) on its bottom side, facing down**.

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

### 5.1 Connectors it mates (bottom side, plugs, facing down)
- **80-pin `DF40C-80DP` @ Site C (38.0, 4.0)** — Bank B signals (camera/config) + GND.
- **50-pin `DF40C-50DP` @ Site A (16.5, 41.0)** — **+3V3** (e.g. pin 1) + mechanical anchor.
- (Site B / Bank A 80-pin is **not** placed — it's HDMI + Ft+ territory.)

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

## 6. Future board: Alchitry Ft+ (USB 3.0)

- **FT601Q**, 32-bit FIFO, 400 MB/s; ~**42 FPGA IO** (≈ a full bank's worth).
- Uses **Bank A low** (control + BE + D16–D31) and **Bank B low** (D0–D15); passes the high
  pins of both banks through (so HDMI on A45–A78 and the daughter board on B27+ survive).
- **Incompatible with the current SLI pinout** until the camera/config signals move to Bank B
  (Section 5.2). After the remap, SLI + Ft+ coexist.

---

## 7. Future board: MIPI CSI-2 camera

- D-PHY receiver needs **differential pairs** on LVDS-capable pins (1 clock + 2–4 data lanes →
  ~6–10 pins).
- **Reserve Bank B high, B39–B54**, and especially the **clock-capable pins
  A41/A42/A47/A48 and B41/B42/B47/B48** — keep these free for the CSI-2 clock/lanes.
- The camera/config block sits at B27–B36, leaving B39+ clean for MIPI.

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
