"""gen_insert_quarter20.py -- the 1/4-20 brass heat-set insert, as its own part.

Writes ../3dmodels/insert_quarter20.step (+ .stl).

There is no vendor CAD for this insert; it is being modelled feature by
feature from the part in hand. So far:

    body      plain cylinder, 8.0 mm dia x 13.0 mm long
    thread    ONE external helical ridge, 1.0 mm high (outside diameter 10.0),
              TRAPEZOID section: 1.0 mm wide at the root tapering to a 0.3 mm
              flat at the tip; pitch 3.5 mm (MEASURED, crest to crest) over
              the full 13.0 mm -- 3.71 turns -- trimmed flush at both end faces

    z = 0     the insert's bottom face, axis on +Z

Unlike the other generators this one uses cadquery (OpenCASCADE): a helix is a
swept B-rep surface, which step_writer's planar prisms cannot express. The
assembly script gen_stack_assembly.py already depends on the same kernel.
"""
import argparse, os, sys

import cadquery as cq

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "insert_quarter20.step")
BRASS = cq.Color(0.78, 0.62, 0.26)


def build(args):
    r = args.dia / 2.0
    body = cq.Workplane("XY").circle(r).extrude(args.length)

    # The thread: a trapezoid section swept along a helix whose centreline runs
    # the full length, then trimmed to the body's end faces. Starting the helix
    # half a section below z=0 and ending it half above means the trimmed
    # ridge reaches both faces at full section rather than tapering out.
    pitch = args.pitch
    turns = args.length / pitch
    t, h, tip = args.thread_w, args.thread_h, args.thread_tip
    z0 = -t                                   # overshoot, trimmed below
    span = args.length + 2 * t
    helix = cq.Wire.makeHelix(pitch, span, r + h / 2.0).translate(cq.Vector(0, 0, z0))
    start = helix.startPoint()
    # Profile in the plane containing the axis, centred on the helix start
    # (local x = radial, local y = axial). The root is sunk EMBED mm into the
    # body, carrying the taper on, so the union has real overlap to work with
    # rather than two faces exactly coincident.
    embed = 0.05
    def half(rho):                            # half-width at rho above the root
        return (t - (t - tip) * rho / h) / 2.0
    pts = [(-h / 2.0 - embed, -half(-embed)), (h / 2.0, -half(h)),
           (h / 2.0, half(h)), (-h / 2.0 - embed, half(-embed))]
    prof = cq.Workplane("XZ", origin=(start.x, start.y, start.z)).polyline(pts).close()
    thread = prof.sweep(cq.Workplane().add(helix), isFrenet=True)
    trim = cq.Workplane("XY").circle(r + h + 1.0).extrude(args.length)
    part = body.union(thread.intersect(trim))

    solid = part.val()
    if not solid.isValid():
        sys.exit("INVALID SOLID -- the sweep or the boolean failed")
    asm = cq.Assembly(name="insert_quarter20")
    asm.add(part, name="insert", color=BRASS)
    tmp = OUT + ".tmp"
    asm.save(tmp, exportType="STEP")
    os.replace(tmp, OUT)
    cq.exporters.export(part, os.path.splitext(OUT)[0] + ".stl", tolerance=0.01,
                        angularTolerance=0.1)

    bb = solid.BoundingBox()
    w = sys.stdout.write
    w("wrote %s\nwrote %s\n" % (OUT, os.path.splitext(OUT)[0] + ".stl"))
    w("body       %.2f dia x %.2f long\n" % (args.dia, args.length))
    w("thread     %.2f high, %.2f wide at the root -> %.2f flat at the tip, %.2f turns, "
      "pitch %.3f, OD %.2f\n" % (h, t, tip, turns, pitch, args.dia + 2 * h))
    w("bbox       x %.2f..%.2f  y %.2f..%.2f  z %.2f..%.2f\n"
      % (bb.xmin, bb.xmax, bb.ymin, bb.ymax, bb.zmin, bb.zmax))
    w("volume     %.2f mm3, valid solid: %s\n" % (solid.Volume(), solid.isValid()))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dia", type=float, default=8.0, help="body diameter, mm")
    p.add_argument("--length", type=float, default=13.0, help="body length, mm")
    p.add_argument("--pitch", type=float, default=3.5,
                   help="thread pitch, crest to crest, mm (MEASURED 3.5); the thread "
                        "runs the full length, so turns = length / pitch")
    p.add_argument("--thread-w", type=float, default=1.0,
                   help="thread ridge width along the axis AT THE ROOT, mm")
    p.add_argument("--thread-tip", type=float, default=0.3,
                   help="width of the flat at the thread's outer edge, mm")
    p.add_argument("--thread-h", type=float, default=1.0,
                   help="thread ridge height out from the body, mm")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
