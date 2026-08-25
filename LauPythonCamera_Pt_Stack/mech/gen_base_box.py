"""gen_base_box.py -- the LOWER half of the two-part printed enclosure.

Writes ../3dmodels/camera_base_box.step (+ .stl).

    z = 0       the CAMERA PCB TOP SURFACE -- the same origin gen_lens_box.py
                uses, so the two halves land on each other when opened together
    +X / +Y     the KiCad top view, origin at U1, +y UP

WHAT THIS IS. The other half of the sandwich. gen_lens_box.py builds an
open-BOTTOMED box whose walls stop at z = -1.60 (the camera PCB's bottom face);
this builds an open-TOPPED box that runs from there down over the rest of the
stack and closes underneath. Together they are one enclosure, split on the
camera PCB's bottom face.

    +19.786  ---- top face = OPTICAL DATUM  --.
                                              |  camera_lens_box.step
     -1.600  ==== MATING PLANE ===============:  (already committed, UNCHANGED)
                                              |
                 [ camera PCB ] sits above     |  camera_base_box.step
                 [ Pt V2 ]                     |   <- this file
                 [ Ft+ ]                       |
    -19.000  ---- Hd+ bottom face              |
    -21.000  ---- floor top (support bosses)   |
    -24.000  ---- floor bottom               --'

=============================================================================
THE STACK HEIGHT IS MEASURED, NOT DERIVED
=============================================================================
MEASURED   19.00 mm, camera PCB top surface -> Hd+ bottom surface. Given
           directly; this file does NOT rebuild it from board thickness times
           gap, because that product is what was wrong before.

For the record, 19.00 disagrees with gen_stack_shroud.py, which computes 17.44
from 4 x 1.60 mm boards + 3 x 3.68 mm gaps. Back-solving the measurement gives
a 4.20 mm gap, not 3.68 -- and 4.20 is far closer to the DF40HC(4.0) sockets on
the Br (4.0 mm nominal stacking height) than 3.68 ever was. Treat the 3.68 in
gen_stack_shroud.py as suspect; it is not used here.

=============================================================================
THE MATING SURFACE IS CHECKED AGAINST THE PART IT MATES TO
=============================================================================
This file does not assume the upper half's dimensions -- it READS
camera_lens_box.step on every run, pulls the 'walls' solid, and asserts that
its own outer profile and top face match. Two halves that do not meet is
exactly the kind of failure a STEP file hides until it has been printed.

=============================================================================
WHY THE SCREWS RUN THROUGH THE BOARDS
=============================================================================
The upper half's only through-features are four 2.4 mm bores at the PCB's own
corner holes, so that is the ONLY place the two halves can be bolted together.
Four M2 screws enter from the TOP -- gen_lens_box.py already exits its driver
pockets through the top face for exactly this -- pass down through the whole
board stack, and thread into nuts captured under this part's floor.

That gives the load path the DF40s should never have had:

    screw head -> lens box washer -> camera PCB -> (board stack in compression,
    connector bodies bearing) -> Hd+ -> support boss -> floor -> nut

The stack is CAPTURED between the lens box's washers and this part's four
support bosses. Neither connector pair is ever in tension, which is what a
19 mm stack hanging off 0.4 mm-pitch DF40s would otherwise be the moment the
assembly is picked up.

UNVERIFIED, AND IT MATTERS: the screws must pass through the corner holes of
the Hd+, Ft+ and Pt V2. The 2.2 mm pattern at (2.5, 2.5) / (2.5, 42.5) /
(52.5, 2.5) / (52.5, 42.5) is confirmed on the camera board (its own KiCad) and
on the Br (Br.step). It is NOT confirmed on those three -- there is no CAD for
them in this repo. An M2 in a 2.2 mm hole has 0.2 mm to spare. If a board
differs the screw simply will not pass, which you find out at assembly with
nothing damaged, so this is a warning and not a refusal.

=============================================================================
CABLES MUST BE ABLE TO LEAVE, AND THIS PART CANNOT GUESS WHERE
=============================================================================
Every external connector in the stack -- HDMI, USB 3, power, JTAG -- is on the
three boards THIS half encloses. A closed base box is therefore useless, and
the generator will not quietly emit one: pass --open-face, or --window with
--window-source, or say --closed-box outright.

--window numbers are stamped into the STEP description together with their
source, for the same reason gen_stack_shroud.py requires --notch-source: a
shop, or you in six weeks, cannot tell a measured cutout from an invented one
by looking at the file.
"""
import argparse, math, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step
from gen_lens_holder import read_pcb

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "3dmodels", "camera_base_box.step")
UPPER = os.path.join(HERE, "..", "3dmodels", "camera_lens_box.step")

STACK_H = 19.00          # MEASURED: camera PCB top -> Hd+ bottom. See header.
Z_SPLIT = -1.60          # mating plane = camera PCB bottom = lens box wall bottom

COLORS = {"box": (0.32, 0.34, 0.38), "boss": (0.28, 0.30, 0.33)}
EDGE_MIN = 0.80          # minimum material from a hole to the edge of its bar
EPS = 1e-6


# ---------------------------------------------------------------------------
# 2D helpers
# ---------------------------------------------------------------------------

def hexagon(cx, cy, af):
    """Counter-clockwise hexagon of a given ACROSS-FLATS size (a nut pocket)."""
    r = (af / 2.0) / math.cos(math.radians(30.0))
    return [(cx + r * math.cos(math.radians(30.0 + 60.0 * i)),
             cy + r * math.sin(math.radians(30.0 + 60.0 * i))) for i in range(6)]


def bar_poly(x0, y0, x1, y1, r, corners, seg=6):
    """A rectangle with only SOME corners rounded.

    The ring's four outer corners belong to the N and S bars, which span the
    full width. A bar piece rounds a corner only when that corner really is one
    of the ring's, so a piece created by splitting a wall for a window gets
    square ends -- which is what it should have.
    """
    pts = []
    if "SW" in corners and r > 0:
        pts += sw.arc(x0 + r, y0 + r, r, math.pi, 1.5 * math.pi, seg)
    else:
        pts.append((x0, y0))
    if "SE" in corners and r > 0:
        pts += sw.arc(x1 - r, y0 + r, r, 1.5 * math.pi, 2.0 * math.pi, seg)
    else:
        pts.append((x1, y0))
    if "NE" in corners and r > 0:
        pts += sw.arc(x1 - r, y1 - r, r, 0.0, 0.5 * math.pi, seg)
    else:
        pts.append((x1, y1))
    if "NW" in corners and r > 0:
        pts += sw.arc(x0 + r, y1 - r, r, 0.5 * math.pi, math.pi, seg)
    else:
        pts.append((x0, y1))
    return sw.dedupe(pts)


def bars(x0, y0, x1, y1, t):
    """Split a rectangular ring into four non-overlapping wall bars.

    The same split gen_stack_shroud.py uses: N and S span the FULL width so the
    corners belong to them, E and W fill the remainder. No overlapping solids
    and no coincident-face soup.
    """
    xi0, yi0, xi1, yi1 = x0 + t, y0 + t, x1 - t, y1 - t
    return {"S": (x0, y0, x1, yi0), "N": (x0, yi1, x1, y1),
            "W": (x0, yi0, xi0, yi1), "E": (xi1, yi0, x1, yi1)}


def split_bar(rect, edge, windows):
    """Cut a bar laterally where windows cross it, returning the pieces left."""
    bx0, by0, bx1, by1 = rect
    horizontal = edge in "NS"
    segs = [(bx0, bx1)] if horizontal else [(by0, by1)]
    for wn in windows:
        lo, hi = wn["lo"], wn["hi"]
        out = []
        for a, b in segs:
            if hi <= a + EPS or lo >= b - EPS:
                out.append((a, b))
                continue
            if lo > a + EPS:
                out.append((a, lo))
            if hi < b - EPS:
                out.append((hi, b))
        segs = out
    pieces = []
    for a, b in segs:
        if b - a < 0.8:                 # a sliver is not a printable feature
            continue
        pieces.append((a, by0, b, by1) if horizontal else (bx0, a, bx1, b))
    return pieces


# ---------------------------------------------------------------------------
# The half we have to mate with
# ---------------------------------------------------------------------------

def upper_half():
    """Read camera_lens_box.step and return its wall footprint and mating face.

    Read, not assumed. If someone regenerates the lens box with a different
    --wall or --pcb-clear, this part must fail rather than print a base that
    overhangs by 1.5 mm.
    """
    if not os.path.exists(UPPER):
        sys.exit("%s not found.\nThe base box mates to the lens box and checks itself "
                 "against it.\nRun: python gen_lens_box.py --bosses" % UPPER)
    _, ents = check_step.parse(UPPER)
    model = check_step.Model(ents)
    for name, faces in model.solids():
        if name != "walls":
            continue
        xs, ys, zs = [], [], []
        for f in faces:
            outer, inners, _, _ = model.face(f)
            for loop in ([outer] if outer else []) + inners:
                for p in loop:
                    xs.append(p[0])
                    ys.append(p[1])
                    zs.append(p[2])
        return dict(x0=min(xs), x1=max(xs), y0=min(ys), y1=max(ys),
                    z_bot=min(zs), z_top=max(zs))
    sys.exit("no 'walls' solid in %s" % UPPER)


def parse_window(text, board, stack_bot):
    """--window edge:along:width:zbot:ztop

    edge   N/S/E/W in the model frame (+y up)
    along  mm along that edge from the board's origin corner (min x / min y)
    width  mm
    zbot   mm ABOVE THE HD+ BOTTOM FACE -- a real surface you can rest calipers
    ztop   on, rather than a signed offset from a datum inside the sensor
    """
    parts = text.split(":")
    if len(parts) != 5:
        sys.exit("--window wants edge:along:width:zbot:ztop, got %r" % text)
    edge = parts[0].upper()
    if edge not in ("N", "S", "E", "W"):
        sys.exit("--window edge %r is not N/S/E/W" % edge)
    try:
        along, width, zb, zt = (float(v) for v in parts[1:])
    except ValueError:
        sys.exit("--window %r: along/width/zbot/ztop must all be numbers" % text)
    if width <= 0:
        sys.exit("--window width must be positive")
    if zt <= zb:
        sys.exit("--window ztop (%.2f) must be above zbot (%.2f)" % (zt, zb))
    origin = board["x0"] if edge in "NS" else board["y0"]
    return dict(edge=edge, lo=origin + along - width / 2.0,
                hi=origin + along + width / 2.0,
                z0=stack_bot + zb, z1=stack_bot + zt, spec=text)


# ---------------------------------------------------------------------------

def build(args):
    edge_geom, pcb_holes, comps, u1 = read_pcb()
    xs = [v for e in edge_geom for v in (e[0], e[2])]
    ys = [v for e in edge_geom for v in (e[1], e[3])]
    bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)

    def to_model(x, y):
        return (x - u1["x"], u1["y"] - y)

    mh = [to_model(h[0], h[1]) for h in pcb_holes]
    m_x0, m_y1 = to_model(bx0, by0)
    m_x1, m_y0 = to_model(bx1, by1)
    board = dict(x0=m_x0, x1=m_x1, y0=m_y0, y1=m_y1)
    bw, bh = m_x1 - m_x0, m_y1 - m_y0
    cx, cy = (m_x0 + m_x1) / 2.0, (m_y0 + m_y1) / 2.0

    t = args.wall
    cav_w, cav_h = bw + 2 * args.pcb_clear, bh + 2 * args.pcb_clear
    out_w, out_h = cav_w + 2 * t, cav_h + 2 * t
    x0, y0 = cx - out_w / 2.0, cy - out_h / 2.0
    x1, y1 = cx + out_w / 2.0, cy + out_h / 2.0

    # ---- Z scheme ---------------------------------------------------------
    z_split = args.z_split                       # mating plane, from the lens box
    z_stack_bot = -args.stack_h                  # Hd+ bottom face (MEASURED)
    z_floor_top = z_stack_bot - args.floor_clear
    z_floor_bot = z_floor_top - args.floor_t
    z_nut_top = z_floor_bot + args.nut_depth

    # ---- assert we actually mate -----------------------------------------
    up = upper_half()
    for what, mine, theirs in (("outer width", out_w, up["x1"] - up["x0"]),
                               ("outer depth", out_h, up["y1"] - up["y0"]),
                               ("outer x0", x0, up["x0"]), ("outer x1", x1, up["x1"]),
                               ("outer y0", y0, up["y0"]), ("outer y1", y1, up["y1"]),
                               ("mating plane", z_split, up["z_bot"])):
        if abs(mine - theirs) > 1e-3:
            sys.exit("HALVES DO NOT MEET: %s is %.3f here but %.3f on the lens box.\n"
                     "Both halves must be generated with the same --wall and "
                     "--pcb-clear.\n(lens box outer %.3f x %.3f, mating face z = %.3f)"
                     % (what, mine, theirs, up["x1"] - up["x0"],
                        up["y1"] - up["y0"], up["z_bot"]))

    # ---- cable exits ------------------------------------------------------
    open_faces = set(f.upper() for f in args.open_face)
    for f in open_faces:
        if f not in ("N", "S", "E", "W"):
            sys.exit("--open-face %r is not N/S/E/W" % f)
    windows = [parse_window(s, board, z_stack_bot) for s in args.window]
    if windows and not args.window_source:
        sys.exit("--window given without --window-source.\n"
                 "Say where the numbers came from. There is no CAD for the Hd+, Ft+ or\n"
                 "Pt V2 in this repo, so a cutout is either MEASURED or INVENTED, and the\n"
                 "STEP file cannot tell them apart. Cite it and it is stamped into the file.")
    if not windows and not open_faces and not args.closed_box:
        sys.exit("REFUSING to emit a sealed base box.\n"
                 "This half encloses the Hd+, Ft+ and Pt V2 -- every external connector\n"
                 "in the design (HDMI, USB 3, power, JTAG) is inside it, and as drawn no\n"
                 "cable can leave. Choose one:\n"
                 "  --open-face E                                leave a whole wall off\n"
                 "                                               (needs no measurements)\n"
                 "  --window E:20:12:3:9 --window-source '...'   a measured cutout\n"
                 "  --closed-box                                 you really do want it sealed")

    for wn in windows:
        if wn["z0"] < z_floor_top - EPS or wn["z1"] > z_split + EPS:
            sys.exit("window %r spans z %.2f..%.2f, outside the wall (%.2f..%.2f)."
                     % (wn["spec"], wn["z0"], wn["z1"], z_floor_top, z_split))

    # ---- assertions on the geometry we can actually check -----------------
    # The support bosses land on the Hd+, which is not modelled anywhere. What
    # IS checkable is that no boss escapes the part's OUTER surface.
    #
    # A boss is EXPECTED to overlap the wall ring, and it is not an error. The
    # corner holes are only 2.5 mm in from the board edge, so a boss big enough
    # to carry an M2 nut (4.0 AF, so 7.0 OD after the web) reaches past the
    # cavity and into the wall by ~0.25 mm. gen_lens_box.py's columns do the
    # same thing for the same reason and say so outright. On a printed part the
    # two solids union, and a boss tied into the wall is stiffer than one
    # standing alone -- this is the good case. What is NOT allowed is a boss
    # breaking through to the outside, which makes a part that looks fine on
    # screen and prints with a hole in its side.
    boss_r = args.boss_dia / 2.0
    for px, py in mh:
        out = min(px - boss_r - x0, x1 - px - boss_r,
                  py - boss_r - y0, y1 - py - boss_r)
        if out < args.wall_clear:
            sys.exit("BOSS AT (%.2f, %.2f) BREAKS THE OUTER SURFACE: dia %.2f leaves "
                     "%.2f mm to the %.2f x %.2f outer profile, needs %.2f.\n"
                     "Shrink --boss-dia or thicken --wall."
                     % (px, py, args.boss_dia, out, out_w, out_h, args.wall_clear))
    if args.boss_dia - args.nut_af < 2 * args.min_web:
        sys.exit("NUT POCKET TOO WIDE: --boss-dia %.2f minus --nut-af %.2f leaves %.2f mm "
                 "of web, need %.2f" % (args.boss_dia, args.nut_af,
                                        (args.boss_dia - args.nut_af) / 2.0, args.min_web))
    if args.floor_t <= args.nut_depth:
        sys.exit("FLOOR TOO THIN: --floor-t %.2f must exceed --nut-depth %.2f, or the "
                 "nut pocket breaks through" % (args.floor_t, args.nut_depth))

    # ---- geometry ---------------------------------------------------------
    desc = "Base box -- lower half, encloses the board stack, mates to camera_lens_box"
    if windows:
        desc += " | window source: %s" % args.window_source
    elif open_faces:
        desc += " | open face(s): %s" % ",".join(sorted(open_faces))
    else:
        desc += " | SEALED: no cable exit"
    step = sw.StepFile("camera_base_box", desc, args.timestamp, tool="gen_base_box.py")
    expected = {}

    # Walls, in Z bands so a window can change the profile with height. A prism
    # has vertical walls and cannot do that on its own; stacking bands can. It
    # is the same trick gen_socket_tile.py uses to turn a groove onto a face.
    zbands = sorted(set([z_floor_top, z_split]
                        + [w["z0"] for w in windows] + [w["z1"] for w in windows]))
    nbar = 0
    for bi in range(len(zbands) - 1):
        za, zc = zbands[bi], zbands[bi + 1]
        if zc - za < EPS:
            continue
        for edge, rectangle in sorted(bars(x0, y0, x1, y1, t).items()):
            if edge in open_faces:
                continue
            active = [w for w in windows if w["edge"] == edge
                      and w["z0"] <= za + EPS and w["z1"] >= zc - EPS]
            for rx0, ry0, rx1, ry1 in split_bar(rectangle, edge, active):
                corners = set()
                if edge == "S":
                    if abs(rx0 - x0) < EPS:
                        corners.add("SW")
                    if abs(rx1 - x1) < EPS:
                        corners.add("SE")
                if edge == "N":
                    if abs(rx0 - x0) < EPS:
                        corners.add("NW")
                    if abs(rx1 - x1) < EPS:
                        corners.add("NE")
                poly = bar_poly(rx0, ry0, rx1, ry1, args.corner_r, corners, args.arc_seg)
                nm = "wall_%s_%d_%d" % (edge, bi, nbar)
                step.prism(poly, za, zc, nm, COLORS["box"])
                expected[nm] = abs(sw.signed_area(poly)) * (zc - za)
                nbar += 1

    # The floor, in two bands: a nut pocket underneath, a screw bore above.
    floor_outer = sw.rounded_rect(cx, cy, out_w, out_h, args.corner_r, args.arc_seg)

    nm = "floor_lower"
    pockets = [sw.reverse(hexagon(px, py, args.nut_af)) for px, py in mh]
    step.prism(floor_outer, z_floor_bot, z_nut_top, nm, COLORS["box"], holes=pockets)
    expected[nm] = ((abs(sw.signed_area(floor_outer))
                     - sum(abs(sw.signed_area(h)) for h in pockets))
                    * (z_nut_top - z_floor_bot))

    nm = "floor_upper"
    bores = [sw.reverse(sw.circle(px, py, args.screw_dia / 2.0, args.segments))
             for px, py in mh]
    step.prism(floor_outer, z_nut_top, z_floor_top, nm, COLORS["box"], holes=bores)
    expected[nm] = ((abs(sw.signed_area(floor_outer))
                     - sum(abs(sw.signed_area(h)) for h in bores))
                    * (z_floor_top - z_nut_top))

    # Four support bosses: the surface the whole board stack actually rests on.
    for i, (px, py) in enumerate(mh):
        nm = "boss%d" % (i + 1)
        ring = sw.circle(px, py, boss_r, args.segments)
        bore = sw.reverse(sw.circle(px, py, args.screw_dia / 2.0, args.segments))
        step.prism(ring, z_floor_top, z_stack_bot, nm, COLORS["boss"], holes=[bore])
        expected[nm] = ((abs(sw.signed_area(ring)) - abs(sw.signed_area(bore)))
                        * (z_stack_bot - z_floor_top))

    text = step.dumps()
    tries = sw.write_verified(OUT, text)
    w = sys.stdout.write
    w("wrote %s  (%d entities, %.1f kB)\n" % (OUT, step.entity_count, len(text) / 1024.0))
    if tries > 1:
        w("           ** read-back MISMATCHED %d time(s); rewritten until it matched.\n"
          "           ** See step_writer.write_verified -- this machine corrupts writes.\n"
          % (tries - 1))
    if args.stl:
        ntri, _ = step.write_stl(os.path.splitext(OUT)[0] + ".stl")
        w("wrote %s  (%d triangles)\n" % (os.path.splitext(OUT)[0] + ".stl", ntri))

    # Screw length is quoted to FULL nut engagement -- head bearing on the lens
    # box washer at +washer_t, tip flush with the underside of the floor. Short
    # of that the nut is only partly engaged, which on an M2 is not much thread.
    grip = args.washer_t - z_nut_top          # head face -> first thread of the nut
    full = args.washer_t - z_floor_bot        # head face -> flush with floor bottom
    stock = [10, 12, 14, 16, 18, 20, 22, 25, 30, 35, 40]
    pick = next((s for s in stock if s >= full - 0.2), int(math.ceil(full)))
    overall_top = up["z_top"] + args.top_t_ref
    w("\nbase box   %.1f x %.1f mm outer, %.2f mm walls, cavity %.1f x %.1f, open top\n"
      % (out_w, out_h, t, cav_w, cav_h))
    w("mates      camera_lens_box.step at z = %.3f -- CHECKED against that file\n" % z_split)
    w("stack      camera PCB top 0.00 | Hd+ bottom %.2f  (MEASURED %.2f mm)\n"
      % (z_stack_bot, args.stack_h))
    w("walls      z %.2f -> %.2f  (%.2f mm tall), %d bar solids\n"
      % (z_floor_top, z_split, z_split - z_floor_top, nbar))
    w("floor      z %.2f -> %.2f  (%.2f thick), nut pocket %.1f AF x %.1f deep\n"
      % (z_floor_bot, z_floor_top, args.floor_t, args.nut_af, args.nut_depth))
    w("bosses     4 x %.1f dia, z %.2f -> %.2f (%.2f tall) -- the stack rests on these\n"
      % (args.boss_dia, z_floor_top, z_stack_bot, args.floor_clear))
    w("           at the PCB corner holes (%s)\n"
      % ", ".join("%.1f/%.1f" % p for p in mh))
    w("clearance  %.2f mm under the Hd+ for its bottom-side parts (--floor-clear)\n"
      % args.floor_clear)
    w("screws     4 x M2 x %d socket cap. Grip %.2f mm to the first thread, %.2f mm\n"
      "           to flush with the floor underside (full nut engagement).\n"
      % (pick, grip, full))
    w("           in from the TOP through the lens box driver pockets, out into the\n"
      "           nuts under this floor. The stack is CAPTURED, never in tension.\n")
    w("assembly   %.2f mm tall overall (z %.3f -> %.3f), %.1f x %.1f footprint\n"
      % (overall_top - z_floor_bot, z_floor_bot, overall_top, out_w, out_h))
    if open_faces:
        w("cables     wall(s) %s left OFF entirely -- needs no connector positions\n"
          % ",".join(sorted(open_faces)))
    for wn in windows:
        w("window     %s edge, %.2f..%.2f, z %.2f..%.2f  [%s]\n"
          % (wn["edge"], wn["lo"], wn["hi"], wn["z0"], wn["z1"], args.window_source))
    if args.closed_box and not windows and not open_faces:
        w("\n  ** SEALED BOX: no cable can leave. You asked for this with --closed-box.\n")

    w("\n  ** The four screws must pass the corner holes of the Hd+, Ft+ and Pt V2.\n"
      "     The 2.2 mm pattern is CONFIRMED on the camera board and the Br, and is\n"
      "     UNVERIFIED on those three -- there is no CAD for them here. An M2 has\n"
      "     0.2 mm of clearance; if a board differs the screw will not pass and you\n"
      "     find out at assembly, with nothing damaged.\n")
    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("volumes    %s\n" % ("OK" if ok else "MISMATCH"))
    return 0 if ok else 1


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stack-h", type=float, default=STACK_H,
                   help="MEASURED camera PCB top surface -> Hd+ bottom surface, mm "
                        "(default %.2f). Not derived from board thickness x gap." % STACK_H)
    p.add_argument("--z-split", type=float, default=Z_SPLIT,
                   help="mating plane; must equal the lens box's wall bottom")
    p.add_argument("--wall", type=float, default=3.0, help="wall thickness, mm")
    p.add_argument("--pcb-clear", type=float, default=0.75,
                   help="board edge to cavity wall, mm -- must match the lens box")
    p.add_argument("--floor-clear", type=float, default=2.0,
                   help="gap under the Hd+ for its bottom-side parts, mm")
    p.add_argument("--floor-t", type=float, default=3.0, help="floor thickness, mm")
    p.add_argument("--boss-dia", type=float, default=7.0, help="support boss OD, mm")
    p.add_argument("--screw-dia", type=float, default=2.40, help="M2 clearance bore, mm")
    p.add_argument("--nut-af", type=float, default=4.20,
                   help="M2 nut across-flats pocket, mm (the nut itself is 4.0)")
    p.add_argument("--nut-depth", type=float, default=2.00, help="nut pocket depth, mm")
    p.add_argument("--min-web", type=float, default=1.00,
                   help="minimum material between the nut pocket and the boss OD, mm")
    p.add_argument("--wall-clear", type=float, default=0.40,
                   help="minimum gap from a boss to a wall, mm")
    p.add_argument("--washer-t", type=float, default=1.20,
                   help="lens box washer thickness -- where the screw head lands")
    p.add_argument("--top-t-ref", type=float, default=3.0,
                   help="lens box top face thickness, for the overall height report")
    p.add_argument("--open-face", action="append", default=[],
                   help="N/S/E/W -- leave a whole wall off for cables. Repeatable.")
    p.add_argument("--window", action="append", default=[],
                   help="edge:along:width:zbot:ztop -- a measured cutout. Repeatable. "
                        "Requires --window-source.")
    p.add_argument("--window-source", default=None,
                   help="WHERE THE WINDOW NUMBERS CAME FROM. Stamped into the STEP.")
    p.add_argument("--closed-box", action="store_true",
                   help="emit a sealed box with no cable exit at all")
    p.add_argument("--corner-r", type=float, default=2.0)
    p.add_argument("--arc-seg", type=int, default=6)
    p.add_argument("--segments", type=int, default=64)
    p.add_argument("--timestamp", default="2026-08-25T00:00:00")
    p.add_argument("--no-stl", dest="stl", action="store_false")
    sys.exit(build(p.parse_args()))


if __name__ == "__main__":
    main()
