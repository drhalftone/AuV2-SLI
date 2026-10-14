#!/usr/bin/env python3
"""Overlay saved lag_sweep.py runs on one 5-frame timeline, raw ROI mean.

    python host/plot_lag_overlay.py lag_sweep_alwayswhite_2026-10-14.csv:WWWWW \
        lag_sweep_wkkkk_2026-10-14.csv:WKKKK --out lag_overlay_2026-10-14.png

Each argument is CSV[:label]. The RAW mean is plotted (not mean - ref), because an
always-white run has no dark reference and the point is to compare absolute levels.
CSVs written before lag_sweep corrected the vblank tag are corrected here (--active-us).
"""
import argparse
import csv
import os

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt                                     # noqa: E402
import numpy as np                                                  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("runs", nargs="+", help="CSV[:label]")
ap.add_argument("--out", help="PNG to write")
ap.add_argument("--active-us", type=float, default=432.5,
                help="vsync -> first active pixel, us (800x600@120: 432). A trigger before it "
                     "is tagged with the PREVIOUS frame, so its reading is moved one frame "
                     "later. Skipped for a CSV that has a tag_fix_us column (already fixed)")
a = ap.parse_args()

fig, ax = plt.subplots(figsize=(14, 6))
fig.subplots_adjust(left=0.06, right=0.985, top=0.9, bottom=0.09)
T = None
for spec in a.runs:
    path, _, label = spec.partition(":")
    rows = list(csv.DictReader(open(path)))
    T = float(rows[0]["T_us"])
    fix = 0.0 if "tag_fix_us" in rows[0] else a.active_us
    d = np.array(sorted((((float(r["t_us"]) + (T if float(r["delay_us"]) < fix else 0))
                          % (5 * T)), float(r["mean"])) for r in rows))
    ax.plot(d[:, 0], d[:, 1], lw=.7, label=label or os.path.basename(path))
for k in range(1, 5):
    ax.axvline(k * T, color="#888", lw=.8)
for k in range(5):
    ax.text((k + .5) * T, 1.01, "frame %d" % k, transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=9, color="#555")
ax.set_xlim(0, 5 * T)
ax.set_xlabel("time after the vsync of frame 0 (us)")
ax.set_ylabel("ROI mean (ADU)")
ax.grid(alpha=.3)
ax.legend(loc="upper right")
fig.suptitle("5-frame delay sweep, %.1f us frame" % T, fontsize=11)
if a.out:
    fig.savefig(a.out, dpi=110)
    print("wrote", a.out)
plt.show()
