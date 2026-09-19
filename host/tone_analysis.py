#!/usr/bin/env python3
"""Floor alignment for slice-and-sum tone sweeps -- part of the analysis, not an extra.

    python host/tone_analysis.py tone_slices_420_lutall.csv --tag lutall
    from tone_analysis import icp_floor_offset          # used live by the sweep scripts

WHY. Each level's trace is 416 slices tiling the frame, and nearly all of them are
dark. So the dark floor, not the light, dominates the summed total: a 1 ADU floor
drift moves the sum by ~416, while the light from a dim square is a few hundred.
The floor drifts a few ADU over a run (warm-up), which once made W=0 read brighter
than W=17.

HOW. The first trace measured (W=0 in the bisection order) is the REFERENCE. Every
other trace is shifted in y by an ICP translation so its floor lies on the
reference's floor: correspondences are taken at the same delay, pairs whose
residual exceeds --thresh (the lit slices) are rejected, and the mean of the rest
is the step. Rejection is by a fixed residual, not by "smallest residuals": a
trimmed median keeps the pairs that already agree and answers 0 on data quantised
to 0.5 ADU. What is left after alignment, sum(aligned - reference), is the light
above the reference.
"""
import argparse
import csv
import os

import numpy as np
from scipy.spatial import cKDTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def icp_floor_offset(ref, mov, thresh=4.0, iters=50):
    """y translation to ADD to `mov` so its floor lies on `ref`'s. Both are (n,2)
    arrays of (delay_us, value)."""
    # Start from the difference of the two traces' 20th-percentile levels (their floors).
    # Fixed-threshold rejection needs the floors to begin within `thresh` of each other;
    # a trace whose floor sits 10+ ADU away matched nothing from zero and stayed put.
    ty = float(np.percentile(ref[:, 1], 20) - np.percentile(mov[:, 1], 20))
    tree = cKDTree(ref)
    for _ in range(iters):
        idx = tree.query(np.c_[mov[:, 0], mov[:, 1] + ty])[1]
        res = ref[idx, 1] - (mov[:, 1] + ty)
        inl = np.abs(res) < thresh
        if not inl.any():
            break
        step = res[inl].mean()
        ty += step
        if abs(step) < 1e-4:
            break
    return float(ty)


def light_mask(ref, mov, ty, thresh=4.0):
    """Classify each slice of `mov` after floor alignment. Returns (light, ref_light):
    light     -- mov is brighter than the reference by more than `thresh` ADU: light in mov
    ref_light -- mov is DARKER than the reference by more than `thresh`: the reference had
                 light there and mov does not, so the slice is not evidence about mov.
    Everything else is an ICP inlier (floor matches floor): no light."""
    res = (mov[:, 1] + ty) - ref[:, 1]
    return res > thresh, res < -thresh


def analyze(summary_csv, tag, ref_file=None, thresh=4.0, plot=True):
    rows = list(csv.DictReader(open(summary_csv, newline="")))
    tr = lambda L, sfx="": os.path.join(
        ROOT, "tone_slices_420_%s_L%03d%s.csv" % (tag, L, sfx))
    load = lambda p: np.loadtxt(p, delimiter=",", skiprows=1)
    first = rows[0]
    ref = load(ref_file or tr(int(first["level"])))
    out = []
    for r in rows:
        L, kind = int(r["level"]), r["kind"]
        if r is first:
            t, ty = ref, 0.0
        else:
            p = tr(L, "r" if kind == "repeat" else "")
            if not os.path.exists(p):
                continue
            t = load(p)
            ty = icp_floor_offset(ref, t, thresh)
        out.append(dict(level=L, kind=kind, t_s=float(r["t_s"]), shift=ty,
                        raw=float(t[:, 1].sum()),
                        aligned=float((t[:, 1] + ty).sum()),
                        light=float(((t[:, 1] + ty) - ref[:, 1]).sum()), trace=t))
    sweep = sorted([o for o in out if o["kind"] == "sweep"], key=lambda o: o["level"])
    print("  W   t_s   ICP shift   raw sum    aligned    light above ref")
    for o in sweep + [o for o in out if o["kind"] == "repeat"]:
        print("%3d%s %5.0f  %+8.3f  %9.1f  %9.1f  %10.1f" % (
            o["level"], "r" if o["kind"] == "repeat" else " ", o["t_s"], o["shift"],
            o["raw"], o["aligned"], o["light"]))
    top = sweep[-1]
    if top["level"] == 255 and top["light"] > 0:
        dev = [(o["level"], o["light"] - top["light"] * o["level"] / 255.0) for o in sweep]
        worst = max(dev, key=lambda d: abs(d[1]))
        print("\nlinearity: worst deviation from the 0..255 line is %+.0f at W=%d (%.1f%% of full)"
              % (worst[1], worst[0], 100 * abs(worst[1]) / top["light"]))
    dst = os.path.splitext(summary_csv)[0] + "_aligned.csv"
    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["level", "kind", "t_s", "icp_shift", "raw_sum", "aligned_sum", "light_above_ref"])
        for o in out:
            w.writerow([o["level"], o["kind"], o["t_s"], round(o["shift"], 4),
                        round(o["raw"], 1), round(o["aligned"], 1), round(o["light"], 1)])
    print("wrote", dst)
    if plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(3, 1, figsize=(13, 13), gridspec_kw={"height_ratios": [3, 2, 3]})
        cm = plt.get_cmap("viridis")
        for o in sweep:
            ax[0].plot(o["trace"][:, 0], o["trace"][:, 1] + o["shift"], lw=.9,
                       color=cm(o["level"] / 255), label="W=%d" % o["level"])
            ax[1].plot(o["trace"][:, 0], o["trace"][:, 1] + o["shift"], lw=.9,
                       color=cm(o["level"] / 255))
        ax[0].set_title("traces ICP-aligned to the first-measured trace")
        ax[0].legend(ncol=4, fontsize=8)
        ax[1].set_ylim(np.percentile(ref[:, 1], 2) - 6, np.percentile(ref[:, 1], 2) + 22)
        ax[1].set_ylabel("floor zoom"); ax[1].set_xlabel("delay (us)")
        ax[2].plot([o["level"] for o in sweep], [o["light"] for o in sweep], "o-",
                   color="#1a8f3c", label="light above reference (aligned)")
        ax[2].plot([o["level"] for o in sweep], [o["raw"] - sweep[0]["raw"] for o in sweep],
                   "o--", color="#999", label="raw sum minus reference raw sum")
        if top["level"] == 255:
            ax[2].plot([0, 255], [0, top["light"]], ":", color="#d0242a", label="linear to W=255")
        ax[2].set_xlabel("commanded W"); ax[2].set_ylabel("light per frame"); ax[2].legend()
        for a in ax:
            a.grid(alpha=.3)
        fig.tight_layout()
        png = os.path.splitext(summary_csv)[0] + "_aligned.png"
        fig.savefig(png, dpi=120)
        print("wrote", png)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("summary", help="the sweep's summary CSV (level,...,t_s,kind)")
    ap.add_argument("--tag", required=True, help="trace-file tag: tone_slices_420_<tag>_L###.csv")
    ap.add_argument("--ref-file", help="reference trace, if the first level's file was overwritten")
    ap.add_argument("--thresh", type=float, default=4.0, help="ADU; larger residuals are lit slices")
    a = ap.parse_args()
    analyze(a.summary, a.tag, a.ref_file, a.thresh)


if __name__ == "__main__":
    main()
