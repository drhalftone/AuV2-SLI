#!/usr/bin/env python3
"""Which frame's grey level does a reading at a given delay follow?

    python -u host/gray_pair.py [COM5] --delays 6500 7000 --expo 5

THE QUESTION. A reading taken late in frame k may carry the image on screen (sent in
frame k-1: the display lags one frame) or the image about to replace it (sent in frame
k). Uniform grey fields make the two separable.

HOW. The impulse generator alternates two full-field levels, A B A B ... (IMPCYC = 2:
the frame at bpos 0 takes IMPLVL2 = A, the other IMPLVL = B). Every frame then has a
known level and a known predecessor, and the top-left pixel names it (the merged build
tags with red only, so tlp = level << 16). Each unordered pair {A, B} from --levels
gives two (previous, this) combinations; A = B gives the diagonal.

For each delay the reading is fitted as

    mean = c + a * previous_level + b * this_level

-- a large a means the delay sees the image on screen, a large b the upcoming one.
"""
import argparse
import csv
import itertools
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import lag_core as lc                                                   # noqa: E402
from frame_sweep import (LINE, R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC,  # noqa: E402
                         R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2)
from frame_sweep import SYNC, OP_W, ck                                  # noqa: E402
from sli_background import spin                                         # noqa: E402


def lines(ser, n, timeout=3.0):
    """n camera lines as (mean, npx, level-from-tag, nhi)."""
    out, buf, t0 = [], b"", time.time()
    ser.timeout = 0
    while len(out) < n and time.time() - t0 < timeout:
        c = ser.read(4096)
        if not c:
            spin(0.0005)
            continue
        buf += c
        ms = list(LINE.finditer(buf))
        for m in ms:
            out.append((int(m.group(1), 16), int(m.group(2), 16),
                        (int(m.group(3), 16) >> 16) & 0xFF,
                        int(m.group(5), 16) if m.group(5) else 0))
        if ms:
            buf = buf[ms[-1].end():]
    ser.timeout = 0.05
    return out


def plot_saved(path):
    """The heat maps of a saved run, log colour scale, in a window and a PNG."""
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    rows = list(csv.DictReader(open(path)))
    L = sorted({int(r["prev_level"]) for r in rows} | {int(r["this_level"]) for r in rows})
    D = sorted({float(r["delay_us"]) for r in rows})
    vals = [float(r["mean"]) for r in rows]
    norm = LogNorm(vmin=max(1.0, min(vals)), vmax=max(vals))
    fig, axs = plt.subplots(1, len(D), figsize=(6.2 * len(D), 6), squeeze=False)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.86, bottom=0.12, wspace=0.3)
    for ax, d in zip(axs[0], D):
        g = np.full((len(L), len(L)), np.nan)
        for r in rows:
            if float(r["delay_us"]) == d:
                g[L.index(int(r["prev_level"])), L.index(int(r["this_level"]))] = float(r["mean"])
        im = ax.imshow(g, origin="lower", cmap="magma", norm=norm, aspect="auto",
                       interpolation="nearest")
        for i in range(len(L) if len(L) <= 16 else 0):
            for j in range(len(L)):
                if np.isfinite(g[i, j]):
                    ax.text(j, i, "%.0f" % g[i, j], ha="center", va="center", fontsize=7,
                            color="white" if g[i, j] < np.sqrt(norm.vmin * norm.vmax) else "black")
        step = max(1, len(L) // 8)
        ax.set_xticks(range(0, len(L), step))
        ax.set_xticklabels(L[::step], fontsize=8)
        ax.set_yticks(range(0, len(L), step))
        ax.set_yticklabels(L[::step], fontsize=8)
        ax.set_xlabel("level sent in THIS frame (upcoming image)")
        ax.set_ylabel("level sent in the PREVIOUS frame (image on screen)")
        ax.set_title("delay %.0f us" % d, fontsize=10, pad=10)
        fig.colorbar(im, ax=ax, label="ROI mean (ADU, log scale)")
    fig.suptitle(path, fontsize=9)
    png = path.rsplit(".", 1)[0] + "_log.png"
    fig.savefig(png, dpi=120)
    print("wrote %s" % png)
    plt.show()


class Stream:
    """Camera lines one at a time, as (mean, npx, level-from-tag, nhi)."""

    def __init__(self, ser):
        self.ser, self.buf, self.q = ser, b"", []

    def get(self, timeout=0.5):
        t0 = time.time()
        while not self.q:
            if time.time() - t0 > timeout:
                return None
            c = self.ser.read(4096)
            if not c:
                spin(0.0005)
                continue
            self.buf += c
            ms = list(LINE.finditer(self.buf))
            for m in ms:
                self.q.append((int(m.group(1), 16), int(m.group(2), 16),
                               (int(m.group(3), 16) >> 16) & 0xFF,
                               int(m.group(5), 16) if m.group(5) else 0))
            if ms:
                self.buf = self.buf[ms[-1].end():]
            elif len(self.buf) > 8192:
                self.buf = self.buf[-64:]
        return self.q.pop(0)


def wr_now(ser, addr, val):
    """A register write without frame_sweep.wr's 20 ms sleep (the ACK is ignored)."""
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default=None)
    ap.add_argument("--display", default=None)
    ap.add_argument("--delays", type=float, nargs="+", default=[6500.0, 7000.0])
    ap.add_argument("--expo", type=float, default=5.0)
    ap.add_argument("--windows", nargs="+", metavar="START:END",
                    help="integrate whole windows instead, e.g. 6300:6700 6700:7100 (us "
                         "into the frame): delay = START, exposure = END - START. "
                         "Replaces --delays/--expo")
    ap.add_argument("--levels", type=int, nargs="+", default=list(range(256)),
                    help="grey levels to pair (default: every 8-bit level)")
    ap.add_argument("--prev", type=int, default=None, metavar="LEVEL",
                    help="measure one row only: the previous frame fixed at LEVEL, this "
                         "frame through every --levels value (live view is a line plot)")
    ap.add_argument("--prev-levels", type=int, nargs="+", default=None, metavar="LEVEL",
                    help="measure these rows only: each previous level held while this "
                         "frame goes through every --levels value, e.g. $(seq 0 32)")
    ap.add_argument("--per", type=int, default=10, help="samples averaged per (prev, this) cell")
    ap.add_argument("--skip", type=int, default=2,
                    help="extra frames dropped after the new levels show in the tag")
    ap.add_argument("--out", default="gray_pair_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S"))
    ap.add_argument("--resume", action="store_true",
                    help="append to --out and skip cells it already holds")
    ap.add_argument("--plot", metavar="CSV", help="redraw a saved run instead of measuring")
    a = ap.parse_args()
    if a.plot:
        return plot_saved(a.plot)

    import serial.tools.list_ports as lp
    port = a.port or ([p.device for p in lp.comports()] + ["COM6"])[0]
    ser, T, mx = lc.preflight(port, a.display or lc.OFFLINE)
    # per-delay exposure units. A window rounds DOWN so it never runs into the next.
    if a.windows:
        win = [tuple(float(v) for v in w_.split(":")) for w_ in a.windows]
        a.delays = [w_[0] for w_ in win]
        units = {w_[0]: max(1, int((w_[1] - w_[0]) / lc.EXPO_UNIT_US)) for w_ in win}
    else:
        units = {d: max(1, int(round(a.expo / lc.EXPO_UNIT_US))) for d in a.delays}
    if max(units.values()) > mx:
        sys.exit("an exposure is over the %.1f us limit" % (mx * lc.EXPO_UNIT_US))
    expo = {d: units[d] * lc.EXPO_UNIT_US for d in a.delays}
    e = expo[a.delays[0]]
    rows = a.prev_levels
    L = sorted(set(a.levels) | ({a.prev} if a.prev is not None else set()) | set(rows or ()))
    if a.prev is not None:                           # one row: A = prev held, B swept
        pairs = [(a.prev, B) for B in L]
    elif rows:                                       # some rows: each A held, B swept
        pairs = [(A, B) for A in sorted(rows) for B in sorted(a.levels)]
    else:
        pairs = list(itertools.combinations_with_replacement(L, 2))
    idx = {lv: i for i, lv in enumerate(L)}

    done = set()
    grids = {d: np.full((len(L), len(L)), np.nan) for d in a.delays}
    if a.resume:
        try:
            for r in csv.DictReader(open(a.out)):
                d, pl, tl = float(r["delay_us"]), int(r["prev_level"]), int(r["this_level"])
                done.add((d, pl, tl))
                if d in grids and pl in idx and tl in idx:
                    grids[d][idx[pl], idx[tl]] = float(r["mean"])
        except FileNotFoundError:
            pass
    one = a.prev if a.prev is not None else (0 if rows else None)   # one direction only
    todo = {d: [(A, B) for A, B in pairs
                if (d, A, B) not in done or (one is None and (d, B, A) not in done)]
            for d in a.delays}
    n_todo = sum(len(v) for v in todo.values())
    est = n_todo * ((1 if one is not None else 2) * 2 * a.per + a.skip + 3) / (1e6 / T) / 3600
    print("frame %.2f us   %s   %d levels   %d pairs to do (%d cells already done)   ~%.1f h"
          % (T, "   ".join("%.0f us + %.3f us" % (d, expo[d]) for d in a.delays), len(L),
             n_todo, len(done), est))

    # ---- live plot: one heat map per delay, previous level x this level ---------------
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm
    plt.ion()
    nd = len(a.delays)
    fig, axs = plt.subplots(1, nd, figsize=(6.4 * nd, 6.2), squeeze=False)
    axs = axs[0]
    fig.subplots_adjust(left=0.08, right=0.97, top=0.85, bottom=0.11, wspace=0.3)
    ims, lines = {}, {}
    step = max(1, len(L) // 8)
    for ax, d in zip(axs, a.delays):
        if a.prev is not None:
            lines[d], = ax.plot(L, grids[d][idx[a.prev]], ".-", ms=3, lw=0.8)
            ax.set_xlim(L[0], L[-1])
            ax.grid(alpha=0.3)
            ax.set_xlabel("level sent in THIS frame (upcoming image)")
            ax.set_ylabel("ROI mean (ADU)")
            ax.set_title("%.0f .. %.0f us into the frame (exposure %.3f us), previous level %d"
                         % (d, d + expo[d], expo[d], a.prev), fontsize=10, pad=10)
            continue
        im = ax.imshow(grids[d], origin="lower", cmap="magma", aspect="auto",
                       norm=LogNorm(vmin=10, vmax=1000), interpolation="nearest")
        ax.set_xticks(range(0, len(L), step))
        ax.set_xticklabels(L[::step], fontsize=8)
        ax.set_yticks(range(0, len(L), step))
        ax.set_yticklabels(L[::step], fontsize=8)
        if rows:                                     # show only the rows measured
            ax.set_ylim(idx[min(rows)] - 0.5, idx[max(rows)] + 0.5)
            ax.set_yticks(range(idx[min(rows)], idx[max(rows)] + 1, max(1, len(rows) // 8)))
            ax.set_yticklabels(sorted(rows)[::max(1, len(rows) // 8)], fontsize=8)
        ax.set_xlabel("level sent in THIS frame (upcoming image)")
        ax.set_ylabel("level sent in the PREVIOUS frame (image on screen)")
        ax.set_title("%.0f .. %.0f us into the frame (exposure %.3f us)"
                     % (d, d + expo[d], expo[d]), fontsize=10, pad=10)
        fig.colorbar(im, ax=ax, label="ROI mean (ADU, log scale)")
        ims[d] = im
    hdr = fig.text(0.08, 0.975, "", va="top", family="monospace", fontsize=9, linespacing=1.6)

    def redraw(txt):
        vals = [g[np.isfinite(g)] for g in grids.values()]
        vals = np.concatenate(vals) if vals else np.array([])
        for d, ln in lines.items():
            row = grids[d][idx[a.prev]]
            ln.set_ydata(row)
            if np.isfinite(row).any():
                lo, hi = np.nanmin(row), np.nanmax(row)
                pad = max(0.5, 0.05 * (hi - lo))
                ln.axes.set_ylim(lo - pad, hi + pad)
        for d, im in ims.items():
            im.set_data(grids[d])
            if vals.size:
                im.set_clim(max(1.0, vals.min()), max(vals.max(), vals.min() + 1))
        hdr.set_text(txt)
        fig.canvas.draw_idle()
        fig.canvas.flush_events()

    # ---- impulse generator: 2-frame cycle, all LEDs -----------------------------------
    lc.wr(ser, 0x13, 0x00)                           # no SLI patterns underneath
    lc.wr(ser, R_IMPCYC, 0x02)                       # 2-frame cycle, bpos 0
    lc.wr(ser, R_IMPRGB, 0x07)
    lc.wr(ser, R_IMPRGB2, 0x07)
    lc.wr(ser, R_ROICTL, 0xC0)                       # impulse + per-frame stream

    new = not (a.resume and done)
    fh = open(a.out, "w" if new else "a", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["delay_us", "expo_us", "prev_level", "this_level", "mean", "sd", "n",
                    "nhi_max", "T_us"])
    st = Stream(ser)
    t0, last_draw, k_done, nfail = time.time(), 0.0, 0, 0
    closed = False
    try:
        for d in a.delays:
            if not todo[d]:
                continue
            e = expo[d]
            lc.wr(ser, R_EXPO_LO, units[d] & 0xFF)
            lc.wr(ser, R_EXPO_HI, (units[d] >> 8) & 0xFF)
            want = int(round(d / lc.TICK_US))
            lc.set_delay(ser, want)
            if not lc.wait_delay(ser, want)[1]:
                raise SystemExit("delay %.1f did not land" % d)
            ser.timeout = 0
            ser.reset_input_buffer()
            st.buf, st.q = b"", []
            old, curA = set(), None
            for A, B in todo[d]:
                if not plt.fignum_exists(fig.number):
                    closed = True
                    raise KeyboardInterrupt
                if A != curA:
                    wr_now(ser, R_IMPLVL2, A)
                    curA = A
                wr_now(ser, R_IMPLVL, B)
                newset = {A, B}
                fresh = newset - old              # a level only the new pair has
                # WAIT FOR THE NEW LEVELS TO BE ON AIR. The tag names the level
                # transmitted in each frame; once the new pair shows, drop --skip more
                # frames so the image on screen (one frame behind) is new as well.
                prev, seen, t_w = None, False, time.time()
                while time.time() - t_w < 1.0:
                    r = st.get()
                    if r is None:
                        break
                    lvl = r[2]
                    if fresh:
                        seen = lvl in fresh
                    else:                         # e.g. (255,255) after (254,255)
                        seen = prev is not None and prev == lvl == A
                    prev = lvl
                    if seen:
                        break
                if not seen:
                    nfail += 1
                for _ in range(a.skip):
                    st.get()
                by = {}
                need = [(A, B), (B, A)] if A != B and one is None else [(A, B)]
                prev, t_c = None, time.time()
                while time.time() - t_c < max(2.0, 3e-6 * T * a.per) and \
                        min(len(by.get(c, [])) for c in need) < a.per:
                    r = st.get()
                    if r is None:
                        break
                    if r[1] == 256 and prev is not None and (prev, r[2]) in need:
                        by.setdefault((prev, r[2]), []).append((r[0], r[3]))
                    prev = r[2]
                for (pl, tl), v in by.items():
                    ms = [x[0] for x in v]
                    m_ = float(np.mean(ms))
                    w.writerow([d, round(e, 3), pl, tl, round(m_, 3),
                                round(float(np.std(ms)), 3), len(ms), max(x[1] for x in v),
                                round(T, 2)])
                    grids[d][idx[pl], idx[tl]] = m_
                old = newset
                k_done += 1
                if time.time() - last_draw > 3.0:
                    last_draw = time.time()
                    fh.flush()
                    el = time.time() - t0
                    redraw("every 8-bit grey pair, A B A B ...   exposure %.3f us   %d per cell\n"
                           "delay %.0f us   pair (%d,%d)   %d/%d   %.1f min, ~%.1f h left   "
                           "level change not seen: %d"
                           % (e, a.per, d, A, B, k_done, n_todo, el / 60,
                              el / k_done * (n_todo - k_done) / 3600, nfail))
    except KeyboardInterrupt:
        print("stopped -- rerun with --resume --out %s to continue" % a.out)
    finally:
        fh.close()
        ser.timeout = 0.05
        time.sleep(0.05)
        lc.wr(ser, R_ROICTL, 0x00)
        lc.wr(ser, R_IMPCYC, 0x05)                   # back to the 5-frame default
        lc.wr(ser, R_IMPLVL, 0xFF)
        lc.wr(ser, R_IMPLVL2, 0xFF)
        lc.set_delay(ser, 0)
        ser.close()

    print("wrote %s   %d pairs this run, level change not seen %d times"
          % (a.out, k_done, nfail))
    if not closed:
        redraw("done: %d pairs, %.1f min" % (k_done, (time.time() - t0) / 60))
        png = a.out.rsplit(".", 1)[0] + ".png"
        fig.savefig(png, dpi=120)
        print("wrote %s -- close the window to exit" % png)
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()
