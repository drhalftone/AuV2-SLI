#!/usr/bin/env python3
"""Predicted versus measured light for many exposure windows, from one slice sweep.

    python -u host/predict_check.py --port COM5 --sweep white_sweep_closeup_2ND_200us.csv

A white_sweep.py CSV tiles the frame with slices whose exposure equals their step, so
(median - floor) / step is the light RATE through each slice. Integrating that rate over
any window [delay, delay + exposure] predicts how much light a single exposure of that
window collects. This checks the prediction: every window is read on a full-white screen
and on a black one at the same delay and exposure, and the measured light is
white - black. Subtracting black at the SAME exposure matters: a long exposure on black
reads well above the short-slice floor (the black frames' own glow plus dark level).

Windows with any saturated ROI pixel are drawn hollow -- their mean is pulled down by
the clipped pixels, so they are not a fair test.

Written to predict_check_<label>.csv and .png; a live chart fills in as it goes.
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

# (delay us, exposure us): short to long, spread over the frame's LED blocks and gaps
WINDOWS = [(500, 100), (1700, 200), (2700, 300), (4200, 400), (5600, 250),
           (6300, 400), (600, 800), (2000, 1000), (3700, 600), (4500, 1200),
           (5800, 900), (100, 1500), (1500, 2000), (3000, 2500), (4000, 3000),
           (550, 3480), (4090, 3480), (200, 4000), (2500, 4500), (1000, 5500)]

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--port", default="COM6", help="camera serial port")
ap.add_argument("--sweep", required=True, help="white_sweep.py CSV to predict from")
ap.add_argument("--frames", type=int, default=24, help="frames averaged per reading")
ap.add_argument("--settle", type=int, default=6)
ap.add_argument("--label", default="check", help="tag for the output files")
a = ap.parse_args()

# ---- the prediction: integrate the sweep's light rate over a window -----------
sw_rows = list(csv.DictReader(open(a.sweep)))
sd_ = np.array([float(x["delay_us"]) for x in sw_rows])
sy = np.array([float(x["median"]) for x in sw_rows])
step = float(np.median(np.diff(sd_)))
floor = float(np.median(sy[(sd_ > 7000) & (sd_ < 7900)]))
srate = (sy - floor) / step                        # ADU per us through each slice


def predict(d, e):
    lo, hi = d, d + e
    ov = np.clip(np.minimum(sd_ + step, hi) - np.maximum(sd_, lo), 0, None)
    return float((srate * ov).sum())


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
wr(ser, 0x13, 0x00)
wr(ser, 0x16, 0x00)
wr(ser, R_ROICTL, 0x40)


def read(d, e):
    u = max(1, int(round(e / EXPO_UNIT_US)))
    if u > mx:
        sys.exit("ABORT: %.0f us is over the exposure limit" % e)
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    set_delay(ser, int(round(d / TICK_US)))
    wait_delay(ser, int(round(d / TICK_US)))
    ser.reset_input_buffer()
    collect(ser, a.settle, timeout=4)
    rows = [r for r in collect(ser, a.frames, timeout=8, sat=True) if r[1] == 256]
    if len(rows) < a.frames // 2:
        sys.exit("ABORT: %d ROI lines at delay %.0f expo %.0f" % (len(rows), d, e))
    m = np.array([r[0] for r in rows], float)
    return m.mean(), m.std(), max(r[4] or 0 for r in rows), u * EXPO_UNIT_US


# ---- live chart ------------------------------------------------------------------
plt.ion()
fig, ax = plt.subplots(figsize=(7.5, 7.5))
fig.canvas.manager.window.wm_geometry("+40+40")
lim = 1.1 * max(predict(d, e) for d, e in WINDOWS) + 20
ax.plot([0, lim], [0, lim], color="#888", lw=1, ls="--", label="measured = predicted")
ax.set_xlim(0, lim)
ax.set_ylim(0, lim)
ax.set_aspect("equal")
ax.set_xlabel("predicted light, ADU (from %s)" % os.path.basename(a.sweep))
ax.set_ylabel("measured light, ADU (white - black, same window)")
ax.grid(alpha=.3)
ok_pts, = ax.plot([], [], "o", color="#1f5fd0", ms=7, label="no saturated pixels")
sat_pts, = ax.plot([], [], "o", mfc="none", mec="#d0453a", mew=1.5, ms=7,
                   label="some ROI pixels saturated")
ax.legend(fontsize=8, loc="upper left")
fig.tight_layout()
plt.pause(0.1)

out = os.path.join(ROOT, "predict_check_%s.csv" % a.label)
fh = open(out, "w", newline="")
w = csv.writer(fh)
w.writerow(["delay_us", "expo_us", "predicted", "white", "white_sd", "white_sat",
            "black", "black_sd", "measured", "error_pct"])
res = []
try:
    for i, (d, e) in enumerate(WINDOWS):
        buf[:, :] = 255
        s.show(buf, settle=0.4)
        wm, wsd, wsat, ee = read(d, e)
        buf[:, :] = 0
        s.show(buf, settle=0.4)
        bm, bsd, _, _ = read(d, e)
        p = predict(d, ee)
        meas = wm - bm
        err = 100.0 * (meas - p) / p if p > 1 else float("nan")
        res.append((d, ee, p, meas, wsat))
        w.writerow([d, round(ee, 2), round(p, 1), round(wm, 2), round(wsd, 2), wsat,
                    round(bm, 2), round(bsd, 2), round(meas, 1), round(err, 1)])
        fh.flush()
        print("%2d  %4d+%4.0f us  predicted %6.1f  measured %6.1f  (%+5.1f %%)  white %.0f black %.0f%s"
              % (i + 1, d, ee, p, meas, err, wm, bm, "  SAT %d px" % wsat if wsat else ""),
              flush=True)
        good = [q for q in res if q[4] == 0]
        bad = [q for q in res if q[4] > 0]
        ok_pts.set_data([q[2] for q in good], [q[3] for q in good])
        sat_pts.set_data([q[2] for q in bad], [q[3] for q in bad])
        ax.set_title("%d / %d windows" % (i + 1, len(WINDOWS)), fontsize=10)
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

for d, ee, p, meas, wsat in res:
    ax.annotate("%.0f" % ee, (p, meas), xytext=(4, -10), textcoords="offset points",
                fontsize=7, color="#555")
good = [q for q in res if q[4] == 0]
if good:
    e_ = np.array([100 * (q[3] - q[2]) / q[2] for q in good if q[2] > 1])
    ax.set_title("%d windows; unsaturated: median error %+.1f %%, rms %.1f %%  (labels: exposure us)"
                 % (len(res), np.median(e_), np.sqrt(np.mean(e_ ** 2))), fontsize=9)
fig.savefig(os.path.join(ROOT, "predict_check_%s.png" % a.label), dpi=120)
print("wrote %s" % out, flush=True)
plt.ioff()
plt.show()
