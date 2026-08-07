# Mechanical models — camera sensor and bracket

| | writes | |
|---|---|---|
| `gen_sensor_step.py` | `../3dmodels/NOIP1SN1300A_LCC48.step` | the PYTHON 1300 package — the reference body everything else is designed around |
| `gen_socket_tile.py` | `../3dmodels/camera_socket_tile.step` | iteration 0 of the bracket: the contact tile |
| `step_writer.py` | — | shared AP214 writer and 2D helpers |
| `check_step.py` | — | validates any of the above |

Regenerate; do not hand-edit a STEP. Output is byte-reproducible, so a regenerated file
that differs is a real change.

```
python gen_sensor_step.py                    # typical dimensions
python gen_sensor_step.py --tolerance max    # worst-case envelope
python gen_socket_tile.py
python check_step.py
```

The first section below covers the sensor; [the socket tile](#the-socket-tile-iteration-0)
is further down.

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

## The socket tile (iteration 0)

A square halo that covers the socket's exposed contact ring, with a window in the middle
for the sensor and two locating pins underneath. Deliberately nothing else yet.

**Different Z datum.** The tile is referenced to the **PCB top surface** (z = 0), because
that is what it mounts to; the sensor model is referenced to its **seating plane** inside
the socket. X and Y agree. The two cannot be stacked in Z until the seating height is
measured — the same open item as above.

| | default | |
|---|---|---|
| Outer | 23.00 mm square, R0.8 | socket pads reach ±11.176, so this covers them by 0.32 |
| Window | 14.80 mm square, R0.4 | 0.19 mm/side over the sensor's worst-case 14.42 body |
| Thickness | 1.50 mm | |
| Underside | z = 2.90 | resting on the socket, whose height is 2.90 REF |
| Locating pins | 2 × Ø1.40, 1.20 deep | in the Ø1.60 index holes; PCB is 1.6 mm thick |

Every one of those is a first guess a test print will move — they are all flags.

The generator re-derives the index holes, the socket body rectangle and the pad reach from
the `.kicad_pcb` on each run and fails if any has moved, so the tile cannot silently drift
away from the board it mounts to.

**The tile clears everything on the board.** Ten footprints fall under it, all on the
bottom layer — the U1 decoupling caps moved to the underside per README §14. Nothing on
the top layer is in the way.

### The locating pins do not fit, and this is not a modelling artefact

Both index holes sit **exactly on the socket body outline** — one at x = −8.382, one at
y = +8.382, and the body is ±8.382. A round pin centred in either hole has half its
section inside the socket's footprint.

That is by construction: those holes exist to take the **Andon part's own index pins**
(the `-1` suffix), so they are underneath its body by definition. A separate bracket
cannot use them while the socket is fitted. Three ways out, none of them chosen here:

- order the **`-0`** socket (no index pins — which §7 already leans toward, because the
  `-1` pins protrude ~1.66 mm into a 1.6 mm board) and relieve the tile's pins to a **D
  section**, keeping only the outer half of each hole;
- **locate off something else** — the socket body itself, or the board outline — and drop
  the pins;
- add **two dedicated mounting holes** on the next board spin.

`gen_socket_tile.py` prints this interference every run rather than quietly emitting a
part that cannot be assembled.

## STL, for tools that will not read STEP

`--stl` writes a binary STL alongside the STEP. **These models are already polyhedra**, so
the STL is exact — nothing is tessellated or approximated on the way out, and the mesh
volume matches the B-rep solid to the last decimal.

```
python gen_socket_tile.py --stl
python gen_sensor_step.py --stl --no-reference
```

STL carries no units; everything here is **millimetres**, so say so on import.

Committed STLs: `camera_socket_tile.stl` (472 triangles) and `NOIP1SN1300A_LCC48.stl`
(856 triangles, built with `--no-reference`).

**SketchUp** cannot import STEP without a paid extension, and it discards geometry below
about 0.001 inch — which is why the sensor STL is generated without reference geometry.
The `active_area_REF` plate is 0.02 mm thick and would not survive; the generator warns
when any solid is under 0.1 mm. The optical axis is in the datums table above if you need
to place it by hand.

The tile's STL contains the plate and both pins as **separate shells**. That is what a
slicer expects and will union, but union them explicitly if you are modelling with it.

## Validation

`check_step.py` re-reads the STEP independently of the code that wrote it and checks that
every edge is used exactly twice per solid (once in each direction), that V − E + F = 2,
that each face loop winds counter-clockwise about the outward normal its plane declares,
that every inner bound winds opposite its outer loop (which is what makes a window a hole
rather than a second region), and that the enclosed volume integrated over the faces
matches the volume computed analytically from the design dimensions. The volume test is
the one that catches an inside-out solid, which every purely topological check passes.

STL export applies the same two tests to the triangle soup before writing — every
half-edge matched exactly once, positive enclosed volume — because a triangulation bug in
a non-convex cap would otherwise stay invisible until it printed.

It reports **genus** rather than asserting a fixed Euler characteristic — the sensor's
bodies are genus 0, the tile is genus 1 because the window goes all the way through.

Both models were additionally imported with OCCT (`cadquery`), which reports every solid
valid with volumes and bounding boxes matching the tables above. `check_step.py` needs no
third-party packages; the OCCT pass was a one-off confirmation.
