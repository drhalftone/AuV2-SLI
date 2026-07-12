# LauPythonCamera_Pt_Stack — Design Blueprint

Custom Alchitry **element board** carrying an onsemi **PYTHON 1300** global-shutter image
sensor in a **socketed 48-pin LCC**, for the AuV2-SLI structured-light system.

Status: **design blueprint / not yet laid out.** See [Open items](#open-items) — the socket
land pattern is still blocked on a vendor drawing.

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

**All seven pairs are on the DF40's ODD row.** This is not cosmetic — see §5.1.1.

| Sensor signal | Sensor pins (N/P) | Dir (FPGA) | Elem pins (N, P) | FPGA pins (N, P) | Clock cap. |
|---|---|---|---|---|---|
| `clock_out±` | 7 / 8 | IN | **B39, B41** | **W12, W11** | **MRCC** — forwarded bit clock |
| `doutn0 / doutp0` | 9 / 10 | IN | B45, B47 | V14, V13 | (spare MRCC) |
| `doutn1 / doutp1` | 11 / 12 | IN | B51, B53 | W10, V10 | — |
| `doutn2 / doutp2` | 13 / 14 | IN | B57, B59 | AB12, AB11 | — |
| `doutn3 / doutp3` | 15 / 16 | IN | B63, B65 | AA11, AA10 | — |
| `sync±` | 17 / 18 | IN | B69, B71 | AB13, AA13 | — |
| `lvds_clock_in±` | 23 / 24 | **OUT** | B75, B77 | AA14, Y13 | — |

Spare: `(B33,B35)` on the odd row, plus all 8 even-row pairs.

#### 5.1.1 Why all-odd-row, and why THIS order

**The DF40's two rows escape in opposite directions.** Measured from the footprint used on the
existing boards (`Hirose_DF40C-80DP-0.4V_2x40-1MP_P0.4mm`): **odd pins sit at y = +1.355 mm,
even pins at y = −1.355 mm**, 40 each, aligned in X on 0.4 mm pitch. A pair on the even row
would escape out the *far* side of the connector, away from the sensor — forcing it to loop
around the connector body or via down. Both are bad, and the second trips the `.kicad_dru`
via warning.

Alchitry's pairs are always (odd, odd+2) or (even, even+2), so **both halves of a pair are
always in the same row.** Bank 13 has exactly **8 odd-row and 8 even-row pairs**; we need 7.
And **both MRCC pairs — (B39,B41) and (B45,B47) — are odd-row.** So all seven fit on the odd
row *and* the forwarded clock still lands on an MRCC.

**The order matters too.** The sensor's LVDS pins are contiguous around the perimeter:

```
side "B" of the LCC, 12 pins, no power interleaved:
  7/8  clock_out | 9/10 dout0 | 11/12 dout1 | 13/14 dout2 | 15/16 dout3 | 17/18 sync
  [corner]
  23/24 lvds_clock_in     <- adjacent side, just around the corner
```

The table above assigns connector pairs in that same sequence, so **nothing crosses**.
`lvds_clock_in` sits at the end of the run, which is where it wants to be — it comes around
the corner from the adjacent sensor edge.

**Polarity falls out for free.** On the sensor, N is always the lower pin number
(7 = `clock_outn`, 8 = `clock_outp`). On the connector, N is also the lower element-bus pin.
**Orient U1 so pin 7 faces the B39 end** and every pair runs N-to-N, P-to-P with no intra-pair
swap.

**Fan-in geometry.** Sensor pair centres are 2.032 mm apart (2 × 1.016 mm pitch); connector
pair centres are 1.2 mm apart. The bundle tapers from ~11.2 mm wide at the socket to ~6.4 mm
at the connector. Gentle and symmetric — keep both legs of a pair bending together and the
intra-pair skew budget survives.

> **Neck-down at the connector.** DF40 pads are 0.4 mm pitch, so traces must narrow below the
> 0.24 mm `CamLVDS` width to enter them. That is normal and acceptable over a short run, but
> it will trip `track_width (min 0.22mm)` in the `.kicad_dru`. Add an **area-scoped exception**
> around the connector rather than lowering the global minimum.

> **POLARITY RULE — in every pair, the LOWER element-bus pin number is N, the HIGHER is P.**
> No exceptions in bank 13. P/N is fixed by the FPGA die and **cannot be swapped in layout.**
>
> Verified against the Xilinx package file `xc7a100tfgg484pkg.csv` — all 16 bank-13 pairs
> resolve to genuine `IO_L##P/N_T#_13` pairs with matching polarity. Note two pairs are
> *diagonal* BGA neighbors — (B33,B35) = AB10/AA9 and (B75,B77) = AA14/Y13 — which look
> wrong but are correct. Do not infer polarity from package-pin adjacency.

`clock_out±` is on an **MRCC** pair so it can drive `BUFIO`/`BUFR` and clock the `ISERDES`
on any bank-13 pin (bank 13 is a single HR bank = one clock region).

**9 spare pairs remain.** Avoid **(B34,B36)** — B36 is the Alchitry `Fn` fan-control pin.
(B70,B72) is usable; B70 is the bank VREF pin, irrelevant for LVDS_25.

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

### 5.5 Pre-fab safety net

Before releasing copper, drop the §5.1 pins into an XDC with `IOSTANDARD LVDS_25` +
`DIFF_TERM TRUE` and run `synth_design` / `report_io`. **Vivado hard-errors on a non-pair or
a reversed P/N** — a 2-minute check that independently confirms this table against Vivado's
own device database. Do this. It is the cheapest insurance in the project.

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

### 6.2 Local regulation — the element connectors have no 2.5 V or 1.8 V

The element connectors expose **only `+3.3V` and `VDD` (5-12 V board input)**. There is **no
2.5 V and no 1.8 V rail** on any element connector — the Pt's internal 2.5 V/500 mA rail is
bank 13's VCCO and is not brought out.

**This board generates the sensor's supplies locally**, from `+3.3V` or `VDD`:

| Rail | Sensor pins | Spec | Notes |
|---|---|---|---|
| `vdd_33` | 1, 19, 29, 36 | 3.3 V | ~140 mA |
| `vdd_18` | 6, 22, 26 | **1.8 V** | ~80 mA — **must be generated on this board** |
| `vdd_pix` | 31, 33, 38, 40 | 3.3 V, **3.25-3.35 V** | tight tolerance; low current (~5 mA) but clean |

**Power-up sequencing is mandatory:** `vdd_18` → `vdd_33` → `vdd_pix`, each >10 µs apart.
Power-down is the reverse. Sequence this on-board; do not assume the stack does it.

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

### 6.5 The regulator chain, as implemented

```
  C header  +3.3V ──┬──> U2  AP2112K-1.8  LDO   ──> +1V8_CAM   (vdd_18,  ~80 mA)
                    └──> U3  load switch  3V3   ──> +3V3_CAM   (vdd_33, ~140 mA)
  C header  VDD ───────> U4  LDO 3V3 ±1%        ──> +3V3_PIX   (vdd_pix,  ~5 mA)
```

Three decisions worth understanding, because each looks odd in isolation:

**`vdd_pix` gets its own ±1% LDO fed from `VDD`, not from the 3.3 V rail.** The sensor
demands **3.25–3.35 V** on this pin. A ±2% tolerance on a nominal 3.3 V rail is 3.23–3.37 V —
**already outside that window** — so tapping the Pt's 3.3 V rail cannot meet spec, and you
cannot LDO 3.3 V down to 3.3 V (no headroom). It has to come from `VDD`. Its current is only
~5 mA, so even at `VDD` = 12 V the dissipation is <90 mW — no thermal issue.

> **Specify U4 as ±1% or better.** A garden-variety ±2% 3.3 V LDO does **not** meet the
> `vdd_pix` window. This is the one part on the board where the tolerance line in the
> datasheet actually bites.

**`vdd_33` gets a LOAD SWITCH, not a regulator.** It's already 3.3 V, so nothing needs
regulating — but it needs an **enable**, because a straight rail tap comes up whenever the
board does, which would violate the power-up order. The switch exists purely so `vdd_33` can
be *sequenced*.

**Sequencing is an RC cascade** (`R8`–`R10`, `C15`–`C17`). Each stage's output enables the
next:

```
  +3V3_SYS ─R8─┬─> U2.EN     (vdd_18 comes up first)
               C15
  +1V8_CAM ─R9─┬─> U3.EN     (vdd_33 follows)
               C16
  +3V3_CAM ─R10┬─> U4.EN     (vdd_pix last)
               C17
```

That yields **`vdd_18` → `vdd_33` → `vdd_pix`**, the datasheet order, with ~1 ms between
stages — comfortably over the 10 µs minimum. Power-down is not sequenced; if that turns out to
matter, it needs a proper sequencer IC.

**`VBSEL_A` / `VBSEL_B` are strapped HIGH** through `R11`/`R12` (1 k to `+3V3_SYS`). This is
what sets bank 13 to 2.5 V. **Not optional** — see §3.

### 6.6 Connectors

| Ref | Part | Role |
|---|---|---|
| `J1` | `DF40C-80DP-0.4V` | **Bank A** — 11 single-ended control signals (banks 14/35, 3.3 V) |
| `J2` | `DF40C-80DP-0.4V` | **Bank B** — the 7 LVDS pairs (bank 13, 2.5 V), all on the ODD row |
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
| **`U4` → `ADP7158-3.3`** | They used the `ADP7158` (ultra-low-noise, high-PSRR, ±0.8%) for their sensitive rails. It satisfies the `vdd_pix` 3.25–3.35 V window with margin, where a garden-variety ±2% LDO does not. |
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

## 8. Mechanical

- Connectors: **DF40, 1.5 mm stack height** (Pt V2 convention).
- The Pt V2 keeps **no components taller than 1.5 mm** on its underside. This board mates to
  the **top**, so that constraint applies to *our* bottom side if anything is ever stacked
  above — but nothing is, so we are free. Keep the bottom clean anyway.
- Total optical height from this board's surface to the sensor glass is **not yet known** —
  it depends on the socket's internal seating plane, which Andon does not publish. **This
  blocks the lens mount design.** See Open items.

---

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
| `F.Cu` | 0.035 mm (1 oz) | copper | **all LVDS routes here** |
| prepreg | **0.2104 mm** | 7628, Er ≈ 4.4 | LVDS references across this |
| `In1.Cu` | 0.0152 mm (0.5 oz) | copper | **solid GND — never split under a pair** |
| core | 1.065 mm | FR-4 | |
| `In2.Cu` | 0.0152 mm (0.5 oz) | copper | PWR |
| prepreg | 0.2104 mm | 7628 | |
| `B.Cu` | 0.035 mm (1 oz) | copper | slow signals, pours |

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

## Open items

| # | Item | Blocks | Owner |
|---|---|---|---|
| 1 | **PCB-surface-to-sensor-glass height.** Not published anywhere. Sets lens flange focal distance. | Lens mount (not the current board) | Measure on the physical socket, or ask Andon |
| 2 | Index-pin protrusion (~1.66 mm) vs. a 1.6 mm board. Order the **`-0`** (no index pins) or confirm the protrusion with Andon. Note the footprint *does* include the two Ø1.6 holes, so the `-1` remains an option. | Socket variant choice | Andon, or just order `-0` |
| 3 | Neck-down at the 0.4 mm DF40 pads violates `track_width (min 0.22mm)` — needs an **area-scoped DRC exception**, not a lower global minimum. | Clean DRC | Add once the board exists |
| 4 | Local regulators + power sequencing (`vdd_18` → `vdd_33` → `vdd_pix`) | Schematic completion | — |
| 5 | DF40 connectors not yet placed in the schematic (footprints already exist on the earlier boards) | Schematic completion | — |
| 6 | Ft+ and Hd current draw — not documented by Alchitry | Power budget | Measure or ask Alchitry |
| 7 | Pt V2's onboard USB2 FIFO signals (`USB_RD`/`USB_WR`/`USB_SIWU`) sit in **bank 13**; setting it to 2.5 V changes their drive level. Appears safe and deliberate, but undocumented. | Nothing (we use the Ft+ for bulk data) | Confirm with Alchitry if the onboard FIFO is ever used |
| 8 | **AND9362/D — PYTHON Developer's Guide** is NDA-gated on the onsemi Image Sensor Portal. It holds the trigger→integration latency, jitter, FOT/ROT clock counts, and the `trigger1`/`trigger2` definitions — none of which are in the public datasheet. | Tight trigger synchronisation | Request portal access |

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
