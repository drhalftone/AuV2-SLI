#!/usr/bin/env python3
"""Does the runway add up over the exposure? Exposure-length test across WKKKK's ramp.

    py -3.12 host/runway_expo.py                    # COM5, live view
    py -3.12 host/runway_expo.py --starts 6000 7800 --lengths 5 50 500

In WKKKK the camera reads a ramp in frame 0, before the white frame's pulses (the
runway), but a power meter on the same screen spot reads dark there (2026-10-27,
pm_sweep.py). If the ramp is light on the screen, the reading over the dark level grows
with the exposure length as the 5 us profile predicts: the profile's excess summed over
the window. If the camera collects it outside its exposure, it does not.

For each --starts delay (us after frame 0's vsync) the exposure steps through --lengths,
each kept inside frame 0 (start + length < T - --margin). Each setting reads --frames
camera lines and takes position 0 (frame 0) and, as the dark level, the median of
positions 2-4 (black frames far from the white one). The prediction comes from --profile's
WKKKK sweep. The live view plots excess vs length per start, measured as points and
predicted as dashed lines. Every reading goes to --out.
"""
import argparse
import csv
import json
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

from sli_ui import (EXPO_UNIT_US, R_EXPO_HI, R_EXPO_LO, TICK_US, active_us,  # noqa: E402
                    collect, positions, preflight, set_delay, start_impulse,
                    stop_impulse, wait_delay, wr)

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("port", nargs="?", default="COM5")
ap.add_argument("--starts", type=float, nargs="+",
                default=[3000, 6000, 6700, 7300, 7800, 8100],
                help="exposure starts, us after frame 0's vsync")
ap.add_argument("--lengths", type=float, nargs="+",
                default=[5, 10, 20, 50, 100, 200, 400, 800, 1600, 3200])
ap.add_argument("--margin", type=float, default=10.0,
                help="keep start + length this far before frame 1's vsync, us")
ap.add_argument("--frames", type=int, default=20)
ap.add_argument("--settle", type=int, default=6)
ap.add_argument("--profile", default=os.path.join(ROOT, "profile_2026-10-16.sliprof"))
ap.add_argument("--out", default=os.path.join(
    ROOT, "runway_expo_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S")))
args = ap.parse_args()

HEAD = ["start_us", "length_req_us", "expo_us", "units", "pos0", "dark", "excess",
        "predicted", "sd0", "n0", "T_us"]


def predictor(path):
    """WKKKK 5 us profile -> f(start, length) = predicted excess over its dark level, ADU:
    the excess of every 5 us reading inside the window, summed, times expo / 4.875."""
    with open(path) as fh:
        j = json.load(fh)
    rows = j["sweeps"]["wkkkk"]["rows"]
    t = np.array([float(r[4]) for r in rows])
    v = np.array([float(r[5]) for r in rows])
    e = float(rows[0][1])
    o = np.argsort(t)
    t, v = t[o], v[o]
    dark = float(np.median(v[t > 2 * j["T_us"]]))

    def f(start, length):
        n = max(1, int(round(length / 5.0)))
        g = start + 5.0 * np.arange(n)
        return float(np.sum(np.interp(g, t, v) - dark) * (length / (5.0 * n)) * 4.875 / e)
    return f


def main():
    ser, T, mx = preflight(args.port)
    act = active_us(ser, T)
    pred = predictor(args.profile)
    print("board %s: frame %.2f us, exposure limit %.1f us" % (args.port, T, mx * EXPO_UNIT_US))
    start_impulse(ser, 255, 0, 5.0, mx)                # WKKKK
    lit = 0xFFFFFF

    plt.ion()
    fig, ax = plt.subplots(figsize=(10, 7))
    fig.canvas.manager.set_window_title("Runway exposure-length test (WKKKK)")
    ax.set_xscale("log")
    ax.set_yscale("symlog", linthresh=1.0)
    ax.set_xlabel("exposure length, us")
    ax.set_ylabel("frame 0 reading over dark, ADU")
    ax.set_title("measured (points) vs light hypothesis from the 5 us profile (dashed)")
    cols = plt.cm.viridis(np.linspace(0, .9, len(args.starts)))
    closed = []
    fig.canvas.mpl_connect("close_event", lambda e: closed.append(1))
    try:
        with open(args.out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(HEAD)
            for s, col in zip(args.starts, cols):
                L = [x for x in args.lengths if s + x < T - args.margin]
                ax.plot(L, [pred(s, x) for x in L], "--", color=col, lw=1)
                pts, = ax.plot([], [], "o-", color=col, ms=5, lw=.8, label="start %.0f us" % s)
                ax.legend(loc="upper left", fontsize=9)
                xs, ys = [], []
                for x in L:
                    if closed:
                        return
                    u = max(1, int(round(x / EXPO_UNIT_US)))
                    if u > mx:
                        continue
                    wr(ser, R_EXPO_LO, u & 0xFF)
                    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
                    want = int(round(s / TICK_US))
                    set_delay(ser, want)
                    wait_delay(ser, want)
                    ser.reset_input_buffer()
                    collect(ser, args.settle, timeout=4)
                    rows = [r for r in collect(ser, args.frames, timeout=8) if r[1] == 256]
                    by = positions(rows, s, lit, act)
                    if len(by) < 5:
                        print("  start %.0f, %.1f us: positions seen %s -- skipped"
                              % (s, x, sorted((k, len(v)) for k, v in by.items())))
                        continue
                    m0 = float(np.mean(by[0]))
                    dark = float(np.median([np.mean(by[k]) for k in (2, 3, 4)]))
                    p = pred(s, x)
                    e = u * EXPO_UNIT_US
                    w.writerow([s, x, e, u, round(m0, 2), round(dark, 2), round(m0 - dark, 2),
                                round(p, 2), round(float(np.std(by[0])), 2), len(by[0]),
                                round(T, 2)])
                    fh.flush()
                    print("start %6.0f  expo %7.1f us: frame 0 %6.1f, dark %5.1f, excess %7.1f"
                          " ADU, light predicts %8.1f" % (s, e, m0, dark, m0 - dark, p))
                    xs.append(e)
                    ys.append(m0 - dark)
                    pts.set_data(xs, ys)
                    ax.relim()
                    ax.autoscale_view()
                    plt.pause(0.05)
        print("wrote %s" % args.out)
    finally:
        stop_impulse(ser)
        ser.close()
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
