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

| Sensor signal | Sensor pins (N/P) | Dir (FPGA) | Elem pins (N, P) | FPGA pins (N, P) | Clock cap. |
|---|---|---|---|---|---|
| `lvds_clock_in±` | 23 / 24 | **OUT** | **B39, B41** | **W12, W11** | MRCC |
| `clock_out±` | 7 / 8 | IN | **B45, B47** | **V14, V13** | **MRCC** — forwarded bit clock |
| `sync±` | 17 / 18 | IN | B40, B42 | Y12, Y11 | SRCC |
| `doutn0 / doutp0` | 9 / 10 | IN | B46, B48 | V15, U15 | SRCC |
| `doutn1 / doutp1` | 11 / 12 | IN | B51, B53 | W10, V10 | — |
| `doutn2 / doutp2` | 13 / 14 | IN | B52, B54 | AB17, AB16 | — |
| `doutn3 / doutp3` | 15 / 16 | IN | B57, B59 | AB12, AB11 | — |

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
Top Bank A is hardwired 3.3 V and entirely free (the Ft+ and Hd are on the *bottom*).

`IOSTANDARD LVCMOS33`.

| Sensor signal | Sensor pin | Dir (FPGA) | Elem pin |
|---|---|---|---|
| `mosi` | 2 | OUT | A3 |
| `miso` | 3 | IN | A4 |
| `sck` | 4 | OUT | A5 |
| `ss_n` | 47 | OUT | A6 |
| `reset_n` | 46 | OUT | A9 |
| `clk_pll` | 25 | OUT | A10 *(unused in this clocking scheme; routed anyway)* |
| `trigger0` | 41 | OUT | A11 |
| `trigger1` | 42 | OUT | A12 |
| `trigger2` | 43 | OUT | A15 |
| `monitor0` | 44 | IN | A16 |
| `monitor1` | 45 | IN | A17 |

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

## Open items

| # | Item | Blocks | Owner |
|---|---|---|---|
| 1 | **Andon socket land pattern.** The public catalogs give pitch (1.016 mm), 12 pads/side, and an 11.18 mm span between outermost pad centers — but **never dimension the centerline-to-pad-row offset, the index-hole positions, or the body keepout.** Cannot author a footprint without them. | **Layout** | Request full `680-48-SM-G10-R14-1` drawing from Andon |
| 2 | **PCB-surface-to-sensor-glass height.** Not published for any Andon socket. Sets the lens flange focal distance. | **Lens mount** | Andon |
| 3 | Index-pin protrusion (~1.66 mm) vs. 1.6 mm board thickness | Board stackup / socket variant choice | Andon |
| 4 | Bank-13 element-bus → FPGA pin map with P/N polarity | §5 pin plan | In progress |
| 5 | Ft+ and Hd current draw — not documented by Alchitry | Power budget | Measure or ask Alchitry |
| 6 | Pt V2's onboard USB2 FIFO signals (`USB_RD`/`USB_WR`/`USB_SIWU`) sit in **bank 13**; setting it to 2.5 V changes their drive level. Appears safe and deliberate, but undocumented. | Nothing (we use the Ft+ for bulk data) | Confirm with Alchitry if the onboard FIFO is ever used |

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
