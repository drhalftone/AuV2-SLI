"""Put the TP1-TP8 pad field along the board edge opposite J6 (the bottom edge, y = 45).

Run with KiCad's Python (pcbnew API):
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" move_testpads.py [--dry-run]

User decision 2026-09-16: pogo-fixture pads on the edge opposite J6 (J6 = control receptacle,
top edge), so a side-entry fixture can reach them with the stack assembled.
Top face, 2.54 mm pitch kept, TP1 -> TP8 left to right, pad centres EDGE_INSET from the edge.
The strip is centred in the free run between the lower-left stand-off keep-out (x <= 5.5)
and J5's courtyard; the script checks it against every top-side courtyard before saving.
"""
import math
import os
import sys

import pcbnew

from kisave import safe_save  # never SaveBoard onto the real file (SCHEMATIC.md 18)

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
OX, OY, W, H = 100.0, 60.0, 55.0, 45.0
PITCH, EDGE_INSET, GAP, EDGE = 2.54, 1.6, 0.25, 0.5   # TP courtyard is 2.09 mm across: 1.5 breaks the 0.5 mm edge rule
STANDOFF_X1 = 2.5 + 3.0          # lower-left mount-hole keep-out right edge
mm = pcbnew.ToMM


def box(fp):
    fp.BuildCourtyardCaches()
    b = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd).BBox()
    return mm(b.GetLeft()) - OX, mm(b.GetTop()) - OY, mm(b.GetRight()) - OX, mm(b.GetBottom()) - OY


def main():
    board = pcbnew.LoadBoard(BOARD)
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    tps = [fps[f"TP{i}"] for i in range(1, 9)]
    j5 = box(fps["J5"])
    y = H - EDGE_INSET
    r = (box(tps[0])[2] - box(tps[0])[0]) / 2
    span_lo, span_hi = STANDOFF_X1 + GAP + r, j5[0] - GAP - r        # allowed pad-centre range
    first = (span_lo + span_hi - 7 * PITCH) / 2
    if first < span_lo or first + 7 * PITCH > span_hi:
        raise SystemExit(f"strip does not fit: centres {span_lo:.2f}..{span_hi:.2f}")
    for i, f in enumerate(tps):
        f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(OX + first + i * PITCH), pcbnew.FromMM(OY + y)))

    others = [(ref, box(f)) for ref, f in fps.items() if not f.IsFlipped() and not ref.startswith("TP")]
    for f in tps:
        b = box(f)
        if b[3] > H - EDGE or b[0] < EDGE:
            raise SystemExit(f"{f.GetReference()} breaks the edge rule: {b}")
        hit = [ref for ref, o in others if min(b[2], o[2]) - max(b[0], o[0]) + GAP > 0 and min(b[3], o[3]) - max(b[1], o[1]) + GAP > 0]
        if hit:
            raise SystemExit(f"{f.GetReference()} overlaps {hit}")
    for f in tps:
        p = f.GetPosition()
        pad = list(f.Pads())[0]
        print(f"{f.GetReference()} {pad.GetNetname():10} at ({mm(p.x) - OX:.2f}, {mm(p.y) - OY:.2f})")
    print(f"free run x {span_lo:.2f}..{span_hi:.2f}; strip x {first:.2f}..{first + 7 * PITCH:.2f}; "
          f"pad centres {EDGE_INSET} mm from the y = {H} edge")
    if "--dry-run" not in sys.argv:
        safe_save(board, BOARD)
        print("saved", BOARD)


if __name__ == "__main__":
    main()
