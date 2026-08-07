#!/usr/bin/env python3
"""Generate the socket tile -- a square halo that covers the Andon socket's contacts.

Iteration 0 of the camera bracket. Deliberately just the tile:

  * a square plate large enough to cover the socket's exposed contact ring,
  * a square window in the middle for the PYTHON 1300 to sit in, and
  * two locating pins on the underside, on the board's Andon index holes.

Everything is parameterised, because almost every dimension here is a first guess that a
test print will move.

Coordinate system
-----------------
    origin      centre of U1 (the socket footprint) on the board
    z = 0       the **PCB top surface**
    +X / +Y     the KiCad top view of the board -- the same view as `gen_sensor_step.py`,
                so the two models share an X/Y frame

Note the Z datum differs from `gen_sensor_step.py`, whose z = 0 is the sensor's *seating
plane* inside the socket. The height of that plane above the PCB is published nowhere and
is still unmeasured, so the two models cannot yet be stacked in Z. See `README.md`.

Geometry that came off the real board rather than a catalogue -- `--check` re-reads it on
every run:

    index holes     2 x dia 1.60 NPTH at (-8.382, -8.128) and (+8.128, +8.382)
    socket body     16.764 mm square (the U1 F.Fab rectangle)
    contact pads    2.54 x 0.635 on a 9.906 mm row centre, so copper reaches +-11.176

Usage
-----
    python gen_socket_tile.py
    python gen_socket_tile.py --outer 24 --window 15.0 --thickness 2.0
"""

import argparse
import os
import re
import sys

import step_writer as sw
import check_step

MM = "mm"

# --------------------------------------------------------------------------------------
# Board-derived constants. `check_against_pcb` re-derives all of these from the .kicad_pcb
# and fails if they have moved, so treat these as documentation of what is expected.
# --------------------------------------------------------------------------------------

INDEX_HOLES = ((-8.382, -8.128), (+8.128, +8.382))   # model frame (+y up), from U1
INDEX_HOLE_DIA = 1.60
SOCKET_BODY_XY = 16.764
SOCKET_HEIGHT = 2.90        # README section 7, Andon 680-48-SM, "2.90 mm (REF)"
PAD_REACH = 11.176          # outer edge of the socket's solder pads
SENSOR_BODY_MAX = 14.42     # NOIP1SN1300A body, 14.22 +0.20

COLORS = {
    "tile": (0.35, 0.55, 0.80),
    "pin": (0.25, 0.40, 0.62),
}


# --------------------------------------------------------------------------------------
# Cross-check against the fabbed board
# --------------------------------------------------------------------------------------

def _sexpr(text, start):
    depth = 0
    i = start
    while True:
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1


def check_against_pcb(pcb_path):
    """Re-derive the index holes, the socket body and the pad reach from the board file.

    Returns (holes, hole_dia, body_half, pad_reach) in the model frame. KiCad's canvas is
    +y DOWN and this model is +y UP, so every y is negated on the way in -- the same
    conversion `gen_sensor_step.py` makes, and the same one that, skipped, once produced a
    mirrored footprint.
    """
    src = open(pcb_path, encoding="utf-8").read()
    for m in re.finditer(r"\n\t\(footprint ", src):
        body = _sexpr(src, m.start() + 1)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', body)
        if not ref or ref.group(1) != "U1":
            continue

        holes, dias, pad_reach = [], set(), 0.0
        for pm in re.finditer(r'\(pad "([^"]*)"', body):
            pad = _sexpr(body, pm.start())
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pad)
            x, y = float(at.group(1)), -float(at.group(2))
            if "np_thru_hole" in pad:
                holes.append((x, y))
                drill = re.search(r"\(drill ([\d.]+)\)", pad)
                dias.add(float(drill.group(1)))
            elif pm.group(1).strip():
                size = re.search(r"\(size ([\d.]+) ([\d.]+)\)", pad)
                w, h = float(size.group(1)), float(size.group(2))
                pad_reach = max(pad_reach, abs(x) + w / 2, abs(y) + h / 2)

        fab = None
        for gm in re.finditer(r"\(fp_rect", body):
            g = _sexpr(body, gm.start())
            if 'layer "F.Fab"' in g:
                s = re.search(r"\(start ([-\d.]+) ([-\d.]+)\)", g)
                e = re.search(r"\(end ([-\d.]+) ([-\d.]+)\)", g)
                fab = max(abs(float(v)) for v in s.groups() + e.groups())

        problems = []
        if len(holes) != 2:
            problems.append("expected 2 non-plated index holes, found %d" % len(holes))
        if len(dias) != 1:
            problems.append("index holes have mixed drill sizes: %s" % sorted(dias))
        elif abs(sorted(dias)[0] - INDEX_HOLE_DIA) > 1e-6:
            problems.append("index hole drill is %.3f, expected %.3f"
                            % (sorted(dias)[0], INDEX_HOLE_DIA))
        for got, want in zip(sorted(holes), sorted(INDEX_HOLES)):
            if abs(got[0] - want[0]) > 1e-6 or abs(got[1] - want[1]) > 1e-6:
                problems.append("index hole at %s, expected %s" % (got, want))
        if fab is None:
            problems.append("no F.Fab body rectangle on U1")
        elif abs(fab * 2 - SOCKET_BODY_XY) > 1e-6:
            problems.append("socket body is %.3f square, expected %.3f"
                            % (fab * 2, SOCKET_BODY_XY))
        if abs(pad_reach - PAD_REACH) > 1e-6:
            problems.append("pads reach %.3f, expected %.3f" % (pad_reach, PAD_REACH))
        if problems:
            raise SystemExit("BOARD MISMATCH vs %s:\n  " % pcb_path + "\n  ".join(problems))
        return sorted(holes), sorted(dias)[0], fab, pad_reach

    raise SystemExit("could not find footprint U1 in %s" % pcb_path)


def neighbours_under(pcb_path, half, exclude="U1"):
    """Every footprint whose origin falls under the tile, so nothing tall is missed."""
    src = open(pcb_path, encoding="utf-8").read()
    u1 = None
    found = []
    for m in re.finditer(r"\n\t\(footprint ", src):
        body = _sexpr(src, m.start() + 1)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', body)
        at = re.search(r"\n\t\t\(at ([-\d.]+) ([-\d.]+)", body)
        if not ref or not at:
            continue
        pos = (float(at.group(1)), float(at.group(2)))
        layer = re.search(r'\n\t\t\(layer "([^"]+)"', body)
        if ref.group(1) == exclude:
            u1 = pos
        found.append((ref.group(1), pos, layer.group(1) if layer else "?"))
    out = []
    for name, pos, layer in found:
        if name == exclude or u1 is None:
            continue
        dx, dy = pos[0] - u1[0], -(pos[1] - u1[1])
        if abs(dx) <= half and abs(dy) <= half:
            out.append((name, dx, dy, layer))
    return sorted(out)


# --------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------

def build(args):
    pcb_ok = False
    holes, hole_dia, body_half, pad_reach = INDEX_HOLES, INDEX_HOLE_DIA, \
        SOCKET_BODY_XY / 2, PAD_REACH
    if args.check:
        holes, hole_dia, body_half, pad_reach = check_against_pcb(args.pcb)
        pcb_ok = True

    outer_half = args.outer / 2.0
    z0 = args.standoff
    z1 = z0 + args.thickness
    pin_r = args.pin_dia / 2.0

    step = sw.StepFile("camera_socket_tile",
                       "Andon 680-48-SM contact tile, iteration 0",
                       args.timestamp, tool="gen_socket_tile.py")

    ring_outer = sw.rounded_rect(0, 0, args.outer, args.outer, args.corner_r)
    window = sw.rounded_rect(0, 0, args.window, args.window, args.window_r)
    step.prism(ring_outer, z0, z1, "tile", COLORS["tile"], holes=[window])

    pins = []
    for i, (hx, hy) in enumerate(holes):
        pins.append(step.prism(sw.circle(hx, hy, pin_r, args.pin_segments),
                               -args.pin_engage, z0, "locating_pin_%d" % (i + 1),
                               COLORS["pin"]))

    out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "3dmodels", "camera_socket_tile.step")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    text = step.dumps()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)

    # ----------------------------------------------------------------------------------
    expected = {
        "tile": (abs(sw.signed_area(ring_outer)) - abs(sw.signed_area(window))) * args.thickness,
    }
    for i in range(len(holes)):
        expected["locating_pin_%d" % (i + 1)] = \
            abs(sw.signed_area(sw.circle(0, 0, pin_r, args.pin_segments))) \
            * (z0 + args.pin_engage)

    report = []
    w = report.append
    w("wrote %s  (%d entities, %.1f kB)" % (out, step.entity_count, len(text) / 1024.0))
    if args.stl:
        stl = out[:-5] + ".stl" if args.stl is True else args.stl
        ntri, _ = step.write_stl(stl)
        w("wrote %s  (%d triangles; tile and pins are separate shells, union to print)"
          % (stl, ntri))
    w("")
    if pcb_ok:
        w("  board geometry            re-derived from %s" % os.path.basename(args.pcb))
    w("  tile                      %.2f square x %.2f thick, R%.1f corners"
      % (args.outer, args.thickness, args.corner_r))
    w("  underside / top           z = %.2f -> %.2f %s above the PCB" % (z0, z1, MM))
    w("  window                    %.2f square, R%.1f" % (args.window, args.window_r))
    w("  locating pins             2 x dia %.2f, z = %+.2f -> %+.2f  (%.2f into the board)"
      % (args.pin_dia, -args.pin_engage, z0, args.pin_engage))
    for i, (hx, hy) in enumerate(holes):
        w("     pin %d                 (%+.3f, %+.3f)" % (i + 1, hx, hy))
    w("")

    # -- does it do the job it was asked to do? ----------------------------------------
    w("  coverage")
    w("     socket pads reach     +-%.3f %s;  tile reaches +-%.3f  -> %s"
      % (pad_reach, MM, outer_half,
         "covers them by %.3f" % (outer_half - pad_reach) if outer_half >= pad_reach
         else "MISSES by %.3f" % (pad_reach - outer_half)))
    w("     inner edge            +-%.3f;  socket body edge +-%.3f  -> overlaps the "
      "contact ring by %.3f" % (args.window / 2, body_half, body_half - args.window / 2))
    w("     sensor (max %.2f)     window clearance %.3f %s per side"
      % (SENSOR_BODY_MAX, (args.window - SENSOR_BODY_MAX) / 2, MM))
    if args.window <= SENSOR_BODY_MAX:
        w("     ** the window is not larger than the sensor's worst-case body **")
    w("")

    # -- the thing that does not fit ----------------------------------------------------
    interference = [i + 1 for i, (hx, hy) in enumerate(holes)
                    if abs(hx) - pin_r < body_half and abs(hy) - pin_r < body_half]
    if interference:
        w("  ! LOCATING PINS FOUL THE SOCKET BODY")
        w("    Both index holes sit exactly ON the socket body outline -- one at")
        w("    x = -8.382 and one at y = +8.382, and the body is +-%.3f. A round pin"
          % body_half)
        w("    centred in either hole therefore has half its section inside the socket's")
        w("    footprint, overlapping it by up to %.3f %s." % (pin_r, MM))
        w("    This is real, not a modelling artefact: the holes exist to take the Andon")
        w("    part's OWN index pins, so they are under its body by construction.")
        w("    Options, none of which this iteration picks for you:")
        w("      - order the -0 socket (no index pins) and relieve the tile's pins to a D")
        w("        section, keeping only the outer half of each hole;")
        w("      - locate the tile off the socket body or the board outline instead, and")
        w("        drop the pins entirely;")
        w("      - move to two new mounting holes in the PCB on the next board spin.")
    w("")

    if pcb_ok:
        near = neighbours_under(args.pcb, outer_half)
        top = [n for n in near if n[3] == "F.Cu"]
        w("  under the tile            %d footprints, %d of them on the TOP layer"
          % (len(near), len(top)))
        for name, dx, dy, layer in top:
            w("     %-6s (%+.2f, %+.2f)" % (name, dx, dy))
        if top:
            w("     the tile's underside is at z = %.2f -- confirm none of these is"
              % z0)
            w("     taller than that before printing.")
        else:
            w("     nothing on the top layer to clear: the ten U1 decoupling caps that")
            w("     sit in this area were moved to the underside (README section 14).")

    ok = check_step.validate(out, expected, out=open(os.devnull, "w"))
    w("")
    w("  validation                %s" % ("PASS" if ok else "*** FAILED -- run check_step.py"))
    return "\n".join(report), ok


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    board = os.path.dirname(here)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--outer", type=float, default=23.0,
                   help="tile outer square, mm (default 23.0, covering the pads at 22.352)")
    p.add_argument("--window", type=float, default=14.80,
                   help="centre window square, mm (default 14.80, 0.19/side over the "
                        "sensor's worst-case 14.42 body)")
    p.add_argument("--thickness", type=float, default=1.50, help="tile thickness, mm")
    p.add_argument("--standoff", type=float, default=SOCKET_HEIGHT,
                   help="height of the tile's underside above the PCB, mm "
                        "(default 2.90, resting on the socket)")
    p.add_argument("--corner-r", type=float, default=0.80, help="outer corner radius, mm")
    p.add_argument("--window-r", type=float, default=0.40, help="window corner radius, mm")
    p.add_argument("--pin-dia", type=float, default=1.40,
                   help="locating pin diameter, mm (default 1.40 in a 1.60 hole)")
    p.add_argument("--pin-engage", type=float, default=1.20,
                   help="how far the pins enter the board, mm (the PCB is 1.6 thick)")
    p.add_argument("--pin-segments", type=int, default=32, help="facets per pin")
    p.add_argument("--out", help="output .step path")
    p.add_argument("--stl", nargs="?", const=True, default=None, metavar="PATH",
                   help="also write a binary STL (millimetres), for tools that cannot "
                        "read STEP; defaults to the .step path with a .stl extension")
    p.add_argument("--pcb", default=os.path.join(board, "LauPythonCamera_Pt_Stack.kicad_pcb"))
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip re-deriving the board geometry")
    p.add_argument("--timestamp", default="2026-08-07T00:00:00",
                   help="STEP header timestamp; fixed by default so output is reproducible")
    args = p.parse_args()
    if args.check and not os.path.exists(args.pcb):
        print("note: %s not found, using the recorded constants" % args.pcb, file=sys.stderr)
        args.check = False
    report, ok = build(args)
    print(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
