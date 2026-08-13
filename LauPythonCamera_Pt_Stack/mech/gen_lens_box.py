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
import argparse, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
from gen_lens_holder import read_pcb, body_height, OPTICAL_CENTER, \
                            DIE_TOP_Z_TYP, GLASS_TOP_TYP

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "camera_lens_box.step")

# --- C-mount, ISO 10935 / JIS B 7141 -----------------------------------------
C_FLANGE_FOCAL = 17.526      # mounting shoulder -> image plane, mm. THE datum.
C_THREAD_OD    = 25.4        # 1"-32 UN-2A major diameter
C_SHOULDER_OD  = 32.0        # typical flange shoulder; the seat must exceed this

COLORS = {"box": (0.32, 0.34, 0.38), "boss": (0.28, 0.30, 0.33)}


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

    # ---- geometry ------------------------------------------------------------
    step = sw.StepFile("camera_lens_box",
                       "C-mount lens box, open bottom, gravity-seated",
                       args.timestamp, tool="gen_lens_box.py")
    expected = {}

    outer = sw.rounded_rect(cx, cy, out_w, out_h, args.corner_r)
    inner = sw.rounded_rect(cx, cy, cav_w, cav_h, max(args.corner_r - t, 0.1))

    # four walls, one ring, standing on the TABLE
    n = "walls"
    hs = [sw.reverse(inner)]
    step.prism(outer, z_table, top_under, n, COLORS["box"], holes=hs)
    expected[n] = ((abs(sw.signed_area(outer)) - abs(sw.signed_area(inner)))
                   * (top_under - z_table))

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
    p.add_argument("--corner-r", type=float, default=2.0)
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--timestamp", default="2026-08-13T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
