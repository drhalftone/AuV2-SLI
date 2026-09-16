"""Force-directed ("springs") placement of the movable parts on the board.

    python spring_place.py [--iters N] [--floor F] [--gif out.gif] [--dry-run]

Physics, per iteration:
  * SPRINGS   every signal net pulls its pads together (star model: each pad toward the
              net's pad centroid, weight 1/(n-1)). GND and +3V3 get NO springs: they reach
              every part and would collapse the board into one clump, and they will be
              planes anyway. Parts whose only nets are power (decoupling caps) get a stiff
              spring to the one power pin they serve instead (DECAP below).
  * SPREADING soft repulsion between every pair of part centres, annealing from 1 to
              --floor (default 0.35) over the first 70 % of the run and holding there, so the
              final layout is a balance of springs and spreading that covers the board.
              Parts with a power pin (decoupling caps, pull-ups/downs, the EN RC) are exempt:
              their springs hold them at the pin they serve.
  * OVERLAP   hard push-apart of courtyards (plus GAP) against each other and against
              fixed parts and keep-outs.
  * BOUNDARY  courtyards stay inside the board, clear of the notch, the mount-hole
              stand-offs and the antenna-cable exit.
Then: legalise (overlap-only iterations until nothing overlaps), try each part's four
rotations and keep the shortest-wire legal one, legalise again, snap to a 0.05 mm grid.

Fixed: the DF40s (element standard), U1 ESP32 and J7 microSD (user decision 2026-09-16).
TP1-TP8 move as ONE rigid strip so the 2.54 mm pogo pitch survives.
Only same-side courtyards interact: all movable parts are on F.Cu, so the B.Cu DF40 plugs
are not obstacles.
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_pcb as P         # noqa: E402
import gen_schematic as S   # noqa: E402

BOARD_FILE = P.OUT
OX, OY = P.ORIGIN
W, H = 55.0, 45.0
EDGE = 0.5            # courtyard inset from the board edge (mm)
GAP = 0.25            # extra courtyard-to-courtyard gap (mm)
FIXED = {"J1", "J2", "J3", "J4", "J5", "J6", "U1", "J7"}
GROUPS = {"TPSTRIP": [f"TP{i}" for i in range(1, 9)]}
POWER = {"GND", "+3V3"}
# decoupling / power-only parts -> (ref, pad) they serve, and spring weight
DECAP = {"C1": ("U1", "3"), "C2": ("U1", "3"), "C3": ("U1", "3"), "C4": ("U1", "3"),
         "C6": ("U2", "14"), "C7": ("J7", "4"), "C8": ("J7", "4")}
K_DECAP = 4.0
SPREAD_FLOOR = float(sys.argv[sys.argv.index("--floor") + 1]) if "--floor" in sys.argv else 0.35
# B.Cu only: no footprints over the Hd's HDMI connectors (add_hdmi_keepout.py, README 6.3).
# Every movable part is on F.Cu today; any bottom-side placement pass must honour this.
from add_hdmi_keepout import HDMI_KEEPOUT       # noqa: E402
BOTTOM_KEEPOUTS = [HDMI_KEEPOUT]
KEEPOUTS = [  # (x0, y0, x1, y1) board-corner coordinates, TOP side
    (49.5 - EDGE, 8.0, W, 37.0),                    # the notch
    (53.5 - EDGE, 6.5 - EDGE, W, 8.0),              # 1.5 mm chamfer where the notch meets the top-right
    (53.5 - EDGE, 37.0, W, 38.5 + EDGE),            # ... and the bottom-right edge
    (44.5, 28.0, 49.5, 34.5),                       # antenna connector + coax exit to the notch
] + [(cx - 3.0, cy - 3.0, cx + 3.0, cy + 3.0)       # M2 stand-off / nut around each mount hole
     for cx, cy in ((2.5, 2.5), (52.5, 2.5), (2.5, 42.5), (52.5, 42.5))]


def sub(e, k):
    return [x for x in e if isinstance(x, list) and x and x[0] == k]


def ref_of(fp):
    return [p[2] for p in sub(fp, "property") if p[1] == '"Reference"'][0].strip('"')


def xform(x, y, rot):
    t = math.radians(rot)
    return x * math.cos(t) + y * math.sin(t), -x * math.sin(t) + y * math.cos(t)


def courtyard_local(fp):
    xs, ys = [], []
    for e in fp:
        if not (isinstance(e, list) and e and e[0] in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc")):
            continue
        if not (sub(e, "layer") and "CrtYd" in sub(e, "layer")[0][1]):
            continue
        if e[0] == "fp_circle":
            cx, cy = map(float, sub(e, "center")[0][1:3])
            ex, ey = map(float, sub(e, "end")[0][1:3])
            r = math.hypot(ex - cx, ey - cy)
            xs += [cx - r, cx + r]
            ys += [cy - r, cy + r]
            continue
        for k in ("start", "end", "mid"):
            for s in sub(e, k):
                xs.append(float(s[1]))
                ys.append(float(s[2]))
        for pts in sub(e, "pts"):
            for xy in sub(pts, "xy"):
                xs.append(float(xy[1]))
                ys.append(float(xy[2]))
    return min(xs), min(ys), max(xs), max(ys)


class Part:
    """A rigid body: one footprint, or a group of footprints moving together."""

    def __init__(self, name, fps, fixed):
        self.name, self.fps, self.fixed = name, fps, fixed
        ats = [list(map(float, sub(f, "at")[0][1:4])) + [0.0] * (3 - len(sub(f, "at")[0][1:4])) for f in fps]
        self.x = np.mean([a[0] - OX for a in ats])
        self.y = np.mean([a[1] - OY for a in ats])
        self.rot = ats[0][2] if len(fps) == 1 else 0.0
        self.members = []      # (fp, dx, dy, local courtyard, local pads)
        for f, a in zip(fps, ats):
            pads = []
            for p in sub(f, "pad"):
                px, py = map(float, sub(p, "at")[0][1:3])
                n = sub(p, "net")
                pads.append((p[1].strip('"'), px, py, n[0][1].strip('"') if n else None))
            self.members.append([f, a[0] - OX - self.x, a[1] - OY - self.y, courtyard_local(f), pads,
                                 a[2] - (self.rot if len(fps) == 1 else 0.0)])

    def bbox(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        rot = self.rot if rot is None else rot
        xs, ys = [], []
        for f, dx, dy, (a, b, c, d), pads, base in self.members:
            r = rot + base
            for u, v in ((a, b), (c, b), (a, d), (c, d)):
                X, Y = xform(u, v, r)
                xs.append(x + dx + X)
                ys.append(y + dy + Y)
        return min(xs), min(ys), max(xs), max(ys)

    def pads(self, x=None, y=None, rot=None):
        x = self.x if x is None else x
        y = self.y if y is None else y
        rot = self.rot if rot is None else rot
        for f, dx, dy, cy, pads, base in self.members:
            for name, px, py, net in pads:
                X, Y = xform(px, py, rot + base)
                yield ref_of(f), name, net, x + dx + X, y + dy + Y


def overlap(a, b, gap):
    ox = min(a[2], b[2]) - max(a[0], b[0]) + gap
    oy = min(a[3], b[3]) - max(a[1], b[1]) + gap
    return (ox, oy) if ox > 0 and oy > 0 else None


def main():
    iters = int(sys.argv[sys.argv.index("--iters") + 1]) if "--iters" in sys.argv else 1500
    gif = sys.argv[sys.argv.index("--gif") + 1] if "--gif" in sys.argv else None
    board = S.parse(open(BOARD_FILE, encoding="utf-8").read())[0]
    fps = {ref_of(f): f for f in sub(board, "footprint")}
    top = {r: f for r, f in fps.items() if sub(f, "layer")[0][1] == '"F.Cu"'}

    grouped = {m for ms in GROUPS.values() for m in ms}
    parts = [Part(g, [top[m] for m in ms], False) for g, ms in GROUPS.items()]
    parts += [Part(r, [f], r in FIXED) for r, f in top.items() if r not in grouped]
    movable = [p for p in parts if not p.fixed]
    fixed = [p for p in parts if p.fixed]
    by_ref = {ref_of(f): p for p in parts for f in p.fps}
    anchored = {p.name for p in movable if len(p.fps) == 1
                and any(net in POWER for _, _, net, _, _ in p.pads())}
    print(f"{len(movable)} movable bodies, {len(fixed)} fixed (+ {len(KEEPOUTS)} keep-outs); "
          f"{len(anchored)} with a power pin are not spread")

    def wirelength():
        nets, moves = {}, set()
        for p in parts:
            for ref, pad, net, x, y in p.pads():
                if net and net not in POWER:
                    nets.setdefault(net, []).append((x, y))
                    if not p.fixed:
                        moves.add(net)
        total = 0.0
        for net, pts in nets.items():
            if len(pts) > 1 and net in moves:   # nets touching only fixed parts cannot change
                xs, ys = zip(*pts)
                total += (max(xs) - min(xs)) + (max(ys) - min(ys))
        return total

    def decap_len():
        tot = 0.0
        for c, (r, pad) in DECAP.items():
            cp = by_ref[c]
            cx, cy = (cp.bbox()[0] + cp.bbox()[2]) / 2, (cp.bbox()[1] + cp.bbox()[3]) / 2
            tx, ty = next((x, y) for rr, pp, n, x, y in by_ref[r].pads() if rr == r and pp == pad)
            tot += math.hypot(cx - tx, cy - ty)
        return tot

    hpwl0, dec0 = wirelength(), decap_len()
    frames = []

    def draw(it):
        from PIL import Image, ImageDraw
        s = 12
        im = Image.new("RGB", (int(W * s) + 20, int(H * s) + 40), (30, 30, 36))
        d = ImageDraw.Draw(im)
        T = lambda x, y: (10 + x * s, 10 + y * s)
        d.rectangle([*T(0, 0), *T(W, H)], outline=(200, 200, 200))
        for k in KEEPOUTS:
            d.rectangle([*T(k[0], k[1]), *T(k[2], k[3])], outline=(120, 60, 60))
        for p in parts:
            b = p.bbox()
            col = (90, 90, 110) if p.fixed else (80, 180, 120) if p.name != "TPSTRIP" else (220, 190, 60)
            d.rectangle([*T(b[0], b[1]), *T(b[2], b[3])], outline=col, fill=None if p.fixed else col)
        d.text((10, int(H * s) + 18), f"iter {it}", fill=(230, 230, 230))
        frames.append(im)

    def forces(spread):
        F = {id(p): np.zeros(2) for p in movable}
        # springs: star model per signal net
        nets = {}
        for p in parts:
            for ref, pad, net, x, y in p.pads():
                if net and net not in POWER:
                    nets.setdefault(net, []).append((p, x, y))
        for pts in nets.values():
            if len(pts) < 2 or len({id(p) for p, _, _ in pts}) < 2:
                continue
            cx = np.mean([x for _, x, _ in pts])
            cy = np.mean([y for _, _, y in pts])
            w = 1.0 / (len(pts) - 1)
            for p, x, y in pts:
                if not p.fixed:
                    F[id(p)] += w * np.array([cx - x, cy - y])
        # decoupling springs to their power pin
        for c, (r, pad) in DECAP.items():
            cp = by_ref[c]
            if cp.fixed:
                continue
            tx, ty = next((x, y) for rr, pp, n, x, y in by_ref[r].pads() if rr == r and pp == pad)
            F[id(cp)] += K_DECAP * np.array([tx - cp.x, ty - cp.y])
        # spreading repulsion between centres (range ~ board size)
        if spread > 0:
            for i, a in enumerate(movable):
                if a.name in DECAP or a.name in anchored:
                    # decoupling caps, and pull-ups/downs/RCs with one power pin: a single weak
                    # signal spring loses to spreading and they drift 20-30 mm from the pin they
                    # serve (seen 2026-09-16). Hold them by their springs; overlap still applies.
                    continue
                for b in parts:
                    if b is a:
                        continue
                    dx, dy = a.x - b.x, a.y - b.y
                    d2 = dx * dx + dy * dy + 1.0
                    F[id(a)] += spread * 40.0 * np.array([dx, dy]) / d2
        return F

    def resolve(push=0.5):
        """One pass of hard constraints; returns total violation (mm)."""
        viol = 0.0
        obstacles = [p.bbox() for p in fixed] + KEEPOUTS
        for i, a in enumerate(movable):
            ba = a.bbox()
            for ob in obstacles:
                o = overlap(ba, ob, GAP)
                if o:
                    viol += min(o)
                    ca, co = ((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2), ((ob[0] + ob[2]) / 2, (ob[1] + ob[3]) / 2)
                    if o[0] < o[1]:
                        a.x += math.copysign(o[0], ca[0] - co[0] or 1)
                    else:
                        a.y += math.copysign(o[1], ca[1] - co[1] or 1)
                    ba = a.bbox()
            for b in movable[i + 1:]:
                bb = b.bbox()
                o = overlap(ba, bb, GAP)
                if o:
                    viol += min(o)
                    if o[0] < o[1]:
                        sgn = math.copysign(1, (ba[0] + ba[2]) - (bb[0] + bb[2]) or 1)
                        a.x += sgn * o[0] * push
                        b.x -= sgn * o[0] * push
                    else:
                        sgn = math.copysign(1, (ba[1] + ba[3]) - (bb[1] + bb[3]) or 1)
                        a.y += sgn * o[1] * push
                        b.y -= sgn * o[1] * push
                    ba = a.bbox()
            # board boundary
            ba = a.bbox()
            if ba[0] < EDGE:
                viol += EDGE - ba[0]; a.x += EDGE - ba[0]
            if ba[2] > W - EDGE:
                viol += ba[2] - (W - EDGE); a.x -= ba[2] - (W - EDGE)
            ba = a.bbox()
            if ba[1] < EDGE:
                viol += EDGE - ba[1]; a.y += EDGE - ba[1]
            if ba[3] > H - EDGE:
                viol += ba[3] - (H - EDGE); a.y -= ba[3] - (H - EDGE)
        return viol

    step = 0.05
    for it in range(iters):
        # spreading anneals from 1 down to SPREAD_FLOOR, never to zero: with no spreading at all
        # the springs collapse every part into one cluster (seen 2026-09-16, first run)
        spread = max(SPREAD_FLOOR, 1.0 - it / (0.7 * iters))
        F = forces(spread)
        for p in movable:
            f = F[id(p)] * step
            n = np.hypot(*f)
            if n > 0.5:                      # clamp per-iteration motion
                f *= 0.5 / n
            p.x += f[0]
            p.y += f[1]
        resolve()
        if gif and it % max(1, iters // 60) == 0:
            draw(it)

    def illegal(p):
        """True if body p overlaps anything or leaves the board."""
        b = p.bbox()
        if b[0] < EDGE - 1e-9 or b[1] < EDGE - 1e-9 or b[2] > W - EDGE + 1e-9 or b[3] > H - EDGE + 1e-9:
            return True
        others = [q.bbox() for q in parts if q is not p] + KEEPOUTS
        return any(overlap(b, o, GAP - 1e-6) for o in others)

    def nearest_free(p, step=0.25, rmax=40.0):
        """Spiral out from p's position to the closest legal spot (fallback for trapped bodies)."""
        x0, y0 = p.x, p.y
        r = step
        while r <= rmax:
            n = max(8, int(2 * math.pi * r / step))
            cands = sorted(((x0 + r * math.cos(2 * math.pi * k / n), y0 + r * math.sin(2 * math.pi * k / n))
                            for k in range(n)), key=lambda c: 0)
            for cx, cy in cands:
                p.x, p.y = cx, cy
                if not illegal(p):
                    return True
            r += step
        p.x, p.y = x0, y0
        return False

    jumped = set()

    def legalise(max_iter=400):
        for k in range(max_iter):
            if resolve(push=0.5) < 1e-6 and not any(illegal(p) for p in movable):
                return k
        for p in movable:                       # push-apart stalled: relocate what is still illegal
            if illegal(p):
                if not nearest_free(p):
                    raise SystemExit(f"no legal position found for {p.name}")
                jumped.add(p.name)
        if any(illegal(p) for p in movable):
            raise SystemExit("could not legalise: " + ", ".join(p.name for p in movable if illegal(p)))
        return max_iter

    legalise()
    # rotation pass: keep the legal orientation with the shortest wires
    improved = 0
    for p in movable:
        if p.name == "TPSTRIP":
            continue
        start = p.rot
        best = (wirelength() + 4 * decap_len(), start)
        for r in (0.0, 90.0, 180.0, 270.0):
            if r == start:
                continue
            p.rot = r
            if not illegal(p):                  # only orientations that fit where the part is
                score = wirelength() + 4 * decap_len()
                if score < best[0] - 1e-6:
                    best = (score, r)
        p.rot = best[1]
        improved += best[1] != start
    for p in movable:
        x, y = p.x, p.y
        p.x, p.y = round(x / 0.05) * 0.05, round(y / 0.05) * 0.05
        if illegal(p):
            p.x, p.y = x, y
    legalise()
    if jumped:
        print("relocated by nearest-free-spot search (push-apart stalled):", sorted(jumped))
    if gif:
        draw(iters)
        frames[0].save(gif, save_all=True, append_images=frames[1:] + [frames[-1]] * 15, duration=80, loop=0)

    hpwl1, dec1 = wirelength(), decap_len()
    print(f"signal wirelength (HPWL, power excluded): {hpwl0:.1f} -> {hpwl1:.1f} mm")
    print(f"decoupling cap distance to their pins:     {dec0:.1f} -> {dec1:.1f} mm")
    print(f"rotations changed: {improved}")
    if "--dry-run" in sys.argv:
        return

    # write back: footprint (at) and, where the rotation changed, pad/text angles
    import place_components as PC
    for p in movable:
        for f, dx, dy, cy, pads, base in p.members:
            at = sub(f, "at")[0]
            old = float(at[3]) if len(at) > 3 else 0.0
            new = (p.rot + base) % 360
            at[1:] = [S.num(OX + p.x + dx), S.num(OY + p.y + dy)] + ([S.num(new)] if new else [])
            if abs(new - old) > 1e-6:
                i = f.index(at)
                rotated = PC.rotate_angles([x for x in f if x is not at], new - old)
                f[:] = rotated[:i] + [at] + rotated[i:]
    open(BOARD_FILE, "w", encoding="utf-8", newline="\n").write(S.dump(board) + "\n")
    print(f"wrote {BOARD_FILE}")


if __name__ == "__main__":
    main()
