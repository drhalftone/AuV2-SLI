"""Measure the Hd's top-side heights from Alchitry's STEP, and what that leaves for the card's
bottom face.

    python hd_clearance.py          # needs docs/Hd.step (cdn.alchitry.com/docs/Hd-V2/Hd.step)

Why not read the STEP directly: its raw point cloud carries axis placements and
construction geometry (README 6.3). Instead KiCad tessellates it: Hd.step is attached as
the 3D model of a dummy footprint and exported with `kicad-cli pcb export stl`; only real
faces survive. From the triangles:
  * the Hd's board top surface is found as the dominant horizontal plane (z = 3.12 in the
    model), and every top-side triangle's height above it is rasterised (conservative:
    each triangle's max z over its XY bounding box) at 0.1 mm;
  * the model frame is y-UP; board frame (KiCad, y DOWN from the board corner) is
    y = 45 - y_model. Checked by the DF40 receptacles landing on the element-standard
    positions (16.5, 4) / (38, 4) / (38, 41);
  * clearance for this card's bottom face = STACK_GAP - Hd height.
Writes mech/hd_top_clearance.png and prints the blobs.
"""
import os
import re
import subprocess
import tempfile
from collections import deque

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CLI = os.path.expandvars(r"%LOCALAPPDATA%\Programs\KiCad\10.0\bin\kicad-cli.exe")
STEP = os.path.join(HERE, "docs", "Hd.step")
STACK_GAP = 4.125          # board surface to board surface, README 6.3
W, H, RES = 55.0, 45.0, 0.1


def tessellate():
    src = open(os.path.join(REPO, "LauPythonCamera_Pt_Stack", "LauPythonCamera_Pt_Stack.kicad_pcb"),
               encoding="utf-8").read().split("\n")
    end = next(i for i, l in enumerate(src) if l.startswith("\t(footprint"))
    step = STEP.replace("\\", "/")
    fp = ('\t(footprint "HD_MODEL" (layer "F.Cu") (uuid "11111111-2222-3333-4444-555555555555") (at 0 0)\n'
          '\t\t(property "Reference" "M1" (at 0 0 0) (layer "F.Fab") (hide yes) '
          '(uuid "11111111-2222-3333-4444-555555555556") (effects (font (size 1 1))))\n'
          f'\t\t(attr smd)\n\t\t(model "{step}" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))\n\t)\n'
          '\t(gr_rect (start -1 -1) (end 1 1) (stroke (width 0.05) (type default)) (fill no) '
          '(layer "Edge.Cuts") (uuid "11111111-2222-3333-4444-555555555557"))\n)\n')
    with tempfile.TemporaryDirectory() as d:
        pcb, stl = os.path.join(d, "hd.kicad_pcb"), os.path.join(d, "hd.stl")
        open(pcb, "w", encoding="utf-8").write("\n".join(src[:end]) + "\n" + fp)
        r = subprocess.run([CLI, "pcb", "export", "stl", "-f", "--no-board-body", "-o", stl, pcb],
                           capture_output=True, text=True)
        if r.returncode or not os.path.exists(stl):
            raise SystemExit(r.stdout + r.stderr)
        v = np.array(re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", open(stl, encoding="ascii").read()), float)
    return v.reshape(-1, 3, 3)


def board_top(tris):
    """Dominant horizontal plane among the upper half: the Hd's top surface."""
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(n, axis=1) / 2
    horiz = np.abs(n[:, 2]) > 0.99 * np.linalg.norm(n, axis=1)
    z = np.round(tris[:, :, 2].mean(axis=1), 2)
    zs, inv = np.unique(z[horiz], return_inverse=True)
    acc = np.bincount(inv, weights=area[horiz])
    big = zs[acc > 0.5 * W * H]                     # planes the size of the board
    return big.max()                                # the higher of bottom/top surface planes


def main():
    tris = tessellate()
    ztop = board_top(tris)
    nx, ny = int(W / RES), int(H / RES)
    h = np.zeros((ny, nx))                          # row = board-frame y (DOWN)
    up = tris[tris[:, :, 2].max(axis=1) > ztop + 0.02]
    xa = np.clip((up[:, :, 0].min(1) / RES).astype(int), 0, nx - 1)
    xb = np.clip((up[:, :, 0].max(1) / RES).astype(int), 0, nx - 1)
    ya = np.clip(((H - up[:, :, 1].max(1)) / RES).astype(int), 0, ny - 1)
    yb = np.clip(((H - up[:, :, 1].min(1)) / RES).astype(int), 0, ny - 1)
    for a, b, c, d, z in zip(xa, xb, ya, yb, up[:, :, 2].max(1) - ztop):
        sl = h[c:d + 1, a:b + 1]
        np.maximum(sl, z, out=sl)

    lab = np.zeros(h.shape, int)
    blobs = []
    for sy, sx in zip(*np.nonzero(h > 0.05)):
        if lab[sy, sx]:
            continue
        lab[sy, sx] = len(blobs) + 1
        q, cells = deque([(sy, sx)]), []
        while q:
            y, x = q.popleft()
            cells.append((y, x))
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < ny and 0 <= xx < nx and h[yy, xx] > 0.05 and not lab[yy, xx]:
                    lab[yy, xx] = len(blobs) + 1
                    q.append((yy, xx))
        ys, xs = zip(*cells)
        blobs.append((max(h[c] for c in cells), min(xs) * RES, (max(xs) + 1) * RES, min(ys) * RES, (max(ys) + 1) * RES))
    blobs.sort(key=lambda b: -b[0])
    print(f"Hd board top surface at model z = {ztop:.2f}; stack gap {STACK_GAP} mm")
    print("Hd top-side parts over 0.5 mm (board frame, mm from the corner, y DOWN):")
    for hh, x0, x1, y0, y1 in blobs:
        if hh > 0.5:
            print(f"  height {hh:4.2f}  x {x0:4.1f}-{x1:4.1f}  y {y0:4.1f}-{y1:4.1f}   "
                  f"-> card bottom-face clearance {STACK_GAP - hh:4.2f} mm")

    s = 14
    clear = STACK_GAP - h
    img = Image.new("RGB", (int(W * s), int(H * s)))
    px = img.load()
    for y in range(ny):
        for x in range(nx):
            c = clear[y, x]
            col = (40, 110, 60) if c > 2.5 else (200, 170, 40) if c > 1.5 else (220, 90, 30) if c > 0.5 else (180, 30, 30)
            for dy in range(int(s * RES + 0.99)):
                for dx in range(int(s * RES + 0.99)):
                    xx, yy = int(x * s * RES) + dx, int(y * s * RES) + dy
                    if xx < img.width and yy < img.height:
                        px[xx, yy] = col
    d = ImageDraw.Draw(img)
    for hh, x0, x1, y0, y1 in blobs:
        if hh > 0.5:
            d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], outline=(255, 255, 255))
            d.text((x0 * s + 2, y0 * s + 2), f"{hh:.2f}", fill=(255, 255, 255))
    d.text((4, int(H * s) - 14), "card bottom-face clearance: green >2.5  yellow 1.5-2.5  orange 0.5-1.5  red <0.5 mm "
           "(view from ABOVE, card frame)", fill=(255, 255, 255))
    os.makedirs(os.path.join(HERE, "mech"), exist_ok=True)
    img.save(os.path.join(HERE, "mech", "hd_top_clearance.png"))


if __name__ == "__main__":
    main()
