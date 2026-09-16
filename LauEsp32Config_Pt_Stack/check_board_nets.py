"""Compare EVERY pad net on the board against the schematic netlist. Optionally repair.

Run with KiCad's Python (pcbnew API):
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" check_board_nets.py [--fix]

Exists because of a real bug (2026-09-16): the DF40 plug footprints copied from the camera
board still carried that board's pad nets ("unconnected-(J1-Pin_29-Pad29)"). gen_pcb.py added
the right net beside the stale one, the stale one won, and J1/J2/J3 sat on wrong nets through
every later step. Earlier checks only counted "pads that have a net" -- which a wrong net
passes. This compares the NAME, pad by pad, against `kicad-cli sch export netlist`.

  * A schematic pin with no connection exports as "unconnected-(REF-...)"; the board must
    match that too (KiCad's own convention for no-connect pins).
  * Footprint pads with no schematic pin (DF40 mounting "MP" pads) must have no net.
--fix sets each wrong pad to the schematic net (creating the net if the board lacks it) and
saves; positions are untouched.
"""
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pcbnew

from kisave import safe_save  # never SaveBoard onto the real file (SCHEMATIC.md 18)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "LauEsp32Config_Pt_Stack"
BOARD = os.path.join(HERE, PROJECT + ".kicad_pcb")
CLI = os.path.join(os.path.dirname(sys.executable), "kicad-cli.exe")


def schematic_nets():
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "net.xml")
        r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", out,
                            os.path.join(HERE, PROJECT + ".kicad_sch")], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        root = ET.parse(out).getroot()
    return {(n.get("ref"), n.get("pin")): net.get("name") for net in root.iter("net") for n in net.findall("node")}


def main():
    fix = "--fix" in sys.argv
    want = schematic_nets()
    board = pcbnew.LoadBoard(BOARD)
    wrong, extra, missing = [], [], []
    seen = set()
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            key = (ref, p.GetNumber())
            seen.add(key)
            have = p.GetNetname()
            exp = want.get(key)
            if exp is None:
                if have:
                    extra.append((ref, p.GetNumber(), have))
                    if fix:
                        p.SetNetCode(0)
                continue
            if have != exp:
                wrong.append((ref, p.GetNumber(), have, exp))
                if fix:
                    net = board.FindNet(exp)
                    if net is None:
                        net = pcbnew.NETINFO_ITEM(board, exp)
                        board.Add(net)
                    p.SetNet(net)
    missing = sorted(k for k in want if k not in seen and not k[0].startswith("#"))
    by_ref = {}
    for ref, pad, have, exp in wrong:
        by_ref.setdefault(ref, []).append((pad, have, exp))
    print(f"pads checked: {len(seen)}; schematic pins: {len(want)}")
    print(f"WRONG net: {len(wrong)} pads" + ("" if not wrong else " -- by footprint:"))
    for ref, rows in sorted(by_ref.items()):
        pad, have, exp = rows[0]
        print(f"  {ref}: {len(rows)} pads, e.g. pad {pad}: board '{have}' vs schematic '{exp}'")
    print(f"net on a pad with no schematic pin: {len(extra)}" + (f"  e.g. {extra[0]}" if extra else ""))
    print(f"schematic pins with no pad on the board: {len(missing)}" + (f"  e.g. {missing[:3]}" if missing else ""))
    if fix and (wrong or extra):
        safe_save(board, BOARD)
        print(f"FIXED {len(wrong)} + {len(extra)} pads and saved (positions untouched). Re-run without --fix to confirm.")
    sys.exit(1 if (wrong or extra or missing) and not fix else 0)


if __name__ == "__main__":
    main()
