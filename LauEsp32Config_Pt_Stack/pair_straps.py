"""Put each rev B JTAG strap (DNP) right beside the rev A strap it replaces.

    python pair_straps.py

Pairs share the buffer-side net, so fitting rev B is a matter of moving four resistors a
millimetre or two (SCHEMATIC.md 4):
    R10 JTAG_TCK -> R14    R11 JTAG_TMS -> R15    R12 JTAG_TDI -> R16    R13 JTAG_TDO -> R17
Each rev B strap goes directly beside its rev A partner, same rotation, courtyards GAP apart.
Where there is no room, the PAIR moves the shortest distance to where both fit (rev A moves
too, and the script says so). Same courtyard / keep-out / edge rules as spring_place.py.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_schematic as S    # noqa: E402
import place_components as PC  # noqa: E402
import spring_place as SP    # noqa: E402

PAIRS = {"R10": "R14", "R11": "R15", "R12": "R16", "R13": "R17"}


def main():
    board = S.parse(open(SP.BOARD_FILE, encoding="utf-8").read())[0]
    fps = {SP.ref_of(f): f for f in SP.sub(board, "footprint")}
    top = {r: SP.Part(r, [f], True) for r, f in fps.items() if SP.sub(f, "layer")[0][1] == '"F.Cu"'}

    def legal(p):
        b = p.bbox()
        if b[0] < SP.EDGE or b[1] < SP.EDGE or b[2] > SP.W - SP.EDGE or b[3] > SP.H - SP.EDGE:
            return False
        return not any(SP.overlap(b, o, SP.GAP - 1e-6)
                       for o in [q.bbox() for q in top.values() if q is not p] + SP.KEEPOUTS)

    def write(p):
        f = p.fps[0]
        at = SP.sub(f, "at")[0]
        prev = float(at[3]) if len(at) > 3 else 0.0
        at[1:] = [S.num(SP.OX + p.x), S.num(SP.OY + p.y)] + ([S.num(p.rot)] if p.rot else [])
        if abs(p.rot - prev) > 1e-6:
            i = f.index(at)
            rest = PC.rotate_angles([x for x in f if x is not at], p.rot - prev)
            f[:] = rest[:i] + [at] + rest[i:]

    snap = lambda v: round(v / 0.05) * 0.05
    for a_ref, b_ref in PAIRS.items():
        a, b = top[a_ref], top[b_ref]
        a0, b0 = (a.x, a.y), (b.x, b.y)
        b.rot = a.rot
        ab = a.bbox()
        # side by side = offset across the resistor's short axis, courtyards GAP apart
        short_x = (ab[2] - ab[0]) < (ab[3] - ab[1])
        pitch = min(ab[2] - ab[0], ab[3] - ab[1]) + SP.GAP + 0.05
        sides = [(pitch, 0), (-pitch, 0)] if short_x else [(0, pitch), (0, -pitch)]
        # 1) rev A stays put, rev B goes directly beside it
        placed = False
        for dx, dy in sides:
            b.x, b.y = snap(a.x + dx), snap(a.y + dy)
            if legal(b):
                placed = True
                break
        # 2) no room: move the PAIR the shortest distance to where both fit side by side
        if not placed:
            b.x, b.y = -100, -100                       # out of the way while testing rev A spots
            offs = sorted(((i * 0.1, j * 0.1) for i in range(-100, 101) for j in range(-100, 101)),
                          key=lambda o: math.hypot(*o))
            for ox, oy in offs:
                a.x, a.y = snap(a0[0] + ox), snap(a0[1] + oy)
                if not legal(a):
                    continue
                for dx, dy in sides:
                    b.x, b.y = snap(a.x + dx), snap(a.y + dy)
                    if legal(b):
                        placed = True
                        break
                if placed:
                    break
                b.x, b.y = -100, -100
        if not placed:
            raise SystemExit(f"no side-by-side spot for {a_ref}/{b_ref} within 10 mm")
        moved_a = math.hypot(a.x - a0[0], a.y - a0[1])
        print(f"{b_ref}: ({b0[0]:.2f}, {b0[1]:.2f}) -> ({b.x:.2f}, {b.y:.2f}), "
              f"{math.hypot(b.x - a.x, b.y - a.y):.2f} mm beside {a_ref}"
              + (f"  [{a_ref} moved {moved_a:.2f} mm to make room]" if moved_a > 1e-6 else ""))
        write(a)
        write(b)
    open(SP.BOARD_FILE, "w", encoding="utf-8", newline="\n").write(S.dump(board) + "\n")


if __name__ == "__main__":
    main()
