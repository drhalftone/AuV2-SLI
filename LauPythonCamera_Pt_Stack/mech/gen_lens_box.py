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

The bore is deliberately a CLEARANCE hole, not a thread -- 1"-32 UN has a
25.4 mm major diameter and the bore is that plus --bore-clear, so the barrel
hangs through without binding and the shoulder alone carries the lens.

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
    wall_bot = z_table
    if args.groove:
        z_gt = z_table + args.groove_depth
        _, _, g_out, g_in = seam_profile(cx, cy, out_w, out_h, t, args.tongue_w,
                                         args.seam_clear, args.corner_r, args.arc_seg)
        n = "wall_seam_outer"
        step.prism(outer, z_table, z_gt, n, COLORS["box"], holes=[sw.reverse(g_out)])
        expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(g_out)))
                       * args.groove_depth)
        n = "wall_seam_inner"
        step.prism(g_in, z_table, z_gt, n, COLORS["box"], holes=[sw.reverse(inner)])
        expected[n] = ((abs(sw.signed_area(g_in)) - abs(sw.signed_area(inner)))
                       * args.groove_depth)
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
            step.prism(outer, wall_bot, args.board_relief, n, COLORS["box"],
                       holes=[sw.reverse(inner)])
            expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(inner)))
                           * (args.board_relief - wall_bot))
        n = "walls"
        step.prism(outer, args.board_relief, top_under, n, COLORS["box"],
                   holes=[sw.reverse(aperture)])
        expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(aperture)))
                       * (top_under - args.board_relief))
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
    w("bore       %.2f mm dia = C-mount thread %.1f + %.2f clearance (NOT threaded)\n"
      % (2 * bore_r, C_THREAD_OD, args.bore_clear))
    w("stack      PCB 0.00 | seat %.2f | image plane %.2f | glass %.2f | "
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
    p.add_argument("--seat-z", type=float, default=1.0,
                   help="sensor seating plane above the PCB, mm. Not published; "
                        "shifts the top face 1:1, so it shifts FOCUS 1:1.")
    p.add_argument("--wall", type=float, default=3.0, help="wall thickness, mm")
    p.add_argument("--pcb-clear", type=float, default=0.75,
                   help="gap between the PCB edge and the cavity wall, mm")
    p.add_argument("--pcb-t", type=float, default=1.6,
                   help="PCB thickness, mm -- the walls stand on the table, so this "
                        "sets how far below the board top they reach")
    p.add_argument("--top-t", type=float, default=3.0, help="top face thickness, mm")
    p.add_argument("--bore-clear", type=float, default=0.80,
                   help="added to the 25.4 mm thread OD so the barrel never touches")
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
    p.add_argument("--corner-r", type=float, default=2.0)
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--timestamp", default="2026-08-13T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
