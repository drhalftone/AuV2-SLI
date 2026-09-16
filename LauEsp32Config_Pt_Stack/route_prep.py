"""Routing step 2: everything Freerouting cannot do -- plane vias, the RAW tie, J3 tap escapes.

Run with KiCad's Python, KiCad closed:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" route_prep.py [--board PATH]

Why (Freerouting test 2026-09-16, SCHEMATIC.md 19): of 154 unrouted connections it left 70:
  GND 32 / +3V3 16  SMD pads need a via to their plane; Freerouting does not add plane vias
  RAW 7             J3's eight RAW pins must be tied by a track; there is no RAW plane
  J3 taps 15        FAB x8, DONE, PROGRAM_B, RESET, J3_P43..49 sit inside the dense fan-out field

1. PLANE VIAS. Every SMD pad on GND/+3V3 (not the DF40s, whose fan-out already reaches the
   planes) gets a 0.45/0.20 via beside it and a short track. Candidates: 16 directions x 0.1 mm
   steps out to 2.5 mm; the shortest legal one wins. A via never sits in its own pad.
   If no via fits, the pad is tied by a track to the nearest same-net pad that already has one.
2. RAW TIE. J3's RAW pins (even 2-16) are the edge-side row; their fan-out vias get stubs out to
   a B.Cu lane between the via field and the board edge, and the lane joins them.
3. J3 TAP ESCAPES. Odd-pin taps (the board-interior row): a straight stub from the fan-out via
   to ESCAPE_Y past the via field, on the layer the net continues on. Even-pin taps (30 IO2,
   32 IO3, 34 MISO, 36 CS; edge-side row): stub to one of two lanes per outer layer in the
   0.5 mm strip between the far vias and the board edge, run past J3's end and its plug's MP
   pads, then down past the via field. The left-most column takes the outer lane on each layer,
   so no stub crosses a lane.
Every via and segment is collision-checked (KiCad's own shape Collide) against all other-net
pads, tracks and vias on its layers and against the board outline (0.5 mm), before it is added.
Clearance 0.15 mm (netclass), 0.10 mm only inside the DF40 fan-out rule areas. All added items
are locked. Nothing is added unless every planned item is legal (step 2/3 are all-or-nothing).
"""
import math
import os
import sys

import pcbnew

from kisave import safe_save

HERE = os.path.dirname(os.path.abspath(__file__))
REAL = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
FM, TM = pcbnew.FromMM, pcbnew.ToMM
VIA_D, VIA_H = 0.45, 0.20
CLR, CLR_FAN, EDGE = 0.15, 0.10, 0.50
POWER = ("GND", "+3V3")
SIG_W, PWR_W = 0.15, 0.30
OX, OY = 100.0, 60.0
J3_Y = 4.0                          # J3 / J6 centre (board-corner mm)
FAR = 2.60
ESCAPE_Y = J3_Y + FAR + 0.9         # odd-row stubs end here, past the fan-out rule area
LANE_OUT, LANE_IN = 0.60, 0.85      # edge-side lanes (board-corner y), two per outer layer
# vertical legs past J3's MP pads (edge x 22.95), STAGGERED BY LAYER: with both layers' legs at
# the same x, each leg end sat directly over/under the other layer's leg, so no via could drop
# there and Freerouting left FAB_IO2 and FAB_CS unrouted (2026-09-16).
# ...and 0.60 mm apart within a layer: at 0.30 mm the inner leg's end had no room for a via
# (needs 0.425 mm to the neighbouring leg), leaving FAB_IO3 / FAB_CS unrouted (2nd run).
TURN_X_BY_LAYER = {pcbnew.F_Cu: (23.35, 23.95), pcbnew.B_Cu: (24.65, 25.25)}
LEG_END_Y = J3_Y + FAR + 0.55       # edge-tap legs stop just past the fan-out area (7.0):
                                    # R27 sits at (23.5, 7.9) on B.Cu, 7.5 would hit it
LAYER_OF_TAP = {                    # where the net continues after J3 (by the part it goes to)
    "FAB_SCK": pcbnew.B_Cu, "FAB_MOSI": pcbnew.B_Cu, "FAB_MISO": pcbnew.B_Cu, "FAB_CS": pcbnew.B_Cu,
    "FAB_IO2": pcbnew.F_Cu, "FAB_IO3": pcbnew.F_Cu, "FAB_IRQ": pcbnew.B_Cu, "FAB_SPARE": pcbnew.B_Cu,
    "FPGA_DONE": pcbnew.B_Cu, "FPGA_RESET": pcbnew.F_Cu, "FPGA_PROGRAM_B": pcbnew.F_Cu,
    "J3_P43": pcbnew.B_Cu, "J3_P45": pcbnew.B_Cu, "J3_P47": pcbnew.B_Cu, "J3_P49": pcbnew.B_Cu,
}


def pad_layer(pad):
    """The copper layer an SMD pad is really on. pad.GetLayer() says F.Cu even for pads of a
    footprint flipped to the bottom (KiCad 10 padstack quirk) -- trusting it put 27 plane-via
    tracks on the wrong side on the first run (2026-09-16)."""
    return pcbnew.B_Cu if pad.IsOnLayer(pcbnew.B_Cu) and not pad.IsOnLayer(pcbnew.F_Cu) else pcbnew.F_Cu


def P(x, y):
    return pcbnew.VECTOR2I(FM(OX + x), FM(OY + y))


class Router:
    def __init__(self, board):
        self.b = board
        self.added = []
        poly = pcbnew.SHAPE_POLY_SET()
        board.GetBoardPolygonOutlines(poly, False)
        self.outline = poly
        self.edges = [poly.COutline(0)] + [poly.CHole(0, i) for i in range(poly.HoleCount(0))]
        self.fan_areas = []
        for z in board.Zones():
            if z.GetIsRuleArea() and z.GetZoneName().startswith("DF40 fan-out"):
                bb = z.GetBoundingBox()
                self.fan_areas.append((bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))

    def clearance_at(self, bbox):
        x0, y0, x1, y1 = bbox
        for a in self.fan_areas:
            if x1 > a[0] and x0 < a[2] and y1 > a[1] and y0 < a[3]:
                return CLR_FAN
        return CLR

    def copper(self, layer):
        for fp in self.b.GetFootprints():
            for p in fp.Pads():
                if p.IsOnLayer(layer):
                    yield p.GetNetname(), p.GetEffectiveShape(layer)
        for t in self.b.GetTracks():
            if t.IsOnLayer(layer):
                yield t.GetNetname(), t.GetEffectiveShape(layer)

    def edge_ok(self, a, b, half_width):
        """Centreline a-b (a == b for a via) must be inside the outline and >= EDGE + half_width
        from every outline edge, holes included. SHAPE_LINE_CHAIN only collides with points/SEGs."""
        if not (self.outline.Contains(a) and self.outline.Contains(b)):
            return False
        # NOT chain.Collide(): for a CLOSED chain it also reports "inside" as a collision, so every
        # item on the board collided with the outline (first run 2026-09-16). Measure edge distance.
        reach = FM(EDGE) + half_width
        probe = a if a == b else pcbnew.SEG(a, b)
        for e in self.edges:
            for i in range(e.SegmentCount()):
                if e.CSegment(i).Distance(probe) < reach:
                    return False
        return True

    def via_ok(self, pos, net, own_pad=None):
        circ = pcbnew.SHAPE_CIRCLE(pos, FM(VIA_D / 2))
        if own_pad is not None and own_pad.GetEffectiveShape(pad_layer(own_pad)).Collide(circ, FM(0.05)):
            return False                                           # no via-in-pad
        if not self.edge_ok(pos, pos, FM(VIA_D / 2)):
            return False
        cl = FM(self.clearance_at((pos.x, pos.y, pos.x, pos.y)))
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            for n, sh in self.copper(layer):
                if n != net and sh.Collide(circ, cl):
                    return False
        hole = FM(VIA_H / 2)
        for t in self.b.GetTracks():                               # hole-to-hole, any net
            if t.GetClass() == "PCB_VIA" and (t.GetPosition() - pos).EuclideanNorm() < FM(VIA_H + 0.25):
                return False
        return True

    def seg_ok(self, a, b, width, layer, net):
        seg = pcbnew.SHAPE_SEGMENT(a, b, FM(width))
        if not self.edge_ok(a, b, FM(width / 2)):
            return False
        cl = FM(self.clearance_at((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))))
        for n, sh in self.copper(layer):
            if n != net and sh.Collide(seg, cl):
                return False
        centre = pcbnew.SEG(a, b)
        need = FM(0.20) + FM(VIA_H / 2) + FM(width / 2)            # 0.20 mm hole-to-track (JLC)
        for t in self.b.GetTracks():
            if t.GetClass() == "PCB_VIA" and t.GetNetname() != net and centre.Distance(t.GetPosition()) < need:
                return False
        return True

    def add_via(self, pos, net):
        v = pcbnew.PCB_VIA(self.b)
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetPosition(pos)
        v.SetWidth(FM(VIA_D))
        v.SetDrill(FM(VIA_H))
        v.SetNet(self.b.FindNet(net))
        v.SetLocked(True)
        self.b.Add(v)
        self.added.append(v)

    def add_seg(self, a, b, width, layer, net):
        if a == b:
            return
        t = pcbnew.PCB_TRACK(self.b)
        t.SetStart(a)
        t.SetEnd(b)
        t.SetWidth(FM(width))
        t.SetLayer(layer)
        t.SetNet(self.b.FindNet(net))
        t.SetLocked(True)
        self.b.Add(t)
        self.added.append(t)


def plane_vias(r):
    done, tied, failed = [], [], []
    pads = [(fp.GetReference(), p) for fp in r.b.GetFootprints() for p in fp.Pads()
            if p.GetNetname() in POWER and fp.GetReference()[:2] not in ("J1", "J2", "J3", "J4", "J5", "J6")
            and p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
    has_via = []
    for ref, pad in sorted(pads, key=lambda rp: (rp[0], rp[1].GetNumber())):
        net, layer, c = pad.GetNetname(), pad_layer(pad), pad.GetPosition()
        best = None
        for width in ((PWR_W, 0.20) if ref != "U1" else (0.25, 0.20)):
            for k in range(16):
                ang = 2 * math.pi * k / 16
                for i in range(3, 26):
                    d = FM(0.1 * i)
                    pos = pcbnew.VECTOR2I(c.x + int(d * math.cos(ang)), c.y + int(d * math.sin(ang)))
                    if best and d >= best[0]:
                        break
                    if r.via_ok(pos, net, own_pad=pad) and r.seg_ok(c, pos, width, layer, net):
                        best = (d, pos, width)
                        break
            if best:
                break
        if best:
            r.add_via(best[1], net)
            r.add_seg(c, best[1], best[2], layer, net)
            has_via.append((ref, pad))
            done.append(f"{ref}.{pad.GetNumber()}")
            continue
        # fall back: tie to the nearest same-net pad (same layer) that already has a via
        cands = sorted((p2 for r2, p2 in has_via if p2.GetNetname() == net and pad_layer(p2) == layer),
                       key=lambda p2: (p2.GetPosition() - c).EuclideanNorm())
        for p2 in cands:
            if (p2.GetPosition() - c).EuclideanNorm() < FM(3.0) and r.seg_ok(c, p2.GetPosition(), 0.20, layer, net):
                r.add_seg(c, p2.GetPosition(), 0.20, layer, net)
                tied.append(f"{ref}.{pad.GetNumber()}")
                break
        else:
            failed.append(f"{ref}.{pad.GetNumber()} {net}")
    return done, tied, failed


def j3_vias(r):
    """fan-out vias of J3 by pin number (via at the end of each J3 plug-pad fan-out track)."""
    fp = next(f for f in r.b.GetFootprints() if f.GetReference() == "J3")
    pads = {p.GetNumber(): p for p in fp.Pads() if p.GetNumber().isdigit()}
    vias = [t for t in r.b.GetTracks() if t.GetClass() == "PCB_VIA"]
    out = {}
    for num, p in pads.items():
        x = p.GetPosition().x
        side = 1 if p.GetPosition().y > FM(OY + J3_Y) else -1
        cand = [v for v in vias if v.GetPosition().x == x and (v.GetPosition().y - FM(OY + J3_Y)) * side > 0
                and v.GetNetname() == p.GetNetname() and abs(v.GetPosition().y - FM(OY + J3_Y)) < FM(3.0)]
        if len(cand) != 1:
            raise SystemExit(f"J3 pin {num}: expected one fan-out via, found {len(cand)}")
        out[num] = (p.GetNetname(), cand[0].GetPosition())
    return out


def plan_raw_and_escapes(r):
    """Return a list of ('seg', a, b, width, layer, net) for step 2 and 3."""
    v = j3_vias(r)
    plan = []
    # RAW tie on B.Cu along the outer edge lane
    raw = sorted((pos for net, pos in v.values() if net.lstrip("/") == "RAW"), key=lambda p: p.x)
    lane_y = FM(OY + LANE_OUT)
    for pos in raw:
        plan.append(("seg", pos, pcbnew.VECTOR2I(pos.x, lane_y), 0.20, pcbnew.B_Cu, "/RAW"))
    plan.append(("seg", pcbnew.VECTOR2I(raw[0].x, lane_y), pcbnew.VECTOR2I(raw[-1].x, lane_y), 0.20, pcbnew.B_Cu, "/RAW"))

    taps = {net.lstrip("/"): (num, pos) for num, (net, pos) in v.items() if net.lstrip("/") in LAYER_OF_TAP}
    missing = set(LAYER_OF_TAP) - set(taps)
    if missing:
        raise SystemExit(f"J3 taps not found: {sorted(missing)}")
    edge_side = {}
    for name, (num, pos) in taps.items():
        layer = LAYER_OF_TAP[name]
        if pos.y > FM(OY + J3_Y):                                  # interior row: straight stub
            plan.append(("seg", pos, pcbnew.VECTOR2I(pos.x, FM(OY + ESCAPE_Y)), 0.10, layer, "/" + name))
        else:
            edge_side.setdefault(layer, []).append((pos.x, name, pos))
    for layer, items in edge_side.items():
        items.sort()                                               # left-most column -> outer lane
        if len(items) > 2:
            raise SystemExit(f"more than two edge-side taps on {pcbnew.LayerName(layer)}")
        tx_in, tx_out = TURN_X_BY_LAYER[layer]
        for (x, name, pos), lane, turn in zip(items, (LANE_OUT, LANE_IN), (tx_out, tx_in)):
            ly, tx = FM(OY + lane), FM(OX + turn)
            net = "/" + name
            plan.append(("seg", pos, pcbnew.VECTOR2I(pos.x, ly), 0.10, layer, net))
            plan.append(("seg", pcbnew.VECTOR2I(pos.x, ly), pcbnew.VECTOR2I(tx, ly), 0.10, layer, net))
            plan.append(("seg", pcbnew.VECTOR2I(tx, ly), pcbnew.VECTOR2I(tx, FM(OY + LEG_END_Y)), 0.10, layer, net))
    return plan


def main():
    path = os.path.abspath(sys.argv[sys.argv.index("--board") + 1]) if "--board" in sys.argv else REAL
    lock = os.path.join(os.path.dirname(path), "~" + os.path.splitext(os.path.basename(path))[0] + ".kicad_pro.lck")
    if os.path.exists(lock):
        raise SystemExit(f"KiCad has this project open ({lock}) -- close it first; nothing written")
    board = pcbnew.LoadBoard(path)
    if sum(1 for _ in board.GetTracks()) != 630:
        raise SystemExit("expected exactly the 630 locked fan-out items -- run on the fan-out board only")
    r = Router(board)

    plan = plan_raw_and_escapes(r)
    bad = []
    for kind, a, b, w, layer, net in plan:
        if not r.seg_ok(a, b, w, layer, net):
            bad.append(f"{net} {pcbnew.LayerName(layer)} ({TM(a.x) - OX:.2f},{TM(a.y) - OY:.2f})->({TM(b.x) - OX:.2f},{TM(b.y) - OY:.2f})")
        else:
            r.add_seg(a, b, w, layer, net)                          # later segments see earlier ones
    if bad:
        print("ILLEGAL escape/tie segments (nothing saved):")
        for s in bad:
            print("  ", s)
        raise SystemExit(1)
    print(f"RAW tie + J3 escapes: {len(plan)} segments, all legal")

    done, tied, failed = plane_vias(r)
    print(f"plane vias: {len(done)} pads got a via; {len(tied)} tied to a neighbour; {len(failed)} FAILED")
    for f in failed:
        print("   FAILED", f)
    safe_save(board, path)
    print(f"saved {path}: added {len(r.added)} locked items")


if __name__ == "__main__":
    main()
