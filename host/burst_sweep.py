#!/usr/bin/env python3
"""Burst delay sweep: N window pictures per projected frame, with a live plot.

    python host/burst_sweep.py [COM5] --n 6 --baud 921600 [--cycle wkkkk] [--step 50]

Needs the profiling bitstream with bursts (build_roimin.tcl; UART_BAUD=921600 for
N > 3), genlock on and offline video running (sli_ui.py's Connect does both), and
sli_ui.py closed.

WHAT THE FPGA DOES. ROICTL bits 3:1 = N-1 make cam_roi_min fire N triggers after
each projected vsync, at gl_dly + k * delta for k = 0..N-1, with
delta = (measured vsync period) / N. It does so only while the sensor reads out the
32x24 window (ROICTL bit 0): a full-frame picture takes ~5.7 ms to read out. Every
ROI line then ends in ",k", the burst index:

    R=mmm,nnn,rrggbb,cc,hh,ll,k<CR><LF>

WHAT THIS SCRIPT DOES. Sweeps gl_dly from 0 to delta, so the N pictures per frame
cover the whole frame in 1/N of the steps. Each line is placed on the time axis at

    t = position * T + gl_dly + k * delta

where position is the projected frame's place in the impulse cycle: 0 for every
frame with --cycle wwwww, and frames since the last white one (from the
transmitted top-left pixel) with --cycle wkkkk.

THE WINDOW READOUT HAS NO RUNWAY. A full-frame reading of row 512 includes the
light of the ~2.9 ms after the exposure (paper/sensor_window_2026-11-02.md on
feat/frame-label); the window is read within ~30 us, so these readings are the
light during the exposure only. They will NOT match an earlier full-frame sweep
between pulses.
"""
import argparse
import csv
import re
import statistics
import sys
import time

import serial

from frame_sweep import (wr, rd, set_delay, wait_delay, TICK_US, EXPO_UNIT_US,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL, R_IMPRGB,
                         R_IMPLVL2, R_IMPRGB2)

R_WINON = 0x48
R_VSPER = 0x8E                       # 0x8E..0x90: measured vsync period, 10 ns ticks
ROICTL_BASE = 0xC1                   # impulse enable | per-frame stream | window
PICTURE_US = 110.0                   # window picture overhead beyond the exposure

# The burst field is OPTIONAL so a line from a non-burst frame still parses; k
# then reads as 0. CRLF is required, as in frame_sweep.LINE.
LINE = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{6}),([0-9A-F]{2})"
                  + b"(?:,([0-9A-F]{2}),([0-9A-F]{2}))?"
                  + b"(?:,([0-7]))?"
                  + bytes([13, 10]))


def rd24(ser, addr):
    """A 24-bit status value; the snapshot republishes at ~10 Hz, so take two that agree."""
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


class Lines:
    """Buffered line reader. EVERY line read is returned, in order, exactly once: a
    reader that kept only the first n lines of each serial read and dropped the
    rest made the ordinal check count the dropped ones as lost (and hid real loss)."""

    def __init__(self, ser):
        self.ser, self.buf = ser, b""

    def flush(self):
        self.ser.reset_input_buffer()
        self.buf = b""

    def read(self, timeout):
        """All complete lines that arrive within `timeout` s (at least one read):
        (mean, npx, tlp, cc, nhi, k)."""
        out, t0 = [], time.time()
        while True:
            self.buf += self.ser.read(4096)
            ms = list(LINE.finditer(self.buf))
            for m in ms:
                out.append((int(m.group(1), 16), int(m.group(2), 16), int(m.group(3), 16),
                            int(m.group(4), 16),
                            int(m.group(5), 16) if m.group(5) else None,
                            int(m.group(7)) if m.group(7) else 0))
            if ms:
                self.buf = self.buf[ms[-1].end():]
            elif len(self.buf) > 16384:
                self.buf = self.buf[-64:]
            if out or time.time() - t0 >= timeout:
                return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", default="COM5")
    ap.add_argument("--baud", type=int, default=921600,
                    help="must match the bitstream's UART_BAUD")
    ap.add_argument("--n", type=int, default=6, help="pictures per projected frame, 1..8")
    ap.add_argument("--step", type=float, default=50.0, help="delay step, us")
    ap.add_argument("--expo-us", type=float, default=4.875)
    ap.add_argument("--cycle", choices=("wwwww", "wkkkk"), default="wwwww")
    ap.add_argument("--level", type=int, default=255, help="white level")
    ap.add_argument("--colour", default="white", choices=("white", "red", "green", "blue"))
    ap.add_argument("--frames", type=int, default=10,
                    help="projected frames kept per delay (per cycle position for wkkkk)")
    ap.add_argument("--out", default=None, help="CSV prefix (default: burst_<date>)")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()
    if not 1 <= a.n <= 8:
        sys.exit("--n must be 1..8")
    if a.n > 3 and a.baud < 921600:
        print("WARNING: N > 3 lines per 8.33 ms frame do not fit in %d baud; "
              "lines will be lost" % a.baud)
    rgb = {"white": 7, "red": 4, "green": 2, "blue": 1}[a.colour]
    out = a.out or time.strftime("burst_%Y%m%d_%H%M%S")

    ser = serial.Serial(a.port, a.baud, timeout=0.05)
    try:
        # sli_ui.py's Connect cannot be used at a non-default baud, so check what it
        # would have checked: the profiling build starts genlocked (GENLOCK_ON=1) and
        # the board picks its offline mode from the projector's EDID by itself.
        gl = rd(ser, 0x5D)
        if gl is None:
            sys.exit("no reply from the board at %d baud -- wrong --baud for this bitstream?"
                     % a.baud)
        if gl & 3 != 3:
            sys.exit("genlock not on and live (0x5D = 0x%02X) -- offline video running?" % gl)
        black = 0 if a.cycle == "wkkkk" else a.level
        wr(ser, R_IMPCYC, 5)
        wr(ser, R_IMPLVL2, a.level)          # the frame at the sequence start
        wr(ser, R_IMPLVL, black)             # the other four
        wr(ser, R_IMPRGB, rgb)
        wr(ser, R_IMPRGB2, rgb)
        units = max(1, int(a.expo_us / EXPO_UNIT_US + 1e-6))
        wr(ser, R_EXPO_LO, units & 0xFF)
        wr(ser, R_EXPO_HI, (units >> 8) & 0xFF)
        wr(ser, R_ROICTL, ROICTL_BASE | ((a.n - 1) << 1))
        t0 = time.time()
        while time.time() - t0 < 3:
            v = rd(ser, R_WINON)
            if v is not None and v & 1:
                break
        else:
            sys.exit("the sensor never reported the window ON -- bursts need it")

        vs_ticks = rd24(ser, R_VSPER)
        if not vs_ticks:
            sys.exit("no vsync period -- genlock on and offline video running?")
        T = vs_ticks * TICK_US
        delta = T / a.n
        print("vsync period %.1f us, N = %d, delta = %.1f us, exposure %.3f us"
              % (T, a.n, delta, units * EXPO_UNIT_US))
        if units * EXPO_UNIT_US + PICTURE_US > delta:
            print("WARNING: exposure + ~%.0f us exceeds delta; triggers will be lost"
                  % PICTURE_US)

        lines = Lines(ser)
        lines.flush()
        probe, t0 = [], time.time()
        while len(probe) < 4 * a.n and time.time() - t0 < 3:
            probe += lines.read(0.2)
        if not probe:
            sys.exit("no ROI lines -- genlock on, offline video running?")
        ks = sorted({p[5] for p in probe})
        print("probe: %d lines, burst indices seen %s" % (len(probe), ks))

        plot = None
        if not a.no_plot:
            import matplotlib
            matplotlib.use("TkAgg")
            import matplotlib.pyplot as plt
            plt.ion()
            fig, ax = plt.subplots(figsize=(12, 5))
            span = (5 if a.cycle == "wkkkk" else 1) * T / 1000.0
            # Dots joined by lines. Within one k the points arrive in delay order,
            # so each k is a contiguous segment of the time axis; the segments
            # meet end to end (wkkkk: one segment per cycle position).
            dots = [ax.plot([], [], ".-", ms=3, lw=0.8, label="k = %d" % k)[0]
                    for k in range(a.n)]
            ax.set_xlim(0, span)
            ax.set_xlabel("time in the %s cycle (ms)" % a.cycle.upper())
            ax.set_ylabel("ROI mean (ADU), window readout")
            ax.set_title("burst sweep: N = %d per frame, delta %.0f us, exposure %.3f us, %s %d"
                         % (a.n, delta, units * EXPO_UNIT_US, a.colour, a.level))
            ax.grid(alpha=0.3)
            ax.legend(loc="upper right", ncol=min(a.n, 4), fontsize=8)
            fig.tight_layout()
            plt.pause(0.05)
            plot = (plt, fig, ax, dots)
        xs = [[] for _ in range(a.n)]
        ys = [[] for _ in range(a.n)]

        rows, lost, clipped, badnpx, received = [], 0, 0, 0, 0
        need_pos = 5 if a.cycle == "wkkkk" else 1
        steps = int(delta // a.step) + 1
        t_start = time.time()
        for i in range(steps):
            d = i * a.step
            if d >= delta:
                break
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if not wait_delay(ser, want)[1]:
                print("delay %.0f did not land -- skipped" % d)
                continue
            # Discard the frame in flight when the delay changed: one projected
            # frame's worth of lines after the flush.
            lines.flush()
            skipped, t_skip = 0, time.time()
            while skipped < 2 * a.n and time.time() - t_skip < 1:
                skipped += len(lines.read(0.1))
            # Group lines into projected frames: k restarts at 0 on each vsync.
            got = {}                                     # (pos, k) -> [means]
            pos, prev_k, prev_cc, seen_white = None, None, None, False
            deadline = time.time() + 4 + a.frames * need_pos / 30.0
            while time.time() < deadline:
                if all(len(got.get((p, k), [])) >= a.frames
                       for p in range(need_pos) for k in range(a.n)):
                    break
                for mean, npx, tlp, cc, nhi, k in lines.read(0.2):
                    received += 1
                    if prev_cc is not None and (cc - prev_cc) & 0xFF != 1:
                        lost += (cc - prev_cc - 1) & 0xFF
                    prev_cc = cc
                    if prev_k is None or k <= prev_k:            # a new projected frame
                        if a.cycle == "wwwww":
                            pos = 0
                        elif tlp != 0:
                            pos, seen_white = 0, True
                        elif seen_white and pos is not None:
                            pos += 1
                    prev_k = k
                    if npx != 0x100:
                        badnpx += 1
                        continue
                    if nhi:
                        clipped += 1
                    if pos is None or pos >= need_pos:
                        continue
                    got.setdefault((pos, k), []).append(mean)
            for (p, k), ms in got.items():
                t = p * T + d + k * delta
                m = statistics.fmean(ms)
                rows.append((t, d, p, k, m, len(ms)))
                xs[k].append(t / 1000.0)
                ys[k].append(m)
            if plot:
                plt, fig, ax, dots = plot
                for k in range(a.n):
                    pts = sorted(zip(xs[k], ys[k]))      # time order, for the lines
                    dots[k].set_data([p[0] for p in pts], [p[1] for p in pts])
                ax.relim()
                ax.autoscale_view(scalex=False)
                plt.pause(0.01)
            if i % 10 == 0:
                print("delay %6.1f us  (%d/%d, %.0f s)" % (d, i + 1, steps, time.time() - t_start),
                      flush=True)
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()

    rows.sort()
    with open(out + ".csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_us", "delay_us", "cycle_pos", "k", "roi_mean", "n_frames"])
        for r in rows:
            w.writerow(["%.2f" % r[0], "%.2f" % r[1], r[2], r[3], "%.3f" % r[4], r[5]])
    print("%d points in %.0f s; %d lines received, %d lost (%.2f %%), npx != 256 %d, "
          "lines with clipped pixels %d"
          % (len(rows), time.time() - t_start, received, lost,
             100.0 * lost / max(1, received + lost), badnpx, clipped))
    print("saved", out + ".csv")
    if plot:
        plt, fig, ax, dots = plot
        fig.savefig(out + ".png", dpi=120)
        print("saved", out + ".png")
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()
