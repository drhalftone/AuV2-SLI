#!/usr/bin/env python3
"""A 5 us delay sweep of the whole frame for each of 16 linear W values (plus a repeat of 0).

    python -u host/level_5us_sweep.py --hole-json hole_blue.json --label blue460
    python -u host/level_5us_sweep.py --hole-json hole_blue.json --label blue460 --resume

SCENE. White (255) everywhere except a black cutout (from host/hole_match.py) at the projector
pixels the ROI sees; a 32 px square flush with the bottom edge is drawn inside it at level W
(R = G = B = W, no LUT). W takes 16 linear values 0, 17, ... 255 and 0 is measured again at the end
as a drift check.

ORDER. Levels are visited in bisection order -- 0, 255, then midpoints -- not 0 -> 255, so a slow
drift shows up as scatter around the level-versus-light curve instead of masquerading as a level
effect (see the sweep notes). The trailing 0 stays last.

EACH LEVEL is a delay sweep with the slice equal to the exposure (5 us -> 4.875 us, 1709 slices),
each slice the MEDIAN of --frames camera lines. One file per level is written as soon as the level
finishes (w5us_<label>_L###.csv, the repeat as ..._L000r.csv), so a crash loses at most one level;
--resume skips levels whose file exists.
--ranges 2500:4000,5700:6800 sweeps only those delay windows (the blue LED's pulses). w5us_<label>_summary.csv lists every level with its dark
level (median of 7000-7900 us, or of the last 100 us of a ranged sweep), peak and time.
"""
import argparse
import csv
import json
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


def dither_order(values):
    """Endpoints first, then keep bisecting the remaining gaps."""
    n = len(values)
    order, seen = [], set()

    def emit(i):
        if i not in seen:
            seen.add(i)
            order.append(i)

    emit(0)
    emit(n - 1)
    intervals = [(0, n - 1)]
    while len(seen) < n:
        nxt = []
        for lo, hi in intervals:
            if hi - lo > 1:
                mid = (lo + hi) // 2
                emit(mid)
                nxt += [(lo, mid), (mid, hi)]
        if not nxt:
            break
        intervals = nxt
    for i in range(n):
        emit(i)
    return [values[i] for i in order]


def scene(s, hole, cx, cy, square, level):
    buf = s.blank()
    buf[:, :] = 255
    if hole > 0:
        buf[max(0, cy - hole // 2):min(s.h, cy + (hole + 1) // 2),
            max(0, cx - hole // 2):min(s.w, cx + (hole + 1) // 2)] = 0
    x0 = max(0, cx - square // 2)
    buf[s.h - square:s.h, x0:x0 + square] = level          # flush with the bottom edge
    return buf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hole-json", required=True, help="hole_<label>.json from host/hole_match.py")
    ap.add_argument("--label", required=True)
    ap.add_argument("--expo", type=float, default=5.0)
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--settle", type=int, default=3)
    ap.add_argument("--levels", type=int, default=16)
    ap.add_argument("--square", type=int, default=32)
    ap.add_argument("--order", choices=("dither", "linear"), default="dither")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--ranges", default=None,
                    help="only sweep these delay ranges, us, e.g. 2500:4000,5700:6800 (default: the whole frame)")
    a = ap.parse_args()

    j = json.load(open(a.hole_json))
    hole, cx, cy = j["size"], j["cx"], j["cy"]
    lin = [int(round(i * 255.0 / (a.levels - 1))) for i in range(a.levels)]
    levels = (dither_order(lin) if a.order == "dither" else lin) + [0]
    print("levels in order:", levels, flush=True)

    try:                                   # ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000002 | 0x00000001)
    except Exception:
        pass
    s = Screen(set_mode_first=True)
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
    ranges = None
    if a.ranges:
        ranges = [tuple(float(v) for v in r.split(':')) for r in a.ranges.split(',')]
        delays = [d for d in delays if any(lo <= d < hi for lo, hi in ranges)]
    print("frame %.1f us   slice %.3f us   %d slices per level%s   cutout %d px at (%d, %d)   square %d px"
          % (T, expo, len(delays), (" in ranges %s" % a.ranges) if ranges else "", hole, cx, cy, a.square), flush=True)

    plt.ion()
    fig, ax = plt.subplots(figsize=(13, 6))
    cm = plt.get_cmap("viridis")
    ax.set_xlim(0, T)
    ax.set_xlabel("delay after vsync (us)")
    ax.set_ylabel("ROI median per %.2f us slice (ADU)" % expo)
    ax.grid(alpha=.3)
    fig.canvas.manager.window.wm_geometry("+40+40")
    fig.tight_layout()
    plt.pause(0.1)

    summ_path = os.path.join(ROOT, "w5us_%s_summary.csv" % a.label)
    new = not (a.resume and os.path.exists(summ_path))
    sf = open(summ_path, "a" if not new else "w", newline="")
    sw = csv.writer(sf)
    if new:
        sw.writerow(["level", "kind", "t_s", "dark_level", "peak", "sat_px_max", "file"])
    t0 = time.time()
    done = 0
    try:
        for li, L in enumerate(levels):
            last = li == len(levels) - 1
            fn = "w5us_%s_L%03d%s.csv" % (a.label, L, "r" if last else "")
            path = os.path.join(ROOT, fn)
            if a.resume and os.path.exists(path):
                print("  W=%3d already done (%s) -- skipped" % (L, fn), flush=True)
                continue
            s.show(scene(s, hole, cx, cy, a.square, L), settle=2.0)   # the picture settles, then the sweep
            line, = ax.plot([], [], lw=1.0, color=cm(L / 255.0), label="W=%d%s" % (L, " (repeat)" if last else ""))
            xs, ys, sat, nfail = [], [], 0, 0
            with open(path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["delay_us", "median", "min", "max", "sat_px"])
                for i, d in enumerate(delays):
                    tick = int(round(d / TICK_US))
                    set_delay(ser, tick)
                    if i % 40 == 0:
                        wait_delay(ser, tick)
                    # A slice that yields too few ROI lines is retried; if it still fails it is
                    # recorded as a gap (nan) and the run carries on -- one bad slice must not
                    # cost a two-hour run.
                    rows = []
                    for attempt in range(4):
                        ser.reset_input_buffer()
                        collect(ser, a.settle, timeout=3)
                        rows = [r for r in collect(ser, a.frames, timeout=5, sat=True) if r[1] == 256]
                        if len(rows) >= 3:
                            break
                        set_delay(ser, tick)
                        wait_delay(ser, tick)
                    if len(rows) < 3:
                        nfail += 1
                        print("  !! W=%d delay %.0f: %d ROI lines after 4 tries -- gap" % (L, d, len(rows)), flush=True)
                        xs.append(d)
                        ys.append(float("nan"))
                        w.writerow([round(d, 2), "nan", "", "", ""])
                        continue
                    m = [r[0] for r in rows]
                    y = float(np.median(m))
                    sat = max(sat, max(r[4] for r in rows))
                    xs.append(d)
                    ys.append(y)
                    w.writerow([round(d, 2), round(y, 1), min(m), max(m), max(r[4] for r in rows)])
                    if i % 8 == 0:
                        line.set_data(xs, ys)
                        ax.relim()
                        ax.autoscale_view(scalex=False)
                        el = (time.time() - t0) / 60
                        ax.set_title("W=%d (%d/%d levels)  slice %d/%d   %.1f min elapsed"
                                     % (L, li + 1, len(levels), i + 1, len(delays), el))
                        try:
                            fig.canvas.draw_idle()
                            fig.canvas.flush_events()
                        except Exception:
                            pass
            y = np.array(ys)
            xa = np.array(xs)
            dm = (xa >= 7000) & (xa < 7900)
            if not dm.any():                  # ranged sweep: use the last 100 us, after the last blue pulse
                dm = xa >= xa.max() - 100
            dark = float(np.nanmedian(y[dm]))
            line.set_data(xs, ys)
            sw.writerow([L, "repeat" if last else "sweep", round(time.time() - t0, 1), round(dark, 2),
                         round(float(np.nanmax(y)), 1), sat, fn])
            sf.flush()
            done += 1
            print("  W=%3d%s  dark %.1f  peak %.1f  sat %d  gaps %d   %.1f min   (%d/%d)"
                  % (L, "r" if last else " ", dark, np.nanmax(y), sat, nfail, (time.time() - t0) / 60, li + 1, len(levels)),
                  flush=True)
            ax.legend(loc="upper right", fontsize=7, ncol=6)
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
        sf.close()
        s.close()
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
    fig.savefig(os.path.join(ROOT, "w5us_%s.png" % a.label), dpi=120)
    print("done: %d levels" % done, flush=True)
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
