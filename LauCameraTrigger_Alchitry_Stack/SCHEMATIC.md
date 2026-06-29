# LauCameraTrigger_Alchitry_Stack — Schematic Build Spec

_Daughter board that **stacks onto the Alchitry DF40 connectors** (top of stack:
this board → Br → Hd → Au) and routes camera/config signals on **Bank B**, instead of plugging
into the Br's 0.1″ headers._

> **The Br is optional.** Every Alchitry board carries the same DF40 sites with pin = signal
> number, passing through the stack, so this board can mate the **Hd directly and the Br drops
> out** — it taps the DF40 itself and provides its own breakout (the JST-7s + DIP switches), so
> the Br's GPIO-breakout/pass-through role is redundant. **Caveat:** as routed it is a *terminal*
> (top-only) board — J1–J3 are bottom-side `…DP` plugs with **no top-side `…DS` sockets**, so
> nothing can stack above it. Adding the matching top sockets (see ROADMAP §5.4) would make it
> fully stack-order-independent.

See [`../ROADMAP.md`](../ROADMAP.md) for the bank-allocation rationale and Ft+/MIPI coexistence.

> **Pre-fab gates (from ROADMAP §8):** `Au2.xdc` must be remapped to these Bank-B pins and
> bench-tested; verify pin-1 mirroring on the bottom-side DF40 plugs; confirm +3V3 pin; check the
> 3D mate vs `Br.step`. **0.4 mm DF40 = fine-pitch SMD (stencil/reflow).**

---

## 1. Net names

| Net | Source (Bank B / DF40 Site C pin) | Meaning | FPGA dir |
|---|---|---|---|
| `CAM_READY`   | B27 / pin 27 | camera ready (FrameTriggerWait) | input |
| `CAM_TRIG`    | B28 / pin 28 | frame trigger | output |
| `CAM_PATTERN` | B29 / pin 29 | first-frame / pattern status | output |
| `CAM_MODE`    | B30 / pin 30 | SLI/HDMI mode select | input |
| `SW_HVSV`     | B33 / pin 33 | scan orientation (H vs V) | input |
| `SW_BLUE`     | B34 / pin 34 | blue enable | input |
| `SW_GREEN`    | B35 / pin 35 | green enable | input |
| `SW_RED`      | B36 / pin 36 | red enable | input |
| `+3V3`        | Site A 50-pin, pin 1 (odd 1–13) | 3.3 V logic rail | — |
| `GND`         | any GND pin (≡1,2 mod 6) on any connector | ground | — |

---

## 2. Components (BOM + KiCad symbol / footprint)

| Ref | Part | KiCad symbol | KiCad footprint | Side | Notes |
|---|---|---|---|---|---|
| **J1** | DF40C-50DP-0.4V | `Connector_Generic:Conn_02x25_Odd_Even` (or Hirose DF40) | Hirose **DF40C-50DP** 0.4 mm | **B.Cu** | Power + mechanical |
| **J2** | DF40C-80DP-0.4V | `Connector_Generic:Conn_02x40_Odd_Even` | Hirose **DF40C-80DP** 0.4 mm | **B.Cu** | **Mechanical only** (Bank A) |
| **J3** | DF40C-80DP-0.4V | `Connector_Generic:Conn_02x40_Odd_Even` | Hirose **DF40C-80DP** 0.4 mm | **B.Cu** | Bank B signals |
| **J4** | JST SM07B-SRSS-TB | `Connector:Conn_01x07_Pin` | `Connector_JST:JST_SH_SM07B-SRSS-TB_1x07-1MP_P1.00mm_Horizontal` | F.Cu | Camera MASTER |
| **J5** | JST SM07B-SRSS-TB | `Connector:Conn_01x07_Pin` | same | F.Cu | Camera SLV1 |
| **J6** | JST SM07B-SRSS-TB | `Connector:Conn_01x07_Pin` | same | F.Cu | Camera SLV2 |
| **SW1–SW4** | SPDT switch (or 4-pos SPDT DIP) | `Switch:SW_SPDT` ×4 | DIP/SMD SPDT (pick part) | F.Cu | HvsV / Blue / Green / Red |
| **R1–R8** | resistor (10 kΩ typ) | `Device:R` | `Resistor_SMD:R_1206_3216Metric` | F.Cu | tie hi/lo, **DNP — populate ≤1 per line** |
| **C1, C2** | 0.1 µF | `Device:C` | `Capacitor_SMD:C_0805_2012Metric` | F.Cu | +3V3 decoupling (optional) |

> Verify the DF40 footprints are the **plug (DP)** gender with correct pad geometry and place them
> **mirrored on B.Cu** (pin-1 flips for a face-down mate). Cross-check against `Br.step`.

---

## 3. DF40 connector wiring

### J3 — Site C, Bank B (signals) — `DF40 pin = B-number`
| Pin | Net | | Pin | Net |
|---|---|---|---|---|
| 27 | `CAM_READY` | | 33 | `SW_HVSV` |
| 28 | `CAM_TRIG`  | | 34 | `SW_BLUE` |
| 29 | `CAM_PATTERN` | | 35 | `SW_GREEN` |
| 30 | `CAM_MODE` | | 36 | `SW_RED` |
| 1,2,7,8,13,14,19,20,25,26,31,32,… (≡1,2 mod 6) | `GND` | | all other pins | **NC** |

### J1 — Site A, 50-pin (power)
- Pins **1,3,5,7,9,11,13** = `+3V3` (tie at least pin 1).
- GND pins (≡1,2 mod 6) = `GND`.
- ⚠️ Even pins 2–16 = **VCC — leave NC** (separate, higher rail; not 3.3 V).
- All other pins = **NC**.

### J2 — Site B, 80-pin Bank A (mechanical only)
- GND pins (≡1,2 mod 6) → `GND` (optional, for bonding).
- **Every Bank-A I/O pin → NC** (HDMI + future Ft+ — must not route).

---

## 4. DIP switches (config level select)

Each switch is **SPDT**: common pole → the config net; one throw → `+3V3`, other throw → `GND`.
(Break-before-make ⇒ can't short the rails. Direct replacement for the old 3-pin jumpers.)

| Switch | Common (net) | Throw 1 | Throw 2 |
|---|---|---|---|
| SW1 | `SW_HVSV`  | `+3V3` | `GND` |
| SW2 | `SW_BLUE`  | `+3V3` | `GND` |
| SW3 | `SW_GREEN` | `+3V3` | `GND` |
| SW4 | `SW_RED`   | `+3V3` | `GND` |

---

## 5. Tie-high/low resistors (camera lines)

Two 1206 positions per camera line — one to `+3V3`, one to `GND`. **Populate at most one** per line
("high or low, not both"); default **DNP**.

| Line | net | R→+3V3 | R→GND |
|---|---|---|---|
| ready  | `CAM_READY`   | R1 | R2 |
| trigger | `CAM_TRIG`   | R3 | R4 |
| pattern | `CAM_PATTERN`| R5 | R6 |
| mode   | `CAM_MODE`    | R7 | R8 |

> ⚠️ `CAM_TRIG` (R3/R4) and `CAM_PATTERN` (R5/R6) are FPGA **outputs** — tying them fights the
> driver. Mark these "bench test only" on the silkscreen; the useful ones are the inputs
> `CAM_READY` and `CAM_MODE`.

---

## 6. Camera JST-7 wiring (Alvium 1800, master + 2 slaves)

Per `../LauCameraTrigger_Alchitry/Alvium_1800_GPIO_Wiring_Guide.md`. Trigger/pattern/GND broadcast
to all three; mode/ready on the **master only** (slaves' pins 3 & 5 N/C to avoid output contention).

| JST pin | Camera line | Net | J4 MASTER | J5 SLV1 | J6 SLV2 |
|---|---|---|---|---|---|
| 1 | GND        | `GND`         | ✓ | ✓ | ✓ |
| 2 | Line0 trigger (cam in)  | `CAM_TRIG`    | ✓ | ✓ | ✓ |
| 3 | Line1 mode (cam out)    | `CAM_MODE`    | ✓ | ✂ NC | ✂ NC |
| 4 | Line2 pattern (cam in)  | `CAM_PATTERN` | ✓ | ✓ | ✓ |
| 5 | Line3 ready (cam out)   | `CAM_READY`   | ✓ | ✂ NC | ✂ NC |
| 6,7 | unused | — | NC | NC | NC |
| MP (×2) | mounting tabs | (mechanical; optional GND) | | | |

---

## 7. Power / decoupling
- `+3V3` from J1 pin 1; `GND` common across all connectors.
- Place C1/C2 (0.1 µF) near the DIP-switch bank / J3 on the `+3V3` rail.

---

## 8. Placement (drives layout)
- **Bottom side (B.Cu), facing down**, at the exact Br sites (board frame, mm):
  J1 @ **(16.5, 41.0)**, J2 @ **(38.0, 41.0)**, J3 @ **(38.0, 4.0)**. Pin-1 mirrored.
- DIP switches, resistors, JSTs on **top (F.Cu)** for access.
- Board outline ≈ Br footprint (~61 × 52 mm; confirm vs `Br.step` / Alchitry mechanical dwg).
- Ground pour both layers; GND stitching vias. **⚠️ rev 1 shipped with a fragmented GND
  island here — see §10.1. The stitching must actually bond the switch / cap / tie-resistor
  GND region to the DF40 connector grounds; a pad being on the `GND` net is NOT enough.**

---

## 9. Open items
- Confirm DF40 plug **footprints** (exact KiCad lib part + bottom-side mirror) against `Br.step`.
- Pick the SPDT DIP switch part (4-position SPDT, or 4× discrete SPDT).
- Tie-resistor value (10 kΩ suggested).
- 1 vs 3 cameras (3 carried over from the 3xJST design).
- **`Au2.xdc` Bank-B remap** (R11/R16/R10/R15/K5/N16/E6/M16) must land before fab.
- ✅ **GND island fix (§10.1)** — done in layout (B.Cu GND traces to J3 + stitching via);
  refill-zones / island review + fab bench-test still pending.

---

## 10. Errata — rev 1 (fabricated 2026) defects & fixes

### 10.1 Floating GND island at the DIP-switch / cap region — FIXED in layout (pending fab verify)

**Defect.** The front-copper (F.Cu) ground pour around the DIP switch is a **fragmented
island** that never bonds to the DF40 connector grounds. Confirmed-floating copper:
**SW1 GND pads 5/6/7**, the **tie-low resistor GND pads (R2/R4/R6/R8)**, and **both
decoupling caps C1/C2**. Net-level DRC passes because every pad is logically on `GND` —
it is a *fill-island*, not a netlist break, so the connectivity checker never flags it.

**Why.** The local pour is pinched off by the switch keepout + SW1's `+3V3` pad + the
signal traces, and no stitching via in that region bonds it to the B.Cu ground pour
(where the DF40 grounds terminate). The `+3V3` rail is unaffected.

**Symptoms (rev 1 board).**
- RGB switches (`SW_BLUE/GREEN/RED` → GND) cannot pull their nets low → config select dead.
- No 3.3 V across C1/C2 (caps ungrounded — not decoupling).
- A DMM reads the floating "GND" as ~0 V vs `+3V3` (a high-Z meter follows the live probe);
  `+3V3` measured against *true* ground is a correct 3.3 V.
- HvsV is unaffected — SW1 ties `SW_HVSV` to `+3V3`, not the broken GND.

**Bodge (rev 1 — CONFIRMED WORKING 2026-06-29).** Tie the island together (switch GND
pads + R2/R4 tie-low GND pads) and jumper it to **true ground via a GND through-hole on the
host Alchitry Pt V2** (system ground; the DF40 pins are too fine-pitch to hand-solder).
Equivalent target if reachable: any **J3 GND pin**. ⚠️ Confirm the host hole is GND, not the
power rail, before bonding. Verify island → true GND ≈ 0 Ω and 3.3 V across C1/C2 after.

**Fix — applied in this PCB (2026-06-29).** Bonded the island to the DF40 ground with explicit
copper rather than trusting pour fill:
- Added **B.Cu GND traces** from the **SW1 GND pad (≈140.8, 99.8)** across to the **J3 / DF40
  ground region (≈156.5, 102.8)**, tying the switch / cap / tie-low GND to true ground.
- Added a **GND stitching via at (162, 97.5)** (F.Cu↔B.Cu) by the C1/C2 region.
- Silk-labeled the switch positions (HvsV / Blue / Green / Red).

**Still to verify (before/at next fab):** refill zones + manual filled-zone / island review
(net-level DRC will NOT catch a fill-island), then bench-test `switch-GND → DF40 GND ≈ 0 Ω` and
3.3 V across C1/C2 on the fabricated board. Rev 1 boards in hand still need the bodge above.
