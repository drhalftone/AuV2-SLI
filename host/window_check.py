#!/usr/bin/env python3
"""Stage 1 of multi-sample profiling: does the sensor's readout WINDOW give the same mean
as the full frame, and how long does a windowed picture really take?

    python host/window_check.py [COM5] [--delays 500 3000 7000] [--frames 60]

Needs the PROFILING bitstream (build_roimin.tcl, WITH_CAM=2) with the window logic, the
board in offline video with genlock on (sli_ui.py's Connect does both).

ROICTL bit 0 switches the sensor between reading the whole 1280x1024 array and reading
only a 32x24 window around the 16x16 ROI (cam_boot_stage1.v). The window covers the SAME
sensor pixels as the full-frame ROI, so with every projected frame white the two modes
must agree. For each delay and each mode this prints:

    mean / sd      of the per-frame ROI means
    npx            the share of lines with npx == 256 (must be 100 %)
    gaps           trigger-ordinal gaps (lost frames)
    trig->fs       trigger to the first line out (exposure + the sensor's own overhead)
    readout        first line to frame end (black rows + the window)

The last two decide how many pictures fit in one projector frame; the datasheet's fps
table predicts ~190 us per windowed picture, against a measured ~1.1 ms trigger->fs on
the full frame. This script measures both instead of assuming either.
"""
import argparse
import statistics
import sys
import time

import serial

from frame_sweep import (wr, rd, set_delay, wait_delay, collect, TICK_US,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL, R_IMPRGB,
                         R_IMPLVL2, R_IMPRGB2)

R_LAT, R_RDLEN, R_WINON = 0x42, 0x45, 0x48     # cam_roi_min status, 10 ns ticks
ROICTL_ON = 0xC0                                 # impulse enable | per-frame stream
WIN_BIT = 0x01


def rd24(ser, addr):
    """A 24-bit status value read one byte at a time. The status snapshot republishes at
    ~10 Hz, so a read can straddle two snapshots: take it only when two agree."""
    last = None
    for _ in range(8):
        b = [rd(ser, addr + i) for i in range(3)]
        if None in b:
            continue
        v = b[0] | (b[1] << 8) | (b[2] << 16)
        if v == last:
            return v
        last = v
    return last


def wait_window(ser, on, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = rd(ser, R_WINON)
        if v is not None and (v & 1) == on:
            return True
    return False


def measure(ser, frames):
    ser.reset_input_buffer()
    collect(ser, 10, timeout=4)                        # discard: the switch, the delay
    rows = collect(ser, frames, timeout=frames / 60 + 5, sat=True)
    good = [r for r in rows if r[1] == 256]
    gaps = sum(1 for a, b in zip(rows, rows[1:]) if (b[3] - a[3]) & 0xFF != 1)
    lat = [v for v in (rd24(ser, R_LAT) for _ in range(5)) if v is not None]
    rdl = [v for v in (rd24(ser, R_RDLEN) for _ in range(5)) if v is not None]
    ms = [r[0] for r in good]
    return dict(
        n=len(rows), npx_ok=len(good) / max(1, len(rows)), gaps=gaps,
        mean=statistics.fmean(ms) if ms else float("nan"),
        sd=statistics.pstdev(ms) if len(ms) > 1 else float("nan"),
        nhi=max((r[4] or 0) for r in good) if good else None,
        lat_us=statistics.median(lat) * TICK_US if lat else float("nan"),
        rd_us=statistics.median(rdl) * TICK_US if rdl else float("nan"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="COM5")
    ap.add_argument("--delays", type=float, nargs="+", default=[500.0, 3000.0, 7000.0],
                    help="genlock delays to compare at, us after vsync")
    ap.add_argument("--frames", type=int, default=60, help="frames kept per point")
    ap.add_argument("--expo-units", type=int, default=13,
                    help="exposure in 0.375 us units (13 = 4.875 us, the sweep's)")
    a = ap.parse_args()

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    try:
        # Every frame white, so the reading does not depend on which frame is which.
        wr(ser, R_IMPCYC, 5)
        for r, v in ((R_IMPLVL, 255), (R_IMPLVL2, 255), (R_IMPRGB, 7), (R_IMPRGB2, 7)):
            wr(ser, r, v)
        wr(ser, R_EXPO_LO, a.expo_units & 0xFF)
        wr(ser, R_EXPO_HI, (a.expo_units >> 8) & 0xFF)
        wr(ser, R_ROICTL, ROICTL_ON)
        time.sleep(0.4)

        probe = collect(ser, 20, timeout=4)
        if not probe:
            sys.exit("no camera lines -- genlock on, offline video running?")
        if not any((b[3] - a_[3]) & 0xFF for a_, b in zip(probe, probe[1:])):
            sys.exit("the trigger ordinal never moves: this is the merged build, not the "
                     "profiling bitstream (build_roimin.tcl). The window is not in it.")

        print("exposure %d units = %.3f us, %d frames per point\n"
              % (a.expo_units, a.expo_units * 0.375, a.frames))
        print("%8s  %-6s %8s %6s %6s %5s %5s %10s %10s"
              % ("delay", "mode", "mean", "sd", "npx", "gaps", "nhi", "trig->fs", "readout"))
        diffs = []
        for d in a.delays:
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if not wait_delay(ser, want)[1]:
                print("  delay %.1f did not land -- skipped" % d)
                continue
            res = {}
            for on in (0, 1):
                wr(ser, R_ROICTL, ROICTL_ON | (WIN_BIT if on else 0))
                if not wait_window(ser, on):
                    print("  the sensor never reported window %s" % ("ON" if on else "OFF"))
                    continue
                m = res[on] = measure(ser, a.frames)
                print("%8.1f  %-6s %8.2f %6.2f %5.0f%% %5d %5s %8.1fus %8.1fus"
                      % (d, "window" if on else "full", m["mean"], m["sd"],
                         100 * m["npx_ok"], m["gaps"], m["nhi"], m["lat_us"], m["rd_us"]))
            if 0 in res and 1 in res:
                diffs.append(res[1]["mean"] - res[0]["mean"])
                print("%8s  window - full = %+.2f ADU\n" % ("", diffs[-1]))

        if diffs:
            print("largest window-vs-full difference: %+.2f ADU" % max(diffs, key=abs))
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()


if __name__ == "__main__":
    main()
