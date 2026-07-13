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

> **Verify with a meter before first power-up.** This mapping is the Pt's *own* top connector. It
> reaches us through the **Hd and Ft+ pass-throughs**, which are believed to be straight
> pass-throughs but have not been independently confirmed. Thirty seconds with a multimeter
> between the Ft+'s top connector and a known `+3V3` point is cheap insurance against a dead
> sensor.

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
    +--> [U1] BOOST 4.46 V  ---+--> [U2] LDO 3.30 V --> vdd_33   140 mA
    |    TPS61023              |    TPS7A2033
    |                          +--> [U3] LDO 3.30 V --> vdd_pix    5 mA
    |                               TPS7A2033
    |
    +--> [U4] LDO 1.80 V ---------------------------> vdd_18    80 mA
    |    TPS7A2018
    |
    +--> [U5] SUPERVISOR 2.93 V  (TLV803S) --> sequencing (§6)
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

## 4. Boost stage — U1, TPS61023

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

**R1 = 649 kΩ 1 %** (VOUT→FB), **R2 = 100 kΩ 1 %** (FB→GND):

```
V_OUT = 0.595 × (1 + 649/100) = 4.457 V        range over V_REF tol: 4.34 … 4.57 V
```

**Why 4.46 V and not lower?** The TPS7A20's PSRR is specified at V_IN = V_OUT + 1.0 V. Even at the
*low* corner, 4.34 − 3.3 = **1.04 V**, so we keep full specified PSRR under all conditions.
Dropping to 4.0 V would save ~60 mW of LDO heat but forfeit that.

R2 = 100 kΩ satisfies TI's R2 < 300 kΩ rule (divider current ≥100× the 20 nA FB leakage).

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

## 5. LDO stage — U2, U3, U4

All three are **TPS7A20** family, SOT-23-5 (DBV). Pinout: **1 = IN, 2 = GND, 3 = EN, 4 = NC,
5 = OUT.** Datasheet SBVS338H.

| | Noise | PSRR | EN thresholds | Auto-discharge | RθJA (DBV) |
|---|---|---|---|---|---|
| TPS7A20 | 7 µV_RMS | 95 dB @1 kHz, 75 dB @100 kHz, 45 dB @1 MHz | V_IH **0.9 V max**, V_IL 0.3 V | **150 Ω** pulldown when EN low | 187.1 °C/W |

Each LDO needs **C_IN = 1 µF, C_OUT = 1 µF** minimum (stable with 1 µF ceramic; no noise-bypass
cap required).

### U2 — `vdd_33` (TPS7A2033, 3.3 V, 140 mA)

| Check | Value | Window | Verdict |
|---|---|---|---|
| Accuracy | 3.3 V ±1.5 % → **3.2505 – 3.3495 V** | 3.20 – 3.40 V | ✓ ~50 mV margin each side |
| Headroom | V_IN 4.34 V min − 3.3 V = **1.04 V** | dropout ≤140 mV @300 mA | ✓ |
| Dissipation | (4.457 − 3.3) × 0.140 = **162 mW** | ×187.1 °C/W | **+30 °C rise** |

### U3 — `vdd_pix` (TPS7A2033, 3.3 V, 5 mA) — *same part number as U2*

| Check | Value | Window | Verdict |
|---|---|---|---|
| Accuracy | 3.3 V ±1.5 % → **3.2505 – 3.3495 V** | **3.250 – 3.350 V** | ⚠ **0.5 mV margin** |
| Dissipation | (4.457 − 3.3) × 0.005 = **6 mW** | — | negligible |

> ### ⚠️ Two hard rules for `vdd_pix`
>
> **1. Kelvin routing is mandatory.** The ±1.5 % LDO consumes the *entire* ±1.5 % window with
> 0.5 mV to spare. At 5 mA, even 100 mΩ of copper spends the whole low-side budget. Route short
> and wide from U3's output capacitor **straight to sensor pins 31/33/38/40**. Never daisy-chain
> off `vdd_33`. There is no tighter part available — this is as good as it gets.
>
> **2. Keep total `vdd_pix` capacitance ≤ ~1.5 µF.** Power-down depends on U3's 150 Ω internal
> pulldown collapsing this rail *first* (§6). Budget: 1 µF (LDO C_OUT) + 4 × 100 nF (per sensor
> pin) = 1.4 µF → τ = 150 Ω × 1.4 µF = **210 µs**. **Do NOT add bulk capacitance to `vdd_pix`.**
> A 10 µF bulk cap here would break the shutdown ordering.

U2 and U3 are the same MPN — one reel, one JLCPCB line item — but they **must be separate
devices**, because `vdd_pix` has to rise after `vdd_33` and collapse before it.

### U4 — `vdd_18` (TPS7A2018, 1.8 V, 80 mA) — fed from `+3V3`, not the boost

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

## 6. Sequencing — U5, TLV803S

**U5 = TLV803S**, 3-pin SOT-23, **active-low open-drain** reset, threshold **V_IT = 2.93 V**
(2.99 V max), 200 ms power-up delay, built-in fast-transient rejection. Needs a 0.1 µF bypass.

**Why 2.93 V is the right threshold:**
- **Never false-trips:** `+3V3` minimum is 3.229 V → **239 mV** above the worst-case 2.99 V trip.
- **Always fires in time:** far above the boost's 1.8 V UVLO and U4's ~1.95 V dropout.

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
| **U4.EN** (`vdd_18`) | tied directly to `+3V3` — comes up with the rail |
| **U1.EN** (boost) | **U7** RESET → **1 kΩ series** → EN node; **220 nF** to GND; U7's RESET has its own **100 kΩ pull-up to `+3V3`** |
| **U2.EN** (`vdd_33`) | tied directly to `vdd_18` |
| **U3.EN** (`vdd_pix`) | **U6** RESET directly; **100 kΩ pull-up to `vdd_33`**; **10 nF** to GND |

**The interlock is U3.EN's pull-up going to `vdd_33`, not to `+3V3`.** Before `vdd_33` exists that
pull-up sits at 0 V, so `vdd_pix` *physically cannot* enable early — the ordering is structural,
not a race between time constants.

**The 200 ms supervisor reset delay does real work:** it guarantees `vdd_18` (up in ~1 ms) is
established long before the boost is ever permitted to start.

### Power-up — `vdd_18` → `vdd_33` → `vdd_pix` ✓

1. `+3V3` rises. **U4.EN** is tied to it → **`vdd_18` up at t ≈ 1 ms.**
2. At **t ≈ 200 ms** both supervisors release (their reset delay).
3. **U1.EN** (boost) charges through 100 kΩ + 1 kΩ into 220 nF (τ = 22 ms), crossing the boost's
   1.2 V V_IH at **t ≈ 210 ms**. Boost runs → 4.46 V in ~700 µs.
4. **U2.EN** is already high (`vdd_18`, since 1 ms). U2 begins regulating once the boost's output
   passes its 1.35 V UVLO → **`vdd_33` up at t ≈ 212 ms.**
5. **U3.EN** was held at 0 V — *its pull-up goes to `vdd_33`, which did not exist until now.* It
   now charges through 100 kΩ ∥ 500 kΩ (internal) × 10 nF (τ = 833 µs), crossing 0.9 V V_IH
   **330 µs after `vdd_33` appears** → **`vdd_pix` up at t ≈ 213.5 ms.**

Separations are **ms**, against a **10 µs** requirement.

### Power-down — `vdd_pix` → `vdd_33` → `vdd_18` ✓

1. `+3V3` falls below 2.93 V → **both** supervisors assert `RESET` **low immediately** (the 200 ms
   delay applies only to *release*, not assert).
2. **U3.EN → low** (U6, direct). TPS7A20's **150 Ω auto-discharge** pulls `vdd_pix` down:
   τ = 150 Ω × 1.44 µF = **216 µs**. → **`vdd_pix` collapses FIRST.** ✓
3. **U1.EN decays** through U7's 1 kΩ into the 220 nF cap: τ = **220 µs**, crossing the boost's
   0.35 V V_IL at **t ≈ 493 µs**. Boost shuts off (**true disconnect**) → the 4.46 V node collapses
   under the 140 mA load → **`vdd_33` falls SECOND.** ✓
4. **U4 keeps regulating** `vdd_18` from `+3V3` until `+3V3` < 1.8 V + dropout ≈ **1.95 V**.
   → **`vdd_18` dies LAST.** ✓

Skew between `vdd_pix` and `vdd_33` ≈ **493 µs**, against the required 10 µs. ✓

> **This is the problem the boost created and the supervisor solves.** Once started, the TPS61023
> runs with V_IN as low as **0.5 V** — left alone it would hold 4.46 V while `+3V3` collapsed,
> making `vdd_33` outlive `vdd_18`: the exact inverse of the required order. The supervisor
> yanking U1.EN low at 2.93 V is what prevents that.

---

## 7. Bill of materials

### Verified on LCSC / JLCPCB

| Ref | Function | MPN | **LCSC** | Package | Stock | Price | JLC |
|---|---|---|---|---|---|---|---|
| U1 | Boost 3.3 → 4.46 V | **TPS61023DRLR** | **C919459** | SOT-563 | 18,195 | $0.267 | Extended |
| U2, U3 | LDO 3.3 V (`vdd_33`, `vdd_pix`) | **TPS7A2033PDBVR** | **C2862740** | SOT-23-5 | 59,955 | $0.110 | Extended |
| U4 | LDO 1.8 V (`vdd_18`) | **TPS7A2018PDBVR** | **C963430** | SOT-23-5 | 27,085 | $0.133 | Extended |
| U6, U7 | Supervisor, 2.93 V, open-drain (**two required — §6**) | **TLV803SDBZT** | **C702125** | SOT-23-3 | in stock | $0.089 | Extended |
| L1 | 2.2 µH ±20 %, Isat ≥1.2 A, DCR ≤98 mΩ | **SWPA3012S2R2MT** | **C36402** | 3×3×1.2 mm | 1,330 ⚠ | $0.042 | Extended |
| C1–C3 | 10 µF 25 V X5R (boost C_IN + 2× C_OUT) | **CL21A106KAYNNNE** | **C15850** | 0805 | in stock | $0.021 | **Basic** |
| R2, R5 | 100 kΩ 1 % | 0402WGF1003TCE | **C25741** | 0402 | in stock | ~$0.01 | **Basic** |

⚠ **L1 stock is thin (1,330).** Find a backup before committing to a build — any 2.2 µH ±20 %,
Isat ≥ 1.0 A part in a ≤3×3 mm footprint will do.

### Standard values — LCSC codes to resolve at order time

These are all commodity JLCPCB **Basic** parts. I have deliberately **not** guessed their C-codes.

| Ref | Value | Qty | Purpose |
|---|---|---|---|
| R1 | 649 kΩ 1 % 0402 | 1 | Boost FB divider, top. MPN **UNI-ROYAL 0402WGF6493TCE** |
| R3 | 100 kΩ 1 % 0402 | 1 | U7 RESET pull-up to `+3V3` — **reuse C25741** |
| R4 | 1 kΩ 1 % 0402 | 1 | U7 RESET → U1.EN series (sets the 493 µs shutdown skew) |
| R5 | 100 kΩ 1 % 0402 | 1 | U3.EN pull-up to **`vdd_33`** (the interlock) — **reuse C25741** |
| R6 | 47 kΩ 1 % 0402 | 1 | `ibias_master` → `gnd_33`. **Mandatory** |
| R7 | 1 kΩ 1 % 0402 | 1 | `VBSEL_A` → `+3V3` (straps bank 13 to 2.5 V) |
| C4 | 220 nF 0402 | 1 | U1.EN shutdown delay |
| C5 | 10 nF 0402 | 1 | U3.EN turn-on delay |
| — | 1 µF X7R 0603 | 6 | LDO C_IN / C_OUT (2 each × 3 LDOs) |
| — | 10 µF | 2 | Bulk on `vdd_33` and `vdd_18` — **reuse C15850**. **NOT on `vdd_pix`** |
| — | 100 nF 0402 | 13 | 11 × sensor supply pins + 2 × supervisor bypass |
| — | 10 nF 0402 | 3 | `vdd_18` HF decoupling (360 MHz LVDS drivers) |
| — | pull resistors | 8 | Sensor CMOS inputs — see §9.4 |

> **Lock the LDOs against JLCPCB "equivalent part" substitution.** The common jellybeans
> (RT9080, ME6211, XC6206, AP7343, TCR2EF33) are **±2 % or worse and fail the `vdd_pix` window on
> their own**. They are not valid substitutes at any price.

### Thermal budget

Dark current roughly doubles every 7 °C — keep heat away from the sensor.

| Source | Dissipation |
|---|---|
| U2 (`vdd_33` LDO) | **162 mW** (+30 °C junction rise) |
| U4 (`vdd_18` LDO) | **118 mW** (+22 °C) |
| U1 (boost, ~10 % loss) | ~72 mW |
| U3 (`vdd_pix` LDO) | ~6 mW |
| **Board total** | **~358 mW** (plus the sensor's own 620 mW) |

**Place U1, U2 and U4 away from the sensor.** U1 additionally because a 1 MHz switching node next
to an image sensor is an EMI problem — keep its SW node small and ground-shielded.

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
   - `vdd_pix` **Kelvin route** from U3's output cap to sensor pins 31/33/38/40 (§5, rule 1).
   - `vdd_pix` **total capacitance ≤ 1.5 µF** (§5, rule 2).
   - U1/U2/U4 **away from the sensor** (heat + EMI).
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
