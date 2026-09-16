"""Routing step 3: GND pours on F.Cu and B.Cu, and GND stitching vias.

Run with KiCad's Python, KiCad closed:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" add_ground_pours.py [--board PATH]

Why (layout review 2026-09-16, SCHEMATIC.md 20): the only ground was In1. Tracks on B.Cu
referenced the +3V3 plane (In2), and the fast nets change layer 1-4 times with no ground via
beside the signal via, so their return current had to find its way through decoupling caps.

1. POURS. A GND zone on F.Cu and on B.Cu over the whole board (fill is clipped to Edge.Cuts,
   holes and notch). 0.15 mm clearance (the DRU still allows 0.10 inside the DF40 fan-out areas),
   0.20 mm min width, thermal reliefs on pads (0.30 gap / 0.30 spoke, so 0402s solder evenly),
   vias solid, isolated islands removed. A no-pour rule area on F.Cu around the antenna
   connector and its cable exit to the notch.
2. STITCHING VIAS (0.45/0.20, GND), two kinds:
   * a GRID every STITCH mm wherever the spot is legal;
   * a COMPANION next to every autorouted (unlocked) via on a signal net, 0.6-1.6 mm away, so
     each layer change has a ground return path beside it.
   Legality is route_prep.Router.via_ok (all other-net copper on F/B, JLC hole rules, board edge),
   plus: not inside any footprint courtyard (either side), not inside a DF40 fan-out area, not in
   the antenna no-pour area, not within 0.9 mm of another stitching via.
3. Zones refilled after the vias. All added vias are locked.
Verify afterwards: check_board_nets.py, check_power_connectivity.py, kicad-cli DRC.
"""
import math
import os
import sys

import pcbnew

from kisave import safe_save
import route_prep as RP

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
FM, TM = pcbnew.FromMM, pcbnew.ToMM
OX, OY, W, H = 100.0, 60.0, 55.0, 45.0
STITCH = 2.0
ANTENNA_NOPOUR = (44.0, 26.5, 50.0, 35.0)       # U1 antenna connector (47, 30) + coax exit to the notch
POUR_NAMES = ("GND pour F.Cu", "GND pour B.Cu")


def rect_zone(board, name, layer, x0, y0, x1, y1):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    poly = z.Outline()                           # build in place: never SetOutline(temporary)
    poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(FM(OX + x), FM(OY + y))
    z.SetZoneName(name)
    return z


def main():
    path = os.path.abspath(sys.argv[sys.argv.index("--board") + 1]) if "--board" in sys.argv else REAL
    lock = os.path.join(os.path.dirname(path), "~" + os.path.splitext(os.path.basename(path))[0] + ".kicad_pro.lck")
    if os.path.exists(lock):
        raise SystemExit(f"KiCad has this project open ({lock}) -- close it first; nothing written")
    board = pcbnew.LoadBoard(path)
    if any(z.GetZoneName() in POUR_NAMES for z in board.Zones()):
        raise SystemExit("board already has the GND pours -- this script only adds them once")

    gnd = board.FindNet("GND")
    for name, layer in zip(POUR_NAMES, (pcbnew.F_Cu, pcbnew.B_Cu)):
        z = rect_zone(board, name, layer, 0, 0, W, H)
        z.SetNet(gnd)
        z.SetLocalClearance(FM(0.15))
        z.SetMinThickness(FM(0.20))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetThermalReliefGap(FM(0.30))
        z.SetThermalReliefSpokeWidth(FM(0.30))
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetAssignedPriority(0)
        board.Add(z)
    ko = rect_zone(board, "Antenna no-pour", pcbnew.F_Cu, *ANTENNA_NOPOUR)
    ko.SetIsRuleArea(True)
    ko.SetDoNotAllowZoneFills(True)
    ko.SetDoNotAllowTracks(False)
    ko.SetDoNotAllowVias(False)
    ko.SetDoNotAllowPads(False)
    ko.SetDoNotAllowFootprints(False)
    board.Add(ko)

    # U1 GND pads connect SOLID: its 0.85 mm-pitch pads leave room for only one thermal spoke
    # (DRC starved_thermal on pad 58, 1st run). It is a big shielded module -- the even-heating
    # reason for reliefs does not apply to it.
    for fp in board.GetFootprints():
        if fp.GetReference() == "U1":
            fp.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)

    r = RP.Router(board)
    courtyards = []
    for fp in board.GetFootprints():
        if fp.GetReference() == "U1":
            continue            # vias under the module body (tented) are normal; most signal vias sit there
        fp.BuildCourtyardCaches()
        for L in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            s = fp.GetCourtyard(L)
            if s.OutlineCount():
                courtyards.append(s)
    fan = r.fan_areas
    ant = (FM(OX + ANTENNA_NOPOUR[0]), FM(OY + ANTENNA_NOPOUR[1]), FM(OX + ANTENNA_NOPOUR[2]), FM(OY + ANTENNA_NOPOUR[3]))
    placed = []

    def extra_ok(pos):
        if any(c.Contains(pos) or c.Collide(pcbnew.SHAPE_CIRCLE(pos, FM(0.225)), 0) for c in courtyards):
            return False
        if any(a[0] <= pos.x <= a[2] and a[1] <= pos.y <= a[3] for a in fan + [ant]):
            return False
        return all((p - pos).EuclideanNorm() >= FM(0.9) for p in placed)

    # companions first, so every signal layer change gets its return via before the grid fills space
    sig_vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and not t.IsLocked()
                and t.GetNetname() not in ("GND", "+3V3")]
    comp_ok = comp_fail = 0
    for v in sig_vias:
        c = v.GetPosition()
        done = False
        for d in (0.6, 0.8, 1.0, 1.2, 1.4, 1.6):
            for k in range(16):
                a = 2 * math.pi * k / 16
                pos = pcbnew.VECTOR2I(c.x + int(FM(d) * math.cos(a)), c.y + int(FM(d) * math.sin(a)))
                if extra_ok(pos) and r.via_ok(pos, "GND"):
                    r.add_via(pos, "GND")
                    placed.append(pos)
                    done = True
                    break
            if done:
                break
        comp_ok += done
        comp_fail += not done

    grid = 0
    nx, ny = int(W / STITCH), int(H / STITCH)
    for i in range(1, nx):
        for j in range(1, ny):
            pos = pcbnew.VECTOR2I(FM(OX + i * STITCH), FM(OY + j * STITCH))
            if extra_ok(pos) and r.via_ok(pos, "GND"):
                r.add_via(pos, "GND")
                placed.append(pos)
                grid += 1

    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    for name, L in zip(POUR_NAMES, (pcbnew.F_Cu, pcbnew.B_Cu)):
        z = next(z for z in board.Zones() if z.GetZoneName() == name)
        f = z.GetFilledPolysList(L)
        print(f"{name}: {f.OutlineCount()} filled region(s), {TM(TM(int(z.GetFilledArea()))):.0f} mm^2")
    print(f"stitching vias: {comp_ok} companions beside {len(sig_vias)} signal vias ({comp_fail} had no legal spot), "
          f"{grid} on the {STITCH} mm grid")
    safe_save(board, path)
    print("saved", path)


if __name__ == "__main__":
    main()
