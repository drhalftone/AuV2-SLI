#!/usr/bin/env python3
"""The 5-frame delay sweep as functions, shared by host/lag_sweep.py and host/sli_ui.py.

See lag_sweep.py for what the sweep measures. This module only measures: no plotting, no
argparse, no sys.exit -- failures raise RuntimeError so a UI can show them.

    ser, T, mx = preflight("COM5", r"\\\\.\\DISPLAY5")
    e = start_impulse(ser, level=255, bg=254, expo=5.0, mx=mx)
    for i, n, d, means, ref in sweep(ser, T, e, delays, ...):
        ...
    stop_impulse(ser)
"""
import collections
import csv
import os
import time

import numpy as np

from align_roi import displays, set_mode
from frame_sweep import (wr, rd, set_delay, wait_delay, collect, EXPO_UNIT_US,  # noqa
                         TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC, R_IMPLVL,
                         R_IMPRGB, R_IMPLVL2, R_IMPRGB2)

MODE = (800, 600, 120)
ACTIVE_US = 432.5          # 800x600@120: vsync -> first active pixel, 33 lines


def projector_display():
    """The one display not at the desktop origin, or None."""
    proj = [d for d in displays() if (d[1], d[2]) != (0, 0)]
    return proj[0][0] if len(proj) == 1 else None


def ensure_mode(dev, log=print):
    """Put display `dev` at 800x600@120 if it is not already."""
    cur = [d for d in displays() if d[0] == dev]
    if not cur:
        raise RuntimeError("display %s is not attached" % dev)
    if tuple(cur[0][3:]) == MODE:
        return
    ok, msg = set_mode(dev, *MODE)
    log("display %s was %dx%d@%d -> %dx%d@%d: %s" % (dev, *cur[0][3:], *MODE, msg))
    if not ok:
        raise RuntimeError("could not set %s to %dx%d@%d: %s" % (dev, *MODE, msg))
    time.sleep(3)                                    # let the FPGA re-measure the frame


def reg(ser, addr):
    v = rd(ser, addr)
    return 0 if v is None else v


def genlock_on(ser, log=print):
    """Turn genlock on over the Ft+ if it is off. gl_en resets to 0 on the merged build."""
    if reg(ser, 0x5D) & 1:
        return
    from ftlink import FtLink
    ft = FtLink()
    ft.send_word((7 << 28) | (1 << 27))              # opcode 7: gl_en = 1, delay 0
    time.sleep(0.3)
    ft.close()
    log("genlock was off -> on: 0x5D = 0x%02X" % reg(ser, 0x5D))


def preflight(port, display, log=print):
    """Display mode, serial port, genlock. Returns (open serial, frame period us, max expo units)."""
    import serial
    ensure_mode(display, log)
    ser = serial.Serial(port, 115200, timeout=0.05)
    time.sleep(0.3)
    try:
        genlock_on(ser, log)
        T = (reg(ser, 0x4A) | (reg(ser, 0x4B) << 8) | (reg(ser, 0x4C) << 16)) * TICK_US
        mx = reg(ser, 0x53) | (reg(ser, 0x54) << 8)
        gl = reg(ser, 0x5D)
        if not (1000 < T < 60000) or (gl & 3) != 3:
            raise RuntimeError("frame %.1f us, genlock 0x%02X -- is video running?" % (T, gl))
    except Exception:
        ser.close()
        raise
    return ser, T, mx


def start_impulse(ser, level, bg, expo, mx):
    """5-frame cycle: `level` at position 0, `bg` at 1-4, all LEDs. Returns the real exposure."""
    u = max(1, int(round(expo / EXPO_UNIT_US)))
    if u > mx:
        raise RuntimeError("%.2f us is over the exposure limit" % expo)
    wr(ser, R_IMPCYC, 5)                             # 5-frame cycle, odd frame at position 0
    wr(ser, R_IMPLVL, bg & 0xFF)
    wr(ser, R_IMPRGB, 0x07)
    wr(ser, R_IMPLVL2, level & 0xFF)
    wr(ser, R_IMPRGB2, 0x07)
    wr(ser, R_ROICTL, 0xC0)                          # impulse_en | per-frame stream
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    time.sleep(0.4)
    return u * EXPO_UNIT_US


def stop_impulse(ser):
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)


def delays_for(T, e, step, dmin=0.0, dmax=None):
    top = T - e - 1.0 if dmax is None else min(dmax, T - e - 1.0)
    return list(np.arange(dmin, top, step))


def positions(rows, d, lit_px, active_us=ACTIVE_US):
    """Camera lines at one delay -> {position: [ROI means]}."""
    by = collections.defaultdict(list)
    # FRAME NUMBERING. The profiling bitstream counts frames in `cc`; the merged one
    # sends cc = 0 on every line. There, the per-frame stream still delivers one line
    # per frame, so the line's ARRIVAL ORDER is the frame number -- and the anchor
    # check below catches a dropped one.
    use_cc = any((rows[k][3] - rows[k - 1][3]) & 0xFF for k in range(1, len(rows)))
    absns, absn = [], 0
    for k, r in enumerate(rows):
        if k:
            absn += ((r[3] - rows[k - 1][3]) & 0xFF) if use_cc else 1
        absns.append(absn)
    # THE WHITE FRAME'S TAG. The merged build latches only RED of the transmitted
    # pixel (build_merged.tcl: cam_frame_ft takes 23:16), so white reads FF0000 there
    # and FFFFFF on the profiling bitstream.
    marks = [n for n, r in zip(absns, rows) if r[2] in (lit_px, lit_px & 0xFF0000)]
    # Re-anchor on EVERY white frame, and drop any cycle whose marks are not exactly 5
    # apart: a missed line then costs one cycle, instead of shifting every later
    # reading onto the wrong position.
    for n, r in zip(absns, rows):
        prev = [m for m in marks if m <= n]
        nxt = [m for m in marks if m > n]
        if not prev or n - prev[-1] > 4:
            continue
        if nxt and nxt[0] - prev[-1] != 5:
            continue
        # A trigger before the first active pixel carries the previous frame's tag:
        # it belongs one position later (4 -> 0 of the next cycle).
        by[(n - prev[-1] + (d < active_us)) % 5].append(r[0])
    return by


CSV_HEAD = ["T_us", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n", "ref",
            "tag_fix_us"]


def sweep(ser, T, e, delays, out, level=255, bg=0, frames=20, settle=4,
          active_us=ACTIVE_US, stop=None, log=print):
    """Step the delay; per delay kept, yield (i, len(delays), delay, [5 means], ref, rows).

    `rows` are the five CSV_HEAD rows for that delay. Every reading is also written to
    `out` as it arrives (None: no file). `stop` is an optional threading.Event checked
    between delays. The caller still owns `ser` and should call stop_impulse() afterwards.
    """
    lit_px = (level << 16) | (level << 8) | level
    with open(out or os.devnull, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_HEAD)
        for i, d in enumerate(delays):
            if stop is not None and stop.is_set():
                return
            want = int(round(d / TICK_US))
            set_delay(ser, want)
            if i % 25 == 0:
                wait_delay(ser, want)
            ser.reset_input_buffer()
            collect(ser, settle, timeout=4)
            rows = [r for r in collect(ser, frames, timeout=6) if r[1] == 256]
            by = positions(rows, d, lit_px, active_us)
            if len(by) < 5:
                log("  delay %.1f: positions seen %s -- skipped"
                    % (d, sorted((k, len(v)) for k, v in by.items())))
                continue
            means = [float(np.mean(by[k])) for k in range(5)]
            ref = 0.0 if bg else float(np.median(means))
            rows = [[round(T, 2), round(e, 3), round(d, 2), k, round(k * T + d, 2),
                     round(means[k], 3), round(float(np.std(by[k])), 3), len(by[k]),
                     round(ref, 3), active_us] for k in range(5)]
            w.writerows(rows)
            if i % 10 == 0:
                fh.flush()
            yield i, len(delays), d, means, ref, rows
