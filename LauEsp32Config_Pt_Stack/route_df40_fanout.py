"""Routing step 1: board rules, power planes, and the DF40 pass-through fan-out.

Run with KiCad's Python (pcbnew API), with KiCad closed:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" route_df40_fanout.py

RULES (JLCPCB 4-layer standard process, checked against jlcpcb.com/capabilities 2026-09-16):
  via 0.45 mm pad / 0.20 mm hole  -- the smallest via at standard price ("0.2 or 0.25 mm hole
                                     with via diameter LESS than 0.45 mm will cost more")
  track 0.10 min, 0.15 default     -- JLC min 0.10/0.10 (4/4 mil)
  hole-to-track 0.20               -- JLC "via hole to track 0.2 mm"
  no via-in-pad                    -- filled/capped vias are an extra-cost process on 4-layer
Written into the .kicad_pro (board rules + Default/Power netclasses) and the .kicad_dru
(0.10 mm clearance allowed only inside the named DF40 fan-out rule areas).

PLANES:  In1.Cu = GND (solid), In2.Cu = +3V3. Both inner layers are otherwise unused, so every
GND / +3V3 connector pin joins its plane through its own fan-out via, and F.Cu/B.Cu stay free
for signals. (The camera board spent both inner layers on GND because of its 720 Mbps LVDS
routing; this card routes nothing faster than 80 MHz SPI -- the HDMI TMDS pairs only pass
straight through the via field.)

FAN-OUT, per plug/receptacle pair (J1/J4 Bank A, J2/J5 Bank B, J3/J6 Control):
pin n of the bottom plug is directly below pin n of the top receptacle (gen_pcb.py checks it).
Each pin gets ONE through via, outboard of its pad row, in the same column:
  column pitch 0.40 mm < via 0.45 mm, so columns alternate between two via rows:
    NEAR row  2.05 mm from the connector centre -- clears the neighbouring columns' pads
              (receptacle 0.20 x 0.70 at +-1.54; plug 0.23 x 0.66 at +-1.355) by >= 0.1 mm
    FAR row   2.60 mm -- 0.65 mm centre-to-centre from the NEAR vias (JLC via-to-via 0.2 mm)
  a 0.10 mm track to a FAR via runs between two NEAR vias: 0.125 mm copper, 0.25 mm hole clearance.
  plug pad --B.Cu track--> via <--F.Cu track-- receptacle pad.
"""
import json
import os

import pcbnew

from kisave import safe_save  # never SaveBoard onto the real file (SCHEMATIC.md 18)

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "LauEsp32Config_Pt_Stack"
BOARD = os.path.join(HERE, PROJECT + ".kicad_pcb")
PRO = os.path.join(HERE, PROJECT + ".kicad_pro")
DRU = os.path.join(HERE, PROJECT + ".kicad_dru")
OX, OY, W, H = 100.0, 60.0, 55.0, 45.0
VIA_D, VIA_H = 0.45, 0.20
FAN_W = 0.10
NEAR, FAR = 2.05, 2.60
PITCH = 0.40
PAIRS = [("J1", "J4"), ("J2", "J5"), ("J3", "J6")]
FM = pcbnew.FromMM


def update_rules():
    d = json.load(open(PRO, encoding="utf-8"))
    r = d["board"]["design_settings"]["rules"]
    r.update({"min_track_width": 0.10, "min_via_diameter": VIA_D, "min_through_hole_diameter": VIA_H,
              "min_hole_clearance": 0.20, "min_hole_to_hole": 0.25, "min_via_annular_width": 0.10,
              "min_copper_edge_clearance": 0.50})
    default = next(c for c in d["net_settings"]["classes"] if c["name"] == "Default")
    default.update({"clearance": 0.15, "track_width": 0.15, "via_diameter": VIA_D, "via_drill": VIA_H})
    power = dict(default, name="Power", track_width=0.30, priority=0)
    d["net_settings"]["classes"] = [default, power]
    d["net_settings"]["netclass_patterns"] = [{"netclass": "Power", "pattern": "+3V3"},
                                              {"netclass": "Power", "pattern": "GND"}]
    ds = d["board"]["design_settings"]
    ds["track_widths"] = [0.0, 0.10, 0.15, 0.30, 0.50]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": VIA_D, "drill": VIA_H}]
    json.dump(d, open(PRO, "w", encoding="utf-8"), indent=2)

    dru = open(DRU, encoding="utf-8").read()
    marker = "# --- DF40 fan-out"
    if marker not in dru:
        dru += """
# --- DF40 fan-out (route_df40_fanout.py): one 0.45/0.20 via per pin in two staggered rows.
#     Inside the named fan-out rule areas only, copper clearance may drop to JLC's 0.10 mm
#     minimum and tracks neck to 0.10 mm. Everywhere else the netclass 0.15 mm applies.
(rule "DF40 fan-out clearance"
	(condition "A.intersectsArea('DF40 fan-out J1') || A.intersectsArea('DF40 fan-out J2') || A.intersectsArea('DF40 fan-out J3')")
	(constraint clearance (min 0.1mm)))

(rule "DF40 fan-out track width"
	(condition "A.Type == 'Track' && (A.intersectsArea('DF40 fan-out J1') || A.intersectsArea('DF40 fan-out J2') || A.intersectsArea('DF40 fan-out J3'))")
	(constraint track_width (min 0.1mm)))
"""
        open(DRU, "w", encoding="utf-8", newline="\n").write(dru)


def rect_outline(x0, y0, x1, y1):
    return (x0, y0, x1, y1)


def add_zone(board, layer, net, name, outline, keepout=False, priority=0):
    # Build the polygon INSIDE the zone's own outline. ZONE.SetOutline(poly) takes ownership of
    # a Python-created SHAPE_POLY_SET; when Python later frees it, SaveBoard reads freed memory,
    # crashes (access violation) and leaves a 0-byte .kicad_pcb. That is exactly what destroyed
    # the board on 2026-09-16 (restored from backup). Never pass a temporary to SetOutline.
    x0, y0, x1, y1 = outline
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    poly = z.Outline()
    poly.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        poly.Append(FM(OX + x), FM(OY + y))
    z.SetZoneName(name)
    if keepout:
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(False)
        z.SetDoNotAllowVias(False)
        z.SetDoNotAllowPads(False)
        z.SetDoNotAllowZoneFills(False)
        z.SetDoNotAllowFootprints(False)
        z.SetLayerSet(pcbnew.LSET.AllCuMask())
    else:
        z.SetNet(board.FindNet(net))
        z.SetLocalClearance(FM(0.20))
        z.SetMinThickness(FM(0.20))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        z.SetAssignedPriority(priority)
    board.Add(z)
    return z


def main():
    update_rules()
    board = pcbnew.LoadBoard(BOARD)
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    if any(True for _ in board.GetTracks()):
        raise SystemExit("board already has tracks/vias -- this script only seeds an unrouted board")

    n_via = n_trk = 0
    for bot, top in PAIRS:
        plug, rec = fps[bot], fps[top]
        cx, cy = plug.GetPosition().x, plug.GetPosition().y
        ppads = {p.GetNumber(): p for p in plug.Pads() if p.GetNumber().isdigit()}
        rpads = {p.GetNumber(): p for p in rec.Pads() if p.GetNumber().isdigit()}
        xmin = min(p.GetPosition().x for p in ppads.values())
        for num, pp in ppads.items():
            rp = rpads[num]
            if pp.GetNetname() != rp.GetNetname():
                raise SystemExit(f"{bot}.{num} {pp.GetNetname()} != {top}.{num} {rp.GetNetname()} -- run check_board_nets.py")
            if pp.GetPosition().x != rp.GetPosition().x:
                raise SystemExit(f"{bot}.{num} and {top}.{num} are not in the same column")
            col = round(pcbnew.ToMM(pp.GetPosition().x - xmin) / PITCH)
            side = 1 if pp.GetPosition().y > cy else -1
            vx = pp.GetPosition().x
            vy = cy + side * FM(NEAR if col % 2 == 0 else FAR)
            net = pp.GetNet()
            via = pcbnew.PCB_VIA(board)
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetPosition(pcbnew.VECTOR2I(vx, vy))
            via.SetWidth(FM(VIA_D))
            via.SetDrill(FM(VIA_H))
            via.SetNet(net)
            board.Add(via)
            n_via += 1
            for pad, layer in ((pp, pcbnew.B_Cu), (rp, pcbnew.F_Cu)):
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pad.GetPosition())
                t.SetEnd(pcbnew.VECTOR2I(vx, vy))
                t.SetWidth(FM(FAN_W))
                t.SetLayer(layer)
                t.SetNet(net)
                board.Add(t)
                n_trk += 1
        # named rule area around this fan-out (connector body + both via rows)
        ext_x = pcbnew.ToMM(max(p.GetPosition().x for p in ppads.values()) - xmin) / 2 + 0.6
        mx, my = pcbnew.ToMM(cx) - OX, pcbnew.ToMM(cy) - OY
        add_zone(board, pcbnew.F_Cu, None, f"DF40 fan-out {bot}",
                 rect_outline(mx - ext_x, my - FAR - 0.4, mx + ext_x, my + FAR + 0.4), keepout=True)

    board_outline = rect_outline(0, 0, W, H)          # fills are clipped to Edge.Cuts
    add_zone(board, pcbnew.In1_Cu, "GND", "GND plane", board_outline)
    add_zone(board, pcbnew.In2_Cu, "+3V3", "+3V3 plane", rect_outline(0, 0, W, H))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    safe_save(board, BOARD)
    print(f"added {n_via} vias, {n_trk} fan-out tracks, 3 fan-out rule areas, GND (In1) and +3V3 (In2) planes")


if __name__ == "__main__":
    main()
