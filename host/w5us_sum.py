#!/usr/bin/env python3
"""Total light against W from the per-level files written by host/level_5us_sweep.py.

    python host/w5us_sum.py --label blue460win

For every finished level (w5us_<label>_L###.csv, the repeat as ..._L000r.csv) this sums the trace
three ways and plots them against W:
  raw           sum of the ROI values over every slice
  above dark    sum of (value - that trace's own dark level); the dark level is the median of
                7000-7900 us, or of the last 100 us of a ranged sweep
  ICP-aligned   each trace shifted in y so its floor lies on the first W = 0 trace
                (tone_analysis.icp_floor_offset), then summed above that W = 0 trace
Levels with saturated ROI pixels are ringed in red: their light is a lower bound. The repeat of
W = 0 is drawn as a black square, so drift over the run is visible.
"""
import argparse
import glob
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from tone_analysis import icp_floor_offset                          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True)
    ap.add_argument("--min-rows", type=int, default=0, help="skip files with fewer rows (unfinished levels); "
                    "default: the longest file's length")
    a = ap.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ld = lambda p: np.loadtxt(p, delimiter=",", skiprows=1, ndmin=2)
    files = sorted(glob.glob(os.path.join(ROOT, "w5us_%s_L*.csv" % a.label)))
    data = {}
    for f in files:
        try:
            d = ld(f)
        except Exception:
            continue
        if d.shape[0] > 0:
            data[f] = d
    if not data:
        sys.exit("no files for label %s" % a.label)
    full = a.min_rows or max(d.shape[0] for d in data.values())
    rows = []
    for f, d in data.items():
        if d.shape[0] < full:
            continue
        L = int(re.search(r"_L(\d+)", os.path.basename(f)).group(1))
        x, y = d[:, 0], d[:, 1]
        dm = (x >= 7000) & (x < 7900)
        if not dm.any():
            dm = x >= x.max() - 100
        dark = float(np.nanmedian(y[dm]))
        rows.append(dict(L=L, rep=f.endswith("r.csv"), x=x, y=y, dark=dark, raw=float(np.nansum(y)),
                         light=float(np.nansum(y - dark)), sat=int((d[:, 4] > 0).sum()),
                         peak=float(np.nanmax(y))))
    rows.sort(key=lambda r: (r["L"], r["rep"]))
    zeros = [r for r in rows if r["L"] == 0 and not r["rep"]]
    if not zeros:
        sys.exit("the first W = 0 trace is not finished yet")
    ref = zeros[0]
    refxy = np.c_[ref["x"], ref["y"]]
    for r in rows:
        r["icp"] = 0.0 if r is ref else icp_floor_offset(refxy, np.c_[r["x"], r["y"]])
        r["aligned"] = float(np.nansum((r["y"] + r["icp"]) - ref["y"]))
    print("finished levels (%d slices each): %s" % (full, ["%d%s" % (r["L"], "r" if r["rep"] else "") for r in rows]))
    print("\n  W   sum(raw)   dark   above own dark   ICP shift   ICP-aligned above W=0    peak   slices w/ sat px")
    for r in rows:
        print("%3d%s %9.0f  %6.1f    %9.0f       %+6.2f       %9.0f          %6.0f      %d" % (
            r["L"], "r" if r["rep"] else " ", r["raw"], r["dark"], r["light"], r["icp"], r["aligned"], r["peak"], r["sat"]))
    rep = [r for r in rows if r["rep"]]
    if rep:
        print("\nrepeat of W = 0 against the first: raw %+.0f, dark %+.1f ADU, ICP shift %+.2f ADU, aligned light %+.0f"
              % (rep[0]["raw"] - ref["raw"], rep[0]["dark"] - ref["dark"], rep[0]["icp"], rep[0]["aligned"]))
    sw = [r for r in rows if not r["rep"]]
    fig, ax = plt.subplots(1, 3, figsize=(16, 5.2))
    for a_, key, lab in ((ax[0], "raw", "sum of the trace (ADU x slices)"),
                         (ax[1], "light", "sum above the trace's own dark level"),
                         (ax[2], "aligned", "ICP floor-aligned, above W = 0")):
        Wv = np.array([r["L"] for r in sw])
        V = np.array([r[key] for r in sw])
        o = np.argsort(Wv)
        a_.plot(Wv[o], V[o], "-", color="#2358c8", lw=1, alpha=.5)
        a_.plot(Wv, V, "o", color="#2358c8", ms=7)
        for r in sw:
            if r["sat"] > 0:
                a_.plot(r["L"], r[key], "o", mfc="none", mec="#c62f26", ms=13, mew=2)
        for r in rep:
            a_.plot(r["L"], r[key], "s", mfc="none", mec="#111", ms=10, mew=2)
        if len(sw) > 1:
            lo, hi = min(sw, key=lambda r: r["L"]), max(sw, key=lambda r: r["L"])
            a_.plot([lo["L"], hi["L"]], [lo[key], hi[key]], ":", color="#888", label="straight line between the ends")
        a_.set_xlabel("commanded W")
        a_.set_ylabel(lab)
        a_.grid(alpha=.3)
    ax[0].legend(fontsize=8)
    fig.suptitle("%s: total light against W  (red ring = saturated pixels, black square = repeat of W = 0)" % a.label)
    fig.tight_layout()
    out = os.path.join(ROOT, "w5us_%s_sum_vs_W.png" % a.label)
    fig.savefig(out, dpi=120)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
