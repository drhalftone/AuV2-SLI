# LauMipiCamera_Alchitry_Stack — Schematic Build Spec

_DF40 stacking daughter board that mates the **Alchitry Pt V2** stack and brings a
**The Imaging Source 36S-series 22-pin MIPI CSI-2 camera** into the FPGA through a **soft D-PHY
(XAPP894 resistor network)** on **bank 13 @ 1.8 V**. Also carries the 4 relocated config switches
on **bank 14 @ 3.3 V** (their move off bank 13 is what lets bank 13 run at 1.8 V)._

See [`../MIPI_CSI2_ROADMAP.md`](../MIPI_CSI2_ROADMAP.md) for the architecture, line-rate budget,
and gateware plan, and [`../ROADMAP.md`](../ROADMAP.md) §3 for the DF40 stack geometry. This board
follows the stacking pattern of
[`../LauCameraTrigger_Alchitry_Stack/SCHEMATIC.md`](../LauCameraTrigger_Alchitry_Stack/SCHEMATIC.md).

> **Pre-fab gates — resolve before layout:**
> 1. ~~**D-PHY front-end network**~~ — **RESOLVED**, see §3. Values taken from XAPP894 v1.0.1
>    Figure 11. Two decisions remain open (§3.3): the 150 Ω vs 100 Ω termination question and the
>    800 Mb/s line-rate ceiling.
> 2. **VCCO13 = 1.8 V** — confirm *how* it is set on the Pt V2 (on-board option vs. supplied over
>    the DF40). If the daughter board must source it, add a +1.8 V feed to the VCCO13 DF40 pin.
> 3. **36S TRM confirmation** — the 22-pin pin assignment, I²C logic level (1.8 vs 3.3 V), I²C
>    address, and trigger-input electrical level. Pinout below is the **Raspberry Pi 22-pin
>    standard**; verify against The Imaging Source's reference manual.
> 4. **Pt V2 stack compatibility** + DF40 pin-1 mirroring (face-down plugs), per ROADMAP §8.

---

## 1. Net names

### 1.1 MIPI (camera side → front end → FPGA)
| Net | Lane | FPGA side |
|---|---|---|
| `CAM_CK_P` / `CAM_CK_N`  | clock | HS diff → CLK pair |
| `CAM_D0_P` / `CAM_D0_N`  | data 0 | HS diff |
| `CAM_D1_P` / `CAM_D1_N`  | data 1 | HS diff |
| `LP_CK_P` / `LP_CK_N`    | clock LP | single-ended `HSUL_12` (§3.2) |
| `LP_D0_P` / `LP_D0_N`    | data 0 LP | single-ended `HSUL_12` (§3.2) |
| `LP_D1_P` / `LP_D1_N`    | data 1 LP | single-ended `HSUL_12` (§3.2) |

> HS and LP are derived from the **same** physical camera pair through the XAPP894 network
> (§3). "HS diff" goes to the FPGA differential input; "LP" taps go to single-ended inputs.

### 1.2 Control / config
| Net | Meaning | Dir (FPGA) |
|---|---|---|
| `CAM_SCL` / `CAM_SDA` | I²C / CCI camera control | bidir |
| `CAM_TRIG` | trigger input to camera | output |
| `SW_HVSV` | scan orientation (H vs V) | input |
| `SW_BLUE` / `SW_GREEN` / `SW_RED` | colour enables | input |

### 1.3 Power
| Net | Source | Use |
|---|---|---|
| `+3V3` | DF40 Site A, 50-pin pin 1 | camera supply + bank-14 logic |
| `+1V8` | see gate 2 | VCCO13 / LP & HS reference / I²C if 1.8 V |
| `GND` | any GND pin (≡1,2 mod 6) | ground |

---

## 2. Components (BOM)

| Ref | Part | Footprint | Side | Notes |
|---|---|---|---|---|
| **J_CAM** | 22-pin 0.5 mm FPC/FFC, bottom-contact | match 36S cable | F.Cu | MIPI camera connector |
| **J1** | DF40C-50DP-0.4V (Hirose) | DF40C-50DP | B.Cu | Site A — +3V3 + GND |
| **J2** | DF40C-80DP-0.4V | DF40C-80DP | B.Cu | Site B (Bank A) — mechanical only, all I/O NC |
| **J3** | DF40C-80DP-0.4V | DF40C-80DP | B.Cu | Site C (Bank B) — signals + GND |
| **R_T0–2** | **150 Ω** diff termination ×3 | 0402 | F.Cu | one across each HS pair, **near J3** (§3.1). XAPP894 `R9`. See §3.3 — out of `ZID` spec by design. |
| **R_LP0P/0N…2P/2N** | **100 Ω** series ×6 | 0402 | F.Cu | LP tap isolation, 2 per lane (§3.1). XAPP894 `R6`/`R7`. |
| **SW1–SW4** | SPDT (4-pos DIP or 4× discrete) | DIP/SMD | F.Cu | HvsV / Blue / Green / Red |
| **R_SDA, R_SCL** | I²C pull-ups (2.2–4.7 kΩ) | 0402 | F.Cu | pull to camera I²C rail (1.8 **or** 3.3 — gate 3) |
| **R_TRIG** | series ~33 Ω (or level pad) | 0402 | F.Cu | trigger to camera; level per gate 3 |
| **C…** | 0.1 µF decoupling + bulk (4.7–10 µF) | 0402/0805 | F.Cu | on +3V3 and +1V8 near loads |

> No oscillator / no sensor power tree: the 36S is a **complete module** (single 3.15–3.45 V
> input, on-board clock + regulators). We only provide +3V3, the front end, I²C, and trigger.

---

## 3. D-PHY analog front end (XAPP894) — per lane ×3

MIPI runs two modes on each pair:
- **HS:** ~200 mV differential (sub-LVDS) — the image data.
- **LP:** 1.2 V single-ended on each wire — the control/handshake.

### 3.1 The circuit (XAPP894 v1.0.1, Figure 11 "FPGA Compatible D-PHY Receiver")

Three resistors per lane. No divider, no comparator, no AC coupling. Both FPGA inputs sit
directly on the camera pair; the LP taps are **series isolation only**, feeding high-impedance
single-ended inputs.

```
 camera P ──┬──────────────────────────────► FPGA *_P ─┐
            │                                          │ LVDS differential input (HS)
            │        ┌─[ R_LP_P  100Ω ]──► FPGA LP_*_P │   → IBUFDS → ISERDES
            ├────────┘                                 │
         [ R_T 150Ω ]  (differential, across P/N)      │
            ├────────┐                                 │
            │        └─[ R_LP_N  100Ω ]──► FPGA LP_*_N │
            │                                          │
 camera N ──┴──────────────────────────────► FPGA *_N ─┘
```

| Ref (per lane) | Value | Placement | Purpose |
|---|---|---|---|
| `R_T` | **150 Ω** | across `P`/`N`, close to the FPGA | HS differential termination (XAPP894 `R9`) |
| `R_LP_P` | **100 Ω** | series, `P` → LP input | LP tap isolation (XAPP894 `R6`) |
| `R_LP_N` | **100 Ω** | series, `N` → LP input | LP tap isolation (XAPP894 `R7`) |

**×3 lanes (CK, D0, D1) ⇒ 3× 150 Ω + 6× 100 Ω = 9 resistors, 0402.**

### 3.2 I/O standards — LP is `HSUL_12`, *not* `LVCMOS18`

XAPP894 is explicit that receiving 1.2 V LP levels on an `LVCMOS18` input is marginal: *"the swing
of the 1.2V low-power D-PHY transmitter is not much more than the minimum requirement to let the
FPGA LVCMOS input trip accordingly. This issue is eliminated when the receiver uses the HSUL_12
I/O-standard."* Figure 11 uses `HSUL_12` on both LP inputs.

| Path | IOSTANDARD | Notes |
|---|---|---|
| HS `*_P` / `*_N` | `LVDS` (differential input) | external `R_T`; **no internal `DIFF_TERM`** in Fig. 11 |
| LP `LP_*_P` / `LP_*_N` | `HSUL_12` | 1.2 V input levels regardless of bank VCCO |

XAPP894: *"For 7 series FPGAs, LVDS, HSTL, LVCMOS_18, and HSUL_12 inputs can be joined in a 1.8V
powered I/O bank."* This is what lets the whole front end live in bank 13 @ 1.8 V. **6 LP inputs
confirmed** (2 per lane × 3).

### 3.3 Two open decisions

1. **150 Ω is deliberately out of MIPI spec.** XAPP894's own Table 2 gives `ZID` (differential
   input impedance) as **80 / 100 / 125 Ω** (min/nom/max) — the Figure 11 value of 150 Ω exceeds
   the 125 Ω maximum. This is one reason AMD labels the resistor network *"for proprietary use
   only"* and *"not a compliant solution."* Options: ship 150 Ω verbatim (as simulated in
   Hyperlynx/SPICE and hardware-tested on the D-PHY FMC board), or use 100 Ω for spec compliance
   at the cost of leaving the validated circuit. **Recommend a footprint that accepts either and
   fitting 150 Ω first**, since that is the only value AMD actually characterized.
2. **Line rate.** XAPP894 characterizes this circuit *"up to 800 Mb/s between an FPGA and a MIPI
   device"* over 300 mm. `MIPI_CSI2_ROADMAP.md` budgets **1.0–1.25 Gb/s per lane**. The compatible
   network is **not** shown to reach that rate. Either re-budget to ≤800 Mb/s (fewer lanes ⇒ lower
   resolution/frame rate, or 4-lane at reduced per-lane rate), or move to the compliant solution
   (an external Meticom `MC20901`-class PHY), which changes this board substantially.

### 3.4 Layout consequence — resistors belong near the FPGA, not the camera

XAPP894's PCB guideline is *"Place the necessary resistors and capacitors as close as possible to
the FPGA."* On this daughter board the FPGA is **on the Pt V2, across the DF40 stack** — so "close
to the FPGA" means **clustered at J3 (Site C)**, not at the FFC. The HS pairs therefore run the
full length of the board *unterminated*, then terminate just before dropping through J3.

Route all three HS pairs as 100 Ω differential, length-matched, on an outer layer over solid
ground (per the `MipiHS` netclass rules in `.kicad_dru`). Keep left/right turns balanced and use
45° or arced corners — never 90°.

---

## 4. FPGA pin assignment (Pt V2, package-verified)

Confirmed against `xc7a100tfgg484pkg.txt`. HS pairs + LP inputs all in **bank 13 (VCCO 1.8 V)**;
switches/I²C/trigger in **bank 14 (3.3 V)**.

### 4.1 MIPI HS pairs — bank 13
| Lane | Alchitry P / N | FPGA ball P / N | Pair / clock |
|---|---|---|---|
| **CK** | B47 / B45 | V13 / V14 | `L13` **MRCC** → BUFIO/BUFR |
| **D0** | B42 / B40 | Y11 / Y12 | `L11` SRCC |
| **D1** | B48 / B46 | U15 / V15 | `L14` SRCC |

(Higher B-number = FPGA **P**. Spare MRCC `L12` = W11/W12 = B41/B39 left free.)

### 4.2 MIPI LP single-ended — bank 13 (freed switch/GPIO pairs)

All six are `HSUL_12` inputs (§3.2), each fed through its 100 Ω series resistor.

| Net | Alchitry sig | FPGA ball |
|---|---|---|
| `LP_CK_P` / `LP_CK_N` | B33 / B35 | AB10 / AA9  (`L8`) |
| `LP_D0_P` / `LP_D0_N` | B34 / B36 | AB15 / AA15 (`L4`) |
| `LP_D1_P` / `LP_D1_N` | B51 / B53 | W10 / V10   (`L10`) |

> These are LP taps off the *same* camera pair as the HS inputs in §4.1 — they are **not** a
> separate set of camera wires. Each LP net shares a copper node with its HS counterpart.

### 4.3 Control / config — bank 14 (3.3 V)
| Net | Alchitry sig | FPGA ball |
|---|---|---|
| `SW_HVSV` | B27 | Y19 |
| `SW_BLUE` | B28 | V20 |
| `SW_GREEN` | B29 | Y18 |
| `SW_RED` | B30 | U20 |
| `CAM_SCL` | B21 | AB20 |
| `CAM_SDA` | B23 | AA19 |
| `CAM_TRIG` | B17 | V17 |

> If gate 3 finds the camera I²C / trigger are **1.8 V**, move `CAM_SCL/SDA/TRIG` onto spare
> bank-13 pins (e.g. `L2` AB16/AB17 = B54/B52, `L7` AB11/AB12 = B57/B59) so levels match without
> a translator.

---

## 5. Camera connector (22-pin) — working pinout

**Raspberry Pi 22-pin 0.5 mm standard** (36S is Pi-5/Orin compatible). **Confirm against the
36S TRM** — especially the power, trigger, and any GPIO pins.

| Pin | Signal | → net |
|---|---|---|
| 1 | GND | `GND` |
| 2 / 3 | CAM_D0_N / CAM_D0_P | `CAM_D0_N` / `CAM_D0_P` |
| 4 | GND | `GND` |
| 5 / 6 | CAM_D1_N / CAM_D1_P | `CAM_D1_N` / `CAM_D1_P` |
| 7 | GND | `GND` |
| 8 / 9 | CAM_CK_N / CAM_CK_P | `CAM_CK_N` / `CAM_CK_P` |
| 10 | GND | `GND` |
| 11–16 | D2/D3 lanes (4-lane) + GND | **NC** (2-lane design) |
| 17 | CAM_GPIO / trigger | `CAM_TRIG` (verify) |
| 18 | reserved / GPIO | NC (verify) |
| 19 | GND | `GND` |
| 20 / 21 | SCL0 / SDA0 | `CAM_SCL` / `CAM_SDA` |
| 22 | +3V3 | `+3V3` |

> The 22-pin connector is **4-lane capable**; we wire only lanes 0–1. Leaving D2/D3 as NC keeps a
> future 4-lane upgrade open (bank 13 has the spare pairs — MIPI_CSI2_ROADMAP §2).

---

## 6. Config switches (relocated here)

Each SPDT: common pole → the config net (bank 14); throws → `+3V3` / `GND`
(break-before-make ⇒ can't short the rails). Identical to the old jumper behaviour.

| Switch | Net | Throw 1 | Throw 2 |
|---|---|---|---|
| SW1 | `SW_HVSV`  | `+3V3` | `GND` |
| SW2 | `SW_BLUE`  | `+3V3` | `GND` |
| SW3 | `SW_GREEN` | `+3V3` | `GND` |
| SW4 | `SW_RED`   | `+3V3` | `GND` |

---

## 7. DF40 stack wiring

Same three-site mating as the trigger stack board (ROADMAP §3, §5):

- **J1 (Site A, 50-pin):** `+3V3` from pin 1 (odd 1–13); GND on ≡1,2 mod 6; **VCC even pins
  2–16 = NC** (separate higher rail). If gate 2 requires it, route `+1V8` to the VCCO13 pin here.
- **J2 (Site B, 80-pin, Bank A):** **mechanical only** — every Bank-A I/O pin **NC** (HDMI /
  future Ft+); GND pins may bond to the plane.
- **J3 (Site C, 80-pin, Bank B):** the MIPI HS/LP + switch/I²C/trigger signals above; GND on
  ≡1,2 mod 6; all other pins NC.

> **Br is optional** — board can mate the Hd directly (ROADMAP §1). For best HS signal integrity
> at 200 mV, prefer the shortest stack path and keep the 3 HS pairs length-matched.

---

## 8. Power & VCCO

| Rail | Value | Feeds |
|---|---|---|
| `+3V3` | 3.3 V (from Site A) | 36S camera (pin 22), bank-14 logic, switch throws |
| `+1V8` | 1.8 V | **VCCO13** (HS + LP I/O), I²C if camera is 1.8 V |

- Bank 13 **must** be VCCO 1.8 V (gate 2). Do **not** leave any 3.3 V load on bank 13 — that's
  why the switches moved to bank 14.
- Decouple +3V3 at J_CAM (camera draw) and +1V8 at the front-end / FPGA bank.
- Ground pour both layers; stitch with vias, especially under the HS pairs.

---

## 9. Open items

- ~~XAPP894 front-end values + LP detection scheme~~ — **done** (§3): 150 Ω diff term + 2× 100 Ω
  LP series per lane, LP on `HSUL_12`. Remaining: pick 150 Ω vs 100 Ω (§3.3 #1), and resolve the
  **800 Mb/s ceiling vs the roadmap's 1.0–1.25 Gb/s budget** (§3.3 #2) — this one may change the
  whole approach.
- **VCCO13 source** on the Pt V2 (gate 2).
- **36S TRM**: 22-pin map, I²C level + address, trigger electrical level (gate 3).
- Pt V2 ↔ Br/Hd stack mate confirmed against mechanical drawing (gate 4).
- Sensor **2-lane mode** selection (AR0521 supports 2- and 4-lane) to live under the ~1080p line-
  rate budget (MIPI_CSI2_ROADMAP §7).
- `Pt2.xdc` port→ball mapping for the pins in §4 (bank 13 @ 1.8 V, bank 14 @ 3.3 V).
