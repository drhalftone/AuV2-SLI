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

THE TAG LATCHES AT THE FIRST ACTIVE PIXEL, NOT AT VSYNC. A trigger in the first
--active-us after vsync (back porch: 33 lines = 432 us at 800x600@120) carries the
PREVIOUS frame's tag, so those readings are moved one position later. Without this the
first ~432 us of every frame shows the next frame's light (2026-10-14: the light jumped
from position 0 to 1 between delays 430 and 435 us at an unchanged 627 ADU). The CSV
records the correction it used in `tag_fix_us`; a CSV without that column is corrected
on --load.

PREFLIGHT. Before measuring it puts the projector display at 800x600@120 and turns
genlock on over the Ft+ (opcode 7) -- both are lost when the board or the display
restarts, and the sweep cannot run without them. With no port given and exactly one
serial port present, that port is used.

The sweep is written to --out as it goes (every delay x position), so --load can redo
the analysis later without repeating it.
"""
import argparse
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
ap.add_argument("port", nargs="?", default=None,
                help="camera serial port (default: the only one present, else COM6)")
ap.add_argument("--display", default=None,
                help=r"projector display, e.g. \\.\DISPLAY5 (default: the one non-primary display)")
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
ap.add_argument("--bg", type=int, default=0,
                help="level of the other four frames. ALWAYS WHITE: --bg 254 -- frame 0 stays "
                     "255 so its tag still anchors the cycle. Then nothing is dark at every "
                     "position, so the raw ROI mean is plotted (ref = 0) and there is no lag")
ap.add_argument("--active-us", type=float, default=432.5,
                help="vsync -> first active pixel, us (800x600@120: 33 lines = 432 us); "
                     "readings at smaller delays carry the previous frame's tag")
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
ax.set_ylabel("ROI mean (ADU)" if a.bg else "ROI mean - median of the 5 positions (ADU)")
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
    fix = 0.0 if "tag_fix_us" in rows[0] else a.active_us    # old CSV: not yet corrected
    data = [(((float(r["t_us"]) + (T if float(r["delay_us"]) < fix else 0.0)) % (5 * T)),
             float(r["mean"]), float(r["ref"])) for r in rows]
    src = a.load
    frame_marks(T)
else:
    import lag_core as lc
    from serial.tools import list_ports
    if a.port is None:
        ports = [p.device for p in list_ports.comports()]
        a.port = ports[0] if len(ports) == 1 else "COM6"
    disp = a.display or lc.OFFLINE
    print("port %s, display %s" % (a.port, disp), flush=True)
    try:
        ser, T, mx = lc.preflight(a.port, disp)
        e = lc.start_impulse(ser, a.level, a.bg, a.expo, mx)
    except RuntimeError as ex:
        sys.exit("ABORT: %s" % ex)
    frame_marks(T)
    delays = lc.delays_for(T, e, a.step, a.dmin, a.dmax)
    out = a.out
    data = []
    t0 = time.time()
    try:
        for i, n, d, means, ref, _ in lc.sweep(ser, T, e, delays, out, a.level, a.bg, a.frames,
                                            a.settle, a.active_us):
            data += [(k * T + d, means[k], ref) for k in range(5)]
            if i % 10 == 0:
                srt = sorted(data)
                line.set_data([q[0] for q in srt], [q[1] - q[2] for q in srt])
                ax.relim()
                ax.autoscale_view(scalex=False)
                el = time.time() - t0
                ui(("always white (bg %d)  " % a.bg if a.bg else "") +
                   "5-frame sweep   %.2f us exposure, %.0f us step   delay %d/%d   %.0f s, "
                   "~%.0f s left" % (e, a.step, i + 1, n, el, el / (i + 1) * (n - i - 1)))
    finally:
        lc.stop_impulse(ser)
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
if not any(q[2] for q in data):                  # always white: no dark reference, no lag
    ax.set_ylabel("ROI mean (ADU)")
    msg = "always white: raw ROI mean across the 5-frame cycle   (%s)" % os.path.basename(src)
    print(msg, flush=True)
    ui(msg)
    fig.savefig(os.path.splitext(src)[0] + ".png", dpi=110)
    plt.ioff()
    plt.show()
    sys.exit(0)
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
