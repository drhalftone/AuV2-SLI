# PYTHON 1300 Camera Board — I/O Pin Map

**Status: VERIFIED.** Re-derived independently from the Alchitry Pt V2 connector sheets and
cross-checked against the schematic, the XDC, and the other elements in the stack. **No errors
found — no changes made.**

> **The previous work broke the *power tree*, not the pin map.** The I/O plan in
> `LauPythonCamera_Pt_Stack/README.md` §5 is correct and is reproduced here with an independent
> derivation. Power: see [`CAMERA_POWER_DESIGN.md`](CAMERA_POWER_DESIGN.md).

---

## 1. How this was derived

Parsed the **Pt V2 schematic sheet 3 ("TOP CONNECTORS")** positionally, using Altium's
`PIJ<conn>0<pin>` pin-instance annotations to key each connector pin to the net label on its row.
This is a machine extraction from Alchitry's own drawing, not a transcription.

**Result — the top-side element bus:**

| Pt connector | Element name | Signal pins | Banks present |
|---|---|---|---|
| **J4** | **Bank A** | 36 | 14 (4), 35 (32) |
| **J9** | **Bank B** | 52 | 14 (20), **13 (32)** |
| J3 | Control | — | VBSEL, JTAG, power |

> ### All 32 bank-13 pins are on Bank B (J9), pins 33–78. Bank A has none.
>
> Bank 13 is the **only** bank whose VCCO can be switched to 2.5 V (via `VBSEL_A`, see
> `CAMERA_POWER_DESIGN.md` §2.4), and `LVDS_25` + `DIFF_TERM` **both require VCCO = 2.5 V**.
> Therefore **every LVDS pair must land on Bank B.** This is not a preference.

Each differential pair occupies pins `(n, n+2)` — **same parity, so a pair never straddles the
DF40's two physical rows.** Odd pins are one row, even pins the other.

Bank 13 has **16 pairs**: 8 on the odd row, 8 on the even row.
The even row contains **2 SRCC pairs (L11, L14) and no MRCC**. The odd row holds both MRCC pairs.

---

## 2. Why the EVEN row (and why losing MRCC is fine)

This constraint is **geometric, not electrical**, and it is independent of the power rework:

- The DF40's two rows escape in **opposite directions** (odd pins at y = +1.355 mm, even at
  y = −1.355 mm; each row's SMT tails splay outward).
- The **16.76 mm socket cannot fit below Bank B**, so the sensor must sit *above* it — meaning
  **only the even row faces the sensor.**
- An odd-row pair would have to cross the even row's 0.4 mm-pitch pads (impossible on `B.Cu`) or
  loop around the connector body. Seven pairs looped around a connector at 720 Mbps is not a route
  worth attempting.

**The consequence:** the clock lands on an **SRCC**, not an MRCC. That is fine, and it was checked
with the real receiver rather than argued:

```
  Vivado, iocheck/pt_camera_rx.v (the actual 1:10 LVDS receiver, not a stub):
    BUFIO placed     : 1     <- driven from an SRCC pin
    BUFR  placed     : 1     <- driven from an SRCC pin
    ISERDESE2 placed : 10    <- 5 lanes x master+slave cascade
```

`BUFIO` is the low-skew I/O clock you actually want for a 720 Mbps source-synchronous link, and
**SRCC drives it.** A `BUFG` (which does need MRCC) is the wrong structure here anyway.

---

## 3. LVDS — Bank B (J9), bank 13, VCCO = 2.5 V, all EVEN pins

`IOSTANDARD LVDS_25`. The six **inputs** use internal `DIFF_TERM TRUE` (legal only because bank 13
is at 2.5 V — this saves 7 external 100 Ω resistors). `lvds_clock_in` is an FPGA **output** and
correctly has **no** DIFF_TERM.

| Sensor signal | Sensor pin | Dir (FPGA) | Elem pin | Pt net | FPGA pin | DIFF_TERM |
|---|---|---|---|---|---|---|
| `clock_outn` | 7 | IN | **B40** | `13_L11_SRCC_N` | **Y12** | TRUE |
| `clock_outp` | 8 | IN | **B42** | `13_L11_SRCC_P` | **Y11** | TRUE |
| `doutn0` | 9 | IN | B46 | `13_L14_SRCC_N` | V15 | TRUE |
| `doutp0` | 10 | IN | B48 | `13_L14_SRCC_P` | U15 | TRUE |
| `doutn1` | 11 | IN | B52 | `13_L2_N` | AB17 | TRUE |
| `doutp1` | 12 | IN | B54 | `13_L2_P` | AB16 | TRUE |
| `doutn2` | 13 | IN | B58 | `13_L1_N` | AA16 | TRUE |
| `doutp2` | 14 | IN | B60 | `13_L1_P` | Y16 | TRUE |
| `doutn3` | 15 | IN | B64 | `13_L15_N` | T15 | TRUE |
| `doutp3` | 16 | IN | B66 | `13_L15_P` | T14 | TRUE |
| `syncn` | 17 | IN | B70 | `13_L6_N` | Y14 | TRUE |
| `syncp` | 18 | IN | B72 | `13_L6_P` | W14 | TRUE |
| `lvds_clock_inn` | 23 | **OUT** | B76 | `13_L16_N` | W16 | — |
| `lvds_clock_inp` | 24 | **OUT** | B78 | `13_L16_P` | W15 | — |

**Polarity is straight through.** On the sensor, `N` is the lower pin number (7 = `clock_outn`);
on the connector, `N` is the lower element pin (B40). Every pair runs **N→N, P→P** with no
intra-pair swap. Every pair uses a **single FPGA pair** (both halves share one `L`-number) — no
pair is split across two FPGA pairs.

**Deliberately unused:** `(B34, B36)` = `13_L4`. **`B36` is the Alchitry `Fn` fan-control pin.**
Do not claim it.

**Spare:** all 8 odd-row bank-13 pairs, plus `13_L4` on the even row.

---

## 4. Single-ended control — Bank A (J4), banks 14 / 35, 3.3 V

`IOSTANDARD LVCMOS33`. Eight FPGA outputs, three FPGA inputs.

| Sensor signal | Sensor pin | Dir (FPGA) | Elem pin | Pt net | FPGA pin |
|---|---|---|---|---|---|
| `mosi` | 2 | OUT | A3 | `14_L10_N` | AB22 |
| `miso` | 3 | **IN** | A4 | `14_L17_N` | AB18 |
| `sck` | 4 | OUT | A5 | `14_L10_P` | AB21 |
| `clk_pll` | 25 | OUT | A6 | `14_L17_P` | AA18 |
| `reset_n` | 46 | OUT | A9 | `35_L6_N` | E3 |
| `ss_n` | 47 | OUT | A10 | `35_L22_N` | N2 |
| `trigger0` | 41 | OUT | A11 | `35_L6_P` | F3 |
| `trigger1` | 42 | OUT | A12 | `35_L22_P` | P2 |
| `trigger2` | 43 | OUT | A15 | `35_L16_N` | M2 |
| `monitor0` | 44 | **IN** | A16 | `35_L15_N` | L1 |
| `monitor1` | 45 | **IN** | A17 | `35_L16_P` | M3 |

Using both halves of a diff pair as two independent single-ended I/O (e.g. `35_L16_N`/`_P` for
`trigger2` and `monitor1`) is fine for LVCMOS33.

**Every one of these eight FPGA *outputs* needs a pull resistor** — FPGA user I/O are Hi-Z until
`DONE`, and `vdd_33` is already up during the whole configuration window, so a floating CMOS input
burns crowbar current in the sensor's input buffer. See `CAMERA_POWER_DESIGN.md` §9.4.

---

## 5. Stack collision check — Hd and Ft+

The camera sits **on top**, so its signals pass through the Hd and Ft+. Those boards **consume**
element-bus pins for their own function, and a collision would be invisible in the camera
schematic alone.

Set-intersected the camera's 25 package pins against every other consumer in the stack:

| vs | Pins they use | Collision |
|---|---|---|
| **Hd** | 24 | **NONE** ✓ |
| **Ft+** | 44 | **NONE** ✓ |
| **Pt base** (JTAG/config/etc.) | 12 | **NONE** ✓ |

The camera's 25 pins are disjoint from all three. Hd and Ft+ live in the A–G rows (banks 15/16)
and the MGT area; the camera is entirely in the T/U/V/W/Y/AA/AB rows, columns 11–22, plus a few
bank-35 pins.

---

## 6. What is checked, and what is not

**Checked:**
- Every one of the 25 signals lands on a pin that **actually exists** on the Pt's top connectors
  (machine-extracted from Alchitry's sheet 3).
- All 7 LVDS pairs are on **bank 13**, all on the **even** row, correct **N→N / P→P** polarity,
  each on a **single FPGA pair**.
- All 11 CMOS signals are on **3.3 V banks** (14/35) — none on bank 13.
- The clock is on a **clock-capable (SRCC)** pin.
- `DIFF_TERM` is on the six inputs and **not** on the output pair.
- **No pin collision** with Hd, Ft+, or the Pt base.

**NOT checked:**
- The Hd / Ft+ / Sp **pass-throughs** are assumed straight-through (their sheets do wire the bus
  between top and bottom connectors). **Meter this before first power-up** — see §7.
- `B36` = Alchitry `Fn` fan pin is taken from Alchitry's element convention, not verified from the
  Pt schematic. It is simply left unused, so nothing depends on it.

---

## 7. ⚠️ Connector pin numbering — RESOLVED. Do not re-open this.

**Every element pin number in §3 and §4 depends on this. Read it before you "fix" any of them.**

Alchitry's schematics show their **bottom plugs** (DF40C-*DP) carrying element-bus net `n` on pin
**`n XOR 1`** — odd/even rows swapped — while every **top receptacle** (DF40C-*DS) carries net `n`
on pin `n`. Consistent across the Pt, Hd and Ft+ (**0/186** identity on the plugs, **184/184** on
the receptacles). It looks exactly like our plug pin numbers (B40, B42, A3, A5 …) are all off by
one.

**They are not. That comparison is invalid.**

A schematic pin number is meaningless alone. The chain that matters is:

```
  schematic pin number  ->  FOOTPRINT pad  ->  physical contact
```

Alchitry's plug pin numbers pair with **their own Altium footprint**, whose pad numbering mirrors
the KiCad/Hirose one for the plug. Comparing their plug pin numbers to ours compares two different
footprint libraries and proves nothing.

> ### The empirical proof: `LauCameraTrigger_Alchitry_Stack`
>
> **Fabbed, and works on the Pt.** It puts **`+3V3` on schematic pin 1** of a **DF40C-50DP** plug
> using the **KiCad `Hirose_DF40C-50DP` footprint**. So with that footprint, **plug pin N mates the
> top receptacle's pin N — identity, no swap.**
>
> **This board uses the identical footprints**, extracted straight out of that PCB into
> `LauCamera.pretty`. Same footprint, same pad numbering, same convention.

**So the element pins in §3 and §4 are correct as written** — B40/B42 for `clock_out±`, A3/A5 for
`mosi`/`sck`, and so on, matched against the Pt's **top-receptacle** numbering.

**The rule:** only the **net names** (`A1..A80`, `B1..B80`, `C1..C50`) carry over from Alchitry's
drawings. **Never transfer their plug pin numbers.**

> **What can never reveal this bug:** GND sits on pins 1,2 / 7,8 / 13,14 … — pairs that map to
> *themselves* under an odd/even swap. Ground looks correct either way. Only power and signal pins
> can expose it.

**The 30-second confirmation.** Power the Pt with Hd + Ft+ stacked and meter the Ft+'s exposed top
connector: the **odd** control-header pins 1–15 must read **3.3 V** (not the even ones, which are
`VCC` at 5 V+). That validates the pass-through *and* the pin mapping at once — against a sensor
whose absolute maximum is **4.3 V**.

---

# 8. The Au V2 bring-up path — SPI only, and why

**Status: DERIVED AND CROSS-CHECKED.** Source: Alchitry Labs 2,
`src/main/kotlin/com/alchitry/labs2/hardware/pinout/AuV2Pin.kt` (the same class of source the Pt map
in §1 came from), plus <https://alchitry.com/tutorials/references/pinouts-and-custom-elements/>.

The camera element is a **Pt V2** board. But the *sensor's SPI is asynchronous to its system clock*
(`CAMERA_SENSOR_PROTOCOL.md` §1) — it needs no clock, no PLL, no LVDS, and no configuration. So the
**Au V2 can talk to the sensor over SPI**, and one chip-ID read proves the power tree, the DF40 pin
map, the stack pass-through and our RTL, all at once. That is the whole Au V2 milestone.

## 8.1 The 11 CMOS signals on the Au V2 — all bank 14/35, fixed 3.3 V

Zero collisions with `Au2.xdc` (checked against every LED, UART, TMDS, switch and trigger pin).

| Sensor signal | Element pin | **Au V2 ball** | Bank |
|---|---|---|---|
| `mosi` | A3 | **N6** | 14 |
| `miso` | A4 | **P9** | 14 |
| `sck` | A5 | **M6** | 14 |
| `clk_pll` | A6 | **N9** | 14 |
| `reset_n` | A9 | **J1** | 35 |
| `ss_n` | A10 | **L2** | 35 |
| `trigger0` | A11 | **K1** | 35 |
| `trigger1` | A12 | **L3** | 35 |
| `trigger2` | A15 | **H1** | 35 |
| `monitor0` | A16 | **K2** | 35 |
| `monitor1` | A17 | **H2** | 35 |

**Three independent sources agree on this map:**

1. Alchitry Labs 2, `AuV2Pin.kt` (the primary derivation above).
2. **`Au2.xdc`'s own commented-out Bank-A lines** — the historical pinout, before the Bank-B remap.
   They annotate the very same element numbers: `M6` → *"A5 orientation"*, `N9` → *"A6 Blue"*,
   `K1` → *"A11 Green"*, `L3` → *"A12 Red"*. Four exact hits, from this repo's own history.
3. The Pt-side derivation in §4, via the shared element bus.

Constrained in `constrs_1/imports/RTL/cam_au2.xdc`. The pull directions there **match the board's
external 10 kΩ resistors** (`ss_n` up; `reset_n` and `trigger0–2` down) — they agree with them
rather than fight them. The external resistors are the primary guarantee: they hold through the
whole FPGA configuration window, when the internal pulls do nothing.

> ### ⚠️ The element-pin / FPGA-ball namespace trap — the same class of bug as §7.
> `Au2.xdc` puts HDMI TMDS on FPGA **balls** literally named `A3`, `A4`, `A5`. The camera uses
> **element pins** also named A3, A4, A5. **They are unrelated.** Element A3 → ball **N6**.
> Never carry a pin number across the two namespaces.

## 8.2 ⛔ LVDS CANNOT WORK ON THE Au. This is not fixable in RTL.

The Pt puts all seven pairs in **one** bank (13) at 2.5 V. On the Au the *same element pins* scatter
across **three banks at three different fixed voltages**:

| Sensor pair | Element | Au balls | Au bank | VCCO |
|---|---|---|---|---|
| `clock_out±` | B40/B42 | P13 / N13 | **14** | 3.3 V (fixed) — no `LVDS_25`, no `DIFF_TERM` |
| `dout0±` | B46/B48 | D9 / D10 | **15** | **1.35 V (fixed)** — the DDR3 bank |
| `dout1–3±`, `sync±`, `lvds_clock_in±` | B52…B78 | P1/N1 … N4/M5 | **34** | 3.3 / 2.5 / 1.8 (selectable) |

The forwarded bit clock lands in a bank that can **never** be 2.5 V, so the one pair that matters
most gets neither `LVDS_25` nor `DIFF_TERM`. And `dout0±` lands on **bank 15**, which Alchitry
documents as **"The 1.35V pins are not 3.3V tolerant."** Only 5 of 7 pairs are even in the
multi-voltage bank. **Do not constrain the LVDS pins in an Au build.**

**Why that is nevertheless SAFE:** the sensor's LVDS drivers are **powered down at reset**
(register 112 = 0, all three fields — see `CAMERA_SENSOR_PROTOCOL.md` §3). They only turn on if
something writes register 112. So `dout0` never drives the 1.35 V bank, provided **nothing writes
register 112 on an Au build**. That is a one-line rule, not a gamble.

## 8.3 VBSEL — the Au has it too, and it is harmless here

The camera board pulls **control-header pin 38 (`VBSEL_A`) to +3V3 through 1 kΩ**. That is a Pt V2
strap, so the obvious worry is what pin 38 does on an Au.

**It is the same pin.** Alchitry's V2 control header is common: pin **38 = `VBSEL_A`**, pin
**40 = `VBSEL_B`**, same truth table on both boards (floating/floating → 3.3 V; high/high → 2.5 V;
high/low → 1.8 V). On the Au they select the VCCO of the **multi-voltage bank, which is bank 34**.

**And the SLI design uses ZERO bank-34 pins.** Its Bank-B remap lands on element B27–B30
(`R11`/`R16`/`R10`/`R15`) and B33–B36 (`K5`/`N16`/`E6`/`M16`) — every one of those is in bank **14
or 35**, both hardwired 3.3 V. So the strap changes the voltage of a bank nothing is using.

> **Recommendation for an Au build: do not populate the `VBSEL_A` strap resistor.** Nothing on the
> Au needs 2.5 V. Leaving it off keeps bank 34 at its 3.3 V default and makes the whole board
> uniformly 3.3 V — one less variable during first power-up. It is electrically safe either way.

## 8.4 What the Au CAN and CANNOT prove

| | Au V2 |
|---|---|
| Power tree comes up, correctly sequenced | ✅ (the sensor answers on SPI) |
| DF40 pin map + stack pass-through correct | ✅ (ditto — §6 lists this as *"NOT checked"*) |
| `reset_n` releases; SPI RTL works both ways | ✅ |
| Trigger / monitor pins | ✅ |
| Bank 13 @ 2.5 V, `DIFF_TERM`, SRCC/BUFIO choice, even-row routing | ❌ **Pt only** |
| 1:10 ISERDES receiver, training, pixels | ❌ **Pt only** |

The half the Au *can* test — the power tree and the pin map — is precisely the half that has never
been energised and was validated only in SPICE and in Vivado. The half it cannot test is the half
Vivado has already checked on paper.
