#!/usr/bin/env python3
"""One quick delay sweep of a solid white screen: when is light reaching the ROI?

    python -u host/white_sweep.py --label green520_10nm
    python -u host/white_sweep.py --expo 30 --frames 6 --label red10nm

The whole projector is filled with 255. The delay steps by the exposure, so the slices
tile the frame edge to edge (30 us -> 277 slices), and each slice is the MEDIAN of
--frames camera lines. Written to white_sweep_<label>.csv / .png. A solid white field
lights the ROI far more than the hole scene does, so watch `sat`: slices near 1023 are
clipped and their shape is not to be trusted -- shorten --expo.
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
ap.add_argument("--expo", type=float, default=30.0, help="slice exposure, us")
ap.add_argument("--frames", type=int, default=6, help="camera lines per slice (median)")
ap.add_argument("--settle", type=int, default=3)
ap.add_argument("--level", type=int, default=255, help="grey level of the whole screen")
ap.add_argument("--label", default="white", help="tag for the output files (e.g. the filter)")
a = ap.parse_args()

s = Screen(set_mode_first=True)
buf = s.blank()
buf[:, :] = a.level
s.show(buf, settle=1.5)

ser = serial.Serial("COM6", 115200, timeout=0.05)
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
delays = [i * expo for i in range(int(T // expo))]
print("frame %.1f us   slice %.2f us   %d slices   screen level %d   median of %d"
      % (T, expo, len(delays), a.level, a.frames), flush=True)

plt.ion()
fig, ax = plt.subplots(figsize=(12, 5))
line, = ax.plot([], [], lw=1.1, color="#1f5fd0")
ax.axhline(1023, color="#9a6614", ls="--", lw=.8)
ax.set_xlim(0, T)
ax.set_ylim(0, 1080)
ax.set_xlabel("delay after vsync (us)")
ax.set_ylabel("ROI median per %.0f us slice" % expo)
ax.grid(alpha=.3)
fig.canvas.manager.window.wm_geometry("+40+40")
fig.tight_layout()
plt.pause(0.1)

out = os.path.join(ROOT, "white_sweep_%s.csv" % a.label)
fh = open(out, "w", newline="")
w = csv.writer(fh)
w.writerow(["delay_us", "median", "min", "max", "sat_px"])
xs, ys = [], []
t0 = time.time()
try:
    for i, d in enumerate(delays):
        set_delay(ser, int(round(d / TICK_US)))
        if i % 40 == 0:
            wait_delay(ser, int(round(d / TICK_US)))
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3)
        rows = [r for r in collect(ser, a.frames, timeout=5, sat=True) if r[1] == 256]
        if len(rows) < 3:
            sys.exit("ABORT: %d ROI lines at delay %.0f" % (len(rows), d))
        m = [r[0] for r in rows]
        y = float(np.median(m))
        sat = max(r[4] for r in rows)
        xs.append(d)
        ys.append(y)
        w.writerow([round(d, 2), round(y, 1), min(m), max(m), sat])
        fh.flush()
        if i % 6 == 0:
            line.set_data(xs, ys)
            ax.set_title("solid white, %.0f us slices   %d/%d   peak so far %.0f   sat px %d   %.0f s"
                         % (expo, i + 1, len(delays), max(ys), sat, time.time() - t0))
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

y = np.array(ys)
line.set_data(xs, ys)
ax.set_title("solid white, %.0f us slices, %s   floor %.0f  peak %.0f  sum %.0f"
             % (expo, a.label, np.percentile(y, 5), y.max(), y.sum()))
fig.savefig(os.path.join(ROOT, "white_sweep_%s.png" % a.label), dpi=120)
print("floor %.0f  peak %.0f  slices above 1000: %d of %d   wrote %s"
      % (np.percentile(y, 5), y.max(), int((y > 1000).sum()), len(y), out), flush=True)
plt.ioff()
plt.show()
