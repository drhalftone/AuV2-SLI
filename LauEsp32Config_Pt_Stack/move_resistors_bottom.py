"""Move every 0402 resistor to the bottom face, then legalise with the least movement.

Run with KiCad's own Python (it needs the pcbnew API):
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" move_resistors_bottom.py

Why pcbnew and not a text edit: flipping a footprint mirrors pads, courtyard, fab and
silkscreen graphics and text justification together; KiCad's own FOOTPRINT.Flip does that
correctly (left/right, KiCad's default flip), about the part's own position.

Legalising on B.Cu: a flipped resistor must not overlap (courtyard + GAP) the DF40 plugs,
the other bottom-side parts, the HDMI no-footprint area (add_hdmi_keepout.py), the mount-hole
stand-offs, the notch, or come within EDGE of the board edge. An illegal part is moved to
the nearest legal spot (0.05 mm spiral search); legal parts do not move at all.
Clearance under the card is >= 2.67 mm outside the HDMI area (README 6.3); a 0402 is ~0.35 mm.
"""
import math
import os
import sys

import pcbnew

from kisave import safe_save  # never SaveBoard onto the real file (SCHEMATIC.md 18)

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
OX, OY = 100.0, 60.0                 # board corner, same as gen_pcb.ORIGIN
W, H = 55.0, 45.0
EDGE, GAP, STEP = 0.5, 0.25, 0.05
MM = pcbnew.FromMM

# (x0, y0, x1, y1) in board-corner mm -- same values as spring_place.py / add_hdmi_keepout.py
KEEPOUTS = [
    (0.0, 17.5, 9.5, 40.0),                         # HDMI clearance (Hd below)
    (49.0, 8.0, W, 37.0), (53.0, 6.0, W, 8.0), (53.0, 37.0, W, 39.0),   # notch and its chamfers
] + [(cx - 3.0, cy - 3.0, cx + 3.0, cy + 3.0) for cx, cy in ((2.5, 2.5), (52.5, 2.5), (2.5, 42.5), (52.5, 42.5))]


def is_0402_resistor(fp):
    return fp.GetReference().startswith("R") and "R_0402" in fp.GetFPIDAsString()


def cy_box(fp):
    fp.BuildCourtyardCaches()
    layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    b = fp.GetCourtyard(layer).BBox()
    return (pcbnew.ToMM(b.GetLeft()) - OX, pcbnew.ToMM(b.GetTop()) - OY,
            pcbnew.ToMM(b.GetRight()) - OX, pcbnew.ToMM(b.GetBottom()) - OY)


def overlaps(a, b, gap=GAP):
    return min(a[2], b[2]) - max(a[0], b[0]) + gap > 1e-6 and min(a[3], b[3]) - max(a[1], b[1]) + gap > 1e-6


def main():
    board = pcbnew.LoadBoard(BOARD)
    fps = list(board.GetFootprints())
    movers = sorted([f for f in fps if is_0402_resistor(f) and not f.IsFlipped()], key=lambda f: f.GetReference())
    for f in movers:
        f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    print(f"flipped {len(movers)} resistors to B.Cu")

    placed_boxes = {f.GetReference(): cy_box(f) for f in fps if f.IsFlipped() and f not in movers}

    def legal(box, ref):
        if box[0] < EDGE or box[1] < EDGE or box[2] > W - EDGE or box[3] > H - EDGE:
            return False
        if any(overlaps(box, k, 0.0) for k in KEEPOUTS):
            return False
        return not any(overlaps(box, b) for r, b in placed_boxes.items() if r != ref)

    moved = []
    # fixed-order legalisation: parts already legal claim their spot first, then the rest search
    order = sorted(movers, key=lambda f: legal(cy_box(f), f.GetReference()), reverse=True)
    for f in order:
        ref = f.GetReference()
        box = cy_box(f)
        if legal(box, ref):
            placed_boxes[ref] = box
            continue
        p0 = f.GetPosition()
        found = None
        r = STEP
        while r <= 15.0 and not found:
            n = max(8, int(2 * math.pi * r / STEP))
            for k in range(n):
                dx, dy = r * math.cos(2 * math.pi * k / n), r * math.sin(2 * math.pi * k / n)
                dx, dy = round(dx / STEP) * STEP, round(dy / STEP) * STEP
                cand = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
                if legal(cand, ref):
                    found = (dx, dy, cand)
                    break
            r += STEP
        if not found:
            raise SystemExit(f"no legal bottom-side spot for {ref} within 15 mm")
        dx, dy, cand = found
        f.SetPosition(pcbnew.VECTOR2I(p0.x + MM(dx), p0.y + MM(dy)))
        placed_boxes[ref] = cand
        moved.append((ref, math.hypot(dx, dy)))

    safe_save(board, BOARD)
    stay = len(movers) - len(moved)
    print(f"{stay} stayed exactly where they were (just flipped); {len(moved)} moved to clear a conflict:")
    for ref, d in sorted(moved, key=lambda m: -m[1]):
        print(f"  {ref}: {d:.2f} mm")


if __name__ == "__main__":
    main()
