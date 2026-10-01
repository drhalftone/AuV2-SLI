#!/usr/bin/env python3
"""A close delay sweep over one short feature of a solid white frame, every frame kept.

    python -u host/spike_sweep.py --port COM5 --t0 3320 --t1 3460 --label blue_spike

white_sweep.py takes the MEDIAN of a few frames per slice, which is right for the LED
blocks but hides anything that changes from frame to frame. The blue flash at about
3370-3400 us after vsync does: in a 5 us sweep its six frames per slice read anywhere
from the dark floor to 400 ADU. So this steps the delay finely across a short window
and writes EVERY frame's reading with the camera's frame counter, so the flash can be
checked for a pattern across frames (every other frame, every fourth, ...) rather
than averaged away.

Written to spike_sweep_<label>.csv (one row per frame) and .png: top, every frame as
a dot with the per-delay median; bottom, delay x frame as an image, frames in the
order they arrived, so a repeating pattern shows as stripes.
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
ap.add_argument("--t0", type=float, default=3320.0, help="first delay, us")
ap.add_argument("--t1", type=float, default=3460.0, help="last delay, us")
ap.add_argument("--step", type=float, default=1.0, help="delay step, us")
ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
ap.add_argument("--frames", type=int, default=48, help="consecutive frames kept per delay")
ap.add_argument("--settle", type=int, default=3)
ap.add_argument("--level", type=int, default=255, help="grey level of the whole screen")
ap.add_argument("--label", default="spike", help="tag for the output files (e.g. the filter)")
a = ap.parse_args()

s = Screen(set_mode_first=True)
buf = s.blank()
buf[:, :] = a.level
s.show(buf, settle=1.5)

ser = serial.Serial(a.port, 115200, timeout=0.05)
time.sleep(0.4)


def R(addr):
    v = rd(ser, addr)
    return 0 if v is None else v


T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
mx = R(0x53) | (R(0x54) << 8)
if (R(0x5D) & 3) != 3 or abs(1e6 / T - 120) > 1 or not (R(0x55) & 0x80):
    sys.exit("ABORT: frame %.1f us genlock 0x%02X" % (T, R(0x5D)))
wr(ser, 0x13, 0x00)
wr(ser, 0x16, 0x00)
wr(ser, R_ROICTL, 0x40)
u = max(1, int(round(a.expo / EXPO_UNIT_US)))
if u > mx:
    sys.exit("ABORT: over the exposure limit")
wr(ser, R_EXPO_LO, u & 0xFF)
wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
expo = u * EXPO_UNIT_US
n = int(round((a.t1 - a.t0) / a.step)) + 1
delays = [a.t0 + i * a.step for i in range(n)]
print("frame %.1f us   exposure %.2f us   %d delays %.1f..%.1f step %.2f   %d frames each"
      % (T, expo, n, delays[0], delays[-1], a.step, a.frames), flush=True)

plt.ion()
fig, ax = plt.subplots(figsize=(12, 5))
dots, = ax.plot([], [], ".", ms=2, color="#1f5fd0", alpha=.35)
med, = ax.plot([], [], lw=1.2, color="k")
ax.set_xlim(delays[0], delays[-1])
ax.set_xlabel("delay after vsync (us)")
ax.set_ylabel("ROI mean, every frame")
ax.grid(alpha=.3)
fig.canvas.manager.window.wm_geometry("+40+40")
fig.tight_layout()
plt.pause(0.1)

out = os.path.join(ROOT, "spike_sweep_%s.csv" % a.label)
fh = open(out, "w", newline="")
w = csv.writer(fh)
w.writerow(["delay_us", "k", "tcnt", "mean", "sat_px"])
grid = np.full((n, a.frames), np.nan)
px, py, mx_, my_ = [], [], [], []
t0 = time.time()
try:
    for i, d in enumerate(delays):
        set_delay(ser, int(round(d / TICK_US)))
        if i % 20 == 0:
            wait_delay(ser, int(round(d / TICK_US)))
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3)
        rows = [r for r in collect(ser, a.frames, timeout=8, sat=True) if r[1] == 256]
        if len(rows) < a.frames // 2:
            sys.exit("ABORT: %d ROI lines at delay %.1f" % (len(rows), d))
        for k, r in enumerate(rows):
            w.writerow([round(d, 2), k, r[3], r[0], r[4]])
            grid[i, k] = r[0]
            px.append(d)
            py.append(r[0])
        mx_.append(d)
        my_.append(float(np.median([r[0] for r in rows])))
        fh.flush()
        if i % 4 == 0:
            dots.set_data(px, py)
            med.set_data(mx_, my_)
            ax.set_ylim(min(py) - 10, max(py) + 20)
            ax.set_title("%s   %d/%d   %.0f s" % (a.label, i + 1, n, time.time() - t0))
            try:
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
            except Exception:
                pass
finally:
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)
    ser.close()
    fh.close()
    s.close()

plt.close(fig)
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, layout="constrained",
                               gridspec_kw={"height_ratios": [1, 1]})
ax1.plot(px, py, ".", ms=2, color="#1f5fd0", alpha=.35, label="every frame")
ax1.plot(mx_, my_, lw=1.2, color="k", label="median")
ax1.set_ylabel("ROI mean (ADU)")
ax1.grid(alpha=.3)
ax1.legend(fontsize=8)
ax1.set_title("%s   %.2f us exposure, %d frames per delay" % (a.label, expo, a.frames))
im = ax2.imshow(grid.T, aspect="auto", origin="lower", cmap="viridis",
                extent=(delays[0] - a.step / 2, delays[-1] + a.step / 2, -.5, a.frames - .5))
ax2.set_ylabel("frame, in arrival order")
ax2.set_xlabel("delay after vsync (us)")
fig.colorbar(im, ax=[ax1, ax2], label="ADU", pad=.01)   # both, or the panels misalign
fig.savefig(os.path.join(ROOT, "spike_sweep_%s.png" % a.label), dpi=120)
floor = np.nanmedian(grid[:3])
print("floor %.0f  median peak %.0f  frame max %.0f   wrote %s"
      % (floor, max(my_), np.nanmax(grid), out), flush=True)
plt.show()
