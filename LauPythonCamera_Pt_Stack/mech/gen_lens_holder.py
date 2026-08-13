"""gen_lens_holder.py -- M12 (S-mount) lens holder for LauPythonCamera_Pt_Stack.

Writes ../3dmodels/camera_lens_holder.step (+ .stl).

    z = 0       the PCB TOP SURFACE  (same datum as gen_socket_tile.py)
    +X / +Y     the KiCad top view, origin at U1, +y UP
                (KiCad y runs DOWN, so model_y = U1_y - kicad_y)

WHAT THIS IS. A plate that bolts to the four corner holes of the camera PCB and
carries an M12 x 0.5 lens on the sensor's optical axis. It stands off on four
posts so that nothing but those posts touches the board.

EVERY NUMBER IS READ, NOT ASSUMED. The board outline, the four mounting holes and
every component position come from ../LauPythonCamera_Pt_Stack.kicad_pcb and are
re-read on every run; the sensor stack-up comes from gen_sensor_step.py, which in
turn cites the datasheet. The script ASSERTS that:

  * the four posts miss every component courtyard, and
  * the plate underside clears the tallest component beneath it.

A holder that fouls a 0402 is a part you find out about with a scalpel, so both
checks fail the build rather than warn.

THE ONE NUMBER THAT IS NOT PUBLISHED is the height of the sensor's seating plane
above the PCB -- gen_socket_tile.py says so explicitly ("published nowhere").
--seat-z takes it; the default of 1.0 mm is the figure supplied for this board.
It sets the image-plane height and therefore the whole optical stack, so if it is
wrong, the focus range is wrong by the same amount. It is a single flag.
"""
import argparse, math, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "..", "LauPythonCamera_Pt_Stack.kicad_pcb")
OUT = os.path.join(HERE, "..", "3dmodels", "camera_lens_holder.step")

# --- from gen_sensor_step.py, which cites NOIP1SN1300A Table 31 / 32 -----------
OPTICAL_CENTER = (-0.17924, +1.36714)   # optical axis relative to the PACKAGE centre
DIE_TOP_Z_TYP  = 1.260                  # package bottom -> die top (the IMAGE PLANE)
GLASS_TOP_TYP  = 1.260 + 0.990          # package bottom -> glass top = 2.250

# --- M12 x 0.5 "S-mount", the standard lens for a 1/2" sensor ----------------
# 11.5 mm is the tap drill for M12 x 0.5 (12.0 - 0.5). Modelled as a plain bore:
# a helical thread is not representable with the prism writer, and a tapped bore
# is how this gets made anyway.
THREAD_TAP_DIA = 11.5
BARREL_OD      = 16.0

COLORS = {"holder": (0.35, 0.37, 0.40), "post": (0.30, 0.32, 0.35)}

# Conservative body heights, mm. Only used for the clearance assertion, so they
# err HIGH: being wrong here must fail the build, not pass it.
HEIGHTS = {"0402": 0.60, "0603": 0.95, "0805": 1.05, "SOT-23": 1.45,
           "SOT-563": 0.70, "L_Power": 2.10, "TestPoint": 0.10,
           "Hirose": 0.00}   # J1/J2/J3 are on B.Cu -- not under this part


def read_pcb():
    s = open(PCB, encoding="utf-8", errors="replace").read()
    edge, holes = [], []
    for m in re.finditer(r'\(gr_line[^()]*\(start ([-\d.]+) ([-\d.]+)\)[^()]*'
                         r'\(end ([-\d.]+) ([-\d.]+)\)(.{0,200}?)\(layer "([^"]+)"', s, re.S):
        if m.group(6) == "Edge.Cuts":
            edge.append(tuple(float(m.group(i)) for i in (1, 2, 3, 4)))
    for m in re.finditer(r'\(gr_circle[^()]*\(center ([-\d.]+) ([-\d.]+)\)[^()]*'
                         r'\(end ([-\d.]+) ([-\d.]+)\)(.{0,200}?)\(layer "([^"]+)"', s, re.S):
        if m.group(6) == "Edge.Cuts":
            cx, cy, ex, ey = (float(m.group(i)) for i in (1, 2, 3, 4))
            holes.append((cx, cy, math.hypot(ex - cx, ey - cy)))
    comps, u1 = [], None
    for f in re.split(r'\n\t\(footprint ', s)[1:]:
        lib = re.match(r'"([^"]+)"', f)
        lay = re.search(r'\(layer "([^"]+)"\)', f)
        at = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', f)
        ref = re.search(r'\(property "Reference" "([^"]+)"', f)
        if not (at and ref and lay and lib):
            continue
        cx, cy = [], []
        for g in re.finditer(r'\((?:fp_line|fp_rect)[^()]*\(start ([-\d.]+) ([-\d.]+)\)[^()]*'
                             r'\(end ([-\d.]+) ([-\d.]+)\)(.{0,160}?)\(layer "([^"]+)"', f, re.S):
            if "CrtYd" in g.group(6):
                cx += [float(g.group(1)), float(g.group(3))]
                cy += [float(g.group(2)), float(g.group(4))]
        w = (max(cx) - min(cx)) if cx else 0.0
        h = (max(cy) - min(cy)) if cy else 0.0
        rec = dict(ref=ref.group(1), layer=lay.group(1), lib=lib.group(1),
                   x=float(at.group(1)), y=float(at.group(2)), w=w, h=h)
        comps.append(rec)
        if rec["ref"] == "U1":
            u1 = rec
            if at.group(3) not in (None, "0"):
                sys.exit("U1 is rotated (%s deg); this script assumes 0." % at.group(3))
    if u1 is None:
        sys.exit("U1 not found in the PCB")
    return edge, holes, comps, u1


def body_height(lib):
    for k, v in HEIGHTS.items():
        if k in lib:
            return v
    return 3.50          # unknown part: assume tall, so the assertion bites


def build(args):
    edge, pcb_holes, comps, u1 = read_pcb()
    xs = [v for e in edge for v in (e[0], e[2])]
    ys = [v for e in edge for v in (e[1], e[3])]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)

    # KiCad -> model frame: origin U1, +y up
    def to_model(x, y):
        return (x - u1["x"], u1["y"] - y)

    mh = [to_model(h[0], h[1]) + (h[2],) for h in pcb_holes]
    if len(mh) != 4:
        sys.exit("expected 4 corner holes, found %d" % len(mh))

    ox, oy = OPTICAL_CENTER                      # U1 unrotated, so this is the axis
    image_z = args.seat_z + DIE_TOP_Z_TYP
    glass_z = args.seat_z + GLASS_TOP_TYP

    # ---- clearance assertions ------------------------------------------------
    post_r = args.post_dia / 2.0
    worst = ("", 0.0)
    for c in comps:
        if c["layer"] != "F.Cu":
            continue
        mx, my = to_model(c["x"], c["y"])
        hz = glass_z if c["ref"] == "U1" else body_height(c["lib"])
        if hz > worst[1]:
            worst = (c["ref"], hz)
        # post vs component courtyard (rectangle, axis-aligned)
        hw, hh = c["w"] / 2.0, c["h"] / 2.0
        for px, py, _ in mh:
            dx = max(abs(px - mx) - hw, 0.0)
            dy = max(abs(py - my) - hh, 0.0)
            if math.hypot(dx, dy) < post_r + args.post_clear:
                sys.exit("POST FOULS %s: gap %.2f mm < %.2f required"
                         % (c["ref"], math.hypot(dx, dy) - post_r, args.post_clear))
    if args.plate_z < worst[1] + args.plate_clear:
        sys.exit("PLATE TOO LOW: underside z=%.2f, tallest part %s at %.2f, "
                 "need %.2f clearance" % (args.plate_z, worst[0], worst[1], args.plate_clear))

    # ---- geometry ------------------------------------------------------------
    step = sw.StepFile("camera_lens_holder",
                       "M12x0.5 lens holder for LauPythonCamera_Pt_Stack",
                       args.timestamp, tool="gen_lens_holder.py")

    plate_top = args.plate_z + args.plate_t
    barrel_top = plate_top + args.barrel_h
    bore_r = args.thread_dia / 2.0
    screw_r = args.screw_dia / 2.0

    # plate outline = the board outline, so it registers to the PCB
    x0, y0 = to_model(bx0, by1)          # KiCad (minx, maxy) -> model (minx, miny)
    x1, y1 = to_model(bx1, by0)
    outline = sw.rounded_rect((x0 + x1) / 2.0, (y0 + y1) / 2.0,
                              (x1 - x0), (y1 - y0), args.corner_r)

    bores = [sw.circle(px, py, screw_r, args.segments) for px, py, _ in mh]
    lens_bore = sw.circle(ox, oy, bore_r, args.segments)

    expected = {}
    name = "plate"
    hs = [sw.reverse(h) for h in bores + [lens_bore]]
    step.prism(outline, args.plate_z, plate_top, name, COLORS["holder"], holes=hs)
    expected[name] = (abs(sw.signed_area(outline))
                      - sum(abs(sw.signed_area(h)) for h in hs)) * args.plate_t

    for i, (px, py, _) in enumerate(mh):
        n = "post%d" % (i + 1)
        ring = sw.circle(px, py, post_r, args.segments)
        hole = sw.reverse(sw.circle(px, py, screw_r, args.segments))
        step.prism(ring, 0.0, args.plate_z, n, COLORS["post"], holes=[hole])
        expected[n] = (abs(sw.signed_area(ring)) - abs(sw.signed_area(hole))) * args.plate_z

    n = "barrel"
    ring = sw.circle(ox, oy, args.barrel_od / 2.0, args.segments)
    hole = sw.reverse(sw.circle(ox, oy, bore_r, args.segments))
    step.prism(ring, plate_top, barrel_top, n, COLORS["holder"], holes=[hole])
    expected[n] = (abs(sw.signed_area(ring)) - abs(sw.signed_area(hole))) * args.barrel_h

    text = step.dumps()
    open(OUT, "w", newline="\n").write(text)
    w = sys.stdout.write
    w("wrote %s  (%d entities, %.1f kB)\n" % (OUT, step.entity_count, len(text) / 1024.0))
    if args.stl:
        ntri, _ = step.write_stl(os.path.splitext(OUT)[0] + ".stl")
        w("wrote %s  (%d triangles)\n" % (os.path.splitext(OUT)[0] + ".stl", ntri))

    w("\nboard      %.1f x %.1f mm, KiCad X %.1f..%.1f  Y %.1f..%.1f\n"
      % (bx1 - bx0, by1 - by0, bx0, bx1, by0, by1))
    w("mount      4 x M%.1f through, holder bore %.2f, on the PCB's own %.2f holes\n"
      % (args.screw_dia - 0.4, args.screw_dia, 2 * mh[0][2]))
    w("optical    axis at model (%.3f, %.3f) = KiCad (%.3f, %.3f)\n"
      % (ox, oy, u1["x"] + ox, u1["y"] - oy))
    w("stack      PCB 0.00 | seat %.2f | image plane %.2f | glass %.2f | "
      "plate %.2f..%.2f | barrel top %.2f\n"
      % (args.seat_z, image_z, glass_z, args.plate_z, plate_top, barrel_top))
    w("clearance  tallest part under the plate: %s at %.2f, plate underside %.2f "
      "(%.2f mm gap)\n" % (worst[0], worst[1], args.plate_z, args.plate_z - worst[1]))
    w("focus      lens rear face can sit %.2f..%.2f mm above the image plane\n"
      % (args.plate_z - image_z, barrel_top - image_z))
    w("thread     bore %.2f = tap drill for M12 x 0.5; tap after printing/machining\n"
      % args.thread_dia)

    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seat-z", type=float, default=1.0,
                   help="sensor seating plane above the PCB, mm (default 1.0). Not "
                        "published; sets the whole optical stack.")
    p.add_argument("--plate-z", type=float, default=4.50,
                   help="plate underside above the PCB, mm (default 4.50)")
    p.add_argument("--plate-t", type=float, default=3.00, help="plate thickness, mm")
    p.add_argument("--barrel-h", type=float, default=12.0,
                   help="threaded barrel height above the plate, mm")
    p.add_argument("--barrel-od", type=float, default=BARREL_OD)
    p.add_argument("--thread-dia", type=float, default=THREAD_TAP_DIA)
    p.add_argument("--post-dia", type=float, default=6.0)
    p.add_argument("--screw-dia", type=float, default=2.40,
                   help="clearance bore for M2, mm")
    p.add_argument("--post-clear", type=float, default=0.50,
                   help="minimum gap from a post to any component courtyard, mm")
    p.add_argument("--plate-clear", type=float, default=1.00,
                   help="minimum gap from the plate underside to the tallest part, mm")
    p.add_argument("--corner-r", type=float, default=2.0)
    p.add_argument("--segments", type=int, default=48)
    p.add_argument("--timestamp", default="2026-08-13T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
