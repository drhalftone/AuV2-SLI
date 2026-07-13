# PYTHON 1300 Camera Board — Power Design

**Status:** Boost stage, LDO stage and sequencing all designed and costed. Layout not started.
**Mount side: RESOLVED — this board sits ON TOP of the stack (§2.5). It cannot go below the Pt.**

**Scope:** how the NOIP1SN1300A sensor gets its three supplies from the Alchitry Pt V2's `+3V3`
rail. I/O, LVDS and pin mapping are a separate document.

> **This document supersedes `LauPythonCamera_Pt_Stack/README.md`, which is built on a false
> premise (see §8). Do not carry any power-tree conclusion forward from it.**

---

## 1. What the sensor requires

From the onsemi **NOIP1SN1300A** datasheet, P1‑SN/SE/FN LVDS (ZROT) column, Table 5, p.4.

| Rail | Min | Nom | Max | Current | Sensor pins |
|---|---|---|---|---|---|
| `vdd_33` | 3.2 V | 3.3 V | 3.4 V | 140 mA | 1, 19, 29, 36 |
| `vdd_18` | 1.7 V | 1.8 V | 1.9 V | 80 mA | 6, 22, 26 |
| `vdd_pix` | **3.25 V** | **3.3 V** | **3.35 V** | 5 mA | 31, 33, 38, 40 |

Grounds: `gnd_18` = 5, 21, 27 · `gnd_33` = 20, 30, 35, 48 · `gnd_colpc` = 32, 34, 37, 39.
All tie to a single 0 V reference. `ibias_master` (pin 28) → **47 kΩ to `gnd_33`**, non-optional.
Total sensor power: **620 mW**.

**Absolute maximums** (Table 4, p.3): 3.3 V group **−0.5 to 4.3 V**; 1.8 V group **−0.5 to 2.2 V**.
Latch-up 100 mA (JESD-78).

### Sequencing (pp.17–18, Figures 18 & 19)

**Power-up: `vdd_18` → `vdd_33` → `vdd_pix`**, each step **≥10 µs**.
> *"Any other supply ramping sequence may lead to high current peaks and, as consequence, a
> failure of the sensor power up."*

**Power-down: `vdd_pix` → `vdd_33` → `vdd_18`** — the exact reverse.
> *"Any other sequence can cause high peak currents."*

**This is the hardest requirement in the design.** It is what forces the boost, and it is why a
bare tap off `+3V3` can never work (§8).

---

## 2. What the Pt V2 provides

### 2.1 `+3V3` is 3.278 V, not 3.300 V

`+3V3` comes from an **ADP5052** buck (ch.1, 4 A) fed from `VCC`. From Alchitry's sheet 7, the
ch.1 feedback divider is **R16 = 31.6 kΩ** (top) / **R15 = 10.2 kΩ** (bottom), and the ADP5052's
buck reference is **0.8 V**:

```
V(+3V3) = 0.8 × (1 + 31.6 / 10.2) = 3.278 V
```

**Validated** — the same convention predicts the Pt's other three rails exactly:

| Rail | Divider | Computed | Actual |
|---|---|---|---|
| 1V | 10.2k / 41.2k | 0.998 V | 1.0 V ✓ |
| 1V8 | 12.7k / 10.2k | 1.796 V | 1.8 V ✓ |
| 1V35 | 7.87k / 11.5k | 1.347 V | 1.35 V ✓ |
| **+3V3** | **31.6k / 10.2k** | **3.278 V** | — |

ADP5052 is **±1.5 %** over temperature. Therefore:

> ### `+3V3` = **3.229 V … 3.327 V**

Alchitry doesn't publish this. They don't have to — their own schematic determines it.

### 2.2 Consequence: `+3V3` cannot feed the sensor directly

| Rail | Sensor window | `+3V3` delivers | Verdict |
|---|---|---|---|
| `vdd_33` | 3.20 – 3.40 V | 3.229 – 3.327 V | passes, ~29 mV margin before IR drop |
| `vdd_pix` | **3.25 – 3.35 V** | 3.229 – 3.327 V | **FAILS low corner by 21 mV** |

`+3V3` is also VCCO for the FPGA's I/O banks and feeds the FTDI, QSPI flash and LEDs, so it
carries buck ripple plus every I/O transient. `vdd_pix` is the pixel array supply with no on-chip
regulation — ripple there lands directly in the image as row banding.

### 2.3 Rails on the control header we do NOT use

- **`VCC`** — raw board supply (USB VBUS via LM73100, or `JP1` "12-5V"). **Excluded by decision.**
- **`A1V8`** — the ADP5052's 200 mA aux LDO feeding the FPGA's *analog* supply. **Do not use:**
  80 mA of sensor load would crowd a shared 200 mA budget and inject LVDS noise into the FPGA's
  analog reference.

### 2.4 VBSEL (recorded so it isn't re-derived)

The Pt already fits **R62 = 10 kΩ pulldown on `VBSEL_A`** and **R61 = 10 kΩ pullup (to +2.5 V) on
`VBSEL_B`** → default (low, high) = 3.3 V, consistent with Alchitry's table.

For **2.5 V** we need (high, high), so **only `VBSEL_A` must be pulled up**. A **1 kΩ to `+3V3`**
against their 10 kΩ gives 3.278 × 10/11 = **2.98 V**, inside Alchitry's "high = 1.1–3.3 V" band.
`VBSEL_B` is already high — leave it alone.

### 2.5 Mount side — TOP of the stack. This board CANNOT go below the Pt.

**Stack order (fixed):  Pt (bottom) → Hd → Ft+ → camera (top).**

The camera reaches the Pt through the **top** connectors, passing through the Hd and Ft+.

> ### This is not a preference. Mounting below the Pt is electrically impossible.

**Reason 1 — there is no 2.5 V bank on the bottom.** The sensor's LVDS requires `LVDS_25` with
`DIFF_TERM`, and **both require VCCO = 2.5 V**. The only bank on the Pt whose VCCO can be switched
to 2.5 V is **bank 13** (Alchitry's `VB34` net physically drives `VCCO_13`, sheet 1 — the net
*name* is misleading, the connection is not). Bank 13 is brought out on the **top connectors only**:

| Connector | Bank 13 pins | Banks present |
|---|---|---|
| **Top** | **32** | 13, 14, 34, 35 |
| **Bottom** | **0** | 16, 34 only |

Banks 16 and 34 are **hard-wired at 3.3 V**. Below the Pt there is no 2.5 V bank, therefore no
`LVDS_25`, no `DIFF_TERM`, and **no way for the sensor to talk to the FPGA at all.**

**Reason 2 — the two sides are not interchangeable by design.** The **MGT / 6.6 Gb/s transceiver**
pins appear on the **bottom connectors only**. Alchitry deliberately put the differential-capable
bank on top and the gigabit transceivers on the bottom. The camera belongs on top.

**Reason 3 — the power pins are mirrored, and getting it wrong destroys the sensor.**

| Control header | Pins 1,3,5…15 (odd) | Pins 2,4,6…16 (even) |
|---|---|---|
| **Top** (J3) — *what we use* | **`+3V3`** | `VCC` |
| **Bottom** (J8) | `VCC` | `+3V3` |

On the bottom header the odd pins carry **`VCC`** — the raw 5–12 V board supply. Wiring `vdd_33`
to them would put ≥5 V on a rail whose absolute maximum is **4.3 V**. Dead sensor on first
power-up.

### 2.5.1 Control header pin map — TOP connector (what this board mates to)

| Pins | Net | Use |
|---|---|---|
| 1, 3, 5, 7, 9, 11, 13, 15 | **`+3V3`** | **All board power. Every rail derives from these.** |
| 2, 4, 6, 8, 10, 12, 14, 16 | `VCC` | **DO NOT CONNECT** (raw 5–12 V board supply) |
| 17 – 28 | `GND` | |
| **38** | **`VBSEL_A`** | Pull to `+3V3` via 1 kΩ → selects bank 13 = 2.5 V (§2.4) |
| 40 | `VBSEL_B` | Already pulled high on the Pt — **leave unconnected** |
| 37, 39, 41 | `RESET`, `DONE`, `PROGRAM_B` | |
| 43, 45, 47, 49 | `TDI`, `TDO`, `TMS`, `TCK` | JTAG |
| 42, 44, 46, 48, 50 | `A1V8`, `AVP`, `AVN`, `AVREF`, `AGND` | Not used (§2.3) |

### 2.5.3 ⚠️ Connector pin numbering — RESOLVED. Do not re-open this.

**This trap cost real time. Read it before you "fix" any connector pin number.**

Alchitry's schematics show their **bottom plugs** (DF40C-*DP) carrying element-bus net `n` on pin
**`n XOR 1`** — the odd/even rows swapped — while every **top receptacle** (DF40C-*DS) carries net
`n` on pin `n`. This is consistent across the Pt, the Hd and the Ft+ (0/186 identity on the plugs,
184/184 on the receptacles). It looks exactly like our board's plug pin numbers are all off by one.

**They are not. That comparison is invalid.**

A schematic pin number means nothing on its own — the chain that matters is:

```
  schematic pin number  ->  FOOTPRINT pad  ->  physical contact
```

Alchitry's plug pin numbers are paired with **their own Altium footprint**, whose pad numbering
mirrors the KiCad/Hirose one for the plug. Comparing their plug pin numbers against ours compares
two different footprint libraries. It proves nothing.

> ### The empirical proof: `LauCameraTrigger_Alchitry_Stack`
>
> That board was **fabbed and works on the Pt**. It puts **`+3V3` on schematic pin 1** of a
> **DF40C-50DP** plug, using the **KiCad `Hirose_DF40C-50DP` footprint**. Therefore, with that
> footprint, **plug pin N mates the Pt's top-receptacle pin N — identity, no swap.**
>
> **This board uses the identical footprints** — they were extracted directly out of that PCB into
> `LauCamera.pretty`. Same footprint, same pad numbering, same convention.

**So the mapping in §2.5.1 is correct as written**: `+3V3` on J3 pins 1, 3, 5 … 15.

**The rule:** only the **net names** (`A1..A80`, `B1..B80`, `C1..C50`) carry over from Alchitry's
drawings. **Never transfer their plug pin numbers.** Match our plug pins to the *receptacle*
numbering, which is what §2.5.1 does.

> **Note the one thing that can never reveal this bug:** GND sits on pins 1,2 / 7,8 / 13,14 … —
> pairs that map to *themselves* under an odd/even swap. Ground will look correct either way. Only
> the power and signal pins can expose it.

### 2.5.4 Still worth 30 seconds with a meter

The **Hd and Ft+ pass-throughs** are taken as straight-through (they are — their sheets show the
bus wired between top and bottom connectors), but that is the one link in the chain not proven by
a working board *in this configuration*.

**Before the sensor ever goes in the socket:** power the Pt with the Hd + Ft+ stacked, and meter
the Ft+'s exposed top connector. Confirm the **odd** control-header pins 1–15 read **3.3 V** (not
the even ones, which are `VCC` at 5 V+). That single measurement validates the pass-through *and*
the pin mapping at once — against a sensor whose absolute maximum is **4.3 V**.

### 2.5.2 Thermal note — why "top" is also the right answer for image quality

Dark current roughly doubles every ~7 °C. With Pt → Hd → Ft+ → camera, there are **two boards
between the FPGA and the sensor**, and the sensor sits at the top of the stack in open air with
the lens pointing away from the heat. Inverting the stack would put the sensor directly beneath a
1.5–2.5 W Artix-7 radiating down onto it — **thermally worse, on top of being electrically
impossible.**

If active cooling is needed, note that with the Pt at the bottom of the stack **its bottom face is
free** (its bottom connectors are unpopulated in this configuration), so a cooler can mount there
without reordering anything — subject to clearing those connectors, and subject to which face the
Artix die is actually on.

---

## 3. Architecture

```
  +3V3  (3.229 – 3.327 V; noisy)
    |
    +--> [U3] BOOST 4.46 V  ---+--> [U4] LDO 3.30 V --> vdd_33   140 mA
    |    TPS61023              |    TPS7A2033
    |                          +--> [U5] LDO 3.30 V --> vdd_pix    5 mA
    |                               TPS7A2033
    |
    +--> [U2] LDO 1.80 V ---------------------------> vdd_18    80 mA
    |    TPS7A2018
    |
    +--> [U6] SUPERVISOR 2.93 V  (TLV803S) --> sequencing (§6)
```

**The boost is not the regulator.** It manufactures headroom, because an LDO regulates only
*downward* and needs its input meaningfully above its output. A 3.278 V input cannot produce an
accurate 3.300 V. So: up, then back down.

All accuracy and noise rejection happen **in the LDOs**. The sensor sees **3.300 V ±1.5 %** (the
LDO's spec) instead of **3.278 V ±1.5 %** (Alchitry's), and the FPGA's buck ripple is rejected by
**75–95 dB** instead of passed through a ferrite.

`vdd_18` skips the boost — it already has 1.478 V of headroom from `+3V3`.

**Why not boost 3.278 → 3.300 directly?** A boost can't regulate an output barely above its input;
it saturates at ~100 % duty and passes the input through. And even a buck-boost that *can* do
3.3 → 3.3 is still a switcher: ±2–3 % accuracy, tens of mV of ripple. Both miss `vdd_pix`'s
±1.5 % window and its noise sensitivity. **No single switching part meets `vdd_pix`.**

---

## 4. Boost stage — U3, TPS61023

SOT-563 (1.2 × 1.6 mm), synchronous. Datasheet SLVSF14B.

| Parameter | Value |
|---|---|
| V_IN | 0.5 – 5.5 V (1.8 V min to *start*) |
| V_OUT setting | 2.2 – 5.5 V |
| V_REF (FB) | 580 / **595** / 610 mV |
| f_SW | **1 MHz** (V_IN > 1.5 V) |
| EN logic high | **1.2 V max** — absolute, *not* ratiometric |
| EN logic low | 0.35 / 0.42 / 0.45 V |
| UVLO rising | 1.7 / 1.8 V |
| Shutdown | **true input-to-output disconnect** |
| Start-up | ~700 µs |
| Inductor range | 0.37 – 2.9 µH |
| Effective C_OUT | 4 / 10 / 1000 µF |

### Output voltage

**R8 = 330 kΩ 1 %** (VOUT→FB), **R9 = 51 kΩ 1 %** (FB→GND):

```
V_OUT = 0.595 × (1 + 330/51) = 4.445 V        range over V_REF tol: 4.33 … 4.56 V
```

**Why 4.45 V and not lower?** The TPS7A20's PSRR is specified at V_IN = V_OUT + 1.0 V. Even at the
*low* corner, 4.33 − 3.3 = **1.03 V**, so we keep full specified PSRR under all conditions.
Dropping to 4.0 V would save ~60 mW of LDO heat but forfeit that.

R9 = 51 kΩ satisfies TI's R9 < 300 kΩ rule (divider current 11.7 µA, ≫100× the 20 nA FB leakage).

> **Why these odd values?** 330 k and 51 k are both **JLCPCB Basic** parts. The neater-looking
> 649 k / 100 k gives 4.457 V — a 12 mV difference, irrelevant — but **649 kΩ is an Extended
> part**, and paying a feeder fee for one resistor is silly. The Basic pair hits the same target
> and keeps the entire BOM (bar the hand-soldered socket) sourceable by JLCPCB.

### The numbers close

Worst case: V_IN = 3.229 V, V_OUT = 4.57 V, I_OUT = 200 mA (145 mA + margin), η = 90 %,
inductance at its −20 % corner (1.76 µH).

```
D      = 1 − 3.229/4.57                     = 0.293
I_L,dc = (4.57 × 0.200) / (3.229 × 0.9)     = 315 mA
ΔI_pp  = (3.229 × 0.293) / (1.76µH × 1MHz)  = 538 mA
I_L,pk = 315 + 538/2                        = 584 mA
```

**Peak 584 mA vs. Isat ≥ 1.2 A → 2.05× margin** ✓
Draw from `+3V3`: 4.457 × 0.145 / (3.278 × 0.9) = **219 mA** (against a 4 A rail) ✓

At this load the converter runs in **PFM / power-save** — ripple is larger, frequency varies.
Deliberate and harmless: nothing but LDO inputs touches this node, and they reject it by 75–95 dB.

### Capacitors

- **C_IN = 10 µF** at the VIN pin.
- **C_OUT = 2 × 10 µF.** A 25 V X5R part derates mildly at 4.5 V bias → ~17 µF effective: inside
  TI's 4–1000 µF range and **below 40 µF, so no feedforward capacitor is needed** (above 40 µF TI
  requires one).

---

## 5. LDO stage — U4, U5, U2

All three are **TPS7A20** family, SOT-23-5 (DBV). Pinout: **1 = IN, 2 = GND, 3 = EN, 4 = NC,
5 = OUT.** Datasheet SBVS338H.

| | Noise | PSRR | EN thresholds | Auto-discharge | RθJA (DBV) |
|---|---|---|---|---|---|
| TPS7A20 | 7 µV_RMS | 95 dB @1 kHz, 75 dB @100 kHz, 45 dB @1 MHz | V_IH **0.9 V max**, V_IL 0.3 V | **150 Ω** pulldown when EN low | 187.1 °C/W |

Each LDO needs **C_IN = 1 µF, C_OUT = 1 µF** minimum (stable with 1 µF ceramic; no noise-bypass
cap required).

### U4 — `vdd_33` (TPS7A2033, 3.3 V, 140 mA)

| Check | Value | Window | Verdict |
|---|---|---|---|
| Accuracy | 3.3 V ±1.5 % → **3.2505 – 3.3495 V** | 3.20 – 3.40 V | ✓ ~50 mV margin each side |
| Headroom | V_IN 4.34 V min − 3.3 V = **1.04 V** | dropout ≤140 mV @300 mA | ✓ |
| Dissipation | (4.457 − 3.3) × 0.140 = **162 mW** | ×187.1 °C/W | **+30 °C rise** |

### U5 — `vdd_pix` (TPS7A2033, 3.3 V, 5 mA) — *same part number as U4*

| Check | Value | Window | Verdict |
|---|---|---|---|
| Accuracy | 3.3 V ±1.5 % → **3.2505 – 3.3495 V** | **3.250 – 3.350 V** | ⚠ **0.5 mV margin** |
| Dissipation | (4.457 − 3.3) × 0.005 = **6 mW** | — | negligible |

> ### ⚠️ Two hard rules for `vdd_pix`
>
> **1. Kelvin routing is mandatory.** The ±1.5 % LDO consumes the *entire* ±1.5 % window with
> 0.5 mV to spare. At 5 mA, even 100 mΩ of copper spends the whole low-side budget. Route short
> and wide from U5's output capacitor **straight to sensor pins 31/33/38/40**. Never daisy-chain
> off `vdd_33`. There is no tighter part available — this is as good as it gets.
>
> **2. Keep total `vdd_pix` capacitance ≤ ~1.5 µF.** Power-down depends on U5's 150 Ω internal
> pulldown collapsing this rail *first* (§6). Budget: 1 µF (LDO C_OUT) + 4 × 100 nF (per sensor
> pin) = 1.4 µF → τ = 150 Ω × 1.4 µF = **210 µs**. **Do NOT add bulk capacitance to `vdd_pix`.**
> A 10 µF bulk cap here would break the shutdown ordering.

U4 and U5 are the same MPN — one reel, one JLCPCB line item — but they **must be separate
devices**, because `vdd_pix` has to rise after `vdd_33` and collapse before it.

### U2 — `vdd_18` (TPS7A2018, 1.8 V, 80 mA) — fed from `+3V3`, not the boost

| Check | Value | Window | Verdict |
|---|---|---|---|
| Accuracy | **±40 mV** → 1.76 – 1.84 V | 1.70 – 1.90 V | ✓ comfortable |
| Headroom | 3.229 V min − 1.8 V = **1.43 V** | dropout ≤205 mV @300 mA | ✓ not dropout-limited |
| Dissipation | (3.278 − 1.8) × 0.080 = **118 mW** | ×187.1 °C/W | **+22 °C rise** |

> **Note the accuracy spec.** In the SOT-23-5 (DBV) package, the ±1.5 % figure applies only for
> V_OUT ≥ 2.8 V. Below that it is **±40 mV** (= ±2.2 %). Still well inside the 1.7–1.9 V window,
> but do not write "±1.5 %" next to this rail.

### Sensor decoupling

| Rail | Per-pin | Bulk |
|---|---|---|
| `vdd_33` (4 pins) | 4 × 100 nF 0402 | 10 µF |
| `vdd_18` (3 pins) | 3 × 100 nF 0402 **+ 3 × 10 nF** | 10 µF |
| `vdd_pix` (4 pins) | 4 × 100 nF 0402 | **NONE — see rule 2 above** |

The extra 10 nF on `vdd_18` is because the sensor's **LVDS drivers run off `vdd_18` and toggle at
360 MHz**, and the sensor is socketed (~1–3 nH per contact). A 1 µF X7R 0402 self-resonates at
3–6 MHz and is inductive above that.

---

## 6. Sequencing — U6 and U7, TLV803S

**U6 and U7 = TLV803S**, 3-pin SOT-23, **active-low open-drain** reset, threshold **V_IT = 2.93 V**
(2.99 V max), 200 ms power-up delay, built-in fast-transient rejection. Needs a 0.1 µF bypass.

**Why 2.93 V is the right threshold:**
- **Never false-trips:** `+3V3` minimum is 3.229 V → **239 mV** above the worst-case 2.99 V trip.
- **Always fires in time:** far above the boost's 1.8 V UVLO and U2's ~1.95 V dropout.

The boost's own EN thresholds (V_IH 1.2 V / V_IL 0.35 V) are far too wide to use a simple divider
on `+3V3` for this — the turn-off point would land somewhere near 0.9 V, uselessly late. A
precision supervisor is required.

### ⚠️ TWO supervisors are required, not one. This is not optional.

**A single supervisor does not work, and the failure is silent.** If one open-drain RESET drives
both enable nodes, then whenever RESET is *released* the two nodes are **connected to each other**
through the series resistor. The 10 kΩ pull-up on `EN_BOOST` (to `vdd_18`) drags `EN_PIX` up to
~1.6 V — above the TPS7A20's 0.9 V V_IH — so **`vdd_pix` enables as soon as `vdd_18` appears** and
rises *simultaneously with* `vdd_33` instead of after it. That is precisely the ordering violation
the supervisor exists to prevent.

Neither workaround rescues it:
- **Resistors alone cannot.** Making the series resistor large enough to decouple the nodes when
  RESET is high makes it too weak to pull the node low when RESET asserts. The two requirements are
  in direct opposition — there is no value that satisfies both.
- **A diode-AND cannot.** A Schottky's reverse leakage at 85 °C (tens of µA through a 100 kΩ
  pull-up) falsely enables `vdd_pix`; a silicon diode's forward drop (~0.4 V at 33 µA) exceeds the
  TPS7A20's **0.3 V max** V_EN(LOW), so it never disables.

**Use one supervisor per enable node.** Each then has its own open-drain and they are genuinely
independent. Cost: $0.09.

### The enable network

| Node | Circuit |
|---|---|
| **U2.EN** (`vdd_18`) | tied directly to `+3V3` — comes up with the rail |
| **U3.EN** (boost) | **U7** RESET → **1 kΩ series** → EN node; **220 nF** to GND; U7's RESET has its own **100 kΩ pull-up to `+3V3`** |
| **U4.EN** (`vdd_33`) | tied directly to `vdd_18` |
| **U5.EN** (`vdd_pix`) | **U6** RESET directly; **100 kΩ pull-up to `vdd_33`**; **10 nF** to GND |

**The interlock is U5.EN's pull-up going to `vdd_33`, not to `+3V3`.** Before `vdd_33` exists that
pull-up sits at 0 V, so `vdd_pix` *physically cannot* enable early — the ordering is structural,
not a race between time constants.

**The 200 ms supervisor reset delay does real work:** it guarantees `vdd_18` (up in ~1 ms) is
established long before the boost is ever permitted to start.

### Power-up — `vdd_18` → `vdd_33` → `vdd_pix` ✓

1. `+3V3` rises. **U2.EN** is tied to it → **`vdd_18` up at t ≈ 1 ms.**
2. At **t ≈ 200 ms** both supervisors release (their reset delay).
3. **U3.EN** (boost) charges through 100 kΩ + 1 kΩ into 220 nF (τ = 22 ms), crossing the boost's
   1.2 V V_IH at **t ≈ 210 ms**. Boost runs → 4.46 V in ~700 µs.
4. **U4.EN** is already high (`vdd_18`, since 1 ms). U4 begins regulating once the boost's output
   passes its 1.35 V UVLO → **`vdd_33` up at t ≈ 212 ms.**
5. **U5.EN** was held at 0 V — *its pull-up goes to `vdd_33`, which did not exist until now.* It
   now charges through 100 kΩ ∥ 500 kΩ (internal) × 10 nF (τ = 833 µs), crossing 0.9 V V_IH
   **330 µs after `vdd_33` appears** → **`vdd_pix` up at t ≈ 213.5 ms.**

Separations are **ms**, against a **10 µs** requirement.

### Power-down — `vdd_pix` → `vdd_33` → `vdd_18` ✓

1. `+3V3` falls below 2.93 V → **both** supervisors assert `RESET` **low immediately** (the 200 ms
   delay applies only to *release*, not assert).
2. **U5.EN → low** (U6, direct). TPS7A20's **150 Ω auto-discharge** pulls `vdd_pix` down:
   τ = 150 Ω × 1.44 µF = **216 µs**. → **`vdd_pix` collapses FIRST.** ✓
3. **U3.EN decays** through U7's 1 kΩ into the 220 nF cap: τ = **220 µs**, crossing the boost's
   0.35 V V_IL at **t ≈ 493 µs**. Boost shuts off (**true disconnect**) → the 4.46 V node collapses
   under the 140 mA load → **`vdd_33` falls SECOND.** ✓
4. **U2 keeps regulating** `vdd_18` from `+3V3` until `+3V3` < 1.8 V + dropout ≈ **1.95 V**.
   → **`vdd_18` dies LAST.** ✓

Skew between `vdd_pix` and `vdd_33` ≈ **493 µs**, against the required 10 µs. ✓

> **This is the problem the boost created and the supervisor solves.** Once started, the TPS61023
> runs with V_IN as low as **0.5 V** — left alone it would hold 4.46 V while `+3V3` collapsed,
> making `vdd_33` outlive `vdd_18`: the exact inverse of the required order. The supervisor
> yanking U3.EN low at 2.93 V is what prevents that.

---

## 7. Bill of materials

**22 line items, 68 placements.** Generated to `LauPythonCamera_Pt_Stack/production/bom.csv` in
JLCPCB's format. **Every part except the socket is sourced and assembled by JLCPCB.**

### Actives

| Ref | Function | MPN | **LCSC** | Package | JLC |
|---|---|---|---|---|---|
| **U3** | Boost, 3.3 → 4.45 V | TPS61023DRLR | **C919459** | SOT-563 | Extended |
| **U4, U5** | LDO 3.3 V (`vdd_33`, `vdd_pix`) | TPS7A2033PDBVR | **C2862740** | SOT-23-5 | Extended |
| **U2** | LDO 1.8 V (`vdd_18`) | TPS7A2018PDBVR | **C963430** | SOT-23-5 | Extended |
| **U6, U7** | Supervisor 2.93 V, open-drain (**two — §6**) | TLV803SDBZT | **C702125** | SOT-23-3 | Extended |
| **L1** | 2.2 µH, **Isat 3.4 A**, DCR 46 mΩ, shielded, 4×4×2 mm | SMNR4020-2.2UH | **C135262** | 4×4 mm | Extended |

> ### ⚠️ L1 IS SIZED FOR THE STARTUP INRUSH, NOT THE STEADY-STATE CURRENT.
>
> **LTspice, using TI's own TPS61023 model, measured a 1.496 A peak inductor current at
> start-up** — the boost charging its output caps against its 3.7 A internal current limit. That
> is **~3× the 0.54 A steady-state peak.** It is not a numerical spike: the current sits above
> 1.2 A for **15 µs** and above 1.4 A for **6 µs**, on every single power-up.
>
> | Part | Isat | vs 1.496 A |
> |---|---|---|
> | Sunlord SWPA3012 | 1.2 A | **0.80× — saturates every startup** ✗ |
> | CENKER CKCS3015 | 1.6 A | **1.07× — no margin** ✗ |
> | **SXN SMNR4020** | **3.4 A** | **2.27×** ✓ |
>
> Both 3×3 mm parts I originally chose were sized on steady-state current and **would saturate**.
> Reducing the boost output cap does **not** fix it (22 µF → 12 µF only moved the peak
> 1.496 → 1.454 A) — the inrush is set by the converter's own soft-start, not by the caps.
>
> **Do not "optimise" L1 back to a 3×3 part.** Stock is 107,410, so there is no sourcing reason to.

### Connectors

| Ref | Function | MPN | **LCSC** |
|---|---|---|---|
| **J1, J2** | Element bus, Bank A / Bank B | DF40C-80DP-0.4V(51) | **C294544** |
| **J3** | Control header | DF40C-50DP-0.4V(51) | **C424645** |

> Both connector part numbers are taken from the **fabbed** `LauCameraTrigger_Alchitry_Stack`
> BOM — proven to source and assemble.

### Passives — all JLCPCB **Basic**

| Ref | Value | Qty | LCSC | Purpose |
|---|---|---|---|---|
| R3–R7, R12–R14 | 10 kΩ 0402 | 8 | **C25744** | Pulls on the 8 sensor CMOS inputs |
| R10, R11, R17 | 1 kΩ 0402 | 3 | **C11702** | VBSEL_A/B straps; U7 RESET → U3.EN |
| R15, R16 | 100 kΩ 0402 | 2 | **C25741** | U5.EN interlock pull-up; U7 RESET pull-up |
| R8 | 330 kΩ 0402 | 1 | **C25778** | Boost FB, top |
| R9 | 51 kΩ 0402 | 1 | **C25794** | Boost FB, bottom |
| R1 | 47 kΩ 0402 | 1 | **C25792** | `ibias_master` → `gnd_33`. **Mandatory** |
| R2 | 100 Ω 0402 | 1 | **C25076** | 100 Ω diff term on `lvds_clock_in` (FPGA→sensor) |
| C12–C22, C39 | 10 nF 0402 | 12 | **C15195** | HF decoupling; U5.EN delay |
| C8–C11, C26–C28, C38, C40 | 100 nF 0402 | 9 | **C1525** | Sensor pins; supervisor bypass |
| C1–C7 | 1 µF 0402 | 7 | **C52923** | Sensor per-pin decoupling |
| C29, C30, C34–C37 | 1 µF **0603** | 6 | **C15849** | **LDO stability caps — 0603, not 0402 (DC-bias derating)** |
| C23, C24, C31–C33 | 10 µF 0805 | 5 | **C15850** | Boost C_IN/C_OUT; `vdd_33` + `vdd_18` bulk |
| C41 | 220 nF 0402 | 1 | **C16772** | U3.EN shutdown delay |

### Hand-soldered — NOT assembled by JLCPCB

| Ref | Part | Note |
|---|---|---|
| **U1** | Andon **680-48-SM-G10-R14** — 48-pin LCC socket | **Blank LCSC. DNP for PCBA; hand-solder.** The sensor itself is inserted into the socket and is never assembled. |

> ### ⚠️ Lock the LDOs against JLCPCB "equivalent part" substitution.
> The common jellybeans (RT9080, ME6211, XC6206, AP7343, TCR2EF33) are **±2 % or worse and fail
> the `vdd_pix` window on their own** (§5). They are **not** valid substitutes at any price. If
> JLC proposes an alternative for **C2862740**, refuse it.

### Thermal budget

Dark current roughly doubles every 7 °C — keep heat away from the sensor.

| Source | Dissipation |
|---|---|
| U4 (`vdd_33` LDO) | **162 mW** (+30 °C junction rise) |
| U2 (`vdd_18` LDO) | **118 mW** (+22 °C) |
| U3 (boost, ~10 % loss) | ~72 mW |
| U5 (`vdd_pix` LDO) | ~6 mW |
| **Board total** | **~358 mW** (plus the sensor's own 620 mW) |

**Place U3, U4 and U2 away from the sensor.** U3 additionally because a 1 MHz switching node next
to an image sensor is an EMI problem — keep its SW node small and ground-shielded.

---

## 7.5 SPICE verification — real manufacturer models

Simulated in **LTspice** (ngspice could not converge TI's models — see §7.6) using **TI's actual
PSpice models**: TPS7A20 (SBVM961) ×3, TLV803S (SBVM034) ×2, TPS61023 (SLVMD68). The sensor has
**no public SPICE model** (confirmed with onsemi) — it is modelled as its datasheet loads plus the
board's real decoupling.

### Sequencing — PASSES in both directions

| Power-up (need 18 → 33 → pix) | at | gap |
|---|---|---|
| `vdd_18` | 0.99 ms | — |
| `vdd_33` | 211.65 ms | +210.7 ms |
| `vdd_pix` | 212.26 ms | **+601 µs** |

| Power-down (need pix → 33 → 18) | at | gap |
|---|---|---|
| `vdd_pix` | 261.29 ms — **first** ✓ | — |
| `vdd_33` | 262.07 ms | **+777 µs** |
| `vdd_18` | 265.77 ms — **last** ✓ | +3.70 ms |

Every gap is **60–380× the required 10 µs**. Both orders correct.

### Steady-state rails — all in spec

| Rail | Simulated | Window | |
|---|---|---|---|
| `vdd_18` | 1.7945 V | 1.70 – 1.90 V | ✓ |
| `vdd_33` | 3.2901 V | 3.20 – 3.40 V | ✓ |
| `vdd_pix` | **3.2998 V** | **3.25 – 3.35 V** | ✓ dead centre |

### Inrush — no startup lockup, but it resized the inductor

With a pessimistic 50 mΩ source impedance (DF40 + traces + Pt buck), the boost's start-up pulls
**1.49 A** from `+3V3` and droops it to **3.203 V** — still **273 mV above** the supervisors'
2.93 V trip. So there is **no power-on oscillation** (the rail does not false-trip U6/U7 and
restart the boost in a loop). That risk was checked and cleared.

But the same run measured a **1.496 A peak inductor current**, which invalidated both inductors
originally chosen. See the L1 note in §7.

### PSRR — the architecture's core claim, now measured

This was the one claim that, if false, would have made the whole redesign pointless: that a
boost+LDO actually rejects the FPGA's noise, and a ferrite tap does not.

**AC sweep, `+3V3` → sensor rail.** Negative dB = attenuation. **Positive dB = amplification.**

| Frequency | OLD: ferrite tap (FB1 → `vdd_33`) | NEW: boost + TPS7A20 |
|---|---|---|
| 10 kHz | **+0.5 dB** | −72.0 dB |
| **39.8 kHz** | **+13.5 dB** ← **worst case** | ~−72 dB |
| 100 kHz | −13.9 dB | −57.6 dB |
| 1 MHz | −55.4 dB | −64.5 dB |

> ### ⚠️ The old ferrite filter AMPLIFIES noise by 4.7× at 40 kHz — and its own design rule is why.
>
> The old README demanded: *"`FB1` must be LOW-DCR (≤ 50 mΩ). This is a hard spec, not a
> preference."* That reasoning was **correct on DC** — at 140 mA a 0.3 Ω bead drops 42 mV and
> pushes `vdd_33` below its 3.2 V floor.
>
> **But low DCR is exactly what removes the damping from the LC filter.** With 50 mΩ and 15 µF,
> Q ≈ 4.7 → an undamped resonance at 40 kHz with **+13.5 dB of gain**. The DC requirement and the
> AC behaviour are in direct conflict. The spec that saves the DC budget is the spec that wrecks
> the noise budget, and the old design never noticed.

**What it costs.** Assume 20 mVpp on `+3V3` at 40 kHz (buck ripple + FPGA I/O transients; scales
linearly). `vdd_33`'s window is ±100 mV:

| | ripple reaching `vdd_33` | |
|---|---|---|
| **OLD (ferrite)** | **94 mVpp** | **47 % of the entire spec window** |
| **NEW (LDO)** | **5 µVpp** | negligible |

**85 dB improvement — ~18,800× less noise on the sensor's analog rail.**

**Every assumption was stacked against the new design and it still wins by 85 dB:**
- Used TI's model's **pessimistic** PSRR (57.6 dB @100 kHz), not the datasheet's 75 dB.
- Gave the boost **zero credit** — assumed it passes input ripple 1:1, when its control loop
  actually rejects low-frequency ripple.
- Modelled the bead optimistically (standard 1 µH + 600 Ω parallel + DCR first-order model).

> **Note on TI's LDO model:** it implements a *crude* PSRR — flat ~75 dB with a pole at 10 kHz and
> a zero at 1 MHz (`psrr=178u, pole=10k, zero=1Meg`). It does **not** reproduce the datasheet
> curve, and is pessimistic at 100 kHz / optimistic at 1 MHz. Do not quote model PSRR as spec.

### ⚠️ What SPICE did NOT and CANNOT confirm

- **The ±1.5 % tolerance windows.** TI's models are typical-value only, with no tolerance data —
  no Monte Carlo is possible. `vdd_pix`'s accuracy rests on the **datasheet guarantee**, not on
  this simulation.
- **What the sensor does if sequencing is violated.** No model exists, and no public data on the
  internal ESD/level-shift structures. This is *why* the ordering is enforced structurally.
- **The Pt's actual rail voltage.** 3.278 V is computed from Alchitry's divider. Only a meter
  confirms it.
- **Thermal.**

## 7.6 Simulator notes (hard-won — do not repeat this)

- **ngspice cannot run these models.** The TPS61023 will not converge (parameter-less diodes, 100 GΩ
  `VSWITCH`es, an ideal-diode bridge driven by a **10 A** source). TI's own profile relies on
  PSpice's `ADVCONV`, which ngspice lacks. The TPS7A20 additionally fails on any VIN ramp slower
  than ~100 µs — and our boost soft-starts over 700 µs. **LTspice runs all of them unmodified.**
- **`method=gear` silently breaks TI's LDO model.** Use the default trapezoidal integration.
- **TI's TPS7A20 model omits the 150 Ω auto-discharge** the datasheet specifies when EN is low.
  Our power-down ordering *depends* on it, so it must be added externally (a switched 150 Ω), or
  the simulation will falsely report a slow `vdd_pix` collapse.
- TI ships `V_out` as a **global** param — make it per-instance or all three LDOs share one output
  voltage.

---

## 8. Why the old design is wrong (do not re-litigate)

The previous `README.md` is founded on: *"There is no 5 V. Everything comes from `+3.3V`… `vdd_33`
and `vdd_pix` cannot be regulated, because you cannot LDO 3.3 V down to 3.3 V."*

That premise is right. The design built on it is not. It tapped `+3V3` through ferrites and gated
`vdd_33` with a load switch. It fails for reasons that are arithmetic, not taste:

1. **`vdd_pix` is out of spec.** The Pt's rail reaches 3.229 V; the sensor floor is 3.25 V. The old
   README assumed 3.300 V nominal and called the tolerance "unpublished." It is **3.278 V** and
   fully derivable (§2.1).
2. **`vdd_33` has ~7 mV of worst-case margin** after ferrite and connector IR drop. That is not a
   margin; it is a coin flip.
3. **The tap makes correct power-up sequencing impossible.** `vdd_33` tapped from `+3V3` rises the
   instant the Pt's rail does. But 1.8 V can only be made by LDO'ing *down from that same rail*,
   and an LDO's output cannot precede its input. So **`vdd_18` always comes up after `vdd_33`** —
   the forbidden order, on every power-up, by construction. This is the one that risks latch-up and
   a dead part, and nothing downstream can fix it.

The load switch was the right *instinct* (something must gate `vdd_33`). The error was believing a
filtered tap could ever be accurate or quiet enough.

---

## 9. Open items

1. **Confirm the Hd and Ft+ pass-throughs with a meter** before first power-up (§2.5.1). The pin
   map is the Pt's own top connector; it reaches us through two intermediate boards.

2. **Layout is now load-bearing, not cosmetic:**
   - `vdd_pix` **Kelvin route** from U5's output cap to sensor pins 31/33/38/40 (§5, rule 1).
   - `vdd_pix` **total capacitance ≤ 1.5 µF** (§5, rule 2).
   - U3/U4/U2 **away from the sensor** (heat + EMI).
   - Boost SW node small and ground-shielded.

3. **Resolve the standard-value LCSC codes** (§7) and find a **backup inductor** for L1.

4. **Sensor CMOS inputs float until FPGA `DONE`.** All eight (`mosi`, `sck`, `clk_pll`,
   `trigger0–2`, `reset_n`, `ss_n`) need pull resistors, or they burn crowbar current in the
   sensor's input buffers during configuration while `vdd_33` is already up. Sensible defaults:
   `reset_n` and `trigger0–2` **pulled down** (fail-safe: held in reset, no spurious exposure);
   `ss_n` **pulled up** (a floating select must not read as asserted). The old design reasoned this
   out correctly and the reasoning carries over.

5. **Bench-verify the sequencing on the first board** — scope `vdd_18`, `vdd_33`, `vdd_pix` on both
   power-up and power-down and confirm the order and the ≥10 µs separations before fitting a
   sensor into the socket. The sensor is the expensive part; the sequencing is the thing that can
   kill it.

---

## 10. Sources

- onsemi **NOIP1SN1300A** — rails (Table 5, p.4), abs max (Table 4, p.3), sequencing (pp.17–18),
  pin list (pp.66–67).
- Alchitry **Pt V2** schematic, *ALCHITRY PLATINUM PP-001-06 Rev B* — sheet 3 (top connectors),
  sheet 4 (bottom connectors), sheet 6 (linear regulators / VBSEL), sheet 7 (SMPS / ADP5052),
  sheet 1 (VCCO_13 ← VB34).
- Analog Devices **ADP5052** — 0.8 V FB reference, ±1.5 % output accuracy.
- TI **TPS61023** (SLVSF14B) — boost.
- TI **TPS7A20** (SBVS338H) — LDOs.
- TI **TLV803** (SBVS157E) — supervisor, Table 5-1 threshold options.
- Sunlord **SWPA** series — SWPA3012S2R2 Isat / DCR.
