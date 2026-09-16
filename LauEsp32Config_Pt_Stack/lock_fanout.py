"""Lock every existing via and track (the DF40 fan-out from route_df40_fanout.py).

Run with KiCad's Python, KiCad closed:
    & "$env:LOCALAPPDATA\Programs\KiCad\10.0\bin\python.exe" lock_fanout.py

Locked items export to Specctra DSN as protected wiring, so Freerouting routes around them
instead of ripping them up; they are also protected from accidental edits in KiCad.
Refuses if the board has anything other than the 210 fan-out vias and 420 fan-out tracks,
so it cannot lock autorouted wiring by mistake.
"""
import os

import pcbnew

from kisave import safe_save

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")


def main():
    board = pcbnew.LoadBoard(BOARD)
    items = list(board.GetTracks())
    vias = [t for t in items if t.GetClass() == "PCB_VIA"]
    tracks = [t for t in items if t.GetClass() == "PCB_TRACK"]
    if len(vias) != 210 or len(tracks) != 420:
        raise SystemExit(f"expected only the fan-out (210 vias, 420 tracks), found {len(vias)} vias, {len(tracks)} tracks")
    for t in items:
        t.SetLocked(True)
    safe_save(board, BOARD)
    print(f"locked {len(vias)} vias and {len(tracks)} tracks")


if __name__ == "__main__":
    main()
