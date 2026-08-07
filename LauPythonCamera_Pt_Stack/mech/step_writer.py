#!/usr/bin/env python3
"""A small AP214 STEP writer, plus the 2D helpers the mechanical generators share.

Only what these models actually need: solids formed by extruding a closed outline between
two Z planes, optionally with holes through them. Every face is planar, so the whole thing
is a few hundred lines and has no third-party dependency -- which matters because the
alternative is a 500 MB CAD kernel to draw what are, geometrically, prisms.

Conventions
-----------
Outlines are lists of (x, y), closed implicitly, with no repeated final point. An outer
outline runs **counter-clockwise**; a hole runs **clockwise**. `prism` normalises the
outer outline for you, but hole winding is load-bearing -- see `planar_face`.
"""

import math

__all__ = ["StepFile", "signed_area", "dedupe", "arc", "rect", "rounded_rect", "circle"]


# --------------------------------------------------------------------------------------
# 2D helpers
# --------------------------------------------------------------------------------------

def signed_area(pts):
    return 0.5 * sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                     pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))


def dedupe(pts, tol=1e-9):
    """Drop consecutive duplicate points, including a final point equal to the first."""
    out = []
    for p in pts:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def arc(cx, cy, r, a0, a1, segments):
    return [(cx + r * math.cos(a0 + (a1 - a0) * i / segments),
             cy + r * math.sin(a0 + (a1 - a0) * i / segments))
            for i in range(segments + 1)]


def rect(cx, cy, w, h):
    """Counter-clockwise rectangle."""
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
            (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def rounded_rect(cx, cy, w, h, r, segments=6):
    """Counter-clockwise rectangle with equal corner radii."""
    if r <= 0:
        return rect(cx, cy, w, h)
    r = min(r, w / 2, h / 2)
    x0, x1 = cx - w / 2 + r, cx + w / 2 - r
    y0, y1 = cy - h / 2 + r, cy + h / 2 - r
    pts = []
    pts += arc(x1, y0, r, -math.pi / 2, 0.0, segments)
    pts += arc(x1, y1, r, 0.0, math.pi / 2, segments)
    pts += arc(x0, y1, r, math.pi / 2, math.pi, segments)
    pts += arc(x0, y0, r, math.pi, 1.5 * math.pi, segments)
    return dedupe(pts)


def circle(cx, cy, r, segments=32):
    """Counter-clockwise regular polygon inscribing... circumscribing nothing: the
    vertices lie ON the circle, so a printed pin is very slightly undersize. Small, and
    on the safe side for something that has to enter a hole."""
    return [(cx + r * math.cos(2 * math.pi * i / segments),
             cy + r * math.sin(2 * math.pi * i / segments)) for i in range(segments)]


def reverse(pts):
    return list(reversed(pts))


# --------------------------------------------------------------------------------------
# STEP
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


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


class StepFile:
    def __init__(self, product, description, timestamp, author="AuV2-SLI",
                 org="University of Kentucky", tool="mech/step_writer.py"):
        self.product = product
        self.description = description
        self.timestamp = timestamp
        self.author = author
        self.org = org
        self.tool = tool
        self._lines = []
        self._n = 0
        self._points = {}
        self._dirs = {}
        self._verts = {}
        self._edges = {}
        self.solids = []      # (entity id, name, rgb)

    @property
    def entity_count(self):
        return self._n

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
        (_, ka), (_, kb) = a, b
        key = (min(ka, kb), max(ka, kb))
        if key not in self._edges:
            p0, p1 = key
            d = [p1[i] - p0[i] for i in range(3)]
            n = math.sqrt(sum(c * c for c in d))
            vec = self._e("VECTOR('',#%d,%s)" % (self.direction(*[c / n for c in d]), _r(1.0)))
            line = self._e("LINE('',#%d,#%d)" % (self.point(*p0), vec))
            self._edges[key] = self._e("EDGE_CURVE('',#%d,#%d,#%d,.T.)"
                                       % (self._verts[p0], self._verts[p1], line))
        return self._e("ORIENTED_EDGE('',*,*,#%d,%s)"
                       % (self._edges[key], ".T." if ka == key[0] else ".F."))

    def _loop(self, pts):
        verts = [self.vertex(*p) for p in pts]
        edges = [self.oriented_edge(verts[i], verts[(i + 1) % len(verts)])
                 for i in range(len(verts))]
        return self._e("EDGE_LOOP('',(%s))" % ",".join("#%d" % e for e in edges))

    def planar_face(self, loop_pts, normal, inner_loops=()):
        """One planar face.

        `loop_pts` must run counter-clockwise seen from `normal`; each entry of
        `inner_loops` must run the other way, which is what tells a CAD kernel it is a
        hole rather than a second disjoint region.
        """
        bounds = ["#%d" % self._e("FACE_OUTER_BOUND('',#%d,.T.)" % self._loop(loop_pts))]
        for hole in inner_loops:
            bounds.append("#%d" % self._e("FACE_BOUND('',#%d,.T.)" % self._loop(hole)))
        # Any unit vector perpendicular to the normal will do for the plane's X axis.
        ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (1.0, 0.0, 0.0)
        ref = _cross(normal, ref)
        n = math.sqrt(sum(c * c for c in ref))
        ref = tuple(c / n for c in ref)
        plane = self._e("PLANE('',#%d)" % self.axis2(loop_pts[0], normal, ref))
        return self._e("ADVANCED_FACE('',(%s),#%d,.T.)" % (",".join(bounds), plane))

    def _walls(self, pts, z0, z1, faces):
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

    def prism(self, outline, z0, z1, name, rgb, holes=()):
        """Extrude a closed outline between two Z planes, optionally with holes through it.

        `outline` is normalised to counter-clockwise; each hole is normalised to clockwise.
        Holes must lie inside the outline and not touch it or each other.
        """
        pts = list(outline)
        if signed_area(pts) < 0:
            pts = reverse(pts)
        hs = []
        for h in holes:
            h = list(h)
            if signed_area(h) > 0:
                h = reverse(h)
            hs.append(h)

        faces = [
            self.planar_face([(x, y, z1) for x, y in pts], (0.0, 0.0, 1.0),
                             [[(x, y, z1) for x, y in h] for h in hs]),
            self.planar_face([(x, y, z0) for x, y in reverse(pts)], (0.0, 0.0, -1.0),
                             [[(x, y, z0) for x, y in reverse(h)] for h in hs]),
        ]
        self._walls(pts, z0, z1, faces)
        for h in hs:
            self._walls(h, z0, z1, faces)

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
            % (self.product, self.timestamp, self.author, self.org, self.tool, self.tool),
            "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
            "ENDSEC;",
            "DATA;",
        ]
        return "\n".join(header + self._lines + ["ENDSEC;", "END-ISO-10303-21;", ""])
