#!/usr/bin/env python3
"""Live strip chart of the ROI on an all-white screen: the last 300 camera frames.

    python -u host/live_white.py --port COM5
    python -u host/live_white.py --port COM5 --delay 540 --expo 6175 --frames 300

For setting up the rig by hand -- moving the camera toward or away from the projector,
adding or removing ND -- while watching what one exposure collects. The whole screen is
filled with --level (255), one exposure of --expo at --delay is taken every camera frame
(the default is the full emission window, 540 us + 6175 us, so the reading is the light
of one whole frame), and the last --frames readings scroll past:

    top     ROI mean, with the 1023 clip line and the --target band
    bottom  saturated ROI pixels out of 256 -- the mean can sit under 1023 while the
            brightest pixels already clip, so this is the number that says "too close"

The big readout shows the newest frame. Runs until the window is closed; nothing is
written to disk.
"""
import argparse
import collections
import os
import sys
import time

import numpy as np
import serial

HERE = os.path.dirname(os.path.abspath(__file__))
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
ap.add_argument("--delay", type=float, default=540.0, help="genlock delay, us")
ap.add_argument("--expo", type=float, default=6175.0, help="exposure, us")
ap.add_argument("--level", type=int, default=255, help="grey level of the whole screen")
ap.add_argument("--frames", type=int, default=300, help="frames kept on the chart")
ap.add_argument("--target", type=float, nargs=2, default=[600.0, 800.0],
                metavar=("LO", "HI"), help="shaded band to aim the mean at, ADU")
a = ap.parse_args()

s = Screen(set_mode_first=True)
buf = s.blank()
buf[:, :] = a.level
s.show(buf, settle=1.0)

ser = serial.Serial(a.port, 115200, timeout=0.05)
time.sleep(0.4)


def R(addr):
    v = rd(ser, addr)
    return 0 if v is None else v


T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
mx = R(0x53) | (R(0x54) << 8)
if (R(0x5D) & 3) != 3 or not (R(0x55) & 0x80):
    sys.exit("ABORT: frame %.1f us genlock 0x%02X -- is video running?" % (T, R(0x5D)))
u = max(1, int(round(a.expo / EXPO_UNIT_US)))
if u > mx:
    sys.exit("ABORT: %.0f us is over the %.0f us exposure limit" % (a.expo, mx * EXPO_UNIT_US))
if a.delay + u * EXPO_UNIT_US > T:
    sys.exit("ABORT: delay + exposure runs past the %.0f us frame" % T)
wr(ser, 0x13, 0x00)
wr(ser, 0x16, 0x00)
wr(ser, R_ROICTL, 0x40)
wr(ser, R_EXPO_LO, u & 0xFF)
wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
set_delay(ser, int(round(a.delay / TICK_US)))
wait_delay(ser, int(round(a.delay / TICK_US)))
expo = u * EXPO_UNIT_US

plt.ion()
fig, (ax, az) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
fig.canvas.manager.window.wm_geometry("+40+40")
fig.subplots_adjust(left=0.07, right=0.98, top=0.86, bottom=0.08, hspace=0.12)
ax.axhspan(a.target[0], a.target[1], color="#2e9e4f", alpha=.12, lw=0)
ax.axhline(1023, color="#d0453a", ls="--", lw=1)
ax.text(0.005, 1023, " clip 1023", transform=ax.get_yaxis_transform(), color="#d0453a",
        fontsize=8, va="bottom")
ax.set_ylim(0, 1080)
ax.set_xlim(0, a.frames)
ax.set_ylabel("ROI mean (ADU)")
ax.grid(alpha=.3)
line, = ax.plot([], [], lw=1.2, color="#1f5fd0")
az.set_ylim(-5, 261)
az.set_ylabel("saturated px")
az.set_xlabel("frame (newest on the right)")
az.grid(alpha=.3)
sline, = az.plot([], [], lw=1.2, color="#d0453a")
big = fig.text(0.07, 0.975, "", va="top", ha="left", fontsize=26, family="monospace")
sub = fig.text(0.07, 0.905, "", va="top", ha="left", fontsize=9.5, family="monospace",
               color="#555")
plt.pause(0.1)

means = collections.deque(maxlen=a.frames)
sats = collections.deque(maxlen=a.frames)
t0 = time.time()
n = 0
try:
    while plt.fignum_exists(fig.number):
        for r in collect(ser, 4, timeout=1.0, sat=True):
            if r[1] != 256:
                continue
            means.append(r[0])
            sats.append(r[4] or 0)
            n += 1
        if not means:
            big.set_text("no frames from the camera")
            plt.pause(0.05)
            continue
        x = np.arange(len(means))
        line.set_data(x, list(means))
        sline.set_data(x, list(sats))
        m = np.array(means, float)
        cur, cs = means[-1], sats[-1]
        state = ("SATURATING" if cs else ("in band" if a.target[0] <= cur <= a.target[1]
                                          else ("too dim" if cur < a.target[0] else "bright")))
        big.set_text("%4d ADU   %3d px saturated   %s" % (cur, cs, state))
        big.set_color("#d0453a" if cs else ("#2e9e4f" if state == "in band" else "#222"))
        sub.set_text("last %d frames: mean %.1f  sd %.1f  min %d  max %d  saturated in %d   |  "
                     "W=%d, exposure %.0f us at %.0f us, frame %.1f us   |  %d frames, %.0f s"
                     % (len(m), m.mean(), m.std(), m.min(), m.max(),
                        int(np.count_nonzero(np.array(sats))), a.level, expo, a.delay, T,
                        n, time.time() - t0))
        try:
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
        except Exception:
            break
finally:
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)
    ser.close()
    s.close()
