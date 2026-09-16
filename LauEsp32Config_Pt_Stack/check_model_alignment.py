"""Check that every 3D model in LauEsp32.pretty sits where its footprint says.

    python check_model_alignment.py [--keep DIR]

For each footprint: place it alone on a scratch board, render it with and without its
model (kicad-cli pcb render, orthographic), and measure the model from the pixels that
change. Scale comes from the board, whose size is known exactly. Checked against the
manufacturer drawing, to +/-0.1 mm:

  * model centred on the pad field (X and Y)
  * model length along X
  * model seated on the board surface (bottom at 0), and its overall height

Why renders and not the STEP files: a STEP point cloud carries axis placements and
construction geometry, so its extents are not the part's (README.md 6.3). The renderer
tessellates only real faces.

Orientation (e.g. a model turned 180 deg) is NOT caught by a bounding box -- see
SCHEMATIC.md 10 for the visual check that found the microSD one.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLI = os.path.expandvars(r"%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe")
MARGIN = 2.0
TOL = 0.1

# footprint: (expected model length along X, expected height, side, source of the numbers)
EXPECT = {
    "Hirose_DF40HC(4.0)-80DS-0.4V_2x40_P0.4mm": (18.6, 3.9, "top", "Hirose DF40 cat. p.6: A=18.6, D=3.9"),
    "Hirose_DF40HC(4.0)-50DS-0.4V_2x25_P0.4mm": (12.6, 3.9, "top", "Hirose DF40 cat. p.6: A=12.6, D=3.9"),
    "ESP32-S2-MINI-1U": (15.4, 2.4, "top", "ESP32-S3-MINI-1U datasheet v1.7 Fig. 10-2: 15.4, 2.4"),
    "microSD_HC_Molex_104031-0811": (11.95, 1.42, "top", "Molex SD-104031-001: 11.95 wide, 1.42 H"),
    "Hirose_DF40C-80DP-0.4V_2x40-1MP_P0.4mm": (17.52, None, "bottom", "Hirose DF40 cat.: A=17.52 (fabbed footprint)"),
    "Hirose_DF40C-50DP-0.4V_2x25-1MP_P0.4mm": (11.52, None, "bottom", "Hirose DF40 cat.: A=11.52 (fabbed footprint)"),
}


def cut_model(t):
    i = t.find("(model ")
    if i < 0:
        return t, None
    j, depth = i, 0
    while True:
        depth += {"(": 1, ")": -1}.get(t[j], 0)
        j += 1
        if depth == 0:
            break
    return t[:i] + t[j:], t[i:j]


def pad_extent(t):
    xs, ys = [], []
    for m in re.finditer(r'\(pad\s+"[^"]*"\s+\w+\s+\w+\s*\(at\s+([-\d.]+)\s+([-\d.]+)[^)]*\)\s*'
                         r'\(size\s+([-\d.]+)\s+([-\d.]+)', t):
        x, y, w, h = map(float, m.groups())
        xs += [x - w / 2, x + w / 2]
        ys += [y - h / 2, y + h / 2]
    return min(xs), max(xs), min(ys), max(ys)


def header():
    src = open(os.path.join(REPO, "LauPythonCamera_Pt_Stack", "LauPythonCamera_Pt_Stack.kicad_pcb"),
               encoding="utf-8").read().split("\n")
    end = next(i for i, l in enumerate(src) if l.startswith("\t(footprint"))
    return "\n".join(src[:end])


def render(pcb, side, png):
    r = subprocess.run([CLI, "pcb", "render", "-o", png, "--side", side, "-w", "2400", "-h", "1800",
                        "--background", "transparent", pcb], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)
    return np.array(Image.open(png)).astype(int)


def check(name, work, hdr):
    length, height, side, source = EXPECT[name]
    text = open(os.path.join(HERE, "LauEsp32.pretty", name + ".kicad_mod"), encoding="utf-8").read().strip()
    bare, model = cut_model(text)
    if model is None:
        return [f"{name}: no model"]
    model = model.replace("${KIPRJMOD}", HERE.replace("\\", "/"))
    x0, x1, y0, y1 = pad_extent(bare)
    bw = (x1 - x0) + 2 * MARGIN
    imgs = {}
    for tag, body in (("n", bare.rstrip()[:-1] + ")"), ("m", bare.rstrip()[:-1] + model + ")")):
        lines = body.split("\n")
        lines.insert(1, "(at 100 100)")
        pcb = os.path.join(work, f"{tag}.kicad_pcb")
        open(pcb, "w", encoding="utf-8").write(
            hdr + "\n" + "\n".join(lines) + "\n"
            f'(gr_rect (start {100 + x0 - MARGIN:.3f} {100 + y0 - MARGIN:.3f}) '
            f'(end {100 + x1 + MARGIN:.3f} {100 + y1 + MARGIN:.3f}) '
            '(stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts"))\n)\n')
        imgs[tag] = {s: render(pcb, s, os.path.join(work, f"{tag}_{s}.png")) for s in (side, "front")}

    def changed(s):
        return np.where(np.abs(imgs["n"][s] - imgs["m"][s]).sum(axis=2) > 30)

    # plan view
    ys, xs = np.where(imgs["n"][side][..., 3] > 0)
    px = (xs.max() - xs.min() + 1) / bw
    cy = (ys.max() + ys.min()) / 2
    cx = (xs.max() + xs.min()) / 2
    my, mx = changed(side)
    got_len = (mx.max() - mx.min() + 1) / px
    got_cx = ((mx.max() + mx.min()) / 2 - cx) / px * (-1 if side == "bottom" else 1) + (x0 + x1) / 2
    got_cy = ((my.max() + my.min()) / 2 - cy) / px + (y0 + y1) / 2
    # elevation
    fy, fx = np.where(imgs["n"]["front"][..., 3] > 0)
    fpx = (fx.max() - fx.min() + 1) / bw
    fdy, _ = changed("front")
    if side == "top":
        seat = (fy.min() - fdy.max()) / fpx          # model bottom relative to top surface
        got_h = (fy.min() - fdy.min()) / fpx
    else:
        seat = (fdy.min() - fy.max()) / fpx          # model top relative to bottom surface
        got_h = (fdy.max() - fy.max()) / fpx

    errs = []
    if abs(got_len - length) > TOL:
        errs.append(f"length {got_len:.2f} != {length}")
    if abs(got_cx - (x0 + x1) / 2) > TOL or abs(got_cy - (y0 + y1) / 2) > TOL:
        errs.append(f"off-centre by ({got_cx - (x0 + x1) / 2:+.2f}, {got_cy - (y0 + y1) / 2:+.2f})")
    if abs(seat) > TOL:
        errs.append(f"not seated: {seat:+.2f} mm from the surface")
    if height is not None and abs(got_h - height) > TOL:
        errs.append(f"height {got_h:.2f} != {height}")
    print(f"{name:45} {side:6} L {got_len:6.2f}  H {got_h:5.2f}  seat {seat:+.2f}  "
          f"{'ok' if not errs else 'FAIL: ' + '; '.join(errs)}   [{source}]")
    return [f"{name}: {e}" for e in errs]


def main():
    keep = sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv else None
    hdr = header()
    failures = []
    for name in EXPECT:
        work = tempfile.mkdtemp(prefix="modelcheck_")
        try:
            failures += check(name, work, hdr)
        finally:
            if keep:
                shutil.copytree(work, os.path.join(keep, name), dirs_exist_ok=True)
            shutil.rmtree(work, ignore_errors=True)
    print(f"\n{len(EXPECT)} models, {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
