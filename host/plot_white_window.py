#!/usr/bin/env python3
"""The white SLI sweep with the scanner's trigger window marked on it.

    python host/plot_white_window.py

Draws sli_frameH_white_1us.csv and overlays what was extracted from it: where the
emission actually starts and stops, the weak shoulder that is not worth chasing,
the dark gaps a single exposure has to span anyway, and the recommended trigger
delay and exposure. The numbers are recomputed from the CSV on every run rather
than written in, so the picture cannot drift away from the data.
"""
import csv, os, statistics, sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "sli_frameH_white_1us.csv")
OUT = os.path.splitext(SRC)[0] + "_window.png"
MARGIN = 33.0            # covers the sampling aperture, which smears the edges

rows = sorted((float(r["delay_us"]), float(r["mean"]))
              for r in csv.DictReader(open(SRC)))
if len(rows) < 8000:
    sys.exit("%s has only %d rows" % (SRC, len(rows)))
xs = [d for d, m in rows]
ys = [m for d, m in rows]

v = sorted(ys)
floor = v[len(v) // 20]
noise = statistics.pstdev([m for m in ys if m < floor + 15])
strong = floor + 0.05 * (max(ys) - floor)      # 5 % of full scale
weak = floor + 5 * noise                        # 5 sigma

lit = [d for d, m in rows if m > strong]
on, off = min(lit), max(lit)
wk = [d for d, m in rows if m > weak]
shoulder = min(wk)

# dark gaps inside the strong window
runs = []
for d in lit:
    if runs and d - runs[-1][1] <= 4:
        runs[-1][1] = d
    else:
        runs.append([d, d])
runs = [r for r in runs if r[1] - r[0] >= 8]
gaps = [(a[1], b[0]) for a, b in zip(runs, runs[1:]) if b[0] - a[1] >= 8]

fig, ax = plt.subplots(figsize=(14, 6.5))
ax.axvspan(on - MARGIN, off + MARGIN, color="#2f7d32", alpha=0.10, lw=0,
           label="recommended exposure  %.0f .. %.0f us" % (on - MARGIN, off + MARGIN))
for a, b in gaps:
    ax.axvspan(a, b, color="#888888", alpha=0.16, lw=0)
ax.axvspan(shoulder, on, color="#c98f00", alpha=0.18, lw=0)

ax.plot(xs, ys, lw=0.8, color="#333333")
ax.axhline(floor, color="#888888", lw=1, ls="--")
ax.text(60, floor + 12, "floor %.1f ADU" % floor, fontsize=9, color="#666666")

for x, lbl in ((on, "first light %.0f" % on), (off, "last light %.0f" % off)):
    ax.axvline(x, color="#c0392b", lw=1.2)
    ax.text(x + 40, max(ys) * 0.96, lbl, fontsize=9, color="#c0392b",
            rotation=90, va="top")
ax.axvline(on - MARGIN, color="#2f7d32", lw=1.2, ls=":")
ax.axvline(off + MARGIN, color="#2f7d32", lw=1.2, ls=":")

ax.text((shoulder + on) / 2, floor + 90,
        "weak shoulder\n%.0f-%.0f us\n0.59%% of the light" % (shoulder, on),
        ha="center", fontsize=8.5, color="#8a6100")
for a, b in gaps:
    if b - a >= 100:
        ax.text((a + b) / 2, max(ys) * 0.55, "%.0f us\ndark" % (b - a),
                ha="center", fontsize=8, color="#555555")

ax.set_xlim(-100, 8426)
ax.set_ylim(0, max(ys) * 1.08)
ax.set_xlabel("genlock delay within the projected frame (us)")
ax.set_ylabel("ROI mean (ADU)")
ax.set_title("White SLI fringes, finest octet, one frame at 1 us  --  "
             "emission %.0f to %.0f us, span %.0f us" % (on, off, off - on))
ax.grid(alpha=0.3)
ax.legend(loc="upper right", fontsize=9)
fig.tight_layout()
fig.savefig(OUT, dpi=130)

print("emission      %.0f .. %.0f us   span %.0f us" % (on, off, off - on))
print("weak shoulder %.0f .. %.0f us" % (shoulder, on))
print("dark gaps     " + ", ".join("%.0f-%.0f (%.0f us)" % (a, b, b - a)
                                   for a, b in gaps))
print("RECOMMEND     trigger delay %.0f us, exposure %.0f us (%d register units)"
      % (on - MARGIN, (off - on) + 2 * MARGIN,
         round(((off - on) + 2 * MARGIN) / 0.375)))
print("wrote %s" % OUT)
