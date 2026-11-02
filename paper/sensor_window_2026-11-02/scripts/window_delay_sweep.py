"""Delay sweep in BOTH readout modes, with a live plot, to find the window's timing error.

Every projected frame is white at --level, so the ROI mean at a given genlock delay is
the light the projector emits in that slice of its frame. If the window exposure lands
at a different time than the full-frame exposure, the window curve is the full curve
shifted along the delay axis; the shift is reported by cross-correlation at the end.

Modes are interleaved at each delay so slow drift cannot masquerade as a difference.

Usage: window_delay_sweep.py OUTPREFIX [--port COM5] [--level 64] [--step 50]
       [--expo-units 13] [--frames 20]
Needs the profiling bitstream with the window, genlock + offline video on, sli_ui closed.
"""
import argparse
import csv
import statistics
import sys
import time

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import numpy as np
import serial

sys.path.insert(0, r"C:\Users\dllau\Developer\AuV2-SLI-sensorwin\host")
from frame_sweep import (wr, rd, set_delay, wait_delay, collect, TICK_US,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL, R_IMPRGB,
                         R_IMPLVL2, R_IMPRGB2)

R_WINON = 0x48
ROICTL_ON, WIN_BIT = 0xC0, 0x01
FRAME_US = 1e6 / 120.0


def wait_window(ser, on, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = rd(ser, R_WINON)
        if v is not None and (v & 1) == on:
            return True
    return False


def point(ser, frames):
    ser.reset_input_buffer()
    collect(ser, 6, timeout=3)                       # discard: the switch, the delay
    rows = collect(ser, frames, timeout=frames / 60 + 4, sat=True)
    good = [r for r in rows if r[1] == 256]
    if not good:
        return float("nan"), 0
    return statistics.fmean(r[0] for r in good), max(r[4] or 0 for r in good)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--level", type=int, default=64)
    ap.add_argument("--step", type=float, default=50.0)
    ap.add_argument("--expo-units", type=int, default=13)
    ap.add_argument("--frames", type=int, default=20)
    a = ap.parse_args()

    delays = np.arange(0.0, FRAME_US, a.step)
    res = {0: np.full(delays.size, np.nan), 1: np.full(delays.size, np.nan)}
    clip = {0: 0, 1: 0}

    plt.ion()
    fig, ax = plt.subplots(figsize=(11, 5))
    lines = {m: ax.plot([], [], ".-", label=n)[0] for m, n in ((0, "full frame"), (1, "window"))}
    ax.set_xlim(0, FRAME_US)
    ax.set_xlabel("genlock delay after vsync (us)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.set_title("level %d, exposure %.3f us, %g us steps"
                 % (a.level, a.expo_units * 0.375, a.step))
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    plt.pause(0.05)

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    t0 = time.time()
    try:
        wr(ser, R_IMPCYC, 5)
        for r, v in ((R_IMPLVL, a.level), (R_IMPLVL2, a.level), (R_IMPRGB, 7), (R_IMPRGB2, 7)):
            wr(ser, r, v)
        wr(ser, R_EXPO_LO, a.expo_units & 0xFF)
        wr(ser, R_EXPO_HI, (a.expo_units >> 8) & 0xFF)
        wr(ser, R_ROICTL, ROICTL_ON)
        time.sleep(0.4)
        if not collect(ser, 10, timeout=4):
            sys.exit("no camera lines -- genlock on, offline video running?")

        for i, d in enumerate(delays):
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if not wait_delay(ser, want)[1]:
                print("delay %.0f did not land -- skipped" % d)
                continue
            for on in (0, 1):
                wr(ser, R_ROICTL, ROICTL_ON | (WIN_BIT if on else 0))
                if not wait_window(ser, on):
                    print("delay %.0f: sensor never reported window %s" % (d, on))
                    continue
                m, nhi = point(ser, a.frames)
                res[on][i] = m
                clip[on] += nhi > 0
                lines[on].set_data(delays[: i + 1], res[on][: i + 1])
            ax.relim()
            ax.autoscale_view(scalex=False)
            plt.pause(0.01)
            if i % 20 == 0:
                print("%5.0f us  full %7.2f  window %7.2f   (%.0f s)"
                      % (d, res[0][i], res[1][i], time.time() - t0), flush=True)
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()

    with open(a.out + ".csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["delay_us", "full", "window"])
        for d, x, y in zip(delays, res[0], res[1]):
            w.writerow(["%.1f" % d, "%.3f" % x, "%.3f" % y])
    print("points with clipped pixels: full %d, window %d" % (clip[0], clip[1]))

    # Circular cross-correlation: the frame repeats every FRAME_US.
    ok = ~(np.isnan(res[0]) | np.isnan(res[1]))
    if ok.sum() > 10:
        x = np.interp(delays, delays[ok], res[0][ok])
        y = np.interp(delays, delays[ok], res[1][ok])
        x0, y0 = x - x.mean(), y - y.mean()
        cc = [float((x0 * np.roll(y0, -k)).sum() / np.sqrt((x0 ** 2).sum() * (y0 ** 2).sum()))
              for k in range(delays.size)]
        k = int(np.argmax(cc))
        shift = k * a.step if k <= delays.size // 2 else (k - delays.size) * a.step
        # cc[k] peaks where window(d + k) matches full(d): the window curve is the
        # full curve moved RIGHT by `shift`, i.e. the window needs `shift` more delay
        # to see the same light -- its exposure lands `shift` earlier after a trigger.
        print("window curve = full curve moved right by %+.0f us (corr %.4f; unshifted %.4f)"
              % (shift, cc[k], cc[0]))
        yb = np.roll(y, -k)
        ratio = np.nanmedian(yb[x > np.nanpercentile(x, 50)] / x[x > np.nanpercentile(x, 50)])
        print("after the shift: median window/full ratio on the brighter half %.3f" % ratio)
        ax.set_title(ax.get_title() + "   |   window = full moved right %+.0f us" % shift)
    fig.savefig(a.out + ".png", dpi=120)
    print("saved", a.out + ".csv", a.out + ".png", "  total %.0f s" % (time.time() - t0))
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
