"""gen_stack_shroud.py -- CNC aluminium shroud rings for the Alchitry board stack.

Writes ../3dmodels/stack_shroud_<layer>.step (+ .stl), one file per layer.

    z = 0       the BOTTOM face of the BOTTOM board (Hd+)
    +X / +Y     the KiCad top view of the camera board, +y UP
                (KiCad y runs DOWN, so model_y = (board_top_y) - kicad_y)

WHAT THIS IS. One rectangular ring per board. Each ring surrounds its board and
the gap above it, so the rings stack beside the boards to form a tube. Cables
leave through full-height notches cut in the wall.

    ring 3  camera board  <- terminal board, carries the lens
    ring 2  Pt V2
    ring 1  Ft+
    ring 0  Hd+           <- bottom

THE SHROUD GOES OUTBOARD OF THE PCB, NOT BETWEEN THE BOARDS. The measured gap
between board faces is 3.68 mm and it is already occupied by components and
connectors, so there is nothing to fit into. The rings clear the 55 x 45 outline
laterally instead.

=============================================================================
THE THING THAT WILL DAMAGE HARDWARE IF IGNORED
=============================================================================
These rings must not end up TALLER than the boards they surround. Four rigid
aluminium rings that total 0.2 mm over will pry the stack apart, and the only
thing holding it together is a row of 0.4 mm-pitch DF40 connectors. Aluminium
will not yield the way a printed part does; the connector will.

So the generator supports exactly two schemes and refuses to blur them:

  --scheme shroud   (default) rings are SHORT by --relief (0.25 mm each) and
                    touch nothing. The DF40s set the spacing, as they do today.
                    Tolerance stacking is harmless. Choose this unless you have
                    a reason not to.

  --scheme spacer   rings are machined to the pitch EXACTLY and become the datum:
                    corner bolts clamp the stack and the DF40s carry signal only.
                    Mechanically the better answer -- a lens cantilevered on top
                    of four boards held together by 0.4 mm connectors is the weak
                    point of the current stack -- but it REQUIRES that all four
                    boards have aligned corner holes, which is unverified. The
                    generator will not emit it without --i-verified-hole-align.

=============================================================================
ALUMINIUM IS CONDUCTIVE -- this is a design input, not a footnote
=============================================================================
Board edges carry ground pours, via stubs and castellations. The default
clearance is therefore 0.75 mm per side, not the 0.3 mm that would be fine in
plastic, and the rings are intended to be BONDED TO GND rather than left
floating: a floating metal enclosure beside 720 Mbps LVDS and the FT601's
source-synchronous bus is a coupling path, and this project has already shipped
one build that streamed at full rate while corrupting half that bus. Bonded, the
same part is a shield. --bond-hole puts a tapped hole in each ring for that.

Do NOT treat anodising as the insulator. It has some dielectric strength but it
is unreliable at edges and easily scratched.

=============================================================================
WHAT IS MEASURED AND WHAT IS ASSUMED
=============================================================================
MEASURED   board-to-board gap 3.68 mm (calipers, on the assembled stack)
READ       55.00 x 45.00 outline, DF40 sites A(16.5,41) B(38,41) C(38,4),
           straight out of LauPythonCamera_Pt_Stack.kicad_pcb on every run
ASSUMED    1.60 mm board thickness -- standard, and consistent with the roadmap's
           "top Z +1.52, bottom -0.08" figures
ASSUMED    all four boards share the 55 x 45 Alchitry V2 outline
UNKNOWN    where every connector actually sits. There is no KiCad for the Hd+,
           Ft+ or Pt V2 in this repo, so NOTCHES DEFAULT TO NOTHING and each one
           has to be declared. Guessing a cutout position would produce a part
           that looks right and does not fit, which is the expensive failure.
           Declare them with --notch (see below) once measured.
"""
import argparse, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "..", "LauPythonCamera_Pt_Stack.kicad_pcb")
OUTDIR = os.path.join(HERE, "..", "3dmodels")

BOARD_T = 1.60          # PCB thickness, mm (assumed -- see header)

# bottom -> top. The camera board is TERMINAL (plugs on B.Cu, no top sockets),
# so it can only ever be the top layer; the others pass DF40 straight through
# and may be reordered freely.
LAYERS = ["hd", "ftplus", "pt", "camera"]

COLORS = {"ring": (0.62, 0.64, 0.67)}    # bare aluminium

# Minimum material between a hole and any edge of the bar it sits in. A hole that
# breaks out of its wall does not fail loudly -- it produces a solid that is not
# watertight, which a mesher may quietly "repair" into a part that gets machined
# wrong. So it is checked here and refused.
EDGE_MIN = 0.80


def place_hole(bar, cx, cy, dia, what):
    """Circle in a bar, or die trying. Returns the outline for prism(holes=...)."""
    bx0, by0, bx1, by1 = bar
    r = dia / 2.0
    slack = min(cx - r - bx0, bx1 - cx - r, cy - r - by0, by1 - cy - r)
    if slack < EDGE_MIN:
        sys.exit("%s: dia %.2f at (%.2f, %.2f) leaves %.2f mm of material, needs "
                 "%.2f.\nThicken --wall or shrink the hole -- a hole that breaks "
                 "out of the wall\nmakes a non-watertight solid, not an error."
                 % (what, dia, cx, cy, slack, EDGE_MIN))
    return sw.circle(cx, cy, r)


def read_outline():
    """Board outline from the KiCad file. Re-read every run, never hard-coded."""
    s = open(PCB, encoding="utf-8", errors="replace").read()
    xs, ys = [], []
    for m in re.finditer(r'\(gr_(?:line|arc|rect)\b(.*?)\)\s*\(layer "Edge\.Cuts"', s, re.S):
        for a, b in re.findall(r'\((?:start|end|mid|center)\s+([-\d.]+)\s+([-\d.]+)\)',
                               m.group(1)):
            xs.append(float(a)); ys.append(float(b))
    if not xs:
        sys.exit("no Edge.Cuts geometry found in %s" % PCB)
    return min(xs), max(xs), min(ys), max(ys)


def parse_notch(text):
    """--notch layer:edge:centre:width  e.g. hd:S:27.5:10.0

    edge is N/S/E/W in the model frame (+y up). centre and width are along that
    edge in board coordinates, measured from the board's origin corner.
    """
    parts = text.split(":")
    if len(parts) != 4:
        sys.exit("--notch wants layer:edge:centre:width, got %r" % text)
    layer, edge, c, w = parts[0], parts[1].upper(), float(parts[2]), float(parts[3])
    if layer not in LAYERS:
        sys.exit("--notch layer %r is not one of %s" % (layer, LAYERS))
    if edge not in "NSEW":
        sys.exit("--notch edge %r is not N/S/E/W" % edge)
    if w <= 0:
        sys.exit("--notch width must be positive")
    return dict(layer=layer, edge=edge, c=c, w=w)


def bars(x0, y0, x1, y1, t):
    """Split a rectangular ring into four non-overlapping wall bars.

    N and S span the FULL width so the corners belong to them; E and W fill the
    remainder. No overlapping solids, no coincident-face soup, and every bar is a
    plain rectangle -- which is also how it gets machined.
    """
    xi0, yi0, xi1, yi1 = x0 + t, y0 + t, x1 - t, y1 - t
    return {
        "S": (x0, y0, x1, yi0),
        "N": (x0, yi1, x1, y1),
        "W": (x0, yi0, xi0, yi1),
        "E": (xi1, yi0, x1, yi1),
    }


def split_bar(rect, edge, notches, x0, y0):
    """Cut full-height notches out of one wall bar, returning the pieces left."""
    bx0, by0, bx1, by1 = rect
    horizontal = edge in "NS"
    segs = [(bx0, bx1)] if horizontal else [(by0, by1)]
    origin = x0 if horizontal else y0
    for n in notches:
        lo, hi = origin + n["c"] - n["w"] / 2.0, origin + n["c"] + n["w"] / 2.0
        out = []
        for a, b in segs:
            if hi <= a or lo >= b:
                out.append((a, b)); continue
            if lo > a: out.append((a, lo))
            if hi < b: out.append((hi, b))
        segs = out
    pieces = []
    for a, b in segs:
        if b - a < 0.5:                 # a sliver is not a machinable feature
            continue
        pieces.append((a, by0, b, by1) if horizontal else (bx0, a, bx1, b))
    return pieces


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--gap", type=float, default=3.68,
                   help="measured board-to-board gap, mm (default 3.68)")
    p.add_argument("--board-t", type=float, default=BOARD_T)
    p.add_argument("--clearance", type=float, default=0.75,
                   help="ring inner face to board edge, mm. 0.75 because the ring "
                        "is CONDUCTIVE and board edges are not clean (default 0.75)")
    # 4.0, not the 2.5 that looks sufficient: the wall also has to carry a dowel
    # and a ground-bond screw. At 2.5 mm an M3 bond tap drill (2.5) is the ENTIRE
    # wall and a 2.0 dowel leaves 0.25 mm. The STL watertightness check caught
    # both -- see EDGE_MIN below, which now refuses them outright.
    p.add_argument("--wall", type=float, default=4.0, help="wall thickness, mm")
    p.add_argument("--scheme", choices=("shroud", "spacer"), default="shroud")
    p.add_argument("--relief", type=float, default=0.25,
                   help="shroud scheme: how much SHORT each ring is cut so it "
                        "cannot load the DF40s (default 0.25)")
    p.add_argument("--i-verified-hole-align", action="store_true",
                   help="required for --scheme spacer: you have confirmed all four "
                        "boards have aligned corner holes")
    p.add_argument("--top-clear", type=float, default=2.0,
                   help="material above the top board, mm (default 2.0)")
    p.add_argument("--dowel-dia", type=float, default=2.0,
                   help="diagonal dowel holes for ring-to-ring registration")
    p.add_argument("--bolt-dia", type=float, default=2.40,
                   help="corner through-bore for M2 (spacer scheme)")
    p.add_argument("--bond-hole", type=float, default=1.60,
                   help="tap drill for an M2 ground-bond screw, one per ring. M2, "
                        "not M3: an M3 tap drill is 2.5 mm and does not fit a wall "
                        "that also needs edge distance either side")
    p.add_argument("--notch", action="append", default=[],
                   help="layer:edge:centre:width -- repeatable. See the header. "
                        "Requires --notch-source.")
    p.add_argument("--notch-source", default=None,
                   help="WHERE THE NOTCH NUMBERS CAME FROM. Required whenever any "
                        "--notch is given, and stamped into the STEP description so "
                        "the provenance travels with the file. e.g. 'calipers on the "
                        "assembled stack 2026-08-24' or 'Alchitry Pt V2 STEP rev C'. "
                        "This exists because a set of placeholder notches invented "
                        "to exercise the code got written to STEP, and a STEP file "
                        "looks authoritative in a way a caveat in conversation does "
                        "not. A shop cannot tell a measured cutout from a guessed one.")
    p.add_argument("--stl", action="store_true", help="also write .stl")
    args = p.parse_args()

    if args.scheme == "spacer" and not args.i_verified_hole_align:
        sys.exit("--scheme spacer sets the board pitch with metal and clamps the\n"
                 "stack through corner bolts. That needs aligned corner holes on\n"
                 "ALL FOUR boards, which nothing in this repo verifies. Check the\n"
                 "boards, then pass --i-verified-hole-align.")

    if args.notch and not args.notch_source:
        sys.exit("--notch given without --notch-source.\n"
                 "Say where the numbers came from. Nothing in this repo knows where\n"
                 "the Hd+/Ft+/Pt V2 connectors are -- there is no Alchitry board CAD\n"
                 "here, only the DF40 connector models and this project's own boards.\n"
                 "So a notch is either MEASURED or INVENTED, and the STEP file cannot\n"
                 "tell a shop which. Cite it and it gets stamped into the file.")

    kx0, kx1, ky0, ky1 = read_outline()
    bw, bh = kx1 - kx0, ky1 - ky0
    pitch = args.board_t + args.gap

    notches = [parse_notch(t) for t in args.notch]

    # ---- ring footprint --------------------------------------------------
    inner_w = bw + 2 * args.clearance
    inner_h = bh + 2 * args.clearance
    x0 = -(args.clearance + args.wall)
    y0 = -(args.clearance + args.wall)
    x1 = bw + args.clearance + args.wall
    y1 = bh + args.clearance + args.wall

    w = sys.stdout.write
    w("stack shroud -- CNC aluminium\n")
    w("  board            %.2f x %.2f mm (read from KiCad)\n" % (bw, bh))
    w("  gap / pitch      %.2f / %.2f mm\n" % (args.gap, pitch))
    w("  clearance / wall %.2f / %.2f mm\n" % (args.clearance, args.wall))
    w("  ring outer       %.2f x %.2f mm\n" % (x1 - x0, y1 - y0))
    w("  scheme           %s\n" % args.scheme)

    total_boards = len(LAYERS) * args.board_t + (len(LAYERS) - 1) * args.gap
    w("  board stack      %.2f mm tall (%d boards)\n" % (total_boards, len(LAYERS)))

    if not notches:
        w("\n  ** NO NOTCHES DECLARED. Every ring is a closed band, so no cable can\n"
          "     leave the stack. This is deliberate: connector positions for the\n"
          "     Hd+/Ft+/Pt V2 are not in this repo and a guessed cutout makes a\n"
          "     part that looks right and does not fit. Measure, then --notch.\n")

    ring_total = 0.0
    for i, name in enumerate(LAYERS):
        base = i * pitch
        top_layer = (i == len(LAYERS) - 1)
        h = (args.board_t + args.top_clear) if top_layer else pitch
        if args.scheme == "shroud":
            h -= args.relief
        ring_total += h

        mine = [n for n in notches if n["layer"] == name]
        # Provenance travels WITH the file, not alongside it in a conversation.
        desc = "Alchitry stack shroud ring -- %s layer" % name
        desc += (" | notch source: %s" % args.notch_source) if mine else                 " | no cutouts: closed band"
        st = sw.StepFile("stack_shroud_%s" % name, desc, "2026-08-24T00:00:00")

        made = 0
        for edge, rect in bars(x0, y0, x1, y1, args.wall).items():
            en = [n for n in mine if n["edge"] == edge]
            for j, (rx0, ry0, rx1, ry1) in enumerate(
                    split_bar(rect, edge, en, 0.0, 0.0)):
                bar = (rx0, ry0, rx1, ry1)
                cy = (ry0 + ry1) / 2.0
                holes = []
                # Dowels on OPPOSITE corners so a ring cannot be fitted 180 deg
                # out; ground bond on the S bar. Every one is range-checked.
                if edge == "N" and j == 0:
                    holes.append(place_hole(bar, rx0 + args.wall, cy,
                                            args.dowel_dia, "%s N dowel" % name))
                if edge == "S" and j == 0:
                    holes.append(place_hole(bar, rx1 - args.wall, cy,
                                            args.dowel_dia, "%s S dowel" % name))
                    holes.append(place_hole(bar, (rx0 + rx1) / 2.0, cy,
                                            args.bond_hole, "%s bond tap" % name))
                st.prism(sw.rect((rx0 + rx1) / 2.0, (ry0 + ry1) / 2.0,
                                 rx1 - rx0, ry1 - ry0),
                         base, base + h, "%s_%s%d" % (name, edge, j),
                         COLORS["ring"], holes=holes)
                made += 1

        out = os.path.join(OUTDIR, "stack_shroud_%s.step" % name)
        open(out, "w", newline="\n").write(st.dumps())
        if args.stl:
            st.write_stl(os.path.splitext(out)[0] + ".stl")
        w("  ring %d %-7s z %6.2f -> %6.2f  h %.2f  %d bars  %d notch  -> %s\n"
          % (i, name, base, base + h, h, made, len(mine), os.path.basename(out)))

    # ---- the assertion that protects the connectors ----------------------
    # Compare only the part of the ring stack that sits ALONGSIDE boards. The top
    # ring is meant to stand proud -- that is the lens/lid interface -- so
    # --top-clear is not part of the load path and must come out of the sum. The
    # first version of this check omitted that and refused a correct design,
    # which is the right direction to be wrong in, but still wrong.
    structural = ring_total - args.top_clear
    w("\n  rings alongside boards %.2f mm vs board stack %.2f mm"
      "   (+%.2f mm top clearance)\n" % (structural, total_boards, args.top_clear))
    if args.scheme == "shroud":
        if structural >= total_boards:
            sys.exit("REFUSING: the rings occupy %.2f mm against a %.2f mm board "
                     "stack.\nIn shroud mode they must be SHORTER or they will pry "
                     "the DF40s apart." % (structural, total_boards))
        w("  rings are %.2f mm short -- they cannot load the connectors. Correct "
          "for\n  shroud mode.\n" % (total_boards - structural))
    else:
        w("  spacer mode: the rings SET the pitch. Corner bolts carry the load and\n"
          "  the DF40s carry signal only. Verify hole alignment on every board.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
