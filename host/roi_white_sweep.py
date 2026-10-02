#!/usr/bin/env python3
"""5 us sweep of ALL-WHITE frames across one frame-period window, against one white frame.

    python -u host/roi_white_sweep.py COM5 --ref lag_sweep_closeup_5us.csv
    python -u host/roi_white_sweep.py --load roi_white_sweep.csv --ref lag_sweep_closeup_5us.csv

lag_sweep.py finds, with ONE white frame among four black, the one-frame-period window
that collects most of that frame's light (its boxcar peak). This sweeps the same window
with EVERY frame white: the FPGA's impulse mode is set to white for all five positions,
so every frame is identical and stepping the delay across one frame period covers the
whole window. Readings are drawn in window order -- from the window's start, wrapping
past the next vsync -- with the single-white-frame sweep over the same window on top,
both as raw ROI means. The gap between them is what the neighbouring white frames add.

--start defaults to the --ref sweep's boxcar peak. Written to --out as it goes.
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
ap.add_argument("port", nargs="?", default="COM6")
ap.add_argument("--ref", help="lag_sweep.py CSV: one white frame among four black")
ap.add_argument("--start", type=float, default=None,
                help="window start on the 5-frame timeline, us (default: --ref's boxcar peak)")
ap.add_argument("--pad", type=float, default=0.1,
                help="also sweep this fraction of a frame before and after the window. With "
                     "every frame white the padding repeats delays already in the window; "
                     "they are MEASURED again, not copied, so the overlap shows repeatability")
ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
ap.add_argument("--step", type=float, default=5.0, help="delay step, us")
ap.add_argument("--frames", type=int, default=8, help="camera frames per delay")
ap.add_argument("--settle", type=int, default=4)
ap.add_argument("--level", type=int, default=255)
ap.add_argument("--out", default=os.path.join(ROOT, "roi_white_sweep.csv"))
ap.add_argument("--load", help="re-plot this saved sweep instead of measuring")
a = ap.parse_args()

# ---- the reference: one white frame, and its boxcar window ---------------------------
ref = None
if a.ref:
    rr = list(csv.DictReader(open(a.ref)))
    Tref = float(rr[0]["T_us"])
    rt = np.array([float(r["t_us"]) for r in rr])
    rm = np.array([float(r["mean"]) for r in rr])
    rf = np.array([float(r["ref"]) for r in rr])
    o = np.argsort(rt)
    rt, rm, rf = rt[o], rm[o], rf[o]
    if a.start is None:
        dt = float(np.median(np.diff(rt)))
        grid = np.arange(0.0, 5 * Tref, dt)
        g = np.interp(grid, rt, rm - rf, period=5 * Tref)
        n = int(round(Tref / dt))
        box = np.convolve(np.concatenate([g, g[:n]]), np.ones(n), mode="valid")[:len(g)]
        a.start = float(grid[int(np.argmax(box))])
    ref = (rt, rm, rf, Tref)
if a.start is None:
    sys.exit("give --start, or --ref to take it from a lag_sweep.py boxcar")

plt.ion()
fig, ax = plt.subplots(figsize=(13, 6))
fig.canvas.manager.window.wm_geometry("+40+40")
fig.subplots_adjust(left=0.07, right=0.985, top=0.88, bottom=0.1)
hdr = fig.text(0.07, 0.985, "", va="top", ha="left", family="monospace", fontsize=9.5)
wl, = ax.plot([], [], lw=.8, color="#d0453a", label="all frames white")
ax.set_xlabel("time after the vsync of the frame that carried white (us) -- the boxcar window")
ax.set_ylabel("ROI mean (ADU), %.2f us exposure" % a.expo)
ax.grid(alpha=.3)


def ui(msg=None):
    if msg:
        hdr.set_text(msg)
    try:
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
    except Exception:
        pass


def place(T):
    lo, hi = a.start - a.pad * T, a.start + (1 + a.pad) * T
    ax.set_xlim(lo, hi)
    ax.axvspan(a.start, a.start + T, color="#2e9e4f", alpha=.07, lw=0)
    for x_ in (a.start, a.start + T):
        ax.axvline(x_, color="#2e9e4f", lw=1, ls="--")
    for k in range(0, 7):
        if lo < k * T < hi:
            ax.axvline(k * T, color="#888", lw=.8)
            ax.text(k * T, 1.01, "vsync of frame %d" % k, transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=8, color="#555")
    if ref:
        rt, rm, rf, _ = ref
        sel = (rt >= a.start - a.pad * T) & (rt <= a.start + (1 + a.pad) * T)
        ax.plot(rt[sel], rm[sel], lw=.8, color="#1f5fd0",
                label="one white frame (%s)" % os.path.basename(a.ref))
        ax.plot(rt[sel], rf[sel], lw=.8, color="#888",
                label="  its dark reference (median of the 5 positions)")
    ax.legend(fontsize=8, loc="upper right")


if a.load:
    rows = list(csv.DictReader(open(a.load)))
    T = float(rows[0]["T_us"])
    place(T)
    pts = sorted((float(r["t_us"]), float(r["mean"])) for r in rows)
    src = a.load
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
    wr(ser, R_IMPCYC, 5)
    wr(ser, R_IMPLVL, a.level & 0xFF)           # ALL five frames white
    wr(ser, R_IMPRGB, 0x07)
    wr(ser, R_IMPLVL2, a.level & 0xFF)
    wr(ser, R_IMPRGB2, 0x07)
    wr(ser, R_ROICTL, 0xC0)
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    time.sleep(0.4)
    place(T)
    # window order, padding included: every t maps to delay t mod T (all frames are the
    # same), skipping the few us at each frame end the sensor cannot reach
    order = [t for t in np.arange(a.start - a.pad * T, a.start + (1 + a.pad) * T, a.step)
             if t % T <= T - e - 1.0]
    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["T_us", "expo_us", "start_us", "delay_us", "t_us", "mean", "sd", "n"])
    pts = []
    t0 = time.time()
    try:
        for i, t in enumerate(order):
            d = t % T
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if i % 25 == 0:
                wait_delay(ser, want)
            ser.reset_input_buffer()
            collect(ser, a.settle, timeout=4)
            v = [r[0] for r in collect(ser, a.frames, timeout=6) if r[1] == 256]
            if len(v) < 2:
                print("  delay %.1f: %d lines -- skipped" % (d, len(v)), flush=True)
                continue
            m = float(np.mean(v))
            w.writerow([round(T, 2), round(e, 3), round(a.start, 2), round(d, 2), round(t, 2),
                        round(m, 3), round(float(np.std(v)), 3), len(v)])
            pts.append((t, m))
            if i % 10 == 0:
                fh.flush()
                wl.set_data([p[0] for p in pts], [p[1] for p in pts])
                ax.relim()
                ax.autoscale_view(scalex=False)
                el = time.time() - t0
                ui("all-white sweep %.0f..%.0f us (window %.0f..%.0f, +-%.0f %%)   %d/%d   %.0f s, ~%.0f s left"
                   % (order[0], order[-1], a.start, a.start + T, 100 * a.pad, i + 1, len(order), el,
                      el / (i + 1) * (len(order) - i - 1)))
    finally:
        fh.close()
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
    src = a.out
    print("wrote %s  (%d readings)" % (a.out, len(pts)), flush=True)

pts.sort()
wl.set_data([p[0] for p in pts], [p[1] for p in pts])
ax.relim()
ax.autoscale_view(scalex=False)
inw = [p for p in pts if a.start <= p[0] < a.start + T]
msg = "window %.1f..%.1f us (frame %d +%.1f)   all-white mean %.1f ADU" % (
    a.start, a.start + T, int(a.start // T), a.start % T, np.mean([p[1] for p in inw]))
if ref:
    rt, rm, rf, _ = ref
    one = np.interp([p[0] for p in inw], rt, rm)
    msg += "   one-white mean %.1f   all-white adds %.1f ADU on average" % (
        one.mean(), np.mean([p[1] for p in inw]) - one.mean())
print(msg, flush=True)
ui(msg)
fig.savefig(os.path.splitext(src)[0] + ".png", dpi=110)
plt.ioff()
plt.show()
