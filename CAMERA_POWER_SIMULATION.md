# PYTHON 1300 Camera Board — Power Simulation Report

**All tests PASS.** One test found a **real bug that would have shipped** (a saturating inductor).

Design under test: [`CAMERA_POWER_DESIGN.md`](CAMERA_POWER_DESIGN.md).
Netlists: [`LauPythonCamera_Pt_Stack/sim/`](LauPythonCamera_Pt_Stack/sim/) — see §6 to reproduce.

---

## 1. Summary

| # | Test | Result | |
|---|---|---|---|
| 1 | Power-up sequencing | `vdd_18` → `vdd_33` → `vdd_pix` | ✅ PASS |
| 2 | Power-down sequencing | `vdd_pix` → `vdd_33` → `vdd_18` | ✅ PASS |
| 3 | Steady-state rail accuracy | all three inside the datasheet windows | ✅ PASS |
| 4 | Boost inrush → supervisor false-trip | 273 mV of margin; no startup lockup | ✅ PASS |
| 5 | **Peak inductor current** | **1.496 A — 3× my hand calc** | 🐞 **BUG FOUND** |
| 6 | PSRR (noise rejection) | 85 dB better than the old ferrite tap | ✅ PASS |
| 7 | Brownout / dip recovery | correct ordering in both directions | ✅ PASS |
| 8 | Supervisor chatter (shallow dip) | no false trip, 120 mV margin | ✅ PASS |
| 9 | Load transient | `vdd_pix` completely isolated (0.0 mVpp) | ✅ PASS |

---

## 2. Toolchain and models

**Simulator: LTspice 26.** ngspice was tried first and **cannot run TI's models** — see §5.

**Real manufacturer models** (downloaded from TI, unmodified except where noted):

| Part | Role | TI lit. no. | `ti.com/lit/zip/…` |
|---|---|---|---|
| TPS7A20 | LDO ×3 | **SBVM961** | `sbvm961` |
| TLV803S | Supervisor ×2 | **SBVM034** | `sbvm034` |
| TPS61023 | Boost | **SLVMD68** | `slvmd68` |

**The sensor has no SPICE model.** Confirmed — onsemi publishes neither SPICE nor IBIS for the
PYTHON 1300. It is modelled as its **datasheet loads** (140 mA / 80 mA / 5 mA) plus the board's
**actual decoupling** (15.04 µF / 14.03 µF / **1.54 µF**).

**Boost stand-in.** TI's switching boost model runs at ~20 s per ms of simulated time — a 285 ms
sequencing run would take ~95 minutes. So the long system runs use a behavioural boost
**calibrated against TI's real model** (Vout 4.506 V, ~450 µs startup, true input-output
disconnect, EN 1.2 V / 0.35 V). The **real** switching model is used for the short runs where its
detail matters: **inrush and peak inductor current (§4.5)**.

---

## 3. Independent model validation

Before trusting any result, each model was checked against its datasheet:

| Model | Simulated | Datasheet | |
|---|---|---|---|
| TLV803S trip point | **2.9299 V** | 2.93 V | ✅ |
| TPS7A20 output @140 mA | 3.282 V | 3.3 V ±1.5 % | ✅ |
| TPS7A20 startup | ~800 µs | 750–1150 µs | ✅ |
| TPS61023 output (330k/51k) | 4.506 V (FB = 0.603 V) | VREF 580–610 mV | ✅ |

---

## 4. Results

### 4.1 Power-up sequencing — PASS

Datasheet requires **`vdd_18` → `vdd_33` → `vdd_pix`**, each ≥10 µs apart.

| Rail | Up at | Gap |
|---|---|---|
| `vdd_18` | 0.99 ms | — |
| `vdd_33` | 211.65 ms | +210.7 ms |
| `vdd_pix` | 212.26 ms | **+601 µs** |

The 210 ms is the TLV803's reset delay — safe, and it guarantees `vdd_18` is long established
before the boost is ever permitted to start.

### 4.2 Power-down sequencing — PASS

Datasheet requires **`vdd_pix` → `vdd_33` → `vdd_18`**.

| Rail | Down at | Gap |
|---|---|---|
| `vdd_pix` | 261.29 ms — **first** | — |
| `vdd_33` | 262.07 ms | **+777 µs** |
| `vdd_18` | 265.77 ms — **last** | +3.70 ms |

Every gap is **60–380× the required 10 µs**.

### 4.3 Steady-state rails — PASS

| Rail | Simulated | Window | |
|---|---|---|---|
| `vdd_18` | 1.7945 V | 1.70 – 1.90 V | ✅ |
| `vdd_33` | 3.2901 V | 3.20 – 3.40 V | ✅ |
| `vdd_pix` | **3.2998 V** | **3.25 – 3.35 V** | ✅ **dead centre** |

`vdd_pix` landing mid-window is the entire point of the redesign — the old tap sat 21 mV *below*
the floor at the rail's low corner.

### 4.4 Inrush → supervisor false-trip — PASS

**Risk:** the boost's startup current droops `+3V3` past the supervisors' 2.93 V trip → U7 kills
the boost → rail recovers → boost restarts → **power-on oscillation that never converges.**

With a deliberately pessimistic **50 mΩ** source impedance (DF40 pins + traces + Pt buck):

| | |
|---|---|
| Peak current drawn from `+3V3` | 1.49 A |
| `+3V3` droops to | **3.203 V** |
| Supervisor trip | 2.93 V |
| **Margin** | **273 mV** ✅ |

**No startup lockup.** Risk checked and cleared.

### 4.5 🐞 Peak inductor current — BUG FOUND

The same run measured a **1.496 A peak inductor current at startup** — the boost charging its
output caps against its own 3.7 A internal current limit. That is **~3× the 0.54 A steady-state
peak I had sized the inductor against.**

**Not a numerical spike.** The current sits above **1.2 A for 15 µs** and above **1.4 A for 6 µs**,
on **every single power-up**:

| Inductor | Isat | vs 1.496 A | |
|---|---|---|---|
| Sunlord SWPA3012 | 1.2 A | **0.80×** | ❌ saturates every startup |
| CENKER CKCS3015 | 1.6 A | **1.07×** | ❌ no margin |
| **SXN SMNR4020** | **3.4 A** | **2.27×** | ✅ **now fitted** |

**Reducing the boost output cap does not fix it** (22 µF → 12 µF moved the peak only
1.496 → 1.454 A). The inrush is set by the converter's soft-start, not by the caps — **the
inductor has to be sized for it.** L1 moved from a 3×3 mm to a 4×4 mm part.

> **This is the single most valuable thing the simulation did.** Hand analysis sized the inductor
> on steady-state current and was wrong by 3×. It would have shipped.

### 4.6 PSRR — PASS (and the old design is worse than we thought)

AC sweep, `+3V3` → sensor rail. Negative dB = attenuation. **Positive dB = amplification.**

| Frequency | OLD: ferrite tap → `vdd_33` | NEW: boost + LDO |
|---|---|---|
| 10 kHz | **+0.5 dB** | −72.0 dB |
| **39.8 kHz** | **+13.5 dB** ← worst case | ~−72 dB |
| 100 kHz | −13.9 dB | −57.6 dB |
| 1 MHz | −55.4 dB | −64.5 dB |

> ### ⚠️ The old ferrite filter AMPLIFIES noise 4.7× at 40 kHz — and its own design rule is why.
>
> The old README demanded *"`FB1` must be LOW-DCR (≤50 mΩ). This is a hard spec, not a
> preference."* That was **correct on DC** — at 140 mA a 0.3 Ω bead drops 42 mV and pushes
> `vdd_33` under its 3.2 V floor.
>
> **But low DCR is exactly what removes the damping from the LC filter.** With 50 mΩ and 15 µF,
> Q ≈ 4.7 → an undamped resonance at 40 kHz with **+13.5 dB of gain**. The DC requirement and the
> AC behaviour are in direct conflict. **The spec that saves the DC budget is the spec that wrecks
> the noise budget**, and the old design never noticed.

**What it costs.** 20 mVpp on `+3V3` at 40 kHz (`vdd_33`'s window is ±100 mV):

| | ripple reaching `vdd_33` | |
|---|---|---|
| **OLD (ferrite)** | **94 mVpp** | **47 % of the entire spec window** |
| **NEW (LDO)** | **5 µVpp** | negligible |

**85 dB improvement — ~18,800× less noise on the sensor's analog rail.**

**Every assumption was stacked against the new design and it still wins by 85 dB:** used TI's
model's *pessimistic* PSRR (57.6 dB @100 kHz vs the datasheet's 75 dB); gave the boost **zero**
credit (assumed it passes input ripple 1:1, when its loop actually rejects low-frequency ripple);
modelled the bead optimistically.

### 4.7 Brownout / dip recovery — PASS

**Deep dip** (`+3V3` → 2.50 V for 5.5 ms, below the trip):

| | | |
|---|---|---|
| `vdd_pix` collapses | 300.24 ms | **first** ✅ |
| `vdd_33` collapses | 301.03 ms | **791 µs** gap ✅ |
| `vdd_18` minimum | **1.7944 V — never dropped** | ✅ correct: it must be last |
| Recovery `vdd_33` → `vdd_pix` | 515.75 → 516.35 ms | **602 µs** gap ✅ |

Correct ordering **in both directions through the brownout**. All rails return to spec. No chatter,
no lockup. The 200 ms reset delay correctly re-arms after the rail recovers.

### 4.8 Supervisor chatter — PASS

**Shallow dip** (`+3V3` → 3.05 V, *above* the 2.93 V trip):

| | |
|---|---|
| `vdd_pix` | **3.2998 V — completely unmoved** ✅ |
| `vdd_18` | 1.7945 V — unmoved ✅ |

**No false trip with 120 mV of margin.** The supervisor does not chatter on a sag that doesn't
warrant a shutdown.

### 4.9 Load transient — PASS, and `vdd_pix` is fully isolated

`vdd_33` stepped 100→140 mA, `vdd_18` 50→80 mA, 1 µs edges.

| Rail | Excursion | Window | Margin to edge |
|---|---|---|---|
| `vdd_33` | 5.8 mVpp | 3.20 – 3.40 V | 89 mV |
| `vdd_18` | 4.5 mVpp | 1.70 – 1.90 V | 93 mV |
| `vdd_pix` | **0.0 mVpp** | 3.25 – 3.35 V | 50 mV |

> **`vdd_pix` does not move at all.** A 40 mA step on `vdd_33` has *zero* effect on the pixel
> supply, because it sits behind its own LDO. In the old design both rails hung off **one** switched
> node through ferrites, so **every `vdd_33` load step would have modulated `vdd_pix` directly** —
> exactly the rail that cannot tolerate it. This isolation is a free consequence of giving
> `vdd_pix` its own regulator, and it is a benefit the design never claimed for itself.

---

## 5. ⚠️ Defects found in TI's own models

**These will bite anyone who re-runs this. They are not our circuit's problems.**

1. **ngspice cannot run these models at all.**
   - **TPS61023 will not converge**: parameter-less diodes (`.model D_D1 d`, no RS, no Cj),
     `VSWITCH` elements with 100 GΩ off-resistance, and an **ideal-diode bridge driven by a 10 A
     current source**. TI's own profile relies on PSpice's `ADVCONV`, which ngspice lacks.
   - **TPS7A20 fails on any VIN ramp slower than ~100 µs** — and our boost soft-starts over 700 µs.
   - **LTspice runs all of them unmodified.**

2. **`method=gear` silently breaks TI's LDO model.** It produces 0 V output with no error. Use the
   default trapezoidal integration.

3. **TI's TPS7A20 model OMITS the 150 Ω auto-discharge** that the datasheet specifies when EN is
   low. **Our power-down ordering depends on it.** It must be added externally (a switched 150 Ω —
   see `S_d2`/`S_d4`/`S_d5` in the netlists) or the simulation will falsely report a slow
   `vdd_pix` collapse and you will "discover" a sequencing failure that does not exist.

4. **TI ships `V_out` as a GLOBAL parameter.** Make it a per-instance subckt parameter, or all
   three LDOs will share one output voltage.

5. **The TPS7A20's PSRR is only crudely modelled** — flat ~75 dB with a pole at 10 kHz and a zero
   at 1 MHz (`psrr=178u, pole=10k, zero=1Meg`). It does **not** reproduce the datasheet curve
   (pessimistic at 100 kHz, optimistic at 1 MHz). **Do not quote model PSRR as spec.**

---

## 6. ⚠️ What SPICE did NOT and CANNOT confirm

**Passing these tests does not mean the board is tested. It means the power architecture is.**

- **The ±1.5 % tolerance windows.** TI's models are **typical-value only**, with no tolerance data
  — no Monte Carlo is possible. `vdd_pix`'s 3.2998 V is a **typical** result. Its accuracy rests
  on the **datasheet guarantee**, not on this simulation. *This is the very thing that started the
  redesign, and it is not simulatable.*
- **What the sensor does if sequencing is violated.** No model exists, and no public data on the
  internal ESD/level-shift structures. This is *why* the ordering is enforced structurally rather
  than by trusting matched time constants.
- **The Pt's actual rail voltage.** 3.278 V is computed from Alchitry's feedback divider. **Only a
  meter confirms it.**
- **Thermal.** Two LDOs run +30 °C and +22 °C above ambient beside a sensor whose dark current
  doubles every ~7 °C. Completely unanalysed.
- **Anything about the PCB.** No layout exists — no DRC, no 100 Ω differential impedance, and not
  the `vdd_pix` Kelvin route that §5 of the design doc calls mandatory.
- **Nothing physical has been measured.**

---

## 7. Reproducing this

```bash
winget install AnalogDevices.LTspice
```

Download the three TI models (they are **not committed here** — vendor licensed):

```
https://www.ti.com/lit/zip/sbvm961     # TPS7A20  -> tps7a20.lib
https://www.ti.com/lit/zip/sbvm034     # TLV803S  -> tlv803s.lib
https://www.ti.com/lit/zip/slvmd68     # TPS61023 -> tps61023.lib
```

**One edit is required** — make the LDO's output voltage per-instance:

```
-  .SUBCKT TPS7A20_ADJ_TRANS VIN GND EN N_C VOUT
+  .SUBCKT TPS7A20_ADJ_TRANS VIN GND EN N_C VOUT PARAMS: V_out=3.3
```

Then, from `LauPythonCamera_Pt_Stack/sim/` with the three `.lib` files alongside:

```bash
LTspice.exe -b -ascii powertree.cir   # sequencing + steady-state  (~2 s)
LTspice.exe -b -ascii brownout.cir    # deep dip + recovery        (~3 s)
LTspice.exe -b -ascii shallow.cir     # supervisor chatter         (~3 s)
LTspice.exe -b -ascii loadtran.cir    # load transient             (~1 min)
LTspice.exe -b -ascii inrush.cir      # REAL boost model           (~2 min)
LTspice.exe -b -ascii psrr_ldo.cir    # LDO PSRR (AC)              (~1 s)
LTspice.exe -b -ascii psrr_old.cir    # the OLD ferrite tap (AC)   (~1 s)
```

Results land in the matching `.log` files (`.meas` output). Add `.param SS=0` when using the real
TPS61023 model — TI references an undefined soft-start parameter.
