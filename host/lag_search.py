#!/usr/bin/env python3
"""Find the projector's lag: where on a 5-frame timeline one commanded white frame lands.

    python -u host/lag_search.py COM5

The time-axis version of align_roi.py. The FPGA's impulse mode generates the video
itself: a 5-frame cycle, black except ONE white frame (position 0). Every camera
line carries the pixel the FPGA was transmitting when that exposure was triggered,
so the white frame marks position 0 and each reading can be placed at

    t = position * T + delay         (us after the vsync of the frame that CARRIED white)

on a 5*T (about 41.7 ms) timeline. A projector with no lag lights up inside
position 0, starting near 575 us (the first LED block); one frame of lag puts the
same light at T + 575, and so on. Five frames is the most this can see.

HOW IT NARROWS. First a coarse pass: one exposure (--start, 1000 us) at delays that
tile the frame, read at all five positions at once -- 45 windows covering the whole
timeline. Each window is LIT or DARK against the dark windows at the same delay (the
white frame lights at most two of the five positions, so the per-delay median is
dark). Then the earliest lit window is halved, both halves are read, and the half
holding the first light is kept, down to --min us: that is the ONSET. The last lit
window is halved the same way for the END. Saturation does not matter: every
decision is lit versus dark, never brighter versus dimmer.

A live window shows it happening: top, the whole 5-frame timeline with each coarse
window as it is read (blue lit, amber faint, grey dark) and the window being narrowed
shaded; bottom, a zoom that follows the search, every halving reading drawn at its
light RATE (ADU/us above dark, log scale) against the lit threshold.

Written to lag_search.csv (every window read) and lag_search.png.
"""
import argparse
import collections
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
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,    # noqa: E402
                         EXPO_UNIT_US, TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI,
                         R_IMPCYC, R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2, COLOURS)

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("port", nargs="?", default="COM6")
ap.add_argument("--start", type=float, default=1000.0, help="coarse exposure, us")
ap.add_argument("--min", type=float, default=5.0, help="stop halving below this, us")
ap.add_argument("--level", type=int, default=255, help="level of the one white frame")
ap.add_argument("--colour", "--color", dest="colour", default="white",
                choices=sorted(COLOURS), help="colour of the one lit frame")
ap.add_argument("--frames", type=int, default=40,
                help="camera frames per reading (about a fifth land on each position)")
ap.add_argument("--settle", type=int, default=10)
ap.add_argument("--thresh", type=float, default=6.0,
                help="ADU above the dark reference a window needs, whatever its length")
ap.add_argument("--rate", type=float, default=2.0,
                help="ADU per us above the dark reference that counts as lit. The white "
                     "frame's LED blocks give tens of ADU/us; the dim glow around and "
                     "between them 0.01-0.5, which is reported but not searched")
ap.add_argument("--tile", type=int, default=0,
                help="instead of chasing the two edges, halve the exposure and re-tile the "
                     "WHOLE lit span (first lit window to last) this many times")
ap.add_argument("--overlay", help="a white_sweep.py CSV (delay_us, median) to draw as a "
                                   "light-rate line in the frame where the light was found, "
                                   "to check the edges against a slice sweep")
ap.add_argument("--out", default=os.path.join(ROOT, "lag_search.csv"))
a = ap.parse_args()

ser = serial.Serial(a.port, 115200, timeout=0.05)
time.sleep(0.3)


def R(addr):
    v = rd(ser, addr)
    return 0 if v is None else v


T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
mx = R(0x53) | (R(0x54) << 8)
gl = R(0x5D)
if not (1000 < T < 60000) or (gl & 3) != 3:
    ser.close()
    sys.exit("ABORT: frame %.1f us, genlock 0x%02X -- is video running?" % (T, gl))

# the 5-frame cycle: four black, ONE lit at position 0
lit_px = (((a.level if COLOURS[a.colour] & 4 else 0) << 16)
          | ((a.level if COLOURS[a.colour] & 2 else 0) << 8)
          | (a.level if COLOURS[a.colour] & 1 else 0))
wr(ser, R_IMPCYC, (0 << 4) | 5)
wr(ser, R_IMPLVL, 0)
wr(ser, R_IMPRGB, COLOURS[a.colour])
wr(ser, R_IMPLVL2, a.level & 0xFF)
wr(ser, R_IMPRGB2, COLOURS[a.colour])
wr(ser, R_ROICTL, 0xC0)                  # impulse_en | per-frame stream
time.sleep(0.4)

fh = open(a.out, "w", newline="")
wcsv = csv.writer(fh)
wcsv.writerow(["stage", "expo_us", "delay_us", "position", "t_us", "mean", "sd", "n",
               "ref", "lit"])
cur_units = [None]

# ---- the live UI ----------------------------------------------------------------
# Every reading is drawn as a bar at its light RATE (ADU/us above dark, log scale),
# so a 1000 us window and a 7.8 us one share an axis. Shorter windows are drawn ON
# TOP of longer ones: near an edge they are the only ones with a true rate (a long
# window that clipped reads low), so they stand above it rather than behind it.
plt.ion()
fig = plt.figure(figsize=(13, 8.6))
gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], hspace=0.42, wspace=0.18,
                      left=0.07, right=0.985, top=0.905, bottom=0.07)
ax = fig.add_subplot(gs[0, :])
azs = [fig.add_subplot(gs[1, 0]), fig.add_subplot(gs[1, 1])]
fig.canvas.manager.window.wm_geometry("+40+40")
YLO, YHI = 0.01, 300.0
for axx in [ax] + azs:
    axx.set_yscale("log")
    axx.set_ylim(YLO, YHI)
    axx.axhline(a.rate, color="#d0453a", ls="--", lw=1, zorder=50)
    axx.grid(alpha=.3, which="both")
    axx.set_xlabel("time after the vsync of the frame that carried white (us)")
ax.text(0.003, a.rate * 1.15, "lit above %.1f ADU/us" % a.rate,
        transform=ax.get_yaxis_transform(), color="#d0453a", fontsize=8, va="bottom")
for k in range(1, 5):
    ax.axvline(k * T, color="#888", lw=.8)
ax.set_xlim(0, 5 * T)
for k in range(5):
    ax.text((k + .5) * T, YHI * 1.08, "frame %d%s" % (k, "  (white sent)" if k == 0 else ""),
            ha="center", va="bottom", fontsize=9, color="#555")
ax.set_ylabel("light rate, ADU/us above dark")
azs[0].set_ylabel("light rate, ADU/us above dark")
azs[0].set_title("first light", fontsize=10)
azs[1].set_title("last light", fontsize=10)
span = ax.axvspan(0, 0, color="#d0453a", alpha=.18, lw=0, zorder=0)
hdr = fig.text(0.07, 0.985, "", va="top", ha="left", family="monospace", fontsize=9.5)


def bar(axes, t0, e, r, L, fnt=False):
    """One window as a bar from the floor of the axis up to its rate; shorter on top."""
    z = 2 + max(0.0, 12 - np.log2(max(e, 1.0)))
    col = "#1f5fd0" if L else ("#e0a030" if fnt else "#9aa3ad")
    for axx in axes:
        axx.bar(t0, r - YLO, width=e, bottom=YLO, align="edge", lw=0, color=col,
                alpha=.45 if e >= a.start else .9, zorder=z)


def ui(status=None):
    if status:
        hdr.set_text(status)
    try:
        fig.canvas.draw_idle()
        fig.canvas.flush_events()
    except Exception:
        pass


def rate(m, ref, e):
    return max(0.012, (m - ref) / e)


ui("frame %.1f us   reading the coarse pass..." % T)


def read(delay_us, expo_us, stage):
    """One reading: mean per position at this delay and exposure.
    Returns {position: (mean, sd, n)} and the dark reference (median of positions)."""
    u = max(1, int(round(expo_us / EXPO_UNIT_US)))
    if u > mx:
        sys.exit("ABORT: %.0f us is over the exposure limit" % expo_us)
    if u != cur_units[0]:
        wr(ser, R_EXPO_LO, u & 0xFF)
        wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
        cur_units[0] = u
    e = u * EXPO_UNIT_US
    want = int(round(delay_us / TICK_US))
    set_delay(ser, want)
    got, ok = wait_delay(ser, want)
    if not ok:
        sys.exit("ABORT: delay %.1f us did not land (holds %s)" % (delay_us, got))
    ser.reset_input_buffer()
    collect(ser, a.settle, timeout=8.0)
    by = collections.defaultdict(list)
    last, absn, anchor = None, 0, None
    for mean, npx, tlp, cc in collect(ser, a.frames, timeout=12.0):
        if npx != 256:
            continue
        # unwrap the 8-bit ordinal: 256 is not a multiple of 5
        if last is not None:
            absn += (cc - last) & 0xFF
        last = cc
        if tlp == lit_px:
            anchor = absn
        if anchor is None:
            continue                     # before the first marker: position unknown
        by[(absn - anchor) % 5].append(mean)
    if len(by) < 5 or min(len(v) for v in by.values()) < 2:
        sys.exit("ABORT: positions seen %s at delay %.1f -- raise --frames"
                 % (sorted((k, len(v)) for k, v in by.items()), delay_us))
    res = {k: (float(np.mean(v)), float(np.std(v)), len(v)) for k, v in by.items()}
    ref = float(np.median([m for m, _, _ in res.values()]))
    for k in range(5):
        m, sd, n = res[k]
        wcsv.writerow([stage, round(e, 3), round(delay_us, 2), k, round(k * T + delay_us, 2),
                       round(m, 2), round(sd, 2), n, round(ref, 2), int(lit(m, ref, e))])
    fh.flush()
    return res, ref, e


def lit(m, ref, e):
    """Lit means a RATE of light, so a 5 us and a 1000 us window are judged alike."""
    # a clipped window cannot show its rate, so the bar stops at 60 % of the headroom
    return m - ref > max(a.thresh, min(a.rate * e, 0.6 * (1023 - ref)))


def faint(m, ref):
    return m - ref > a.thresh


windows = []                                       # (t0, t1, mean, ref, lit)
try:
    # ---- coarse pass: tile the frame, all five positions at once ------------
    e0 = a.start
    delays = [d for d in np.arange(0.0, T - e0, e0)] + [T - e0 - 1.0]
    print("frame %.1f us   coarse exposure %.0f us at %d delays -> %d windows over 5 frames"
          % (T, e0, len(delays), 5 * len(delays)), flush=True)
    for d in delays:
        res, ref, e = read(d, e0, "coarse")
        for k in range(5):
            m = res[k][0]
            L = lit(m, ref, e)
            windows.append((k * T + d, k * T + d + e, m, ref, L))
            bar([ax] + azs, k * T + d, e, rate(m, ref, e), L, faint(m, ref))
        ui("coarse pass   %.0f us windows   delay %.0f of %.0f us" % (e0, d, T))
    windows.sort()
    for t0, t1, m, ref, L in windows:
        if L or faint(m, ref):
            print("  %s %8.0f..%8.0f us  (frame %d +%6.0f)  %6.1f ADU vs dark %.1f"
                  % ("lit  " if L else "faint", t0, t1, int(t0 // T), t0 % T, m, ref),
                  flush=True)
    litw = [w for w in windows if w[4]]
    if not litw:
        sys.exit("NO LIGHT FOUND in any window -- is the projector showing the FPGA's video?")

    def tile(litw, e):
        """Halve the exposure and re-tile the WHOLE lit span, first lit window to last,
        --tile times. Every delay read covers all five frames, so a span longer than a
        frame simply re-tiles the whole frame."""
        for it in range(1, a.tile + 1):
            t_lo, t_hi = litw[0][0], max(w[1] for w in litw)
            e = e / 2
            if t_hi - t_lo >= T:
                ds = list(np.arange(0.0, T - e, e))
            else:
                ds = sorted({round(t % T, 3) for t in np.arange(t_lo, t_hi, e)})
            ds = sorted({min(d, T - e - 1.0) for d in ds})
            span.set_x(t_lo)
            span.set_width(t_hi - t_lo)
            print("  tile %d: %.1f us windows over %.0f..%.0f us -> %d delays"
                  % (it, e, t_lo, t_hi, len(ds)), flush=True)
            new = []
            for i, d in enumerate(ds):
                res, ref, ee = read(d, e, "tile%d" % it)
                for k in range(5):
                    t = k * T + d
                    m = res[k][0]
                    L = lit(m, ref, ee)
                    bar([ax] + azs, t, ee, rate(m, ref, ee), L, faint(m, ref))
                    if t_lo - ee < t < t_hi:
                        new.append((t, t + ee, m, ref, L))
                if i % 4 == 0:
                    ui("tile %d of %d   %.1f us windows   delay %d of %d"
                       % (it, a.tile, ee, i + 1, len(ds)))
            new.sort()
            lw = [w for w in new if w[4]]
            if not lw:
                print("     nothing lit at %.1f us -- keeping the previous span" % e, flush=True)
                break
            litw = lw
            print("     lit from %.1f to %.1f us" % (litw[0][0], max(w[1] for w in litw)),
                  flush=True)
        last = max(litw, key=lambda w: w[1])
        return litw[0][0], litw[0][1] - litw[0][0], last[0], last[1] - last[0]

    def narrow(t0, e, want_first):
        """Halve [t0, t0+e] toward the first (or last) light, down to --min."""
        while e / 2 >= a.min:
            h = e / 2
            k = int(t0 // T)
            d = t0 - k * T
            halves = []
            span.set_x(t0)
            span.set_width(e)
            span.set_color("#d0453a" if want_first else "#9a6614")
            az = azs[0 if want_first else 1]
            az.set_xlim(t0 - 0.25 * e, t0 + 1.25 * e)
            az.set_title("%s search: %.1f us window at %.1f us (frame %d, +%.1f)"
                         % ("first-light" if want_first else "last-light", e, t0,
                            k, t0 - k * T), fontsize=10)
            for dd in (d, d + h):
                res, ref, ee = read(dd, h, "onset" if want_first else "end")
                halves.append((k * T + dd, res[k][0], ref, ee))
                L = lit(res[k][0], ref, ee)
                bar([ax, az], k * T + dd, ee, rate(res[k][0], ref, ee), L)
                ui("%s search   %.1f us halves   reading %.1f us"
                   % ("first-light" if want_first else "last-light", h, k * T + dd))
            (ta, ma, ra, ea), (tb, mb, rb, eb) = halves
            la, lb = lit(ma, ra, ea), lit(mb, rb, eb)
            print("  %s  %7.1f us window  halves %.0f:%s  %.0f:%s"
                  % ("onset" if want_first else "end  ", h, ta, "LIT" if la else "dark",
                     tb, "LIT" if lb else "dark"), flush=True)
            if want_first:
                if la:
                    t0 = ta
                elif lb:
                    t0 = tb
                else:
                    print("     neither half lit -- the edge is at the halves' seam", flush=True)
                    return tb, h
            else:
                if lb:
                    t0 = tb
                elif la:
                    t0 = ta
                else:
                    print("     neither half lit -- the edge is at the halves' seam", flush=True)
                    return ta, h
            e = h
        return t0, e

    # the onset: earliest lit window. Windows at the frame's last delay overlap the
    # one before; the earliest start is what counts.
    if a.tile:
        on_t, on_e, end_t, end_e = tile(litw, e0)
    else:
        f = litw[0]
        on_t, on_e = narrow(f[0], f[1] - f[0], True)
        l_ = max(litw, key=lambda w: w[1])
        end_t, end_e = narrow(l_[0], l_[1] - l_[0], False)
finally:
    wr(ser, R_ROICTL, 0x00)
    set_delay(ser, 0)
    ser.close()
    fh.close()

onset = on_t
end = end_t + end_e
print("\nLAG FOUND")
print("  first light  %.1f us after the white frame's vsync   (+- %.1f)"
      % (onset, on_e), flush=True)
print("               = frame %d of the cycle, %.1f us into it" % (int(onset // T), onset % T))
print("  last light   %.1f us  (+- %.1f)  = frame %d, %.1f us into it"
      % (end, end_e, int(end // T), end % T))
print("  light spans  %.1f us" % (end - onset))
print("  frame lag    %d frame(s), if the first LED block starts ~575 us into a frame"
      % int(round((onset - 575.0) / T)))
print("wrote %s" % a.out, flush=True)

span.set_width(0)
if a.overlay:
    # the sweep's delay is within ONE frame; put it in the frame the light landed in
    ov = list(csv.DictReader(open(a.overlay)))
    od = np.array([float(x["delay_us"]) for x in ov])
    oy = np.array([float(x["median"]) for x in ov])
    oe = float(np.median(np.diff(od)))                  # slices tile: step = exposure
    ofl = float(np.median(oy[(od > 7000) & (od < 7900)]))
    orate = np.maximum((oy - ofl) / oe, YLO)
    ox = od + int(onset // T) * T
    for axx in [ax] + azs:
        axx.step(ox, orate, where="post", color="black", lw=.9, zorder=55,
                 label=("%s (%.2f us slices, floor %.0f), placed in frame %d"
                        % (os.path.basename(a.overlay), oe, ofl, int(onset // T))
                        if axx is ax else None))
for axx in [ax] + azs:
    axx.axvline(onset, color="#d0453a", lw=1.2, zorder=60)
    axx.axvline(end, color="#9a6614", lw=1.2, zorder=60)
ax.plot([], [], color="#d0453a", label="first light %.1f us (frame %d, +%.1f)"
        % (onset, int(onset // T), onset % T))
ax.plot([], [], color="#9a6614", label="last light %.1f us (frame %d, +%.1f)"
        % (end, int(end // T), end % T))
ax.legend(fontsize=9, loc="upper right")
azs[0].set_xlim(onset - 150, onset + 150)
azs[0].set_title("first light %.1f us: frame %d, +%.1f  (+- %.1f)"
                 % (onset, int(onset // T), onset % T, on_e), fontsize=10)
azs[1].set_xlim(end - 150, end + 150)
azs[1].set_title("last light %.1f us: frame %d, +%.1f  (+- %.1f)"
                 % (end, int(end // T), end % T, end_e), fontsize=10)
ui("LAG: first light %.1f us, last light %.1f us after the white frame's vsync  ->  "
   "%d frame(s) late\nclose the window to exit" % (onset, end, int(round((onset - 575.0) / T))))
fig.savefig(os.path.splitext(a.out)[0] + ".png", dpi=110)
plt.ioff()
plt.show()
