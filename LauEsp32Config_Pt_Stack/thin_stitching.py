"""Replace the dense 2 mm GND stitching grid with a sparse, purpose-placed set.

Run with KiCad's Python, KiCad closed:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" thin_stitching.py [--board PATH]

Why (review 2026-09-16, SCHEMATIC.md 21): add_ground_pours.py put 180 GND vias on a 2 mm grid.
Stitching only needs spacing <~ lambda/20 of the highest frequency of concern: ~7 mm for the
80 MHz SPI's content up to ~1 GHz, ~3 mm only where 2.4 GHz WiFi matters (board edges, antenna).
Every GND via also punches a clearance hole in the +3V3 plane (In2), so the extra 2 mm grid
vias cost plane integrity for no benefit.

Removed: exactly the GND vias sitting on the 2 mm grid points (x, y multiples of 2.000 mm from the
board corner). Untouched: DF40 fan-out, plane vias on SMD pads, autorouter vias, and the
companion GND vias beside signal vias.
Added (0.45/0.20 GND, locked), each legality-checked exactly as add_ground_pours.py:
  * INTERIOR grid every 5 mm;
  * EDGE row every 3 mm, EDGE_INSET mm inside the board outline, notch and chamfers included;
  * RING every 3 mm around the antenna no-pour area.
Then all zones are refilled.
"""
import math
import os
import sys

import pcbnew

from kisave import safe_save
import route_prep as RP
import add_ground_pours as AGP

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
FM, TM = pcbnew.FromMM, pcbnew.ToMM
OX, OY, W, H = 100.0, 60.0, 55.0, 45.0
OLD_GRID = 2.0
INTERIOR = 5.0
EDGE_PITCH, EDGE_INSET = 3.0, 1.0
RING_PITCH, RING_GAP = 3.0, 0.8
MIN_SPACING = 0.9


def main():
    path = os.path.abspath(sys.argv[sys.argv.index("--board") + 1]) if "--board" in sys.argv else REAL
    lock = os.path.join(os.path.dirname(path), "~" + os.path.splitext(os.path.basename(path))[0] + ".kicad_pro.lck")
    if os.path.exists(lock):
        raise SystemExit(f"KiCad has this project open ({lock}) -- close it first; nothing written")
    board = pcbnew.LoadBoard(path)

    # 1. remove the 2 mm grid vias -- exact grid points only
    step = FM(OLD_GRID)
    grid = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"
            and (t.GetPosition().x - FM(OX)) % step == 0 and (t.GetPosition().y - FM(OY)) % step == 0]
    before = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    for v in grid:
        board.Remove(v)
    print(f"removed {len(grid)} grid vias ({before} -> {before - len(grid)} vias)")
    if not 150 <= len(grid) <= 200:
        raise SystemExit(f"expected ~180 grid vias, found {len(grid)} -- refusing (board not in the expected state)")

    r = RP.Router(board)
    courtyards = []
    for fp in board.GetFootprints():
        if fp.GetReference() == "U1":
            continue
        fp.BuildCourtyardCaches()
        for L in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            s = fp.GetCourtyard(L)
            if s.OutlineCount():
                courtyards.append(s)
    a = AGP.ANTENNA_NOPOUR
    ant = (FM(OX + a[0]), FM(OY + a[1]), FM(OX + a[2]), FM(OY + a[3]))
    placed = [t.GetPosition() for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"]

    def try_via(pos):
        if any(c.Contains(pos) or c.Collide(pcbnew.SHAPE_CIRCLE(pos, FM(0.225)), 0) for c in courtyards):
            return False
        if any(f[0] <= pos.x <= f[2] and f[1] <= pos.y <= f[3] for f in r.fan_areas + [ant]):
            return False
        if any((p - pos).EuclideanNorm() < FM(MIN_SPACING) for p in placed):
            return False
        if not r.via_ok(pos, "GND"):
            return False
        r.add_via(pos, "GND")
        placed.append(pos)
        return True

    counts = {"interior 5 mm grid": 0, "edge row": 0, "antenna ring": 0}
    # 2a. interior grid
    for i in range(1, int(W / INTERIOR) + 1):
        for j in range(1, int(H / INTERIOR) + 1):
            counts["interior 5 mm grid"] += try_via(pcbnew.VECTOR2I(FM(OX + i * INTERIOR), FM(OY + j * INTERIOR)))
    # 2b. edge row: walk the outer outline, offset inward
    outer = r.outline.COutline(0)
    for s in range(outer.SegmentCount()):
        seg = outer.CSegment(s)
        ax, ay, bx, by = seg.A.x, seg.A.y, seg.B.x, seg.B.y
        L = math.hypot(bx - ax, by - ay)
        if L < FM(0.5):
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = -uy, ux                                  # one normal; flip if it points outside
        mid = pcbnew.VECTOR2I(int((ax + bx) / 2 + nx * FM(EDGE_INSET)), int((ay + by) / 2 + ny * FM(EDGE_INSET)))
        if not r.outline.Contains(mid):
            nx, ny = -nx, -ny
        n = max(1, int(L // FM(EDGE_PITCH)))
        for k in range(n + 1):
            t = (k + 0.5) * L / (n + 1)
            pos = pcbnew.VECTOR2I(int(ax + ux * t + nx * FM(EDGE_INSET)), int(ay + uy * t + ny * FM(EDGE_INSET)))
            counts["edge row"] += try_via(pos)
    # 2c. ring around the antenna no-pour area
    x0, y0, x1, y1 = (a[0] - RING_GAP, a[1] - RING_GAP, a[2] + RING_GAP, a[3] + RING_GAP)
    for (px, py, qx, qy) in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        L = math.hypot(qx - px, qy - py)
        n = max(1, int(L // RING_PITCH))
        for k in range(n + 1):
            t = k / n
            pos = pcbnew.VECTOR2I(FM(OX + px + (qx - px) * t), FM(OY + py + (qy - py) * t))
            if r.outline.Contains(pos):
                counts["antenna ring"] += try_via(pos)

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    after = sum(1 for t in board.GetTracks() if t.GetClass() == "PCB_VIA")
    print("added:", counts, f"-> total vias {after}")
    safe_save(board, path)
    print("saved", path)


if __name__ == "__main__":
    main()
