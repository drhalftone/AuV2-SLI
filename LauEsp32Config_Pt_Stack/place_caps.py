"""Place the decoupling / bulk capacitors next to the power pins they serve.

Run with KiCad's Python (pcbnew API):
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" place_caps.py [--dry-run]

Why (review 2026-09-16): after the spring placement the caps sat 6-15 mm from their pins --
C4 (the ESP32's 100 nF) 8.1 mm, C8 (the microSD's 100 nF) 12.8 mm. On TOP there is no room:
U1's 3V3 pin (module pin 3) is beside the notch where the antenna-cable keep-out is, and
J7's VDD pin (pad 4) is under the socket body at the left edge. The BOTTOM face is
measured clear (>= 2.67 mm to the Hd outside the HDMI area, README 6.3), so:

  C4 100n, C3 10u, C2/C1 22u  -> BOTTOM, under U1 pin 3, in that priority order
  C8 100n                     -> BOTTOM, nearest legal spot to J7 pad 4 (the HDMI
                                 no-footprint area covers the pad itself)
  C7 10u                      -> TOP, nearest legal spot to J7 pad 4 (bulk, a few mm is fine)
  C5 (EN RC), C6 (U2, 2.3 mm) -> unchanged
C8 may overlap the SD pull-ups R31-R36 (YIELD); ALWAYS run place_resistors_near_pins.py after
this script so they are re-placed around the caps.

Cost per cap = w_pwr * distance(its +3V3 pad, the served power pin)
             + w_gnd * distance(its GND pad, the nearest GND pad of the same part)
Search: every legal 0.1 mm grid spot in four orientations on the target side (courtyard +
GAP clear of every same-side footprint, the side's keep-outs, EDGE). Caps are placed in
priority order, so the 100 nF always gets the closest spot.
"""
import math
import os
import sys

import numpy as np
import pcbnew

from kisave import safe_save  # never SaveBoard onto the real file (SCHEMATIC.md 18)

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
OX, OY, W, H = 100.0, 60.0, 55.0, 45.0
EDGE, GAP, GRID = 0.5, 0.25, 0.1
HOLES = [(cx - 3.0, cy - 3.0, cx + 3.0, cy + 3.0) for cx, cy in ((2.5, 2.5), (52.5, 2.5), (2.5, 42.5), (52.5, 42.5))]
NOTCH = [(49.0, 8.0, W, 37.0), (53.0, 6.0, W, 8.0), (53.0, 37.0, W, 39.0)]
KEEPOUTS = {
    "bottom": [(0.0, 17.5, 9.5, 40.0)] + NOTCH + HOLES,           # HDMI clearance (Hd below)
    "top": [(44.5, 28.0, 49.5, 34.5)] + NOTCH + HOLES,            # antenna connector + coax exit
}
# parts a cap may displace: the SD pull-ups are position-insensitive (DC), so C8 takes the
# spot next to the HDMI area first and place_resistors_near_pins.py re-places them after.
YIELD = {"C8": {"R31", "R32", "R33", "R34", "R35", "R36"}}
# cap: (side, served part, power pad number, w_pwr, w_gnd) -- in placement priority order
PLAN = [
    ("C4", "bottom", "U1", "3", 5.0, 1.0),
    ("C3", "bottom", "U1", "3", 3.0, 1.0),
    ("C2", "bottom", "U1", "3", 2.0, 0.5),
    ("C1", "bottom", "U1", "3", 2.0, 0.5),
    ("C8", "bottom", "J7", "4", 5.0, 1.0),
    ("C7", "top", "J7", "4", 3.0, 1.0),
]
mm = pcbnew.ToMM


def xy(v):
    return mm(v.x) - OX, mm(v.y) - OY


def cy_box(fp):
    fp.BuildCourtyardCaches()
    b = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd).BBox()
    return mm(b.GetLeft()) - OX, mm(b.GetTop()) - OY, mm(b.GetRight()) - OX, mm(b.GetBottom()) - OY


def main():
    board = pcbnew.LoadBoard(BOARD)
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    gx = np.arange(0, W + 1e-9, GRID)
    gy = np.arange(0, H + 1e-9, GRID)
    CX, CY = np.meshgrid(gx, gy)
    report = []
    for ref, side, part, pwr_pad, w_pwr, w_gnd in PLAN:
        f = fps[ref]
        want_bottom = side == "bottom"
        if f.IsFlipped() != want_bottom:
            f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)

        target = [xy(p.GetPosition()) for p in fps[part].Pads() if p.GetNumber() == pwr_pad][0]
        gnds = [xy(p.GetPosition()) for p in fps[part].Pads() if p.GetNetname() == "GND"]
        cpads = {p.GetNetname(): p for p in f.Pads()}
        if set(cpads) != {"+3V3", "GND"}:
            raise SystemExit(f"{ref}: expected +3V3/GND pads, got {sorted(cpads)}")
        pos0, ang0 = f.GetPosition(), f.GetOrientationDegrees()
        before_pwr = math.dist(xy(cpads["+3V3"].GetPosition()), target)

        # per-orientation geometry, measured on the part itself after the flip
        geom = {}
        for ang in (0, 90, 180, 270):
            f.SetOrientationDegrees(ang)
            c = xy(f.GetPosition())
            b = cy_box(f)
            geom[ang] = (np.subtract(xy(cpads["+3V3"].GetPosition()), c), np.subtract(xy(cpads["GND"].GetPosition()), c),
                         c[0] - b[0], b[2] - c[0], c[1] - b[1], b[3] - c[1])
        f.SetOrientationDegrees(ang0)

        same_side = [cy_box(o) for r, o in fps.items()
                     if r != ref and o.IsFlipped() == want_bottom and r not in YIELD.get(ref, ())]
        best = (math.inf, None)
        for ang, (op, og, l, r_, t, bt) in geom.items():
            ok = (CX - l >= EDGE) & (CY - t >= EDGE) & (CX + r_ <= W - EDGE) & (CY + bt <= H - EDGE)
            for (x0, y0, x1, y1), g in [(o, GAP) for o in same_side] + [(k, 0.0) for k in KEEPOUTS[side]]:
                ok &= ~((CX + r_ + g > x0) & (CX - l - g < x1) & (CY + bt + g > y0) & (CY - t - g < y1))
            cost = w_pwr * np.hypot(CX + op[0] - target[0], CY + op[1] - target[1])
            gd = np.full(CX.shape, np.inf)
            for gxp, gyp in gnds:
                gd = np.minimum(gd, np.hypot(CX + og[0] - gxp, CY + og[1] - gyp))
            cost = np.where(ok, cost + w_gnd * gd, np.inf)
            k = int(np.argmin(cost))
            if cost.flat[k] < best[0]:
                i, j = np.unravel_index(k, cost.shape)
                best = (float(cost.flat[k]), (float(gx[j]), float(gy[i]), ang))
        if best[1] is None:
            raise SystemExit(f"no legal spot for {ref} on the {side}")
        cx, cy, ang = best[1]
        f.SetOrientationDegrees(ang)
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(OX + cx), pcbnew.FromMM(OY + cy)))
        after_pwr = math.dist(xy(cpads["+3V3"].GetPosition()), target)
        after_gnd = min(math.dist(xy(cpads["GND"].GetPosition()), g) for g in gnds)
        report.append((ref, f.GetValue(), side, part, pwr_pad, before_pwr, after_pwr, after_gnd, (cx, cy, ang)))

    print(f"{'cap':4} {'value':5} {'side':6} {'serves':9} {'+3V3 pad to pin, before -> after':34} GND pad to nearest GND")
    for ref, val, side, part, pad, b, a, g, (cx, cy, ang) in report:
        print(f"{ref:4} {val:5} {side:6} {part} pin {pad:3} {b:6.2f} -> {a:5.2f} mm {'':15} {g:5.2f} mm   at ({cx:.1f}, {cy:.1f}) rot {ang}")
    if "--dry-run" in sys.argv:
        return
    safe_save(board, BOARD)
    print("saved", BOARD)


if __name__ == "__main__":
    main()
