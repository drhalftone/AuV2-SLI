"""Import a Freerouting session (.ses) into the board, refill planes, verify the fan-out survived.

Run with KiCad's Python:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" import_routes.py SES [--board PATH]

--board defaults to the real LauEsp32Config_Pt_Stack.kicad_pcb. Refuses to touch the real board
while KiCad has the project open (the .lck file exists): a later GUI save would silently
overwrite the import (2026-09-16).

Checks before saving:
  * all 210 DF40 fan-out vias are still present at their original positions (the SES carries
    the fixed wiring back, but it is re-verified, and re-locked, by position);
  * the zones are refilled so the plane connections are current for DRC.
Afterwards run check_board_nets.py and a fresh kicad-cli DRC.
"""
import os
import sys

import pcbnew

from kisave import safe_save

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "LauEsp32Config_Pt_Stack"
REAL = os.path.join(HERE, PROJECT + ".kicad_pcb")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    ses = os.path.abspath(args[0])
    board_path = os.path.abspath(sys.argv[sys.argv.index("--board") + 1]) if "--board" in sys.argv else REAL
    lock = os.path.join(os.path.dirname(board_path), "~" + os.path.splitext(os.path.basename(board_path))[0] + ".kicad_pro.lck")
    if os.path.exists(lock):
        raise SystemExit(f"KiCad has this project open ({lock}) -- close it first; nothing written")

    board = pcbnew.LoadBoard(board_path)
    fan_before = {(v.GetPosition().x, v.GetPosition().y): v.GetNetname()
                  for v in board.GetTracks() if v.GetClass() == "PCB_VIA" and v.IsLocked()}
    if len(fan_before) != 210:
        raise SystemExit(f"expected 210 locked fan-out vias before import, found {len(fan_before)}")

    if not pcbnew.ImportSpecctraSES(board, ses):
        raise SystemExit("ImportSpecctraSES failed")

    vias = {(v.GetPosition().x, v.GetPosition().y): v for v in board.GetTracks() if v.GetClass() == "PCB_VIA"}
    missing = [p for p in fan_before if p not in vias or vias[p].GetNetname() != fan_before[p]]
    if missing:
        raise SystemExit(f"{len(missing)} fan-out vias missing or changed after import -- nothing written")
    # re-lock the fan-out (vias by position, tracks touching them inside a connector's fan-out band)
    fan_pts = set(fan_before)
    relocked = 0
    for t in board.GetTracks():
        if t.GetClass() == "PCB_VIA" and (t.GetPosition().x, t.GetPosition().y) in fan_pts:
            t.SetLocked(True)
            relocked += 1
        elif t.GetClass() == "PCB_TRACK" and ((t.GetStart().x, t.GetStart().y) in fan_pts or (t.GetEnd().x, t.GetEnd().y) in fan_pts) \
                and t.GetWidth() == pcbnew.FromMM(0.10):
            t.SetLocked(True)
            relocked += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    tracks = list(board.GetTracks())
    n_via = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
    n_trk = sum(1 for t in tracks if t.GetClass() == "PCB_TRACK")
    safe_save(board, board_path)
    print(f"imported {os.path.basename(ses)} into {board_path}")
    print(f"  now {n_via} vias ({n_via - 210} new), {n_trk} track segments; fan-out intact and re-locked ({relocked} items)")


if __name__ == "__main__":
    main()
