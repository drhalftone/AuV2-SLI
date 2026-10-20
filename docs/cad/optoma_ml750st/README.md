# Optoma ML750ST — simplified STEP

`ml750st_simple.step` is a deliberately simple model of the Optoma ML750ST: a filleted box,
the lens barrel (coloured like the body), a black lens glass recessed in its front, and
four feet. It is for **arrangement diagrams**: where the camera sits and points relative
to the projector lens, as in the projector profiling setup ([`host/SLI_UI.md`](../../../host/SLI_UI.md)). It is not for fit checks.

Regenerate it with:

```
python docs/cad/optoma_ml750st/make_ml750st_step.py
```

This needs `cadquery`. The script holds every dimension, and marks which are measured and
which are assumed.

## Coordinates

| | |
|---|---|
| Origin | centre of the lens barrel's front face |
| +Z | out of the lens: the lens axis, toward the screen |
| +Y | up |
| +X | to your right when you face the lens |
| Units | mm |

With the origin on the lens, a camera placed in a diagram reads directly as its distance
and offset from the lens.

## Dimensions

| | Value | Source |
|---|---|---|
| Body width (X) | 104.3 | measured |
| Body depth (Z), foremost body point to back | 107.9 | measured |
| Body height (Y), the vented box without feet | 32.0 | **styled**, see below |
| Lens barrel length, front to its back end | 58.4 | **styled**: reached the body's mid-depth before the shift below |
| Extra lens shift out of the front face | 12.0 | **styled**: the barrel stands 16.4 proud in all |
| Lens barrel diameter | 45.4 | measured |
| Lens barrel front, proud of the body's foremost point | 4.4 | measured |
| Lens centre, right of the body centre (facing the lens) | 22.1 | measured |
| Lens centre, above the body's bottom face | 20.6 | measured |
| Corner fillet 6, glass Ø 0.8 × barrel recessed 2, feet Ø10 × 5 | | **assumed**, cosmetic |

Overall, the model is 104.3 × 48.3 × 124.3 mm (W × H × D, including feet and lens).

**Why the body is 32 mm, not 45.** The outline measurement gives 45 mm (42.4–47.0 across
the four views). That is the full height, up to the top of the hood that runs over the
lens. The vented box is lower, and the lens stands taller than it. The box's straight
vertical corner edges have true lengths of 32.8–36.6 mm in the four views, and stop
where the top and bottom edges round off, which puts the real box at roughly 38 mm.

The model is drawn lower still, at 32 mm, so that the lens reads clearly in figures. This
is a styling choice, not a measurement. The bottom face and the lens are unchanged. The
lens (Ø45.4, centre 20.6 mm up) runs from 2.1 mm below the box to 11.4 mm above it.
The barrel is extended back to the body's mid-depth (58.4 mm long), so it shows as a
cylinder along the top, standing in for the hood. It is then moved 12 mm further out of
the front face (16.4 mm proud instead of the measured 4.4), so that it sticks out clearly.
The origin stays on the lens front, so the box moved back by 12 mm.

## How it was measured

**The source.** Coretronic's drawing "ID SIX VIEW ML750ST" (`ML750ST-ID-SIX-VIEW.DRW`,
sheet 2 of 2, A2, scale 1.000, mm), from a DWG (AutoCAD R14) downloaded 2026-10-19. That
sheet has no dimensioned orthographic views. It has four shaded orthographic general
views: front-top, back-top, front-bottom and back-bottom.

**Conversion.** The DWG was converted to DXF with LibreDWG 0.14 (`dwg2dxf`) and read with
`ezdxf`. The parts are on their own layers. The body was measured from the top-cover,
bottom-cover and side-shell layers, and the lens from the focus-ring and lens layers.

**True lengths from an orthographic view.** In each view, the three body edge directions
were taken from the length-weighted angles of the straight edges. Those three angles alone
fix each axis's foreshortening factor. The factors come from three linear equations in f²:
the projection rows must be orthonormal. The body's outline extent, measured
perpendicular to each projected axis, then gives three linear equations for the three true
lengths.

**Cross-checks:**
- All four views give the same body to within a few mm: width 103.6–104.7, depth
  106.9–110.8, height 42.4–47.0.
- Known parts measure at their real size, which confirms the 1:1 scale: the USB-A opening
  is 13.1–13.6 mm and the HDMI opening is 13.9–14.1 mm.
- The lens diameter is 45.4 mm in both front views. It comes from the outline width across
  the lens axis, which is independent of the barrel's length. That width also identifies
  which body axis is the lens axis.
- The lens position comes from least-squares fitting a cylinder to the lens outline,
  relative to the body, in the two front views. The fits agree with each other, and the
  mirrored solution fits worse in both:

| View | Lens centre from the side | Height above bottom | Proud | Fit error |
|---|---|---|---|---|
| front-top | 74.2 | 22.6 | 5.1 | 0.53 mm rms |
| front-bottom | 73.9 | 18.6 | 3.7 | 0.45 mm rms |

- A render of the STEP from the drawing's front-top direction puts the lens where the
  drawing has it: low on the front face, toward your right as you face the lens.

## What it leaves out

- **No hood, and a flat front.** In the drawing, the vented front face is set back behind the
  lens, and a raised hood on the top cover runs forward over the barrel. The model's front
  face is the foremost body point (the hood), so the vents are shown too far forward. The
  hood itself, which rises to 45 mm over the lens, is represented only by the long barrel.
- **No keypad, vents, connectors or focus ring.**
- **The feet are guessed.** Their layers include the internal adjustable-foot mechanism,
  so their height could not be measured.

**The datasheet disagrees.** Retailer listings give 112.5 × 123 × 57 mm (W × D × H), which
is larger than the drawing in every direction. That figure presumably includes the feet,
the lens and the bezel, but I have not confirmed this. The drawing's own scale checks out
against the USB and HDMI openings, so the model follows the drawing.
