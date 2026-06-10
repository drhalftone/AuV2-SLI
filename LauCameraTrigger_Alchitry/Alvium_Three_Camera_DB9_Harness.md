# Alvium Three-Camera DB-9 Harness (1 Master + 2 Slaves)

This document describes a wiring harness that combines three Allied Vision Alvium 1800 USB cameras into a single DB-9 connection to the FPGA. One camera acts as the **master** (its outputs feed the FPGA); the other two are **slaves** (they receive the broadcast trigger / pattern signals but their outputs are left disconnected so they do not contend on the shared bus).

The harness assumes each camera is terminated with the standard JST-to-DB9 cable described in [Alvium_1800_GPIO_Wiring_Guide.md](Alvium_1800_GPIO_Wiring_Guide.md).

---

## Stage 1: Three identical JST → DB-9 cables (one per camera)

Build three cables exactly per the Alvium guide. Repeated here for reference:

| AVT JST Pin | Wire Color | Signal | Camera Dir | DB-9 Pin |
|-------------|------------|--------|------------|----------|
| 1           | Black      | GND    | —          | **1**    |
| 2           | Brown      | Line0  | INPUT      | **5**    |
| 3           | Orange     | Line1  | OUTPUT     | **9**    |
| 4           | Yellow     | Line2  | INPUT      | **4**    |
| 5           | Green      | Line3  | OUTPUT     | **8**    |

JST pins 6 and 7 are unused. DB-9 pins 2, 3, 6, 7 are unused.

The FPGA breakout PCB used in this build has a **DB-9 female** receptacle, so each camera cable must terminate in a **DB-9 male** plug (so it mates with the PCB directly when not going through the harness, and with the harness when it is).

---

## Stage 2: Three-to-one DB-9 harness

### Connector summary

| Leg | Role          | Connector   | Mates with                       |
|-----|---------------|-------------|----------------------------------|
| A   | Master camera | DB-9 female | Camera 1 cable (DB-9 male)       |
| B   | Slave camera  | DB-9 female | Camera 2 cable (DB-9 male)       |
| C   | Slave camera  | DB-9 female | Camera 3 cable (DB-9 male)       |
| D   | To FPGA       | DB-9 male   | FPGA breakout PCB (DB-9 female)  |

The FPGA-side gender (male on leg D) matches the custom FPGA breakout PCB used in this build, which exposes a DB-9 female receptacle. If you ever swap to a different breakout, re-check this — the stock Alchitry Br V2 and Numato Mimas A7 docs in this repo describe a male PCB receptacle, which would require leg D to be female instead.

### Signal direction recap (FPGA's perspective)

| DB-9 Pin | Wire Color (camera side) | Signal             | FPGA Dir | Action in harness                                    |
|----------|--------------------------|--------------------|----------|------------------------------------------------------|
| 1        | Black                    | GND                | —        | Bus all 4 connectors together                        |
| 4        | Yellow                   | Pattern frame      | OUTPUT   | Broadcast: FPGA → all three cameras                  |
| 5        | Brown                    | Trigger            | OUTPUT   | Broadcast: FPGA → all three cameras                  |
| 8        | Green                    | Camera ready       | INPUT    | **Master only.** Slaves left open                    |
| 9        | Orange                   | SLI/HDMI mode      | INPUT    | **Master only.** Slaves left open                    |

The slaves' Green (Line3) and Orange (Line1) outputs must NOT be tied to the FPGA — multiple push-pull outputs sharing a wire would fight and damage the camera transceivers.

### Wiring table

| Signal / Wire            | Master (A) Pin | Slave 1 (B) Pin | Slave 2 (C) Pin | FPGA (D) Pin |
|--------------------------|----------------|-----------------|-----------------|--------------|
| GND (Black)              | 1              | 1               | 1               | 1            |
| Yellow / Line2 / Pattern | 4              | 4               | 4               | 4            |
| Brown  / Line0 / Trigger | 5              | 5               | 5               | 5            |
| Green  / Line3 / Ready   | 8              | **N/C**         | **N/C**         | 8            |
| Orange / Line1 / Mode    | 9              | **N/C**         | **N/C**         | 9            |

"N/C" = no connection. Cut the wire flush inside the slave-side DB-9 shell (or do not crimp those positions at all). Do not jumper them to ground.

### Diagram

```
  Camera 1 (MASTER)              Harness                    FPGA
  DB-9 male  ───►  Leg A (DB-9 female)  ┐
                                        │
  Camera 2 (SLAVE)                      │
  DB-9 male  ───►  Leg B (DB-9 female)  ├──►  Leg D (DB-9 male)  ───►  FPGA breakout (DB-9 female)
                                        │
  Camera 3 (SLAVE)                      │
  DB-9 male  ───►  Leg C (DB-9 female)  ┘

  Pin 1 (Black/GND)        A1 ── B1 ── C1 ── D1
  Pin 4 (Yellow/Pattern)   A4 ── B4 ── C4 ── D4    [FPGA OUT → all 3 cameras]
  Pin 5 (Brown/Trigger)    A5 ── B5 ── C5 ── D5    [FPGA OUT → all 3 cameras]
  Pin 8 (Green/Ready)      A8 ──────────────── D8  [MASTER only → FPGA IN]
                           B8: N/C   C8: N/C
  Pin 9 (Orange/Mode)      A9 ──────────────── D9  [MASTER only → FPGA IN]
                           B9: N/C   C9: N/C
```

### Build notes

1. **Star vs. daisy-chain at GND**: Star all four GND pins to a single junction inside the harness shell; do not chain them in series. The Pattern and Trigger wires can be paralleled in either topology — keep stub lengths short (<10 cm) so the shared wire still presents a clean rising edge to all three cameras.

2. **Why broadcast trigger works**: Line0 and Line2 on the camera are configured as **inputs** (`LineMode = Input`), so paralleling three of them just adds three high-impedance loads to the FPGA's push-pull driver. The 12 mA / 3.3 V FPGA output drives them comfortably.

3. **Why slave outputs must be isolated**: Line1 and Line3 are configured as **push-pull outputs** on every camera. If two cameras simultaneously drive their Green wires, one HIGH and one LOW, you create a short across the camera transceivers. Leaving slaves' pin 8 / pin 9 open is mandatory, not optional.

4. **Slave handshake loss**: Because slaves do not return `FrameTriggerWait` (Line3) to the FPGA, the FPGA only knows the **master** is ready. Set the FPGA frame interval slow enough that the slowest camera (with its own jitter / queueing) is always ready before the next trigger. The Vimba `AcquisitionFrameRate` and exposure settings should be identical across all three cameras.

5. **Mode-select line**: Only the master's Orange (Line1) reaches the FPGA, so the master alone selects SLI vs HDMI pass-through for the system. The slaves still drive their own Line1 internally — that's fine, it just terminates inside the slave's DB-9 shell.

6. **Cable labeling**: Permanently mark Leg A as MASTER. Swapping legs A↔B at the harness will silently produce a system where the FPGA reads the wrong camera's ready/mode signals and runs open-loop on what you thought was the master.

7. **Strain relief**: Three cables converging on a single shell puts a lot of mass on the harness. Use a backshell with a clamp, or cast the junction in heat-shrink with hot-melt adhesive lining.

---

## Quick verification (multimeter, harness only, no cameras attached)

With nothing plugged in, between the four DB-9 connectors you should see:

| Continuity check                | Expected       |
|---------------------------------|----------------|
| A1 ↔ B1 ↔ C1 ↔ D1               | Connected      |
| A4 ↔ B4 ↔ C4 ↔ D4               | Connected      |
| A5 ↔ B5 ↔ C5 ↔ D5               | Connected      |
| A8 ↔ D8                         | Connected      |
| B8 to anything                  | **Open**       |
| C8 to anything                  | **Open**       |
| A9 ↔ D9                         | Connected      |
| B9 to anything                  | **Open**       |
| C9 to anything                  | **Open**       |
| Any pin 2, 3, 6, 7              | **Open**       |

---

## References

- [Alvium_1800_GPIO_Wiring_Guide.md](Alvium_1800_GPIO_Wiring_Guide.md) — single-camera JST-to-DB-9 mapping
- [Vimba_FPGA_LCG_Timing_Guide.md](Vimba_FPGA_LCG_Timing_Guide.md) — FPGA timing assumptions
