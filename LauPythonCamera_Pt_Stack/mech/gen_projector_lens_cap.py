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

    # ---- the filter pocket, if one was asked for ----------------------------
    # A 50 mm filter cannot pass into a 44.4 bore, and the only space in the beam
    # is inside that bore, so the skirt is split into three axial bands: root,
    # pocket, grip. The pocket's cavity is wider than the bore; the root and grip
    # bands keep the bore, so their annular faces trap the filter axially while
    # the cradle carries its weight. The skirt LENGTHENS to put the lens face
    # past the pocket, which moves the sensor the same distance -- reported below,
    # because it changes absolute readings and nothing else warns about it.
    fil = None
    if args.filter_dia > 0:
        pocket_ir = (args.filter_dia + args.filter_clear) / 2.0
        pocket_z0 = plate_z1 + args.pocket_root
        pocket_z1 = pocket_z0 + args.filter_t + args.filter_clear
        # the barrel tip must sit past the pocket, and the skirt must reach the
        # projector body, so the skirt grows by however much the pocket added
        skirt_z1 = pocket_z1 + args.lens_clear + args.barrel_len
        fil = dict(ir=pocket_ir, orr=pocket_ir + args.pocket_wall,
                   z0=pocket_z0, z1=pocket_z1,
                   tip=pocket_z1 + args.lens_clear)
    if args.cb_depth >= args.plate_t - args.min_web:
        sys.exit("COUNTERBORE TOO DEEP: %.2f in a %.2f plate leaves %.2f mm of floor, "
                 "need %.2f" % (args.cb_depth, args.plate_t,
                                args.plate_t - args.cb_depth, args.min_web))

    bore_r = args.bore_dia / 2.0
    skirt_or = bore_r + args.wall
    # THE BANDS AND THE POCKET SHARE ONE OUTER SHELL, always. An early revision
    # grew the wall to only 0.5 mm past the cavity's inner radius, so the pocket met
    # the bands on a 0.5 mm annular ledge -- and across the open slot the band that
    # grips the lens had nothing under it at all. It printed as two loose pieces.
    # Tying the two radii together here means no revision can reintroduce that: the
    # tube has one outside, and the slot is the only thing missing from it.
    if fil is not None:
        skirt_or = max(skirt_or, fil["ir"] + args.pocket_wall)
        args.wall = skirt_or - bore_r
        fil["orr"] = skirt_or
        if args.pocket_wall < args.min_wall:
            sys.exit("POCKET WALL TOO THIN: %.2f mm outside the cavity, need %.2f. "
                     "That wall is also the web either side of the slot, and those "
                     "webs are the only thing holding the lens grip on."
                     % (args.pocket_wall, args.min_wall))
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

    # ---- optional stand tab: a tongue on the plate's DOWN edge ----------------
    # For running the camera away from the projector, on a desk. The tab is a
    # rectangle in the plate's own plane and thickness, so it prints in the same
    # layers as the plate (in-plane strength) and drops into stand.step's pocket.
    # DOWN is opposite the filter slot, which must face up. It overlaps the plate
    # by --tab-overlap so the two solids fuse in the slicer whatever the outline.
    tab = None
    if args.tab_w > 0:
        td = math.radians(args.slot_deg + 180.0 if args.tab_deg is None else args.tab_deg)
        ux, uy = math.cos(td), math.sin(td)
        vx, vy = -uy, ux
        reach = max((p_[0] - ox) * ux + (p_[1] - oy) * uy for p_ in outline)
        d0, d1 = reach - args.tab_overlap, reach + args.tab_len
        hw = args.tab_w / 2.0
        tab_poly = [(ox + ux * d + vx * w, oy + uy * d + vy * w)
                    for d, w in ((d0, -hw), (d1, -hw), (d1, hw), (d0, hw))]
        if sw.signed_area(tab_poly) < 0:
            tab_poly.reverse()
        tab = dict(poly=tab_poly, reach=reach, deg=math.degrees(td), d1=d1)

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

    if tab is not None:
        step.prism(tab["poly"], plate_z0, plate_z1, "tab", COLORS["plate"])
        expected["tab"] = abs(sw.signed_area(tab["poly"])) * args.plate_t

    # ---- skirt ---------------------------------------------------------------
    def at(i, r):
        th = prof[i][0]
        return (ox + r * math.cos(th), oy + r * math.sin(th))

    def emit_band(z0, z1, tag):
        """The skirt profile extruded between two heights, windows and all."""
        if z1 - z0 <= 1e-9:
            return 0
        if not windows:
            ring = [at(i, prof[i][1]) for i in range(n)]
            hole = sw.reverse(sw.circle(ox, oy, bore_r, n))
            step.prism(ring, z0, z1, tag, COLORS["skirt"], holes=[hole])
            expected[tag] = (abs(sw.signed_area(ring))
                             - abs(sw.signed_area(hole))) * (z1 - z0)
            return 1
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
        for j, run in enumerate(runs):
            poly = [at(i, prof[i][1]) for i in run]
            poly += [at(i, bore_r) for i in reversed(run)]
            nm = "%s%d" % (tag, j + 1)
            step.prism(poly, z0, z1, nm, COLORS["skirt"])
            expected[nm] = abs(sw.signed_area(poly)) * (z1 - z0)
        return len(runs)

    if fil is not None:
        # root band, then the cradle, then the band that grips the barrel
        segs = emit_band(plate_z1, fil["z0"], "skirt_root")
        # THE POCKET BAND IS THE TUBE WITH A LETTERBOX CUT IN ITS TOP. The slot
        # has to clear |x| < the cavity radius, because a descending disc sweeps
        # that whole vertical band on its way in -- but NOT the material further
        # out than that. What survives either side is a full-height web joining
        # the root band to the band that grips the lens, which is the connection
        # the first print was missing.
        #
        #        outer arc, 180-a .. 360+a          .-"""""-.        slot
        #        down x=+ir, inner semicircle,     | |     | |   <-- (open)
        #        up x=-ir                          |  \___/  |
        #                                           \_______/
        ir, orr = fil["ir"], fil["orr"]
        ta = math.acos(min(1.0, ir / orr))          # where x=ir meets the shell
        h = math.sqrt(max(0.0, orr * orr - ir * ir))
        m = max(48, args.skirt_segments)
        a0, a1 = math.pi - ta, 2.0 * math.pi + ta   # CCW, the long way round
        # the arc already ENDS at (+ir, +h) and STARTS at (-ir, +h) -- repeating
        # either makes a zero-length edge, which the STEP writer divides by
        poly = [(ox + orr * math.cos(a0 + (a1 - a0) * k / m),
                 oy + orr * math.sin(a0 + (a1 - a0) * k / m)) for k in range(m + 1)]
        poly.append((ox + ir, oy))                  # down the right web's inner face
        mi = max(24, args.skirt_segments // 2)      # inner semicircle, 0 -> -180
        poly += [(ox + ir * math.cos(-math.pi * k / mi),
                  oy + ir * math.sin(-math.pi * k / mi)) for k in range(1, mi)]
        poly.append((ox - ir, oy))                  # up the left web, closing on
                                                    # the arc's own start point
        # Built with the slot facing +y, then rolled to wherever it has to face.
        # The slot must end up pointing UP with the stack mounted, so this angle
        # is set by the assembly's roll -- opposite the cable exit.
        roll = math.radians(args.slot_deg - 90.0)
        if abs(roll) > 1e-12:
            ca, sa = math.cos(roll), math.sin(roll)
            poly = [(ox + (px - ox) * ca - (py - oy) * sa,
                     oy + (px - ox) * sa + (py - oy) * ca) for px, py in poly]
        if sw.signed_area(poly) < 0:
            poly = list(reversed(poly))
        step.prism(poly, fil["z0"], fil["z1"], "pocket", COLORS["skirt"])
        expected["pocket"] = abs(sw.signed_area(poly)) * (fil["z1"] - fil["z0"])
        segs += 1
        segs += emit_band(fil["z1"], skirt_z1, "skirt_grip")
    elif not windows:
        segs = emit_band(plate_z1, skirt_z1, "skirt")
    else:
        segs = emit_band(plate_z1, skirt_z1, "skirt")

    # ---- write ---------------------------------------------------------------
    # A cap with a filter pocket is a DIFFERENT part, not a new revision of the
    # plain one: the plain cap is what every measurement so far was taken with,
    # and it has to stay on disk to go back to.
    out = OUT if fil is None else os.path.join(
        os.path.dirname(OUT), "projector_lens_cap_filter.step")
    if tab is not None:
        out = os.path.splitext(out)[0] + "_tab.step"
    text = step.dumps()
    sw.write_verified(out, text)
    stl = os.path.splitext(out)[0] + ".stl"
    w = sys.stdout.write
    w("wrote %s  (%d entities, %.1f kB)\n" % (out, step.entity_count, len(text) / 1024.0))
    if args.stl:
        ntri, _ = step.write_stl(stl)
        w("wrote %s  (%d triangles)\n" % (stl, ntri))

    span = (max(p[0] for p in outline) - min(p[0] for p in outline),
            max(p[1] for p in outline) - min(p[1] for p in outline))
    w("\nplate      %.1f x %.1f mm, %.2f thick, aperture %.1f mm square on the axis\n"
      % (span[0], span[1], args.plate_t, args.aperture))
    w("skirt      bore %.2f, wall %.2f (OD %.2f), %.1f mm deep, %d segment(s)\n"
      % (args.bore_dia, args.wall, 2 * skirt_or, skirt_z1 - plate_z1, segs))
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
      % (args.barrel_len, (skirt_z1 - plate_z1) - args.barrel_len,
         (skirt_z1 - plate_z1) - args.barrel_len + args.plate_t
         + (plate_z0 - image_z)))
    if fil is not None:
        w("filter     %.1f x %.1f mm in a %.2f dia x %.2f cavity, z %.2f..%.2f\n"
          % (args.filter_dia, args.filter_t, 2 * fil["ir"],
             fil["z1"] - fil["z0"], fil["z0"], fil["z1"]))
        w("           slot faces %.0f deg CCW from +x in the model frame -- aim it\n"
          "           OPPOSITE the cable exit so it points up once mounted\n"
          % args.slot_deg)
        w("           the pocket band is the SAME tube (r %.2f..%.2f) with a letterbox\n"
          "           cut in it, clearing %.2f mm either side of the axis, which is\n"
          "           what a descending disc sweeps. Everything further out survives\n"
          "           as a %.2f mm full-height web each side, and those webs are what\n"
          "           tie the lens grip to the root band. The bore either side traps\n"
          "           the filter axially; gravity holds it down.\n"
          % (fil["ir"], fil["orr"], fil["ir"], fil["orr"] - fil["ir"]))
        w("           pocket floor sits %.2f mm above the plate face, clearing the\n"
          "           proud screw head; a driver still reaches that screw through the\n"
          "           empty cavity.\n" % args.pocket_root)
        w("COST       the skirt grew %.2f mm to put the lens face past the pocket, so\n"
          "           the sensor sits that much further from the lens than with the\n"
          "           plain cap. Absolute readings WILL shift; ratios and timings\n"
          "           should not.\n" % ((skirt_z1 - plate_z1) - 25.0))
    if tab is not None:
        w("tab        %.1f wide, %.1f long, %.2f thick (= plate), pointing %.0f deg CCW from +x\n"
          "           (opposite the filter slot). Tab tip is %.2f mm from the optical axis;\n"
          "           model x,y of the tab end centre = (%.2f, %.2f).\n"
          % (args.tab_w, args.tab_len, args.plate_t, tab["deg"], tab["d1"],
             ox + math.cos(math.radians(tab["deg"])) * tab["d1"],
             oy + math.sin(math.radians(tab["deg"])) * tab["d1"]))
    w("screws     the cap adds %.2f mm of grip above the PCB top (counterbore floor\n"
      "           at z=%.2f), so add that to whatever length holds the stack today\n"
      % (cb_z0, cb_z0))

    ok = check_step.validate(out, expected, out=open(os.devnull, "w"))
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
    # ---- optional drop-in filter pocket -------------------------------------
    p.add_argument("--filter-dia", type=float, default=0.0,
                   help="mounted diameter of a filter to carry in the beam, mm. "
                        "0 (default) builds the plain cap, byte-identical to what "
                        "it always built. Non-zero writes a SEPARATE step file.")
    p.add_argument("--filter-t", type=float, default=6.0, help="filter thickness, mm")
    p.add_argument("--filter-clear", type=float, default=0.60,
                   help="pocket oversize on both diameter and thickness, mm. Small "
                        "on purpose: the cavity is what centres the filter on the "
                        "axis, so slop here is decentring there.")
    p.add_argument("--pocket-root", type=float, default=2.50,
                   help="skirt between the plate face and the pocket floor, mm. Must "
                        "clear any screw head left sitting proud of the plate.")
    p.add_argument("--pocket-wall", type=float, default=1.60,
                   help="shell outside the filter cavity, mm. This also sets the "
                        "skirt wall, because the pocket and the bands either side "
                        "share ONE outer surface -- a pocket that bulges past its "
                        "neighbours is what made the first print come apart.")
    p.add_argument("--slot-deg", type=float, default=90.0,
                   help="direction the filter slot faces, degrees CCW from +x in "
                        "the model frame (90 = +y). The slot wants to face UP once "
                        "the stack is mounted, so this is set by which way the "
                        "assembly is rolled: point it opposite the cable exit.")
    p.add_argument("--lens-clear", type=float, default=0.60,
                   help="gap from the pocket's outer face to the lens face, mm")
    p.add_argument("--tab-w", type=float, default=0.0,
                   help="width of a stand tab on the plate's down edge, mm. 0 (default) "
                        "builds no tab. Non-zero writes a SEPARATE *_tab.step file.")
    p.add_argument("--tab-len", type=float, default=16.0,
                   help="how far the tab projects past the plate outline, mm")
    p.add_argument("--tab-overlap", type=float, default=1.0,
                   help="how far the tab reaches back into the plate, mm")
    p.add_argument("--tab-deg", type=float, default=None,
                   help="tab direction, degrees CCW from +x in the model frame "
                        "(default: opposite --slot-deg, i.e. down)")
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--skirt-segments", type=int, default=180)
    p.add_argument("--timestamp", default="2026-09-03T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
