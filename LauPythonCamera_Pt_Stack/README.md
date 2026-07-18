# LauPythonCamera_Pt_Stack — Design Blueprint

> ### Power: §§6.2 and 6.5 were rewritten on 2026-07-18 and now match the board.
>
> **[`../CAMERA_POWER_DESIGN.md`](../CAMERA_POWER_DESIGN.md) remains authoritative** for the
> power tree — full derivation, SPICE verification and BOM. This document is the design
> blueprint; where the two disagree, that one wins.
>
> **What changed, and why it matters historically.** The original power section described a
> "3.3 V tap through ferrites + load switch" tree, built on the premise that the Pt's `+3.3V`
> rail is 3.300 V. **It is 3.278 V** — derivable from Alchitry's own feedback divider
> (R16 = 31.6 kΩ / R15 = 10.2 kΩ into the ADP5052's 0.8 V reference), ±1.5 % → **3.229–3.327 V**.
> That broke the design three ways:
>
> - **`vdd_pix` (3.25–3.35 V) was out of spec** at the rail's low corner, by 21 mV.
> - **`vdd_33` had ~7 mV** of worst-case margin after ferrite and connector IR drop.
> - **The tap made correct power-up sequencing impossible.** `vdd_33` tapped from `+3V3` rises the
>   instant the Pt's rail does, but 1.8 V can only be made by LDO'ing *down from that same rail* —
>   so `vdd_18` always came up **after** `vdd_33`, the forbidden order, on every power-up. That
>   risks latch-up and a dead sensor.
>
> The board uses **boost → LDO**: `+3V3` → TPS61023 boost to 4.45 V → two TPS7A2033 LDOs for
> `vdd_33` and `vdd_pix`, `vdd_18` from a TPS7A2018 off `+3V3` directly, and **two TLV803S
> supervisors** enforcing power-up *and* power-down ordering. **There is no load switch and no
> ferrite on this board.** If you find text saying otherwise, it is stale — report it (§14.6).

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

**`NOIP1SN1300A-QTI`** — PYTHON 1300, monochrome, 4-LVDS output, 48-pin LCC. *(The
originally-specified `-QDI` is discontinued; `-QTI` is the identical sensor with a peel-off
protective foil over the glass — see the ordering note below.)*

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

> **Order the `-QTI` suffix.** The originally-specified `-QDI` (no protective foil) has been
> discontinued. `-QTI` is the identical PYTHON 1300 sensor but ships with a **peel-off
> protective foil** over the glass — remove it before installing the sensor (handle the glass
> by the edges afterward). The foil is otherwise harmless handling protection, and a bonus:
> its tab points toward pin 1 — a useful orientation aid, since the package has no chamfer/dot (§12).

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

> ### ⚠️ SUPERSEDED — we now use the sensor's internal PLL. See [`CAMERA_SENSOR_PROTOCOL.md`](../CAMERA_SENSOR_PROTOCOL.md) §4.
>
> **The escape hatch became the main road.** The FPGA drives **`clk_pll` at 72 MHz (CMOS)** and the
> sensor's internal PLL multiplies ×5 to the 360 MHz bit clock. **`lvds_clock_in±` is NOT driven** —
> those two pins stay undriven and unconstrained.
>
> Why: Avnet's *published, proven* register sequence (`docs/reference/onsemi_python_sw.c`) is the
> PLL variant, and no bypass-mode sequence is published — the datasheet warns that inventing one
> *"may cause the sensor to malfunction."* It also deletes the entire 360 MHz LVDS transmit path
> (no ODDR, no OBUFDS), and it costs nothing: **both modes give 720 Mbps/lane and the same max
> frame rate.**
>
> **The board is unchanged** — `clk_pll` was already routed for exactly this. Nothing to respin.

**Original plan (no longer followed):** ~~the FPGA drives the sensor's LVDS clock directly
(`lvds_clock_in±` at ~360 MHz, PLL bypassed); `clk_pll` is routed but unused — kept as an escape
hatch.~~

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

### 6.2 There is no 5 V. Everything comes from `+3.3V`.

The Alchitry Pt V2 is a **3.3 V board**. The element connectors expose `+3.3V` on the control
header (pins 1–15 odd, 4 A available) and nothing else usable.

> **The `VDD` pins (control header, 2–16 even) are DELIBERATELY LEFT UNCONNECTED.** Alchitry's
> pinout page describes `VDD` as "5–12 V board power," but there is no 5 V on this board. Do
> not design against it, and do not "helpfully" wire it up.

**This board generates every sensor rail from `+3.3V`.** That single constraint shapes the whole
of §6.5. You **cannot LDO 3.3 V down to an accurate 3.3 V** — there is no headroom — so the
board **boosts to 4.45 V first and regulates back down**. That is why `U3` exists.

> An earlier revision concluded from this that `vdd_33` and `vdd_pix` "cannot be regulated" and
> had to be filtered taps. **That conclusion is wrong and is superseded by §6.5.** Both rails are
> now properly LDO-regulated; only the *headroom* had to be manufactured.

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
| `mosi` | 2 | IN | **R12, 10k DOWN** | |
| `sck` | 4 | IN | **R13, 10k DOWN** | |
| `clk_pll` | 25 | IN | **R14, 10k DOWN** | unused in this clocking scheme, but still a CMOS input |

> **Designators verified against the routed board (2026-07-18).** An earlier revision of this
> table listed these three as `R13`/`R14`/`R15`, off by one. **`R15` is not a sensor pull at
> all** — it is the **100 kΩ `EN_PIX` pull-up to `+3V3_CAM`**, i.e. the sequencing interlock
> (§6.5). Do not repurpose it.

> **`R12`/`R13`/`R14` were missing.** They were originally exempted because `mosi` and `sck`
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

> **This tree is what is on the board** — verified against the schematic, the routed copper and
> `production/LauPythonCamera_Pt_Stack_bom.csv`. Full derivation, SPICE verification and BOM:
> **[`../CAMERA_POWER_DESIGN.md`](../CAMERA_POWER_DESIGN.md)**, which is authoritative if the two
> ever disagree.
>
> An earlier revision of this section described a **tap-through-ferrites** design — a load switch
> plus `FB1`/`FB2` beads. **That design is dead, and there is no `FB1` or `FB2` on this board.**
> It could not meet `vdd_pix`'s accuracy window and could not sequence correctly. If you find
> text anywhere referring to `U3` as a "load switch" or to a ferrite, it is stale — report it.

```
  +3V3_SYS  (J3 pins 1,3,5,7,9,11,13,15 — 3.229 to 3.327 V, noisy)
     |
     +--> U3  TPS61023 boost ---> +4V5 (4.45 V) --+--> U4 TPS7A2033 --> +3V3_CAM  vdd_33  140 mA
     |    L1 2.2uH, R8/R9 330k/51k                |
     |                                            +--> U5 TPS7A2033 --> +3V3_PIX  vdd_pix   5 mA
     |
     +--> U2  TPS7A2018 -----------------------------------> +1V8_CAM  vdd_18   80 mA
     |
     +--> U6  TLV803S (2.93 V) --> EN_PIX   (pull-up to +3V3_CAM = the interlock)
     +--> U7  TLV803S (2.93 V) --> EN_BOOST (via R17 1k / C41 220n = 493 us shutdown skew)
```

One boost, three LDOs, two supervisors. The boost exists **only** to give the LDOs headroom —
you cannot LDO 3.278 V down to an accurate 3.300 V. All accuracy and all noise rejection
(75–95 dB PSRR) happen in the LDOs.

**Power-up:** `vdd_18` → `vdd_33` → `vdd_pix`.  **Power-down:** `vdd_pix` → `vdd_33` → `vdd_18`.
Both enforced structurally, not by matched time constants.

> **`+3V3_PIX` must keep ≤ ~1.5 µF total** (C8–C11 are **100n**, not 1µ; there is deliberately
> **no bulk cap** on this rail). Power-down depends on U5's 150 Ω internal auto-discharge
> collapsing `vdd_pix` first. Adding a 10 µF bulk cap here silently breaks the shutdown ordering.
>
> **As built the rail carries 1.540 µF** — C8–C11 100n, C19–C22 10n, C28 100n, C37 1µ — i.e.
> 40 nF over the stated budget. τ = 150 Ω × 1.54 µF = **231 µs**, still three orders of magnitude
> inside the 10 µs requirement, so this is accepted. Recorded so it stays a decision, not a drift.

#### `U3` is a BOOST, and it exists only to manufacture headroom

**You cannot LDO 3.3 V down to 3.3 V.** The Pt's `+3V3` is 3.278 V — an LDO regulates only
*downward* and needs its input meaningfully above its output. So the answer is **up, then back
down**: boost to 4.45 V, then LDO back to an accurate 3.300 V.

`U3` is a **TPS61023** synchronous boost, not a switch and not the regulator. **All accuracy and
all noise rejection happen in the LDOs** (75–95 dB PSRR). The sensor sees **3.300 V ±1.5 %** —
the LDO's spec — rather than 3.278 V ±whatever Alchitry's tolerance turns out to be.

`R8` = **330 kΩ**, `R9` = **51 kΩ** → V_OUT = 0.595 × (1 + 330/51) = **4.445 V**.

> **Do not "tidy" R8/R9 to 649 k/100 k.** That pair gives 4.457 V — a 12 mV difference, entirely
> irrelevant — but **649 kΩ is a JLCPCB *Extended* part**, and paying a feeder fee for one
> resistor is silly. 330 k/51 k are both **Basic**. This was a deliberate choice, not an
> approximation.

**Why 4.45 V and not lower?** The TPS7A20's PSRR is specified at V_IN = V_OUT + 1.0 V. Even at
the low corner of the boost's reference tolerance, 4.33 − 3.3 = **1.03 V**, so full specified
PSRR is retained under all conditions. Dropping to 4.0 V would save ~60 mW of LDO heat and
forfeit that.

`vdd_18` **skips the boost entirely** — it already has 1.43 V of headroom straight from `+3V3`.

#### `vdd_pix` gets its own LDO — and that is what closes the accuracy window

`vdd_pix`'s window is **3.25–3.35 V (±1.52 %)**, which is tight. `U5` is a **TPS7A2033**, the
same part as `U4`, regulating to **3.3 V ±1.5 % → 3.2505–3.3495 V**. That fits, with ~50 mV of
margin at each end.

> **This supersedes an earlier "honest cost" note** which stated that `vdd_pix`'s tolerance *is*
> the Pt's `+3.3V` tolerance, and that the rail could sit ~16 mV outside its window at both
> extremes. **That was true of the dead tap-through-ferrites design and is not true now.**
> `vdd_pix` is independently regulated, so the Pt's rail accuracy no longer propagates to it.
> The old action item — "measure the Pt's 3.3 V rail the day the board arrives" — is **no longer
> load-bearing** for `vdd_pix`. Measuring it is still worth 30 seconds, but nothing depends on it.

#### Sequencing — two supervisors, and both are required

`U6` and `U7` are **TLV803S**, active-low open-drain, V_IT = **2.93 V**, 200 ms power-up delay.

| Node | Circuit |
|---|---|
| `U2.EN` (`vdd_18`) | tied directly to `+3V3` — comes up with the rail |
| `U3.EN` (boost) | `U7` RESET → `R17` **1 kΩ** series → EN; `C41` **220 nF** to GND; 100 kΩ pull-up to `+3V3` |
| `U4.EN` (`vdd_33`) | tied directly to `+1V8_CAM` |
| `U5.EN` (`vdd_pix`) | `U6` RESET direct; **100 kΩ pull-up to `+3V3_CAM`**; `C39` **10 nF** to GND |

**The interlock is `U5.EN`'s pull-up going to `vdd_33`, not to `+3V3`.** Before `vdd_33` exists
that pull-up sits at 0 V, so `vdd_pix` *physically cannot* enable early. The ordering is
structural, not a race between time constants.

> **⚠️ TWO supervisors are required. This is not optional, and the failure is silent.** If one
> open-drain RESET drove both enables, then whenever RESET released, the two nodes would be
> connected *to each other* through the series resistor — the pull-up on `EN_BOOST` would drag
> `EN_PIX` to ~1.6 V, above the TPS7A20's 0.9 V V_IH, and `vdd_pix` would rise *with* `vdd_33`
> instead of after it. Neither a larger resistor nor a diode-AND rescues it (see
> `CAMERA_POWER_DESIGN.md` §6). Cost of the second supervisor: $0.09.

*Power-up* — **`vdd_18` → `vdd_33` → `vdd_pix`**: `vdd_18` at t ≈ 1 ms; both supervisors release
at t ≈ 200 ms; the boost's EN crosses 1.2 V at t ≈ 210 ms → 4.45 V in ~700 µs; `vdd_33` at
t ≈ 212 ms; `U5.EN` then charges (τ ≈ 833 µs) and `vdd_pix` follows **330 µs later**, t ≈ 213.5 ms.

*Power-down* — **`vdd_pix` → `vdd_33` → `vdd_18`**: both supervisors assert **immediately** below
2.93 V (the 200 ms delay applies only to release). `vdd_pix` collapses first via U5's 150 Ω
discharge (τ = 150 Ω × 1.54 µF as built = **231 µs**; `CAMERA_POWER_DESIGN.md` quotes 216 µs
against its 1.44 µF budget); `U3.EN` decays through 1 kΩ into 220 nF and the boost disconnects at
t ≈ 493 µs, dropping `vdd_33` second; `U2` holds `vdd_18` up until `+3V3` falls below ~1.95 V,
so it dies last.

Separations are **hundreds of µs to ms**, against a **10 µs** datasheet requirement — and both
directions are **SPICE-verified** with manufacturer models (`CAMERA_POWER_DESIGN.md` §7.5).

> **The boost is what created the power-down problem, and the supervisor is what solves it.**
> Once started, the TPS61023 runs with V_IN as low as 0.5 V — left alone it would hold 4.45 V
> while `+3V3` collapsed, making `vdd_33` outlive `vdd_18`: the exact inverse of the required
> order. `U7` yanking `U3.EN` low at 2.93 V is what prevents that.

#### Part requirements — do not let a BOM optimiser touch these

| | |
|---|---|
| **`U2`** | `TPS7A2018PDBVR` — LDO 3.3 → 1.8 V, SOT-23-5. Accuracy below 2.8 V out is **±40 mV**, not ±1.5 % — do not write "±1.5 %" next to this rail. |
| **`U3`** | `TPS61023DRLR` — synchronous boost, SOT-563. **EN V<sub>IH</sub> 1.2 V max, absolute not ratiometric.** True input-to-output disconnect in shutdown — which the power-down ordering depends on. |
| **`U4`, `U5`** | `TPS7A2033PDBVR` — **same part number for both.** 150 Ω auto-discharge; `U5` relies on it. |
| **`U6`, `U7`** | `TLV803SDBZT` — 2.93 V threshold. **Two of them.** |
| **`L1`** | 2.2 µH, **I<sub>sat</sub> ≥ 1.2 A** (peak is 584 mA worst-case → 2.05× margin). |

#### Decoupling

As built, verified against the routed copper — **11 supply pins, each with a primary cap and a
10 nF**, plus per-rail bulk:

| Rail | Per-pin primary | Per-pin HF | Bulk / local | Total |
|---|---|---|---|---|
| `vdd_33` (`+3V3_CAM`, 4 pins) | C1–C4 **1 µF** | C12–C15 **10 nF** | C23 10 µF, C26 100 n, C35 1 µF | 15.14 µF |
| `vdd_18` (`+1V8_CAM`, 3 pins) | C5–C7 **1 µF** | C16–C18 **10 nF** | C24 10 µF, C27 100 n, C30 1 µF | 14.13 µF |
| `vdd_pix` (`+3V3_PIX`, 4 pins) | C8–C11 **100 nF** | C19–C22 **10 nF** | C28 100 n, C37 1 µF — **no bulk** | 1.54 µF |
| `+4V5` (boost out) | — | — | C32/C33 10 µF, C34/C36 1 µF | 22.0 µF |
| `+3V3_SYS` (boost in) | — | — | C31 10 µF, C29 1 µF, C38/C40 100 n | 11.2 µF |

> **`vdd_pix`'s primary is 100 nF, not 1 µF** — deliberately, to stay inside the capacitance
> budget the shutdown ordering depends on (above). It is the one rail that breaks the pattern.

`C39` (10 n) and `C41` (220 n) are **not decoupling** — they are the `EN_PIX` and `EN_BOOST`
timing capacitors in the sequencing network.

**The 10 nF bank is not optional.** There was originally **no capacitor anywhere on this board
below 100 nF** — while the sensor's **LVDS drivers run off `vdd_18` and toggle at 360 MHz**. A
1 µF X7R 0402 self-resonates at 3–6 MHz and is *inductive* above that. Compounding it: **both
inner layers are ground (§11.2) — there is no power plane**; and the sensor is **socketed**,
adding ~1–3 nH per contact.

#### VBSEL — verified, and clean

`R10`/`R11` (1 k to `+3V3_SYS`) strap `VBSEL_A`/`VBSEL_B` HIGH → **bank 13 VCCO = 2.5 V**. Not
optional (§3).

They land on **TPS2116 `PR1` pins** — analog comparator inputs with a ~1.0 V internal reference,
which is why Alchitry spec a *band* ("low = 0–0.9 V, high = 1.1–3.3 V") rather than a CMOS
level. The Pt already loads them with **10 kΩ**, so our 1 kΩ gives **3.0 V** — comfortably
inside the high band.

**Nothing else in the stack drives them.** The **Hd**, **Ft+** and **Br** control headers are
pure pass-throughs with **zero components on any control pin** — verified from their schematics.
The only unverified link is the **`Sp` spacer** (no published schematic). Almost certainly a bare
pass-through, but **worth 30 seconds with a multimeter** before trusting it.

> Note the Pt V2 being a "3.3 V board" refers to its **power input and default I/O**. Its **32
> triple-voltage pins (bank 13)** genuinely do support 2.5 V — that is what VBSEL selects, and
> Vivado independently places `LVDS_25` + `DIFF_TERM` there with VCCO = 2.50 V (§13). The LVDS
> plan is unaffected by any of this.

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

> **⚠️ Read this table as history.** The first two rows describe a comparison that led to the
> **ferrite-split topology, which is now dead** (§6.5, §14.6). They are kept because the
> *diagnosis* was right — `vdd_pix` genuinely needed dedicated attention — even though the
> *remedy* was later replaced by a better one. **Row 3 is still current.**

| Change | Why | Status |
|---|---|---|
| **Ferrite bead on `vdd_pix`** | Their board beads `VPIX`, `VDDA` and `VADC` (BLM18). We had **nothing** on the pixel supply — the most noise-sensitive rail on the sensor. This was a genuine omission. | ❌ **Superseded.** `vdd_pix` now has its **own TPS7A2033 LDO** (`U5`) with 75–95 dB PSRR — strictly better than a bead. No ferrite on the board. |
| **The whole topology** | They feed **one ultra-low-noise LDO** into the sensitive rails and **split them with ferrites**, with the LDO's output cap **before** the beads. | ❌ **Superseded** by boost → 3 LDOs → 2 supervisors. The `vdd_pix` power-down problem is instead eliminated by U5's 150 Ω auto-discharge plus the `EN_PIX` interlock. |
| **Per-pin decoupling 100 nF → 1 µF** | Their primary decoupler is **1 µF**, not 100 nF, backed by 0.1 µF. A modern 1 µF X7R in 0402 has similar ESL to a 100 nF but 10× the capacitance, so it holds supply impedance down across a wider band. | ✅ **Current** for `vdd_33` and `vdd_18`. **`vdd_pix` is the deliberate exception** — 100 nF per pin, to stay inside its capacitance budget (§6.5). |

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
  is at mid-left. (We now order the `-QTI` part, which has a **peel-off protective foil**; its
  tab points toward pin 1, so it doubles as an orientation aid. Peel the foil before installing
  the sensor. The originally-specified `-QDI` — no foil — is discontinued.)
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
| Layout | Routed and statically reviewed (§14). **KiCad DRC has NOT been run** — see §14.0 |

---

## 14. Layout review — findings against the routed board

Reviewed **2026-07-18** against `LauPythonCamera_Pt_Stack.kicad_pcb` at commit `2bc854b`.
Three independent passes: LVDS routing geometry, power/decoupling, placement/mechanical.

### 14.0 ⚠️ What this review is, and is not

**This is static geometric analysis of the `.kicad_pcb` s-expressions — not a DRC run.**
KiCad was not installed on the machine that produced it, so `kicad-cli pcb drc` was never
executed. Items 14.2.3 and 14.2.4 below are *predicted* DRC violations and must be confirmed
in KiCad before anyone acts on them. Nothing here supersedes an actual DRC pass.

Coordinates are **KiCad page coordinates**. Subtract (100, 60) for board-relative.

### 14.1 Tier 1 — fix before fab

**1. Five of U1's eleven supply pins have no usable decoupling.**
Found independently by two passes. Every `vdd_33` / `vdd_18` cap sits west (x ≈ 120–123) or
north (y ≈ 67.5–69.4) of the socket. The east-column pins are naked:

| Pin | Rail | Nearest cap | Routed distance |
|---|---|---|---|
| U1.19 | `+3V3_CAM` | C4 (1 µF) | **32.1 mm** |
| U1.22 | `+1V8_CAM` | C7 (1 µF) | **25.2 mm** |
| U1.26 | `+1V8_CAM` | C7 (1 µF) | **28.0 mm** |
| U1.29 | `+3V3_CAM` | C4 (1 µF) | **29.5 mm** |
| U1.36 | `+3V3_CAM` | C4 (1 µF) | **25.4 mm** |

Only the four `vdd_pix` pins (C19–C22, 2.75–2.90 mm) actually satisfy §11.5's "decoupling caps
hard against their supply pins. **All 11 of them.**"

At 25 mm of 0.5 mm trace there is ~20 nH of loop inductance ahead of the cap — a 10 nF at that
distance is decoration. This bites hardest on `+1V8_CAM`, which per §6.5 is what the **360 MHz
LVDS drivers** run from, and pins 22 and 26 are two of the three `vdd_18` pins. Compounded by
the two facts §6.5 already flags: there is **no power plane** (both inner layers are GND,
§11.2.1), and the sensor is **socketed** (~1–3 nH per contact).

**Fix:** the strip east of U1 (x 146–154.5) is clear F.Cu pour. Place one 1 µF + one 10 nF pair
within ~1.5 mm of each of pins 19, 22, 26, 29, and one pair north of pin 36. B.Cu directly under
those pins is also completely free. **This is the single highest-value change on the board.**

**2. Almost no ground stitching in the power section.**
Exactly **one** GND via exists at x < 112. U3 (the boost) sits at (107, 73).

| Pad | Nearest GND via |
|---|---|
| `U3.4` (boost GND) | **10.80 mm** |
| `C31` (10 µF boost input) | **15.13 mm** |
| `U2.2` | 10.74 mm |
| `C34` | 11.15 mm |

The boost's high-di/dt return loop is confined to the F.Cu/B.Cu pours with no path into the two
inner planes for over a centimetre. **Fix:** cluster 4 stitching vias within 1 mm of `U3.4`, and
put vias directly on the GND pads of C31–C34.

Related: U1's own GND pads are 2.6–6.6 mm from the nearest stitch. For a *socketed* sensor
already carrying 1–3 nH per contact, add a via beside each U1 GND pad — cheap, and directly
under the part.

**3. The whole board is powered through 0.2 mm trace and a single via.**
`+3V3_SYS` daisy-chains J3 pins 1–15 odd along y = 65.355 on **0.2 mm B.Cu**, then rises into
**one via at (111.70, 69.90)**. Cut analysis confirms removing it isolates `L1.1`, `U3.3` and
`U2.1` — everything.

Current: boost input ≈ 234 mA + 80 mA for U2 ≈ **320 mA average**, plus boost peak inductor
current, against ~0.5 A for 0.2 mm on 1 oz outer copper. 64 % loaded, no redundancy, and the
last daisy-chain segment carries the full aggregate.

**Fix:** widen the riser and aggregation run to 0.5–0.8 mm, place 2–3 parallel vias at
(111.70, 69.90), and **fan** the eight J3 power pins into a short bus rather than chaining them.

### 14.2 Tier 2 — cheap and real

**1. GND stitch vias at the LVDS layer transitions.**
Both passes agree to within 0.1 mm. Distance from each pair's signal vias to the nearest GND via:

| Pair | Transition | Nearest GND via |
|---|---|---|
| `CAM_LVDSCLK` | (148.01, 84.84) | **8.4 / 9.1 mm** |
| `CAM_D2` | (141.60, 98.80) | 4.3 / 4.8 mm |
| `CAM_SYNC` | (144.00, 98.80) | 2.7 / 3.4 mm |
| `CAM_D0` | (139.20, 98.80) | 1.9 / 2.6 mm |
| `CAM_D3` | (139.05, 93.75) | 1.7 / 0.85 mm |
| `CAM_CLKOUT` | (130.92, 93.75) | 0.80 / 0.83 mm ✓ |
| `CAM_D1` | (134.98, 93.75) | 0.82 / 0.87 mm ✓ |

This is rule 2 of the four "rules for a human" in the `.kicad_dru` that DRC cannot enforce.

**Calibrate the severity honestly:** return current for a *differential* signal hops mostly
between the P and N barrels, which sit 0.68–1.02 mm apart at the same spot. So this is primarily
a **common-mode / radiated-emissions** problem, not a differential-eye problem. Stitch vias are
free, though. D0/D2/SYNC all cross at y = 98.800 with 1.72 mm between barrels — four vias at
x ≈ 138.1, 140.4, 142.8, 145.1 cover all three. D3 needs one near (140.2, 94.4). LVDSCLK has
nothing within 8 mm and needs two placed deliberately.

**2. Two single vias carry a whole rail each.** Via (124.42, 89.20) is the sole feed to all four
`+3V3_CAM` sensor pins (140 mA through one 0.4 mm drill); via (109.67, 73.20) is the sole feed
to both LDOs off `+4V5` (~150 mA through one 0.3 mm drill). DC current density is fine — a
0.4 mm via handles ~1 A — so this is single-point-of-failure and AC impedance, not heating.
Double both up.

**3. 1.84 mm of fully uncoupled run on CLKOUT, D1, D3 — exceeds the 1.5 mm DRU limit.**
These three drop straight down from the U1 pads at 1.016 mm pitch (gap 0.776 mm) and only
converge at the via. Zdiff rises to ~126 Ω over that stretch. D0, D2 and SYNC already do this
correctly, converging to 0.44 mm pitch within ~1.06 mm of the pad — **apply the same fanout to
the other three and the violation disappears.**

**4. Via barrel gap 0.18 mm on D0, D2, SYNC vs `diff_pair_via_gap` 0.25 mm.** 0.680 mm via
spacing with 0.5 mm pads. Predicted DRC violation, and tight for a 0.5 mm via at JLC. Open to
0.75 mm spacing.

**5. Boost input loop is long and thin.** `C31 → L1.1` is **19.08 mm**, 10.5 mm of it at 0.2 mm
width, with C31 at (103.5, 73.95) on the far side of U3 from L1 at (112.28, 76.15). The input
cap should form a tight loop with the inductor and the IC's VIN/GND. Move C31 adjacent to the
L1/VIN node and connect at 0.5 mm.

### 14.3 Tier 3 — polish

- **`R2` is topologically a stub tee, not an end-of-line termination.** Both clock nets form a
  3-way node at their transition via, so the clock is terminated *before* the sensor, leaving
  2.7–4.5 mm unterminated stubs. Position passes (R2.2 is 0.14 mm from U1.23), but §11.5's
  actual requirement is about topology. Magnitude is ~12–30 ps against a ~1 ns edge — **it will
  work.** But R2 is also the only discrete on the mating side; moving it to the empty F.Cu at
  (147.6–148.6, 82.5–83.5) fixes the topology *and* makes the bottom side 100 % connectors.
- **Silk refdes were never nudged off pads.** Worst: `U1`'s own designator overlaps socket pads
  36/37 by 0.64 mm; `R1` sits over U1.28–30; `R14` over U1.25–27. Most fabs auto-clip, so the
  practical result is fragmented designators — but not on the socket pads.
- **J1/J2/J3 refdes are hidden**, leaving two physically identical DF40C-80DP connectors
  unlabeled on the blind side. B_Silkscreen has 101 coordinate points vs 4341 on F.
- **No test points anywhere** — including `+3V3_PIX`, the rail whose 3.25–3.35 V window §6.5
  says to measure the day the board arrives. `+1V8_CAM` and `IBIAS_MASTER` have zero vias, so
  there is not even an accidental probe target. Recommend 5 rails + GND; x 0–3, y 30–45 is empty.
- **U1 index hole is 0.317 mm from the `+3V3_PIX` via** at (142.625, 73.825). NPTH tolerance is
  ±0.1 mm with no annular ring — right at the fab's 0.3 mm floor. Shift the via ~0.3 mm.
- **R6 ↔ U5 courtyards are 0.030 mm apart** — inside pick-and-place repeatability. Nudge R6
  (and R5, for symmetry) down 0.2–0.3 mm.
- **Zones `poly8` (81 mm²), `poly4` (48 mm²) and B.Cu `poly1` (39 mm²)** are each large flaps of
  copper on a single tie. Add 2–3 stitches to each.

### 14.4 The DRU skew rule is too tight to be useful

At 600 Mbps the UI is 1667 ps and tpd is ~5.4 ps/mm, so a strict 2 %-UI intra-pair budget is
**~6 mm** of length mismatch. The `.kicad_dru` value of **0.08 mm is 0.43 ps — 0.03 % of a UI.**

Measured intra-pair skew: `CAM_CLKOUT`, `D0`, `D1`, `D2`, `D3`, `SYNC` are all **exactly
0.000 mm**. `CAM_LVDSCLK` is 0.137 mm — which breaks the DRU rule and will raise a DRC warning
for a **0.74 ps** difference that cannot matter.

**Recommendation: relax the seven skew rules to ~1 mm** so that real violations are not lost in
noise. The rules were inherited from the MIPI board; the reasoning in §11.4 for constraining
intra-pair and not inter-lane remains correct — only the numeric value is miscalibrated.

### 14.5 Verified correct — do not "fix" these

- **Trace geometry:** 0.24 mm / 0.20 mm edge-to-edge over the entire routed length of all 14
  LVDS nets (median gap exactly 0.200), matching §11.3 and the `CamLVDS` netclass.
- **Reference planes:** In1.Cu and In2.Cu are each a single GND fill of 2115 mm² with **zero**
  non-GND copper. The only plane discontinuity any LVDS trace crosses is its own via antipad.
- **P/N via counts match 1:1 on every pair.** One layer change each, as §11.2.1 intends.
- **Inter-pair fan is clean:** the six sensor-output nets step monotonically 10.714 → 14.873 mm
  in exact 0.83 mm increments. Deliberately unmatched, per §11.4 — correct.
- **LVDS vs the switcher (§11.5):** minimum distance from any pair to L1's body or the `SW` node
  is **20.73 mm**. The "regulators left of x = 24" rule from §8.2 was followed and it worked.
- **The B.Cu GND pour does not load the pairs.** Checked specifically because 0.25 mm ≈ 1.2× the
  dielectric height looked like a coplanar risk: a same-mesh 2D FD solve says the pour moves
  Zdiff by only ~1.5 %. The copper is too thin for edge coupling to compete with the plane
  0.21 mm below. **Leave the pour alone.**
- **The §8.1 notch decision was executed exactly.** Socket copper spans x 124.824–147.176,
  giving **2.324 mm** to the notch — matching the predicted 2.32 mm. The x = 38 alternative that
  would have given 0.32 mm was correctly rejected.
- **Edge clearance:** minimum copper-to-`Edge.Cuts` anywhere is 0.525 mm against a 0.3 mm target.
- **Series elements are intact:** `SW` contains exactly `L1.2` and `U3.5` — no zone, no stray
  trace. No LDO output is shorted back to its input anywhere in copper.
- **No courtyard overlaps, no islanded zones, no unrouted nets.** The ~150
  `unconnected-(J1/J2/J3-…)` entries are deliberately unused connector pins.
- **Pin-1 markers are present** on U1, J1–J3 and U2–U7. (L1 has none — correct, it is non-polar.)

### 14.6 Documentation drift found during review — fixed 2026-07-18, all closed

These were **doc bugs, not layout bugs**, but a reader following them would have ordered the
wrong parts. All were reconciled against **`CAMERA_POWER_DESIGN.md`** (authoritative), the
schematic, `production/LauPythonCamera_Pt_Stack_bom.csv`, and the routed copper. The list is
kept as a record of what was wrong and why.

1. ✅ **§6.5's prose described a dead design.** It marked the tree OBSOLETE, then the prose
   beneath it ("`U3` is a SWITCH, not a regulator", "`FB1`/`FB2` are what give the sensor supply
   rejection", the `FB1` DCR ≤ 50 mΩ spec) described exactly that dead design. **There is no FB1
   or FB2 on this board.** *Fixed:* the load-switch/ferrite prose is replaced with the boost +
   3-LDO + 2-supervisor description, and the banner now warns that any surviving reference to a
   "load switch" or ferrite is stale.
2. 🔴 **The tree diagram carried the WRONG feedback divider** — `R8`/`R9` as **649 k/100 k**.
   The board and the BOM both use **330 k/51 k**. This is the most consequential of the seven:
   649 k/100 k is the pair `CAMERA_POWER_DESIGN.md` §4 explicitly **rejected**, because 649 kΩ is
   a JLCPCB *Extended* part. Electrically the two are 12 mV apart and interchangeable; the choice
   was about sourcing. *Fixed,* with the reasoning recorded so it is not "tidied" back.
3. ✅ **The old §6.5 "HONEST COST" section was wrong under the current design.** It claimed
   `vdd_pix`'s tolerance *is* the Pt's `+3V3` tolerance and that the rail could sit ~16 mV
   outside its window at both extremes. True of the tap-through-ferrites design; **false now** —
   `U5` regulates `vdd_pix` independently to 3.2505–3.3495 V. *Fixed,* and the obsolete action
   item ("measure the Pt's 3.3 V rail the day the board arrives") is marked as no longer
   load-bearing.
4. ✅ **§6.4.1's refdes table was off by one.** On the board the pulls are **R12 = `mosi`,
   R13 = `sck`, R14 = `clk_pll`**. **`R15` is not a sensor pull at all** — it is the 100 kΩ
   `EN_PIX` pull-up to `+3V3_CAM`, i.e. the sequencing interlock. *Fixed,* with a warning not to
   repurpose R15.
5. ✅ **Open item 3** inherited the stale load-switch/ferrite spec as an ordering blocker.
   *Closed* — every part is a JLCPCB part number in the BOM.
6. ✅ **Open item 5** was already implemented in `LauPythonCamera_Pt_Stack.kicad_dru`. *Closed.*
7. ✅ **The decoupling table did not match copper.** It claimed "11 × 1 µF, one per supply pin";
   in fact `vdd_pix`'s four pins use **100 nF**, deliberately, to stay inside the capacitance
   budget the shutdown ordering depends on. The "11 × 10 nF" claim was correct (C12–C22).
   *Fixed:* replaced with a per-rail table built from the routed nets. Also recorded that
   `+3V3_PIX` measures **1.540 µF** against its own "≤ ~1.5 µF" budget — accepted, since
   τ = 231 µs is still far inside the 10 µs requirement, but now a decision rather than a drift.

**`C25` — ✅ RESOLVED, and the gap is deliberate. Do not "restore" it.**

The numbering jumps C24 → C26 in the layout, the BOM and the schematic. `C25` **was a 10 µF
0805 bulk capacitor on `+3V3_PIX`** — it is still visible in `LauPythonCamera_Pt_Stack.kicad_sch.bak`
(`Device:C`, value `10u`, footprint `C_0805_2012Metric`, between `+3V3_PIX` and `GND`). It was
**deliberately deleted**, and its designator was never reused.

**That deletion is exactly the fix §6.5 demands:** `vdd_pix` must carry **no bulk cap**, because
power-down depends on U5's 150 Ω auto-discharge collapsing the rail first. A 10 µF part there
would have taken τ from 231 µs to ~1.7 ms and **silently broken the shutdown ordering** — the
precise failure §6.5 warns about.

> **The missing designator is the fingerprint of a bug that was found and fixed.** If a future
> BOM audit flags "C25 is missing", the correct response is to add a note, not a capacitor.

*(The single `"C25"` string remaining in the schematic is unrelated: it is a **pin name** on the
DF40C-50DP symbol, whose 50 pins are named `C1`–`C50`. There is no component instance `C25`.)*

---

## Open items

| # | Item | Blocks | Owner |
|---|---|---|---|
| **1** | **🔴 ORDER THE SENSOR AND SOCKET.** Order **`NOIP1SN1300A-QTI`** — the originally-specified `-QDI` is **discontinued**; `-QTI` is the same sensor with a peel-off foil (§4, §12). Expect a **~27-week factory lead**; if distributor stock runs out the board arrives and sits on a bench for months. This is the only genuinely time-critical item in the project and it is *not* blocked on layout. | Nothing — do it now | **You** |
| 2 | **Socket variant: `-0` or `-1`?** The `-1`'s index pins are what key the socket's rotation, but they protrude ~1.66 mm against a 1.6 mm board. The footprint includes both Ø1.6 holes, so `-1` stays available. | Item 1 | Andon (one email), or default to `-0` |
| ~~3~~ | ✅ **CLOSED.** Superseded by the boost + 3-LDO + 2-supervisor tree (§6.5). There is no load switch and no ferrite: `U3` = `TPS61023DRLR`, `U4`/`U5` = `TPS7A2033PDBVR`, `U2` = `TPS7A2018PDBVR`, `U6`/`U7` = `TLV803SDBZT`. **Every part is a JLCPCB part number in `production/LauPythonCamera_Pt_Stack_bom.csv`** — the BOM is orderable. | — | — |
| 4 | **PCB-surface-to-sensor-glass height.** Not published anywhere — not in Andon's catalog, not in the Eagle library. Sets the lens flange focal distance. | Lens mount (not this board) | Measure the physical socket |
| ~~5~~ | ✅ **CLOSED.** The area-scoped exceptions exist in `LauPythonCamera_Pt_Stack.kicad_dru` — rules *"DF40 land pattern — 0.4mm pitch, intra-connector only"* and *"TPS61023 SOT-563 land pattern"*. They relax clearance **only between pads of the same component**; the global minimum and the LVDS rules are untouched. | — | — |
| 6 | **Impedance geometry is an IPC-2141 approximation (±10%)**, not a field solver. Confirm in KiCad's stackup calculator and **tick "impedance controlled" when ordering** so JLC verifies it. | Signal integrity | At layout |
| ~~7~~ | ✅ **CLOSED.** Stale — it described the dead RC-cascade design. **Power-down IS sequenced**, by `U6`/`U7` asserting immediately below 2.93 V: `vdd_pix` collapses first via U5's 150 Ω auto-discharge, `vdd_33` follows when the boost disconnects at t ≈ 493 µs, `vdd_18` dies last. Both directions are **SPICE-verified** (`CAMERA_POWER_DESIGN.md` §7.5). No sequencer IC needed. | — | — |
| 8 | Ft+ and Hd current draw — not documented by Alchitry | Power budget | Measure or ask Alchitry |
| 9 | Pt V2's onboard USB2 FIFO signals (`USB_RD`/`USB_WR`/`USB_SIWU`) sit in **bank 13**; setting it to 2.5 V changes their drive level. Appears safe and deliberate, but undocumented. | Nothing (we use the Ft+ for bulk data) | Confirm with Alchitry if the onboard FIFO is ever used |
| 10 | **AND9362/D — PYTHON Developer's Guide** is NDA-gated on the onsemi Image Sensor Portal. It holds the trigger→integration latency, jitter, FOT/ROT clock counts, and the `trigger1`/`trigger2` definitions — **none of which are in the public datasheet**. | Tight trigger synchronisation | Request portal access |

| **11** | **🔴 Apply the §14 Tier 1 layout fixes on a machine with KiCad, then run `kicad-cli pcb drc`.** Three items: local decoupling for U1 pins 19/22/26/29/36; GND stitching around U3 and C31–C34; widen the `+3V3_SYS` entry and multiply its via. §14 was produced by static geometric analysis **with no DRC run** — confirm the predicted violations (§14.2.3, §14.2.4) in KiCad before acting. Regenerate `production/` afterwards. | Fab | **You / a KiCad PC** |

**Closed:** socket land pattern (§12) · bank-13 pin map (§5.1) · P/N polarity (§13.1) ·
stack compatibility (§13.2) · regulators + sequencing (§6.5) · DF40 connectors (§6.6) ·
power part numbers (old item 3) · fine-pitch DRC exceptions (old item 5) ·
power-section documentation drift (§14.6)

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
