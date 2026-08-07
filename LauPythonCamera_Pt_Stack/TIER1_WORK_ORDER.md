# Tier 1 work order — the three fixes that stand between this board and fab

**Target:** clear README §14.1 so the board can be ordered (§15). Prepared 2026-07-28 against
`LauPythonCamera_Pt_Stack.kicad_pcb` at commit `0417463`, which is **DRC-clean (0 errors, 0
unconnected)** — so any DRC error you see after these edits is yours, which is the point of doing
it in this order.

---

## Status — RE-VERIFIED against the board, 2026-08-06. All four jobs are DONE.

> ⚠️ **The old status table here was stale and under-reported the work.** It said job 1 and the J3
> fan-out were still outstanding; both are in the board. The board was re-routed after this work
> order was written (the `.kicad_pcb` was substantially rewritten), so **trust the board, not the
> coordinates below** — several specific via positions in §2 and §3 no longer match. The table
> below was checked directly against the current `.kicad_pcb` and `production/`.

| Job | State |
|---|---|
| **1 — U1 decoupling** | ✅ **DONE — by redistribution, not addition.** No `C42`–`C51`; instead ten *existing* caps were moved to **B.Cu directly under the pins**. Five 1 µF + 10 nF pairs, one per naked pin — see the table below. This is better than the plan: no new part, no new BOM line, no feeder fee, and the BOM stayed byte-identical. |
| **2 — GND stitching** | ✅ **Done in substance.** The power section (x < 120, y < 95) now carries **13 GND vias**; the original complaint was that **exactly one** existed at x < 112. The eight specific coordinates in §2 no longer all match — the re-route moved them. |
| **3 — `+3V3_SYS` entry** | ✅ **Widened, and the J3 fan-out is DONE.** ⬜ **One item genuinely open — see "What is actually left" below.** |
| **4 — silk off U1's pads** | ✅ **Done** — U1, R1, R14 reference fields moved |
| **Test points** | ✅ **DONE, and not in the original plan.** `TP1`–`TP6` exist on F.Cu, one per rail plus GND. |

### Job 1 as built — which cap pairs with which pin

| Pin | Rail | 1 µF | 10 nF |
|---|---|---|---|
| 19 | `+3V3_CAM` | `C2` | `C15` |
| 22 | `+1V8_CAM` | `C6` | `C17` |
| 26 | `+1V8_CAM` | `C5` | `C16` |
| 29 | `+3V3_CAM` | `C1` | `C12` |
| 36 | `+3V3_CAM` | `C3` | `C13` |

All ten are bottom-side, ~1–2 mm from their pin, against the 25–32 mm they had. **Do not add
`C42`–`C51`** — the job is done, and adding them would double the decoupling and change the BOM.

### Test points as built

| | net | | net |
|---|---|---|---|
| `TP1` | `+3V3_SYS` | `TP4` | `+3V3_CAM` |
| `TP2` | `+4V5` | `TP5` | `+3V3_PIX` |
| `TP3` | `+1V8_CAM` | `TP6` | `GND` |

Five rails + GND, which is exactly what §14.3 asked for and what §15.5 needs on bring-up day.
They carry no LCSC part and are excluded from the BOM and CPL by construction.

---

## What is actually left

Two items, both the same *kind* of thing — a rail reaching somewhere through a **single via**. Neither
is a DRC error, neither is a DC current problem, and neither blocks fab. They are single-point-of-
failure and AC-impedance insurance.

| Where | Current state | Note |
|---|---|---|
| **`+3V3_SYS` entry from J3** at **(111.725, 69.875)** | **one** 0.8/0.4 via | §3 below claims two parallel vias were added at (112.35, 69.90) and (113.00, 69.90). **They are not in the current board** — the re-route removed them. Every milliamp the board consumes still crosses this one via. Cheapest fix on the list: add one or two more on the 0.6 mm stub. |
| **`+3V3_CAM`** at **(124.425, 89.200)** | **one** 0.8/0.4 via | §14.2.2's "double both up". The `+4V5` half of that item now has two vias on the path — (109.675, 73.200) and (106.175, 78.050) — so only the `+3V3_CAM` half remains. |

Optional, from §14.2.1 (EMI insurance, never a blocker): two GND stitches by `CAM_LVDSCLK`'s layer
transition at (148.01, 84.84), and one near (140.2, 94.4) for D3.

Board state as last recorded: **0 errors, 0 unconnected, 70 warnings (all silkscreen), 110 benign
parity warnings.** Zones were refilled and the board saved.

> **The "you must open KiCad to refill zones" caveat is withdrawn.**
> `kicad-cli pcb drc --refill-zones --save-board` does it from the command line, and that is how the
> current fills were produced. It matters more than it sounds: **widening a track without refilling
> makes DRC report the stale fill as a 0.000 mm clearance error** — 15 phantom errors appeared that
> way mid-job and all 15 vanished on refill. If you see a wall of zone-clearance errors, refill
> before believing any of them.
>
> **`production/` is CURRENT** (was: "now stale"). It was regenerated 2026-07-28 against the board
> with job 1, the six test points and the J3 fan-out all in place — README §15.6 records the
> verification. Only regenerate if you touch the board again, e.g. to close the two single-via
> items above. The `production/` BOM was hand-corrected on 2026-08-06 for the `U6`/`U7` supervisor
> part number (`C702125` → `C132016`), matching the schematic, so a regeneration will reproduce it.

Coordinates are **KiCad page coordinates**, the same frame the `.kicad_pcb` uses. Subtract
(100, 60) for board-relative.

> **Read this first: §14.1's stated location for job 1 is wrong.** It says to use "the strip east of
> U1 (x 146–154.5)". The board is **notched to x = 149.5** between y 69.5 and 95.5 — everything past
> 149.5 is off-board — and the 2.3 mm that does exist is already carrying `CAM_CLK_PLL` (x 148.82),
> the `CAM_LVDSCLK` escape and both its vias (x 147.7–148.6, y 82.5–85), `IBIAS_MASTER` and GND.
> **Job 1 below uses the B.Cu-under-the-pins option instead**, which was verified free.

---

## Job 1 — local decoupling for U1's five naked supply pins

**Why it is first:** §14.1 calls it "the single highest-value change on the board". Five of eleven
supply pins have their nearest cap 25–32 mm away — ~20 nH of loop inductance, on a board with **no
power plane** (both inner layers are GND, §11.2.1) feeding a **socketed** sensor (1–3 nH per
contact). Pins 22 and 26 are two of the three `vdd_18` pins, and `vdd_18` is what the **360 MHz LVDS
drivers** run from (§6.5).

### 1.1 The parts to add

Ten 0402s — **five 1 µF + 10 nF pairs**, one per naked pin.

| Qty | Value | Footprint | LCSC | Note |
|---|---|---|---|---|
| 5 | 1 µF | `C_0402_1005Metric` | `C52923` | **same line as C1–C7** — no new BOM line, no new feeder fee |
| 5 | 10 nF | `C_0402_1005Metric` | `C15195` | **same line as C12–C22** — ditto |

**Designators: use C42–C51.** The highest existing is C41.
**Do not reuse C25.** Its absence is deliberate — a 10 µF on `+3V3_PIX` that would have broken the
power-down ordering (§14.6). Restoring that designator would resurrect a fixed bug in the next
BOM audit.

### 1.2 Where each pin's via goes — these land on existing copper

The socket's east pads are **2.54 × 0.635 mm centred at x = 145.906**, so each pad spans
x 144.636 → 147.176. Every one of these four pins **already has its rail trace running west off the
pad**, so the feed via lands on existing copper at **x = 144.40** — 0.24 mm clear of the pad edge,
~1.5 mm from the pad centre:

| Pin | Rail | Pad centre | Put a via at | Lands on |
|---|---|---|---|---|
| 19 | `+3V3_CAM` | (145.906, 87.588) | **(144.40, 87.588)** | the existing 0.5 mm run west to x 138.188 |
| 22 | `+1V8_CAM` | (145.906, 84.540) | **(144.40, 84.540)** | the existing 0.5 mm run west to x 135.81 |
| 26 | `+1V8_CAM` | (145.906, 80.476) | **(144.40, 80.476)** | the existing run west to the x 144.136 riser |
| 29 | `+3V3_CAM` | (145.906, 77.428) | **(144.40, 77.428)** | the existing 0.5 mm run west to x 136.497 |

Use the standard 0.5/0.3 via. **Loop budget: ~1.2 nH via + ~1.5 mm trace ≈ 2.5 nH, against ~20 nH
today.** That is the whole point of the job — do not let the cap drift west to make the routing
tidy, because distance is the defect.

### 1.3 Where the caps go

**Bottom side (B.Cu), directly beneath the pads.** Verified free: the only copper in
x 143–147.5 / y 76–89 on B.Cu is a 1 mm `CAM_LVDSCLK_N` detour at y 87.6–88.6, x 146.97–147.88 —
east of where these caps sit, but **watch it when you place pin 19's pair.**

- **B.Cu is a GND pour** (`(zone (net "GND") (layer "B.Cu"))`), so each cap's ground pad connects to
  the plane where it stands — no ground trace, no ground via, near-zero return inductance. This is
  what makes the bottom side the better option.
- Put the **10 nF nearest the via**, the 1 µF outboard of it. Suggested centres: 10 nF at
  (143.55, *y*<sub>pin</sub>), 1 µF at (141.85, *y*<sub>pin</sub>), with a 0.5 mm B.Cu rail trace
  running west from the via. There is ±1.5 mm of clear y around each pin (pins are 3.0–4.1 mm apart),
  so stacking a pair in y instead is equally fine — **your call, whichever routes cleaner.**
- **Height is not a problem and there is precedent:** `R2` is already a bottom-side 0402 inside the
  DF40 mated height. Still worth a sanity check against the stack gap once placed.

### 1.4 Pin 36 — the one that needs your eye

`+3V3_CAM` at **(136.508, 72.094)**, on the socket's *north* edge, not the east column.

The north strip (y 60–72) holds **no components at all** but is dense with traces — 38 `+3V3_PIX`
segments, GND, and a dozen control nets. **Fallback:** B.Cu immediately south of the pad, under the
socket cavity. Target the same ≤1.5 mm as the others; take ≤2.5 mm if that is what fits. Even a
2.5 mm placement is an order of magnitude better than the 25.4 mm it has now.

---

## Job 2 — ground stitching in the power section ✅ DONE

**Exactly one GND via existed at x < 112.** The boost's high-di/dt return loop was confined to the
F.Cu/B.Cu pours with no path into either inner ground plane for over a centimetre.

> ⚠️ **Correction — the GND pad coordinates first written here were wrong.** They were computed with
> the rotation applied the wrong way round. KiCad's transform is
> **`absolute = origin + R(−θ)·local`**, so for the 90°-rotated caps the two pads were **swapped**.
> Caught because a "GND" trace appeared to terminate on a `+3V3_SYS` pad — an impossible short that
> DRC was not reporting, which meant the arithmetic was wrong, not the board. **U1, U3 and the socket
> are all rotation 0, so job 1's coordinates were never affected.** Verified positions below.

**Eight vias placed, all 0.5/0.3, all on existing GND copper so connectivity does not depend on the
pour:**

| Target | GND pad (verified) | Was | Vias placed |
|---|---|---|---|
| `U3.4` | (107.713, 73.50) | **10.80 mm** | (107.50, 74.05) and (107.50, 74.75) on the attached GND trace; (108.20, 74.60) and (106.95, 74.60) fed by two new 0.5 mm stubs off (107.50, 74.40) |
| `C31.2` | **(103.50, 72.05)** | 15.13 mm | (102.20, 72.89) on the attached diagonal |
| `C32.2` | **(117.50, 71.25)** | — | (117.50, 70.20) |
| `C33.2` | **(117.50, 75.45)** | — | (118.50, 74.45) |
| `C34.2` | **(103.50, 86.225)** | 11.15 mm | (102.575, 87.60) |

`SW` still contains **exactly** `L1.2` and `U3.5` (§14.5) — the cluster was placed clear of it, the
nearest approach being 0.71 mm edge-to-edge.

**Two placements had to move after DRC objected, and both objections were real:**

- **C34's first position (102.60, 85.325) sat 0.03 mm from `EN_BOOST`.** That B.Cu track runs
  *parallel* to C34's GND trace, 0.407 mm away centre-to-centre — a 0.5 mm via cannot fit between
  them at all. Moved to the pad's *other* GND branch, the one heading south-west.
- **C33's first position (118.60, 74.35) was 0.165 mm hole-to-hole from an existing via** at
  (118.925, 73.95) — under the 0.2495 mm minimum. Backed off along the same diagonal.

The lesson for job 1: **check B.Cu and existing vias, not just F.Cu tracks.** Both misses were
things I had not queried.

**Also worth doing while you are in there** (Tier 2, §14.2.1): `CAM_LVDSCLK`'s layer transition at
(148.01, 84.84) has **no GND via within 8 mm** — the worst on the board. Two deliberate stitches
there. D0/D2/SYNC all cross at y = 98.800 and are covered by four vias at x ≈ 138.1, 140.4, 142.8,
145.1; D3 needs one near (140.2, 94.4). This is common-mode/EMI, not differential-eye — cheap
insurance, not a blocker.

---

## Job 3 — the whole board is powered through 0.2 mm and one via

Traced end to end, `+3V3_SYS` reaches everything like this:

```
J3 pins (odd 1-15)  --0.2mm B.Cu, daisy-chained along y=65.355-->  x=111.7
   -->  0.2mm B.Cu riser to (111.7, 69.9)  -->  ONE 0.8/0.4 via  -->
   0.2mm F.Cu  (111.7,69.9)->(107.7,69.9)->(107,70.6)->(107,73.175)  -->  U3.3 (boost VIN)
```

Cut that one via and `L1.1`, `U3.3` and `U2.1` all go dark — **everything.** Current is ~320 mA
average (234 mA boost input + 80 mA for U2) plus boost peak inductor current, against ~0.5 A for
0.2 mm on 1 oz outer copper: **64 % loaded, no redundancy, and the last daisy-chain segment carries
the full aggregate.**

Three changes — **1 and 2 are done, 3 is yours:**

**1. ✅ Widened.** Not uniformly, because the run is not uniformly free:

| Segment | Was | Now | Why |
|---|---|---|---|
| F.Cu (111.7, 69.9) → (107.7, 69.9) | 0.2 | **0.8** | nothing within 1.3 mm — verified clear |
| F.Cu (107.7, 69.9) → (107, 70.6) | 0.2 | **0.8** | same |
| F.Cu (107, 70.6) → (107, 71.6) | 0.2 | **0.8** | clear until U3's pad field starts |
| F.Cu (107, 71.6) → (107, 73.175) | 0.2 | **0.3** | **capped by U3's pads** — see below |
| B.Cu (111.7, 66.3) → (111.7, 69.9) | 0.2 | **0.8** | clear of J3's courtyard |
| B.Cu (111.7, 65.355) → (111.7, 66.3) | 0.2 | **0.2** | inside J3's courtyard, stays necked |

> **The last approach into U3 cannot take 0.5 mm.** `U3.2` (`EN_BOOST`) and `U3.5` (`SW`) sit
> **0.3745 mm** from that segment's centreline, so 0.5 mm width leaves 0.125 mm — under the 0.2 mm
> netclass clearance. Maximum legal there is ~0.349 mm; it was set to **0.3**, giving 0.2245 mm.
> A short neck at a fine-pitch IC pad is normal; the defect §14.1 identified was the *long* run and
> the single via, and both are gone.

**2. ✅ Two parallel vias added** at (112.35, 69.90) and (113.00, 69.90), on 0.8 mm stub copper
extended east on **both** layers from the original transition. Three vias now carry the rail where
one did. Spacing is set by the **0.2495 mm minimum hole-to-hole**: with 0.4 mm drills, centres must
be ≥0.65 mm apart, which is why they are not tucked in tighter.

**3. ⬜ YOURS — fan the eight J3 power pins** into a short bus rather than chaining them along
y = 65.355, so no single segment carries the full aggregate. This is inside the 0.4 mm-pitch
connector fanout and it is routing, not a mechanical edit.

While you are here: §14.2.2 flags two more single-via rails — (124.42, 89.20) is the sole feed to
all four `+3V3_CAM` sensor pins, and (109.67, 73.20) the sole feed to both LDOs off `+4V5`. DC
current density is fine; this is single-point-of-failure and AC impedance. **Double both up.**

---

## Job 4 — silk off the pads ✅ DONE (for U1)

Three reference fields moved. **U1's socket pads now carry no silk at all** except the pin-1 marker:

| Ref | Was | Now | Cleared |
|---|---|---|---|
| `U1` | (136, 72.118) — dead centre of the pad-36/37 row | **(136, 94.6)**, south of the socket | pads 36, 37 |
| `R1` | (146.905, 77.5) — pointing west onto the socket | **(148.075, 76.0)** | pads 28, 29 |
| `R14` | (146.93, 80.7) — same | **(148.10, 82.4)** | pads 25, 26, 27 |

U1's designator went **north first and landed on C20** (which sits at exactly (136, 69.4) — the
`vdd_pix` cap row is right there). South of the socket is the only genuinely empty ground: no
footprint origins at all in x 125–150, y 92–100.

**Deliberately left alone: U1's pin-1 marker circle over pad 1.** It is flagged, but it is the pin-1
indicator on a part with no mechanical keying (§10), it will be clipped by the fab like any silk over
a mask opening, and moving it is a footprint-library edit rather than a board edit.

`R1`'s and `R14`'s references now overlap **their own** pads — the ordinary, harmless case that most
of the 38 remaining `silk_over_copper` warnings are. The point was to get them off the *socket*.

---

## Closing out

1. **Re-run DRC** — expect 0 errors. The command is in README §14.7.
   Watch two things specifically: the `CAM_LVDSCLK` uncoupled margin is only **0.29 mm** (8.7148 vs
   9.0), and the B.Cu GND pour has a **0.5 mm keep-back from the LVDS pairs** to preserve the
   microstrip impedance — job 1 pours new copper near neither, but a refill can surprise you.
2. **Re-run with `--schematic-parity`** after the schematic changes. Expect the same 110 benign
   `unconnected-(…)` warnings and **zero net conflicts**; a new net conflict means a cap landed on
   the wrong net.
3. **Regenerate `production/`** — gerbers, drill, **and** BOM + CPL, since job 1 changes both
   (README §15.6).
4. **Order** per README §15 — and note `U1` stays **do not place**.
