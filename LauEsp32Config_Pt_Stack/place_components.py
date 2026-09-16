"""Add every remaining schematic part to the board, at an initial floorplan position.

    python place_components.py

Adds only references NOT already on the board, so it never moves or duplicates a part
someone has already placed by hand. Pads get their nets from the schematic netlist and
each footprint carries its symbol UUID (same as gen_pcb.py), so "Update PCB from
Schematic" stays in sync.

Floorplan (SCHEMATIC.md 12) -- coordinates relative to the board corner, all TOP side:
the underside faces the Hd+, whose component heights are unmeasured (README 6.3), so
only the DF40 plugs live on B.Cu.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_pcb as P          # noqa: E402
import gen_schematic as S    # noqa: E402

KICAD_FP = "C:/Users/drhal/AppData/Local/Programs/KiCad/10.0/share/kicad/footprints"

# ref: (x, y, rotation deg CCW). Rationale per group in SCHEMATIC.md 12.
PLACE = {
    # ESP32 module, rotated 180 so the antenna-connector corner (pin-1 corner) sits by the
    # notch: the coax leaves through it (README 6.4). Module courtyard x 32.1-48.0.
    "U1": (40.0, 23.0, 180),
    # bulk + decoupling along the module's 3V3 side, EN RC beside EN (module pin 45)
    "C1": (46.0, 33.5, 0), "C2": (42.5, 33.5, 0), "C3": (39.0, 33.5, 0), "C4": (36.0, 33.5, 0),
    "R1": (30.3, 26.0, 90), "C5": (30.3, 29.0, 90),
    # microSD, slot facing the LEFT board edge so a card can be inserted on the bench
    "J7": (7.5, 23.0, 90),
    "R30": (3.0, 13.0, 90), "R31": (4.3, 13.0, 90), "R32": (5.6, 13.0, 90), "R33": (6.9, 13.0, 90),
    "R34": (8.2, 13.0, 90), "R35": (9.5, 13.0, 90), "R36": (10.8, 13.0, 90),
    "C8": (12.1, 13.0, 90), "C7": (13.8, 13.0, 90),
    # fabric-SPI series resistors, in a row under J3 pins 29-36
    "R20": (9.5, 8.6, 90), "R21": (10.8, 8.6, 90), "R22": (12.1, 8.6, 90), "R23": (13.4, 8.6, 90),
    "R24": (14.7, 8.6, 90), "R25": (16.0, 8.6, 90), "R26": (17.3, 8.6, 90), "R27": (18.6, 8.6, 90),
    # JTAG straps under J3 pins 43-49: rev A R10-R13, rev B R14-R17 (DNP)
    "R10": (19.9, 8.6, 90), "R11": (21.2, 8.6, 90), "R12": (22.5, 8.6, 90), "R13": (23.8, 8.6, 90),
    "R14": (25.1, 8.6, 90), "R15": (26.4, 8.6, 90), "R16": (27.7, 8.6, 90), "R17": (29.0, 8.6, 90),
    # JTAG buffer and its pull resistors / decoupling
    "U2": (24.0, 13.0, 0), "C6": (24.0, 17.0, 0),
    "R2": (18.5, 11.5, 90), "R3": (18.5, 14.0, 90),
    # PROGRAM_B / Reset FETs, gate pull-downs, DONE series, status LED
    "Q1": (31.5, 9.8, 0), "Q2": (31.5, 13.3, 0),
    "R4": (34.2, 9.8, 90), "R5": (34.2, 13.3, 90), "R6": (35.6, 9.8, 90),
    "R7": (37.0, 9.8, 90), "D1": (39.5, 9.8, 0),
    # pad field (bare copper, not assembled), 2.54 mm pitch for a pogo fixture
    "TP1": (3.0, 36.0, 0), "TP2": (5.54, 36.0, 0), "TP3": (8.08, 36.0, 0), "TP4": (10.62, 36.0, 0),
    "TP5": (13.16, 36.0, 0), "TP6": (15.7, 36.0, 0), "TP7": (18.24, 36.0, 0), "TP8": (20.78, 36.0, 0),
}


FAB_REFS = {"J7", "U1", "U2", "Q1", "Q2"} | {f"TP{i}" for i in range(1, 9)}
# J7: designator lands on the left board edge. U2/Q1/Q2/TPs: designators collide with
# neighbours at this density. All still visible on F.Fab and in KiCad.


def lib_path(fp):
    lib, name = fp.split(":")
    base = S.LOCAL_FP.get(lib, f"{KICAD_FP}/{lib}.pretty")
    return os.path.join(base, name + ".kicad_mod")


def rotate_angles(e, rot):
    """Add the footprint rotation to every child (at x y [a]): KiCad stores pad/text angles absolute."""
    if not isinstance(e, list):
        return e
    if e and e[0] in ("pad", "property", "fp_text"):
        out = []
        for x in e:
            if isinstance(x, list) and x and x[0] == "at":
                a = float(x[3]) if len(x) > 3 else 0.0
                # pads take the full rotation; text is kept readable (never upside down)
                x = ["at", x[1], x[2], S.num((a + rot) % (360 if e[0] == "pad" else 180))]
            out.append(rotate_angles(x, rot) if isinstance(x, list) and x[0] != "at" else x)
        return out
    return [rotate_angles(x, rot) for x in e]


def footprint(ref, part, nets, x, y, rot):
    fp = S.parse(open(lib_path(part["fp"]), encoding="utf-8").read())[0]
    if P.sub(fp, "layer")[0][1] != '"F.Cu"':
        raise SystemExit(f"{ref}: footprint is not drawn on F.Cu")
    count = [0]

    def walk(e):
        if not isinstance(e, list):
            return e
        if e and e[0] == "uuid":
            count[0] += 1
            return ["uuid", S.q(P.U(ref, count[0]))]
        if e and e[0] == "property" and e[1] == '"Reference"':
            e = e[:2] + [S.q(ref)] + e[3:]
            if ref in FAB_REFS or part["lib_id"] in ("Device:R", "Device:C", "Device:LED"):
                # 0402 rows at 1.3 mm pitch leave no room for printed designators; keep them on
                # F.Fab (visible in KiCad and on the assembly drawing, not silkscreened)
                e = [["layer", '"F.Fab"'] if isinstance(v, list) and v[0] == "layer" else v for v in e]
        if e and e[0] == "property" and e[1] == '"Value"':
            e = e[:2] + [S.q(part["value"])] + e[3:]
        if e and e[0] == "pad":
            net = nets.get((ref, e[1].strip('"')))
            # never keep a net carried in from the library footprint (see gen_pcb.py)
            e = [walk(v) for v in e if not (isinstance(v, list) and v and v[0] == "net")]
            if net:
                i = next(i for i, v in enumerate(e) if isinstance(v, list) and v[0] == "layers") + 1
                e.insert(i, ["net", S.q(net)])
            return e
        if e and e[0] == "attr" and part["dnp"] and "dnp" not in e:
            return e + ["dnp"]
        return [walk(v) for v in e]

    body = [walk(v) for v in fp[2:]
            if not (isinstance(v, list) and v[0] in ("version", "generator", "generator_version", "uuid", "at"))]
    body = rotate_angles(body, rot)
    i = next(i for i, v in enumerate(body) if isinstance(v, list) and v[0] == "layer") + 1
    at = ["at", S.num(P.ORIGIN[0] + x), S.num(P.ORIGIN[1] + y)] + ([S.num(rot)] if rot else [])
    body[i:i] = [["uuid", S.q(P.U(ref, "fp"))], at]
    j = max(i for i, v in enumerate(body) if isinstance(v, list) and v[0] == "property") + 1
    extra = [["path", S.q("/" + S.U("sym", ref, 1))], ["sheetname", '"/"'],
             ["sheetfile", S.q(S.PROJECT + ".kicad_sch")]]
    if part["lcsc"]:
        extra.insert(0, ["property", '"LCSC"', S.q(part["lcsc"]), ["at", "0", "0", S.num(rot)],
                         ["layer", '"F.Fab"'], ["hide", "yes"], ["uuid", S.q(P.U(ref, "lcsc"))],
                         ["effects", ["font", ["size", "1", "1"]]]])
    body[j:j] = extra
    return ["footprint", S.q(part["fp"])] + body


def main():
    board = S.parse(open(P.OUT, encoding="utf-8").read())[0]
    present = {[p[2] for p in P.sub(f, "property") if p[1] == '"Reference"'][0].strip('"')
               for f in P.sub(board, "footprint")}
    parts = {}
    for p in S.parts:                       # multi-unit symbols: one footprint per reference
        if p["fp"] and not p["ref"].startswith("#"):
            parts.setdefault(p["ref"], p)
    missing = sorted(set(parts) - present - set(PLACE))
    if missing:
        raise SystemExit(f"no floorplan position for: {missing}")
    nets = P.netlist()
    new = [footprint(r, parts[r], nets, *PLACE[r]) for r in PLACE if r not in present]
    insert_at = max(i for i, e in enumerate(board) if isinstance(e, list) and e[0] == "footprint") + 1
    board[insert_at:insert_at] = new
    open(P.OUT, "w", encoding="utf-8", newline="\n").write(S.dump(board) + "\n")
    print(f"added {len(new)} footprints ({len(present)} were already placed); "
          f"board now has {len(present) + len(new)}")


if __name__ == "__main__":
    main()
