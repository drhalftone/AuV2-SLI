"""Add a B.Cu 'no footprints' rule area over the Hd's two HDMI connectors.

    python add_hdmi_keepout.py

The card's bottom face sits 4.125 mm above the Hd's top side (README 1a). The Hd's HDMI
connectors are 2.97 mm tall at x 0.2-7.8, y 19.2-25.8 and 31.7-38.3 (hd_clearance.py,
README 6.3), leaving 1.15 mm -- and a mated plug's shell may need more. Decision
2026-09-16: keep the bottom face clear near the HDMI ports.

Footprints are not allowed on B.Cu inside HDMI_KEEPOUT; tracks, vias and pours are
(flat copper costs no height). Idempotent: replaces an existing area of the same name.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_pcb as P        # noqa: E402
import gen_schematic as S  # noqa: E402

NAME = "HDMI clearance (Hd below)"
MARGIN = 1.5
# both connectors plus MARGIN, run out to the board's left edge (board-corner coordinates)
HDMI_KEEPOUT = (0.0, 19.2 - MARGIN - 0.2, 7.8 + MARGIN + 0.2, 38.3 + MARGIN + 0.2)   # x0, y0, x1, y1


def zone():
    x0, y0, x1, y1 = HDMI_KEEPOUT
    X = lambda v: S.num(P.ORIGIN[0] + v)
    Y = lambda v: S.num(P.ORIGIN[1] + v)
    return ["zone", ["net", "0"], ["net_name", '""'], ["layers", '"B.Cu"'],
            ["uuid", S.q(P.U("zone", NAME))], ["name", S.q(NAME)], ["hatch", "edge", "0.5"],
            ["connect_pads", ["clearance", "0"]], ["min_thickness", "0.25"],
            ["keepout", ["tracks", "allowed"], ["vias", "allowed"], ["pads", "allowed"],
             ["copperpour", "allowed"], ["footprints", "not_allowed"]],
            ["fill", ["thermal_gap", "0.5"], ["thermal_bridge_width", "0.5"]],
            ["polygon", ["pts", ["xy", X(x0), Y(y0)], ["xy", X(x1), Y(y0)], ["xy", X(x1), Y(y1)], ["xy", X(x0), Y(y1)]]]]


def main():
    board = S.parse(open(P.OUT, encoding="utf-8").read())[0]
    board[:] = [e for e in board if not (isinstance(e, list) and e[0] == "zone"
                                         and P.sub(e, "name") and P.sub(e, "name")[0][1] == S.q(NAME))]
    at = max(i for i, e in enumerate(board) if isinstance(e, list) and e[0] in ("footprint", "gr_line", "gr_circle")) + 1
    board.insert(at, zone())
    open(P.OUT, "w", encoding="utf-8", newline="\n").write(S.dump(board) + "\n")
    print(f"added B.Cu keep-out '{NAME}': x {HDMI_KEEPOUT[0]}-{HDMI_KEEPOUT[2]}, y {HDMI_KEEPOUT[1]}-{HDMI_KEEPOUT[3]}")


if __name__ == "__main__":
    main()
