#!/usr/bin/env python3
"""Size the black cutout in an all-white screen so the ROI's background matches a target.

    python -u host/hole_match.py --target-from sli5us_red.csv --cx 384 --cy 592 --label red
    python -u host/hole_match.py --target 158.2 --cx 384 --cy 592

WHAT IT MATCHES. The projector shows white (255) everywhere except a black square of side S
centred on the projector pixels the camera ROI sees (find them with host/align_roi.py). The ROI
is read with a 5 us exposure at a 8000 us genlock delay -- after the last LED of the frame -- so
what it reads is the leak from the white surround, not an LED pulse. That reading falls as the
hole grows. The target is the reading the SLI scan gives at the same delay and exposure (--target
or --target-from a sli_frame_sweep CSV), so the calibration square sits in the same background
the real measurement does.

HOW. Full white reads highest; a big hole reads lowest. The size is found by bisection (the
reading is monotone in S). Each camera line is a WHOLE number of ADU, so a median of them can only
land on a whole number and cannot resolve a 0.3 ADU match; each reading here is instead the TRIMMED
MEAN of --frames lines (fractional), with its standard error printed. The winner is then re-read
--confirm times and the whole search is repeated from the nearest size if the average is not within
--tol. Finally the no-cutout level is re-read to expose drift during the run. The result is written
to hole_<label>.json for the sweep scripts.
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


def scene(s, size, cx, cy):
    """White field with a black square of side `size` centred on (cx, cy), clipped to the panel."""
    buf = s.blank()
    buf[:, :] = 255
    if size > 0:
        x0, x1 = max(0, cx - size // 2), min(s.w, cx + (size + 1) // 2)
        y0, y1 = max(0, cy - size // 2), min(s.h, cy + (size + 1) // 2)
        buf[y0:y1, x0:x1] = 0
    return buf


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=float, help="ROI level to match, ADU")
    ap.add_argument("--target-from", help="a sli_frame_sweep CSV; the level at --delay is the target")
    ap.add_argument("--cx", type=int, default=384, help="projector x of the ROI (align_roi.py)")
    ap.add_argument("--cy", type=int, default=592, help="projector y of the ROI (align_roi.py)")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--delay", type=float, default=8000.0, help="genlock delay, us")
    ap.add_argument("--frames", type=int, default=90, help="camera lines per reading (trimmed mean)")
    ap.add_argument("--confirm", type=int, default=3, help="fresh readings averaged to confirm the winner")
    ap.add_argument("--settle", type=int, default=6)
    ap.add_argument("--tol", type=float, default=0.25, help="stop when the confirmed level is within this many ADU")
    ap.add_argument("--max-size", type=int, default=480)
    ap.add_argument("--label", default="hole")
    a = ap.parse_args()

    if a.target is None and not a.target_from:
        sys.exit("give --target or --target-from")
    if a.target is None:
        d = np.loadtxt(a.target_from, delimiter=",", skiprows=1)
        w = np.abs(d[:, 0] - a.delay) <= 30
        if not w.any():
            sys.exit("%s has no points near %.0f us" % (a.target_from, a.delay))
        a.target = float(d[w, 1].mean())
    print("target %.2f ADU at %.0f us delay, %.2f us exposure; ROI at projector (%d, %d)"
          % (a.target, a.delay, a.expo, a.cx, a.cy), flush=True)

    s = Screen(set_mode_first=True)
    ser = serial.Serial("COM6", 115200, timeout=0.05)
    time.sleep(0.4)

    def R(addr):
        v = rd(ser, addr)
        return 0 if v is None else v

    T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
    if (R(0x5D) & 3) != 3 or abs(1e6 / T - 120) > 1 or not (R(0x55) & 0x80):
        sys.exit("ABORT: frame %.1f us genlock 0x%02X -- is the PC's HDMI connected?" % (T, R(0x5D)))
    wr(ser, 0x13, 0x00)
    wr(ser, 0x16, 0x00)
    wr(ser, R_ROICTL, 0x40)
    u = max(1, int(round(a.expo / EXPO_UNIT_US)))
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    tick = int(round(a.delay / TICK_US))
    set_delay(ser, tick)
    got, ok = wait_delay(ser, tick)
    if not ok:
        sys.exit("delay did not land (%s)" % got)

    plt.ion()
    fig, ax = plt.subplots(figsize=(8, 5))
    pts, = ax.plot([], [], "o-", color="#2358c8")
    ax.axhline(a.target, color="#c62f26", ls="--", label="target %.2f" % a.target)
    ax.set_xlabel("side of the black cutout (px)")
    ax.set_ylabel("ROI level (ADU), %.1f us at %.0f us" % (u * EXPO_UNIT_US, a.delay))
    ax.grid(alpha=.3)
    ax.legend()
    fig.tight_layout()
    plt.pause(0.1)

    log = []

    def measure(size):
        s.show(scene(s, size, a.cx, a.cy), settle=1.2)
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3)
        rows = [r for r in collect(ser, a.frames, timeout=6, sat=True) if r[1] == 256]
        if len(rows) < a.frames // 2:
            sys.exit("ABORT: %d ROI lines at size %d" % (len(rows), size))
        vals = np.sort(np.array([r[0] for r in rows], float))
        k = max(1, len(vals) // 10)
        core = vals[k:-k] if len(vals) > 2 * k else vals        # trimmed mean: drops the 10% tails
        v = float(core.mean())
        se = float(core.std(ddof=1) / np.sqrt(len(core))) if len(core) > 1 else 0.0
        log.append((size, v))
        o = sorted(log)
        pts.set_data([p[0] for p in o], [p[1] for p in o])
        ax.relim()
        ax.autoscale_view()
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
        print("  size %3d  ->  %.2f ADU  +-%.2f   (%+.2f from target, %d lines)"
              % (size, v, se, v - a.target, len(rows)), flush=True)
        return v

    try:
        hi_r = measure(0)                      # no cutout: brightest
        lo_r = measure(a.max_size)             # biggest cutout: darkest
        if not (lo_r - a.tol <= a.target <= hi_r + a.tol):
            print("TARGET %.2f IS OUTSIDE THE REACHABLE RANGE %.2f .. %.2f" % (a.target, lo_r, hi_r))
            best = min(log, key=lambda p: abs(p[1] - a.target))
            check = measure(best[0])
        else:
            lo, hi = 0, a.max_size             # reading(lo) >= target >= reading(hi)
            while hi - lo > 4:
                mid = ((lo + hi) // 2) & ~1
                v = measure(mid)
                if v > a.target:
                    lo = mid
                else:
                    hi = mid
            # linear interpolation between the two bracketing readings, then confirm by repeated reading
            rlo = float(np.mean([p[1] for p in log if p[0] == lo]))
            rhi = float(np.mean([p[1] for p in log if p[0] == hi]))
            size = int(round((lo + (hi - lo) * (rlo - a.target) / (rlo - rhi)) / 2.0)) * 2 if rlo != rhi else (lo + hi) // 2
            for attempt in range(6):
                reads = [measure(size) for _ in range(a.confirm)]
                check = float(np.mean(reads))
                print("  confirm size %d: %s -> mean %.2f (%+.2f from target)"
                      % (size, ", ".join("%.2f" % r for r in reads), check, check - a.target), flush=True)
                if abs(check - a.target) <= a.tol:
                    break
                # step toward the target using the local slope of the readings so far
                near = sorted(log, key=lambda p: abs(p[0] - size))[:6]
                xs_, ys_ = np.array([p[0] for p in near], float), np.array([p[1] for p in near], float)
                slope = np.polyfit(xs_, ys_, 1)[0] if len(set(xs_)) > 1 else -0.05
                slope = slope if slope < -0.005 else -0.05
                size = int(round((size + (a.target - check) / slope) / 2.0)) * 2
                size = max(0, min(a.max_size, size))
            best = (size, check)
        drift = measure(0) - hi_r          # the same no-cutout picture, re-read at the end
        print("  drift over the run (no-cutout level, end minus start): %+.2f ADU" % drift, flush=True)
        out = {"label": a.label, "size": best[0], "cx": a.cx, "cy": a.cy, "target": a.target,
               "level": check, "drift_adu": drift, "expo_us": u * EXPO_UNIT_US, "delay_us": a.delay,
               "frames": a.frames}
        path = os.path.join(ROOT, "hole_%s.json" % a.label)
        with open(path, "w") as f:
            json.dump(out, f, indent=1)
        with open(os.path.join(ROOT, "hole_%s.csv" % a.label), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["size_px", "roi_adu"])
            for row in sorted(log):
                w.writerow(row)
        print("\nCUTOUT %d x %d px centred on (%d, %d): reads %.2f ADU against a target of %.2f   wrote %s"
              % (best[0], best[0], a.cx, a.cy, check, a.target, path), flush=True)
        fig.savefig(os.path.join(ROOT, "hole_%s.png" % a.label), dpi=120)
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
        s.close()
    plt.ioff()
    plt.show()


if __name__ == "__main__":
    main()
