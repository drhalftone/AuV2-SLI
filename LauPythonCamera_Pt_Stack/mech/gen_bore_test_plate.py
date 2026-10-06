#!/usr/bin/env python3
"""gen_bore_test_plate.py -- TEMPORARY: find the bore a C-mount lens threads into.

Writes ../3dmodels/bore_test_plate.step (+ .stl).

The lens box bore (gen_lens_box.py) is a 26.20 mm CLEARANCE hole: the 25.4 mm
1"-32 UN thread plus --bore-clear 0.80. To screw the lens straight into the
plastic instead, the hole has to be smaller -- by how much depends on the
printer, so print this and find out.

A flat plate, --plate-t thick (3.0 = the lens box's --top-t, so the threads cut
the same depth of plastic), with --count holes fly's-eye (hexagonally) packed in two
offset rows, largest top-left. Hole k (k = 1..count) has radius

    bore_r - k * --step        default 13.10 - 0.1 k  ->  dia 26.0, 25.8 ... 24.2

Each hole carries its diameter in raised 7-segment digits below it ("258" =
25.8 mm), so a hole that works can be read straight off the part. PRINT IT FLAT,
the way the lens box top face prints, or the holes will not be the same holes.

The winner goes back into gen_lens_box.py as --bore-clear = dia - 25.4
(negative is fine: it is an interference fit the thread cuts into).
"""

import argparse, math, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_writer as sw
import check_step

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "3dmodels",
                   "bore_test_plate.step")

C_THREAD_OD = 25.4          # 1"-32 UN-2A major diameter (as gen_lens_box.py)
BORE_CLEAR  = 0.80          # gen_lens_box.py --bore-clear default: today's bore

#                a  b  c  d  e  f  g
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
            "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg"}


def digit_rects(ch, x0, y0, w, h, t):
    """7-segment glyph as non-overlapping rectangles, lower-left corner at (x0, y0)."""
    hv = (h - 3 * t) / 2.0                     # height of a vertical segment
    ym = y0 + t + hv                           # bottom of the middle bar
    seg = {
        "a": (x0, y0 + h - t, w, t),
        "g": (x0, ym, w, t),
        "d": (x0, y0, w, t),
        "f": (x0, ym + t, t, hv),
        "b": (x0 + w - t, ym + t, t, hv),
        "e": (x0, y0 + t, t, hv),
        "c": (x0 + w - t, y0 + t, t, hv),
    }
    return [seg[s] for s in SEGMENTS[ch]]


def label_rects(text, cx, y0, w, h, t, gap):
    """Rectangles for a centred row of digits, a decimal dot before the last one."""
    n = len(text)
    total = n * w + (n - 1) * gap + (t + gap)
    x = cx - total / 2.0
    out = []
    for i, ch in enumerate(text):
        if i == n - 1:                         # the decimal point
            out.append((x, y0, t, t))
            x += t + gap
        out += digit_rects(ch, x, y0, w, h, t)
        x += w + gap
    return out


def main(args):
    bore_r = (C_THREAD_OD + BORE_CLEAR) / 2.0
    radii = [bore_r - (k + 1) * args.step for k in range(args.count)]
    if radii[-1] <= 0:
        sys.exit("STEP TOO LARGE: hole %d would have radius %.2f" % (args.count, radii[-1]))

    # FLY'S-EYE (hexagonal) packing: two rows, the second shifted half a pitch so
    # every hole nests between two of the other row. The triangular gaps are too
    # small for digits, so each row is labelled on its OUTSIDE edge.
    cols = int(math.ceil(args.count / 2.0))
    dmax = 2.0 * radii[0]
    pitch = dmax + args.web
    pitch_y = pitch * math.sqrt(3.0) / 2.0
    band = args.web / 2.0 + args.text_h + args.web     # label strip outside a row
    width = (cols - 1) * pitch + pitch / 2.0 + dmax + 2.0 * args.web
    height = band + dmax + pitch_y + band

    step = sw.StepFile("bore_test_plate",
                       "TEMPORARY C-mount thread-in bore test plate",
                       time.strftime("%Y-%m-%dT%H:%M:%S"))
    holes, labels, expected = [], [], {}
    for k, r in enumerate(radii):
        row, col = divmod(k, cols)
        cx = args.web + dmax / 2.0 + col * pitch + row * pitch / 2.0
        cy = height - band - dmax / 2.0 - row * pitch_y
        holes.append(sw.reverse(sw.circle(cx, cy, r, args.segments)))
        txt = "%03d" % int(round(20.0 * r))    # diameter in tenths of a mm
        if row == 0:
            ly = cy + dmax / 2.0 + args.web / 2.0
        else:
            ly = cy - dmax / 2.0 - args.web / 2.0 - args.text_h
        labels.append((txt, label_rects(txt, cx, ly, args.text_h * 0.55, args.text_h,
                                        args.stroke, args.stroke)))

    outline = sw.rounded_rect(width / 2.0, height / 2.0, width, height, 3.0)
    step.prism(outline, 0.0, args.plate_t, "plate", (0.85, 0.85, 0.85), holes=holes)
    expected["plate"] = (abs(sw.signed_area(outline))
                         - sum(abs(sw.signed_area(h)) for h in holes)) * args.plate_t

    for k, (txt, rects) in enumerate(labels):
        for j, (x, y, w, h) in enumerate(rects):
            nm = "label%d_%s_%d" % (k + 1, txt, j)
            step.prism(sw.rect(x + w / 2.0, y + h / 2.0, w, h), args.plate_t,
                       args.plate_t + args.text_z, nm, (0.2, 0.2, 0.2))
            expected[nm] = w * h * args.text_z

    open(OUT, "w", newline="\n").write(step.dumps())
    ntri, _ = step.write_stl(os.path.splitext(OUT)[0] + ".stl")

    w = sys.stdout.write
    w("wrote %s\nwrote %s  (%d triangles)\n" % (OUT, os.path.splitext(OUT)[0] + ".stl", ntri))
    w("plate      %.1f x %.1f x %.2f mm, %d holes fly's-eye packed, pitch %.1f\n"
      % (width, height, args.plate_t, args.count, pitch))
    w("today      bore dia %.2f (= %.1f + %.2f clearance)\n" % (2 * bore_r, C_THREAD_OD, BORE_CLEAR))
    for k, r in enumerate(radii):
        w("hole %2d    r %.2f  dia %.2f  -> --bore-clear %+.2f\n"
          % (k + 1, r, 2 * r, 2 * r - C_THREAD_OD))
    ok = check_step.validate(OUT, expected, out=open(os.devnull, "w"))
    w("check_step %s\n" % ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--count", type=int, default=10, help="number of holes")
    p.add_argument("--step", type=float, default=0.10, help="radius decrement per hole, mm")
    p.add_argument("--plate-t", type=float, default=3.0,
                   help="plate thickness, mm (gen_lens_box.py --top-t)")
    p.add_argument("--web", type=float, default=4.0, help="material between holes, mm")
    p.add_argument("--text-h", type=float, default=5.0, help="digit height, mm")
    p.add_argument("--stroke", type=float, default=0.8, help="digit stroke width, mm")
    p.add_argument("--text-z", type=float, default=0.6, help="digit relief height, mm")
    p.add_argument("--segments", type=int, default=128)
    sys.exit(main(p.parse_args()))
