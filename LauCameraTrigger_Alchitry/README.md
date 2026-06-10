# LauCameraTrigger — Alchitry camera-trigger breakout PCB

A small KiCad breakout board that connects the **Alchitry Au V2 + Br V2** GPIO to the
structured-light **cameras**, carrying the 4-line trigger/handshake protocol
(trigger out, first-frame/pattern out, camera-ready in, mode in) over a **DB-9** camera port.
It is the hardware companion to the [AuV2-SLI](../README.md) FPGA design.

This folder contains the **minimum KiCad source** needed to fabricate and assemble the board,
plus the **camera-wiring documents** for connecting Basler and Allied Vision USB-3 cameras.

---

## Files in this folder

### KiCad project (order + assemble from these three)
| File | What it is |
|---|---|
| `LauCameraTrigger_Alchitry.kicad_pro` | KiCad 7 project file. |
| `LauCameraTrigger_Alchitry.kicad_sch` | Schematic — the BOM and assembly reference. |
| `LauCameraTrigger_Alchitry.kicad_pcb` | Board layout. Footprints/geometry are embedded, so this file is self-contained — plot Gerbers + drill from it, or upload it directly to a fab (e.g. JLCPCB/PCBWay). |

The board is a passive interconnect: **1× DB-9 (DE9) camera port**, **2× 1×08 + 4× 1×03 + 1× 2×03
0.1″ pin headers** to the Br V2 GPIO, and **8 axial resistors** (line series/pull resistors). All
footprints are standard KiCad libraries; no custom library is required.

### Camera-wiring documents
| File | Covers |
|---|---|
| `Basler_ACE_USB_GPIO_Wiring_Guide.md` | **Basler ACE USB 3.0** (Hirose 6-pin / Opto-GP-I/O Y-cable) → DB-9. Pinout, wire colors, opto-isolated vs. TTL lines, pull-up/level-shift notes, Pylon SDK config. |
| `Alvium_1800_GPIO_Wiring_Guide.md` | **Allied Vision Alvium 1800 USB** (JST 7-pin) → DB-9. Pinout, wire colors, 3.3 V push-pull GPIO (no pull-ups needed), Vimba SDK config. |
| `Alvium_Three_Camera_DB9_Harness.md` | External harness combining **three Alvium cameras into one DB-9** (1 master + 2 slaves) so the slaves share the broadcast trigger without bus contention. |
| `Alvium_Three_Camera_Onboard_JST_PCB.md` | A **variant of *this* board** that replaces the DB-9 with **three on-board JST-7 connectors** — cameras plug in with 1:1 JST cables (the "JST-to-PCB" build). Lists the exact schematic edits and the `Connector_JST` footprint. |
| `Vimba_FPGA_LCG_Timing_Guide.md` | Camera **timing & GPIO handshake** settings (trigger delay, exposure, FrameTriggerWait) for FPGA-triggered SLI capture. |

---

## Ordering the PCB

1. Open `LauCameraTrigger_Alchitry.kicad_pcb` in KiCad 7+.
2. **File → Plot** → Gerbers, and **Fabrication Outputs → Drill Files** — or upload the
   `.kicad_pcb` straight to a fab that accepts it.
3. Run **DRC** first if you've edited anything.

## Assembling

Use `LauCameraTrigger_Alchitry.kicad_sch` to generate the BOM (DB-9 receptacle, the 0.1″ headers,
and the 8 resistors). Then build the **camera cable** for your camera from the matching wiring guide
above (Basler or Alvium), which maps each camera line to the DB-9 pins the board expects.

---

## Notes before you build

- **DB-9 vs. JST.** This board uses a **DB-9** camera port; cameras attach via a JST→DB-9 cable
  (per the wiring guides). If you'd rather the cameras plug **directly** into the board with JST
  cables, build the on-board JST-7 variant in `Alvium_Three_Camera_Onboard_JST_PCB.md`.
- **Header pin labels.** The schematic's FPGA-side nets use the DB-9 harness convention
  (`A28/A29/A31/A32`). Confirm these line up with the FPGA constraints
  (`../constrs_1/imports/RTL/Au2.xdc`, which uses `A17/A23/A29/A35` for camera 1) before relying on
  the trigger lines — the two numbering schemes differ.
- **Camera 2 is outputs-only in the current AuV2 bitstream.** The DB-9 exposes a second camera
  channel, but `C2_in` (camera-ready / mode) is unbound in `Au2.xdc` — camera 2 receives triggers
  but its ready handshake isn't read. See the wiring guides and the main README for details.
- **Basler needs extra parts on the Br V2.** Basler Line 2 is open-collector (needs a pull-up) and
  Line 1 is opto-isolated (needs 5–24 V drive). The stock Br V2 provides neither, so add them
  externally for a Basler. The Alvium (3.3 V push-pull) works directly.
