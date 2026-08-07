# Mechanical model — PYTHON 1300 in its 48-pin LCC

`gen_sensor_step.py` writes `../3dmodels/NOIP1SN1300A_LCC48.step`, a solid model of the
sensor package intended as the reference body for a 3D-printed lens / retention bracket.
`check_step.py` validates the result. Regenerate; do not hand-edit the STEP.

```
python gen_sensor_step.py                    # typical dimensions
python gen_sensor_step.py --tolerance max    # worst-case envelope
python check_step.py
```

Every dimension comes from `docs/datasheets/NOIP1SN1300A.pdf` — **Table 31** (Mechanical
Specification), **Figure 50** (Package Drawing), **Table 32 / Figures 51–52** (Optical
Center) and **Table 33** (tray thickness, used only as a cross-check). Nothing is scaled
off a drawing or recalled from memory.

## Coordinate frame

Origin at the centre of the package footprint, on the **seating plane** (package bottom);
+Z out through the glass. X and Y are the datasheet **top view** of Figure 52 — pin 1 at
the middle of the left edge, numbering counter-clockwise. That is the same view as the
KiCad canvas for U1.

`gen_sensor_step.py` asserts its pin ring against U1's socket footprint in the
`.kicad_pcb` on every run, including the left-edge handedness test from README §12
(`43,44,45,46,47,48,1,2,3,4,5,6`, top to bottom). The footprint was mirrored once already;
a mirrored model here would be the same class of expensive mistake, and it would look
perfectly correct.

## Datums

| | min | **typ** | max |
|---|---|---|---|
| Body, square | 14.09 | **14.22** | 14.42 |
| Overall height, seating plane → top of glass | 1.820 | **2.250** | 2.710 |
| **Image plane**, seating plane → top of die surface | 1.165 | **1.260** | 1.405 |
| Top of glass → image plane | 0.655 | **0.990** | 1.305 |
| Glass lid thickness (13.60 sq) | 0.5 | **0.55** | 0.6 |

**Optical axis: x = −0.179 mm, y = +1.367 mm from the package centre.** Active area
6.1440 × 4.9152 mm (1280 × 1024 at 4.8 µm). **Centre the lens on the optical axis, not on
the package and not on the socket** — the Y offset is 1.37 mm, far too large to ignore.

## What this means for the bracket

**The height tolerance is 0.89 mm.** Overall height runs 1.820–2.710 mm and the image
plane itself runs 1.165–1.405 mm. Nothing that references the top of the glass can be
rigid, and focus has to be adjustable — a fixed-height lens standoff cannot work across
the band.

**Press on the ceramic rim, never on the glass.** The nominal ledge between the 14.22 mm
body and the 13.60 mm lid is only 0.31 mm per side, and Figure 50 annotates a "Max Glass
Overlap" of 0.5, which reads as the lid being allowed to sit off-centre. Treat the ledge
as neither symmetric nor guaranteed until measured on a real part.

**The bracket is the retention.** The Andon 680-48-SM is open-frame — no lid, no clamshell,
no clip — and holds the sensor by contact friction alone (README §7). Anything handled or
vibrated needs the bracket to hold the sensor down.

**Clear the socket, not just the sensor.** Socket body 16.764 mm square, 2.90 mm tall, with
solder pads reaching ±11.176 mm from centre. Pass `--socket-seat MM` to include it as a
keep-out solid.

### The one number that is missing

**The height of the sensor's seating plane above the PCB is not documented** — not in the
datasheet, not in the Andon catalog. Everything in this model is referenced to the seating
plane, so that single measurement is what places the optical axis in board coordinates.
Measure it on the assembled board with calipers or a depth gauge and pass it as
`--socket-seat`. Until then the model is correct in its own frame and unplaced in the
board's.

## Modelling choices

- The ceramic is a **solid envelope** with no cavity — the cavity is not dimensioned
  anywhere in the datasheet. The glue line under the lid is rolled into the ceramic, which
  gives the same outer envelope in one fewer body. Derived typical height is 2.250 mm
  against Table 33's 2.28 mm, which includes glue.
- `die_REF` and `active_area_REF` are **reference geometry** and interpenetrate the
  ceramic. `active_area_REF`'s **top face lies exactly on the image plane**. Hide them, or
  drop them with `--no-reference`.
- Castellations are modelled as 0.51 mm square notches 0.19 mm deep (Figure 50 gives
  0.51 ±0.05 width and R0.19). They only remove material, so the envelope is exactly
  14.22 mm either way; `--no-castellations` gives a plain rounded square.
- Corner radius R0.20, faceted in 4 segments.
- The header timestamp is fixed, so regenerating produces a byte-identical file.

## Validation

`check_step.py` re-reads the STEP independently of the code that wrote it and checks that
every edge is used exactly twice per solid (once in each direction), that V − E + F = 2,
that each face loop winds counter-clockwise about the outward normal its plane declares,
and that the enclosed volume integrated over the faces matches the volume computed
analytically from the datasheet dimensions. The volume test is the one that catches an
inside-out solid, which every purely topological check passes.

The output was additionally imported with OCCT (`cadquery`), which reports all four solids
valid, with volumes and bounding boxes matching the table above. `check_step.py` needs no
third-party packages; the OCCT pass was a one-off confirmation.
