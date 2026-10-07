#!/usr/bin/env python3
"""A 5 us delay sweep across all five frames of a 1-white/4-black cycle, then a boxcar.

    python -u host/lag_sweep.py COM5 --out lag_sweep_closeup.csv
    python -u host/lag_sweep.py --load lag_sweep_closeup.csv        # re-analyse, no hardware

The FPGA's impulse mode sends a 5-frame cycle, black except one white frame (position
0), and tags every camera line with the frame being transmitted when it was triggered.
So one delay read gives all five positions at once, and stepping the delay across one
frame period fills the whole 5-frame (41.7 ms) timeline:

    t = position * T + delay       (us after the vsync of the frame that carried white)

The signal at each t is the ROI mean at that position minus the MEDIAN of the five
positions at the same delay -- the white frame lights at most two of them, so the
median is a dark reference, and any part of the reading that does not depend on the
picture cancels.

THE LAG. A boxcar exactly one frame period wide slides along the timeline (circularly:
the cycle repeats). Its peak is where ONE frame's worth of exposure collects the most of
the white frame's light; the window's start is the lag, measured from the white frame's
vsync. A projector with no lag peaks with the window starting near 0; one frame late,
near T.

The sweep is written to --out as it goes (every delay x position), so --load can redo
the analysis later without repeating it.
"""
import argparse
import collections
import csv
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import matplotlib                                                   # noqa: E402
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt                                     # noqa: E402

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("port", nargs="?", default="COM6")
ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
ap.add_argument("--step", type=float, default=5.0, help="delay step, us")
ap.add_argument("--frames", type=int, default=20,
                help="camera frames per delay (about a fifth land on each position)")
ap.add_argument("--settle", type=int, default=4)
ap.add_argument("--dmin", type=float, default=0.0,
                help="first delay, us. With --dmax, sweeps only the part of the frame the "
                     "light can land in -- the skipped delays are dark at every position, "
                     "and the boxcar interpolates across them")
ap.add_argument("--dmax", type=float, default=None, help="last delay, us (default: the frame)")
ap.add_argument("--level", type=int, default=255, help="level of the one white frame")
ap.add_argument("--out", default=os.path.join(ROOT, "lag_sweep.csv"))
ap.add_argument("--load", help="analyse this saved sweep instead of measuring")
a = ap.parse_args()

# ---- plot ---------------------------------------------------------------------------
plt.ion()
fig, (ax, ab) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                             gridspec_kw={"height_ratios": [1.4, 1]})
fig.canvas.manager.window.wm_geometry("+40+40")
fig.subplots_adjust(left=0.07, right=0.985, top=0.9, bottom=0.07, hspace=0.12)
hdr = fig.text(0.07, 0.985, "", va="top", ha="left", family="monospace", fontsize=9.5)
line, = ax.plot([], [], lw=.7, color="#1f5fd0")
ax.set_ylabel("ROI mean - median of the 5 positions (ADU)")
ax.grid(alpha=.3)
ab.set_ylabel("one-frame boxcar sum (ADU x slices)")
ab.set_xlabel("time after the vsync of the frame that carried white (us)")
ab.grid(alpha=.3)


def ui(msg=None):
    if msg:
        hdr.set_text(msg)
    try:
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
    except Exception:
        pass


def frame_marks(T):
    for axx in (ax, ab):
        for k in range(1, 5):
            axx.axvline(k * T, color="#888", lw=.8)
        axx.set_xlim(0, 5 * T)
    for k in range(5):
        ax.text((k + .5) * T, 1.01, "frame %d%s" % (k, "  (white sent)" if k == 0 else ""),
                transform=ax.get_xaxis_transform(), ha="center", va="bottom", fontsize=9,
                color="#555")


# ---- measure, or load ----------------------------------------------------------------
if a.load:
    rows = list(csv.DictReader(open(a.load)))
    T = float(rows[0]["T_us"])
    data = [(float(r["t_us"]), float(r["mean"]), float(r["ref"])) for r in rows]
    src = a.load
    frame_marks(T)
else:
    import serial
    from frame_sweep import (wr, rd, set_delay, wait_delay, collect, EXPO_UNIT_US,  # noqa
                             TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL,
                             R_IMPRGB, R_IMPLVL2, R_IMPRGB2)
    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)

    def R(addr):
        v = rd(ser, addr)
        return 0 if v is None else v

    T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
    mx = R(0x53) | (R(0x54) << 8)
    if not (1000 < T < 60000) or (R(0x5D) & 3) != 3:
        sys.exit("ABORT: frame %.1f us, genlock 0x%02X -- is video running?" % (T, R(0x5D)))
    u = max(1, int(round(a.expo / EXPO_UNIT_US)))
    if u > mx:
        sys.exit("ABORT: over the exposure limit")
    e = u * EXPO_UNIT_US
    lit_px = (a.level << 16) | (a.level << 8) | a.level
    wr(ser, R_IMPCYC, 5)                         # 5-frame cycle, odd frame at position 0
    wr(ser, R_IMPLVL, 0)
    wr(ser, R_IMPRGB, 0x07)
    wr(ser, R_IMPLVL2, a.level & 0xFF)
    wr(ser, R_IMPRGB2, 0x07)
    wr(ser, R_ROICTL, 0xC0)                      # impulse_en | per-frame stream
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    time.sleep(0.4)
    frame_marks(T)
    dmax = T - e - 1.0 if a.dmax is None else min(a.dmax, T - e - 1.0)
    delays = list(np.arange(a.dmin, dmax, a.step))
    out = a.out
    fh = open(out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["T_us", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n", "ref"])
    data = []
    t0 = time.time()
    try:
        for i, d in enumerate(delays):
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if i % 25 == 0:
                wait_delay(ser, want)
            ser.reset_input_buffer()
            collect(ser, a.settle, timeout=4)
            by = collections.defaultdict(list)
            rows = [r for r in collect(ser, a.frames, timeout=6) if r[1] == 256]
            # FRAME NUMBERING. The profiling bitstream counts frames in `cc`; the
            # merged one sends cc = 0 on every line. There, the per-frame stream
            # still delivers one line per frame, so the line's ARRIVAL ORDER is
            # the frame number -- and the anchor check below catches a dropped one.
            use_cc = any((rows[k][3] - rows[k - 1][3]) & 0xFF for k in range(1, len(rows)))
            absns, absn = [], 0
            for k, r in enumerate(rows):
                if k:
                    absn += ((r[3] - rows[k - 1][3]) & 0xFF) if use_cc else 1
                absns.append(absn)
            # THE WHITE FRAME'S TAG. The merged build latches only RED of the
            # transmitted pixel (build_merged.tcl: cam_frame_ft takes 23:16), so
            # white reads FF0000 there and FFFFFF on the profiling bitstream.
            marks = [n for n, r in zip(absns, rows)
                     if r[2] in (lit_px, lit_px & 0xFF0000)]
            # Re-anchor on EVERY white frame, and drop any cycle whose marks are
            # not exactly 5 apart: a missed line then costs one cycle, instead of
            # shifting every later reading onto the wrong position.
            for n, r in zip(absns, rows):
                prev = [m for m in marks if m <= n]
                nxt = [m for m in marks if m > n]
                if not prev or n - prev[-1] > 4:
                    continue
                if nxt and nxt[0] - prev[-1] != 5:
                    continue
                by[n - prev[-1]].append(r[0])
            if len(by) < 5:
                print("  delay %.1f: positions seen %s -- skipped"
                      % (d, sorted((k, len(v)) for k, v in by.items())), flush=True)
                continue
            ref = float(np.median([np.mean(v) for v in by.values()]))
            for k in range(5):
                v = by[k]
                t = k * T + d
                w.writerow([round(T, 2), round(e, 3), round(d, 2), k, round(t, 2),
                            round(float(np.mean(v)), 3), round(float(np.std(v)), 3), len(v),
                            round(ref, 3)])
                data.append((t, float(np.mean(v)), ref))
            if i % 10 == 0:
                fh.flush()
                srt = sorted(data)
                line.set_data([q[0] for q in srt], [q[1] - q[2] for q in srt])
                ax.relim()
                ax.autoscale_view(scalex=False)
                el = time.time() - t0
                ui("5-frame sweep   %.2f us exposure, %.0f us step   delay %d/%d   %.0f s, "
                   "~%.0f s left" % (e, a.step, i + 1, len(delays), el,
                                    el / (i + 1) * (len(delays) - i - 1)))
    finally:
        fh.close()
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
    src = out
    print("wrote %s  (%d readings)" % (out, len(data)), flush=True)

# ---- analyse: signal on a uniform grid, then a one-frame circular boxcar ----------------
data.sort()
t = np.array([q[0] for q in data])
sig = np.array([q[1] - q[2] for q in data])
line.set_data(t, sig)
ax.relim()
ax.autoscale_view(scalex=False)
dt = float(np.median(np.diff(t)))
grid = np.arange(0.0, 5 * T, dt)
g = np.interp(grid, t, sig, period=5 * T)        # fills the small gap at each frame end
n = int(round(T / dt))
ext = np.concatenate([g, g[:n]])
box = np.convolve(ext, np.ones(n), mode="valid")[:len(g)]   # box[i] = sum g[i:i+n]
i_pk = int(np.argmax(box))
start = grid[i_pk]
ab.plot(grid, box, color="#2e9e4f", lw=1.2)
ab.axvline(start, color="#d0453a", lw=1.2)
for axx in (ax, ab):
    axx.axvspan(start, start + T, color="#d0453a", alpha=.08, lw=0)
total = float(g.sum())
frac = float(box[i_pk] / total) if total > 0 else float("nan")
k = int(start // T)
msg = ("boxcar peak: one frame period starting at %.1f us = frame %d +%.1f us  ->  lag %.3f frames\n"
       "that window holds %.1f %% of the cycle's light   (%s)"
       % (start, k, start - k * T, start / T, 100 * frac, os.path.basename(src)))
print(msg, flush=True)
ui(msg)
fig.savefig(os.path.splitext(src)[0] + ".png", dpi=110)
plt.ioff()
plt.show()
