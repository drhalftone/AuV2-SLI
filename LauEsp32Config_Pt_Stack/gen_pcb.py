"""Create the STARTING board: outline, mount holes, stackup and the six DF40 connectors.

    python gen_pcb.py            # refuses if LauEsp32Config_Pt_Stack.kicad_pcb exists
    python gen_pcb.py --force    # overwrite (destroys any layout done since)

This is a one-shot seed, not a generator to re-run: once layout starts in KiCad, the
.kicad_pcb is the source of truth. Everything placed here is fixed by the Alchitry
element standard, so there is nothing in it a layout pass should move.

Geometry -- all copied, none invented (see SCHEMATIC.md 11):
  * Edge.Cuts outline, notch and 4 x 2.2 mm mount holes: copied element-for-element from
    LauPythonCamera_Pt_Stack.kicad_pcb, which matches the FABBED, WORKING
    LauCameraTrigger_Alchitry_Stack board to 0.001 mm after its origin shift.
  * Board origin (100, 60), so every coordinate here equals the camera board's.
  * Stackup / setup: the camera board's JLC04161H-7628 4-layer 1.6 mm.
  * Plug positions: the camera board's J1/J2/J3 (element standard README 8).
  * Receptacles: same XY, F.Cu, rotation 0 -- pin n sits directly above plug pin n
    (checked below and by check_passthrough()).

Pad nets come from kicad-cli's netlist export of the schematic, so they are exactly what
ERC saw. Footprints carry the schematic symbol UUID as their path, so KiCad's
"Update PCB from Schematic" recognises them instead of adding duplicates.
"""
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_schematic as S  # noqa: E402  (parts list, symbol UUIDs, s-expr helpers)

PROJECT = S.PROJECT
CLI = os.path.expandvars(r"%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe")
CAMERA = os.path.join(os.path.dirname(HERE), "LauPythonCamera_Pt_Stack", "LauPythonCamera_Pt_Stack.kicad_pcb")
OUT = os.path.join(HERE, PROJECT + ".kicad_pcb")
NS = uuid.UUID("0c0b1d5e-4f0a-4b8e-9a51-7d7f3c2e6a10")
ORIGIN = (100.0, 60.0)

# ref -> (x, y relative to the board corner, layer). Plugs = element standard (camera README 8).
PLACE = {
    "J3": (16.5, 4.0, "B.Cu"),   # Control 50-pin plug
    "J1": (38.0, 4.0, "B.Cu"),   # Bank A 80-pin plug
    "J2": (38.0, 41.0, "B.Cu"),  # Bank B 80-pin plug
    "J6": (16.5, 4.0, "F.Cu"),   # Control 50-pin 4.0 mm receptacle
    "J4": (38.0, 4.0, "F.Cu"),   # Bank A 80-pin 4.0 mm receptacle
    "J5": (38.0, 41.0, "F.Cu"),  # Bank B 80-pin 4.0 mm receptacle
}


def U(*k):
    return str(uuid.uuid5(NS, "/".join(map(str, k))))


def sub(e, k):
    return [x for x in e if isinstance(x, list) and x and x[0] == k]


def netlist():
    """(ref, pin) -> net name, from the schematic as KiCad itself resolves it."""
    with tempfile.TemporaryDirectory() as d:
        xml = os.path.join(d, "net.xml")
        r = subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadxml", "-o", xml,
                            os.path.join(HERE, PROJECT + ".kicad_sch")], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stdout + r.stderr)
        root = ET.parse(xml).getroot()
    nets = {}
    for n in root.iter("net"):
        for node in n.findall("node"):
            nets[(node.get("ref"), node.get("pin"))] = n.get("name")
    return nets


def footprint(ref, part, nets):
    lib, name = part["fp"].split(":")
    fp = S.parse(open(os.path.join(HERE, "LauEsp32.pretty", name + ".kicad_mod"), encoding="utf-8").read())[0]
    x, y, layer = PLACE[ref]
    native = sub(fp, "layer")[0][1].strip('"')
    if native != layer:
        raise SystemExit(f"{ref}: {name} is drawn on {native}, needs {layer} -- refusing to flip it here")
    out = ["footprint", S.q(part["fp"])]
    count = [0]

    def walk(e):
        if not isinstance(e, list):
            return e
        if e and e[0] == "uuid":
            count[0] += 1
            return ["uuid", S.q(U(ref, count[0]))]
        if e and e[0] == "property" and e[1] == '"Reference"':
            e = e[:2] + [S.q(ref)] + e[3:]
        if e and e[0] == "property" and e[1] == '"Value"':
            e = e[:2] + [S.q(part["value"])] + e[3:]
        if e and e[0] == "pad":
            net = nets.get((ref, e[1].strip('"')))
            # drop any net the library footprint already carries: the DF40 plugs were copied from
            # the camera board with ITS nets inside, and a stale net beside ours wins (2026-09-16)
            e = [walk(x) for x in e if not (isinstance(x, list) and x and x[0] == "net")]
            if net:
                e.insert(next(i for i, x in enumerate(e) if isinstance(x, list) and x[0] == "layers") + 1,
                         ["net", S.q(net)])
            return e
        return [walk(x) for x in e]

    body = [walk(x) for x in fp[2:] if not (isinstance(x, list) and x[0] in ("version", "generator",
                                                                               "generator_version"))]
    body = [e for e in body if not (isinstance(e, list) and e[0] in ("uuid", "at"))]  # top-level only
    at_idx = next(i for i, e in enumerate(body) if isinstance(e, list) and e[0] == "layer") + 1
    body[at_idx:at_idx] = [["uuid", S.q(U(ref, "fp"))], ["at", S.num(ORIGIN[0] + x), S.num(ORIGIN[1] + y)]]
    props_end = max(i for i, x in enumerate(body) if isinstance(x, list) and x[0] == "property") + 1
    body[props_end:props_end] = [
        ["property", '"LCSC"', S.q(part["lcsc"]), ["at", "0", "0", "0"], ["layer", '"F.Fab"'], ["hide", "yes"],
         ["uuid", S.q(U(ref, "lcsc"))], ["effects", ["font", ["size", "1", "1"]]]],
        ["path", S.q("/" + S.U("sym", ref, 1))],
        ["sheetname", '"/"'],
        ["sheetfile", S.q(PROJECT + ".kicad_sch")],
    ]
    return out + body


def camera_board():
    return S.parse(open(CAMERA, encoding="utf-8").read())[0]


def check_passthrough(board):
    """Worst distance, per connector pair, between top pin n and bottom pin n (absolute)."""
    def abs_pads(fp):
        ax, ay = map(float, sub(fp, "at")[0][1:3])
        d = {}
        for p in sub(fp, "pad"):
            px, py = map(float, sub(p, "at")[0][1:3])
            d[p[1].strip('"')] = (ax + px, ay + py)
        return d

    fps = {[p[2] for p in sub(f, "property") if p[1] == '"Reference"'][0].strip('"'): f for f in sub(board, "footprint")}
    worst = {}
    for bot, top in (("J1", "J4"), ("J2", "J5"), ("J3", "J6")):
        b, t = abs_pads(fps[bot]), abs_pads(fps[top])
        pins = [k for k in t if k.isdigit()]
        worst[(bot, top)] = max(math.hypot(b[k][0] - t[k][0], abs(b[k][1] - t[k][1]) - 0.185) for k in pins)
        xs = max(abs(b[k][0] - t[k][0]) for k in pins)
        side = all((b[k][1] - (ORIGIN[1] + PLACE[bot][1])) * (t[k][1] - (ORIGIN[1] + PLACE[top][1])) > 0 for k in pins)
        print(f"  {bot}(bottom) / {top}(top): {len(pins)} pins, worst X offset {xs:.3f} mm, "
              f"every pin on the same row side: {side}")
        if xs > 0.001 or not side:
            raise SystemExit("pass-through pin mismatch")


def main():
    if os.path.exists(OUT) and "--force" not in sys.argv:
        raise SystemExit(f"{OUT} exists -- layout may have started. Use --force to overwrite it.")
    cam = camera_board()
    head = [x for x in cam[1:] if isinstance(x, list) and x[0] in ("version", "generator", "generator_version",
                                                                    "general", "paper", "layers", "setup")]
    edges = []
    for i, e in enumerate(x for x in cam[1:] if isinstance(x, list) and x[0] in ("gr_line", "gr_circle", "gr_arc")):
        if sub(e, "layer") and sub(e, "layer")[0][1] == '"Edge.Cuts"':
            edges.append([y if not (isinstance(y, list) and y[0] == "uuid") else ["uuid", S.q(U("edge", i))]
                          for y in e])
    nets = netlist()
    parts = {p["ref"]: p for p in S.parts}
    fps = [footprint(ref, parts[ref], nets) for ref in PLACE]
    board = ["kicad_pcb"] + head + fps + edges + [["embedded_fonts", "no"]]
    for e in board:
        if isinstance(e, list) and e[0] == "generator":
            e[1] = '"gen_pcb.py"'
    check_passthrough(board)
    open(OUT, "w", encoding="utf-8", newline="\n").write(S.dump(board) + "\n")
    print(f"wrote {OUT}: {len(fps)} footprints, {len(edges)} Edge.Cuts elements, "
          f"{sum(1 for f in fps for p in sub(f, 'pad') if sub(p, 'net'))} pads with nets")


if __name__ == "__main__":
    main()
