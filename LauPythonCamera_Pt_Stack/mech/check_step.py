#!/usr/bin/env python3
"""Validate a STEP file written by gen_sensor_step.py, without a CAD kernel.

A STEP file can be perfectly well-formed text and still describe a solid no kernel will
accept -- an unclosed shell, an edge used once, a face whose loop winds the wrong way so
the body is inside-out. This reads the file back and checks the topology and the geometry
independently of the code that wrote it:

  * every edge is used exactly twice per solid, once forward and once reversed
    (the definition of a closed 2-manifold),
  * Euler's formula V - E + F = 2 holds for each solid,
  * each face's loop winds counter-clockwise about the outward normal its PLANE declares,
  * the enclosed volume, integrated over the faces by the divergence theorem, is positive
    and matches the volume computed analytically from the datasheet dimensions.

The last one is the check that catches an inside-out solid, which every purely topological
test passes.

    python check_step.py [file.step]
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_sensor_step as gen


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


# --------------------------------------------------------------------------------------
# Topology walk
# --------------------------------------------------------------------------------------

class Model:
    def __init__(self, entities):
        self.e = entities

    def args(self, ref):
        return split_args(self.e[ref][1])

    def point(self, ref):
        assert self.e[ref][0] == "CARTESIAN_POINT", self.e[ref]
        return tuple(float(v) for v in split_args(self.args(ref)[1].strip("()")))

    def vertex(self, ref):
        return self.point(refs(self.args(ref)[1])[0])

    def direction(self, ref):
        return tuple(float(v) for v in split_args(self.args(ref)[1].strip("()")))

    def face_loop(self, face):
        """Return (ordered points, declared outward normal, [edge_curve, orientation])."""
        a = self.args(face)
        bound = refs(a[1])[0]
        plane = refs(a[2])[0]
        assert self.e[bound][0] == "FACE_OUTER_BOUND", self.e[bound]
        loop = refs(self.args(bound)[1])[0]
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

        axis = refs(self.args(plane)[1])[0]
        normal = self.direction(refs(self.args(axis)[2])[0])
        return pts, normal, used

    def solids(self):
        out = []
        for ref, (kind, body) in sorted(self.e.items()):
            if kind == "MANIFOLD_SOLID_BREP":
                a = split_args(body)
                name = a[0].strip("'")
                shell = refs(a[1])[0]
                faces = refs(self.args(shell)[1])
                out.append((name, faces))
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


def face_volume(pts):
    """Contribution of one planar face to the enclosed volume (divergence theorem)."""
    total = 0.0
    p0 = pts[0]
    for i in range(1, len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        total += (p0[0] * (a[1] * b[2] - a[2] * b[1])
                  - p0[1] * (a[0] * b[2] - a[2] * b[0])
                  + p0[2] * (a[0] * b[1] - a[1] * b[0])) / 6.0
    return total


# --------------------------------------------------------------------------------------

def expected_volumes(tol="typ"):
    half = gen.BODY_XY[tol] / 2.0
    die_top = gen.DIE_TOP_Z[tol]
    glass_top = die_top + gen.DIE_TOP_TO_GLASS_TOP[tol]
    glass_bot = glass_top - gen.GLASS_T[tol]
    ring = gen.pin_ring()
    outline = gen.package_outline(half, ring)
    aw = gen.ACTIVE_PIXELS[0] * gen.PIXEL_PITCH
    ah = gen.ACTIVE_PIXELS[1] * gen.PIXEL_PITCH
    return {
        "ceramic_body": abs(gen._signed_area(outline)) * glass_bot,
        "glass_lid": gen.GLASS_XY ** 2 * gen.GLASS_T[tol],
        "die_REF": gen.DIE_XY[0] * gen.DIE_XY[1] * gen.DIE_T,
        "active_area_REF": aw * ah * 0.02,
    }


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "3dmodels", "NOIP1SN1300A_LCC48.step")

    text, entities = parse(path)
    if not text.startswith("ISO-10303-21;") or not text.rstrip().endswith("END-ISO-10303-21;"):
        raise SystemExit("FAIL: missing ISO-10303-21 wrapper")

    model = Model(entities)
    solids = model.solids()
    if not solids:
        raise SystemExit("FAIL: no MANIFOLD_SOLID_BREP found")

    want = expected_volumes()
    print("%-18s %6s %6s %6s   %-12s %-12s" % ("solid", "V", "E", "F", "volume mm3", "expected"))
    ok = True
    for name, faces in solids:
        verts, edge_use, vol = set(), {}, 0.0
        for f in faces:
            pts, normal, used = model.face_loop(f)
            for p in pts:
                verts.add(tuple(round(c, 9) for c in p))
            for curve, fwd in used:
                edge_use.setdefault(curve, []).append(fwd)
            got = newell(pts)
            if sum(got[i] * normal[i] for i in range(3)) < 0.999:
                print("  FAIL %s: face #%d loop winds against its declared normal "
                      "%s (loop gives %s)" % (name, f, normal, tuple(round(c, 3) for c in got)))
                ok = False
            vol += face_volume(pts)

        bad = {c: u for c, u in edge_use.items() if sorted(u) != [False, True]}
        if bad:
            print("  FAIL %s: %d edges are not used exactly once in each direction "
                  "(e.g. #%d used %s)" % (name, len(bad), *list(bad.items())[0]))
            ok = False

        V, E, F = len(verts), len(edge_use), len(faces)
        euler = V - E + F
        exp = want.get(name)
        flag = ""
        if euler != 2:
            flag += "  <- FAIL Euler V-E+F=%d, expected 2" % euler
            ok = False
        if vol <= 0:
            flag += "  <- FAIL inside-out (negative volume)"
            ok = False
        if exp is not None and abs(vol - exp) > 1e-6 * max(1.0, exp):
            flag += "  <- FAIL volume mismatch"
            ok = False
        print("%-18s %6d %6d %6d   %-12.5f %-12s%s"
              % (name, V, E, F, vol, ("%.5f" % exp) if exp else "-", flag))

    print()
    print("PASS -- closed, manifold, outward-oriented" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
