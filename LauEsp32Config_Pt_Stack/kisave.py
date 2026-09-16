"""Safe board save for every pcbnew script in this folder.

pcbnew.SaveBoard can crash (access violation) mid-write and leave the .kicad_pcb EMPTY -- it
did on 2026-09-16 (SCHEMATIC.md 18). So never write the real board directly:
save to a temp file, prove it reloads with the same footprint count, then atomically replace.
SaveBoard also drops a .kicad_pro/.kicad_prl beside the temp file; remove them.
"""
import os

import pcbnew


def safe_save(board, path):
    tmp = path + ".tmp.kicad_pcb"
    pcbnew.SaveBoard(tmp, board)
    try:
        if os.path.getsize(tmp) == 0:
            raise SystemExit("SaveBoard wrote an empty file -- real board untouched")
        check = pcbnew.LoadBoard(tmp)
        if check is None or len(check.GetFootprints()) != len(board.GetFootprints()):
            raise SystemExit("temp board does not reload cleanly -- real board untouched")
        os.replace(tmp, path)
    finally:
        for leftover in (tmp, path + ".tmp.kicad_pro", path + ".tmp.kicad_prl"):
            if os.path.exists(leftover):
                os.remove(leftover)
