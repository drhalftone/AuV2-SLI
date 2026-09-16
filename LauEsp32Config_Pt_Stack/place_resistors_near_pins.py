"""Place the bottom-side 0402 resistors by FUNCTION: terminations at their driver pin.

Run with KiCad's Python (pcbnew API):
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" place_resistors_near_pins.py [--dry-run]

Why by function (2026-09-16). An earlier version pulled every resistor toward the connector
pins (weight x3). That put the 33 ohm series terminations at the WRONG end: a source
termination only works within a few mm of the pin DRIVING the line (edges ~1 ns, ~6-7 ps/mm,
so "a few mm" is electrically the same point). It also packed 17 resistors around J3
for nothing. So each resistor now has an explicit anchor list in SPEC:

  TERM  (w 5)  33R source terminations -> the pin that drives the line
                 R20/21/23/24/25 FAB SCK/MOSI/CS/IO2/IO3 and R30 SD_CLK -> U1 (ESP32)
                 R10/11/12 rev A straps on TCK/TMS/TDI                -> U2 output (1Y/2Y/3Y)
  PAIR  (w 5)  rev B strap R14-R17 -> its rev A partner's centre (keep the pairs for rework)
  NEAR  (w 1)  mild: gate pull-downs near their FET, TDO straps near U2, rev B near U2
  LOOSE (w 0.3) FPGA-driven series R (MISO, IRQ, SPARE -- no position terminates them on this
               card) and every DC pull-up/down, EN RC, DONE series, LED resistor: only kept
               from wandering; anywhere reasonable is electrically fine.

Cost = sum of weight * distance(the resistor's pad on that net, the anchor pin).
Search: every legal 0.1 mm grid spot, all four orientations, on B.Cu (courtyard + GAP clear of
the DF40 plugs and the other resistors; HDMI no-footprint area, stand-offs, notch, EDGE).
Greedy pass (terminations first, then straps, then the rest), then single moves and
pairwise swaps until nothing improves. U1/U2/Q1/Q2/D1/J7 are on TOP; a bottom resistor
directly under its driver pin is the ideal, one via away.
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
TERM, PAIRW, NEAR, LOOSE = 5.0, 5.0, 1.0, 0.3
KEEPOUTS = [
    (0.0, 17.5, 9.5, 40.0),                                              # HDMI clearance (Hd below)
    (49.0, 8.0, W, 37.0), (53.0, 6.0, W, 8.0), (53.0, 37.0, W, 39.0),    # notch and chamfers
] + [(cx - 3.0, cy - 3.0, cx + 3.0, cy + 3.0) for cx, cy in ((2.5, 2.5), (52.5, 2.5), (2.5, 42.5), (52.5, 42.5))]

# ref -> (role, [(net, anchor ref, weight)], pair partner or None)
SPEC = {
    # source terminations, driven by the ESP32
    "R20": ("term: ESP drives SCK", [("ESP_FAB_SCK", "U1", TERM)], None),
    "R21": ("term: ESP drives MOSI", [("ESP_FAB_MOSI", "U1", TERM)], None),
    "R23": ("term: ESP drives CS", [("ESP_FAB_CS", "U1", TERM)], None),
    "R24": ("term: ESP drives IO2", [("ESP_FAB_IO2", "U1", TERM)], None),
    "R25": ("term: ESP drives IO3", [("ESP_FAB_IO3", "U1", TERM)], None),
    "R30": ("term: ESP drives SD_CLK", [("SD_CLK", "U1", TERM)], None),
    # FPGA-driven: no position on this card terminates them
    "R22": ("series, FPGA drives MISO", [("ESP_FAB_MISO", "U1", LOOSE), ("FAB_MISO", "J3", LOOSE)], None),
    "R26": ("series, FPGA drives IRQ", [("ESP_FAB_IRQ", "U1", LOOSE), ("FAB_IRQ", "J3", LOOSE)], None),
    "R27": ("series, FPGA drives SPARE", [("ESP_FAB_SPARE", "U1", LOOSE), ("FAB_SPARE", "J3", LOOSE)], None),
    # JTAG straps (rev A fitted). U2 drives TCK/TMS/TDI; the FPGA drives TDO.
    "R10": ("term: U2 drives TCK (rev A)", [("JTAG_TCK", "U2", TERM)], None),
    "R11": ("term: U2 drives TMS (rev A)", [("JTAG_TMS", "U2", TERM)], None),
    "R12": ("term: U2 drives TDI (rev A)", [("JTAG_TDI", "U2", TERM)], None),
    "R13": ("strap TDO (rev A)", [("JTAG_TDO", "U2", NEAR)], None),
    "R14": ("strap TCK (rev B, DNP)", [("JTAG_TCK", "U2", NEAR)], "R10"),
    "R15": ("strap TMS (rev B, DNP)", [("JTAG_TMS", "U2", NEAR)], "R11"),
    "R16": ("strap TDI (rev B, DNP)", [("JTAG_TDI", "U2", NEAR)], "R12"),
    "R17": ("strap TDO (rev B, DNP)", [("JTAG_TDO", "U2", NEAR)], "R13"),
    # DC / slow
    "R1": ("EN pull-up", [("ESP_EN", "U1", LOOSE)], None),
    "R2": ("OE pull-up", [("JTAG_OE_N", "U2", LOOSE)], None),
    "R3": ("TDO pull-down", [("ESP_TDO", "U2", LOOSE)], None),
    "R4": ("gate pull-down Q1", [("PROG_EN", "Q1", NEAR)], None),
    "R5": ("gate pull-down Q2", [("RESET_EN", "Q2", NEAR)], None),
    "R6": ("DONE series (protection)", [("ESP_DONE", "U1", LOOSE), ("FPGA_DONE", "J3", LOOSE)], None),
    "R7": ("LED resistor", [("LED_A", "D1", LOOSE)], None),
    "R31": ("SD pull-up", [("SD_CMD", "J7", LOOSE)], None),
    "R32": ("SD pull-up", [("SD_D0", "J7", LOOSE)], None),
    "R33": ("SD pull-up", [("SD_D1", "J7", LOOSE)], None),
    "R34": ("SD pull-up", [("SD_D2", "J7", LOOSE)], None),
    "R35": ("SD pull-up", [("SD_D3", "J7", LOOSE)], None),
    "R36": ("SD pull-up", [("SD_CD_N", "J7", LOOSE)], None),
}
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
    res = sorted([r for r, f in fps.items() if r.startswith("R") and f.IsFlipped() and "R_0402" in f.GetFPIDAsString()],
                 key=lambda r: int(r[1:]))
    if set(res) != set(SPEC):
        raise SystemExit(f"SPEC does not match the bottom-side resistors: missing {sorted(set(res) - set(SPEC))}, "
                         f"extra {sorted(set(SPEC) - set(res))}")
    obstacles = [cy_box(f) for r, f in fps.items() if f.IsFlipped() and r not in res]

    # geometry per orientation, measured on a real flipped resistor (KiCad's own flip mirroring)
    t = fps[res[0]]
    p0, a0 = t.GetPosition(), t.GetOrientationDegrees()
    geom = {}
    for ang in (0, 90, 180, 270):
        t.SetOrientationDegrees(ang)
        cx, cy = xy(t.GetPosition())
        pads = {p.GetNumber(): tuple(np.subtract(xy(p.GetPosition()), (cx, cy))) for p in t.Pads()}
        b = cy_box(t)
        geom[ang] = (pads, (b[2] - b[0]) / 2, (b[3] - b[1]) / 2)
    t.SetOrientationDegrees(a0)
    t.SetPosition(p0)

    # resolve SPEC to (resistor pad number, anchor x, anchor y, weight)
    anchors = {}
    for r, (role, specs, partner) in SPEC.items():
        anchors[r] = []
        for net, aref, w in specs:
            rpad = [p.GetNumber() for p in fps[r].Pads() if p.GetNetname().lstrip("/") == net]
            apads = [xy(p.GetPosition()) for p in fps[aref].Pads() if p.GetNetname().lstrip("/") == net]
            if len(rpad) != 1 or not apads:
                raise SystemExit(f"{r}: net {net} not found on {r} and {aref}")
            for ax, ay in apads:
                anchors[r].append((rpad[0], ax, ay, w / len(apads)))

    gx = np.arange(0, W + 1e-9, GRID)
    gy = np.arange(0, H + 1e-9, GRID)
    CX, CY = np.meshgrid(gx, gy)
    base = {}
    for r in res:
        for ang, (pads, _, _) in geom.items():
            c = np.zeros_like(CX)
            for num, ax, ay, w in anchors[r]:
                c += w * np.hypot(CX + pads[num][0] - ax, CY + pads[num][1] - ay)
            base[(r, ang)] = c
    pos = {}

    def box(r, cx, cy, ang):
        _, hw, hh = geom[ang]
        return cx - hw, cy - hh, cx + hw, cy + hh

    def rects(r):
        return [(o, GAP) for o in obstacles] + [(k, 0.0) for k in KEEPOUTS] + \
               [(box(q, *pos[q]), GAP) for q in pos if q != r]

    def legal_mask(r, ang):
        _, hw, hh = geom[ang]
        ok = (CX - hw >= EDGE) & (CY - hh >= EDGE) & (CX + hw <= W - EDGE) & (CY + hh <= H - EDGE)
        for (x0, y0, x1, y1), g in rects(r):
            ok &= ~((CX + hw + g > x0) & (CX - hw - g < x1) & (CY + hh + g > y0) & (CY - hh - g < y1))
        return ok

    def legal_at(r, cx, cy, ang):
        x0, y0, x1, y1 = box(r, cx, cy, ang)
        if x0 < EDGE - 1e-9 or y0 < EDGE - 1e-9 or x1 > W - EDGE + 1e-9 or y1 > H - EDGE + 1e-9:
            return False
        return not any(x1 + g > a0 + 1e-9 and x0 - g < a1 - 1e-9 and y1 + g > b0 + 1e-9 and y0 - g < b1 - 1e-9
                       for (a0, b0, a1, b1), g in rects(r))

    def pair_term(r, cx, cy):
        partner = SPEC[r][2]
        if partner and partner in pos:
            return PAIRW * math.hypot(cx - pos[partner][0], cy - pos[partner][1])
        return 0.0

    def point_cost(r, cx, cy, ang):
        pads, _, _ = geom[ang]
        return sum(w * math.hypot(cx + pads[n][0] - ax, cy + pads[n][1] - ay)
                   for n, ax, ay, w in anchors[r]) + pair_term(r, cx, cy)

    def best_spot(r):
        best = (math.inf, None)
        for ang in geom:
            c = base[(r, ang)]
            partner = SPEC[r][2]
            if partner and partner in pos:
                c = c + PAIRW * np.hypot(CX - pos[partner][0], CY - pos[partner][1])
            c = np.where(legal_mask(r, ang), c, np.inf)
            k = int(np.argmin(c))
            if c.flat[k] < best[0]:
                i, j = np.unravel_index(k, c.shape)
                best = (float(c.flat[k]), (float(gx[j]), float(gy[i]), ang))
        return best

    def primary_dist(r, cx, cy, ang):
        """Distance from the resistor's pad to its heaviest anchor pin."""
        pads, _, _ = geom[ang]
        n, ax, ay, w = max(anchors[r], key=lambda a: a[3])
        return math.hypot(cx + pads[n][0] - ax, cy + pads[n][1] - ay)

    before = {}
    for r in res:
        cx, cy = xy(fps[r].GetPosition())
        before[r] = primary_dist(r, cx, cy, int(round(fps[r].GetOrientationDegrees())) % 360)

    def rank(r):
        w = max(a[3] * (len([b for b in anchors[r] if b[1:3] != a[1:3]]) or 1) for a in anchors[r])
        return (-max(s[2] for s in SPEC[r][1]), SPEC[r][2] is not None, int(r[1:]))

    for r in sorted(res, key=rank):
        c, spot = best_spot(r)
        if spot is None:
            raise SystemExit(f"no legal spot for {r}")
        pos[r] = spot
    t0 = sum(point_cost(r, *pos[r]) for r in res)

    for rnd in range(20):
        improved = False
        for r in res:
            cur = point_cost(r, *pos[r])
            old = pos.pop(r)
            c, spot = best_spot(r)
            if spot is not None and c < cur - 1e-6:
                pos[r], improved = spot, True
            else:
                pos[r] = old
        for i, a in enumerate(res):
            for b in res[i + 1:]:
                pa, pb = pos[a], pos[b]
                cur = point_cost(a, *pa) + point_cost(b, *pb)
                trial = None
                for aa in geom:
                    for bb in geom:
                        na, nb = (pb[0], pb[1], aa), (pa[0], pa[1], bb)
                        pos[a], pos[b] = na, nb
                        c = point_cost(a, *na) + point_cost(b, *nb)
                        if c < cur - 1e-6 and (trial is None or c < trial[0]) and legal_at(a, *na) and legal_at(b, *nb):
                            trial = (c, na, nb)
                        pos[a], pos[b] = pa, pb
                if trial:
                    pos[a], pos[b] = trial[1], trial[2]
                    improved = True
        if not improved:
            break
    t1 = sum(point_cost(r, *pos[r]) for r in res)
    print(f"weighted cost: greedy {t0:.1f} -> local search {t1:.1f} ({rnd + 1} rounds)")
    print(f"{'ref':>4}  {'role':32} {'anchor':6} {'pad-to-anchor-pin mm, before -> after'}")
    for r in sorted(res, key=rank):
        n, ax, ay, w = max(anchors[r], key=lambda a: a[3])
        aref = max(SPEC[r][1], key=lambda s: s[2])[1]
        print(f"{r:>4}  {SPEC[r][0]:32} {aref:6} {before[r]:5.2f} -> {primary_dist(r, *pos[r]):5.2f}")
    for a, b in (("R10", "R14"), ("R11", "R15"), ("R12", "R16"), ("R13", "R17")):
        print(f"pair {a}/{b}: {math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]):.2f} mm apart")
    if "--dry-run" in sys.argv:
        return
    for r, (cx, cy, ang) in pos.items():
        f = fps[r]
        f.SetOrientationDegrees(ang)
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(OX + cx), pcbnew.FromMM(OY + cy)))
    safe_save(board, BOARD)
    print("saved", BOARD)


if __name__ == "__main__":
    main()
