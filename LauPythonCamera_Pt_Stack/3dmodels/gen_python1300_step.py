#!/usr/bin/env python3
"""
Generate a STEP (AP214) solid model of the onsemi PYTHON 1300 (NOIP1SN1300A)
48-pin LCC image sensor.

WHY THIS EXISTS
    The Andon 680-48-SM-G10-R14-x socket is effectively unobtainable, so the
    fallback is a printed fixture that clamps wires between the camera board and
    the sensor's bottom castellation pads. That fixture has to be designed around
    a real sensor envelope, so this is that envelope.

SOURCE OF EVERY DIMENSION
    onsemi NOIP1SN1300A datasheet (NOIP1SN1300A-D):
      * Figure 50, "Package Drawing for the 48-pin LCC Package"  (p.70)
      * Figure 51/52, "Graphical Representation of the Optical Center" (p.71-72)
      * Table 32, "Optical Center Information"                   (p.71)
      * Table 33, "Packing and Tray Specification"               (p.73)
    Pin numbering and handedness follow LauPythonCamera_Pt_Stack.kicad_pcb, whose
    footprint chirality was independently cross-checked against onsemi CASE 115AO
    (README section 12).

COORDINATE SYSTEM
    Origin  : package centre, on the BOTTOM (contact) face.
    +Z      : up, toward the glass lid.
    Top view (looking down -Z) matches the KiCad top view: pin 1 at mid-left,
    one position BELOW the centreline; pin 2 below pin 1.

SOLIDS EMITTED
    CERAMIC_BASE_CASTELLATED
                        14.22 x 14.22, R0.20 corners, z 0 -> 1.08, with all 48
                        castellation GROOVES cut into the four side walls --
                        0.51 wide, NOTCH_D deep, R0.19 inner fillets. These are
                        the indentations the socket contacts (or your wires)
                        seat into. See the UNCONFIRMED note below.
    CERAMIC_UPPER       14.22 x 14.22, R0.20 corners, z 1.08 -> 1.73 (no grooves)
    GLASS_LID           13.60 x 13.60,                z 1.73 -> 2.28
    PAD_01 .. PAD_48    0.51 wide, on the bottom face, z -0.02 -> 0. The pad
                        starts at the GROOVE'S INNER FACE (not the nominal
                        package edge) because there is no ceramic under the
                        groove, and reaches 1.02 in from the package outline --
                        2.16 for PAD_01, whose castellation is ~2x and T-shaped.
    ACTIVE_AREA_REF     6.144 x 4.9152 at (-0.179, +1.367), z 2.28 -> 2.29
                        Not part of the sensor. It is the optical aperture
                        target, projected to the glass top. Delete if unwanted.

ONE MODELLED ASSUMPTION -- flagged deliberately
    Total height 2.28 mm is authoritative (Table 33, "*Includes package, glass
    and glue attach thickness"). The ceramic/glass SPLIT at 1.73/0.55 is read off
    the Figure 50 cross-section and is the only number here that is interpreted
    rather than quoted. It does not affect the outer envelope, the pad plane, or
    the pad positions -- i.e. it does not affect the fixture. It affects only
    where the glass underside sits, which matters if you design a lens spacer.
    Measure it on a real part before committing optics.
"""
import math

OUT = r"C:\Users\dllau\Developer\AuV2-SLI\LauPythonCamera_Pt_Stack\3dmodels\PYTHON1300_NOIP1SN1300A_LCC48.step"

# ----------------------------------------------------------------- dimensions
BODY      = 14.22       # +0.30 / -0.13   Figure 50
CORNER_R  = 0.20        # (R.20) 4x       Figure 50
GLASS     = 13.60       # +/-0.1          Figure 50
H_TOTAL   = 2.28        # Table 33
H_CERAMIC = 1.73        # interpreted -- see docstring
PITCH     = 1.016       # +/-0.05         Figure 50
SPAN      = 11.176      # +/-0.13  (P = 1.016 x 11)
PAD_W     = 0.51        # .51 +/-.05 [48x]
PAD_L     = 1.02        # (1.02) [47x]
PAD_L_P1  = 2.16        # pin 1: L1 = 1.90-2.42, T-shaped; modelled as a plain
                        # rectangle at the nominal midpoint
PAD_T     = 0.02        # metallisation proud of the ceramic (modelled)
NOTCH_R   = 0.19        # [48x] (R.19)     Figure 50 / Figure 52 detail D
# --- the two castellation numbers the drawing does not let me read reliably ---
NOTCH_D   = 0.30        # ** UNCONFIRMED ** radial depth of the groove into the side
NOTCH_H   = 1.08        # ** UNCONFIRMED ** groove height from the bottom face.
#   1.08 is the left-hand dimension on the Figure 50 side view, which is the only
#   plausible candidate for the castellated band's height. Both of these are
#   MEASURABLE ON A PHYSICAL PART in seconds with calipers, and both directly
#   decide whether a wire seats in the groove -- so confirm before printing.
ACT_W     = 1280 * 0.0048     # 6.1440
ACT_H     = 1024 * 0.0048     # 4.9152
ACT_DX    = -0.179      # optical centre offset from package centre, Table 32
ACT_DY    = +1.367
HALF      = BODY / 2.0  # 7.11

# ------------------------------------------------------------------ geometry
def rounded_rect(size, r, seg=6):
    """CCW polygon (viewed from +Z) of a square with rounded corners."""
    h = size / 2.0
    pts = []
    # centres of the four corner arcs, CCW starting bottom-right
    corners = [( h - r, -h + r, -90.0), ( h - r,  h - r,   0.0),
               (-h + r,  h - r,  90.0), (-h + r, -h + r, 180.0)]
    for cx, cy, a0 in corners:
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    # drop duplicated seam points, including the wrap-around
    out = []
    for p in pts:
        if not out or (abs(p[0] - out[-1][0]) > 1e-9 or abs(p[1] - out[-1][1]) > 1e-9):
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-9 and abs(out[0][1] - out[-1][1]) < 1e-9:
        out.pop()
    return out

def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

def _fillet(cx, cy, r, a0, a1, seg=4):
    """Arc points from a0 to a1 degrees about (cx,cy)."""
    return [(cx + r*math.cos(math.radians(a0 + (a1-a0)*i/seg)),
             cy + r*math.sin(math.radians(a0 + (a1-a0)*i/seg))) for i in range(seg+1)]

def notch_profile(c, edge):
    """Points for one castellation groove, in perimeter-walk order.

    edge is 'S','E','N','W' -- the side being walked. The walk directions are
    S:+X, E:+Y, N:-X, W:-Y (counter-clockwise seen from +Z). The groove is a
    NOTCH_D-deep slot NOTCH_W wide with R0.19 fillets at its two inner corners.
    """
    hw, d, r = PAD_W/2.0, NOTCH_D, NOTCH_R
    # profile in a local frame: u = along the walk, v = inward from the edge
    pts = [(-hw, 0.0), (-hw, d - r)]
    pts += _fillet(-hw + r, d - r, r, 180.0, 90.0)      # inner corner 1
    pts += _fillet( hw - r, d - r, r,  90.0,  0.0)      # inner corner 2
    pts += [(hw, d - r), (hw, 0.0)]
    if edge == "S":  return [(c + u, -HALF + v) for (u, v) in pts]
    if edge == "E":  return [(HALF - v, c + u) for (u, v) in pts]
    if edge == "N":  return [(c - u,  HALF - v) for (u, v) in pts]
    if edge == "W":  return [(-HALF + v, c - u) for (u, v) in pts]
    raise ValueError(edge)

def castellated_outline(seg=6):
    """CCW body outline with all 48 castellation grooves cut into the sides."""
    h, r = HALF, CORNER_R
    asc  = sorted(OFF)                 # -5.588 .. +5.588
    out = []
    # South edge, walking +X, then the SE corner arc
    out.append((-h + r, -h))
    for c in asc:            out += notch_profile(c, "S")
    out.append((h - r, -h))
    out += _fillet(h - r, -h + r, r, -90.0, 0.0, seg)
    # East edge, walking +Y
    for c in asc:            out += notch_profile(c, "E")
    out.append((h, h - r))
    out += _fillet(h - r, h - r, r, 0.0, 90.0, seg)
    # North edge, walking -X
    for c in reversed(asc):  out += notch_profile(c, "N")
    out.append((-h + r, h))
    out += _fillet(-h + r, h - r, r, 90.0, 180.0, seg)
    # West edge, walking -Y
    for c in reversed(asc):  out += notch_profile(c, "W")
    out.append((-h, -h + r))
    out += _fillet(-h + r, -h + r, r, 180.0, 270.0, seg)
    # de-duplicate consecutive coincident points, including the wrap
    ded = []
    for p in out:
        if not ded or math.hypot(p[0]-ded[-1][0], p[1]-ded[-1][1]) > 1e-9:
            ded.append(p)
    if math.hypot(ded[0][0]-ded[-1][0], ded[0][1]-ded[-1][1]) < 1e-9:
        ded.pop()
    return ded

def pin_offsets():
    """Six positions each side of centre: +/-0.508, 1.524, ... 5.588."""
    k = (SPAN / 2.0)          # 5.588
    return [k - i * PITCH for i in range(12)]   # +5.588 down to -5.588

OFF = pin_offsets()

def pad_polygon(pin):
    """CCW footprint of a bottom-face pad, in the CAD frame.

    The pad lies ONLY where there is ceramic under it. The groove removes the
    ceramic from the package edge (+/-HALF) inward to +/-(HALF - NOTCH_D), so the
    pad's OUTER end is the groove's inner face, not the nominal package outline.
    Its inner end is placed so the total reach from the package edge is still the
    datasheet's 1.02 mm (2.16 for pin 1) -- i.e. the pad is (L - NOTCH_D) long.
    Plating on the groove walls is ~10-20 um and is not modelled.
    """
    L = PAD_L_P1 if pin == 1 else PAD_L
    hw, d = PAD_W / 2.0, NOTCH_D
    if 43 <= pin <= 48 or 1 <= pin <= 6:                 # WEST edge
        idx = (pin - 43) if pin >= 43 else (pin + 5)     # 43->0 ... 48->5, 1->6 ... 6->11
        y = OFF[idx]
        return rect(-HALF + d, y - hw, -HALF + L, y + hw)
    if 7 <= pin <= 18:                                   # SOUTH edge
        x = OFF[11 - (pin - 7)]                          # 7 -> -5.588 ... 18 -> +5.588
        return rect(x - hw, -HALF + d, x + hw, -HALF + L)
    if 19 <= pin <= 30:                                  # EAST edge
        y = OFF[11 - (pin - 19)]                         # 19 -> -5.588 ... 30 -> +5.588
        return rect(HALF - L, y - hw, HALF - d, y + hw)
    if 31 <= pin <= 42:                                  # NORTH edge
        x = OFF[pin - 31]                                # 31 -> +5.588 ... 42 -> -5.588
        return rect(x - hw, HALF - L, x + hw, HALF - d)
    raise ValueError(pin)

# --------------------------------------------------------------- STEP writer
class Step:
    def __init__(self):
        self.lines = []
        self.n = 0
        self._pt = {}
        self._dir = {}
        self.xyz = {}          # point id -> (x, y, z)

    def add(self, body):
        self.n += 1
        self.lines.append("#%d = %s;" % (self.n, body))
        return self.n

    def point(self, x, y, z):
        k = (round(x, 9), round(y, 9), round(z, 9))
        if k not in self._pt:
            i = self.add("CARTESIAN_POINT('',(%.9G,%.9G,%.9G))" % k)
            self._pt[k] = i
            self.xyz[i] = k
        return self._pt[k]

    def direction(self, x, y, z):
        k = (round(x, 9), round(y, 9), round(z, 9))
        if k not in self._dir:
            self._dir[k] = self.add("DIRECTION('',(%.9G,%.9G,%.9G))" % k)
        return self._dir[k]

    def prism(self, poly, z0, z1, name):
        """Extrude a CCW polygon (viewed from +Z) between two Z planes."""
        n = len(poly)
        pb = [self.point(x, y, z0) for (x, y) in poly]
        pt = [self.point(x, y, z1) for (x, y) in poly]
        vb = [self.add("VERTEX_POINT('',#%d)" % p) for p in pb]
        vt = [self.add("VERTEX_POINT('',#%d)" % p) for p in pt]

        edges = {}
        def edge(va, vb_, pa, pb_):
            """Return (edge_curve_id, same_sense). Canonical direction is
            whichever way the edge was first encountered."""
            key = frozenset((va, vb_))
            if key not in edges:
                ax, ay, az = self.xyz[pa]
                bx, by, bz = self.xyz[pb_]
                dx, dy, dz = bx - ax, by - ay, bz - az
                L = math.sqrt(dx*dx + dy*dy + dz*dz)
                d = self.direction(dx/L, dy/L, dz/L)
                vec = self.add("VECTOR('',#%d,1.)" % d)
                ln = self.add("LINE('',#%d,#%d)" % (pa, vec))
                ec = self.add("EDGE_CURVE('',#%d,#%d,#%d,.T.)" % (va, vb_, ln))
                edges[key] = (ec, va)          # canonical start vertex
            ec, first = edges[key]
            return ec, (first == va)

        faces = []
        def face(vlist, plist, nx, ny, nz):
            oes = []
            for i in range(len(vlist)):
                a, b = i, (i + 1) % len(vlist)
                ec, fwd = edge(vlist[a], vlist[b], plist[a], plist[b])
                oes.append(self.add("ORIENTED_EDGE('',*,*,#%d,%s)"
                                    % (ec, ".T." if fwd else ".F.")))
            loop = self.add("EDGE_LOOP('',(%s))" % ",".join("#%d" % o for o in oes))
            bound = self.add("FACE_OUTER_BOUND('',#%d,.T.)" % loop)
            ax = self.direction(nx, ny, nz)
            rf = self.direction(*((1., 0., 0.) if abs(nz) > 0.5 else (0., 0., 1.)))
            pl = self.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (plist[0], ax, rf))
            surf = self.add("PLANE('',#%d)" % pl)
            faces.append(self.add("ADVANCED_FACE('',(#%d),#%d,.T.)" % (bound, surf)))

        face(list(reversed(vb)), list(reversed(pb)), 0., 0., -1.)   # bottom, -Z
        face(vt, pt, 0., 0., 1.)                                    # top, +Z
        for i in range(n):                                          # sides
            j = (i + 1) % n
            dx, dy = poly[j][0] - poly[i][0], poly[j][1] - poly[i][1]
            L = math.hypot(dx, dy)
            face([vb[i], vb[j], vt[j], vt[i]],
                 [pb[i], pb[j], pt[j], pt[i]], dy/L, -dx/L, 0.)

        shell = self.add("CLOSED_SHELL('',(%s))" % ",".join("#%d" % f for f in faces))
        return self.add("MANIFOLD_SOLID_BREP('%s',#%d)" % (name, shell))

s = Step()
solids = []
solids.append(s.prism(castellated_outline(), 0.0, NOTCH_H, "CERAMIC_BASE_CASTELLATED"))
solids.append(s.prism(rounded_rect(BODY, CORNER_R), NOTCH_H, H_CERAMIC, "CERAMIC_UPPER"))
solids.append(s.prism(rect(-GLASS/2, -GLASS/2, GLASS/2, GLASS/2), H_CERAMIC, H_TOTAL, "GLASS_LID"))
for pin in range(1, 49):
    solids.append(s.prism(pad_polygon(pin), -PAD_T, 0.0, "PAD_%02d" % pin))
solids.append(s.prism(rect(ACT_DX - ACT_W/2, ACT_DY - ACT_H/2,
                           ACT_DX + ACT_W/2, ACT_DY + ACT_H/2),
                      H_TOTAL, H_TOTAL + 0.01, "ACTIVE_AREA_REF"))

# ------------------------------------------------------------ product wrapper
o = s.point(0., 0., 0.)
zd = s.direction(0., 0., 1.)
xd = s.direction(1., 0., 0.)
axis = s.add("AXIS2_PLACEMENT_3D('',#%d,#%d,#%d)" % (o, zd, xd))

lu = s.add("( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )")
au = s.add("( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) )")
su = s.add("( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() )")
unc = s.add("UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#%d,"
            "'distance_accuracy_value','confusion accuracy')" % lu)
ctx = s.add("( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
            "GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#%d)) "
            "GLOBAL_UNIT_ASSIGNED_CONTEXT((#%d,#%d,#%d)) "
            "REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') )"
            % (unc, lu, au, su))

appctx = s.add("APPLICATION_CONTEXT('core data for automotive mechanical design processes')")
s.add("APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#%d)" % appctx)
pctx = s.add("PRODUCT_CONTEXT('',#%d,'mechanical')" % appctx)
NAME = "PYTHON1300_NOIP1SN1300A_LCC48"
prod = s.add("PRODUCT('%s','%s','48-pin LCC image sensor',(#%d))" % (NAME, NAME, pctx))
s.add("PRODUCT_RELATED_PRODUCT_CATEGORY('part','',(#%d))" % prod)
pdf = s.add("PRODUCT_DEFINITION_FORMATION('','',#%d)" % prod)
pdctx = s.add("PRODUCT_DEFINITION_CONTEXT('part definition',#%d,'design')" % appctx)
pd = s.add("PRODUCT_DEFINITION('design','',#%d,#%d)" % (pdf, pdctx))
pds = s.add("PRODUCT_DEFINITION_SHAPE('','',#%d)" % pd)
items = ",".join("#%d" % i for i in ([axis] + solids))
absr = s.add("ADVANCED_BREP_SHAPE_REPRESENTATION('%s',(%s),#%d)" % (NAME, items, ctx))
s.add("SHAPE_DEFINITION_REPRESENTATION(#%d,#%d)" % (pds, absr))

hdr = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('onsemi PYTHON 1300 NOIP1SN1300A 48-pin LCC image sensor'),'2;1');
FILE_NAME('PYTHON1300_NOIP1SN1300A_LCC48.step','2026-08-07T00:00:00',
  ('AuV2-SLI / LauPythonCamera_Pt_Stack'),('generated by gen_python1300_step.py'),
  'none','none','Dimensions from onsemi NOIP1SN1300A-D Figure 50 / Table 32 / Table 33');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));
ENDSEC;
DATA;"""
open(OUT, "w", encoding="utf-8").write(hdr + "\n" + "\n".join(s.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")
print("wrote", OUT)
print("entities:", s.n, "| solids:", len(solids))
