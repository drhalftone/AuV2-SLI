#!/usr/bin/env python3
"""From three 5 us SLI sweeps (one per filter) find each LED's on-blocks, then choose ONE
exposure and a set of trigger delays that capture every block, plus a dark background delay.

    python host/led_windows.py --red sli5us_red.csv --green sli5us_green.csv --blue sli5us_blue460.csv
    python host/led_windows.py ... --exposure 825 --jitter 10

WHY ONE EXPOSURE. If every measurement uses the same exposure, the sensor pedestal and its
fixed overhead are the same in all of them, so each LED's total light is simply the sum of its
windows minus one background reading per window. The exposure must be at least as long as the
widest block, and a window longer than its block spills into the neighbouring dark gap (or
neighbouring LED). The spill is measured, not assumed: for each block the window sum, in that
block's OWN filter trace, is compared with the whole block's light.

Reads sweeps written by host/sli_frame_sweep.py (delay_us, mean, ...). The frame period is
taken from --frame (8325.8 us at 120.11 Hz).
"""
import argparse
import csv
import os
import sys

import numpy as np

STEP = 5.0
EXPO_UNIT_US = 0.375
TICK_US = 0.01


def load(p):
    d = np.loadtxt(p, delimiter=",", skiprows=1)
    return d[:, 0], d[:, 1]


def blocks_of(x, y, frac=0.10, merge=25.0):
    fl = np.percentile(y, 10)
    exc = y - fl
    mad = np.median(np.abs(y - np.median(y))) * 1.4826
    thr = max(frac * exc.max(), 4 * mad, 3.0)
    m = exc > thr
    out, i = [], 0
    while i < len(m):
        if m[i]:
            j = i
            while j + 1 < len(m) and m[j + 1]:
                j += 1
            out.append([x[i], x[j] + STEP])
            i = j + 1
        else:
            i += 1
    mg = []
    for b in out:                       # merge across short dips (fringe averaging)
        if mg and b[0] - mg[-1][1] <= merge:
            mg[-1][1] = b[1]
        else:
            mg.append(b)
    return exc, mg


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--red", required=True)
    ap.add_argument("--green", required=True)
    ap.add_argument("--blue", required=True)
    ap.add_argument("--exposure", type=float, default=825.0, help="us; rounded to 0.375 us units")
    ap.add_argument("--jitter", type=float, default=10.0, help="+-us of timing error the windows must tolerate")
    ap.add_argument("--frame", type=float, default=8325.8)
    ap.add_argument("--background-centre", type=float, default=8000.0)
    ap.add_argument("--background-delay", type=float, default=None,
                    help="trigger delay of the dark exposure, us (overrides --background-centre)")
    ap.add_argument("--centre", action="store_true",
                    help="centre each pulse in its exposure window instead of minimising the error")
    ap.add_argument("--out", default="led_windows_plan.csv")
    a = ap.parse_args()

    T = a.frame
    traces = {"red": a.red, "green": a.green, "blue": a.blue}
    x = None
    exc, blk = {}, []
    for k, p in traces.items():
        xx, yy = load(p)
        x = xx if x is None else x
        e, b = blocks_of(xx, yy)
        exc[k] = e
        blk += [(lo, hi, k) for lo, hi in b]
    blk.sort()
    units = int(round(a.exposure / EXPO_UNIT_US))
    E = units * EXPO_UNIT_US
    last = max(hi for lo, hi, k in blk)
    first = min(lo for lo, hi, k in blk)
    # BACKGROUND FIRST: a real measurement subtracts a dark window of the same exposure, so the
    # per-slice level of that window is removed from each trace before the windows are judged.
    s_bg = min(max(a.background_centre - E / 2, last + a.jitter), T + first - E - a.jitter)
    if a.background_delay is not None:
        s_bg = a.background_delay
        if s_bg < last + a.jitter:
            sys.exit('background window starts at %.0f us but the last LED block ends at %.0f us' % (s_bg, last))
    bg_light = {k: exc[k][(x >= s_bg) & (x < s_bg + E)].mean() for k in exc}
    exc = {k: exc[k] - bg_light[k] for k in exc}

    def err(k, lo, hi, s):
        w = (x >= s) & (x < s + E)
        inb = (x >= lo) & (x < hi)
        tot = exc[k][inb].sum()
        return (exc[k][w].sum() - tot) / tot, exc[k][w & inb].sum() / tot

    plan = []
    for lo, hi, k in blk:
        if a.centre:
            s = max(0.0, round(((lo + hi) / 2 - E / 2) / STEP) * STEP)
            worst = max(abs(err(k, lo, hi, s + d)[0]) for d in (-a.jitter, 0.0, a.jitter))
        else:
            best = None
            for s in np.arange(max(0.0, lo - E), min(hi, T + 575 - E) + 1e-9, STEP):
                worst = max(abs(err(k, lo, hi, s + d)[0]) for d in (-a.jitter, 0.0, a.jitter))
                if best is None or worst < best[0]:
                    best = (worst, s)
            worst, s = best
        e0, cover = err(k, lo, hi, s)
        plan.append(dict(led=k, block_lo=lo, block_hi=hi, delay_us=s, err=e0, worst=worst, cover=cover))

    print("exposure %.3f us = %d units; jitter tolerance +-%.0f us; frame %.1f us" % (E, units, a.jitter, T))
    print("\n id  LED     block (us)          width  delay (us)   window ends   own light in window  error  worst(+-jitter)")
    for i, p in enumerate(plan, 1):
        print("%3d  %-6s %5d-%5d  %6d   %8.1f      %8.1f         %5.1f%%        %+5.2f%%   %5.2f%%" % (
            i, p["led"], p["block_lo"], p["block_hi"], p["block_hi"] - p["block_lo"], p["delay_us"],
            p["delay_us"] + E, 100 * p["cover"], 100 * p["err"], 100 * p["worst"]))
    print("\nbackground window: delay %.1f us  (ends %.1f us; last LED block ends %d us)" % (s_bg, s_bg + E, last))
    print("  its per-slice level above the trace floor, per filter (subtracted from every window): " + "  ".join("%s %.2f ADU" % (k, v) for k, v in bg_light.items()))
    with open(a.out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "led", "block_lo_us", "block_hi_us", "delay_us", "delay_ticks_10ns",
                    "exposure_us", "exposure_units", "err_pct", "worst_err_pct"])
        for i, p in enumerate(plan, 1):
            w.writerow([i, p["led"], p["block_lo"], p["block_hi"], round(p["delay_us"], 2),
                        int(round(p["delay_us"] / TICK_US)), E, units, round(100 * p["err"], 3),
                        round(100 * p["worst"], 3)])
        w.writerow([0, "background", "", "", round(s_bg, 2), int(round(s_bg / TICK_US)), E, units, "", ""])
    print("\nwrote", a.out)
    return plan, s_bg, E, x, exc, blk


if __name__ == "__main__":
    main()
