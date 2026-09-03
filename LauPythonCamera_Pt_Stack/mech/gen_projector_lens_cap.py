"""gen_projector_lens_cap.py -- projector-lens cap that carries the camera stack.

Writes ../3dmodels/projector_lens_cap.step (+ .stl).

    z = 0       the camera PCB TOP SURFACE (the same datum as gen_lens_holder.py
                and gen_socket_tile.py)
    +Z          out of the sensor face, i.e. TOWARDS THE PROJECTOR
    +X / +Y     the KiCad top view, origin at U1, +y UP
                (KiCad y runs DOWN, so model_y = U1_y - kicad_y)

WHAT THIS IS. A flat cap that slips over the projector's 44 mm lens barrel and
holds the LauPythonCamera_Pt_Stack in front of it, BARE SENSOR (no M12 lens),
with the sensor's optical axis on the lens axis. A square aperture over the
sensor is the only opening; the rest of the plate is a baffle.

    projector body
      |<--- 25 mm skirt --->|
      |   [ 20 mm barrel ]  |
      |                     [plate 4 mm][gap 6 mm][camera PCB]...rest of stack
                             ^ aperture on the optical axis

THE ONE THING THAT IS NOT OBVIOUS. The sensor's optical axis is NOT in the
middle of the board's M2 bolt rectangle. The axis sits 8.32 mm off-centre in X
and 1.87 mm in Y, so a cap with its bore centred on the bolt pattern would put
the sensor ~8.5 mm off the lens axis. This part is deliberately eccentric: the
bore and aperture are on the OPTICAL AXIS, the four bolt holes fall where the
PCB puts them, and the PCB is re-read on every run so they cannot drift apart.

THE CONSEQUENCE, which drives most of the code below. That eccentricity puts one
bolt only 24.63 mm from the axis, while the 44.4 bore has a 22.20 mm radius --
2.43 mm of daylight, less than an M2 head. So:

  * the skirt's OUTER radius is clamped per-angle to stay off every screw's
    keep-out, and where the clamp would leave less than --min-wall of material
    the wall is WINDOWED instead (the skirt becomes arc segments);
  * a counterbore is cut only where it leaves --min-web to the bore and to the
    outline. Where it does not, that screw stays a plain through-hole and its
    head sits proud inside the window -- which fouls nothing, because a 25 mm
    skirt over a 20 mm barrel holds the plate 5 mm clear of the lens face.

Both are computed from the PCB, not hardcoded, and both are printed at the end.
If a parameter change makes a wall or a web too thin, the build FAILS. A cap
that cracks off a lens is a part you find out about with the projector on the
floor.
"""
import argparse, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
# The PCB reader, the optical-axis offset and the sensor stack-up are all in
# gen_lens_holder.py already; re-deriving them here is how the two parts would
# quietly disagree about where the sensor is.
import gen_lens_holder as glh

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "projector_lens_cap.step")

COLORS = {"plate": (0.28, 0.30, 0.34), "skirt": (0.22, 0.24, 0.28)}


# ---------------------------------------------------------------------------
def hull(points):
    """Andrew's monotone chain. Returns the convex hull, CCW."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) < 3:
        sys.exit("degenerate hull")

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and sw._cross2(out[-2], out[-1], p) <= 0:
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(list(reversed(pts)))
    return lower[:-1] + upper[:-1]


def ray_enters_disc(theta, cx, cy, k):
    """Smallest r > 0 where the ray from the origin at `theta` enters the disc of
    radius k centred at (cx, cy), or None. Standard line-circle intersection with
    the ray's unit vector; the ray is anchored at the skirt axis."""
    ux, uy = math.cos(theta), math.sin(theta)
    b = cx * ux + cy * uy               # projection of the centre onto the ray
    c2 = cx * cx + cy * cy - k * k
    disc = b * b - c2
    if disc < 0:
        return None                      # the ray misses the disc entirely
    root = math.sqrt(disc)
    for r in (b - root, b + root):
        if r > 0:
            return r
    return None


# ---------------------------------------------------------------------------
def build(args):
    edge, pcb_holes, comps, u1 = glh.read_pcb()

    def to_model(x, y):
        return (x - u1["x"], u1["y"] - y)

    mh = [to_model(h[0], h[1]) + (h[2],) for h in pcb_holes]
    if len(mh) != 4:
        sys.exit("expected 4 corner holes in the PCB, found %d" % len(mh))

    ox, oy = glh.OPTICAL_CENTER            # optical axis, model frame
    glass_z = args.seat_z + glh.GLASS_TOP_TYP
    image_z = args.seat_z + glh.DIE_TOP_Z_TYP

    # ---- the plate must clear everything on the top of the board -------------
    worst = ("(nothing)", 0.0)
    for c in comps:
        if c["layer"] != "F.Cu":
            continue
        h = glh.body_height(c["lib"])
        if h > worst[1]:
            worst = (c["ref"] + " " + c["lib"], h)
    if glass_z > worst[1]:
        worst = ("sensor glass top (seat %.2f)" % args.seat_z, glass_z)
    if args.gap - worst[1] < args.plate_clear:
        sys.exit("PLATE TOO LOW: underside z=%.2f, tallest thing %s at %.2f, need "
                 "%.2f mm clearance -- raise --gap" % (args.gap, worst[0], worst[1],
                                                       args.plate_clear))

    plate_z0 = args.gap
    plate_z1 = args.gap + args.plate_t
    cb_z0 = plate_z1 - args.cb_depth
    skirt_z1 = plate_z1 + args.skirt_depth
    if args.cb_depth >= args.plate_t - args.min_web:
        sys.exit("COUNTERBORE TOO DEEP: %.2f in a %.2f plate leaves %.2f mm of floor, "
                 "need %.2f" % (args.cb_depth, args.plate_t,
                                args.plate_t - args.cb_depth, args.min_web))

    bore_r = args.bore_dia / 2.0
    skirt_or = bore_r + args.wall
    shank_r = args.screw_dia / 2.0
    head_r = args.head_dia / 2.0
    boss_r = head_r + args.min_web

    # ---- per-screw: is there room for a counterbore? ------------------------
    screws = []
    for px, py, _ in mh:
        d = math.hypot(px - ox, py - oy)
        web = (d - head_r) - bore_r          # head edge to bore edge, radially
        cbore = web >= args.min_web
        shank_web = (d - shank_r) - bore_r
        if shank_web < args.min_web:
            sys.exit("SCREW HOLE BREAKS INTO THE BORE at model (%.2f, %.2f): %.2f mm "
                     "from the axis leaves a %.2f mm web to the %.2f bore, need %.2f."
                     % (px, py, d, shank_web, args.bore_dia, args.min_web))
        screws.append(dict(x=px, y=py, d=d, web=web, cbore=cbore))

    # ---- skirt: clamp the outer radius, window it where clamping is not enough
    n = args.skirt_segments
    prof = []                               # (theta_rad, outer r or None = window)
    for i in range(n):
        th = 2.0 * math.pi * i / n
        r = skirt_or
        for s in screws:
            # Driver access is sized on the HEAD even when the head is not
            # recessed: a proud head still needs a clear column past the skirt.
            hit = ray_enters_disc(th, s["x"] - ox, s["y"] - oy, head_r + args.min_web)
            if hit is not None:
                r = min(r, hit)
        prof.append((th, None if r < bore_r + args.min_wall else r))

    windows = [i for i, (_, r) in enumerate(prof) if r is None]
    if len(windows) == n:
        sys.exit("SKIRT ENTIRELY WINDOWED -- nothing would locate on the lens.")

    step = sw.StepFile("projector_lens_cap",
                       "Projector lens cap carrying LauPythonCamera_Pt_Stack, bare sensor",
                       args.timestamp, tool="gen_projector_lens_cap.py")
    expected = {}

    # ---- outline: convex hull of the skirt OD and a boss at each screw ------
    pool = [(ox + skirt_or * math.cos(2 * math.pi * i / args.segments),
             oy + skirt_or * math.sin(2 * math.pi * i / args.segments))
            for i in range(args.segments)]
    for s in screws:
        pool += [(s["x"] + boss_r * math.cos(2 * math.pi * i / args.segments),
                  s["y"] + boss_r * math.sin(2 * math.pi * i / args.segments))
                 for i in range(args.segments)]
    outline = hull(pool)

    aperture = sw.reverse(sw.rounded_rect(ox, oy, args.aperture, args.aperture,
                                          args.aperture_r, segments=6))

    # plate, below the counterbore band: every screw is a plain shank bore
    hs = [aperture] + [sw.reverse(sw.circle(s["x"], s["y"], shank_r, args.segments))
                       for s in screws]
    step.prism(outline, plate_z0, cb_z0, "plate", COLORS["plate"], holes=hs)
    expected["plate"] = (abs(sw.signed_area(outline))
                         - sum(abs(sw.signed_area(h)) for h in hs)) * (cb_z0 - plate_z0)

    # the counterbore band: head-diameter where it fits, shank where it does not
    hs = [aperture] + [sw.reverse(sw.circle(s["x"], s["y"],
                                            head_r if s["cbore"] else shank_r,
                                            args.segments)) for s in screws]
    step.prism(outline, cb_z0, plate_z1, "plate_face", COLORS["plate"], holes=hs)
    expected["plate_face"] = (abs(sw.signed_area(outline))
                              - sum(abs(sw.signed_area(h)) for h in hs)) * args.cb_depth

    # ---- skirt ---------------------------------------------------------------
    def at(i, r):
        th = prof[i][0]
        return (ox + r * math.cos(th), oy + r * math.sin(th))

    if not windows:
        ring = [at(i, prof[i][1]) for i in range(n)]
        hole = sw.reverse(sw.circle(ox, oy, bore_r, n))
        step.prism(ring, plate_z1, skirt_z1, "skirt", COLORS["skirt"], holes=[hole])
        expected["skirt"] = (abs(sw.signed_area(ring))
                             - abs(sw.signed_area(hole))) * args.skirt_depth
        segs = 1
    else:
        # walk the circle, cutting it into runs of surviving wall
        runs, cur = [], []
        start = (windows[-1] + 1) % n
        for k in range(n):
            i = (start + k) % n
            if prof[i][1] is None:
                if len(cur) >= 2:
                    runs.append(cur)
                cur = []
            else:
                cur.append(i)
        if len(cur) >= 2:
            runs.append(cur)
        if not runs:
            sys.exit("SKIRT HAS NO CONTINUOUS WALL LEFT")
        segs = len(runs)
        for j, run in enumerate(runs):
            # out along the run, back along the bore: one closed C-section
            poly = [at(i, prof[i][1]) for i in run]
            poly += [at(i, bore_r) for i in reversed(run)]
            nm = "skirt%d" % (j + 1)
            step.prism(poly, plate_z1, skirt_z1, nm, COLORS["skirt"])
            expected[nm] = abs(sw.signed_area(poly)) * args.skirt_depth

    # ---- write ---------------------------------------------------------------
    text = step.dumps()
    sw.write_verified(OUT, text)
    stl = os.path.splitext(OUT)[0] + ".stl"
    w = sys.stdout.write
    w("wrote %s  (%d entities, %.1f kB)\n" % (OUT, step.entity_count, len(text) / 1024.0))
    if args.stl:
        ntri, _ = step.write_stl(stl)
        w("wrote %s  (%d triangles)\n" % (stl, ntri))

    span = (max(p[0] for p in outline) - min(p[0] for p in outline),
            max(p[1] for p in outline) - min(p[1] for p in outline))
    w("\nplate      %.1f x %.1f mm, %.2f thick, aperture %.1f mm square on the axis\n"
      % (span[0], span[1], args.plate_t, args.aperture))
    w("skirt      bore %.2f, wall %.2f (OD %.2f), %.1f mm deep, %d segment(s)\n"
      % (args.bore_dia, args.wall, 2 * skirt_or, args.skirt_depth, segs))
    w("           %d of %d profile steps windowed (%.1f deg of wall removed)\n"
      % (len(windows), n, 360.0 * len(windows) / n))
    w("optical    axis at model (%.3f, %.3f) = KiCad (%.3f, %.3f)\n"
      % (ox, oy, u1["x"] + ox, u1["y"] - oy))
    w("eccentric  bolt rectangle centre is (%.2f, %.2f) from the axis -- NOT concentric\n"
      % (sum(s["x"] for s in screws) / 4.0 - ox, sum(s["y"] for s in screws) / 4.0 - oy))
    for s in screws:
        w("screw      (%7.2f,%7.2f)  r=%5.2f from axis  web to bore %5.2f  %s\n"
          % (s["x"], s["y"], s["d"], s["web"],
             "counterbored %.2f x %.2f" % (args.head_dia, args.cb_depth) if s["cbore"]
             else "PLAIN HOLE -- no room to counterbore, head sits proud"))
    w("stack      PCB 0.00 | seat %.2f | image plane %.2f | glass %.2f | "
      "plate %.2f..%.2f | skirt end %.2f\n"
      % (args.seat_z, image_z, glass_z, plate_z0, plate_z1, skirt_z1))
    w("clearance  tallest thing under the plate: %s at %.2f (%.2f mm gap)\n"
      % (worst[0], worst[1], plate_z0 - worst[1]))
    w("optics     lens face sits %.1f mm inside the skirt end, so it is %.2f mm from\n"
      "           the plate and %.2f mm from the image plane\n"
      % (args.barrel_len, args.skirt_depth - args.barrel_len,
         args.skirt_depth - args.barrel_len + args.plate_t + (plate_z0 - image_z)))
    w("screws     the cap adds %.2f mm of grip above the PCB top (counterbore floor\n"
      "           at z=%.2f), so add that to whatever length holds the stack today\n"
      % (cb_z0, cb_z0))

    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bore-dia", type=float, default=44.40,
                   help="skirt bore, mm (default 44.40 = a 44.0 barrel + 0.4 slip fit)")
    p.add_argument("--barrel-len", type=float, default=20.0,
                   help="how far the lens barrel protrudes, mm -- reported, not modelled")
    p.add_argument("--skirt-depth", type=float, default=25.0, help="skirt depth, mm")
    p.add_argument("--wall", type=float, default=2.50, help="nominal skirt wall, mm")
    p.add_argument("--min-wall", type=float, default=1.20,
                   help="thinnest skirt wall allowed before the wall is windowed instead")
    p.add_argument("--plate-t", type=float, default=4.00, help="plate thickness, mm")
    p.add_argument("--gap", type=float, default=6.00,
                   help="plate underside above the PCB top, mm")
    p.add_argument("--plate-clear", type=float, default=1.50,
                   help="minimum gap from the plate underside to the tallest part, mm")
    p.add_argument("--aperture", type=float, default=12.00,
                   help="square aperture over the sensor, mm (glass lid is 13.6)")
    p.add_argument("--aperture-r", type=float, default=0.60,
                   help="aperture corner radius, mm")
    p.add_argument("--screw-dia", type=float, default=2.40, help="M2 clearance bore, mm")
    p.add_argument("--head-dia", type=float, default=4.20,
                   help="counterbore, mm (M2 socket cap head 3.8 + clearance)")
    p.add_argument("--cb-depth", type=float, default=2.20, help="counterbore depth, mm")
    p.add_argument("--min-web", type=float, default=0.80,
                   help="thinnest plate web allowed between a bore and anything else, mm")
    p.add_argument("--seat-z", type=float, default=1.0,
                   help="sensor seating plane above the PCB, mm (default 1.0)")
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--skirt-segments", type=int, default=180)
    p.add_argument("--timestamp", default="2026-09-03T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
