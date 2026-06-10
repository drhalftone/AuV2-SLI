# Alvium Three-Camera On-Board JST-7 PCB Variant

This document describes a variant of the `LauCameraTrigger_Alchitry` FPGA breakout PCB in
which the single **DB-9 (DE9)** camera port is replaced by **three on-board JST-7
connectors**, so three Allied Vision Alvium 1800 USB cameras plug straight into the board
with 1:1 JST-7 → JST-7 cables. It bakes the external [three-camera DB-9
harness](Alvium_Three_Camera_DB9_Harness.md) directly onto the board.

- **Source project:** `Developer/LAUCameraTriggerPCB` (DB-9 version, unchanged)
- **Variant project:** `Developer/LAUCameraTriggerPCB_3xJST` (schematic edited; PCB pending sync)
- **Camera:** Alvium 1800 U-507 (5.1 MP Sony IMX264, global shutter), 7-pin JST `BM07B-SRSS-TBT`
- **Board connector:** JST `SM07B-SRSS-TB` right-angle, footprint
  `Connector_JST:JST_SH_SM07B-SRSS-TB_1x07-1MP_P1.00mm_Horizontal`

---

## Why master + 2 slaves (not "all pins tied together")

Each camera GPIO line is either a **camera input** (FPGA drives it) or a **camera output**
(the camera drives it — 3.3 V push-pull):

| Camera line | Function | Camera direction | Safe to parallel? |
|-------------|----------|------------------|-------------------|
| Line0 (trigger) | Frame trigger | **input** | ✅ yes — FPGA drives 3 high-Z loads |
| Line2 (pattern) | Pattern/sync | **input** | ✅ yes |
| Line1 (mode) | SLI/HDMI mode | **output** | ❌ **no** — paralleling = driver contention |
| Line3 (ready) | FrameTriggerWait | **output** | ❌ **no** |

Tying three camera **outputs** together would let one camera drive a line HIGH while
another drives it LOW — a near-short across the camera transceivers (see build note 3 in the
[DB-9 harness doc](Alvium_Three_Camera_DB9_Harness.md)). So the trigger/pattern/GND lines
are broadcast to all three connectors, while **only the master** returns mode + ready to the FPGA.

---

## Connector wiring (PCB JST-7, 1:1 cable to camera)

Assumes a straight-through cable: PCB JST pin N ↔ camera JST pin N.

| JST pin | Camera line | Function | Net (FPGA) | **J3 MASTER** | **J9 SLV1** | **J10 SLV2** |
|---------|-------------|----------|------------|---------------|-------------|--------------|
| 1 | — | GND | GND | ✅ | ✅ | ✅ |
| 2 | Line0 | Trigger (cam in) | A31_1 | ✅ | ✅ | ✅ |
| 3 | Line1 | Mode (cam out) | A28_1 | ✅ | ✂️ N/C | ✂️ N/C |
| 4 | Line2 | Pattern (cam in) | A29_1 | ✅ | ✅ | ✅ |
| 5 | Line3 | Ready (cam out) | A32_1 | ✅ | ✂️ N/C | ✂️ N/C |
| 6 | — | unused | — | N/C | N/C | N/C |
| 7 | — | unused | — | N/C | N/C | N/C |

"N/C" pads are left **unrouted** on the board. The slave cameras still drive their Line1/Line3
into those pads, but the signal dead-ends at the connector — no contention.

### Bus topology

```
  FPGA (Alchitry bank A)                 J3 MASTER   J9 SLV1   J10 SLV2
  ─────────────────────────────────────────────────────────────────────
  A31_1 trigger  (out) ──┬──────────────── pin2 ────── pin2 ───── pin2
  A29_1 pattern  (out) ──┼──────────────── pin4 ────── pin4 ───── pin4
  GND                  ──┼──────────────── pin1 ────── pin1 ───── pin1
  A28_1 mode     (in)  ───────────────────  pin3       (N/C)      (N/C)
  A32_1 ready    (in)  ───────────────────  pin5       (N/C)      (N/C)
```

Keep the trigger/pattern stubs short (<10 cm of trace equivalent) so all three cameras see a
clean rising edge.

---

## What was changed in the schematic

Edited file: `LAUCameraTriggerPCB_3xJST/LauCameraTrigger_Alchitry.kicad_sch`

1. Removed the DSUB (J3) symbol instance.
2. Added a `Conn_01x07` library symbol and three instances: **J3** (CAM MASTER), **J9**
   (CAM SLV1), **J10** (CAM SLV2), each with the JST `SM07B-SRSS-TB` footprint.
3. Net labels attached per the table above; intentional `no_connect` flags on every unused pin.

The **PCB was not modified** — it is regenerated from the schematic in KiCad.

### Finish steps in KiCad (on the Mac)

1. Open the schematic → **Inspect → ERC**. Delete the leftover **dangling wires/labels**
   where the DSUB used to be (≈ x245, y140) and the now-unused channel-2 nets
   (A28_2…A32_2). The live nets are intact.
2. Open the PCB → **Tools → Update PCB from Schematic** (F8): removes the DSUB footprint,
   adds the three JST-7 footprints.
3. Place the three connectors in the area freed by the DSUB; route the broadcast bus
   (GND/A31_1/A29_1 to all three) and the master-only lines (A28_1/A32_1 to J3); re-pour ground.
4. **DRC**, then re-export Gerbers + drill.

### Suggested silkscreen (paste as `gr_text` on `F.SilkS` after placing the connectors)

```
J3  : CAM MASTER  (pins 1-5 all used)
J9  : CAM SLV1    (pins 3 & 5 N/C)
J10 : CAM SLV2    (pins 3 & 5 N/C)
Pin1 = GND, nearest screw-lock hole
```

Mark **J3 = MASTER** clearly — swapping the master with a slave makes the FPGA read the wrong
camera's ready/mode and run open-loop (see harness doc note 6).

---

## ⚠️ Open item: signal-direction contradiction to reconcile

The source schematic's annotation text disagrees with
[`Alvium_1800_GPIO_Wiring_Guide.md`](Alvium_1800_GPIO_Wiring_Guide.md) on two nets:

| Net | Schematic note | Wiring guide |
|-----|----------------|--------------|
| A29_1 | `sync_in_2` → FPGA **input** | Pattern → FPGA **output** |
| A32_1 | `sync_out_2` (trigger) → FPGA **output** | Camera-ready → FPGA **input** |

The master/slave split above is safe **either way**, because it keys off which lines the
*camera* drives (Line1=mode=A28_1, Line3=ready=A32_1) — a physical fact. **But** confirm the
FPGA `.xdc` pin directions match the wiring guide, or the cameras won't actually sync. Check
`Au2.xdc` / the Mimas constraints in the SLI repos.

---

## References

- [Alvium_1800_GPIO_Wiring_Guide.md](Alvium_1800_GPIO_Wiring_Guide.md) — single-camera JST→DB-9 mapping, electrical specs
- [Alvium_Three_Camera_DB9_Harness.md](Alvium_Three_Camera_DB9_Harness.md) — the external harness this board replaces
- [Vimba_FPGA_LCG_Timing_Guide.md](Vimba_FPGA_LCG_Timing_Guide.md) — FPGA timing assumptions
- KiCad footprint: `Connector_JST:JST_SH_SM07B-SRSS-TB_1x07-1MP_P1.00mm_Horizontal`
- Camera mating connector: JST `BM07B-SRSS-TBT`; cable housing `SHR-07V-S`, crimps `SSH-003T-P0.2-H`
