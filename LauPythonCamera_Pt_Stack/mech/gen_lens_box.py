"""gen_lens_box.py -- C-mount lens box for LauPythonCamera_Pt_Stack.

Writes ../3dmodels/camera_lens_box.step (+ .stl).

    z = 0       the PCB TOP SURFACE
    +X / +Y     the KiCad top view, origin at U1, +y UP

WHAT THIS IS. An open-bottomed box that sits over the PCB. Four walls stand on
the board, a top face spans them, and a plain round hole in that top face passes
the C-mount lens barrel WITHOUT touching it. The lens is not threaded into
anything: its flange shoulder rests on the top face and gravity holds it there,
with the board lying flat.

THE TOP FACE IS THE OPTICAL DATUM. C-mount flange focal distance is 17.526 mm
from the lens's mounting shoulder to the image plane, so:

    top surface z = image plane z + 17.526

That single surface sets focus. Its height is the one dimension on this part
that has to be right; everything else is clearance. Print/machine it flat and do
not sand it.

The bore is a THREAD-IN hole: 1"-32 UN has a 25.4 mm major diameter and the
bore is that plus --bore-clear (-0.10 -> 25.3 mm), so the lens screws into the
plastic and cuts its own thread, and the shoulder still lands on the top face.
The number came off a printed test plate (gen_bore_test_plate.py, 2026-10):
25.2 threads, 25.3 is the better fit. +0.80 restores the old 26.2 clearance hole.

EVERY GEOMETRIC NUMBER IS READ FROM THE BOARD. The outline, the four corner
holes, U1 and every component courtyard come from ../LauPythonCamera_Pt_Stack.kicad_pcb
on every run. The script FAILS rather than warns if a wall lands on a component
or the top face is too low. A box that fouls a 0402 is discovered with a scalpel.
"""
import argparse, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
from gen_lens_holder import read_pcb, body_height, OPTICAL_CENTER, \
                            DIE_TOP_Z_TYP, GLASS_TOP_TYP

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "camera_lens_box.step")
PCB = os.path.join(HERE, "..", "LauPythonCamera_Pt_Stack.kicad_pcb")

# --- C-mount, ISO 10935 / JIS B 7141 -----------------------------------------
C_FLANGE_FOCAL = 17.526      # mounting shoulder -> image plane, mm. THE datum.
C_THREAD_OD    = 25.4        # 1"-32 UN-2A major diameter
C_SHOULDER_OD  = 32.0        # typical flange shoulder; the seat must exceed this

COLORS = {"box": (0.32, 0.34, 0.38), "boss": (0.28, 0.30, 0.33)}
EPS = 1e-9


def circle_phase(cx, cy, r, n, phase):
    """A circle whose first vertex is rotated by `phase`.

    step_writer.bridge_holes threads each hole into the outer loop by casting a
    +x ray from the hole's RIGHTMOST VERTEX. With sw.circle that vertex sits
    exactly at the centre's y, so the two left screw pockets cast rays at exactly
    the same height as the two right ones -- straight through a pocket that has
    already been merged. The keyhole then bridges to a vertex it cannot see and
    the cap ear-clips into a non-watertight mess (the STL check catches it).

    A per-hole phase moves each rightmost vertex a hair off centre-y, so no two
    rays share a height. The circle is geometrically identical; only which vertex
    is 'first' changes.
    """
    return [(cx + r * math.cos(2.0 * math.pi * i / n + phase),
             cy + r * math.sin(2.0 * math.pi * i / n + phase)) for i in range(n)]


def board_polygon(pcb, u1x, u1y):
    """The board's REAL outline, chained into one ordered CCW loop in model coords.

    Everything else in this file has only ever used the bounding box, and for
    this board that is wrong in a way that mattered: there is a 29 x 5.5 mm
    NOTCH cut into the east edge -- 164 mm2 of missing board -- so the Pt's LED
    array shines straight up through it. A wall built on the bounding box caps
    1.4 mm of that notch and leaves the other 4.1 x 26 mm wide open into the
    optical cavity, which is most of the light this box was trying to keep out.
    """
    s = open(pcb, encoding="utf-8", errors="replace").read()
    segs = []
    for m in re.finditer(r'\(gr_line\b(.*?)\(layer "Edge\.Cuts"', s, re.S):
        p = re.findall(r'\((?:start|end)\s+([-\d.]+)\s+([-\d.]+)\)', m.group(1))
        if len(p) == 2:
            segs.append(tuple((float(a), float(b)) for a, b in p))
    if not segs:
        sys.exit("no Edge.Cuts lines in %s" % pcb)
    loop, used = [segs[0][0], segs[0][1]], {0}
    while len(used) < len(segs):
        for i, (a, b) in enumerate(segs):
            if i in used:
                continue
            if abs(a[0] - loop[-1][0]) < 1e-6 and abs(a[1] - loop[-1][1]) < 1e-6:
                loop.append(b); used.add(i); break
            if abs(b[0] - loop[-1][0]) < 1e-6 and abs(b[1] - loop[-1][1]) < 1e-6:
                loop.append(a); used.add(i); break
        else:
            break
    if len(used) != len(segs):
        sys.exit("Edge.Cuts does not chain into ONE closed loop: %d of %d segments "
                 "used.\nA second loop means a cutout this code would silently ignore."
                 % (len(used), len(segs)))
    if abs(loop[0][0] - loop[-1][0]) < 1e-6 and abs(loop[0][1] - loop[-1][1]) < 1e-6:
        loop.pop()
    poly = [(x - u1x, u1y - y) for x, y in loop]
    return poly if sw.signed_area(poly) > 0 else list(reversed(poly))


def offset_polygon(poly, dists):
    """Inward offset of a CCW polygon, each edge by its OWN distance.

    Per-edge and not uniform, because the board does not allow uniform: R14 and
    R1 sit 0.47 mm off the notch's inner wall, so that one 26 mm edge can only be
    covered by 0.07 mm while every other edge has 1.4 mm or more to give. A
    single figure would either foul two 0402s or throw away the overlap
    everywhere else.

    Vertices are the intersections of consecutive offset lines, so a corner
    between two different offsets simply lands where the two faces meet.
    """
    n = len(poly)
    lines = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            sys.exit("offset_polygon: zero-length edge %d" % i)
        nx, ny = -dy / L, dx / L                 # CCW -> interior is to the LEFT
        lines.append((a[0] + nx * dists[i], a[1] + ny * dists[i], dx / L, dy / L))
    out = []
    for i in range(n):
        px, py, ux, uy = lines[i - 1]
        qx, qy, vx, vy = lines[i]
        den = ux * vy - uy * vx
        if abs(den) < 1e-12:                     # collinear: faces already meet
            out.append((qx, qy))
            continue
        t = ((qx - px) * vy - (qy - py) * vx) / den
        out.append((px + ux * t, py + uy * t))
    return out


def east_notch(contour):
    """The NOTCH in the board's east edge as its own CCW polygon (model coords).

    It is the run of outline vertices that leaves the east edge of the bounding
    box and comes back to it, closed by the straight line the board would have
    had. Exactly one such run is expected; anything else exits rather than plug
    the wrong hole.
    """
    xe = max(p[0] for p in contour)
    n = len(contour)
    on = [abs(p[0] - xe) < 1e-6 for p in contour]
    runs = []
    for i in range(n):
        if on[i] and not on[(i + 1) % n]:          # leaves the east edge here
            j, run = (i + 1) % n, [contour[i]]
            while not on[j]:
                run.append(contour[j]); j = (j + 1) % n
            run.append(contour[j])
            runs.append(run)
    # The corner chamfers also leave the east edge, but run on round the board
    # onto the north/south edges; the notch never touches those.
    y0, y1 = min(p[1] for p in contour), max(p[1] for p in contour)
    runs = [r for r in runs if all(y0 + 1e-6 < p[1] < y1 - 1e-6 for p in r)]
    if len(runs) != 1:
        sys.exit("east_notch: expected ONE notch in the east edge, found %d" % len(runs))
    poly = list(reversed(runs[0]))                 # board CCW -> notch CCW
    return poly if sw.signed_area(poly) > 0 else list(reversed(poly))


def union_circle(poly, cx, cy, r, seg=24):
    """Union a CCW polygon with a circle that crosses its boundary exactly twice.

    The rectangle-only version of this was analytic and assumed the corner was
    one vertex between two axis-aligned edges. The real outline has chamfers, so
    a boss can straddle three edges and the analytic form does not apply. This
    finds the crossings, works out which side of the boundary is inside the
    circle, and swaps that stretch for the outward arc.
    """
    n = len(poly)
    hits = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        fx, fy = a[0] - cx, a[1] - cy
        A, B, C = dx * dx + dy * dy, 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
        disc = B * B - 4 * A * C
        if disc <= 1e-12:
            continue
        sq = math.sqrt(disc)
        for t in ((-B - sq) / (2 * A), (-B + sq) / (2 * A)):
            if 1e-9 < t < 1 - 1e-9:
                hits.append((i, t, (a[0] + t * dx, a[1] + t * dy)))
    if len(hits) != 2:
        return None
    hits.sort(key=lambda h: (h[0], h[1]))
    (i1, _, p1), (i2, _, p2) = hits
    fwd = [poly[k % n] for k in range(i1 + 1, i2 + 1)]
    inside = all((v[0] - cx) ** 2 + (v[1] - cy) ** 2 < r * r for v in fwd)
    if inside:                                   # p1 -> p2 forward is buried
        start, end, keep = p1, p2, [poly[k % n] for k in range(i2 + 1, i1 + 1 + n)]
    else:                                        # the other stretch is
        start, end, keep = p2, p1, [poly[k % n] for k in range(i1 + 1, i2 + 1)]
    a1 = math.atan2(start[1] - cy, start[0] - cx)
    a2 = math.atan2(end[1] - cy, end[0] - cx)
    return sw.dedupe(sw.arc(cx, cy, r, a1, a1 + ((a2 - a1) % (2.0 * math.pi)), seg) + keep)


def aperture_notched(cx, cy, w, h, bosses, r, seg=10):
    """A rectangle whose four corners are pushed OUT around each boss, CCW.

    The thick wall's inner face wants to sit inboard of the board edge, but that
    puts it straight through the screw-head pockets: each pocket's centre is
    inside the rectangle while the circle reaches past BOTH edges near the
    corner. Cutting it as a hole is not an option -- a hole that crosses its own
    outline is not a hole, and prism() would produce a solid that is not
    watertight rather than an error.

    So the aperture is the UNION of the rectangle with a circle at each boss.
    The pocket then lies wholly in the void and needs no hole at all, and the
    wall stays a CONNECTED ring with material still outboard of every pocket --
    which four separate bars, the obvious alternative, would not.

    Light is not lost at the notches: the washer (z 0 -> washer_t) and the column
    above it are the same diameter and fill exactly that region, sealing down
    onto the board at each corner.
    """
    hw, hh = w / 2.0, h / 2.0
    x0, x1, y0, y1 = cx - hw, cx + hw, cy - hh, cy + hh

    def boss(sx, sy):
        for b in bosses:                      # (x, y) or (x, y, r) -- only xy matters
            px, py = b[0], b[1]
            if (px - cx) * sx > 0 and (py - cy) * sy > 0:
                return px, py
        sys.exit("aperture_notched: no boss in quadrant (%+d, %+d)" % (sx, sy))

    def leg(a):                       # half-chord of the circle at offset a
        if abs(a) >= r:
            sys.exit("aperture_notched: boss is %.2f mm from the aperture edge but "
                     "its radius is only %.2f -- it no longer straddles the corner, "
                     "so the notch is unnecessary. Reduce --overlap." % (abs(a), r))
        return math.sqrt(r * r - a * a)

    # CCW: ...S edge -> BR -> E edge -> TR -> N edge -> TL -> W edge -> BL -> ...
    # Each corner contributes the arc from where the incoming edge meets its
    # circle to where the outgoing edge does; the straight runs are implied by
    # joining one corner's exit to the next corner's entry.
    corners = []
    nx, ny = boss(+1, -1)                                            # BR
    corners.append(((nx - leg(ny - y0), y0), (x1, ny + leg(x1 - nx)), (nx, ny)))
    nx, ny = boss(+1, +1)                                            # TR
    corners.append(((x1, ny - leg(x1 - nx)), (nx - leg(y1 - ny), y1), (nx, ny)))
    # Leaving TL we head DOWN the W edge, so the exit is BELOW the notch centre;
    # arriving at BL we are still heading down, so the entry is ABOVE it. Getting
    # either the wrong way round runs the arc the long way and closes the polygon
    # back over the pocket -- which reads as a plausible aperture and blocks two
    # of the four screws.
    nx, ny = boss(-1, +1)                                            # TL
    corners.append(((nx + leg(y1 - ny), y1), (x0, ny - leg(nx - x0)), (nx, ny)))
    nx, ny = boss(-1, -1)                                            # BL
    corners.append(((x0, ny + leg(nx - x0)), (nx + leg(ny - y0), y0), (nx, ny)))

    pts = []
    for (ex, ey), (fx, fy), (nx, ny) in corners:
        a1 = math.atan2(ey - ny, ex - nx)
        a2 = math.atan2(fy - ny, fx - nx)
        sweep = (a2 - a1) % (2.0 * math.pi)
        pts += sw.arc(nx, ny, r, a1, a1 + sweep, seg)
    return sw.dedupe(pts)


def seam_profile(cx, cy, out_w, out_h, wall, tongue_w, clear, corner_r, seg=6):
    """The tongue-and-groove at the mating plane, defined ONCE for both halves.

    gen_base_box.py imports this rather than re-deriving it. Two files computing
    the same joint from the same formula is exactly the arrangement that drifts
    the first time one of them is edited, and a tongue 0.2 mm proud of its groove
    is not visible in either STEP on its own.

    The tongue is CENTRED in the wall, so the material either side of the groove
    is equal. Each profile's corner radius is the outer radius less that
    profile's inset from the outer face, which keeps the groove concentric with
    the wall and the leg thickness uniform right around the corners -- a square
    joint inside a rounded wall pinches to a third of its width at the corners.

    Returns (tongue_outer, tongue_inner, groove_outer, groove_inner).
    """
    off = (wall - tongue_w) / 2.0
    def prof(inset):
        return sw.rounded_rect(cx, cy, out_w - 2 * inset, out_h - 2 * inset,
                               max(corner_r - inset, 0.2), seg)
    return (prof(off), prof(off + tongue_w),
            prof(off - clear), prof(off + tongue_w + clear))


def parse_port_notch(text):
    """--port-notch FACE:LO:HI:ZTOP[:R] -> dict. LO/HI run along the face (y for
    E/W) in the model frame; the notch runs up from the wall bottom to ZTOP, its
    two top corners rounded to R (default 2.0)."""
    parts = text.split(":")
    if len(parts) not in (4, 5) or parts[0].upper() not in ("E", "W"):
        sys.exit("--port-notch wants FACE:LO:HI:ZTOP[:R] with FACE E or W, got %r" % text)
    try:
        nums = [float(v) for v in parts[1:]]
    except ValueError:
        sys.exit("--port-notch %r: LO/HI/ZTOP/R must be numbers" % text)
    lo, hi, zt = nums[:3]
    r = nums[3] if len(nums) == 4 else 2.0
    if hi - lo < 2 * r:
        sys.exit("--port-notch %r: %.2f wide cannot carry two R%.2f corners" % (text, hi - lo, r))
    return dict(face=parts[0].upper(), lo=lo, hi=hi, z_top=zt, r=r, spec=text)


def notch_profile(notches, ya, yb, z0, z1, flare, step_mm=0.1):
    """The wall's outline, seen face-on, with every notch on that face taken out.

    Local (u, v) = (along the face, z). The block is [ya, yb] x [z0, z1]; each
    notch removes a U opening up from z0 with its two top corners rounded to its
    R, and its two lower corners -- where the slot meets the wall's bottom edge --
    rounded to `flare` so there is no sharp edge for a cable to catch on. Notches
    may overlap; the opening is their UNION, so a deep notch beside a shallow one
    makes one stepped opening with rounded corners throughout.

    Returned counter-clockwise, as step_writer wants.
    """
    def body(n, u):
        """Height of this notch's own opening at u (no flare), or None."""
        lo, hi, zt, r = n["lo"], n["hi"], n["z_top"], n["r"]
        if not lo <= u <= hi:
            return None
        if u < lo + r:
            return zt - r + math.sqrt(max(r * r - (lo + r - u) ** 2, 0.0))
        if u > hi - r:
            return zt - r + math.sqrt(max(r * r - (u - hi + r) ** 2, 0.0))
        return zt

    def base(n, side):
        """What a notch's side stands on just outside it: the wall bottom, or the
        floor of a neighbouring notch it rises out of."""
        u = n["lo"] - 1e-3 if side < 0 else n["hi"] + 1e-3
        return max([z0] + [h for h in (body(m, u) for m in notches if m is not n)
                           if h is not None])

    def top(n, u):
        """Height of this notch's opening at u, flares included, or None."""
        h = body(n, u)
        if h is not None:
            return h
        # Outside a side: round the convex corner where the side meets whatever
        # it stands on -- the wall bottom edge, or a shallower notch's floor. The
        # radius is capped at the straight part of the side, or the flare would
        # climb past where the top corner's arc begins.
        for side, edge in ((-1, n["lo"]), (1, n["hi"])):
            b = base(n, side)
            f = min(flare, n["z_top"] - n["r"] - b)
            if f > 1e-6 and 0 < (edge - u) * -side <= f:
                d = abs(u - edge)                      # distance out from the side
                return b + f - math.sqrt(max(f * f - (f - d) ** 2, 0.0))
        return None

    def bottom(u):
        hs = [h for h in (top(n, u) for n in notches) if h is not None]
        return max([z0] + hs)

    # Sample finely, and put two points at every side -- just inside and just
    # outside -- so the vertical side of each notch comes out vertical.
    us = set()
    k = 0
    while ya + k * step_mm < yb:
        us.add(round(ya + k * step_mm, 6)); k += 1
    us.add(yb)
    eps = 1e-4
    for n in notches:
        for e in (n["lo"], n["hi"]):
            us.update((e - eps, e + eps))
    edge = [(u, bottom(u)) for u in sorted(us) if ya <= u <= yb]
    # drop collinear runs along the flat bottom and flat tops
    pts = [edge[0]]
    for i in range(1, len(edge) - 1):
        a, b, c = pts[-1], edge[i], edge[i + 1]
        if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1e-9:
            pts.append(b)
    pts.append(edge[-1])
    if max(p[1] for p in pts) >= z1 - 0.3:
        sys.exit("a --port-notch leaves less than 0.3 mm of wall above it")
    return pts + [(yb, z1), (ya, z1)]


def _face_edge(poly, face):
    """Index i of the straight edge poly[i] -> poly[i+1] lying on FACE."""
    ax = 0 if face in "EW" else 1
    ext = (min if face in "WS" else max)(p[ax] for p in poly)
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if abs(a[ax] - ext) < 1e-6 and abs(b[ax] - ext) < 1e-6:
            return i
    sys.exit("no straight edge on face %s" % face)


def cut_ring(outer, inner, notch):
    """A ring (CCW outer, CCW inner) with a slot cut clean through one face.

    The result is a single C-shaped polygon with no hole: down the outer
    boundary to the slot, across the wall, back round the inner boundary the
    other way, and across again. Asserts the slot lies on the straight part of
    the face -- a slot into a corner radius would need a different shape.
    """
    f, lo, hi = notch["face"], notch["lo"], notch["hi"]
    along = 1 if f in "EW" else 0

    def pieces(poly):
        i = _face_edge(poly, f)
        a, b = poly[i], poly[(i + 1) % len(poly)]
        if not (min(a[along], b[along]) + 1e-6 < lo and hi < max(a[along], b[along]) - 1e-6):
            sys.exit("--port-notch %s spans %.2f..%.2f but the straight part of that face "
                     "is only %.2f..%.2f" % (notch["spec"], lo, hi,
                                             min(a[along], b[along]), max(a[along], b[along])))
        def at(v):
            q = list(a)
            q[along] = v
            return tuple(q)
        first, second = (at(hi), at(lo)) if b[along] < a[along] else (at(lo), at(hi))
        return i, first, second

    n_o, n_i = len(outer), len(inner)
    io, f_o, s_o = pieces(outer)
    ii, f_i, s_i = pieces(inner)
    poly = [s_o]
    poly += [outer[(io + 1 + k) % n_o] for k in range(n_o)]     # ends on outer[io]
    poly += [f_o, f_i]
    poly += [inner[(ii - k) % n_i] for k in range(n_i)]          # backwards, ends on inner[ii+1]
    poly += [s_i]
    if sw.signed_area(poly) <= 0:
        sys.exit("cut_ring produced a clockwise polygon -- face %s" % f)
    return poly


def cut_rect(rect, notch):
    """Split an axis-aligned rectangle (x0, y0, x1, y1) around a notch on its face."""
    along = 1 if notch["face"] in "EW" else 0
    a0, a1 = rect[along], rect[along + 2]
    out = []
    for s0, s1 in ((a0, min(a1, notch["lo"])), (max(a0, notch["hi"]), a1)):
        if s1 - s0 > 1e-6:
            r = list(rect)
            r[along], r[along + 2] = s0, s1
            out.append(tuple(r))
    return out


def threaded_boss(x0, x1, y0, y1, depth, hx, hy, core_r, pitch, root_w, tip_w,
                  thread_h, interference, nseg, phase=0.0937):
    """A rectangular boss (local x0..x1, y0..y1, z 0..depth) with an INTERNAL helical
    thread along local z, as explicit planar faces for StepFile.polyhedron().

    The thread is the negative of gen_insert_quarter20.py's ridge: a groove 1.0 wide
    at the core (root_w) narrowing to tip_w at full thread_h, right-handed, rising
    toward +z going counter-clockwise about +z. It is cut `interference` SHALLOWER
    than the insert's thread is tall, and the core is the insert's body diameter
    line-to-line, so the insert screws in tight and is then heated home.

    The surface is meshed on a grid that FOLLOWS THE HELIX -- rows are helices, so
    the groove's corners are exact on every row -- then each facet is clipped flat
    at z = 0 and z = depth. Clip points are computed per edge from a canonically
    ordered pair, so two facets sharing an edge get the same point and the end
    faces close the shell exactly.

    Returns (faces, triangles, max radius).
    """
    hg = thread_h - interference                 # groove depth actually cut
    ug = (root_w - hg * (root_w - tip_w) / thread_h) / 2.0
    pattern = [(-root_w / 2.0, core_r), (-ug, core_r + hg), (ug, core_r + hg),
               (root_w / 2.0, core_r)]
    K = len(pattern)
    S = []                                       # (s, r) rows, periodic with period K
    k = int(math.floor(-2.0 * pitch / pitch)) - 1
    while True:
        row = [(phase + k * pitch + b, r) for b, r in pattern]
        if row[0][0] > depth + 2.0 * pitch:
            break
        S += row
        k += 1
    N = nseg

    def key(p):
        return (round(p[0], 9), round(p[1], 9), round(p[2], 9))

    def pt(i, j):
        if i == N:                               # the seam: same point, one period on
            return pt(0, j + K)
        s, r = S[j]
        th = 2.0 * math.pi * i / N
        return (hx + r * math.cos(th), hy + r * math.sin(th), s + pitch * i / N)

    def cut(p, q, zc):
        a, b = (p, q) if key(p) <= key(q) else (q, p)
        t = (zc - a[2]) / (b[2] - a[2])
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), zc)

    def clip(poly, zc, keep_above):
        out = []
        for i in range(len(poly)):
            p, q = poly[i], poly[(i + 1) % len(poly)]
            pin = (p[2] >= zc) if keep_above else (p[2] <= zc)
            qin = (q[2] >= zc) if keep_above else (q[2] <= zc)
            if pin:
                out.append(p)
            if pin != qin:
                out.append(cut(p, q, zc))
        return out

    def newell(pts):
        n = [0.0, 0.0, 0.0]
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            n[0] += (a[1] - b[1]) * (a[2] + b[2])
            n[1] += (a[2] - b[2]) * (a[0] + b[0])
            n[2] += (a[0] - b[0]) * (a[1] + b[1])
        m = math.sqrt(sum(c * c for c in n))
        return tuple(c / m for c in n), m

    faces, tris = [], []
    rim = {0.0: {}, depth: {}}                   # boundary edges on each end plane
    for j in range(len(S) - 1 - K):
        for i in range(N):
            A, B, C, D = pt(i, j), pt(i + 1, j), pt(i + 1, j + 1), pt(i, j + 1)
            for tri in ((A, D, C), (A, C, B)):   # normal toward the axis: out of the solid
                if max(p[2] for p in tri) <= 0.0 or min(p[2] for p in tri) >= depth:
                    continue
                poly = clip(clip(list(tri), 0.0, True), depth, False)
                if len(poly) < 3:
                    continue
                n, area2 = newell(poly)
                if area2 < 1e-12:
                    continue
                faces.append((poly, n, []))
                tris += [(poly[0], poly[m], poly[m + 1]) for m in range(1, len(poly) - 1)]
                for m in range(len(poly)):
                    p, q = poly[m], poly[(m + 1) % len(poly)]
                    for zc in (0.0, depth):
                        if p[2] == zc and q[2] == zc:
                            rim[zc][key(q)] = (q, p)     # end face runs it the other way

    def chain(edges):
        start = next(iter(edges))
        loop, k0 = [], start
        while True:
            q, p = edges[k0]
            loop.append(q)
            k0 = key(p)
            if k0 == start:
                break
            if k0 not in edges or len(loop) > len(edges):
                sys.exit("threaded_boss: the end-face rim does not close")
        if len(loop) != len(edges):
            sys.exit("threaded_boss: the end-face rim is %d edges in %d loops"
                     % (len(edges), 2))
        return loop

    front, back = chain(rim[depth]), chain(rim[0.0])
    rect = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    faces.append(([(x, y, depth) for x, y in rect], (0.0, 0.0, 1.0), [front]))
    faces.append(([(x, y, 0.0) for x, y in reversed(rect)], (0.0, 0.0, -1.0), [back]))
    for (ax, ay), (bx, by) in zip(rect, rect[1:] + rect[:1]):
        side = [(ax, ay, 0.0), (bx, by, 0.0), (bx, by, depth), (ax, ay, depth)]
        dx, dy = bx - ax, by - ay
        m = math.hypot(dx, dy)
        faces.append((side, (dy / m, -dx / m, 0.0), []))
        tris += [(side[0], side[1], side[2]), (side[0], side[2], side[3])]
    for z, loop, up in ((depth, front, True), (0.0, back, False)):
        outer = rect if up else list(reversed(rect))
        hole = [(p[0], p[1]) for p in loop]
        if up:
            caps = sw.cap_triangles(rect, [hole])
        else:                                    # triangulate seen from +z, then flip
            caps = [(c, b, a) for a, b, c in sw.cap_triangles(rect, [list(reversed(hole))])]
        tris += [tuple((q[0], q[1], z) for q in t) for t in caps]
    return faces, tris, core_r + hg


def mesh_volume(tris):
    return sum((t[0][0] * (t[1][1] * t[2][2] - t[1][2] * t[2][1])
                - t[0][1] * (t[1][0] * t[2][2] - t[1][2] * t[2][0])
                + t[0][2] * (t[1][0] * t[2][1] - t[1][1] * t[2][0])) / 6.0 for t in tris)


def build(args):
    edge, pcb_holes, comps, u1 = read_pcb()
    xs = [v for e in edge for v in (e[0], e[2])]
    ys = [v for e in edge for v in (e[1], e[3])]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)

    def to_model(x, y):
        return (x - u1["x"], u1["y"] - y)

    mh = [to_model(h[0], h[1]) + (h[2],) for h in pcb_holes]
    ox, oy = OPTICAL_CENTER
    image_z = args.seat_z + DIE_TOP_Z_TYP
    glass_z = args.seat_z + GLASS_TOP_TYP

    top_surf = image_z + C_FLANGE_FOCAL          # <-- the optical datum
    top_under = top_surf - args.top_t
    bore_r = (C_THREAD_OD + args.bore_clear) / 2.0

    # model-frame board rectangle
    x0, y0 = to_model(bx0, by1)
    x1, y1 = to_model(bx1, by0)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    bw, bh = (x1 - x0), (y1 - y0)
    t = args.wall

    # THE BOX STANDS ON THE TABLE, NOT ON THE PCB.
    #
    # The first version put the walls on the board edge and the clearance check
    # rejected it: C31, an 0805, has its courtyard 1.8 mm in from the left edge,
    # so a wall thick enough to be worth printing has nowhere to stand. Since the
    # board lies flat on a table anyway, straddle it -- the cavity is the PCB
    # outline plus --pcb-clear on every side, and the walls run from the TABLE
    # (z = -pcb thickness) up. Nothing touches the board, and wall thickness stops
    # being hostage to a passive on the edge.
    cav_w, cav_h = bw + 2 * args.pcb_clear, bh + 2 * args.pcb_clear
    out_w, out_h = cav_w + 2 * t, cav_h + 2 * t
    z_table = -args.pcb_t

    # ---- assertions ----------------------------------------------------------
    # 1. the cavity must swallow the whole board, so no wall is over a component
    inner_x0, inner_x1 = cx - cav_w / 2.0, cx + cav_w / 2.0
    inner_y0, inner_y1 = cy - cav_h / 2.0, cy + cav_h / 2.0
    worst = ("", 0.0)
    for c in comps:
        if c["layer"] != "F.Cu":
            continue
        mx, my = to_model(c["x"], c["y"])
        hw, hh = c["w"] / 2.0, c["h"] / 2.0
        hz = glass_z if c["ref"] == "U1" else body_height(c["lib"])
        if hz > worst[1]:
            worst = (c["ref"], hz)
        # component bbox
        ax0, ax1, ay0, ay1 = mx - hw, mx + hw, my - hh, my + hh
        # inside the cavity (clear of the walls) ?
        if (ax0 >= inner_x0 + args.wall_clear and ax1 <= inner_x1 - args.wall_clear and
                ay0 >= inner_y0 + args.wall_clear and ay1 <= inner_y1 - args.wall_clear):
            continue
        # otherwise it overlaps the wall ring (or its clearance band)
        if (ax1 > x0 and ax0 < x1 and ay1 > y0 and ay0 < y1):
            sys.exit("WALL FOULS %s (%s) at model (%.2f, %.2f): cavity is "
                     "%.2f..%.2f x %.2f..%.2f, part spans %.2f..%.2f x %.2f..%.2f.\n"
                     "Reduce --wall (now %.2f) or inset the box."
                     % (c["ref"], c["lib"].split(":")[-1], mx, my,
                        inner_x0, inner_x1, inner_y0, inner_y1,
                        ax0, ax1, ay0, ay1, t))
    # 2. the top face must clear everything under it
    if top_under < worst[1] + args.top_clear:
        sys.exit("TOP TOO LOW: underside %.2f, tallest part %s at %.2f"
                 % (top_under, worst[0], worst[1]))
    # 3. the bosses (if asked for) must land on bare PCB, clear of every part.
    #    Checked, not assumed -- same discipline as the walls.
    if args.bosses:
        br = args.boss_dia / 2.0
        for c in comps:
            if c["layer"] != "F.Cu":
                continue
            mx, my = to_model(c["x"], c["y"])
            hw, hh = c["w"] / 2.0, c["h"] / 2.0
            for px, py, _ in mh:
                dx = max(abs(px - mx) - hw, 0.0)
                dy = max(abs(py - my) - hh, 0.0)
                if math.hypot(dx, dy) < br + args.wall_clear:
                    sys.exit("BOSS FOULS %s at model (%.2f, %.2f): gap %.2f mm"
                             % (c["ref"], mx, my, math.hypot(dx, dy) - br))
        # The foot MAY overhang the board edge -- the holes are only 2.5 mm in, and
        # a foot small enough to stay entirely on the board (Ø5) cannot also
        # enclose an M2 head (Ø3.8 + clearance). The overhanging sliver is carried
        # by the top face and the wall, not by the board, so it is structural, not
        # cantilevered off nothing. What is NOT negotiable is a real wall between
        # the head pocket and the outside of the foot:
        if args.boss_dia - args.head_dia < 2 * args.min_web:
            sys.exit("FOOT WALL TOO THIN: --boss-dia %.2f minus --head-dia %.2f "
                     "leaves %.2f mm of web, need %.2f"
                     % (args.boss_dia, args.head_dia,
                        (args.boss_dia - args.head_dia) / 2.0, args.min_web))

    # 4. the seat must be wide enough to carry the flange shoulder
    # measured on the BOX, not the board: the optical axis is 8.3 mm off the
    # board centre, so the narrow side of the seat is what matters.
    seat = min(out_w, out_h) / 2.0 - max(abs(ox - cx), abs(oy - cy))
    if 2 * bore_r + 2 * args.seat_min > 2 * seat:
        sys.exit("SEAT TOO NARROW: %.2f mm of face outside the bore, need %.2f "
                 "for a %.1f mm shoulder" % (seat - bore_r, args.seat_min, C_SHOULDER_OD))

    # 5. THE LIGHT LIP. The cavity is the board plus --pcb-clear on every side,
    #    which leaves a 0.75 mm slot running the FULL height of the wall, all the
    #    way around the board. That slot connects the space below the camera PCB
    #    -- where the Pt's LED array is -- straight into the optical cavity, and
    #    it is how LED light was reaching the sensor. The lip is a ledge standing
    #    off the wall inboard, overhanging the board edge, so a ray must climb the
    #    slot, run inward under the lip, and turn back up to get in.
    #
    #    HOW FAR IT MAY REACH IS NOT A FREE CHOICE. It is set by the nearest
    #    top-side component, so it is derived from the board and then checked,
    #    the same way --expose derives the socket tile's outer size.
    lip_ov = None
    contour = board_polygon(PCB, u1["x"], u1["y"])
    if args.thick_wall:
        top_side = [c for c in comps if c["layer"] == "F.Cu"]

        def near_edge(a, b, c):
            """Closest approach of a component's courtyard to one outline edge."""
            mx, my = to_model(c["x"], c["y"])
            hw, hh = c["w"] / 2.0, c["h"] / 2.0
            best = 1e9
            for sx in (-1, 1):
                for sy in (-1, 1):
                    px, py = mx + sx * hw, my + sy * hh
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    L = dx * dx + dy * dy
                    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - a[0]) * dx +
                                                              (py - a[1]) * dy) / L))
                    best = min(best, math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy)))
            return best

        # EVERY EDGE GETS AS MUCH AS ITS OWN NEIGHBOURS ALLOW, capped by --overlap.
        # Derived, never chosen: the notch's 26 mm inner wall can only give
        # 0.07 mm because R14 sits 0.47 mm off it, while the far side of the
        # board gives the full 1.40.
        room, who = [], []
        for i in range(len(contour)):
            a, b = contour[i], contour[(i + 1) % len(contour)]
            best, blame = 1e9, None
            for c in top_side:
                hz = glass_z if c["ref"] == "U1" else body_height(c["lib"])
                if hz < args.board_relief:
                    continue                      # fits under the wall; no constraint
                d = near_edge(a, b, c) - args.wall_clear
                if d < best:
                    best, blame = d, c
            room.append(best)
            who.append(blame)

        # THE CAP IS DERIVED FROM THE PERIMETER, NOT FROM THE NOTCH. Left
        # uncapped, each edge takes its own maximum -- up to 5.3 mm on the north
        # edge -- which makes the wall wander, eats the aperture, and (found the
        # hard way) deforms it enough that a boss no longer straddles the
        # boundary and its notch cannot be built. So the cap is what the true
        # outer perimeter allows, and interior edges are then reduced from it by
        # their own neighbours. An edge is "perimeter" when it lies on the
        # bounding box; everything else is notch or chamfer.
        xs2 = [p[0] for p in contour]
        ys2 = [p[1] for p in contour]
        pb = (min(xs2), max(xs2), min(ys2), max(ys2))
        perim = []
        for i in range(len(contour)):
            a, b = contour[i], contour[(i + 1) % len(contour)]
            mx2, my2 = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            if (abs(mx2 - pb[0]) < 1e-6 or abs(mx2 - pb[1]) < 1e-6 or
                    abs(my2 - pb[2]) < 1e-6 or abs(my2 - pb[3]) < 1e-6):
                perim.append(i)
        cap = args.overlap if args.overlap is not None else min(room[i] for i in perim)
        dists = [max(0.0, min(cap, r_)) for r_ in room]
        lip_ov = max(dists)
        capped = [i for i in range(len(contour)) if dists[i] < cap - 1e-9]
        if lip_ov <= 0:
            sys.exit("NO ROOM TO THICKEN THE WALL anywhere: every edge is blocked by a "
                     "part taller than --board-relief %.2f." % args.board_relief)
        notch_area = abs(sw.signed_area(sw.rect(cx, cy, bw, bh))) - abs(sw.signed_area(contour))
        aperture = offset_polygon(contour, dists)
        if sw.signed_area(aperture) <= 0:
            sys.exit("the offset outline inverted -- --overlap %.2f is too large for "
                     "this board" % lip_ov)

        # The bosses straddle the aperture near the corners; push it out round each.
        if args.bosses:
            for px, py, _ in mh:
                merged = union_circle(aperture, px, py,
                                      args.head_dia / 2.0 + args.wall_clear,
                                      max(args.segments // 4, 8))
                if merged is None:
                    sys.exit("boss at (%.2f, %.2f) does not cross the aperture boundary "
                             "exactly twice;\nthe notch for it cannot be built." % (px, py))
                aperture = merged

        # THE SCREWS MUST STILL GO IN. The notches are the whole reason the wall
        # can be this thick, and a wrong one does not look wrong: the first
        # version of aperture_notched() ran two of the four arcs the long way
        # round, producing a closed, valid, entirely plausible aperture that
        # quietly buried two screw heads in 1 mm of wall. Nothing else here would
        # have caught it -- the solid was watertight and the volume was right.
        # So the head circle is walked against the aperture, every boss, always.
        if args.bosses:
            def in_void(px, py):
                c, n = False, len(aperture)
                for k in range(n):
                    ax, ay = aperture[k]
                    bx, by = aperture[(k + 1) % n]
                    if (ay > py) != (by > py) and px < (bx - ax) * (py - ay) / (by - ay) + ax:
                        c = not c
                return c
            head_r = args.head_dia / 2.0
            for px, py, _ in mh:
                blocked = [a for a in range(0, 360, 5)
                           if not in_void(px + head_r * math.cos(math.radians(a)),
                                          py + head_r * math.sin(math.radians(a)))]
                if blocked:
                    sys.exit(
                        "WALL BURIES A SCREW HEAD at (%.2f, %.2f): %d of 72 points on the "
                        "%.1f mm\nhead circle fall in wall material. The aperture notch for "
                        "that corner is wrong\nor too small -- a driver could not reach the "
                        "screw and the box could not be\nfitted." % (px, py, len(blocked),
                                                                     args.head_dia))
        # It must not intrude on the light cone. The bore is the widest the cone
        # ever is, so clearing the bore's footprint clears everything below it.
        # Checked against the real aperture polygon now, not a pair of half-widths.
        for a in range(0, 360, 5):
            qx, qy = ox + bore_r * math.cos(math.radians(a)), oy + bore_r * math.sin(math.radians(a))
            hit, m = False, len(aperture)
            for k in range(m):
                ax, ay = aperture[k]
                bx2, by2 = aperture[(k + 1) % m]
                if (ay > qy) != (by2 > qy) and qx < (bx2 - ax) * (qy - ay) / (by2 - ay) + ax:
                    hit = not hit
            if not hit:
                sys.exit("WALL VIGNETTES: the %.1f mm bore at (%.2f, %.2f) is not clear "
                         "of the wall" % (2 * bore_r, ox, oy))

    # ---- geometry ------------------------------------------------------------
    step = sw.StepFile("camera_lens_box",
                       "C-mount lens box, open bottom, gravity-seated",
                       args.timestamp, tool="gen_lens_box.py")
    expected = {}

    outer = sw.rounded_rect(cx, cy, out_w, out_h, args.corner_r)
    inner = sw.rounded_rect(cx, cy, cav_w, cav_h, max(args.corner_r - t, 0.1))

    # four walls, one ring, standing on the TABLE.
    #
    # With --groove the bottom of that ring is split into two legs with the
    # groove between them, which a single prism cannot express: a prism has
    # vertical walls and cannot change profile with height. So the wall is built
    # in two Z bands and the lower one is two concentric rings -- the same
    # stacking gen_socket_tile.py uses to turn a slot onto another face.
    # PORT NOTCHES cut through the wall from its bottom edge so a cable can reach
    # a connector on a board below the camera PCB. A prism along Z cannot carry
    # a rounded corner in the wall's own plane, so the wall is built differently
    # where a notch is: every ring is cut CLEAN through over a span a little
    # wider than the notches (each ring a C, see cut_ring), from the wall bottom
    # up to --board-relief, and that gap is filled with one block extruded
    # THROUGH the wall thickness, whose face-on outline is notch_profile() --
    # rounded corners, overlapping notches merged into one stepped opening.
    notches = [parse_port_notch(s) for s in args.port_notch]
    for nt in notches:
        if nt["z_top"] <= z_table + nt["r"]:
            sys.exit("--port-notch %s: ZTOP is within R of the wall bottom %.2f"
                     % (nt["spec"], z_table))
        if nt["z_top"] > args.board_relief - 0.3 and args.thick_wall:
            sys.exit("--port-notch %s: ZTOP %.2f reaches the thick wall at %.2f. Above the\n"
                     "board's top face the wall is the stray-light baffle; keep the notch "
                     "below it." % (nt["spec"], nt["z_top"], args.board_relief))
    if notches and not args.thick_wall:
        sys.exit("--port-notch needs the thick wall (it fills up to --board-relief)")
    blocks = []                                 # one per face: the span every ring is cut over
    for f in sorted(set(nt["face"] for nt in notches)):
        on = [nt for nt in notches if nt["face"] == f]
        ya = min(nt["lo"] for nt in on) - args.notch_flare - 1.0
        yb = max(nt["hi"] for nt in on) + args.notch_flare + 1.0
        blocks.append(dict(face=f, lo=ya, hi=yb, z_top=args.board_relief, notches=on,
                           spec="%s block %.2f..%.2f" % (f, ya, yb)))
    notches_raw, notches = notches, blocks      # ring() cuts at the BLOCK span

    def ring(o, i, z0, z1, name):
        """A wall ring o-minus-i from z0 to z1, notched where a --port-notch reaches."""
        live = [nt for nt in notches if nt["z_top"] > z0 + EPS]
        if not live:
            step.prism(o, z0, z1, name, COLORS["box"], holes=[sw.reverse(i)])
            expected[name] = (abs(sw.signed_area(o)) - abs(sw.signed_area(i))) * (z1 - z0)
            return
        if len(live) > 1:
            sys.exit("only one --port-notch per wall band is supported")
        nt = live[0]
        zc = min(z1, nt["z_top"])
        c = cut_ring(o, i, nt)
        step.prism(c, z0, zc, name, COLORS["box"])
        expected[name] = abs(sw.signed_area(c)) * (zc - z0)
        if z1 - zc > EPS:
            ring(o, i, zc, z1, name + "_above_notch")

    wall_bot = z_table
    seam_open = set(f.upper() for f in args.open_face)
    for f in seam_open:
        if f not in ("N", "S", "E", "W"):
            sys.exit("--open-face %r is not N/S/E/W" % f)
    if args.groove:
        z_gt = z_table + args.groove_depth
        _, _, g_out, g_in = seam_profile(cx, cy, out_w, out_h, t, args.tongue_w,
                                         args.seam_clear, args.corner_r, args.arc_seg)
        ring(outer, g_out, z_table, z_gt, "wall_seam_outer")
        ring(g_in, inner, z_table, z_gt, "wall_seam_inner")

        # A GROOVE WITH NO TONGUE IN IT IS JUST A SLOT. gen_base_box.py omits the
        # tongue across an --open-face, because there it would bar the opening
        # and bridge it in mid-air. Give this half the same faces and the groove
        # is filled back to solid wall over exactly that span -- so the seam
        # feature stops where the joint stops, instead of leaving an open channel
        # along the bottom of the wall above the access slot.
        #
        # The fill is inset by --seam-clear from the tongue's own ends, so it can
        # never touch the tongue segments that wrap the corners either side.
        off = (t - args.tongue_w) / 2.0
        tw = args.tongue_w
        tb = (cx - out_w / 2.0 + off, cy - out_h / 2.0 + off,
              cx + out_w / 2.0 - off, cy + out_h / 2.0 - off)
        gi, go = off + tw + args.seam_clear, off - args.seam_clear
        for f in sorted(seam_open):
            lo, hi = ((tb[0] + tw + args.seam_clear, tb[2] - tw - args.seam_clear)
                      if f in "NS" else
                      (tb[1] + tw + args.seam_clear, tb[3] - tw - args.seam_clear))
            if hi - lo < 1.0:
                sys.exit("seam fill on face %s is only %.2f mm long" % (f, hi - lo))
            if f == "N":
                r = (lo, cy + out_h / 2.0 - gi, hi, cy + out_h / 2.0 - go)
            elif f == "S":
                r = (lo, cy - out_h / 2.0 + go, hi, cy - out_h / 2.0 + gi)
            elif f == "E":
                r = (cx + out_w / 2.0 - gi, lo, cx + out_w / 2.0 - go, hi)
            else:
                r = (cx - out_w / 2.0 + go, lo, cx - out_w / 2.0 + gi, hi)
            rects = [r]
            for nt in notches:
                if nt["face"] == f:
                    rects = [q for rr in rects for q in cut_rect(rr, nt)]
            for k, q in enumerate(rects):
                poly = sw.rect((q[0] + q[2]) / 2.0, (q[1] + q[3]) / 2.0,
                               q[2] - q[0], q[3] - q[1])
                nm = "wall_seam_fill_%s" % f + ("" if len(rects) == 1 else "_%d" % k)
                step.prism(poly, z_table, z_gt, nm, COLORS["box"])
                expected[nm] = abs(sw.signed_area(poly)) * args.groove_depth
        wall_bot = z_gt

    # ABOVE THE BOARD THE WALL IS SIMPLY THICKER. The cavity has to be the board
    # plus --pcb-clear only where the BOARD is; above its top face nothing needs
    # that width, so the wall steps inboard and stays there to the top. That is
    # what closes the 0.75 mm slot the LEDs were coming through -- not a ledge
    # hung off the wall, just a wall with a rebate at the bottom to clear the
    # board. One face, no shelf.
    if args.thick_wall:
        n = "wall_lower"                       # the rebate that clears the board
        if args.board_relief - wall_bot > EPS:
            ring(outer, inner, wall_bot, args.board_relief, n)
        n = "walls"
        step.prism(outer, args.board_relief, top_under, n, COLORS["box"],
                   holes=[sw.reverse(aperture)])
        expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(aperture)))
                       * (top_under - args.board_relief))

        # THE PORT-NOTCH BLOCKS: the wall put back over each cut span, extruded
        # through the wall thickness, with the notches in its outline.
        for b in blocks:
            if b["face"] == "W":
                org = (cx - out_w / 2.0, 0.0, 0.0)
                ex, ez, sgn = (0.0, 1.0, 0.0), (1.0, 0.0, 0.0), 1.0
            else:
                org = (cx + out_w / 2.0, 0.0, 0.0)
                ex, ez, sgn = (0.0, -1.0, 0.0), (-1.0, 0.0, 0.0), -1.0
            # local u = sgn * y, so an E face reads its spans mirrored
            loc = [dict(n, lo=min(sgn * n["lo"], sgn * n["hi"]),
                        hi=max(sgn * n["lo"], sgn * n["hi"])) for n in b["notches"]]
            ua, ub = sorted((sgn * b["lo"], sgn * b["hi"]))
            prof = notch_profile(loc, ua, ub, z_table, args.board_relief, args.notch_flare)
            n = "wall_port_block_%s" % b["face"]
            step.prism(prof, 0.0, t, n, COLORS["box"], frame=(org, ex, (0.0, 0.0, 1.0), ez))
            expected[n] = abs(sw.signed_area(prof)) * t

        # THE NOTCH PLUG. The thick wall stops at --board-relief above the PCB,
        # but over the notch there is no board to stop at: the Pt's LEDs see the
        # wall's underside straight through the notch, and the gap between it and
        # the board is still a way in. The plug fills the notch itself, keeping
        # --pcb-clear off the board's edges and running out to the cavity wall on
        # the east side so it is one piece with the box, down --notch-drop below
        # the wall -- i.e. below the board's top surface, inside the notch.
        if args.notch_drop > EPS:
            npoly = east_notch(contour)
            nd = [args.pcb_clear] * len(npoly)
            nd[-1] = -args.pcb_clear                # the closing edge: out to the wall
            plug = offset_polygon(npoly, nd)
            z_pl = args.board_relief - args.notch_drop
            if z_pl < wall_bot - EPS:
                sys.exit("--notch-drop %.2f puts the plug at z %.2f, below the wall "
                         "bottom %.2f" % (args.notch_drop, z_pl, wall_bot))
            n = "notch_plug"
            step.prism(plug, z_pl, args.board_relief, n, COLORS["box"])
            expected[n] = abs(sw.signed_area(plug)) * args.notch_drop
    else:
        n = "walls"
        step.prism(outer, wall_bot, top_under, n, COLORS["box"],
                   holes=[sw.reverse(inner)])
        expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(inner)))
                       * (top_under - wall_bot))

    # the top face, with the lens clearance bore
    n = "top"
    tholes = [sw.reverse(sw.circle(ox, oy, bore_r, args.segments))]
    if args.bosses:
        # driver access straight down onto each screw head
        tholes += [sw.reverse(circle_phase(px, py, args.head_dia / 2.0, args.segments,
                                          (k + 1) * 2.0 * math.pi / args.segments / 5.0))
                   for k, (px, py, _) in enumerate(mh)]
    step.prism(outer, top_under, top_surf, n, COLORS["box"], holes=tholes)
    expected[n] = (abs(sw.signed_area(outer))
                   - sum(abs(sw.signed_area(h)) for h in tholes)) * args.top_t

    # A 1/4-20 HEAT-SET INSERT, for a tripod, on the side wall nearest the board's
    # Bank B connector (J2, the lone 80-pin DF40 on the SOUTH edge -- the north
    # edge carries J1 + the 50-pin J3). A buttress on the outside of that wall,
    # running the box's full height so it stands on the table like the walls and
    # prints with no overhang either way up. Its hole is horizontal and BLIND:
    # it runs through the buttress only and the wall closes the bottom, so the
    # insert never opens the cavity to light. Built in a local frame (step_writer
    # `frame=`) because a prism along Z cannot carry a horizontal hole.
    ins = None
    if args.insert_face.upper() != "NONE":
        f = args.insert_face.upper()
        if f in seam_open:
            sys.exit("--insert-face %s is an --open-face" % f)
        outward = {"N": (0.0, 1.0, 0.0), "S": (0.0, -1.0, 0.0),
                   "E": (1.0, 0.0, 0.0), "W": (-1.0, 0.0, 0.0)}[f]
        half = out_h / 2.0 if f in "NS" else out_w / 2.0
        span = out_w if f in "NS" else out_h
        if args.insert_boss_w / 2.0 + abs(args.insert_x) > span / 2.0 - args.corner_r:
            sys.exit("INSERT BOSS OFF THE FLAT: %.1f wide at %+.1f on a %.1f face "
                     "with %.1f corners" % (args.insert_boss_w, args.insert_x, span,
                                            args.corner_r))
        ez = outward
        ey = (0.0, 0.0, 1.0)
        ex = (ey[1] * ez[2] - ey[2] * ez[1], ey[2] * ez[0] - ey[0] * ez[2],
              ey[0] * ez[1] - ey[1] * ez[0])            # ex = ey x ez, right-handed
        origin = (cx + outward[0] * half, cy + outward[1] * half, 0.0)
        ins_z = (z_table + top_surf) / 2.0 if args.insert_z is None else args.insert_z
        hole_r = args.insert_hole / 2.0
        if args.insert_thread:                   # the groove reaches past the core
            hole_r += args.thread_h - args.thread_interference
        if (ins_z - hole_r < z_table + args.insert_wall
                or ins_z + hole_r > top_surf - args.insert_wall
                or hole_r > args.insert_boss_w / 2.0 - args.insert_wall):
            sys.exit("INSERT HOLE BREAKS OUT: %.1f hole at z %.2f in a %.1f wide boss, "
                     "z %.2f..%.2f" % (args.insert_hole, ins_z, args.insert_boss_w,
                                       z_table, top_surf))
        n = "insert_boss"
        if args.insert_thread:
            # THE HOLE IS THREADED TO THE INSERT (gen_insert_quarter20.py): its
            # 8.0 body line-to-line, and a groove matching its 3.5-pitch ridge cut
            # --thread-interference shallower, so the insert screws in TIGHT and is
            # then heated home. Depth = insert length, so it finishes flush.
            bx0, bx1 = args.insert_x - args.insert_boss_w / 2.0, args.insert_x + args.insert_boss_w / 2.0
            faces, tris, _ = threaded_boss(bx0, bx1, z_table, top_surf, args.insert_depth,
                                           args.insert_x, ins_z, args.insert_hole / 2.0,
                                           args.thread_pitch, args.thread_root,
                                           args.thread_tip, args.thread_h,
                                           args.thread_interference, args.thread_segments)
            step.polyhedron(faces, tris, n, COLORS["boss"], frame=(origin, ex, ey, ez))
            expected[n] = mesh_volume(tris)
        else:
            prof = sw.rect(args.insert_x, (z_table + top_surf) / 2.0, args.insert_boss_w,
                           top_surf - z_table)
            hole = sw.reverse(sw.circle(args.insert_x, ins_z, hole_r, args.segments))
            step.prism(prof, 0.0, args.insert_depth, n, COLORS["boss"], holes=[hole],
                       frame=(origin, ex, ey, ez))
            expected[n] = ((abs(sw.signed_area(prof)) - abs(sw.signed_area(hole)))
                           * args.insert_depth)
        ins = (f, ins_z)

    # Optional locating bosses on the PCB's own corner holes.
    #
    # These HANG FROM THE TOP FACE down to the board -- the first version ran
    # them z = 0 .. 4 mm, which is four cylinders floating in mid-air joined to
    # nothing. A boss has to be attached to the part it locates.
    #
    # The column lands on bare PCB at the mounting hole (checked clear of every
    # component), and a smaller pin continues into the Ø2.2 hole itself to fix
    # the box laterally. The pin is a slip fit, not a press fit: this part is
    # meant to lift off.
    if args.bosses:
        foot_r = args.boss_dia / 2.0
        shank_r = args.screw_dia / 2.0
        head_r = args.head_dia / 2.0
        for i, (px, py, _) in enumerate(mh):
            # 1. the WASHER: a flat annulus bearing on the PCB around the hole.
            #    Bore is M2 shank clearance, so the screw passes through into the
            #    board's own hole and its head lands on this ring's top face.
            nm = "washer%d" % (i + 1)
            ring = sw.circle(px, py, foot_r, args.segments)
            bore = sw.reverse(sw.circle(px, py, shank_r, args.segments))
            step.prism(ring, 0.0, args.washer_t, nm, COLORS["boss"], holes=[bore])
            expected[nm] = ((abs(sw.signed_area(ring)) - abs(sw.signed_area(bore)))
                            * args.washer_t)

            # 2. the COLUMN, arching from the washer up to the top face, bored
            #    out to head diameter -- this is the box "curving around the
            #    screw head". The bore runs all the way through the top face so a
            #    driver reaches the screw from outside without lifting the box.
            nm = "column%d" % (i + 1)
            pocket = sw.reverse(circle_phase(px, py, head_r, args.segments,
                                             (i + 1) * 2.0 * math.pi / args.segments / 5.0))
            step.prism(ring, args.washer_t, top_under, nm, COLORS["boss"],
                       holes=[pocket])
            expected[nm] = ((abs(sw.signed_area(ring)) - abs(sw.signed_area(pocket)))
                            * (top_under - args.washer_t))

    text = step.dumps()
    open(OUT, "w", newline="\n").write(text)
    w = sys.stdout.write
    w("wrote %s  (%d entities, %.1f kB)\n" % (OUT, step.entity_count, len(text) / 1024.0))
    if args.stl:
        ntri, _ = step.write_stl(os.path.splitext(OUT)[0] + ".stl")
        w("wrote %s  (%d triangles)\n" % (os.path.splitext(OUT)[0] + ".stl", ntri))

    w("\nbox        %.1f x %.1f mm outer, %.2f mm walls, cavity %.1f x %.1f, open bottom\n"
      % (out_w, out_h, t, cav_w, cav_h))
    w("stands     on the TABLE at z = %.2f (PCB %.2f thick); board sits inside with "
      "%.2f mm all round;\n           the WALLS never touch the PCB%s\n"
      % (z_table, args.pcb_t, args.pcb_clear,
         " -- only the four feet below bear on it" if args.bosses else ""))
    if args.bosses:
        w("feet       4 x %.1f dia washer faces bearing on the PCB at its own corner "
          "holes,\n           %.2f thick, bored %.2f for the M2 shank. The box arches "
          "over the head\n           in a %.2f pocket that exits through the top face "
          "for a driver.\n"
          % (args.boss_dia, args.washer_t, args.screw_dia, args.head_dia))
    w("board      %.1f x %.1f mm inside a %.1f x %.1f cavity\n"
      % (bw, bh, cav_w, cav_h))
    w("optical    axis KiCad (%.3f, %.3f), bore centred there\n"
      % (u1["x"] + ox, u1["y"] - oy))
    w("bore       %.2f mm dia = C-mount thread %.1f %+.2f (%s)\n"
      % (2 * bore_r, C_THREAD_OD, args.bore_clear,
         "thread-in" if args.bore_clear < 0 else "clearance, NOT threaded"))
    if ins:
        w("insert     1/4-20 heat-set on the %s face (by J2, Bank B): %.2f hole, %.1f deep, "
          "blind,\n           axis z %.2f, %+.1f mm along the face, in a %.1f mm buttress "
          "z %.2f..%.2f\n" % (ins[0], args.insert_hole, args.insert_depth, ins[1],
                              args.insert_x, args.insert_boss_w, z_table, top_surf))
    w("stack     PCB 0.00 | seat %.2f | image plane %.2f | glass %.2f | "
      "top face %.3f\n" % (args.seat_z, image_z, glass_z, top_surf))
    w("*** TOP FACE %.3f mm = image plane %.3f + C-mount flange %.3f ***\n"
      % (top_surf, image_z, C_FLANGE_FOCAL))
    w("clearance  tallest part under the top: %s at %.2f, top underside %.2f (%.2f gap)\n"
      % (worst[0], worst[1], top_under, top_under - worst[1]))
    if args.groove:
        leg = (t - (args.tongue_w + 2 * args.seam_clear)) / 2.0
        w("groove     %.2f wide x %.2f deep in the wall bottom, z %.2f -> %.2f,\n"
          "           centred in the %.2f wall leaving %.2f mm of leg either side\n"
          % (args.tongue_w + 2 * args.seam_clear, args.groove_depth, z_table,
             z_table + args.groove_depth, t, leg))
        w("           mates gen_base_box.py's tongue -- both halves import "
          "seam_profile()\n           from this file, so the joint is defined once\n")
    if args.thick_wall:
        run = args.pcb_clear + lip_ov
        w("wall       %.2f thick where the board is, %.2f thick above it. It steps\n"
          "           inboard %.2f mm at z %.2f and stays there to the top face.\n"
          % (t, t + args.pcb_clear + lip_ov, lip_ov, args.board_relief))
        w("           inner face FOLLOWS THE BOARD OUTLINE (%d edges), notched round each\n"
          "           screw pocket so an M2 head still clears\n" % len(contour))
        w("           CLOSES the %.2f mm slot round the board: a ray must climb it, run\n"
          "           %.2f mm inboard through a %.2f mm channel, then turn back up --\n"
          "           %.1f:1, so nothing within %.0f deg of horizontal gets through\n"
          % (args.pcb_clear, run, args.board_relief,
             run / args.board_relief, math.degrees(math.atan2(args.board_relief, run))))
        w("           cap %.2f mm %s; %d of %d edges cut back by their own neighbours:\n"
          % (cap, "DERIVED from the perimeter" if args.overlap is None else "given",
             len(capped), len(contour)))
        for i in capped:
            a, b = contour[i], contour[(i + 1) % len(contour)]
            w("             %5.1f mm edge (%6.1f,%6.1f)->(%6.1f,%6.1f)  %.2f mm  (%s)\n"
              % (math.hypot(b[0] - a[0], b[1] - a[1]), a[0], a[1], b[0], b[1],
                 dists[i], who[i]["ref"] if who[i] else "-"))
        w("           the 26 mm one is the NOTCH's inner wall -- covering it is what\n"
          "           takes %.0f mm2 of open board out of the light path\n" % notch_area)
        w("           print this part in a MATTE BLACK material -- the geometry stops\n"
          "           the direct path, absorption is what deals with the scattered rest\n")
    else:
        w("wall       NOT thickened -- the cavity is open to the boards below through a %.2f mm\n"
          "           slot all round. This is the path the Pt's LEDs used.\n" % args.pcb_clear)
    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # 0.0 since 2026-10: the colour sensor sits FLAT on the PCB in the Andon
    # socket. The earlier mono build sat it 1.0 mm up, and the old default
    # matched that -- pass --seat-z 1.0 to regenerate that part.
    p.add_argument("--seat-z", type=float, default=0.0,
                   help="sensor seating plane above the PCB, mm (default 0.0 = flat, "
                        "Andon socket). Shifts the top face 1:1, so it shifts FOCUS 1:1.")
    p.add_argument("--wall", type=float, default=3.0, help="wall thickness, mm")
    p.add_argument("--pcb-clear", type=float, default=0.75,
                   help="gap between the PCB edge and the cavity wall, mm")
    p.add_argument("--pcb-t", type=float, default=1.6,
                   help="PCB thickness, mm -- the walls stand on the table, so this "
                        "sets how far below the board top they reach")
    p.add_argument("--top-t", type=float, default=3.0, help="top face thickness, mm")
    p.add_argument("--bore-clear", type=float, default=-0.10,
                   help="added to the 25.4 mm thread OD; negative = the lens threads into "
                        "the plastic (-0.10 = 25.3 mm, from the test plate; +0.80 = the "
                        "old clearance hole)")
    p.add_argument("--insert-face", default="S",
                   help="wall for the 1/4-20 tripod insert: N/S/E/W, or none. S = the "
                        "side nearest J2, the lone 80-pin DF40")
    p.add_argument("--insert-hole", type=float, default=8.0,
                   help="insert hole diameter, mm -- take it from the insert's datasheet")
    p.add_argument("--insert-depth", type=float, default=13.0,
                   help="hole depth = buttress protrusion, mm (an insert up to 12.7 long)")
    p.add_argument("--insert-boss-w", type=float, default=16.0,
                   help="buttress width along the face, mm")
    p.add_argument("--no-insert-thread", dest="insert_thread", action="store_false",
                   help="plain --insert-hole bore instead of the printed thread")
    p.add_argument("--thread-pitch", type=float, default=3.5,
                   help="insert thread pitch, mm -- MEASURED; = gen_insert_quarter20.py")
    p.add_argument("--thread-root", type=float, default=1.0,
                   help="insert thread width at the root, mm")
    p.add_argument("--thread-tip", type=float, default=0.3,
                   help="insert thread flat at the tip, mm")
    p.add_argument("--thread-h", type=float, default=1.0,
                   help="insert thread height off the 8.0 body, mm")
    p.add_argument("--thread-interference", type=float, default=0.15,
                   help="how much SHALLOWER the printed groove is than the insert's "
                        "thread, mm -- the tightness; the insert is heated to finish")
    p.add_argument("--thread-segments", type=int, default=72,
                   help="facets per turn of the printed thread")
    p.add_argument("--insert-wall", type=float, default=2.5,
                   help="minimum plastic round the insert hole, mm")
    p.add_argument("--insert-x", type=float, default=0.0,
                   help="buttress offset along the face from its centre, mm")
    p.add_argument("--insert-z", type=float, default=None,
                   help="insert axis height, mm (default: mid-height of the box)")
    p.add_argument("--wall-clear", type=float, default=0.40,
                   help="minimum gap from a wall to any component, mm")
    p.add_argument("--top-clear", type=float, default=1.00)
    p.add_argument("--seat-min", type=float, default=4.0,
                   help="minimum annulus of top face outside the bore, mm")
    p.add_argument("--bosses", action="store_true",
                   help="bolt the box down: a flat washer on the PCB at each of the "
                        "four corner holes, with the box arching over the screw head")
    p.add_argument("--boss-dia", type=float, default=7.0,
                   help="foot / column outside diameter, mm. Above ~7.8 it fouls U7 "
                        "at the top-left hole, which the script checks.")
    p.add_argument("--min-web", type=float, default=1.00,
                   help="minimum wall between the head pocket and the foot OD, mm")
    p.add_argument("--washer-t", type=float, default=1.20,
                   help="thickness of the flat washer bearing on the PCB, mm")
    p.add_argument("--head-dia", type=float, default=4.20,
                   help="pocket the box arches over the screw head with, mm "
                        "(M2 socket head is 3.8, pan head 4.0)")
    p.add_argument("--screw-dia", type=float, default=2.40)
    p.add_argument("--port-notch", action="append", default=[],
                   help="FACE:LO:HI:ZTOP[:R] -- cut the E or W wall from its bottom up to "
                        "ZTOP between y=LO and y=HI, top corners rounded to R (default 2), "
                        "for a cable to reach a port below the camera PCB. Repeatable; "
                        "overlapping notches merge. See README for the Pt V2 / Ft+ values")
    p.add_argument("--notch-flare", type=float, default=1.0,
                   help="radius rounding each notch's lower corners into the wall's "
                        "bottom edge, mm (0 = sharp)")
    p.add_argument("--open-face", action="append", default=[],
                   help="N/S/E/W -- face(s) where gen_base_box.py leaves the wall "
                        "off. The WALL here is never opened (this is the optical "
                        "enclosure); it only fills the seam groove back to solid, "
                        "so no empty channel is left where there is no tongue. "
                        "Give both halves the SAME faces.")
    p.add_argument("--no-groove", dest="groove", action="store_false",
                   help="omit the groove half of the seam joint")
    p.add_argument("--tongue-w", type=float, default=1.20,
                   help="tongue thickness, mm -- the GROOVE is this plus 2x "
                        "--seam-clear, so the wall keeps (wall - groove)/2 either "
                        "side (default 1.20 leaves 0.75 mm legs in a 3 mm wall)")
    p.add_argument("--seam-clear", type=float, default=0.15,
                   help="clearance per side between tongue and groove, mm")
    p.add_argument("--groove-depth", type=float, default=1.70,
                   help="groove depth, mm. Deliberately DEEPER than the tongue is "
                        "tall so the tongue can never bottom out -- the board stack "
                        "is the axial datum, not this joint")
    p.add_argument("--arc-seg", type=int, default=6)
    p.add_argument("--no-thick-wall", dest="thick_wall", action="store_false",
                   help="omit the light lip. The cavity is then open to the boards "
                        "below through a %s mm slot all round, which is how the Pt's "
                        "LEDs reached the sensor.")
    p.add_argument("--overlap", type=float, default=None,
                   help="how far the lip reaches over the board, mm. DERIVED from the "
                        "nearest top-side component when not given; passing a larger "
                        "value is checked against every part it would then cover.")
    p.add_argument("--board-relief", type=float, default=0.30,
                   help="gap between the board's top face and the lip's underside, mm. "
                        "The lip must NOT touch the board -- the four washers are the "
                        "seating datum and a proud lip would fight them (default 0.30)")
    p.add_argument("--notch-drop", type=float, default=2.0,
                   help="fill the board's east notch from --board-relief down this "
                        "far (mm), with --pcb-clear off the board edges, to shut the "
                        "Pt LEDs out (default 2.0; 0 = no plug)")
    p.add_argument("--corner-r", type=float, default=2.0)
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--timestamp", default="2026-08-13T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
