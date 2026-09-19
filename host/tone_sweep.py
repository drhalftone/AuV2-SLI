#!/usr/bin/env python3
"""16-level tone-curve sweep for ONE colour filter: a white square, corrected by a LUT.

    python -u host/tone_sweep.py                       # LUT report_figures/data/blue_lut_v3.csv
    python -u host/tone_sweep.py --lut my_lut.csv --tag green1

The scene is a white field with a 184x184 hole open at the bottom edge and a 32x32
square in it, commanded R=G=B=LUT[W]. Each level is 416 delays of 20 us slices that
tile the frame; each delay is the MEDIAN of --frames camera lines (a mean lets one
glitched line drag a point). Levels are visited in bisection order (0, 255, then
midpoints) so elapsed time cannot masquerade as level. After every level the trace is
floor-aligned to the first one (tone_analysis.icp_floor_offset) and the aligned total
is drawn live beside the raw one. Traces go to tone_slices_420_<tag>_L###.csv, the
level-0 repeat to ..._L000r.csv, and the summary to --out. Analyse afterwards with
host/tone_analysis.py.
"""
import sys, csv, time, argparse
import os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np, serial
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from screen import Screen
from tone_analysis import icp_floor_offset
from frame_sweep import (wr, rd, set_delay, wait_delay, collect, EXPO_UNIT_US, TICK_US,
                         R_ROICTL, R_EXPO_LO, R_EXPO_HI)

ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser()
ap.add_argument("--expo", type=float, default=20.0)
ap.add_argument("--levels", type=int, default=16)
ap.add_argument("--notch-w", type=int, default=184)
ap.add_argument("--notch-h", type=int, default=184)
ap.add_argument("--frames", type=int, default=6)
ap.add_argument("--settle", type=int, default=4)
ap.add_argument("--lut", default=os.path.join(ROOT, "report_figures", "data", "blue_lut_v3.csv"))
ap.add_argument("--out", default=os.path.join(ROOT, "tone_sweep.csv"))
ap.add_argument("--tag", default="sweep")
a = ap.parse_args()
CX, SQ = 384, 32


def dither_order(values):
    """Visit endpoints first, then keep bisecting the remaining gaps: 0, MAX,
    mid, quarters, eighths, ... instead of sequential low-to-high.

    WHY. A sequential sweep makes elapsed time and commanded level PERFECTLY
    confounded -- level 0 is always measured first, level 255 always last, so a
    slow drift (thermal settling after a reflash, ambient light, whatever) looks
    EXACTLY like a level-dependent effect and there is no way to tell them apart
    after the fact (this is what made the first LUT-check run uninterpretable:
    corr(raw, level) and corr(raw, elapsed_time) were both -0.946, identically).
    Bisection spreads the level values evenly across the whole run instead, so a
    real drift shows up as scatter around the level-vs-light curve rather than
    as its own fake slope.
    """
    n = len(values)
    order, seen = [], set()

    def emit(i):
        if i not in seen:
            seen.add(i); order.append(i)

    emit(0)
    if n > 1:
        emit(n - 1)
    intervals = [(0, n - 1)]
    while len(seen) < n:
        nxt = []
        for lo, hi in intervals:
            if hi - lo > 1:
                mid = (lo + hi) // 2
                emit(mid)
                nxt.append((lo, mid)); nxt.append((mid, hi))
        if not nxt:
            break
        intervals = nxt
    for i in range(n):
        emit(i)
    return [values[i] for i in order]


levels_seq = [int(round(i * 255.0 / (a.levels - 1))) for i in range(a.levels)]
levels = dither_order(levels_seq) + [0]   # trailing 0 is still the drift-check repeat

lut = [0] * 256
with open(a.lut, newline="") as f:
    for row in csv.DictReader(f):
        lut[int(row["level_wanted"])] = int(row["level_to_command_blue"])
print("loaded LUT from", a.lut, "-- sample 0,64,128,192,255 ->",
      [lut[i] for i in (0, 64, 128, 192, 255)], flush=True)


def build_corrected(s, notch_w, notch_h, level, size, bg=255, cx=384):
    buf = s.blank()
    buf[:, :] = bg
    x0 = max(0, cx - notch_w // 2)
    buf[s.h - notch_h:s.h, x0:x0 + notch_w] = 0
    sx = max(0, cx - size // 2)
    buf[s.h - size:s.h, sx:sx + size, 0] = lut[level]
    buf[s.h - size:s.h, sx:sx + size, 1] = lut[level]
    buf[s.h - size:s.h, sx:sx + size, 2] = lut[level]
    return buf


plt.ion()
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), gridspec_kw={"height_ratios": [2, 3]})
cmap = plt.get_cmap("viridis")
ax1.set_xlabel("trigger delay (us)")
ax1.set_ylabel("ROI MEDIAN per %.0f us slice" % a.expo)
ax1.grid(alpha=0.3)
lc, = ax2.plot([], [], lw=1.6, color="#1a8f3c", marker="o", ms=5, label="sum(median), no floor")
lr, = ax2.plot([], [], ls="", marker="s", ms=7, mfc="none", color="#111", label="level 0 repeat")
la, = ax2.plot([], [], lw=1.6, color="#1f5fd0", marker="o", ms=5, label="floor-aligned (ICP to first trace)")
# PREDICTED CURVE: drawn once both endpoints (W=0 and W=255) are measured -- a
# straight line between this run's own floor-aligned totals.
pred, = ax2.plot([], [], ls="--", lw=1.6, color="#d0242a",
                 label="predicted: straight line, this run's W=0 to W=255")
ax2.set_xlabel("NOMINAL level (R=G=B=lut[level])")
ax2.set_ylabel("sum(median) over the whole frame")
ax2.set_xlim(-5, 260); ax2.grid(alpha=0.3); ax2.legend(loc="upper left", fontsize=9)
ttl = fig.suptitle("")
fig.canvas.manager.window.wm_geometry("+40+40")
fig.tight_layout(); plt.pause(0.1)

s = Screen(set_mode_first=True)


def scene(level):
    s.show(build_corrected(s, a.notch_w, a.notch_h, level, SQ, 255, CX), settle=1.2)


ser = serial.Serial("COM6", 115200, timeout=0.05); time.sleep(0.4)


def R(addr):
    v = rd(ser, addr)
    return 0 if v is None else v


T = (R(0x4A) | (R(0x4B) << 8) | (R(0x4C) << 16)) * TICK_US
mx = R(0x53) | (R(0x54) << 8)
if (R(0x5D) & 3) != 3 or abs(1e6 / T - 120) > 1 or not (R(0x55) & 0x80):
    sys.exit("ABORT: frame %.1f us genlock 0x%02X" % (T, R(0x5D)))
wr(ser, 0x13, 0x00); wr(ser, 0x16, 0x00); wr(ser, R_ROICTL, 0x40)
u = max(1, int(round(a.expo / EXPO_UNIT_US)))
if u > mx:
    sys.exit("ABORT: over the exposure limit")
wr(ser, R_EXPO_LO, u & 0xFF); wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
expo = u * EXPO_UNIT_US
delays = [i * a.expo for i in range(int(T // a.expo))]
ax1.set_xlim(0, T)
print("frame %.1f us   slice %.2f us   %d delays per level   %d levels   MEDIAN of %d"
      % (T, expo, len(delays), len(levels), a.frames), flush=True)
print("scene: white upside-down U, notch %dx%d open at the bottom, "
      "%dx%d target -- R=G=B=LUT(nominal)"
      % (a.notch_w, a.notch_h, SQ, SQ), flush=True)

fh = open(a.out, "w", newline="")
w = csv.writer(fh)
w.writerow(["level", "blue_commanded", "sum_median", "peak", "sat_px", "n_slices", "t_s", "kind",
            "icp_shift", "aligned_sum"])
lx, ly, rx, ry, t0 = [], [], [], [], time.time()
ax_, ay_ = [], []      # aligned series
ref_trace = None       # the first-measured trace is the floor reference
try:
    for li_i, L in enumerate(levels):
        scene(L)
        ln, = ax1.plot([], [], lw=1.0, color=cmap(L / 255.0), label="L=%d" % L)
        xs, ys, sat = [], [], 0
        for i, d in enumerate(delays):
            t = int(round(d / TICK_US)); set_delay(ser, t)
            if i % 40 == 0:
                wait_delay(ser, t)
            ser.reset_input_buffer(); collect(ser, a.settle, timeout=3)
            rows = [r for r in collect(ser, a.frames, timeout=5, sat=True) if r[1] == 256]
            if len(rows) < 3:
                sys.exit("ABORT: %d ROI lines at level %d delay %.0f" % (len(rows), L, d))
            m = [r[0] for r in rows]
            y = float(np.median(m))
            ys.append(y); xs.append(d)
            sat = max(sat, max(r[4] for r in rows))
            if i % 8 == 0:
                ln.set_data(xs, ys); ax1.relim(); ax1.autoscale_view(scalex=False)
                if li_i == 0 and i == 0:
                    ax1.legend(loc="upper right", fontsize=7, ncol=3)
                ttl.set_text("level %d (%d/%d)   slice %d/%d   %.1f min elapsed   max sat px %d"
                             % (L, li_i + 1, len(levels), i + 1, len(delays),
                                (time.time() - t0) / 60, sat))
                try:
                    fig.canvas.draw_idle(); fig.canvas.flush_events()
                except Exception:
                    pass
        y = np.array(ys)
        total = float(y.sum())
        last = li_i == len(levels) - 1
        trace = np.c_[np.array(xs), y]
        if ref_trace is None:
            ref_trace = trace
        shift = icp_floor_offset(ref_trace, trace)
        aligned = total + shift * len(y)
        w.writerow([L, lut[L], round(total, 2), round(float(y.max()), 2), sat, len(y),
                    round(time.time() - t0, 1), "repeat" if last else "sweep",
                    round(shift, 4), round(aligned, 2)])
        fh.flush()
        np.savetxt(os.path.join(ROOT, "tone_slices_420_%s_L%03d%s.csv" % (a.tag, L, "r" if last else "")), trace,
                   delimiter=",", header="delay_us,median", comments="")
        if last:
            rx.append(L); ry.append(total)
            lr.set_data(rx, ry)
        else:
            lx.append(L); ly.append(total)
            ax_.append(L); ay_.append(aligned)
            if 0 in ax_ and 255 in ax_:
                pred.set_data([0, 255], [ay_[ax_.index(0)], ay_[ax_.index(255)]])
            _p = sorted(range(len(ax_)), key=lambda k: ax_[k]); la.set_data([ax_[k] for k in _p], [ay_[k] for k in _p])
            _o = sorted(range(len(lx)), key=lambda k: lx[k]); lc.set_data([lx[k] for k in _o], [ly[k] for k in _o])
        ax2.relim(); ax2.autoscale_view(scalex=False)
        ax1.legend(loc="upper right", fontsize=7, ncol=3)
        print("  level %3d (blue commanded %3d): sum(median) %9.1f  aligned %9.1f (shift %+.2f)   peak %6.1f   sat %d   (%.1f min)"
              % (L, lut[L], total, aligned, shift, y.max(), sat, (time.time() - t0) / 60), flush=True)
        try:
            fig.canvas.draw_idle(); fig.canvas.flush_events()
        except Exception:
            pass
finally:
    wr(ser, R_ROICTL, 0x00); set_delay(ser, 0); ser.close(); fh.close(); s.close()

ttl.set_text("LUT-corrected blue tone curve (median, no floor), %.0f us slices" % expo)
fig.tight_layout(); fig.savefig(os.path.join(ROOT, "tone_sweep.png"), dpi=120)
print("done: level 0 %.1f at the start, %.1f at the end" % (ly[0] if ly else 0,
                                                            ry[0] if ry else 0), flush=True)
plt.ioff(); plt.show()
