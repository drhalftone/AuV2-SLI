#!/usr/bin/env python3
"""Find the DLP's on/off step edges in 5 us sweeps, and the frame start.

    python host/dlp_edges.py --one lag_sweep_closeup_5us.csv --all roi_white_sweep_closeup_5us.csv

EXPLORATORY -- see the caveats in ml750st_report.html ("Step edges and the frame
start"). Two sweeps over the same one-frame window (lag_sweep.py's boxcar window, padded
by roi_white_sweep.py --pad):

    --one   lag_sweep.py CSV: one white frame among four black (raw ROI mean used)
    --all   roi_white_sweep.py CSV: every frame white

For each, the step-to-step change between consecutive readings is compared with the
noise (1.4826 x MAD of the steps); a step over max(--k x noise, --floor ADU) is a
candidate, and consecutive candidates of the same sign merge into one edge, timed where
half its total change is reached (interpolated). Edges of the two sweeps within --pair
us of each other and of the same sign are paired. The frame start is the first ON edge
of each frame in --win (us into the frame). Nothing here is a fit; the edge list
depends on --k and --floor.
"""
import argparse
import csv

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                     # noqa: E402

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--one", required=True)
ap.add_argument("--all", required=True)
ap.add_argument("--k", type=float, default=8.0, help="threshold in noise units")
ap.add_argument("--floor", type=float, default=4.0, help="threshold floor, ADU")
ap.add_argument("--pair", type=float, default=8.0, help="pairing tolerance, us")
ap.add_argument("--win", type=float, nargs=2, default=[500.0, 650.0],
                help="frame-start search window, us into the frame")
ap.add_argument("--png", default="dlp_transitions_closeup.png")
a = ap.parse_args()

ar = list(csv.DictReader(open(a.all)))
T = float(ar[0]["T_us"])
start = float(ar[0]["start_us"])
aw = sorted((float(r["t_us"]), float(r["mean"])) for r in ar)
lo, hi = aw[0][0], aw[-1][0]
ow = sorted((float(r["t_us"]), float(r["mean"])) for r in csv.DictReader(open(a.one))
            if lo <= float(r["t_us"]) <= hi)


def edges(pts):
    t = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    d = np.diff(y)
    sig = 1.4826 * np.median(np.abs(d - np.median(d)))
    thr = max(a.k * sig, a.floor)
    cand = np.abs(d) > thr
    out, i = [], 0
    while i < len(d):
        if not cand[i]:
            i += 1
            continue
        s = np.sign(d[i])
        j = i
        while j + 1 < len(d) and cand[j + 1] and np.sign(d[j + 1]) == s:
            j += 1
        seg = d[i:j + 1]
        tot = seg.sum()
        cum = np.cumsum(seg)
        k = int(np.argmax(np.abs(cum) >= abs(tot) / 2))
        prev = cum[k - 1] if k > 0 else 0.0
        f = (tot / 2 - prev) / seg[k] if seg[k] else 0.0
        out.append((t[i + k] + f * (t[i + k + 1] - t[i + k]), "ON" if s > 0 else "OFF", tot))
        i = j + 1
    return t, y, out, sig, thr


ta, ya, ea, sa, tha = edges(aw)
to, yo, eo, so, tho = edges(ow)
print("all white: noise %.2f ADU/step, threshold %.1f -> %d edges" % (sa, tha, len(ea)))
print("one white: noise %.2f ADU/step, threshold %.1f -> %d edges" % (so, tho, len(eo)))

rows, used = [], set()
for te, dr, tot in ea:
    best = None
    for j, (t2, d2, _) in enumerate(eo):
        if j not in used and d2 == dr and abs(t2 - te) < a.pair and \
                (best is None or abs(t2 - te) < abs(eo[best][0] - te)):
            best = j
    if best is None:
        rows.append((te, dr, tot, None, None))
    else:
        used.add(best)
        rows.append((te, dr, tot, eo[best][0], eo[best][2]))
rows += [(t2, d2, None, t2, tot2) for j, (t2, d2, tot2) in enumerate(eo) if j not in used]
rows.sort(key=lambda r: r[0])
print("\n  dir  frame | all white: us into frame, step | one white: us into frame, step | diff")
for te, dr, tot, t2, tot2 in rows:
    k = int(te // T)
    aa = "%7.1f %+7.1f" % (te - k * T, tot) if tot is not None else "%7s %7s" % ("-", "-")
    bb = "%7.1f %+7.1f" % (t2 - int(t2 // T) * T, tot2) if t2 is not None else "%7s %7s" % ("-", "-")
    df = "%+5.1f" % (t2 - te) if tot is not None and t2 is not None else ""
    print("  %-3s  %d     | %s | %s | %s" % (dr, k, aa, bb, df))

starts = [e for e in ea if e[1] == "ON" and a.win[0] < e[0] % T < a.win[1]]
print("\nframe starts (all white): %s" % ", ".join("frame %d +%.1f us" % (int(e[0] // T), e[0] % T)
                                                  for e in starts))
if len(starts) > 1:
    print("spacing %.1f us (frame period %.1f)" % (starts[1][0] - starts[0][0], T))

fig, axs = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
for ax, (t, y, E, nm, c) in zip(axs, [(ta, ya, ea, "all frames white", "#d0453a"),
                                      (to, yo, eo, "one white frame among four black", "#1f5fd0")]):
    ax.plot(t, y, lw=.7, color=c)
    for te, dr, _ in E:
        ax.axvline(te, color="#2e9e4f" if dr == "ON" else "#7a4b00", lw=.7, alpha=.7)
    for e in starts:
        ax.axvline(e[0], color="k", lw=2, alpha=.5)
    ax.axvspan(start, start + T, color="#2e9e4f", alpha=.06, lw=0)
    ax.set_ylabel("ROI mean (ADU)")
    ax.set_title("%s: %d transitions (green ON, brown OFF; thick black = frame start)"
                 % (nm, len(E)), fontsize=10)
    ax.grid(alpha=.3)
axs[1].set_xlabel("time after the vsync of the frame that carried white (us)")
axs[1].set_xlim(lo, hi)
fig.tight_layout()
fig.savefig(a.png, dpi=110)
print("wrote %s" % a.png)
