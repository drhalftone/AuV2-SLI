#!/usr/bin/env python3
"""Validate a STEP file written by the generators here, without a CAD kernel.

A STEP file can be perfectly well-formed text and still describe a solid no kernel will
accept -- an unclosed shell, an edge used once, a face whose loop winds the wrong way so
the body is inside-out. This reads the file back and checks the topology and the geometry
independently of the code that wrote it:

  * every edge is used exactly twice per solid, once forward and once reversed
    (the definition of a closed 2-manifold),
  * the Euler characteristic V - E + F is even and non-positive-genus, and the implied
    genus is reported -- 0 for a plain prism, 1 for something with a hole through it,
  * every face loop winds counter-clockwise about the outward normal its PLANE declares,
    and every inner bound winds the other way (which is what makes it a hole and not a
    second disjoint region),
  * the enclosed volume, integrated over the faces by the divergence theorem, is positive
    and matches the volume computed analytically from the design dimensions.

The last one is the check that catches an inside-out solid, which every purely topological
test passes.

    python check_step.py [file.step]        # defaults to the sensor model
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------

def parse(path):
    text = open(path, encoding="utf-8").read()
    data = text.split("DATA;", 1)[1].rsplit("ENDSEC;", 1)[0]
    entities = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Z_0-9]*)\s*\((.*?)\)\s*;\s*(?=#|$)", data, re.S):
        entities[int(m.group(1))] = (m.group(2), m.group(3))
    return text, entities


def split_args(s):
    """Split a STEP argument list on top-level commas."""
    out, depth, cur, quote = [], 0, [], False
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out


def refs(s):
    return [int(x) for x in re.findall(r"#(\d+)", s)]


class Model:
    def __init__(self, entities):
        self.e = entities

    def args(self, ref):
        return split_args(self.e[ref][1])

    def point(self, ref):
        return tuple(float(v) for v in split_args(self.args(ref)[1].strip("()")))

    def vertex(self, ref):
        return self.point(refs(self.args(ref)[1])[0])

    def direction(self, ref):
        return tuple(float(v) for v in split_args(self.args(ref)[1].strip("()")))

    def loop_points(self, loop):
        """Ordered points of an EDGE_LOOP, plus the (edge, orientation) pairs it uses."""
        assert self.e[loop][0] == "EDGE_LOOP", self.e[loop]
        pts, used = [], []
        for oe in refs(self.args(loop)[1]):
            assert self.e[oe][0] == "ORIENTED_EDGE", self.e[oe]
            oa = self.args(oe)
            curve = refs(oa[3])[0]
            fwd = oa[4].strip() == ".T."
            ca = self.args(curve)
            v0, v1 = refs(ca[1])[0], refs(ca[2])[0]
            pts.append(self.vertex(v0 if fwd else v1))
            used.append((curve, fwd))
        return pts, used

    def face(self, ref):
        """Return (outer points, [inner loops], declared normal, [(edge, orientation)])."""
        a = self.args(ref)
        outer, inners, used = None, [], []
        for bound in refs(a[1]):
            kind = self.e[bound][0]
            assert kind in ("FACE_OUTER_BOUND", "FACE_BOUND"), self.e[bound]
            pts, u = self.loop_points(refs(self.args(bound)[1])[0])
            used += u
            if kind == "FACE_OUTER_BOUND":
                outer = pts
            else:
                inners.append(pts)
        plane = refs(a[2])[0]
        axis = refs(self.args(plane)[1])[0]
        normal = self.direction(refs(self.args(axis)[2])[0])
        return outer, inners, normal, used

    def solids(self):
        out = []
        for ref, (kind, body) in sorted(self.e.items()):
            if kind == "MANIFOLD_SOLID_BREP":
                a = split_args(body)
                out.append((a[0].strip("'"), refs(self.args(refs(a[1])[0])[1])))
        return out


# --------------------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------------------

def newell(pts):
    n = [0.0, 0.0, 0.0]
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n[0] += a[1] * b[2] - a[2] * b[1]
        n[1] += a[2] * b[0] - a[0] * b[2]
        n[2] += a[0] * b[1] - a[1] * b[0]
    m = math.sqrt(sum(c * c for c in n))
    return tuple(c / m for c in n) if m > 1e-12 else (0.0, 0.0, 0.0)


def loop_volume(pts):
    """A planar loop's contribution to the enclosed volume (divergence theorem).

    For a planar loop this evaluates to d * A / 3, with d the plane's distance from the
    origin and A the loop's signed area about the plane normal -- so an inner bound, which
    winds the other way, contributes its area negatively and the holes subtract themselves.
    """
    total = 0.0
    p0 = pts[0]
    for i in range(1, len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        total += (p0[0] * (a[1] * b[2] - a[2] * b[1])
                  - p0[1] * (a[0] * b[2] - a[2] * b[0])
                  + p0[2] * (a[0] * b[1] - a[1] * b[0])) / 6.0
    return total


def dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


# --------------------------------------------------------------------------------------

def validate(path, expected=None, out=sys.stdout):
    """Structurally validate a STEP file. `expected` maps solid name -> volume in mm3."""
    expected = expected or {}
    text, entities = parse(path)
    if not text.startswith("ISO-10303-21;") or not text.rstrip().endswith("END-ISO-10303-21;"):
        print("FAIL: missing ISO-10303-21 wrapper", file=out)
        return False

    model = Model(entities)
    solids = model.solids()
    if not solids:
        print("FAIL: no MANIFOLD_SOLID_BREP found", file=out)
        return False

    print("%-20s %6s %6s %6s %6s   %-12s %-12s"
          % ("solid", "V", "E", "F", "genus", "volume mm3", "expected"), file=out)
    ok = True
    for name, faces in solids:
        verts, edge_use, vol = set(), {}, 0.0
        for f in faces:
            outer, inners, normal, used = model.face(f)
            if outer is None:
                print("  FAIL %s: face #%d has no outer bound" % (name, f), file=out)
                ok = False
                continue
            for loop in [outer] + inners:
                for p in loop:
                    verts.add(tuple(round(c, 9) for c in p))
                vol += loop_volume(loop)
            for curve, fwd in used:
                edge_use.setdefault(curve, []).append(fwd)
            if dot(newell(outer), normal) < 0.999:
                print("  FAIL %s: face #%d outer loop winds against its declared normal"
                      % (name, f), file=out)
                ok = False
            for h in inners:
                if dot(newell(h), normal) > -0.999:
                    print("  FAIL %s: face #%d inner bound does not wind opposite the "
                          "outer loop -- it will read as a separate region, not a hole"
                          % (name, f), file=out)
                    ok = False

        bad = {c: u for c, u in edge_use.items() if sorted(u) != [False, True]}
        if bad:
            print("  FAIL %s: %d edges are not used exactly once in each direction "
                  "(e.g. #%d used %s)" % (name, len(bad), *list(bad.items())[0]), file=out)
            ok = False

        V, E, F = len(verts), len(edge_use), len(faces)
        chi = V - E + F
        genus = (2 - chi) / 2.0
        exp = expected.get(name)
        flag = ""
        if chi % 2 or genus < 0:
            flag += "  <- FAIL V-E+F=%d implies genus %s" % (chi, genus)
            ok = False
        if vol <= 0:
            flag += "  <- FAIL inside-out (negative volume)"
            ok = False
        if exp is not None and abs(vol - exp) > 1e-6 * max(1.0, abs(exp)):
            flag += "  <- FAIL volume mismatch"
            ok = False
        print("%-20s %6d %6d %6d %6g   %-12.5f %-12s%s"
              % (name, V, E, F, genus, vol, ("%.5f" % exp) if exp is not None else "-", flag),
              file=out)

    print(file=out)
    print("PASS -- closed, manifold, outward-oriented" if ok else "FAILED", file=out)
    return ok


def sensor_expected(tol="typ"):
    import gen_sensor_step as gen
    half = gen.BODY_XY[tol] / 2.0
    die_top = gen.DIE_TOP_Z[tol]
    glass_top = die_top + gen.DIE_TOP_TO_GLASS_TOP[tol]
    glass_bot = glass_top - gen.GLASS_T[tol]
    outline = gen.package_outline(half, gen.pin_ring())
    aw = gen.ACTIVE_PIXELS[0] * gen.PIXEL_PITCH
    ah = gen.ACTIVE_PIXELS[1] * gen.PIXEL_PITCH
    return {
        "ceramic_body": abs(sw.signed_area(outline)) * glass_bot,
        "glass_lid": gen.GLASS_XY ** 2 * gen.GLASS_T[tol],
        "die_REF": gen.DIE_XY[0] * gen.DIE_XY[1] * gen.DIE_T,
        "active_area_REF": aw * ah * 0.02,
    }


def main():
    default = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "3dmodels", "NOIP1SN1300A_LCC48.step")
    path = sys.argv[1] if len(sys.argv) > 1 else default
    expected = sensor_expected() if os.path.abspath(path) == os.path.abspath(default) else None
    return 0 if validate(path, expected) else 1


if __name__ == "__main__":
    sys.exit(main())
