# LauMipiCamera_Alchitry_Stack — Schematic Build Spec

_DF40 stacking daughter board that mates the **Alchitry Pt V2** stack and brings a
**The Imaging Source DMM 36SR0234-ML** 22-pin MIPI CSI-2 camera (onsemi AR0234CS, global shutter,
1920×1200) into the FPGA through a **soft D-PHY
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
> 3. ~~**36S TRM confirmation**~~ — **RESOLVED** against the *DMM 36SR0234-ML Technical Reference
>    Manual* (The Imaging Source, last update Dec 2025). Pinout, I²C levels/addresses, and trigger
>    level are now confirmed (§5). **All camera I/O is 3.3 V** — no level translation needed.
> 4. **Pt V2 stack compatibility** + DF40 pin-1 mirroring (face-down plugs), per ROADMAP §8.
> 5. **NEW — lane count.** The camera exposes **4 data lanes**; this board wires **2**. At full
>    resolution/frame rate 2 lanes is not enough (§5.3). Decide 2-lane@60fps vs 4-lane@120fps
>    **before layout** — it changes the pair count, the resistor count, and the bank-13 pinout.
> 6. **NEW — register documentation.** *"The data sheet for the AR0234CS image sensor is not
>    publicly available."* (TRM §7.) Register settings must come from The Imaging Source support.
>    This is a **gateware** risk, not a PCB risk, but it is on the critical path for bring-up.

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
| `CAM_TRIG` | trigger input to camera (pin 17) | output |
| `CAM_STROBE` | strobe / exposure-active from camera (pin 18) | input |
| `SW_HVSV` | scan orientation (H vs V) | input |
| `SW_BLUE` / `SW_GREEN` / `SW_RED` | colour enables | input |

### 1.3 Power
| Net | Source | Use |
|---|---|---|
| `+3V3` | DF40 Site A, 50-pin pin 1 | camera supply + bank-14 logic |
| `+1V8` | see gate 2 | VCCO13 only (HS + LP bank supply). **Not** I²C — camera I/O is 3.3 V (§5.2). |
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
| **R_SDA, R_SCL** | I²C pull-ups (2.2–4.7 kΩ) | 0402 | F.Cu | pull to **`+3V3`** (confirmed §5.2; abs max 3.8 V) |
| **R_TRIG** | series ~33 Ω | 0402 | F.Cu | trigger to camera, 3.3 V (§5.2) |
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

> Gate 3 is closed: camera I/O is **3.3 V** (§5.2), so these stay in bank 14 and no translator is
> needed. `CAM_STROBE` (pin 18) still needs a bank-14 ball assigned — spare Bank-B signals remain.

---

## 5. Camera: The Imaging Source **DMM 36SR0234-ML**

Monochrome 36S-series module, **onsemi AR0234CS**, 1920×1200 (2.3 MP), **global shutter**,
**120 fps** at full resolution, **10-bit mono** output. 3.3 V (±5 %) single supply, ≈260 mA.
30×30×6 mm. Complete module — on-board 25 MHz `INCK` and regulators, so this board supplies no
sensor clock and no sensor rails.

> Global shutter + a hardware trigger input is exactly the combination structured-light needs;
> the sensor's own **strobe output** (pin 18) gives us exposure feedback for free.

### 5.1 22-pin connector — **confirmed** against the TRM

*"compatible to the 22-pin Raspberry Pi MIPI Interface."* Mating FPC part on the camera:
**Würth 687122149022**, 22-pin, 0.5 mm pitch.

| Pin | TRM name | Type | → net |
|---|---|---|---|
| 1 | GND (**capacitively coupled**) | GND | `GND` — see note |
| 2 / 3 | CH1 N / CH1 P | O | `CAM_D0_N` / `CAM_D0_P` |
| 4 | GND | GND | `GND` |
| 5 / 6 | CH2 N / CH2 P | O | `CAM_D1_N` / `CAM_D1_P` |
| 7 | GND | GND | `GND` |
| 8 / 9 | DCK N / DCK P | O | `CAM_CK_N` / `CAM_CK_P` |
| 10 | GND | GND | `GND` |
| 11 / 12 | CH3 N / CH3 P | O | `CAM_D2_*` — **NC in 2-lane**, wired in 4-lane (§5.3) |
| 13 | GND | GND | `GND` |
| 14 / 15 | CH4 N / CH4 P | O | `CAM_D3_*` — **NC in 2-lane**, wired in 4-lane (§5.3) |
| 16 | GND | GND | `GND` |
| 17 | `GPIO1_3V3` | I/O | `CAM_TRIG` — **trigger input** |
| 18 | `GPIO2_3V3` | I/O | `CAM_STROBE` — **strobe output** (was assumed NC — **wire it**) |
| 19 | GND | GND | `GND` |
| 20 | `I2C_SCL_3V3` | I/O | `CAM_SCL` |
| 21 | `I2C_SDA_3V3` | I/O | `CAM_SDA` |
| 22 | `+3V3` | PWR | `+3V3` |

> **Pin 1 is not a plain ground.** The TRM marks it *"(GND) capacitive coupled."* Treat it as a
> shield/return through the camera's own capacitor — bond to the ground pour, but do not rely on
> it as the module's DC return (pins 4/7/10/13/16/19 are the real grounds).

### 5.2 Electrical — all camera I/O is **3.3 V**

*"All I/Os have the same I/O voltage of 3.3 V."* This **resolves gate 3** and kills the old
contingency about moving I²C/trigger onto bank-13 balls: `CAM_SCL`, `CAM_SDA`, `CAM_TRIG`, and
`CAM_STROBE` all stay in **bank 14 @ 3.3 V**.

| Item | Symbol | Pins | Abs max | Recommended |
|---|---|---|---|---|
| Supply | `+3V3_D` (VCC) | 22 | −0.3 … **+5.5 V** | +3.1 / **+3.3** / +3.5 V |
| GPIO | `GPIO1`, `GPIO2` | 17, 18 | −0.3 … **VCC** | +2.9 / 3.3 / VCC |
| I²C | `I2C_SCL`, `I2C_SDA` | 20, 21 | −0.5 … **+3.8 V** | +2.9 / 3.3 / VCC |

> I²C pull-ups go to **+3V3**, never to a higher rail — abs max on those pins is 3.8 V.
> `R_SDA` / `R_SCL` therefore pull to `+3V3`; the `+1V8` option in §1.3 is dead.

**I²C devices (7-bit):** `0x10` = AR0234CS image sensor · `0x50` = AT24C256C EEPROM.

**Power-up sequence (TRM §7.2):** supply 3.3 V, then **wait 350 ms** before writing sensor
registers. The CCI master must hold off that long after `+3V3` is good.

### 5.3 Lane count — 2 lanes cannot carry full resolution

The module outputs **4 data lanes** (CH1…CH4) plus the clock. Payload at full res:

```
1920 × 1200 × 120 fps × 10 bit = 2.7648 Gb/s   (before blanking overhead)
```

| Wiring | Per-lane rate | vs XAPP894 800 Mb/s | vs HR `-2` ISERDES ~1.0–1.25 Gb/s |
|---|---|---|---|
| **2 lanes @ 120 fps** | 1382 Mb/s | ✗ 1.7× over | ✗ over |
| **2 lanes @ 60 fps** | 691 Mb/s | ✓ (tight once blanking is added) | ✓ |
| **4 lanes @ 120 fps** | 691 Mb/s | ✓ (tight once blanking is added) | ✓ |

So the resistor front end **can** run this camera — but only at 60 fps on 2 lanes, or at the full
120 fps if all 4 lanes are wired (5 pairs: 15 resistors, 10 LP inputs). Both land at the same
691 Mb/s/lane; 4-lane buys frame rate, not margin.

> ⚠️ Whether the module can be *configured* for 2-lane output is **unknown** — the AR0234CS
> register map is not public (gate 6). If it only ever emits 4 lanes, the 2-lane option does not
> exist and this board must wire 5 pairs. **Confirm with The Imaging Source before layout.**

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
- ~~36S TRM: 22-pin map, I²C level + address, trigger electrical level~~ — **done** (§5).
- Pt V2 ↔ Br/Hd stack mate confirmed against mechanical drawing (gate 4).
- **2-lane vs 4-lane** (gate 5, §5.3). The sensor is an **AR0234CS**, not an AR0521. Whether a
  2-lane output mode exists is unconfirmed — its register map is not public (gate 6).
- `Pt2.xdc` port→ball mapping for the pins in §4 (bank 13 @ 1.8 V, bank 14 @ 3.3 V).
