# LauPythonCamera_Pt_Stack — Design Blueprint

Custom Alchitry **element board** carrying an onsemi **PYTHON 1300** global-shutter image
sensor in a **socketed 48-pin LCC**, for the AuV2-SLI structured-light system.

## Status

| | |
|---|---|
| Pin plan | ✅ **Confirmed by Vivado** (§13.1) |
| Whole stack — Pt V2 + Hd + Ft+ + camera | ✅ **Confirmed by Vivado** — 105 ports, no conflicts (§13.2) |
| Schematic | ⚠️ Complete and audited — but the **power tree was rebuilt from scratch** after an audit found it architecturally wrong (§6.5). **Deserves a human read before fab.** |
| Socket footprint | ✅ Built and verified (§12) — **no longer blocked** |
| Stackup / netclasses / DRC rules | ✅ Written (§11) |
| **Layout** | ❌ Not started — needs KiCad |
| **Sensor + socket ordered** | ❌ **27-week factory lead if the in-stock units go. Do this first.** |

**Eleven bugs have been found and fixed in this design so far**, including two that would have
destroyed the sensor and two that would have scrapped the board. Every one was a *boundary
crossed without re-deriving*: datasheet frame → KiCad frame, body extent → copper extent,
thermal question → voltage question, logic behaviour → electrical behaviour.

The FPGA side is tool-verified (§13). **The board side is verified only by my own arithmetic.**
The power section in particular has been rewritten and should be read by a human before copper.

---

## 1. What this board is for

The SLI system puts the FPGA **inline on HDMI between the host PC and the projector**
(hence the EDID-merge / HPD re-pulse logic in the main RTL). This board adds the *camera*
half: a 1.3 MP global-shutter sensor whose exposure can be synchronized to the projected
pattern sequence.

Because the camera must capture **in sync** with the projected patterns, HDMI pass-through
and camera capture run **simultaneously**. That constraint drives the entire pin plan below.

---

## 2. Board stack

```
   [ LauPythonCamera_Pt_Stack ]  <- THIS BOARD.  TOP, Bank B (B33-B78), bank 13, VCCO = 2.5 V
 ────────────────────────────────
          Alchitry Pt V2            XC7A100T-2FGG484I, 206 IO, 256 MB DDR3L
 ────────────────────────────────
   [ Sp ]    spacer — clears the Pt's bottom-side capacitors        $14.99
   [ Ft+ ]   bottom  A3-A42 + B3-B24   (3.3 V)   FT601Q USB3, 350 MB/s measured
   [ Sp ]    spacer — micro-HDMI / USB cable clearance              $14.99
   [ Hd  ]   bottom  A45-A78           (3.3 V)   2x micro-HDMI (PC in, projector out)
```

**This board goes on TOP, alone.** Rationale:

- The **Hd's signal pass-through is undocumented** and may terminate a stack. Keeping the
  camera on top means it never has to sit above the Hd, and **this board needs no
  pass-through connectors at all.**
- The 32 multi-voltage pins (the only ones that can run at 2.5 V) exist **only on the top**
  Bank-B connector. There are **zero** multi-voltage pins on the bottom. The camera
  therefore *must* be on top.

A `Br` ($14.99) may substitute for either `Sp`. **Do not use the `Fn`** — its fan-control
solder jumper lands on **B36, a bank-13 pin**, and would consume half of one of our LVDS
pairs.

---

## 3. Why the pin plan works — bank voltages

This is the crux of the design, and it was the one thing that could have sunk the stack.

| Consumer | IO standard | Required VCCO |
|---|---|---|
| Hd (HDMI TMDS) | `TMDS_33` | **3.3 V** |
| Ft+ (FT601 FIFO) | `LVCMOS33` | **3.3 V** |
| **This board (sensor LVDS)** | **`LVDS_25`** | **2.5 V** |

An FPGA bank has exactly one VCCO, so these had to land in different banks. On the Pt V2
they do:

| Bank | VCCO | Pins |
|---|---|---|
| 14, 16, 34, 35 | **hardwired 3.3 V** | Hd and Ft+ live here |
| 15 | 1.35 V | DDR3L (internal) |
| **13** | **switchable 3.3 / 2.5 / 1.8 V** | **top Bank B, B33-B78 — 32 pins / 16 pairs — ALL OURS** |

**Neither the Hd nor the Ft+ places a single pin in bank 13.** Verified against Alchitry's
own constraint sources (`ft_plus_v2.acf`, `hd_v2.acf`, `PtV2TopPin.kt`) and cross-checked
against the official `pt_hd_bottom.xdc` / `pt_ft_plus_bottom.xdc`. The product pages do not
contain this information.

The Ft+ (A3-A42 + B3-B24) and the Hd (A45-A78) are also **mutually disjoint** — they are
designed to coexist. Their only conflict is mechanical (cable clearance), solved by the `Sp`.

### LVDS_25 nuance that forces bank 13

An `LVDS_25` **input** with *external* 100 Ω termination imposes no VCCO requirement and
would work in a 3.3 V bank. But:

- an `LVDS_25` **output** — which we need, to drive `lvds_clock_in±` **into** the sensor — and
- any input using the FPGA's **internal `DIFF_TERM`**

both **require VCCO = 2.5 V**. So bank 13 is mandatory regardless of how we terminate.

> **DANGER.** Alchitry: *"Failing to set the tri-voltage pins correctly could damage the
> FPGA."* See the VBSEL strapping in §6.

---

## 4. Sensor

**`NOIP1SN1300A-QDI`** — PYTHON 1300, monochrome, 4-LVDS output, 48-pin LCC.

| | |
|---|---|
| Resolution / rate | 1280 x 1024, 10-bit, **150 fps** |
| Pixel | 4.8 x 4.8 µm, global shutter |
| Package | 48-pin LCC, **14.22 x 14.22 mm, 1.016 mm pitch**, D263 glass lid |
| Output | 4 LVDS data + 1 LVDS sync + 1 LVDS clock out; 720 Mbps/ch |
| Supplies | `vdd_33` 3.3 V, `vdd_18` 1.8 V, `vdd_pix` 3.3 V (tight: 3.25-3.35 V) |
| Price / lead | ~$139, long factory lead — order early |

**The VITA 1300 (`NOIV1SN1300A`) is pin-for-pin identical** — all 48 pins, both LVDS and
parallel variants — so this board accepts either. PYTHON is preferred: faster, and the VITA
datasheet carries a *"no silicon fix planned"* erratum (first-row blooming, costing 5 rows).

Design the rails to **PYTHON's tighter tolerances** so either part drops in.

> Order the **`-QDI`** suffix, **not `-QTI`** — the latter ships with a protective foil over
> the glass.

### Bandwidth check

```
1280 x 1024 x 10 bit x 150 fps  =  245.8 MB/s
Ft+ (FT601Q), measured           =  350   MB/s     ->  fits, ~42% headroom
Ft  (FT600),  measured           =  190   MB/s     ->  does NOT fit
```

The Ft+ is **mandatory**. The Ft cannot carry full-rate capture, and the Pt V2's onboard
FT2232HQ is USB 2.0 (JTAG/UART only) — not a data path.

---

## 5. Pin plan

<!-- PIN_TABLE_START -->

**Clocking scheme: the FPGA drives the sensor's LVDS clock directly** (`lvds_clock_in±` at
~360 MHz, PLL bypassed). `clk_pll` is routed but unused — kept as an escape hatch.

### 5.1 LVDS — top Bank B, bank 13, VCCO = 2.5 V

All 7 differential pairs. `IOSTANDARD LVDS_25`; inputs use internal `DIFF_TERM TRUE`
(legal because bank 13 is at 2.5 V).

**All seven pairs are on the DF40's EVEN row.** This is **forced by geometry, not chosen** —
see §5.1.1. An earlier revision used the odd row (both MRCC pairs are there) and was wrong.

| Sensor signal | Sensor pins (N/P) | Dir (FPGA) | Elem pins (N, P) | FPGA pins (N, P) | Clock cap. |
|---|---|---|---|---|---|
| `clock_out±` | 7 / 8 | IN | **B40, B42** | **Y12, Y11** | **SRCC** — forwarded bit clock |
| `doutn0 / doutp0` | 9 / 10 | IN | B46, B48 | V15, U15 | (spare SRCC) |
| `doutn1 / doutp1` | 11 / 12 | IN | B52, B54 | AB17, AB16 | — |
| `doutn2 / doutp2` | 13 / 14 | IN | B58, B60 | AA16, Y16 | — |
| `doutn3 / doutp3` | 15 / 16 | IN | B64, B66 | T15, T14 | — |
| `sync±` | 17 / 18 | IN | B70, B72 | Y14, W14 | — |
| `lvds_clock_in±` | 23 / 24 | **OUT** | B76, B78 | W16, W15 | — |

Spare: all 8 odd-row pairs. **`(B34,B36)` is deliberately NOT used** — `B36` is the Alchitry
`Fn` fan-control pin.

#### 5.1.1 Why the EVEN row — geometry, not preference

**The DF40's two rows escape in OPPOSITE directions.** Measured from the footprint on the
fabbed boards: **odd pins at y = +1.355 mm, even pins at y = −1.355 mm**, 40 each, on 0.4 mm
pitch. Each row's SMT tails splay *outward*, away from the connector centreline.

Now put that on the real board (§8, taken from the fabbed `LauCameraTrigger_Alchitry_Stack`):

```
   board:   55 x 45 mm
   Bank B:  at y = 41       -> odd row at y = 42.4, even row at y = 39.6
   board edge at y = 45     -> the ODD row escapes into a 2.6 mm strip.
```

**A 16.76 mm socket cannot fit below Bank B.** So the sensor must sit *above* it — and
therefore **only the EVEN row faces the sensor.** An odd-row pair would have to cross the even
row's 0.4 mm-pitch pads (impossible on `B.Cu`) or loop around the connector body. Seven
differential pairs looped around a connector at 720 Mbps is not a route anyone wants.

**The catch, and how it was settled.** Bank 13's even row has **no MRCC pairs** — only two
**SRCC**. So: can an SRCC pin drive `BUFIO` + `BUFR` into a cascaded `ISERDESE2`? I.e. does
the *real* 1:10 LVDS receiver place on these pins?

**Vivado says yes** (§13.1 — `iocheck/pt_camera_rx.v` is the actual receiver, not a stub):

```
  BUFIO placed     : 1     <- an SRCC pin drives it
  BUFR  placed     : 1     <- an SRCC pin drives it
  ISERDESE2 placed : 10    <- 5 lanes x master+slave cascade, 10 bits each
```

A `BUFG` (which *does* need MRCC) is the wrong structure for a 720 Mbps source-synchronous
link anyway. **`BUFIO` is the low-skew I/O clock you actually want**, and SRCC drives it.

#### The order, and why nothing crosses

With the (corrected, §12) footprint at rotation 0, the sensor's LVDS pins land contiguously on
the edge facing Bank B:

```
  sensor bottom edge, left -> right:
  7/8 clock_out | 9/10 dout0 | 11/12 dout1 | 13/14 dout2 | 15/16 dout3 | 17/18 sync
  [corner]
  23/24 lvds_clock_in     <- right edge, mid-height
```

The table assigns even-row pairs in that same left-to-right sequence, so **nothing crosses**.
`lvds_clock_in` takes the far-right pair because it exits the sensor's right edge.

**Polarity is free.** On the sensor, N is the lower pin number (7 = `clock_outn`). On the
connector, N is also the lower element-bus pin (B40 = N). Every pair runs N-to-N, P-to-P with
no intra-pair swap.

### 5.2 Single-ended control — top Bank A, 3.3 V

**Deliberately NOT in bank 13.** The sensor's CMOS pins are **3.3 V** level; driving them
from a 2.5 V bank would put V<sub>OH</sub> uncomfortably close to the sensor's V<sub>IH</sub>.

Top Bank A is **hardwired 3.3 V and cannot be dragged to 2.5 V by the VBSEL straps** —
`PtV2TopPin.bankToVcco()` returns a single-element `["3.3"]` for banks 14/16/34/35, and only
bank 13 returns `["3.3","2.5","1.8"]`. VBSEL controls bank 13 and nothing else. All 52 pins of
top Bank A are free (the Ft+ and Hd are on the *bottom*).

`IOSTANDARD LVCMOS33`.

#### The bank 14 / bank 35 split is deliberate — do not shuffle it

**`A3-A6` are bank 14, the Artix-7 configuration bank. `A9-A18` are bank 35, ordinary IO.**

FPGA user I/O sit **Hi-Z until `DONE` goes high**. Between power-up and bitstream load, every
control line into the sensor floats. So anything that could *disturb* the sensor while
floating is kept **off bank 14** and given an **external pull on this board**:

| Sensor signal | Sensor pin | Dir | Elem pin | FPGA pin | Bank | Pull |
|---|---|---|---|---|---|---|
| `mosi` | 2 | OUT | A3 | AB22 | 14 | — |
| `miso` | 3 | IN | A4 | AB18 | 14 | — |
| `sck` | 4 | OUT | A5 | AB21 | 14 | — |
| `clk_pll` | 25 | OUT | A6 | AA18 | 14 | — *(unused; PLL bypassed)* |
| `reset_n` | 46 | OUT | A9 | E3 | 35 | **R4, 10k PULL-DOWN** |
| `ss_n` | 47 | OUT | A10 | N2 | 35 | **R3, 10k PULL-UP** |
| `trigger0` | 41 | OUT | A11 | F3 | 35 | **R5, 10k PULL-DOWN** |
| `trigger1` | 42 | OUT | A12 | P2 | 35 | **R6, 10k PULL-DOWN** |
| `trigger2` | 43 | OUT | A15 | M2 | 35 | **R7, 10k PULL-DOWN** |
| `monitor0` | 44 | IN | A16 | L1 | 35 | — |
| `monitor1` | 45 | IN | A17 | M3 | 35 | — |

`A18` (M1) is spare.

**Why each pull:**
- **`ss_n` pulled HIGH** — a floating SPI select could read as **asserted**, and the sensor
  would try to clock in garbage from a floating `sck`/`mosi`.
- **`reset_n` pulled LOW** — holds the sensor *in reset* until the FPGA is configured and
  deliberately releases it. Fail-safe direction.
- **`trigger0-2` pulled LOW** — no spurious exposures during configuration.

Bank 14 then carries only signals that are harmless while floating: `mosi` and `sck` (inert
while `ss_n` is deasserted), `miso` (an input), and `clk_pll` (unused entirely).

The XDC also sets `BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN` so the FPGA's internal weak pulls
agree with the external ones rather than fight them. **The external resistors are the primary
guarantee** — don't rely on the bitstream setting alone, since it does nothing before the
bitstream loads, which is exactly the window that matters.

> **No I²C pull-ups.** Unlike `LauMipiCamera_Alchitry_Stack` (which has `R_SCL`/`R_SDA` at
> 4k7), the PYTHON uses **SPI**, not I²C. Push-pull, no bus pull-ups needed.

### 5.3 Control header (C) — power and strapping

| C pin(s) | Signal | This board |
|---|---|---|
| 1, 3, 5, 7, 9, 11, 13, 15 | `+3.3V` | Input |
| 2, 4, 6, 8, 10, 12, 14, 16 | `VDD` (5–12 V) | Input — source for local regulators |
| 17–28 | `GND` | — |
| **38** | **`VBSEL A`** | **Strap HIGH** |
| **40** | **`VBSEL B`** | **Strap HIGH** |

`VBSEL A` = `VBSEL B` = HIGH → **bank 13 VCCO = 2.5 V**. Required. Nothing else may drive them.

> **Do not route the JTAG pins** (C43/45/47/49). Alchitry: *"JTAG pins will be reordered to
> match the Au on rev B."* That mapping is changing between board revisions.

### 5.4 Connector pin pattern (both A and B, 80-pin)

```
pins  1, 2   GND
pins  3, 4   IO (N)      pins  5, 6   IO (P)     -> pairs (3,5) and (4,6)
pins  7, 8   GND
      ... repeating: 2 GND, then 4 IO, ...
pins 79,80   GND
```
52 IO + 28 GND = 80. Odd column pairs with odd; even with even.

> **Bottom-side connector numbering is MIRRORED** relative to the silkscreen. Irrelevant here
> (this board is on top) — but it will bite if the board is ever moved to the bottom.

### 5.5 ✅ This pin plan is CONFIRMED BY VIVADO — see §13

Everything in §5 was derived by hand, from Alchitry's `.acf` files, a Kotlin source file, and
a scraped Xilinx package CSV. **It has since been checked against Xilinx's own device
database and it passes.** Do not "correct" this table from memory. See **§13**.

<!-- PIN_TABLE_END -->

---

## 6. Power and strapping — rules this board MUST obey

### 6.1 VBSEL — sets bank 13 to 2.5 V

Bank 13's VCCO is selected by two strap pins on the **50-pin control header (C connector)**:

| VBSEL A | VBSEL B | Bank 13 VCCO |
|---|---|---|
| floating | floating | 3.3 V |
| low | low | 3.3 V |
| low | high | 3.3 V |
| high | low | 1.8 V |
| **high** | **high** | **2.5 V  <- REQUIRED** |

**This board must strap `VBSEL A` = `VBSEL B` = HIGH.** No other board in the stack may
drive them. (low = 0-0.9 V, high = 1.1-3.3 V.)

### 6.2 Local regulation — the element connectors have no 2.5 V and no 1.8 V

The element connectors expose **only `+3.3V` and `VDD`**. There is **no 2.5 V and no 1.8 V** on
any element connector — the Pt's internal 2.5 V rail is bank 13's VCCO and is not brought out.
**This board generates every sensor rail locally.**

> **`VDD` is 5 V.** The Pt is powered over **USB-C**. Alchitry spec `VDD` as "5–12 V board
> power," but this design targets **5 V and only 5 V**. That is a deliberate commitment, and it
> is what makes the power tree in §6.5 possible: at 5 V in, LDOs are cheap, and `vdd_33` can
> have real PSRR. **If you ever power the Pt from 12 V, this board's regulators are out of
> spec.**

| Rail | Sensor pins | Spec | Current |
|---|---|---|---|
| `vdd_33` | 1, 19, 29, 36 | 3.2–3.4 V | ~140 mA |
| `vdd_18` | 6, 22, 26 | 1.7–1.9 V | ~80 mA |
| `vdd_pix` | 31, 33, 38, 40 | **3.25–3.35 V** — tight | ~5 mA |

### 6.3 Power entry

**All element power arrives on the 50-pin control header (C connector) only.** The Bank A
and Bank B 80-pin connectors carry **IO + GND exclusively — no power pins.** Route
`+3.3V` / `VDD` / `GND` from the C header.

### 6.4 Required discretes

- **47 kΩ from `ibias_master` (pin 28) to `gnd_33`.** Non-optional — the sensor's master
  bias reference.
- **100 Ω differential termination** on the LVDS receive pairs, unless using the FPGA's
  internal `DIFF_TERM` (which is available, since bank 13 is at 2.5 V).

---

### 6.4.1 Pull resistors — every sensor CMOS INPUT gets one

FPGA user I/O are **Hi-Z until `DONE` goes high**. During the whole configuration window,
`vdd_33` is already up and every control line into the sensor floats. A floating CMOS input
sits at an indeterminate level and burns **crowbar current** in the sensor's input buffer.

**Every sensor CMOS input on this board has a pull. All eight of them.**

| Net | Sensor pin | Dir | Pull | Why |
|---|---|---|---|---|
| `ss_n` | 47 | IN | **R3, 10k UP** → `+3V3_CAM` | a floating select could read as ASSERTED |
| `reset_n` | 46 | IN | **R4, 10k DOWN** | active-low → holds the sensor in reset. Fail-safe. |
| `trigger0-2` | 41-43 | IN | **R5/R6/R7, 10k DOWN** | rising edge starts integration; low = no spurious exposure |
| `mosi` | 2 | IN | **R13, 10k DOWN** | |
| `sck` | 4 | IN | **R14, 10k DOWN** | |
| `clk_pll` | 25 | IN | **R15, 10k DOWN** | unused in this clocking scheme, but still a CMOS input |

> **`R13`/`R14`/`R15` were missing.** They were originally exempted because `mosi` and `sck`
> are "inert while `ss_n` is deasserted". **That is a logic argument, not an electrical one.**
> The pins are CMOS input buffers regardless of what the protocol is doing, and they float for
> tens to hundreds of milliseconds at every power-up.

> **`R3` pulls up to `+3V3_CAM`, not `+3V3_SYS`.** This looks like an inconsistency and is
> deliberate: pulling to the *sensor's own* rail avoids driving a pin high through the sensor's
> ESD structure while `vdd_33` is still off. Do not "tidy" it to `+3V3_SYS`.

`BITSTREAM.CONFIG.UNUSEDPIN PULLDOWN` in the XDC does **not** cover these — they are *used*
pins, and the setting does nothing before the bitstream loads, which is exactly the window
that matters. **The external resistors are the only guarantee.**

### 6.5 The power tree

```
  VDD (5 V, USB-C) --+--> U2  LDO 1.8 V              --------------> +1V8_CAM   vdd_18   80 mA
                     |
                     +--> U3  LDO 3.3 V, LOW-NOISE --+-- C31 ------> +3V3_CAM   vdd_33  140 mA
                                                     |   10u
                                                     +-- FB1 ------> +3V3_PIX   vdd_pix   5 mA
                                                        BLM18
```

Two LDOs. That is the whole thing.

#### Why `vdd_pix` is not its own rail — and why that is the important part

`vdd_pix` and `vdd_33` are **both 3.3 V**. `vdd_33`'s window is 3.2–3.4 V; `vdd_pix`'s is
3.25–3.35 V. **A single LDO holding the tighter window satisfies both.** So `vdd_pix` is simply
`vdd_33` through a ferrite, the bead giving the pixel array its HF isolation.

**The consequence is the whole point: `vdd_pix` cannot outlive `vdd_33`.**

The datasheet (p.18) requires power-down in the order `vdd_pix → vdd_33 → vdd_18` and warns that
*"Any other sequence can cause high peak currents."* An earlier revision fed `vdd_pix` from an
**independent rail**, which meant it sat at 3.3 V into a dead core for ~9 ms on **every**
power-down. That took a PMOS clamp, a series resistor and a diode to *mitigate* — and even then
it was a race.

**Sharing the rail removes the failure mode by construction.** No clamp, no diode, no
supervisor, no supply monitor. On power-down `vdd_pix` drains back through `FB1`'s 0.38 Ω DCR:
τ ≈ 5 µs.

> This is the topology of **[ruffner/lupa300](https://github.com/ruffner/lupa300)** — a *fabbed*
> board with the same class of sensor: **one clean LDO, split to the sensitive rails by
> ferrites, with the LDO's output capacitor before the beads.** Reading their schematic is what
> exposed this.

#### `vdd_33` must be REGULATED, not switched

An earlier revision took `vdd_33` through a **load switch** off the Pt's `+3V3_SYS`. That was
architecturally wrong, in two independent ways:

- **`+3V3_SYS` is a 4 A ADP5052 buck feeding an XC7A100T and DDR3L.** A load switch has **no
  PSRR**. Every FPGA and DDR3 transient landed directly on the sensor's **analog** 3.3 V domain.
  Meanwhile the 5 mA `vdd_pix` got the low-noise LDO. **Backwards.**
- **The DC budget did not close.** At ±2% on the Pt's rail there were **34 mV** of total IR
  budget for connector + trace + switch — and **at ±3%, no R<sub>DS(on)</sub> works at all. Out
  of spec at zero ohms.**

Regulating `vdd_33` from `VDD` fixes the PSRR *and* the tolerance with one part.

#### `U3` — requirements, not yet a part number

**This is the one part on the board where a substitution silently breaks it.**

| | |
|---|---|
| Output | 3.3 V |
| **Total error** | **≤ ±1.5% OVER TEMPERATURE** |
| Noise / PSRR | low-noise, high-PSRR (it feeds the pixel array) |
| Current | ≥ 200 mA |
| **EN V<sub>IH</sub>** | **≤ 1.2 V** — its enable is driven from the **1.8 V** rail |
| Package | SOT-23-5 / TSOT-23-5 |

Candidate: **`ADP7118ARDZ-3.3`** (TSOT-23-5, V<sub>in</sub> 2.7–20 V, 200 mA, 15 µV RMS,
PSRR 88 dB). **Verify its over-temperature accuracy against the window before ordering.**

> **"±1%" is not the same as "±1.5% over temperature."** The `vdd_pix` window is **±1.52%**, and
> a part *sold* as ±1% is typically ±1% at 25 °C and **±2% over temperature** — 3.234–3.366 V,
> **outside at both ends.** An earlier revision of this README specified "±1% or better," which
> was itself inadequate. Read the temperature column, not the headline number.

> **Do NOT use the `ADP7158`.** An earlier revision named it. It is wrong three times over: its
> **abs-max V<sub>IN</sub> is 7 V**; it is a **2 A** part (the load is 5 mA); and it is a 10-lead
> LFCSP / 8-lead SOIC, **never a SOT-23-5**.

#### `C31` sits at `U3`'s VOUT pin, BEFORE the bead

An LDO's output capacitor is part of its **compensation loop** and must be at the pin. An
earlier revision put *every* `vdd_pix` capacitor behind `FB1`, leaving the LDO output with
**zero** capacitance — the bead's impedance then swamps the cap's at loop crossover and the
regulator can peak or oscillate. Size `C31` from the chosen part's datasheet (typically ≥10 µF)
and **derate for DC bias**: a 2.2 µF X5R 0402 at 3.3 V is only ~1.2 µF effective.

#### Sequencing

*Power-up* is an RC cascade: `R8`/`C29` enables `U2` from `VDD`; `R9`/`C30` enables `U3` from
**`+1V8_CAM`**. Deriving the second enable from the *first rail* means the order is guaranteed
**by construction**, not by matched time constants: **`vdd_18` → `vdd_33`** (and `vdd_pix` with
it, through `FB1`). ~0.6–1.5 ms per stage against a 10 µs minimum.

**That is why `U3`'s EN V<sub>IH</sub> must be ≤ 1.2 V** — its enable asymptote is 1.8 V, not
3.3 V. A part with a 1.5 V threshold has only 0.3 V of margin; one spec'd **ratiometrically**
(e.g. 0.7 × V<sub>IN</sub> = 3.5 V) **never turns on at all**, and the sensor comes up
half-powered.

*Power-down* needs nothing, because `vdd_pix` shares `vdd_33`'s rail. See above.

#### Decoupling

| | |
|---|---|
| 11 × **1 µF** | one per supply pin — primary |
| 11 × **10 nF** | one per supply pin — **HF** |
| 3 × 10 µF + 3 × 100 nF | per rail |
| `C31` / `C32` / `C33` | `U3` output; `VDD` input bulk + HF |

**The 10 nF bank is not optional.** There was originally **no capacitor anywhere on this board
below 100 nF** — while the sensor's **LVDS drivers run off `vdd_18` and toggle at 360 MHz**. A
1 µF X7R 0402 self-resonates at 3–6 MHz and is *inductive* above that. Compounding it: **both
inner layers are ground (§11.2) — there is no power plane**, so the rails are `F.Cu` traces; and
the sensor is **socketed**, adding ~1–3 nH per contact. **The HF PDN was the weakest part of
this design.**

#### VBSEL — verified, and clean

`R10`/`R11` (1 k to `+3V3_SYS`) strap `VBSEL_A`/`VBSEL_B` HIGH → **bank 13 VCCO = 2.5 V**.
Not optional (§3).

They land on **TPS2116 `PR1` pins** — analog comparator inputs with a ~1.0 V internal reference,
which is why Alchitry spec a *band* ("low = 0–0.9 V, high = 1.1–3.3 V") rather than a CMOS
level. The Pt already loads them with **10 kΩ**, so our 1 kΩ gives **3.3 × 10/11 = 3.0 V** —
comfortably inside the high band, at 0.3 mA.

**Nothing else in the stack drives them.** The **Hd**, **Ft+** and **Br** control headers are
**pure pass-throughs with zero components on any control pin** — verified from their schematics.
The only unverified link is the **`Sp` spacer**, which has no published schematic. Almost
certainly a bare pass-through, but **worth 30 seconds with a multimeter** (C38→GND, C40→GND on a
bare `Sp`) before trusting it.

### 6.6 Connectors

| Ref | Part | Role |
|---|---|---|
| `J1` | `DF40C-80DP-0.4V` | **Bank A** — 11 single-ended control signals (banks 14/35, 3.3 V) |
| `J2` | `DF40C-80DP-0.4V` | **Bank B** — the 7 LVDS pairs (bank 13, 2.5 V), all on the **EVEN** row (§5.1.1) |
| `J3` | `DF40C-50DP-0.4V` | **Control header** — *all* element power, plus the VBSEL straps |

The 80-pin symbol puts **odd pins on the left, even on the right**, mirroring the connector's
two physical rows — so each differential pair (odd, odd+2) lands adjacent on the same side of
the symbol, and the row split that drives the whole layout (§5.1.1) is visible in the
schematic rather than buried in a table.

**`J1` and `J2` carry no power pins at all** — only IO and GND. Every volt arrives on `J3`.

### 6.7 Cross-checked against a fabbed board

This design was compared against
**[ruffner/lupa300](https://github.com/ruffner/lupa300) → `pcb/uZed/lupa-hdmi-carrier.sch`** —
a *manufactured* FPGA carrier for a LUPA300 in the **same Andon LCC48 socket**. Three changes
came out of that comparison:

| Change | Why |
|---|---|
| **`FB1` — ferrite bead on `vdd_pix`** | Their board beads `VPIX`, `VDDA` and `VADC` (BLM18). We had **nothing** on the pixel supply — the most noise-sensitive rail on the sensor. This was a genuine omission. |
| **The whole topology** | They feed **one ultra-low-noise LDO** into the sensitive rails and **split them with ferrites**, with the LDO's output cap **before** the beads. That is now §6.5. It is what eliminated our `vdd_pix` power-down problem *by construction* — and it is why we no longer have a `U4`, a PMOS clamp, or a diode. |
| **Per-pin decoupling 100 nF → 1 µF**, plus `C20-C22` (100 nF per rail) | Their primary decoupler is **1 µF**, not 100 nF, backed by 0.1 µF. A modern 1 µF X7R in 0402 has similar ESL to a 100 nF but 10× the capacitance, so it holds supply impedance down across a wider band. |

**Two things from that board we deliberately did NOT copy:**

- **Their bias network.** Every LUPA300 bias pin (`BIAS1-4`, `ADC_BIAS`, `PRECHARGE_BIAS`) has
  an R **and** a C. It is tempting to look at our bare `R1` and think a cap is missing. **It is
  not.** Those are *voltage* nodes fed by RC dividers; the PYTHON's `ibias_master` is a
  **current reference** — the 47 kΩ to `gnd_33` sets a reference current and the datasheet
  specifies nothing else. Hanging a cap on a current-set node is not obviously safe and onsemi
  does not ask for one. (If you want certainty, that is an AND9158 question.)
- **Their LVDS termination — because they have none.** The LUPA300 is a **parallel** sensor
  (`D0`–`D9`, `FRAME_VALID`, `LINE_VALID`). Their board says nothing about our termination
  scheme. The 51 Ω networks on it are not on the sensor.

They also generate the sensor clock **locally, with an SI514 oscillator**, rather than driving
it from the FPGA. We deliberately do the opposite (§5) — a local oscillator would break
phase-lock to the projector, which is the whole point of the SLI system.

---

## 7. Socket and optics

**Socket: Andon Electronics `680-48-SM-G10-R14-1`** — recommended *by name* in both the
PYTHON and VITA datasheets.

- **Surface-mount, solder-down.** The *socket* solders to this board; the *sensor* drops in
  and is removable. Gold "Senstac" castellation contacts, 10 µin min.
- **Open frame** — no lid, no clamshell, no clip. The sensor is retained by contact friction
  alone. Consider mechanical retention if the system will be handled or vibrated.
- Suffix: **`-1` = with two index/alignment pins**, `-0` = without. If `-1`, the board needs
  two Ø1.50 mm holes.
- Socket height **2.90 mm (REF)**.

> **Index-pin vs board thickness.** The index pins appear to protrude **~1.66 mm** below the
> socket. On a standard **1.6 mm** PCB they would bottom out or protrude through. Either
> order the **`-0`** (no index pins), specify a thicker board, or confirm the protrusion with
> Andon. **Unresolved — see Open items.**

### Optical center — do not center the lens on the package

The sensor's optical center is offset from the package center by:

```
X = -179 µm      Y = +1367 µm     (i.e. ~1.37 mm in Y — large)
```

**Center the lens mount on the optical axis, not on the package or the socket.** This offset
is identical on PYTHON and VITA.

---

## 8. Mechanical — the Alchitry element standard

**These are not free parameters.** The board outline and the DF40 positions must match the
element standard or the board will not mate. Everything below is measured from
`LauCameraTrigger_Alchitry_Stack`, which was **fabbed and works**.

```
  Board         55 x 45 mm, 1.5 mm chamfered corners, notch on the right edge
  Mount holes   4 x Ø2.2 mm at (2.5, 2.5) (2.5, 42.5) (52.5, 2.5) (52.5, 42.5)
                -> a 50 x 40 mm rectangle, 2.5 mm in from each corner

  J2  Bank B    80-pin DF40C-80DP  at (38.0, 41.0)   <- the 7 LVDS pairs
  J1  Bank A    80-pin DF40C-80DP  at (38.0,  4.0)   <- 11 control signals
  J3  Control   50-pin DF40C-50DP  at (16.5,  4.0)   <- ALL power + VBSEL

  ALL THREE CONNECTORS ARE ON B.Cu (bottom). They mate DOWNWARD into the Pt V2.
```

> ⚠️ **Refdes differ from the fabbed reference board.** `LauCameraTrigger_Alchitry_Stack` calls
> the control header `J1` and Bank B `J3`. **This** board calls Bank A `J1`, Bank B `J2`, and
> the control header `J3` — matching its own schematic. The *positions* above are what matter;
> take the refdes from our schematic, not from theirs.

> **Bank B is the one at y = 41.** Confirmed by tracing nets on the fabbed board — it is the
> connector carrying that design's `CAM_TRIG`/`CAM_READY`, which its own notes place on Bank B.

### 8.1 The board has a NOTCH — and the socket is bigger than its body

Two things that constrain placement, both easy to miss:

**1. The socket's copper is 22.35 mm square, not 16.76 mm.** The *body* is 16.76 mm, but the
pads sit at a 9.906 mm radius and are 2.54 mm long, so they reach **±11.176 mm**. That is what
must clear everything.

**2. There is a notch in the right edge**, cut in to **x = 49.5 mm**, spanning y ≈ 8–37. It is
on the fabbed board, so it is presumably connector/cable clearance for the stack. **Keep it.**

Those two together bite:

| Sensor centre x | Socket pads reach | Gap to notch | |
|---|---|---|---|
| **38.0** (aligned with Bank B) | 49.18 | **0.32 mm** | ✗ inside our own 0.3 mm edge-clearance rule |
| 37.0 | 48.18 | 1.32 mm | ok |
| **36.0** | **47.18** | **2.32 mm** | ✓ **use this** |

### 8.2 Floorplan

```
        +--------------------------------------------------+  y=0
        |  o                                            o  |   mount holes (2.5, 2.5) etc
        |         [J3 ctrl]        [J1 Bank A]             |   y=4   (J3=control, J1=Bank A)
        |                                                  |
        |          +----------------------+          +-----+
        |          |                      |          |
        |          |   U1  PYTHON 1300    |          | NOTCH    x >= 49.5
        |          |   socket, F.Cu       |          |          y ~ 8..37
        |          |   centre (36, 22)    |          |
        |          +----------+-----------+          +-----+
        |                 :::::::::::                      |   via field, ~6 mm
        |                [ J2  Bank B ]                    |   y=41  (J2 = Bank B, the LVDS)
        |  o                                            o  |
        +--------------------------------------------------+  y=45
```

- **Sensor centre (36, 22).** Not 38 — see §8.1. Socket copper spans x 24.8–47.2, y 10.8–33.2.
- **LVDS fan skews right.** The sensor's LVDS pads span x 30.4–41.6; Bank B's usable even pairs
  span x 37.8–45.4. So the bundle runs ~5 mm to the right over a ~6 mm drop. Diagonal, but
  **intra-pair matching is preserved** — that is what matters.
- **~6 mm between the socket's bottom pad row and Bank B** is the **via field**. Every signal
  crosses the board (§11.2.1). Reserve it for the 14 signal vias **plus their GND stitching
  vias**. Do not let component placement eat it.
- Control signals exit the sensor's **left** edge → route up to Bank A. Slow; length irrelevant.
- **`R2`** (100 Ω termination) at the sensor's **right** edge, pins 23/24. **`R1`** (47 kΩ bias)
  also right, at pin 28.
- Regulators and bulk caps go **left** (x < 24), well away from the LVDS band.
- **Fiducials:** the fabbed board has none and JLC assembled it anyway. Optional — but for
  0.4 mm-pitch DF40s, three local fiducials are cheap insurance.

## 9. Consequences for the RTL / constraints

Moving from the Au V2 to the Pt V2 changes the FPGA package:

```
Au V2:  XC7A35T-2FTG256I
Pt V2:  XC7A100T-2FGG484I     <- different die AND different package
```

**Every `PACKAGE_PIN` in `constrs_1/imports/RTL/Au2.xdc` becomes invalid.** The RTL port
list survives untouched; the pin map does not. Today that file constrains **45 pins, all
3.3 V** (29 `LVCMOS33` + 16 `TMDS_33`) — **this board introduces the project's first 2.5 V
bank.**

Also budget for re-solving HDMI timing on the new die: the `create_clock` / `set_false_path`
structure carries over, but ISERDES placement and clock routing get re-solved.

---

## 10. KiCad assets

| Asset | Location | Status |
|---|---|---|
| Sensor symbol | `../LauSensorLibrary/LauSensors.kicad_sym` | **Done** — authored from the datasheet pinout, 48/48 pins verified |
| — `PYTHON1300_VITA1300_LVDS` | | for `NOIP1…` / `NOIV1…` (this design) |
| — `PYTHON1300_VITA1300_CMOS` | | for the parallel-output variants |
| Socket footprint | — | **BLOCKED** (see below) |

> The SnapEDA part for `NOIP1SN1300A` exists but is **user-uploaded, not vendor-verified**,
> and its footprint is the **bare LCC-48 land pattern** — wrong for a socketed design. Do not
> use it.

> **Pin 21 differs by variant:** `gnd_18` on the LVDS parts, `clk_out` on the parallel parts.
> Grounding it on a parallel part would destroy a driven output. The two symbols keep this
> straight; don't hand-edit around them.

---

## 11. Stackup and high-speed routing

### 11.1 This board is 4-layer — unlike its predecessors

`LauCameraTrigger_Alchitry_Stack` and `LauMipiCamera_Alchitry_Stack` are both **2-layer**
(`F.Cu` + `B.Cu` only). For the trigger board that is entirely fine — the signals are slow.

**It is not fine here.** Every LVDS pair on this board runs at **720 Mbps** (data and sync)
or **360 MHz** (both clocks). On a 2-layer 1.6 mm board the only reference plane sits across
the full core, so the return path is ~1.6 mm from the signal: large loop area, poor field
containment, and an impedance set by P-to-N edge coupling rather than by a controlled height
over a plane. You can build it; you cannot *control* it. Add a socket and a board-to-board
connector already eating margin, and that is where the eye closes.

> Note: `LauMipiCamera_Alchitry_Stack.kicad_dru` describes its geometry as *"typical for
> ~0.10-0.13 mm trace on a thin (~0.1 mm) prepreg to the GND plane"* — a **4-layer** stackup
> description sitting in a **2-layer** board, with the width/gap left as explicit
> placeholders. The impedance on that board was never actually resolved. Don't inherit it.

### 11.2 Stackup — JLCPCB `JLC04161H-7628`, 1.6 mm

JLCPCB's standard 4-layer stackup. Set this in **Board Setup → Physical Stackup**.

| Layer | Thickness | Material | Role |
|---|---|---|---|
| `F.Cu` | 0.035 mm (1 oz) | copper | image sensor **socket (faces UP)**, all components |
| prepreg | **0.2104 mm** | 7628, Er ≈ 4.4 | |
| `In1.Cu` | 0.0152 mm (0.5 oz) | copper | **SOLID GND — never split under a pair** |
| core | 1.065 mm | FR-4 | |
| `In2.Cu` | 0.0152 mm (0.5 oz) | copper | **SOLID GND** (not power — see below) |
| prepreg | 0.2104 mm | 7628 | |
| `B.Cu` | 0.035 mm (1 oz) | copper | **DF40 connectors (face DOWN)**, LVDS pairs |

### 11.2.1 ⚠️ Vias are MANDATORY, and both inner layers are GROUND

**The socket is on TOP and the DF40s are on the BOTTOM.** They mate downward into the Pt V2,
while the sensor must look upward. **So every signal has to cross the board. One via per trace
is geometrically unavoidable — 25 signals, 25 vias.** Zero-via routing is not an option, and
an earlier revision of this document that claimed otherwise was simply wrong.

**That is why `In2.Cu` is ground, not power.** If it were a power plane, a pair routed on
`B.Cu` would reference *power*, and at the via the return current would have to hop from the
`In1` ground reference to the `In2` power reference — which it can only do **through the
decoupling capacitors**. That is the classic way to wreck a high-speed layer transition.

With both inner layers ground, the transition is a **ground-to-ground hop**, and a stitching
via right beside the signal via gives the return current a direct path.

**You can afford to spend both inner layers on ground because the power here is trivial:**
140 mA + 80 mA + 5 mA. Three rails, all tiny. They route as ordinary traces or small pours on
`F.Cu`. **Do not spend an inner layer on a power plane** — it would buy you nothing and cost
you the LVDS reference.

**Routing the transition:**

```
  F.Cu    socket pads ──escape──┐          (1.016 mm pitch: roomy)
                                │
          ═══════ via ══════════╪═════ + GND stitching via alongside
                                │
  In1     ████ solid GND ███████╪████████
  In2     ████ solid GND ███████╪████████
                                │
  B.Cu                          └──── 100 Ω diff pair ────> DF40 pads
```

Escape the socket on `F.Cu` where the pitch is generous, **drop through in open board area** —
*not* in the congested 0.4 mm DF40 fanout, where there is no room to stitch — then run the
controlled-impedance length on `B.Cu`.

Four rules for the transition, none of which DRC can enforce:

1. **Both vias of a pair, symmetric.** Same distance along the pair, so P and N pick up equal
   delay. An asymmetric via pair converts differential signal into common mode.
2. **A GND stitching via immediately beside each pair's vias.** This is what carries the return
   current between `In1` and `In2`. Without it, the return has no path and the transition
   radiates.
3. **Drop through in open area**, per above.
4. **No stub — for free.** A through-hole via on a 4-layer board spans the full stack, so
   nothing dangles.

Via geometry is `0.5 mm / 0.3 mm` (JLCPCB standard process), set in the `CamLVDS` netclass.

> The impedance target `W = 0.24 / S = 0.20` applies **on both outer layers** — the stackup is
> symmetric, with 0.2104 mm of prepreg to the adjacent ground plane on each side.

### 11.3 Geometry for 100 Ω differential

Edge-coupled microstrip on `F.Cu` over `In1.Cu`, H = 0.2104 mm, T = 0.035 mm, Er ≈ 4.4:

| W (mm) | S (mm) | Z<sub>diff</sub> |
|---|---|---|
| 0.20 | 0.20 | 108.7 Ω |
| 0.22 | 0.20 | 104.1 Ω |
| **0.24** | **0.20** | **99.8 Ω  ← use this** |
| 0.25 | 0.20 | 97.8 Ω |

**`W = 0.24 mm, S = 0.20 mm`.** This is set in the `CamLVDS` netclass.

> These are **IPC-2141 closed-form approximations (±10%)**, not a field-solver result. Before
> fab: confirm in **KiCad Board Setup → Board Stackup** (which has a built-in impedance
> calculator — enter the real Er) and/or JLCPCB's own calculator, and **tick "Impedance
> controlled" when ordering** so JLC verifies the geometry against their actual process.
> Do not treat the table above as final.

### 11.4 Netclasses and DRC

`LauPythonCamera_Pt_Stack.kicad_pro` defines three netclasses:

| Netclass | Track | Diff W / gap | Clearance | Matches |
|---|---|---|---|---|
| `CamLVDS` | 0.24 | 0.24 / 0.20 | 0.25 | `CAM_LVDSCLK*` `CAM_CLKOUT*` `CAM_D0-3*` `CAM_SYNC*` |
| `Power` | 0.5 | — | 0.2 | `+3V3_CAM` `+1V8_CAM` `+3V3_PIX` `GND` `VDD` |
| `Default` | 0.25 | — | 0.2 | everything else |

`LauPythonCamera_Pt_Stack.kicad_dru` enforces: 100 Ω geometry, ≤1.5 mm uncoupled run,
**0.08 mm intra-pair skew**, 0.25 mm clearance to foreign nets, 0.3 mm edge clearance, and
a **warning on any via** in an LVDS pair.

**Intra-pair skew is constrained; inter-lane is deliberately not.** The PYTHON's LVDS link is
source-synchronous and sends training patterns, so the FPGA's `ISERDES` bitslips each lane
independently. Match P to N obsessively; don't waste effort matching lane to lane. (Same
reasoning as the MIPI board's D-PHY rules — it transfers verbatim.)

Because the nets are named `_P`/`_N`, KiCad's **Route Differential Pairs** tool finds all
seven automatically.

### 11.5 Placement rules DRC cannot express

- **`R2` (100 Ω) must sit within a few mm of U1 pins 23/24**, at the *end* of the trace. It is
  the far-end termination for the clock the FPGA drives into the sensor. A termination in the
  wrong place is worse than none.
- **The six sensor-output pairs get no resistors.** They terminate inside the FPGA via
  internal `DIFF_TERM`. This looks like an omission and is not.
- **Decoupling caps hard against their supply pins.** All 11 of them.
- **Keep the LVDS away from the switching regulators.** They are the loudest thing on the board.

### 11.6 Connectors — already solved

Both existing stacked boards use stock KiCad footprints for the Alchitry element bus. Reuse
them directly; nothing needs authoring:

| Connector | Footprint |
|---|---|
| Bank A, Bank B (80-pin) | `Connector_Hirose_DF40:Hirose_DF40C-80DP-0.4V_2x40-1MP_P0.4mm` |
| Control header (50-pin) | `Connector_Hirose_DF40:Hirose_DF40C-50DP-0.4V_2x25-1MP_P0.4mm` |

---

## 12. The socket footprint — where it came from

`LauCamera.pretty/Andon_680-48-SM-G10-R14.kicad_mod`.

Andon's public catalogs give pitch, pad width, and the outer-pad span, but **never dimension
the centreline-to-pad-row offset or the index-hole positions** — the two numbers you cannot
lay out a board without. They came instead from
**[ruffner/lupa300](https://github.com/ruffner/lupa300) → `pcb/libraries/andon.lbr`**, package
`LUPA300` (also a 48-pin LCC on the same Andon socket).

**Every dimension that can be cross-checked against Andon's catalog matches exactly:**

| | Andon catalog | `andon.lbr` |
|---|---|---|
| Pitch | .040 in = 1.016 mm | **1.016 mm** ✓ |
| Pad width | .025 in = 0.635 mm | **0.635 mm** ✓ |
| Outer-pad span (A/B) | .440 in = 11.18 mm | **11.176 mm** ✓ |
| Index holes | Ø1.50 mm, diagonal corners | **Ø1.6 drill, diagonal** ✓ |

And it supplies what Andon withheld:

- **Centreline → pad-row centre = 9.906 mm** (a clean 0.390 in)
- **Index holes at (−8.382, +8.128) and (+8.128, −8.382)** — note these are **deliberately
  asymmetric**: they key the socket so it can only mount one way. Do not "tidy" them into
  symmetry.
- Pad 2.54 × 0.635 mm; socket body 16.764 mm square.

> **The Eagle library's pad NUMBERS were discarded.** They are Eagle auto-names (`P$1`…`P$48`)
> running left/right/bottom/top — **not a perimeter walk**, so they are meaningless. Pin
> numbers in our footprint are assigned from the PYTHON 1300 package drawing.

### ⚠️ THE FOOTPRINT WAS MIRRORED ONCE. Here is the guard.

**KiCad's canvas has +y DOWN. The datasheet drawings use +y UP.** The first version of this
footprint was built straight from the datasheet without converting, which produced a
**mirrored** footprint: numbering ran **clockwise** on the canvas where the real part runs
**counter-clockwise**.

**No rotation of a physical part can fix a mirrored footprint.** It would have been a scrapped
board and a $139 sensor. And it passed every other check — 48 pads, correct pitch, pins 7-18
contiguous — because none of them looked at *handedness*.

**The check that catches it** (now an assert in the generator, and it fires on the old map):

> Read the **left edge, top to bottom, on the KiCad canvas**. It must give
> **`43, 44, 45, 46, 47, 48, 1, 2, 3, 4, 5, 6`** — numbers *increasing* downward, matching
> datasheet Fig. 51 which reads `45, 48, 1, 5` going down.
>
> The mirrored version gave `6, 5, 4, 3, 2, 1, 48, 47, …` — decreasing. That one line is the
> whole test.

If you ever regenerate this footprint, keep that assert.

### Pin 1 and orientation — verified, do not re-derive from memory

From the PYTHON 1300 datasheet, **Figure 51** (tick marks around the package) and **Figure 52
"Top view"**:

- **TOP VIEW: pin 1 is at the MIDDLE OF THE LEFT EDGE**, and numbering increases
  **counter-clockwise**. Figure 51's left-edge ticks read, top to bottom: **45, 48, 1, 5** —
  the numbering wraps 48 → 1 at the middle of that edge.
- Edges, top view:

```
  left  edge:  43..48 (top-left corner DOWN to mid-left), then 1..6 (mid-left DOWN to bottom-left)
  bottom edge: 7..18   left -> right      <-- ALL SIX LVDS OUTPUT PAIRS, contiguous
  right edge:  19..30  bottom -> top      <-- lvds_clock_in = 23/24
  top   edge:  31..42  right -> left
```

- `lvds_clock_in` (23/24) sits on the **right edge, at roughly MID-HEIGHT** — pin 24 at
  y = −0.508 mm, pin 23 at y = −1.524 mm. That is **4–5 pitches (~5 mm) up from the
  bottom-right corner**, *not* immediately around the corner from pin 18. It still takes the
  rightmost DF40 pair (`B75/B77`, §5.1) since it approaches from the right — but budget for a
  longer trace than a corner-adjacent exit would need.

> Earlier notes appeared to conflict — one read said "pin 1 mid-left", another "pin 1 just
> right of top-centre". Both were right: the second was describing the **bottom view**, which
> mirrors. Figure 52 prints both side by side and they are consistent.
>
> **Independent chirality check:** onsemi's *recommended mounting footprint* (CASE 115AO) puts
> pin 1 on the **bottom** edge, **right** of centre. No rotation of the *bottom* view can
> produce that — only a **mirror** can. So the handedness is confirmed twice, by different
> drawings.

### ⚠️ ASSEMBLY: the sensor is NOT mechanically keyed

**The package has no chamfer and no dot. All four ceramic corners are identical (R0.20).**
The only physical index is **pin 1's castellation, which is roughly twice as long as the other
47** (L1 = 1.90–2.42 mm vs L = 0.84–1.20 mm) and T-shaped.

**Consequence: the sensor can physically drop into the socket in four orientations.** Nothing
stops a 90° error.

- **Orient by the top-surface laser marking.** "ON / PYTHON 1300 A" reads upright when pin 1
  is at mid-left. (The protective foil's tab, on `-QTI` parts, also points toward pin 1 — but
  we order `-QDI`, which has no foil.)
- The board's silkscreen carries a **pin-1 dot** outside the left pad row. Use it.
- **Cross-check that closes the loop:** Andon's own Fig. 17 puts **pin 1 on the left-hand
  column, middle** — matching onsemi's mid-left. The socket and the sensor agree.
- **The socket's asymmetric index pins fix the board rotation** — but only if you order the
  **`-1`**. The **`-0`** has no index pins and therefore no keying to the board at all; you
  would be relying on silkscreen alignment by eye. Prefer `-1` if the index-pin protrusion
  (open item 2) can be resolved.

> **Pad uniformity is correct here.** onsemi's *direct-solder* land pattern gives pin 1 a
> longer pad (2.54 mm vs 1.39 mm for the other 47) to match its longer castellation. **Our
> footprint is for the SOCKET**, whose 48 contacts are identical, so all 48 pads are uniform
> (2.54 × 0.635 mm). This is not an oversight — do not "fix" it.

### Still not known

- **PCB-surface-to-sensor-glass height.** Not in the Andon catalog, not in the Eagle library.
  Sets the lens flange focal distance. Currently harmless (bare socket, no lens mount), but it
  will block any optics design. Easiest path: measure it on the physical socket.

---

## 13. Validation — what a tool has checked, not just me

Almost everything in this document was derived by hand: from Alchitry's `.acf` constraint
files, a Kotlin source file (`PtV2TopPin.kt`), a scraped Xilinx package CSV, and two datasheet
PDFs. That is a lot of places to make a quiet mistake. So it has been checked.

Everything below is **reproducible** — the scripts are in `iocheck/`.

### 13.1 Camera pin plan + the REAL LVDS receiver — ✅ PASS

```
vivado -mode batch -source iocheck/run_camcheck.tcl
```

`iocheck/pt_camera_rx.v` is **the actual 1:10 LVDS receiver**, not a stub:

```
  IBUFDS -> BUFIO   -> ISERDESE2.CLK      (360 MHz bit clock, DDR)
         -> BUFR/5  -> ISERDESE2.CLKDIV   (72 MHz word clock)
  ISERDESE2 master+slave cascade = 10 bits/lane, x5 (4 data + sync)
```

`IOSTANDARD` and `DIFF_TERM` are deliberately left to the XDC — the XDC is the thing under
test.

```
  ports placed as constrained : ALL OK (25)
  DIFF_TERM on input pairs    : 6 / 6
  DIFF_TERM on the OUTPUT pair: 0        (R2 terminates it at the sensor)
  BUFIO placed     : 1                   <- an SRCC pin drives it
  BUFR  placed     : 1                   <- an SRCC pin drives it
  ISERDESE2 placed : 10                  <- 5 lanes x master+slave
  cam_clkout_p pin : Y11 = IO_L11P_T1_SRCC_13
  LVDS ports outside bank 13 : 0
  Synthesis: 0 errors   place_design: succeeded
```

**What this proves:**

- **The EVEN-row plan works with the real receiver.** Bank 13's even row has no MRCC pairs,
  and the whole layout depends on using it (§5.1.1). **An SRCC pin drives `BUFIO` and `BUFR`
  into a full 10× `ISERDESE2` cascade.** Without this, the floorplan would have had to change.
- **P/N polarity.** The rule "lower element-bus pin = N" was derived by hand; Vivado's
  placement bears it out on every pair. **A single reversal is a hard error in Vivado.**
- **`R2` is right, and Vivado says so unprompted.** `report_io` gives the six *input* pairs
  `100 Ohm Differential` **on-chip** termination, and gives `cam_lvdsclk` — the *output* — an
  **off-chip `FD_100`**: far-end differential 100 Ω. The tool independently arrived at the
  schematic's termination scheme.

> One trap worth recording. An early version of this harness exposed `word_clk` and the pixel
> buses as **unconstrained top-level ports**. Vivado defaulted them to `LVCMOS18` and then
> failed placement with `[Place 30-294]` — which looked exactly like an SRCC/BUFIO
> incompatibility and was nothing of the sort. Keep the deserialised data internal.

### 13.2 The WHOLE STACK — ✅ PASS

```
vivado -mode batch -source iocheck/run_stackcheck.tcl
```

§13.1 proved the camera's 25 pins were self-consistent. It did **not** prove the Hd and Ft+
could coexist with them — that still rested on reading Alchitry's `.acf` sources by hand.

`iocheck/run_stackcheck.tcl` loads **Alchitry's own published constraint files** alongside ours
and places all four boards as one design:

| File | Board |
|---|---|
| `alchitry_pt_base.xdc` | the Pt's own clk / rst_n / LEDs / USB |
| `alchitry_pt_hd_bottom.xdc` | 2× micro-HDMI, `TMDS_33` |
| `alchitry_pt_ft_plus_bottom.xdc` | FT601Q 32-bit FIFO, `LVCMOS33` |
| `pt_camera.xdc` | **ours** — 7 `LVDS_25` pairs + 11 `LVCMOS33` |

```
  ports placed                 : 105
  pin collisions               : NONE
  bank 13 VCCO = 2.50 V     <-- camera LVDS
  bank 14 VCCO = 3.30 V
  bank 16 VCCO = 3.30 V
  bank 34 VCCO = 3.30 V
  bank 35 VCCO = 3.30 V
  LVDS_25  (camera)            : 14
  TMDS_33  (Hd HDMI)           : 16
  LVCMOS33 (Ft+/base/cam ctrl) : 75
  Synthesis: 0 errors, 0 critical warnings.   place_design: succeeded.
```

**This settles the question that could have killed the project.** HDMI needs `TMDS_33` at
3.3 V; the sensor needs `LVDS_25` at 2.5 V; a bank gets exactly one VCCO; and the camera
**must** run *simultaneously* with the HDMI pass-through, because it has to capture in sync
with the projected patterns (§1). Vivado confirms bank 13 @ 2.5 V coexists with banks
14/16/34/35 @ 3.3 V on the real device, with **no pin claimed twice across the four boards.**

Corroboration worth noting: the pin counts came out **Hd = 24, Ft+ = 44, camera = 25, Pt base
= 12** — exactly the hand-derived figures from the `.acf` files. The scraping was right.

### 13.3 What is still NOT tool-checked

Be clear about the boundary. Vivado has validated the **FPGA side**. It knows nothing about:

| Not checked | How it *was* checked |
|---|---|
| The socket land pattern | Cross-checked against Andon's catalog on every dimension they publish (§12) |
| Sensor pin 1 / orientation | Read directly off the datasheet package drawings, twice, independently (§12) |
| The schematic | Programmatic net check (31 nets, no orphans) + compared against a **fabbed** board (§6.7) |
| Impedance geometry | IPC-2141 closed-form, ±10%. **Confirm with KiCad's stackup calculator and tick "impedance controlled" when ordering** (§11.3) |
| Layout | Not started |

---

## Open items

| # | Item | Blocks | Owner |
|---|---|---|---|
| **1** | **🔴 ORDER THE SENSOR AND SOCKET.** `NOIP1SN1300A-QDI` had **49 in stock at DigiKey and a 27-week factory lead**. If that stock goes, the board arrives and sits on a bench for six months. This is the only genuinely time-critical item in the project and it is *not* blocked on layout. | Nothing — do it now | **You** |
| 2 | **Socket variant: `-0` or `-1`?** The `-1`'s index pins are what key the socket's rotation, but they protrude ~1.66 mm against a 1.6 mm board. The footprint includes both Ø1.6 holes, so `-1` stays available. | Item 1 | Andon (one email), or default to `-0` |
| 3 | **`U3` has requirements, not yet a part number.** 3.3 V, **total error ≤ ±1.5% OVER TEMPERATURE**, low-noise, high-PSRR, ≥200 mA, **EN V<sub>IH</sub> ≤ 1.2 V** (its enable comes from the 1.8 V rail). Candidate `ADP7118ARDZ-3.3` — **verify its over-temp accuracy against the window.** Note "±1%" on a datasheet front page usually means ±2% over temperature, which is **outside** the window. **The BOM is not orderable until this is settled.** | Correct `vdd_pix`, and the sensor coming up at all | Purchasing |
| 4 | **PCB-surface-to-sensor-glass height.** Not published anywhere — not in Andon's catalog, not in the Eagle library. Sets the lens flange focal distance. | Lens mount (not this board) | Measure the physical socket |
| 5 | Neck-down at the 0.4 mm DF40 pads violates `track_width (min 0.22mm)` — needs an **area-scoped DRC exception**, not a lower global minimum. | Clean DRC | Add once the board exists |
| 6 | **Impedance geometry is an IPC-2141 approximation (±10%)**, not a field solver. Confirm in KiCad's stackup calculator and **tick "impedance controlled" when ordering** so JLC verifies it. | Signal integrity | At layout |
| 7 | **Power-DOWN is not sequenced** — only power-up. The RC cascade (§6.5) handles the rise order only. If reverse sequencing turns out to matter, this needs a real sequencer IC. | Possibly nothing | Watch for it in bring-up |
| 8 | Ft+ and Hd current draw — not documented by Alchitry | Power budget | Measure or ask Alchitry |
| 9 | Pt V2's onboard USB2 FIFO signals (`USB_RD`/`USB_WR`/`USB_SIWU`) sit in **bank 13**; setting it to 2.5 V changes their drive level. Appears safe and deliberate, but undocumented. | Nothing (we use the Ft+ for bulk data) | Confirm with Alchitry if the onboard FIFO is ever used |
| 10 | **AND9362/D — PYTHON Developer's Guide** is NDA-gated on the onsemi Image Sensor Portal. It holds the trigger→integration latency, jitter, FOT/ROT clock counts, and the `trigger1`/`trigger2` definitions — **none of which are in the public datasheet**. | Tight trigger synchronisation | Request portal access |

**Closed:** socket land pattern (§12) · bank-13 pin map (§5.1) · P/N polarity (§13.1) ·
stack compatibility (§13.2) · regulators + sequencing (§6.5) · DF40 connectors (§6.6)

---

## References

- onsemi PYTHON 1300 datasheet — `NOIP1SN1300A/D`
- onsemi VITA 1300 datasheet — `NOIV1SN1300A/D` Rev. 11
- Andon Electronics image-sensor socket catalog (socket `680-48-SM-G10-R14-X`)
- Alchitry Pt V2 — <https://shop.alchitry.com/products/alchitry-pt>
- Alchitry constraints reference — <https://alchitry.com/tutorials/references/alchitry-constraints-reference/>
- Alchitry pinouts & custom elements — <https://alchitry.com/tutorials/references/pinouts-and-custom-elements/>
- Authoritative element pin maps: `alchitry/Alchitry-Labs-V2` →
  `src/main/resources/library/components/Constraints/{ft_plus_v2,hd_v2}.acf` and
  `src/main/kotlin/com/alchitry/labs2/hardware/pinout/PtV2TopPin.kt`
