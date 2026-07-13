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
- The Hd / Ft+ / Sp **pass-throughs** are assumed straight-through. Believed true, not confirmed
  from their schematics. **Meter this before first power-up** — it is 30 seconds and the sensor is
  the expensive part.
- `B36` = Alchitry `Fn` fan pin is taken from Alchitry's element convention, not verified from the
  Pt schematic. It is simply left unused, so nothing depends on it.
