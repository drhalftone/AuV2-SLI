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

def _arc(cx, cy, r, a0, a1, segments):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / segments),
             cy + r * math.sin(a0 + (a1 - a0) * i / segments))
            for i in range(segments + 1)]


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
    out += _arc(c, -c, corner_r, -math.pi / 2, 0.0, corner_segments)   # bottom-right
    out += edge("E", reverse=False)                                    # right, bottom to top
    out += [(half, c)]
    out += _arc(c, c, corner_r, 0.0, math.pi / 2, corner_segments)     # top-right
    out += edge("N", reverse=True)                                     # top, right to left
    out += [(-c, half)]
    out += _arc(-c, c, corner_r, math.pi / 2, math.pi, corner_segments)
    out += edge("W", reverse=True)                                     # left, top to bottom
    out += [(-half, -c)]
    out += _arc(-c, -c, corner_r, math.pi, 1.5 * math.pi, corner_segments)
    return _dedupe(out)


def rect_outline(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
            (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def _dedupe(pts, tol=1e-9):
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def _signed_area(pts):
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                     pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


# --------------------------------------------------------------------------------------
# Minimal AP214 STEP writer -- enough for prisms with planar faces
# --------------------------------------------------------------------------------------

def _r(v):
    """Format a STEP REAL. The decimal point is mandatory."""
    s = "%.9G" % float(v)
    if "E" in s:
        mant, exp = s.split("E")
        if "." not in mant:
            mant += "."
        return mant + "E" + exp
    return s if "." in s else s + "."


class StepFile:
    def __init__(self, product, description, timestamp):
        self.product = product
        self.description = description
        self.timestamp = timestamp
        self._lines = []
        self._n = 0
        self._points = {}
        self._dirs = {}
        self._verts = {}
        self._edges = {}
        self.solids = []      # (entity id, name, rgb)

    def _e(self, body):
        self._n += 1
        self._lines.append("#%d=%s;" % (self._n, body))
        return self._n

    # -- cached primitives -------------------------------------------------------------
    def point(self, x, y, z):
        key = (round(x, 9), round(y, 9), round(z, 9))
        if key not in self._points:
            self._points[key] = self._e("CARTESIAN_POINT('',(%s,%s,%s))"
                                        % (_r(x), _r(y), _r(z)))
        return self._points[key]

    def direction(self, x, y, z):
        key = (round(x, 9), round(y, 9), round(z, 9))
        if key not in self._dirs:
            self._dirs[key] = self._e("DIRECTION('',(%s,%s,%s))" % (_r(x), _r(y), _r(z)))
        return self._dirs[key]

    def vertex(self, x, y, z):
        key = (round(x, 9), round(y, 9), round(z, 9))
        if key not in self._verts:
            self._verts[key] = self._e("VERTEX_POINT('',#%d)" % self.point(x, y, z))
        return self._verts[key], key

    def axis2(self, origin, axis, ref):
        return self._e("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)"
                       % (self.point(*origin), self.direction(*axis), self.direction(*ref)))

    def oriented_edge(self, a, b):
        """Shared EDGE_CURVE between two vertices, oriented for this traversal."""
        (va, ka), (vb, kb) = a, b
        key = (min(ka, kb), max(ka, kb))
        if key not in self._edges:
            p0, p1 = key
            d = [p1[i] - p0[i] for i in range(3)]
            n = math.sqrt(sum(c * c for c in d))
            vec = self._e("VECTOR('',#%d,%s)" % (self.direction(*[c / n for c in d]), _r(1.0)))
            line = self._e("LINE('',#%d,#%d)" % (self.point(*p0), vec))
            v0 = self._verts[p0]
            v1 = self._verts[p1]
            self._edges[key] = self._e("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (v0, v1, line))
        same = (ka == key[0])
        return self._e("ORIENTED_EDGE('',*,*,#%d,%s)"
                       % (self._edges[key], ".T." if same else ".F."))

    def planar_face(self, loop_pts, normal):
        """One planar face. `loop_pts` must run counter-clockwise seen from `normal`."""
        verts = [self.vertex(*p) for p in loop_pts]
        edges = [self.oriented_edge(verts[i], verts[(i + 1) % len(verts)])
                 for i in range(len(verts))]
        loop = self._e("EDGE_LOOP('',(%s))" % ",".join("#%d" % e for e in edges))
        bound = self._e("FACE_OUTER_BOUND('',#%d,.T.)" % loop)
        # Any unit vector perpendicular to the normal will do for the plane's X axis.
        ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (1.0, 0.0, 0.0)
        ref = _cross(normal, ref)
        n = math.sqrt(sum(c * c for c in ref))
        ref = tuple(c / n for c in ref)
        plane = self._e("PLANE('',#%d)" % self.axis2(loop_pts[0], normal, ref))
        return self._e("ADVANCED_FACE('',(#%d),#%d,.T.)" % (bound, plane))

    def prism(self, outline, z0, z1, name, rgb):
        """Extrude a closed CCW outline between two Z planes into a manifold solid."""
        pts = list(outline)
        if _signed_area(pts) < 0:
            pts.reverse()
        faces = [self.planar_face([(x, y, z1) for x, y in pts], (0.0, 0.0, 1.0)),
                 self.planar_face([(x, y, z0) for x, y in reversed(pts)], (0.0, 0.0, -1.0))]
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            dx, dy = bx - ax, by - ay
            n = math.hypot(dx, dy)
            if n < 1e-9:
                continue
            faces.append(self.planar_face(
                [(ax, ay, z0), (bx, by, z0), (bx, by, z1), (ax, ay, z1)],
                (dy / n, -dx / n, 0.0)))
        shell = self._e("CLOSED_SHELL('',(%s))" % ",".join("#%d" % f for f in faces))
        solid = self._e("MANIFOLD_SOLID_BREP('%s',#%d)" % (name, shell))
        self.solids.append((solid, name, rgb))
        return solid

    # -- assembly ----------------------------------------------------------------------
    def dumps(self):
        ctx = self._e("APPLICATION_CONTEXT('automotive design')")
        self._e("APPLICATION_PROTOCOL_DEFINITION('international standard',"
                "'automotive_design',2000,#%d)" % ctx)
        pctx = self._e("PRODUCT_CONTEXT('',#%d,'mechanical')" % ctx)
        prod = self._e("PRODUCT('%s','%s','%s',(#%d))"
                       % (self.product, self.product, self.description, pctx))
        pdf = self._e("PRODUCT_DEFINITION_FORMATION('','',#%d)" % prod)
        dctx = self._e("PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design')" % ctx)
        pd = self._e("PRODUCT_DEFINITION('design','',#%d,#%d)" % (pdf, dctx))
        pds = self._e("PRODUCT_DEFINITION_SHAPE('','',#%d)" % pd)
        self._e("PRODUCT_RELATED_PRODUCT_CATEGORY('part','',(#%d))" % prod)

        length = self._e("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
        angle = self._e("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        solid_a = self._e("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        unc = self._e("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#%d,"
                      "'distance_accuracy_value','confusion accuracy')" % length)
        geo = self._e("(GEOMETRIC_REPRESENTATION_CONTEXT(3)"
                      "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d))"
                      "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d))"
                      "REPRESENTATION_CONTEXT('',''))" % (unc, length, angle, solid_a))

        origin = self.axis2((0, 0, 0), (0, 0, 1), (1, 0, 0))
        items = ["#%d" % origin] + ["#%d" % s for s, _, _ in self.solids]
        shape = self._e("ADVANCED_BREP_SHAPE_REPRESENTATION('%s',(%s),#%d)"
                        % (self.product, ",".join(items), geo))
        self._e("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (pds, shape))

        styled = []
        for solid, name, rgb in self.solids:
            colour = self._e("COLOUR_RGB('%s',%s,%s,%s)"
                             % (name, _r(rgb[0]), _r(rgb[1]), _r(rgb[2])))
            fill = self._e("FILL_AREA_STYLE_COLOUR('',#%d)" % colour)
            fas = self._e("FILL_AREA_STYLE('',(#%d))" % fill)
            ssfa = self._e("SURFACE_STYLE_FILL_AREA(#%d)" % fas)
            sss = self._e("SURFACE_SIDE_STYLE('',(#%d))" % ssfa)
            ssu = self._e("SURFACE_STYLE_USAGE(.BOTH.,#%d)" % sss)
            psa = self._e("PRESENTATION_STYLE_ASSIGNMENT((#%d))" % ssu)
            styled.append(self._e("STYLED_ITEM('colour',(#%d),#%d)" % (psa, solid)))
        if styled:
            self._e("MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(%s),#%d)"
                    % (",".join("#%d" % s for s in styled), geo))

        header = [
            "ISO-10303-21;",
            "HEADER;",
            "FILE_DESCRIPTION(('%s'),'2;1');" % self.description,
            "FILE_NAME('%s','%s',('%s'),('%s'),'%s','%s','');"
            % (self.product, self.timestamp, "AuV2-SLI", "University of Kentucky",
               "gen_sensor_step.py", "gen_sensor_step.py"),
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
            "ENDSEC;",
            "DATA;",
        ]
        return "\n".join(header + self._lines + ["ENDSEC;", "END-ISO-10303-21;", ""])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


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

    step = StepFile("NOIP1SN1300A_LCC48",
                    "PYTHON 1300 48-pin LCC, %s dimensions" % tol,
                    args.timestamp)

    step.prism(package_outline(half, ring, castellations=not args.no_castellations),
               0.0, ceramic_top, "ceramic_body", COLORS["ceramic"])
    step.prism(rect_outline(0, 0, GLASS_XY, GLASS_XY),
               glass_bot, glass_top, "glass_lid", COLORS["glass"])

    aw = ACTIVE_PIXELS[0] * PIXEL_PITCH
    ah = ACTIVE_PIXELS[1] * PIXEL_PITCH
    if not args.no_reference:
        step.prism(rect_outline(DIE_OFFSET[0], DIE_OFFSET[1], *DIE_XY),
                   die_bot, die_top, "die_REF", COLORS["die"])
        # A thin plate whose TOP face lies exactly on the image plane.
        step.prism(rect_outline(OPTICAL_CENTER[0], OPTICAL_CENTER[1], aw, ah),
                   die_top - 0.02, die_top, "active_area_REF", COLORS["active"])

    if args.socket_seat is not None:
        s = args.socket_seat
        step.prism(rect_outline(0, 0, SOCKET_BODY_XY, SOCKET_BODY_XY),
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
    w("wrote %s  (%d entities, %.1f kB)" % (out, step._n, len(text) / 1024.0))
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
