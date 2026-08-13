# Mechanical models — camera sensor and bracket

| | writes | |
|---|---|---|
| `gen_sensor_step.py` | `../3dmodels/NOIP1SN1300A_LCC48.step` | the PYTHON 1300 package — the reference body everything else is designed around |
| `gen_socket_tile.py` | `../3dmodels/camera_socket_tile.step` | iteration 0 of the bracket: the contact tile |
| `gen_lens_box.py` | `../3dmodels/camera_lens_box.step` | **C-mount lens box** — open-bottomed, straddles the board, gravity-seated lens |
| `gen_lens_holder.py` | `../3dmodels/camera_lens_holder.step` | M12 / S-mount alternative: plate on four posts with a threaded barrel |
| `step_writer.py` | — | shared AP214 writer and 2D helpers |
| `check_step.py` | — | validates any of the above |

Regenerate; do not hand-edit a STEP. Output is byte-reproducible, so a regenerated file
that differs is a real change.

```
python gen_sensor_step.py                    # typical dimensions
python gen_sensor_step.py --tolerance max    # worst-case envelope
python gen_socket_tile.py
python gen_lens_box.py --bosses              # the C-mount box, located on the corner holes
python gen_lens_holder.py                    # the M12 alternative
python check_step.py
```

The first section below covers the sensor; [the socket tile](#the-socket-tile-iteration-0)
and [the lens mounts](#the-lens-mounts) are further down.

## The lens mounts

Both read `../LauPythonCamera_Pt_Stack.kicad_pcb` on **every run** for the board outline,
the four Ø2.2 corner holes, U1's position and rotation, and every component courtyard.
Neither hard-codes board geometry, and both **fail the build** rather than warn when a
wall, post or boss would land on a part. A holder that fouls a 0402 is something you
discover with a scalpel.

The optical axis is **not** the package centre. `OPTICAL_CENTER = (-0.17924, +1.36714)`
(Table 32) puts it at KiCad **(135.821, 80.633)** with U1 at (136, 82) unrotated. Skipping
that offset decentres the image and looks perfectly plausible while doing so.

### `gen_lens_box.py` — C-mount (preferred)

An open-bottomed box. The lens is not threaded into anything: its flange shoulder rests on
the top face and gravity holds it, board flat on a table. The bore is a **clearance** hole,
25.4 + 0.8 mm, so the barrel hangs through without touching.

**The top face is the optical datum.** C-mount flange focal distance is 17.526 mm from the
shoulder to the image plane, so `top surface = image plane + 17.526` = **19.786 mm** above
the PCB at the default seat height. That one surface sets focus; everything else is
clearance. Machine or print it flat and do not sand it.

**It stands on the table, not the board.** The first version put walls on the board edge
and the clearance assertion rejected it: C31, an 0805, has its courtyard 1.8 mm in from the
left edge, leaving nowhere for a wall worth printing. Straddling the PCB removes the
constraint entirely — the 56.5 × 46.5 cavity swallows the 55 × 45 board with 0.75 mm all
round, and the box touches nothing.

`--bosses` adds four Ø5 columns that **hang from the top face** down to the board, each with
a Ø2.0 pin entering the board's own Ø2.2 hole 1 mm (a slip fit — this part is meant to lift
off). Ø5 rather than Ø6 because the holes sit 2.5 mm in from the edge and anything larger
overhangs; the script asserts that too.

### `gen_lens_holder.py` — M12 / S-mount

Kept as the alternative. A plate on four corner posts with a tapped barrel; bore 11.5 mm is
the tap drill for M12 × 0.5. Note the PYTHON 1300's image circle is 7.87 mm diagonal — a
**1/2″ format** — and many M12 lenses are only corrected for 1/3″ and will vignette.

### The number that is not published

`--seat-z`, the sensor's seating plane above the PCB, defaults to 1.0 mm. It is not in any
datasheet — `gen_socket_tile.py` says so outright — and it shifts the top face, and
therefore **focus**, 1:1. Measure the glass height with calipers before committing to a
machined part. A C-mount lens at f/1.4 has very little depth of focus.

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

## The socket tile

A square frame that sits **flush on the PCB**, with a window the sensor sits in, 48 wire
slots on the sensor's pin pitch, and two legs through the board's index holes.

**This is the socket-less build.** Sitting flush means the Andon socket is not fitted —
the tile's window at ±7.400 is inside the socket body at ±8.382, so the two cannot both be
there. That is not a compromise, it is the point: if the socket were fitted it would make
the connections itself and there would be nothing to wire. Here the sensor sits directly on
the board and 48 hand-soldered wires do the socket's job, with the tile holding the sensor
in place and guiding each wire to its pad.

> **Check this is still the plan.** This part was drawn when the socket looked unobtainable.
> As of 2026-08-07 it is not — Andon builds it to order in **6–8 weeks**, comfortably inside
> the sensor's ~27-week lead, so the socket is off the critical path (board README §15.1).
> Hand-soldering 48 nets is a lot of work to take on for a problem that turned out to be
> smaller than it looked. The tile stays worth having as a fallback, and `--standoff 2.9`
> returns the socketed geometry — but decide deliberately rather than by inertia.

**Both models now share one origin.** With the sensor sitting directly on the board, its
seating plane *is* the PCB top surface, so `gen_sensor_step.py`'s z = 0 and this file's
z = 0 are the same plane. Open the two STEPs together and they land correctly in all three
axes. (The unmeasured socket seating height above only matters for a socketed build.)

| | default | |
|---|---|---|
| Outer | **20.352 mm** square, R0.8 | derived: pads end at 11.176, less `--expose 1.0` |
| Window | 14.80 mm square, R0.4 | 0.19 mm/side over the sensor's worst-case 14.42 body |
| Thickness | 1.50 mm | |
| Underside | **z = 0** | flush on the board |
| Locating legs | 2 × Ø1.40, **1.70 mm tall** | through the 1.6 mm board, 0.10 proud beneath |
| Wire slots | 48 × 0.51 wide | on the sensor's own 1.016 mm pitch |

**The outer size is derived, not chosen.** `--expose` says how much of each solder joint to
leave visible past the tile edge; the pads run 8.636 → 11.176 from centre, so the default
1.0 mm puts the edge at 10.176. Change `--expose` and the tile follows. `--outer` overrides
it if you want a flat number.

Note the trade-off that buys: the tile no longer shields the whole contact ring. It covers
8.636 → 10.176 of each 2.54 mm pad and deliberately leaves the last millimetre open so an
iron can reach it.

Every one of those is a first guess a test print will move — they are all flags.

### The wire slots

Each slot runs the full height of the window wall as a 0.40 mm deep groove, then turns onto
the bottom face and runs out through the rim as a 0.40 mm deep channel. Slot positions come
from `gen_sensor_step.pin_ring()` rather than being re-derived, so they line up with the
sensor's castellations by construction and inherit that ring's cross-check against U1's
footprint.

**The board's pads sit on that same pitch, at the same along-edge offsets — to 0.000000 mm.**
So a wire leaving sensor pin N and running straight out lands on pad N, with no crossing and
no fan-out: a 1.526 mm run from the sensor edge at 7.110 to where the pads start at 8.636.
That is what makes a straight radial channel the right shape rather than a convenient one,
and the generator asserts it against the board every run.

The turn is why the tile is **two stacked solids**, `tile_upper` and `tile_lower`, split at
z = 0.40. A prism has vertical walls and cannot change profile with height; stacking two is
what lets the groove change direction. Union them to print.

Three things about the slots worth knowing before printing:

- **The ribs between slots are 0.506 mm** — 1.016 pitch less 0.51 slot. That is at or under
  a single FDM extrusion. Print this on a resin machine, or slot every second pin to get
  1.52 mm of pitch. The generator warns about it.
- **The channels run through the rim**, so a wire exits at the tile edge and lands on the
  exposed millimetre of pad. `--channel-reach` stops them short if you want a rim instead.
- **Breaking through severs the bottom layer into 48 pieces** — eleven between the slots on
  each side, plus one wrapping each corner. That is the geometry, not a modelling
  compromise: each piece hangs from the upper ring, which is what holds the tile together.
  They are separate solids (`tile_lower_01` … `48`); union everything to print.
- **The corner is a square step, not a radius.** Give the wire its own bend relief.
- **The exit is flat.** With the tile flush, the channel floor *is* the board, so a wire
  runs level from the castellation onto its pad with no bend at the rim. (Raise
  `--standoff` and that stops being true; the generator reports the resulting bend angle.)

`--no-slots` gives the plain tile back.

The generator re-derives the index holes, the socket body rectangle and the pad reach from
the `.kicad_pcb` on each run and fails if any has moved, so the tile cannot silently drift
away from the board it mounts to.

**The tile clears everything on the board.** Ten footprints fall under it, all on the
bottom layer — the U1 decoupling caps moved to the underside per README §14. Nothing on
the top layer is in the way, which is what makes flush mounting possible at all.

**What it does not do: retain the sensor.** The tile surrounds the sensor without covering
it, so nothing holds the part down vertically. That wants a separate lid, and it is the
obvious next iteration.

### The legs

Ø1.40 in the Ø1.60 index holes — 0.20 mm of diametral clearance — running 1.70 mm from the
tile's underside, so they pass through the 1.6 mm board and stand 0.10 mm proud beneath it.
Nothing on the bottom layer is near either leg; the ten underside caps are all elsewhere.

`--pin-engage` sets the depth. Shorten it below 1.6 and they stop inside the board, which
the generator reports rather than leaves for you to notice.

**The socket interference that blocked the earlier iterations is gone**, and worth recording
why. Both index holes sit exactly on the socket body outline — one at x = −8.382, one at
y = +8.382, against a body of ±8.382 — so any post centred in either had half its section
inside the socket's footprint. The holes are there to take the Andon part's **own** index
pins, so they were under its body by construction. With no socket fitted, they are simply
empty, and the legs use them as intended. If you go back to a socketed build
(`--standoff 2.9`), the interference returns and the generator says so again.

`--pin-length` shortens the leg itself and turns the remainder into a `--boss-dia`
shoulder. That mattered when the tile stood 2.90 mm off the board and the post would
otherwise have been a 4.1 mm needle. Flush, the leg is 1.70 mm and needs no shoulder.

## STL, for tools that will not read STEP

`--stl` writes a binary STL alongside the STEP. **These models are already polyhedra**, so
the STL is exact — nothing is tessellated or approximated on the way out, and the mesh
volume matches the B-rep solid to the last decimal.

```
python gen_socket_tile.py --stl
python gen_sensor_step.py --stl --no-reference
```

STL carries no units; everything here is **millimetres**, so say so on import.

Committed STLs: `camera_socket_tile.stl` (2232 triangles) and `NOIP1SN1300A_LCC48.stl`
(856 triangles, built with `--no-reference`).

Caps are triangulated by ear clipping, not a fan from vertex 0 — neither the sensor
outline nor the tile's cap is remotely convex. Caps with a hole are first merged into one
simple polygon by the standard keyhole bridge.

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

STL export applies the same two tests to the triangles before writing — every half-edge
matched exactly once, positive enclosed volume — because a triangulation bug in a
non-convex cap would otherwise stay invisible until it printed. It checks **per solid**,
not across the model: stacked solids share edges on their common plane quite legitimately,
and checking globally rejects them. (It did, the first time the tile grew slots. The check
was wrong, not the geometry — but it was the check finding the disagreement that made that
worth looking at.)

It reports **genus** rather than asserting a fixed Euler characteristic — the sensor's
bodies are genus 0, the tile is genus 1 because the window goes all the way through.

Both models were additionally imported with OCCT (`cadquery`), which reports every solid
valid with volumes and bounding boxes matching the tables above. `check_step.py` needs no
third-party packages; the OCCT pass was a one-off confirmation.
