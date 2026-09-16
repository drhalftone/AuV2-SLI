"""Prove GND and +3V3 are each ONE connected piece of copper -- independently of KiCad's own
connectivity engine. Read-only: the board is never saved.

Run with KiCad's Python:
    & "$env:LOCALAPPDATA\\Programs\\KiCad\\10.0\\bin\\python.exe" check_power_connectivity.py

Method: for each power net, every copper item is a node --
  pads (on each copper layer they occupy), vias (all four layers), tracks (their layer), and each
  filled region of the net's zones (In1 GND plane, In2 +3V3 plane).
Two nodes are joined only if their copper physically touches on a shared layer (KiCad's shape
Collide at 0 clearance; zone fills via SHAPE_POLY_SET Collide). Union-find then counts islands.
ONE island per net = every pad, via and track on that net is galvanically connected.

Also reported:
  * how many separate filled regions each plane has (a split plane shows as > 1);
  * any same-net via that does NOT touch its plane fill (a via stranded in a clearance notch);
  * a NEGATIVE TEST: the same analysis with every GND via removed in memory must split GND into
    islands -- proving the check can actually detect a break.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "LauEsp32Config_Pt_Stack.kicad_pcb")
TM = pcbnew.ToMM
CU = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu]
PLANES = {"GND": "GND plane", "+3V3": "+3V3 plane"}


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def xy(v):
    return round(TM(v.x) - 100, 2), round(TM(v.y) - 60, 2)


def nodes_for(board, net, skip=None):
    """list of (label, layer, kind, obj, bbox) for every copper item of `net`."""
    out = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != net:
                continue
            for L in CU:
                if p.IsOnLayer(L):
                    # label carries the pad UUID: U1's thermal pad is NINE pads all numbered "61", and
                    # grouping by name would silently treat them as one conductor
                    out.append((f"{fp.GetReference()}.{p.GetNumber()}#{p.m_Uuid.AsString()[:8]}", L, "shape",
                                p.GetEffectiveShape(L), p.GetBoundingBox()))
    for t in board.GetTracks():
        # compare by KiCad UUID, never `is`: SWIG hands out a NEW Python wrapper on every iteration,
        # so an identity test never matches and the negative test silently skipped nothing (1st run)
        if t.GetNetname() != net or (skip is not None and t.m_Uuid.AsString() in skip):
            continue
        if t.GetClass() == "PCB_VIA":
            for L in CU:
                out.append((f"via{xy(t.GetPosition())}#{t.m_Uuid.AsString()[:8]}", L, "shape", t.GetEffectiveShape(L), t.GetBoundingBox()))
        else:
            out.append((f"track{xy(t.GetStart())}-{xy(t.GetEnd())}", t.GetLayer(), "shape", t.GetEffectiveShape(t.GetLayer()), t.GetBoundingBox()))
    for z in board.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != net:
            continue
        for L in CU:
            if not z.IsOnLayer(L):
                continue
            fill = z.GetFilledPolysList(L)
            for o in range(fill.OutlineCount()):
                region = pcbnew.SHAPE_POLY_SET(fill.COutline(o))
                bb = fill.COutline(o).BBox()
                out.append((f"{z.GetZoneName()} region{o} on {pcbnew.LayerName(L)}", L, "poly", region, bb))
    return out


def bbox_overlap(a, b):
    return not (a.GetRight() < b.GetLeft() or b.GetRight() < a.GetLeft() or a.GetBottom() < b.GetTop() or b.GetBottom() < a.GetTop())


def islands(board, net, skip=None):
    nodes = nodes_for(board, net, skip)
    d = DSU(len(nodes))
    # items that are the SAME physical object on several layers (a via, a through pad) are one conductor
    same = {}
    for i, (label, L, kind, obj, bb) in enumerate(nodes):
        if label.startswith("via") or kind == "shape" and not label.startswith("track"):
            same.setdefault(label, []).append(i)
    for idx in same.values():
        for j in idx[1:]:
            d.union(idx[0], j)
    by_layer = {}
    for i, n in enumerate(nodes):
        by_layer.setdefault(n[1], []).append(i)
    for L, idx in by_layer.items():
        for a in range(len(idx)):
            i = idx[a]
            li, _, ki, oi, bi = nodes[i]
            for c in range(a + 1, len(idx)):
                j = idx[c]
                lj, _, kj, oj, bj = nodes[j]
                if d.find(i) == d.find(j) or not bbox_overlap(bi, bj):
                    continue
                if ki == "poly" and kj == "poly":
                    touch = oi.Collide(oj, 0) if hasattr(oi, "Collide") else False
                elif ki == "poly":
                    touch = oi.Collide(oj, 0)
                elif kj == "poly":
                    touch = oj.Collide(oi, 0)
                else:
                    touch = oi.Collide(oj, 0)
                if touch:
                    d.union(i, j)
    groups = {}
    for i in range(len(nodes)):
        groups.setdefault(d.find(i), []).append(nodes[i][0])
    return nodes, sorted(groups.values(), key=len, reverse=True)


def main():
    board = pcbnew.LoadBoard(BOARD)
    ok = True
    for net, plane in PLANES.items():
        z = next(z for z in board.Zones() if z.GetZoneName() == plane)
        L = next(L for L in CU if z.IsOnLayer(L))
        regions = z.GetFilledPolysList(L).OutlineCount()
        nodes, groups = islands(board, net)
        pads = {n[0] for n in nodes if not n[0].startswith(("via", "track")) and "region" not in n[0]}
        vias = {n[0] for n in nodes if n[0].startswith("via")}
        print(f"{net}: plane '{plane}' on {pcbnew.LayerName(L)} has {regions} filled region(s); "
              f"{len(pads)} pads, {len(vias)} vias, {sum(1 for n in nodes if n[0].startswith('track'))} tracks")
        print(f"   copper islands: {len(groups)}" + ("   -> ONE connected conductor" if len(groups) == 1 else "   -> NOT CONNECTED"))
        if len(groups) != 1:
            ok = False
            for g in groups[1:]:
                print("     separate island:", sorted(set(g))[:8], "..." if len(set(g)) > 8 else "")
        # vias of this net that do not touch their own plane
        fill = z.GetFilledPolysList(L)
        stranded = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == net
                    and not fill.Collide(t.GetEffectiveShape(L), 0)]
        print(f"   vias on {net} not touching the plane: {len(stranded)}" + (f"  e.g. {xy(stranded[0].GetPosition())}" if stranded else ""))
        ok &= not stranded and regions == 1

    # NEGATIVE TEST: with every GND via removed in memory, pads and pours on different layers can no
    # longer reach each other or the In1 plane, so GND MUST split into islands. The first version
    # removed one cap's track; once outer GND pours existed that cap stayed connected through the
    # pour, so the test itself became invalid. This one cannot be bypassed by a pour.
    gnd_vias = {t.m_Uuid.AsString() for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"}
    _, groups = islands(board, "GND", skip=gnd_vias)
    detected = len(groups) > 1
    print(f"negative test (all {len(gnd_vias)} GND vias removed in memory): {len(groups)} islands -> "
          + ("break DETECTED, the check works" if detected else "break NOT detected -- the check is broken"))
    ok &= detected
    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
