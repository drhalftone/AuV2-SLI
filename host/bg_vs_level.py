#!/usr/bin/env python3
"""Background light versus whole-screen level: the ROI at ~8000 us while W steps 0..255.

    python -u host/bg_vs_level.py --port COM5 --label blue_20in

By 8000 us after vsync every LED block of the frame is over (the last light ends near
6700 us), so what the ROI reads there is the background: light that persists when no
sub-field is lit. This fills the WHOLE screen with grey level W and reads that
background at each W, to see whether and how much it follows the picture.

Each --expo is read at every level, with the exposure placed so it ends at --end (so a
long exposure reaches back from the same point rather than running into the next
frame). Levels are visited in bisection order (0, 255, 128, 64, 192, ...), and level 0
is read again at the end, so drift over the run cannot pass for a dependence on W.
Written to bg_vs_level_<label>.csv (one row per level x exposure) and .png.
"""
import argparse
import csv
import os
import sys
import time

import numpy as np
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import matplotlib                                                   # noqa: E402
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt                                     # noqa: E402
from screen import Screen                                           # noqa: E402
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,    # noqa: E402
                         EXPO_UNIT_US, TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI)

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--port", default="COM6", help="camera serial port")
ap.add_argument("--expo", type=float, nargs="+", default=[5.0, 300.0],
                help="exposures to read at every level, us")
ap.add_argument("--end", type=float, default=8200.0,
                help="every exposure ends here, us after vsync (default 8200: a 5 us "
                     "probe then sits near the report's 8000 us)")
ap.add_argument("--levels", type=int, default=17, help="number of levels 0..255")
ap.add_argument("--frames", type=int, default=48, help="frames averaged per reading")
ap.add_argument("--settle", type=int, default=6)
ap.add_argument("--black-each", action="store_true",
                help="read a black screen right after every level and report the level's "
                     "light as level - black. The long-exposure black level drifts by "
                     "100+ ADU over minutes, so a single black reference is not enough "
                     "for a tone curve.")
ap.add_argument("--label", default="bg", help="tag for the output files (e.g. the filter)")
a = ap.parse_args()


def bisection(n):
    """0, 255, then midpoints, coarse to fine, as n distinct levels."""
    full = sorted({int(round(255 * i / (n - 1))) for i in range(n)})
    order, spans = [full[0], full[-1]], [(0, len(full) - 1)]
    while spans:
        nxt = []
        for lo, hi in spans:
            if hi - lo > 1:
                mid = (lo + hi) // 2
                order.append(full[mid])
                nxt += [(lo, mid), (mid, hi)]
        spans = nxt
    return order


levels = bisection(a.levels) + [0]
s = Screen(set_mode_first=True)
buf = s.blank()

ser = serial.Serial(a.port, 115200, timeout=0.05)
time.sleep(0.4)


def R(addr):
    v = rd(ser, addr)
    return 0 if v is None else v


T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
mx = R(0x53) | (R(0x54) << 8)
if (R(0x5D) & 3) != 3 or abs(1e6 / T - 120) > 1 or not (R(0x55) & 0x80):
    sys.exit("ABORT: frame %.1f us genlock 0x%02X" % (T, R(0x5D)))
if a.end > T:
    sys.exit("ABORT: --end %.0f is past the %.0f us frame" % (a.end, T))
wr(ser, 0x13, 0x00)
wr(ser, 0x16, 0x00)
wr(ser, R_ROICTL, 0x40)

probes = []                                      # (units, exposure us, delay us)
for e in a.expo:
    u = max(1, int(round(e / EXPO_UNIT_US)))
    if u > mx:
        sys.exit("ABORT: %.0f us is over the exposure limit" % e)
    probes.append((u, u * EXPO_UNIT_US, a.end - u * EXPO_UNIT_US))
print("frame %.1f us   %s   %d levels, %d frames each"
      % (T, "   ".join("%.2f us at %.1f" % (x, d) for _, x, d in probes),
         len(levels), a.frames), flush=True)

out = os.path.join(ROOT, "bg_vs_level_%s.csv" % a.label)
fh = open(out, "w", newline="")
w = csv.writer(fh)
w.writerow(["order", "level", "expo_us", "delay_us", "mean", "sd", "sem", "n", "sat_px",
            "black", "black_sem", "light"])
res = {x: [] for _, x, _ in probes}


def read_probe(u, x, d, L):
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    set_delay(ser, int(round(d / TICK_US)))
    wait_delay(ser, int(round(d / TICK_US)))
    ser.reset_input_buffer()
    collect(ser, a.settle, timeout=3)
    rows = [r for r in collect(ser, a.frames, timeout=8, sat=True) if r[1] == 256]
    if len(rows) < a.frames // 2:
        sys.exit("ABORT: %d ROI lines at level %d" % (len(rows), L))
    m = np.array([r[0] for r in rows], float)
    return m.mean(), m.std(ddof=1), m.std(ddof=1) / np.sqrt(len(m)), len(m),         max(r[4] or 0 for r in rows)


# ---- live chart: per exposure, the raw reading (and black) above, the light below ----
plt.ion()
nr = 2 if a.black_each else 1
fig, axs = plt.subplots(nr, len(probes), figsize=(6.5 * len(probes), 4.2 * nr),
                        squeeze=False, sharex=True)
fig.canvas.manager.window.wm_geometry("+40+40")
art = {}
for j, (_, x, d) in enumerate(probes):
    ax = axs[0][j]
    lv, = ax.plot([], [], "o", color="#1f5fd0", ms=4, label="level W")
    sv, = ax.plot([], [], "o", mfc="none", mec="#d0453a", ms=6, label="saturated pixels")
    bk, = ax.plot([], [], ".", color="#666", ms=4, label="black, read after each level")
    ax.axhline(1023, color="#9a6614", ls="--", lw=.8)
    ax.set_title("%.2f us exposure at %.0f..%.0f us" % (x, d, d + x), fontsize=10)
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=.3)
    ax.legend(fontsize=8, loc="upper left")
    li = None
    if a.black_each:
        ax2 = axs[1][j]
        li, = ax2.plot([], [], "o", color="#2e9e4f", ms=4)
        ax2.set_ylabel("light = level - black (ADU)")
        ax2.grid(alpha=.3)
    axs[-1][j].set_xlabel("whole-screen level W")
    axs[-1][j].set_xlim(-5, 260)
    art[x] = (ax, lv, sv, bk, li)
fig.tight_layout()
plt.pause(0.1)


def redraw():
    for x, (ax, lv, sv, bk, li) in art.items():
        r = res[x]
        lv.set_data([q[1] for q in r], [q[2] for q in r])
        sv.set_data([q[1] for q in r if q[4]], [q[2] for q in r if q[4]])
        if a.black_each:
            bk.set_data([q[1] for q in r], [q[5] for q in r])
            li.set_data([q[1] for q in r], [q[2] - q[5] for q in r])
            li.axes.relim()
            li.axes.autoscale_view()
        ax.relim()
        ax.autoscale_view()
    try:
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
    except Exception:
        pass


t0 = time.time()
try:
    for k, L in enumerate(levels):
        buf[:, :] = L
        s.show(buf, settle=0.5)
        got = [read_probe(u, x, d, L) for u, x, d in probes]
        blk = [(float("nan"), float("nan"))] * len(probes)
        if a.black_each:
            buf[:, :] = 0
            s.show(buf, settle=0.5)
            blk = [read_probe(u, x, d, 0)[0:3:2] for u, x, d in probes]
        line = []
        for (u, x, d), (mm, sd, sem, n, sat), (bm, bsem) in zip(probes, got, blk):
            w.writerow([k, L, round(x, 2), round(d, 1), round(mm, 3), round(sd, 3),
                        round(sem, 3), n, sat, round(bm, 3), round(bsem, 3),
                        round(mm - bm, 3)])
            res[x].append((k, L, mm, sem, sat, bm))
            line.append("%.0f us %.2f +- %.2f%s%s"
                        % (x, mm, sem, ("  black %.2f  light %.2f" % (bm, mm - bm))
                           if a.black_each else "", "  SAT %d" % sat if sat else ""))
        fh.flush()
        print("W %3d   %s   %.0f s" % (L, "   ".join(line), time.time() - t0), flush=True)
        for x, (ax, *_r) in art.items():
            ax.set_title("%.2f us exposure   level %d/%d (W=%d)" % (x, k + 1, len(levels), L),
                         fontsize=10)
        redraw()
finally:
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)
    ser.close()
    fh.close()
    s.close()

for _, x, d in probes:
    art[x][0].set_title("%.2f us exposure at %.0f..%.0f us" % (x, d, d + x), fontsize=10)
fig.suptitle("Whole-screen level sweep, %s" % a.label)
fig.tight_layout()
fig.savefig(os.path.join(ROOT, "bg_vs_level_%s.png" % a.label), dpi=120)
print("wrote %s" % out, flush=True)
plt.ioff()
plt.show()
