#!/usr/bin/env python3
"""Generate a STEP solid model of the PYTHON 1300 (NOIP1SN1300A) 48-pin LCC package.

Intended as the reference body for designing a 3D-printed lens/retention bracket, so
the model is built around the three things a bracket actually needs:

  * the **outer envelope** the bracket must clear or grip,
  * the **optical axis**, which is NOT the package centre, and
  * the **image plane height** above the seating plane.

Every dimension below is taken from the onsemi NOIP1SN1300A datasheet in
`docs/datasheets/NOIP1SN1300A.pdf` -- Table 31 (Mechanical Specification), Figure 50
(Package Drawing), Table 32 / Figure 51-52 (Optical Center) and Table 33 (tray, for the
overall-thickness cross-check). Nothing here is estimated from a photograph or from memory.

Coordinate system
-----------------
    origin      centre of the package footprint, on the **seating plane** (package bottom)
    +Z          up, out of the glass lid
    +X / +Y     the datasheet TOP VIEW of Figure 52, i.e. pin 1 at the middle of the LEFT
                edge and pin numbering running COUNTER-CLOCKWISE

That frame is the same one you see on the KiCad canvas for U1, and `--check` asserts it
against the real socket footprint in the .kicad_pcb rather than taking it on trust. A
mirrored model here would be as expensive as the mirrored footprint nearly was --
see README section 12.

Usage
-----
    python gen_sensor_step.py                      # typical dimensions
    python gen_sensor_step.py --tolerance max      # worst-case envelope -- cut pockets from THIS
    python gen_sensor_step.py --socket-seat 0.9    # add the socket body as a keep-out

No third-party packages. The STEP is written directly as AP214 boundary representation,
so this runs on a bare Python install.
"""

import argparse
import math
import os
import re
import sys

import step_writer as sw

MM = "mm"

# --------------------------------------------------------------------------------------
# Datasheet parameters. Do not "tidy" these -- each one is cited.
# --------------------------------------------------------------------------------------

# Figure 50, Package Drawing. Body is 14.22 +0.20/-0.13 square with R0.20 corners.
BODY_XY = {"min": 14.22 - 0.13, "typ": 14.22, "max": 14.22 + 0.20}
BODY_CORNER_R = 0.20

# Figure 50: pitch 1.016 +-0.05, outer-pin span 11.176 +-0.13 (= 1.016 x 11),
# castellation width 0.51 +-0.05 (48x), radius R0.19.
PIN_PITCH = 1.016
PIN_COUNT_PER_SIDE = 12
PIN_WIDTH = 0.51
PIN_DEPTH = 0.19  # (R.19) -- modelled as a square notch of this depth, see --castellations

# Table 31, Glass Lid Specification. XY 13.6 x 13.6, thickness 0.5/0.55/0.6, D263 Teco.
GLASS_XY = 13.60
GLASS_T = {"min": 0.5, "typ": 0.55, "max": 0.6}

# Table 31, Die. 9.0 x 7.95 mm, 725 um thick, centred on the package in X and -175 um in Y.
DIE_XY = (9.0, 7.95)
DIE_T = 0.725
DIE_OFFSET = (0.0, -0.175)

# Table 31, the two stack-up dimensions that set every height in this model.
DIE_TOP_Z = {"min": 1.165, "typ": 1.260, "max": 1.405}  # package bottom -> die top surface
DIE_TOP_TO_GLASS_TOP = {"min": 0.655, "typ": 0.990, "max": 1.305}

# Table 31 / Table 32. The optical centre is offset from the PACKAGE centre. This is the
# single most important number in the file for bracket design: centre the lens here, not
# on the package and not on the socket.
OPTICAL_CENTER = (-0.17924, +1.36714)

# Table 32. 1280 x 1024 active pixels on a 4.8 um pitch.
PIXEL_PITCH = 0.0048
ACTIVE_PIXELS = (1280, 1024)

# Table 33, tray spec: 2.28 mm "includes package, glass and glue attach thickness".
# Used only as a cross-check on the derived typical height.
TRAY_THICKNESS_REF = 2.28

# Section 7 of the README: Andon 680-48-SM-G10-R14-1, body 16.764 mm square, 2.90 mm tall,
# solder pads reaching +-11.176 mm from centre.
SOCKET_BODY_XY = 16.764
SOCKET_HEIGHT = 2.90
SOCKET_PAD_REACH = 11.176

COLORS = {
    "ceramic": (0.90, 0.88, 0.82),
    "glass": (0.55, 0.76, 0.86),
    "die": (0.24, 0.26, 0.32),
    "active": (0.15, 0.60, 0.38),
    "socket": (0.75, 0.30, 0.25),
}


# --------------------------------------------------------------------------------------
# Pin ring
# --------------------------------------------------------------------------------------

def pin_ring():
    """Map pin number -> (side, along-edge offset) in the datasheet top view.

    Numbering runs counter-clockwise from pin 1 at the middle of the left edge. The
    12 pins on a side sit at (k - 5.5) * 1.016 for k = 0..11, spanning +-5.588.
    """
    off = [(k - (PIN_COUNT_PER_SIDE - 1) / 2.0) * PIN_PITCH for k in range(PIN_COUNT_PER_SIDE)]
    ring = {}
    # Left edge, read top to bottom: 43,44,45,46,47,48,1,2,3,4,5,6.
    for k in range(PIN_COUNT_PER_SIDE):
        pin = 43 + k if k < 6 else k - 5
        ring[pin] = ("W", off[PIN_COUNT_PER_SIDE - 1 - k])
    for k in range(PIN_COUNT_PER_SIDE):      # bottom edge, left to right
        ring[7 + k] = ("S", off[k])
    for k in range(PIN_COUNT_PER_SIDE):      # right edge, bottom to top
        ring[19 + k] = ("E", off[k])
    for k in range(PIN_COUNT_PER_SIDE):      # top edge, right to left
        ring[31 + k] = ("N", off[PIN_COUNT_PER_SIDE - 1 - k])
    assert len(ring) == 48, ring
    return ring


def pin_xy(ring, pin, half):
    """Centre of a pin's castellation on the package edge, at half-width `half`."""
    side, along = ring[pin]
    return {"W": (-half, along), "E": (half, along),
            "S": (along, -half), "N": (along, half)}[side]


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


def check_against_pcb(pcb_path, ring):
    """Assert the model's pin ring matches U1's socket footprint on the real board.

    The board file is the verified reference: its footprint was built from the datasheet
    package drawing, caught a mirroring bug once already, and has been fabbed. The socket's
    pads sit further out than the sensor's castellations (9.906 mm vs the package edge), so
    only the side assignment and the along-edge offset are comparable -- which is exactly
    what handedness depends on.
    """
    src = open(pcb_path, encoding="utf-8").read()
    pads = None
    for m in re.finditer(r"\n\t\(footprint ", src):
        body = _sexpr(src, m.start() + 1)
        ref = re.search(r'\(property "Reference"\s+"([^"]+)"', body)
        if not ref or ref.group(1) != "U1":
            continue
        pads = {}
        for pm in re.finditer(r'\(pad "([^"]*)"', body):
            pad = _sexpr(body, pm.start())
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pad)
            key = pm.group(1).strip()
            if key:
                # KiCad's canvas is +y DOWN; the datasheet top view is +y UP.
                pads[int(key)] = (float(at.group(1)), -float(at.group(2)))
        break
    if not pads:
        raise SystemExit("could not find footprint U1 in %s" % pcb_path)
    if len(pads) != 48:
        raise SystemExit("U1 has %d numbered pads, expected 48" % len(pads))

    row = max(abs(c) for xy in pads.values() for c in xy)
    problems = []
    for pin, (x, y) in sorted(pads.items()):
        if abs(abs(x) - row) < 1e-6:
            side, along = ("E" if x > 0 else "W"), y
        else:
            side, along = ("N" if y > 0 else "S"), x
        want_side, want_along = ring[pin]
        if side != want_side or abs(along - want_along) > 1e-3:
            problems.append("pin %d: board says %s%+.3f, model says %s%+.3f"
                            % (pin, side, along, want_side, want_along))
    if problems:
        raise SystemExit("PIN RING MISMATCH vs %s:\n  " % pcb_path + "\n  ".join(problems))

    # The README's handedness test, restated. A mirrored model passes every other check.
    left = sorted((p for p, (s, _) in ring.items() if s == "W"),
                  key=lambda p: -ring[p][1])
    if left != [43, 44, 45, 46, 47, 48, 1, 2, 3, 4, 5, 6]:
        raise SystemExit("HANDEDNESS: left edge top->bottom reads %s, expected "
                         "43,44,45,46,47,48,1,2,3,4,5,6" % left)
    return row


# --------------------------------------------------------------------------------------
# Outlines (closed, counter-clockwise, arcs faceted)
# --------------------------------------------------------------------------------------

def package_outline(half, ring, castellations=True, corner_r=BODY_CORNER_R, corner_segments=4):
    """Package body outline: a rounded square with a notch at each of the 48 castellations.

    The notches only remove material, so the outer envelope stays exactly 2 * `half`
    whether or not they are drawn.
    """
    notch = {}
    if castellations:
        for pin, (side, along) in ring.items():
            notch.setdefault(side, []).append(along)
        for side in notch:
            notch[side].sort()

    def edge(side, reverse):
        """Points along one edge, excluding the corner tangents."""
        pts = []
        for along in notch.get(side, []):
            a, b = along - PIN_WIDTH / 2.0, along + PIN_WIDTH / 2.0
            d = half - PIN_DEPTH
            if side == "S":
                pts += [(a, -half), (a, -d), (b, -d), (b, -half)]
            elif side == "N":
                pts += [(a, half), (a, d), (b, d), (b, half)]
            elif side == "W":
                pts += [(-half, a), (-d, a), (-d, b), (-half, b)]
            else:
                pts += [(half, a), (d, a), (d, b), (half, b)]
        if reverse:
            pts.reverse()
        return pts

    c = half - corner_r
    out = []
    out += [(-c, -half)]
    out += edge("S", reverse=False)                                    # bottom, left to right
    out += [(c, -half)]
    out += sw.arc(c, -c, corner_r, -math.pi / 2, 0.0, corner_segments)   # bottom-right
    out += edge("E", reverse=False)                                      # right, bottom to top
    out += [(half, c)]
    out += sw.arc(c, c, corner_r, 0.0, math.pi / 2, corner_segments)     # top-right
    out += edge("N", reverse=True)                                       # top, right to left
    out += [(-c, half)]
    out += sw.arc(-c, c, corner_r, math.pi / 2, math.pi, corner_segments)
    out += edge("W", reverse=True)                                       # left, top to bottom
    out += [(-half, -c)]
    out += sw.arc(-c, -c, corner_r, math.pi, 1.5 * math.pi, corner_segments)
    return sw.dedupe(out)


# --------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------

def build(args):
    tol = args.tolerance
    half = BODY_XY[tol] / 2.0

    die_top = DIE_TOP_Z[tol]
    glass_t = GLASS_T[tol]
    glass_top = die_top + DIE_TOP_TO_GLASS_TOP[tol]
    glass_bot = glass_top - glass_t
    die_bot = die_top - DIE_T

    # The ceramic is modelled as a solid envelope up to the underside of the glass. The
    # datasheet dimensions the ceramic at 1.65 and the glass sits on it through a glue
    # line; rolling the glue into the ceramic gives the same outer envelope with one
    # fewer body. The cavity is not dimensioned anywhere in the datasheet, so it is not
    # modelled -- which is why the die and active area below are reference geometry that
    # interpenetrates the ceramic rather than sitting in a pocket.
    ceramic_top = glass_bot

    ring = pin_ring()
    checked = None
    if args.check:
        checked = check_against_pcb(args.pcb, ring)

    step = sw.StepFile("NOIP1SN1300A_LCC48",
                       "PYTHON 1300 48-pin LCC, %s dimensions" % tol,
                       args.timestamp, tool="gen_sensor_step.py")

    step.prism(package_outline(half, ring, castellations=not args.no_castellations),
               0.0, ceramic_top, "ceramic_body", COLORS["ceramic"])
    step.prism(sw.rect(0, 0, GLASS_XY, GLASS_XY),
               glass_bot, glass_top, "glass_lid", COLORS["glass"])

    aw = ACTIVE_PIXELS[0] * PIXEL_PITCH
    ah = ACTIVE_PIXELS[1] * PIXEL_PITCH
    if not args.no_reference:
        step.prism(sw.rect(DIE_OFFSET[0], DIE_OFFSET[1], *DIE_XY),
                   die_bot, die_top, "die_REF", COLORS["die"])
        # A thin plate whose TOP face lies exactly on the image plane.
        step.prism(sw.rect(OPTICAL_CENTER[0], OPTICAL_CENTER[1], aw, ah),
                   die_top - 0.02, die_top, "active_area_REF", COLORS["active"])

    if args.socket_seat is not None:
        s = args.socket_seat
        step.prism(sw.rect(0, 0, SOCKET_BODY_XY, SOCKET_BODY_XY),
                   -s, SOCKET_HEIGHT - s, "socket_KEEPOUT", COLORS["socket"])

    out = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "3dmodels", "NOIP1SN1300A_LCC48.step")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    text = step.dumps()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)

    # ----------------------------------------------------------------------------------
    report = []
    w = report.append
    w("wrote %s  (%d entities, %.1f kB)" % (out, step.entity_count, len(text) / 1024.0))
    w("")
    w("  tolerance case            %s" % tol)
    if checked is not None:
        w("  pin ring                  checked against U1 in %s"
          % os.path.basename(args.pcb))
    w("")
    w("  body                      %.2f x %.2f %s square, R%.2f corners"
      % (2 * half, 2 * half, MM, BODY_CORNER_R))
    w("  overall height            %.3f %s   (seating plane -> top of glass)"
      % (glass_top, MM))
    w("  top of ceramic / glass    %.3f -> %.3f %s  (glass %.2f thick)"
      % (ceramic_top, glass_top, MM, glass_t))
    w("  IMAGE PLANE               %.3f %s above the seating plane" % (die_top, MM))
    w("  glass top -> image plane  %.3f %s" % (glass_top - die_top, MM))
    w("")
    w("  OPTICAL AXIS              x = %+.3f  y = %+.3f %s from the package centre"
      % (OPTICAL_CENTER[0], OPTICAL_CENTER[1], MM))
    w("  active area               %.4f x %.4f %s  (%d x %d px @ %.1f um)"
      % (aw, ah, MM, ACTIVE_PIXELS[0], ACTIVE_PIXELS[1], PIXEL_PITCH * 1000))
    w("     corners               x %+.3f .. %+.3f    y %+.3f .. %+.3f"
      % (OPTICAL_CENTER[0] - aw / 2, OPTICAL_CENTER[0] + aw / 2,
         OPTICAL_CENTER[1] - ah / 2, OPTICAL_CENTER[1] + ah / 2))
    w("")
    w("  socket keep-out           body %.3f sq, %.2f tall; pads reach +-%.3f %s"
      % (SOCKET_BODY_XY, SOCKET_HEIGHT, SOCKET_PAD_REACH, MM))
    if args.socket_seat is None:
        w("     (not modelled -- pass --socket-seat MM, the height of the sensor's")
        w("      seating plane above the PCB, to place it. Measure it; the datasheet")
        w("      and the Andon catalog both leave it undimensioned.)")

    # The bracket has to swallow the full range of the two stack-up dimensions.
    lo = DIE_TOP_Z["min"] + DIE_TOP_TO_GLASS_TOP["min"]
    hi = DIE_TOP_Z["max"] + DIE_TOP_TO_GLASS_TOP["max"]
    w("")
    w("  ! overall height varies %.3f .. %.3f %s across the datasheet tolerance band"
      % (lo, hi, MM))
    w("    -- a span of %.3f %s. Anything that clamps on the glass must take up that"
      % (hi - lo, MM))
    w("    range compliantly. Typical is %.3f; the tray spec's %.2f includes glue."
      % (DIE_TOP_Z["typ"] + DIE_TOP_TO_GLASS_TOP["typ"], TRAY_THICKNESS_REF))
    w("  ! image plane itself varies %.3f .. %.3f %s -- focus must be adjustable."
      % (DIE_TOP_Z["min"], DIE_TOP_Z["max"], MM))
    return "\n".join(report)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    board = os.path.dirname(here)
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--tolerance", choices=("min", "typ", "max"), default="typ",
                   help="which end of the datasheet tolerance band to model "
                        "(use 'max' for pockets and clearances)")
    p.add_argument("--out", help="output .step path")
    p.add_argument("--pcb", default=os.path.join(board, "LauPythonCamera_Pt_Stack.kicad_pcb"),
                   help="board file to cross-check the pin ring against")
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip the cross-check against U1's footprint")
    p.add_argument("--no-castellations", action="store_true",
                   help="draw the body as a plain rounded square")
    p.add_argument("--no-reference", action="store_true",
                   help="omit the die and active-area reference solids")
    p.add_argument("--socket-seat", type=float, metavar="MM",
                   help="add the Andon socket as a keep-out, with the sensor's seating "
                        "plane this far above the PCB surface")
    p.add_argument("--timestamp", default="2026-08-07T00:00:00",
                   help="STEP header timestamp; fixed by default so output is reproducible")
    args = p.parse_args()
    if args.check and not os.path.exists(args.pcb):
        print("note: %s not found, skipping the pin-ring cross-check" % args.pcb,
              file=sys.stderr)
        args.check = False
    print(build(args))


if __name__ == "__main__":
    main()
