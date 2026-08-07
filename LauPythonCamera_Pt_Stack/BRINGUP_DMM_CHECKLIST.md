# Bring-up — pin-by-pin DMM checklist

**Conditions this checklist assumes.** Socket **empty** (no PYTHON 1300 inserted), board mated to
the Pt V2 via J1/J2/J3, Pt powered, **FPGA unconfigured** (no bitstream). Every expected value
below was derived from `LauPythonCamera_Pt_Stack.kicad_pcb` — the pad-to-net map, not the docs.

> **Do not insert the sensor until §1, §2 and §4 all pass.** The socket exists precisely so a
> wiring fault costs a board and not a 27-week-lead sensor.

---

## Bench results — 2026-08-07

**§1–§3 PASS. §4 answered. The power tree is fully confirmed, including both hand-soldered supervisors.**

> ### ⚠️ The meter used for these readings has a +3.3 % gain error
>
> Established against three independent regulators: TP1 (the Pt's ADP5052) read +3.42 %, TP3 (U2)
> +3.50 %, TP5 (U5) +3.33 %. TP3 at 1.863 V settled it — a pure offset error would have put it at
> 1.911 V. **Divide readings by 1.033.** Zero is unaffected. Swap the meter battery before any
> absolute measurement matters.

| Probe | Read | ÷1.033 | Window | |
|---|---|---|---|---|
| TP1 `+3V3_SYS` | 3.390 | **3.282 V** | 3.229 – 3.327 | ✓ |
| TP2 `+4V5` | 4.690 | **4.540 V** | 4.33 – 4.56 | ✓ |
| TP3 `+1V8_CAM` | 1.863 | **1.804 V** | 1.760 – 1.840 | ✓ |
| TP4 `+3V3_CAM` | 3.401 | **3.293 V** | 3.250 – 3.350 | ✓ |
| TP5 `+3V3_PIX` | 3.410 | **3.301 V** | 3.250 – 3.350 | ✓ |

`vdd_pix` landed at 3.301 V — essentially dead centre of a window the design had **0.5 mV** of
margin in. TP4 and TP5 are the same MPN off the same reel and read 9 mV apart, which also rules out
a bad U5 independently.

**§3 needed no measurements.** `+3V3_PIX` existing at all requires `EN_PIX` high → U6 released →
`+3V3_CAM` present → U2's 1.8 V → `+4V5` → boost running → `EN_BOOST` high → U7 released. Both
supervisors are alive and correctly oriented.

**§4: configuration pull-ups are OFF.** R5 pad 1 read 0.001 V against a hard 0.000 V on its ground
pad. The Pt leaves its I/Os in high-Z, so **17 pins cannot be resolved by voltage at all** — see the
revised split in §5. Most of the remaining proof now lives in §6.

---

> **Getting to "unconfigured".** Erase the Pt's flash first:
> `alchitry.exe load --erase --board PtV2` (2.0.52 loader), **then power-cycle the board** — the
> FPGA's SRAM configuration survives a flash erase and only clears on power-down or PROGRAM_B.
> Confirm the Pt's DONE LED stays off. A leftover bitstream does not have to *conflict* with the
> camera pins to ruin these readings: Vivado's default `UNUSEDPIN` policy is `PULLDOWN`, so any
> unrelated design still actively pulls all 25 camera pins down and hides exactly the opens §5
> is looking for.

**Probing the socket.** The Andon 680-48-SM is an **open-frame** socket — no lid, no clamshell, no
clip. All 48 gold castellation contacts are exposed from above at all times, and the sensor is
retained by contact friction alone. Use a fine/needle probe and no force: a bent contact scraps the
socket, it is hand-solder DNP, and no distributor stocks a replacement (§ ordering).

**Physical orientation of U1** (board as drawn, top side up, J3 control header at the bottom edge):

```
                    N (top edge)  — pins 31…42, left→right
        +-------------------------------------------+
        |  48                                   43  |
   W    |   1  ←pin 1, just BELOW centre of the     |    E
 (left) |      left edge                            |  (right)
  pins  |                                           |   pins
 1…6,   |                  U1                       |  19…30,
 43…48  |                                           |  top→bottom
        |   6                                   19  |
        +-------------------------------------------+
                    S (bottom edge) — pins 7…18, left→right
```

Pin 1 sits one position **below** the left-edge centreline; pin 48 one position above it. The two
unnumbered mechanical pads are on the SW and NE diagonals — use them to confirm rotation.

---

## 1. FIRST, BEFORE ANYTHING ELSE — the VBSEL straps

Bank 13's VCCO is set by **hardware**, by this board strapping the Pt's tri-voltage select pins.
Alchitry: *"failing to set the tri-voltage pins correctly could damage the FPGA."* The board is
already powered, so verify this before spending time anywhere else.

| Probe point | Location (KiCad page coords, top side) | Net | Expected |
|---|---|---|---|
| **R10 pad 2** | 119.51, 101.0 | `VBSEL_A` | **3.23 – 3.33 V** |
| **R11 pad 2** | 122.51, 101.0 | `VBSEL_B` | **3.23 – 3.33 V** |

Both R10 and R11 are 1 kΩ 0402 at the bottom edge of the board, next to TP1. Both HIGH → bank 13
VCCO = 2.5 V, which is what `LVDS_25` and `DIFF_TERM` require.

❌ **Either one at 0 V or floating → power the stack down now.** Bank 13 is at the wrong VCCO.

---

## 2. Rails — the six test points (powered)

All six test points are on the **top** layer. Black lead on TP6 for every reading.

| TP | Location | Net | Expected | Notes |
|---|---|---|---|---|
| **TP6** | 119.64, 82.79 | `GND` | — | reference for everything |
| **TP1** | 111.6, 101.0 | `+3V3_SYS` | **3.23 – 3.33 V** (typ 3.278) | the Pt's rail. *Not* 3.300 — its divider makes 3.278 |
| **TP2** | 121.4, 67.5 | `+4V5` | **4.33 – 4.56 V** (typ 4.445) | TPS61023 boost, set by R8 330 k / R9 51 k |
| **TP3** | 117.3, 86.48 | `+1V8_CAM` | **1.76 – 1.84 V** | TPS7A2018, enabled straight off +3V3_SYS |
| **TP4** | 115.36, 88.81 | `+3V3_CAM` | **3.23 – 3.37 V** | TPS7A2033 (U4), EN tied to `+1V8_CAM` |
| **TP5** | 128.14, 65.71 | `+3V3_PIX` | **3.25 – 3.35 V** | TPS7A2033 (U5), EN from supervisor U6 |

### Reading the failures

| Symptom | Meaning |
|---|---|
| TP2 ≈ TP1 (~3.3 V, not 4.4 V) | Boost not switching — `EN_BOOST` low, or L1/U3 fault. Go to §3 |
| TP2 = 0 V | Boost dead or `+4V5` shorted |
| TP3 = 0 V | U2 (TPS7A2018) dead — everything downstream stays dark, because U4's EN comes from this rail |
| TP4 = 0 V but TP3 = 1.8 V | U4 fault |
| **TP5 = 0 V, everything else good** | `EN_PIX` low → **U6**. This is your hand-soldered part. Go to §3 |

---

## 3. The two supervisors you soldered — U6 and U7 (powered)

TLV803S, SOT-23-3. Pinout: **pin 1 = GND, pin 2 = RESET (open-drain, active low), pin 3 = VDD.**
A 180°-rotated part puts VDD on GND — if either package is warm to the touch, kill power now.

> **Ordering, if you need more:** `TLV803SDBZR` = **`C132016`**. Order the **`R`** (reel) code, not
> the `T` — same die, same SOT-23-3, same 2.93 V threshold, but only the `R` is actually stocked.
> The BOM originally named the `T` code and **the first order shipped without U6/U7 entirely**,
> which is why these two are hand-soldered. Only an order-time check catches it.

| Probe point | Location | Net | Expected |
|---|---|---|---|
| U7 pin 3 | 105.938, 96.5 | `+3V3_SYS` | 3.23 – 3.33 V |
| U7 pin 1 | 104.062, 95.55 | `GND` | 0.000 V |
| **U7 pin 2** (= R16 pad 2) | 104.062, 97.45 | `RST_BOOST` | **≈ 3.28 V** — reset *released* |
| **R17 pad 2** | 113.5, 97.01 | `EN_BOOST` | **≈ 3.28 V** — boost enabled |
| U6 pin 3 | 105.938, 92.0 | `+3V3_SYS` | 3.23 – 3.33 V |
| U6 pin 1 | 104.062, 91.05 | `GND` | 0.000 V |
| **U6 pin 2** (= R15 pad 2) | 104.062, 92.95 | `EN_PIX` | **≈ 3.3 V** (pulled to `+3V3_CAM`) — vdd_pix enabled |

Both supervisors trip at **V_IT = 2.93 V** with a **200 ms** power-up delay. With +3V3_SYS at
3.278 V both must be released. A RESET pin stuck LOW while its VDD pin reads 3.28 V is a dead or
mis-oriented supervisor, not a sequencing event.

Note `EN_PIX`'s 100 kΩ pull-up (R15) goes to **`+3V3_CAM`, not `+3V3_SYS`** — that is the deliberate
interlock. Before vdd_33 exists, EN_PIX physically cannot rise. If TP4 = 0 V then EN_PIX = 0 V is
*correct behaviour*, not a U6 fault. Fix TP4 first.

---

## 4. Establish what the FPGA is doing to its pins — ONE measurement

With no bitstream, the Artix-7's I/Os are either Hi-Z or holding weak configuration pull-ups,
depending on how the Pt straps PUDC_B. Which one it is decides how much of §5 is conclusive, so
measure it once, up front.

**Probe R5 pad 1** (130.89, 69.7) = `CAM_TRIG0`, which has a 10 kΩ pull-down to GND on this board.

| Reading | Conclusion | Consequence for §5 |
|---|---|---|
| **0.000 V** | Config pull-ups **OFF** (PUDC_B high) | The 15 pins with no on-board passive (3, 44, 45, and the 12 LVDS) **float** — §5 cannot resolve them. Use §6 |
| **≈ 1.0 – 2.0 V** | Config pull-ups **ON** | Every pin is measurable, and the LVDS pins double as a live readout of bank 13's VCCO |

> ### ✅ ANSWERED 2026-08-07 — **0.001 V. Pull-ups are OFF.**
>
> Ground pad read a hard 0.000 V; `CAM_TRIG0` read 0.001 V with a millivolt of last-digit jitter.
> The 48 pins now split **four** ways, not two:
>
> | | Count | Verdict from the powered walk |
> |---|---|---|
> | Rails + pin 47 | **12** | fully verified — real voltages |
> | Grounds + pin 28 | **12** | fully verified — hard 0.000 V |
> | Control pins 2, 4, 25, 41, 42, 43, 46 | **7** | read 0.000 V — **but proves little** |
> | LVDS 7–18, 23, 24 + pins 3, 44, 45 | **17** | float — **no verdict** |
>
> **Eight pins read correctly even when broken.** Those seven control pins and pin 47 sit on
> on-board pull resistors. Their reading proves the resistor and the copper to the socket, *not*
> the link onward through J1 to the FPGA. Only the walking-ones bitstream (§7) proves that, because
> only it catches pin **swaps**.

---

## 5. The 48-pin walk (powered, socket empty)

Black lead on TP6 throughout. **"pull-ups ON" / "OFF"** columns refer to the §4 result.

| Pin | Side | Net | Expected — pull-ups OFF | Expected — pull-ups ON |
|---|---|---|---|---|
| 1 | W | `+3V3_CAM` | = TP4, within a few mV | same |
| 2 | W | `CAM_MOSI` | 0.000 V (R12 10k↓) | 1.0–2.0 V, **same as pins 4, 25, 41, 42, 43, 46** |
| 3 | W | `CAM_MISO` | floats — inconclusive | ≈ 3.3 V |
| 4 | W | `CAM_SCK` | 0.000 V (R13 10k↓) | matches the group |
| 5 | W | `GND` | 0.000 V | 0.000 V |
| 6 | W | `+1V8_CAM` | = TP3 | same |
| 7 | S | `CAM_CLKOUT_N` | floats | **≈ 2.5 V** ← bank 13 VCCO |
| 8 | S | `CAM_CLKOUT_P` | floats | ≈ 2.5 V |
| 9 | S | `CAM_D0_N` | floats | ≈ 2.5 V |
| 10 | S | `CAM_D0_P` | floats | ≈ 2.5 V |
| 11 | S | `CAM_D1_N` | floats | ≈ 2.5 V |
| 12 | S | `CAM_D1_P` | floats | ≈ 2.5 V |
| 13 | S | `CAM_D2_N` | floats | ≈ 2.5 V |
| 14 | S | `CAM_D2_P` | floats | ≈ 2.5 V |
| 15 | S | `CAM_D3_N` | floats | ≈ 2.5 V |
| 16 | S | `CAM_D3_P` | floats | ≈ 2.5 V |
| 17 | S | `CAM_SYNC_N` | floats | ≈ 2.5 V |
| 18 | S | `CAM_SYNC_P` | floats | ≈ 2.5 V |
| 19 | E | `+3V3_CAM` | = TP4 | same |
| 20 | E | `GND` | 0.000 V | 0.000 V |
| 21 | E | `GND` | 0.000 V | 0.000 V |
| 22 | E | `+1V8_CAM` | = TP3 | same |
| 23 | E | `CAM_LVDSCLK_N` | floats — but see §6, R2 | ≈ 2.5 V |
| 24 | E | `CAM_LVDSCLK_P` | floats — but see §6, R2 | ≈ 2.5 V |
| 25 | E | `CAM_CLK_PLL` | 0.000 V (R14 10k↓) | matches the group |
| 26 | E | `+1V8_CAM` | = TP3 | same |
| 27 | E | `GND` | 0.000 V | 0.000 V |
| 28 | E | `IBIAS_MASTER` | **0.000 V** (R1 47 k to GND, no source with the socket empty) | 0.000 V |
| 29 | E | `+3V3_CAM` | = TP4 | same |
| 30 | E | `GND` | 0.000 V | 0.000 V |
| 31 | N | `+3V3_PIX` | = TP5 | same |
| 32 | N | `GND` | 0.000 V | 0.000 V |
| 33 | N | `+3V3_PIX` | = TP5 | same |
| 34 | N | `GND` | 0.000 V | 0.000 V |
| 35 | N | `GND` | 0.000 V | 0.000 V |
| 36 | N | `+3V3_CAM` | = TP4 | same |
| 37 | N | `GND` | 0.000 V | 0.000 V |
| 38 | N | `+3V3_PIX` | = TP5 | same |
| 39 | N | `GND` | 0.000 V | 0.000 V |
| 40 | N | `+3V3_PIX` | = TP5 | same |
| 41 | N | `CAM_TRIG0` | 0.000 V (R5 10k↓) | matches the group |
| 42 | N | `CAM_TRIG1` | 0.000 V (R6 10k↓) | matches the group |
| 43 | W | `CAM_TRIG2` | 0.000 V (R7 10k↓) | matches the group |
| 44 | W | `CAM_MON0` | floats — inconclusive | ≈ 3.3 V |
| 45 | W | `CAM_MON1` | floats — inconclusive | ≈ 3.3 V |
| 46 | W | `CAM_RESET_N` | 0.000 V (R4 10k↓) | matches the group |
| 47 | W | **`CAM_SS_N`** | **≈ 3.3 V** — R3 is a 10 kΩ **pull-UP** to `+3V3_CAM` | ≈ 3.3 V |
| 48 | W | `GND` | 0.000 V | 0.000 V |

### What to actually look for

- **Pin 47 is the odd one out by design.** It is the only control pin pulled *up*. If it reads
  0 V, R3 is open/missing or `CAM_SS_N` is shorted to GND. If any *other* control pin reads 3.3 V,
  you have a short to a 3.3 V rail.
- **Uniformity is the test, not the absolute number.** With pull-ups ON, pins 2, 4, 25, 41, 42, 43
  and 46 all sit on identical 10 kΩ↓ + weak↑ dividers and must read within ~50 mV of each other.
  One pin low → open on the FPGA side. One pin high → open pull-down, or short to a rail.
- **11 GND pins:** 5, 20, 21, 27, 30, 32, 34, 35, 37, 39, 48. Any one not at 0.000 V is an
  unsoldered socket contact.
- **Rail groups must match their TP exactly**, not approximately. A pin reading 3.2 V where TP4
  reads 3.30 V means current is flowing through a bad joint.

---

## 6. Resistance walk — power OFF, board unplugged from the Pt

This is what proves the *socket solder joints*, which voltage alone cannot: a socket contact that
is merely near its pad will still show the right voltage through the meter's 10 MΩ input.

Unplug the stack. Wait for the rails to bleed (the TPS7A20's auto-discharge makes this quick).

**6.1 Ground integrity** — TP6 to each of pins **5, 20, 21, 27, 30, 32, 34, 35, 37, 39, 48**:
**< 1 Ω** each. This is the check that would have caught the LauCameraTrigger rev-1 floating-GND
island, so do not skip it.

**6.2 Rail pins to their test point** — **< 1 Ω** each:

| Pins | → TP |
|---|---|
| 1, 19, 29, 36 | TP4 (`+3V3_CAM`) |
| 6, 22, 26 | TP3 (`+1V8_CAM`) |
| 31, 33, 38, 40 | TP5 (`+3V3_PIX`) |

**6.3 Signal pins to their pull resistor** — **< 1 Ω** each. All on the top layer:

| Socket pin | Net | Probe partner | Location |
|---|---|---|---|
| 2 | `CAM_MOSI` | R12 pad 1 | 123.75, 82.79 |
| 4 | `CAM_SCK` | R13 pad 1 | 123.75, 85.09 |
| 25 | `CAM_CLK_PLL` | R14 pad 1 | 148.10, 80.19 |
| 28 | `IBIAS_MASTER` | R1 pad 1 | 148.075, 76.99 |
| 41 | `CAM_TRIG0` | R5 pad 1 | 130.89, 69.70 |
| 42 | `CAM_TRIG1` | R6 pad 1 | 128.39, 69.70 |
| 43 | `CAM_TRIG2` | R7 pad 1 | 123.75, 75.89 |
| 46 | `CAM_RESET_N` | R4 pad 1 | 123.75, 78.19 |
| 47 | `CAM_SS_N` | R3 pad 2 | 123.75, 81.51 |

Also: **pin 28 → TP6 should read 47 kΩ** (R1). That single reading proves the mandatory
`ibias_master` bias resistor is present, correctly valued, and bonded to ground.

**6.4 The LVDS clock pair — the one differential check you can do with a meter:**

> **Pin 23 ↔ pin 24 = 100 Ω.** That is R2, the far-end termination for the FPGA→sensor LVDS clock.

R2 is on the **bottom** layer (147.59–148.61, 83.0), so it is inaccessible while the board is mated
to the Pt — but you do not need to reach it. Measuring 100 Ω between the two socket pins proves
R2's value, both of its joints, and both socket contacts in one reading.

**6.5 Short hunt on the 15 pins with no passive** (3, 44, 45 and 7–18). Resistance can only prove
the *absence* of shorts here, not the presence of connections:

- Each → TP6: **open** (> 1 MΩ)
- Each → its physical neighbour: **open**
- Each LVDS P/N pair (7↔8, 9↔10, 11↔12, 13↔14, 15↔16, 17↔18): **open** with the FPGA
  unconfigured. `DIFF_TERM` is internal to the FPGA and inactive until a bitstream is loaded —
  once one is, these same pairs should read ≈ 100 Ω, which is a useful later confirmation that
  bank 13 really came up at 2.5 V.

---

## 7. What this checklist cannot prove

**Continuity to the FPGA for 15 pins.** `CAM_MISO` (3), `CAM_MON0` (44), `CAM_MON1` (45) and the
12 LVDS pins (7–18) connect only to the socket, the DF40 (J1/J2) and the FPGA ball. With the board
mated there is no third probe point, so a meter can rule out shorts but cannot confirm the FPGA is
actually reachable — unless §4 found config pull-ups ON, in which case the ≈2.5 V / ≈3.3 V readings
*do* confirm it end to end.

**If §4 came back 0.000 V, these 15 pins need a bring-up bitstream** — a walking-ones design that
drives one pin at a time so the meter sees 3.3 V (or 2.5 V) on exactly one socket contact and 0 V
on the other 47. That catches pin **swaps**, which no static reading can. Notes toward building it:

- The 12 LVDS pins are inputs in `pt_camera.xdc`. For bring-up only, re-constrain them as
  `LVCMOS25` **outputs** — legal and safe with the socket empty, since nothing else drives them.
- **Do not drive pins 23 and 24 to opposite levels**: R2's 100 Ω across them would draw 25 mA.
  Drive one at a time with the other at Hi-Z, or drive both to the same level.
- `iocheck/pt_stack_iocheck.v` is a *placement* check only — it proves the pin plan fits, it does
  not toggle anything. The walking-ones design is new work.
