"""A delay sweep across both frames of a 2-frame A B cycle, with a live timeline.

The impulse generator alternates level A (position 0) and level B (position 1) every
frame. Each camera line is tagged with the level transmitted when it was triggered, so
one delay read gives both positions, and stepping the delay across one frame period
fills the whole 2-frame timeline:

    t = position * T + delay       (us after the vsync of the frame that carried A)

A trigger before the first active pixel (--active-us) carries the previous frame's tag,
so those readings are moved one position later, as in lag_sweep.py.

    python -u host/pair_sweep.py COM5 --a 255 --b 0
"""
import argparse
import csv
import sys
import time

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import lag_core as lc                                                   # noqa: E402
from frame_sweep import (R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC,       # noqa: E402
                         R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2)
from gray_pair import Stream                                            # noqa: E402


def mark_windows(ax, T, windows):
    """Vertical lines (and a light band) at each START:END exposure window, in both
    frames of the cycle. A window that runs past the end of the cycle wraps to t = 0."""
    for k, (s, e) in enumerate(windows):
        for p in (0, 1):
            a_, b_ = p * T + s, p * T + e
            spans = [(a_, b_)] if b_ <= 2 * T else [(a_, 2 * T), (0.0, b_ - 2 * T)]
            for x0, x1 in spans:
                ax.axvspan(x0, x1, color="C%d" % (k + 2), alpha=0.08, lw=0)
            for x in (a_, b_ % (2 * T) if b_ > 2 * T else b_):
                ax.axvline(x, color="C%d" % (k + 2), lw=1.2,
                           label=("exposure %.2f .. %.2f us" % (s, e)) if (p, x) == (0, a_)
                           else None)


def plot_saved(path, windows, overlay=False, extra=()):
    import matplotlib.pyplot as plt
    rows = list(csv.DictReader(open(path)))
    T = float(rows[0]["T_us"])
    fig, ax = plt.subplots(figsize=(13, 5.6))
    fig.subplots_adjust(left=0.06, right=0.98, top=0.9, bottom=0.11)
    ci = 0
    for f_ in [path] + list(extra):                  # --also: more sweeps, overlay only
        rs = rows if f_ == path else list(csv.DictReader(open(f_)))
        lv = {int(x["position"]): int(x["level"]) for x in rs}
        for p in (0, 1):
            r = sorted((float(x["delay_us" if overlay else "t_us"]), float(x["mean"]))
                       for x in rs if int(x["position"]) == p)
            if not r:
                continue
            prev = lv.get(1 - p, lv[p])              # the other frame of the cycle
            lab = ("%d -> %d" % (prev, lv[p]) if overlay
                   else "frame %s: sent %d" % ("AB"[p], lv[p]))
            ax.plot([x[0] for x in r], [x[1] for x in r], ".-", ms=2, lw=0.6,
                    color="C%d" % (ci if ci < 2 else ci + 1), label=lab)
            ci += 1
    if overlay:                                      # both frames on one delay axis
        for s_, e_ in windows:
            ax.axvline(s_, color="C2", lw=1.2, label="window start %.2f us" % s_)
        ax.autoscale(axis="x")
        ax.set_xlabel("delay (us after the frame's vsync) -- both frames overlaid")
    else:
        for x in (0, T, 2 * T):
            ax.axvline(x, color="0.6", lw=0.8, ls="--")
        mark_windows(ax, T, windows)
        ax.set_xlim(0, 2 * T)
        ax.set_xlabel("t (us after the vsync of the frame that carried A)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("%s   exposure %s us" % (path.replace("\\", "/").rsplit("/", 1)[-1],
                                         rows[0]["expo_us"]), fontsize=10, pad=10)
    png = (path.rsplit(".", 1)[0] + ("_overlay" if overlay else "")
           + ("_marked.png" if windows else ".png"))
    fig.savefig(png, dpi=120)
    print("wrote %s" % png)
    interactive(fig, ax, T)
    plt.show()


def interactive(fig, ax, T):
    """Wheel zooms time about the cursor (Ctrl+wheel zooms ADU), left-drag pans,
    double-click resets. The cursor's time and ADU show at the bottom."""
    home = (ax.get_xlim(), ax.get_ylim())
    cur = fig.text(0.06, 0.01, "", family="monospace", fontsize=9)
    drag = {}

    def scroll(ev):
        if ev.inaxes is not ax:
            return
        f = 0.8 if ev.button == "up" else 1.25
        if ev.key == "control":
            lo, hi = ax.get_ylim()
            ax.set_ylim(ev.ydata - (ev.ydata - lo) * f, ev.ydata + (hi - ev.ydata) * f)
        else:
            lo, hi = ax.get_xlim()
            ax.set_xlim(ev.xdata - (ev.xdata - lo) * f, ev.xdata + (hi - ev.xdata) * f)
        fig.canvas.draw_idle()

    def press(ev):
        if ev.inaxes is not ax or ev.button != 1:
            return
        if ev.dblclick:
            ax.set_xlim(*home[0])
            ax.set_ylim(*home[1])
            fig.canvas.draw_idle()
            return
        drag.update(x=ev.x, y=ev.y, xl=ax.get_xlim(), yl=ax.get_ylim())

    def motion(ev):
        if ev.inaxes is ax and ev.xdata is not None:
            k = int(ev.xdata // T)
            cur.set_text("t %.2f us  (frame %s + %.2f us)   %.1f ADU"
                         % (ev.xdata, "AB"[k % 2], ev.xdata - k * T, ev.ydata))
        if drag:
            inv = ax.transData.inverted()
            (x0, y0), (x1, y1) = inv.transform((drag["x"], drag["y"])), inv.transform((ev.x, ev.y))
            ax.set_xlim(drag["xl"][0] - (x1 - x0), drag["xl"][1] - (x1 - x0))
            ax.set_ylim(drag["yl"][0] - (y1 - y0), drag["yl"][1] - (y1 - y0))
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("scroll_event", scroll)
    fig.canvas.mpl_connect("button_press_event", press)
    fig.canvas.mpl_connect("button_release_event", lambda ev: drag.clear())
    fig.canvas.mpl_connect("motion_notify_event", motion)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM5")
    ap.add_argument("--display", default=None)
    ap.add_argument("--a", type=int, default=255, help="level at position 0")
    ap.add_argument("--b", type=int, default=0, help="level at position 1")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--step", type=float, default=5.0, help="delay step, us")
    ap.add_argument("--per", type=int, default=8, help="readings averaged per position")
    ap.add_argument("--settle", type=int, default=3, help="lines dropped after a delay change")
    ap.add_argument("--dmin", type=float, default=0.0)
    ap.add_argument("--dmax", type=float, default=None)
    ap.add_argument("--cross", action="store_true",
                    help="let the exposure run past the end of the frame (it only has to end "
                         "90 us before the next trigger, which the exposure limit enforces). "
                         "Delays then go up to --dmax, else the end of the frame")
    ap.add_argument("--active-us", type=float, default=lc.ACTIVE_US)
    ap.add_argument("--out", default="pair_sweep_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S"))
    ap.add_argument("--window", nargs="+", default=[], metavar="START:END",
                    help="mark exposure windows (us into the frame), e.g. 6697.16:8869.54")
    ap.add_argument("--load", metavar="CSV", help="redraw a saved sweep instead of measuring")
    ap.add_argument("--also", nargs="+", default=[], metavar="CSV",
                    help="with --load --overlay: more saved sweeps drawn on the same axes")
    ap.add_argument("--overlay", action="store_true",
                    help="with --load: draw both frames against delay, on top of each other")
    a = ap.parse_args()
    windows = [tuple(float(v) for v in w_.split(":")) for w_ in a.window]
    if a.load:
        return plot_saved(a.load, windows, a.overlay, a.also)
    # --a == --b: every frame is the same, so there is one position (e.g. 0 -> 0).

    ser, T, mx = lc.preflight(a.port, a.display or lc.projector_display())
    u = max(1, int(round(a.expo / lc.EXPO_UNIT_US)))
    if u > mx:
        sys.exit("%.2f us is over the exposure limit" % a.expo)
    e = u * lc.EXPO_UNIT_US
    if a.cross:
        delays = list(np.arange(a.dmin, min(a.dmax or T, T - 1.0), a.step))
    else:
        delays = lc.delays_for(T, e, a.step, a.dmin, a.dmax)
    pos_of = {a.a: 0, a.b: 1} if a.a != a.b else {a.a: 0}
    print("frame %.2f us   exposure %.3f us   %d delays   A %d  B %d" % (T, e, len(delays),
                                                                         a.a, a.b))

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()
    fig, ax = plt.subplots(figsize=(13, 5.6))
    fig.subplots_adjust(left=0.06, right=0.98, top=0.86, bottom=0.11)
    tl = {p: ([], []) for p in (0, 1)}
    lines = {p: ax.plot([], [], ".-", ms=2, lw=0.6, color="C%d" % p,
                        label="frame %s: level %d" % ("AB"[p], (a.a, a.b)[p]))[0]
             for p in (0, 1)}
    for x in (0, T, 2 * T):
        ax.axvline(x, color="0.6", lw=0.8, ls="--")
    mark_windows(ax, T, windows)
    ax.set_xlim(0, 2 * T)
    ax.set_xlabel("t (us after the vsync of the frame that carried A)")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)
    ax.set_title("2-frame cycle %d / %d   exposure %.3f us   step %.1f us"
                 % (a.a, a.b, e, a.step), fontsize=10, pad=10)
    hdr = fig.text(0.06, 0.975, "", va="top", family="monospace", fontsize=9)

    lc.wr(ser, R_EXPO_LO, u & 0xFF)
    lc.wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    lc.wr(ser, 0x13, 0x00)                           # no SLI patterns underneath
    lc.wr(ser, R_IMPCYC, 0x02)                       # 2-frame cycle, bpos 0
    lc.wr(ser, R_IMPRGB, 0x07)
    lc.wr(ser, R_IMPRGB2, 0x07)
    lc.wr(ser, R_IMPLVL2, a.a & 0xFF)
    lc.wr(ser, R_IMPLVL, a.b & 0xFF)
    lc.wr(ser, R_ROICTL, 0xC0)                       # impulse + per-frame stream

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["T_us", "expo_us", "delay_us", "position", "level", "t_us", "mean", "sd", "n",
                "nhi_max", "tag_fix_us"])
    st = Stream(ser)
    t0, last_draw, nbad = time.time(), 0.0, 0
    try:
        for i, d in enumerate(delays):
            if not plt.fignum_exists(fig.number):
                raise KeyboardInterrupt
            want = int(round(d / lc.TICK_US))
            ser.timeout = 0.05
            lc.set_delay(ser, want)
            if not lc.wait_delay(ser, want)[1]:
                raise SystemExit("delay %.1f did not land" % d)
            ser.timeout = 0
            ser.reset_input_buffer()
            st.buf, st.q = b"", []
            for _ in range(a.settle):
                st.get()
            by, t_c = {q: [] for q in set(pos_of.values())}, time.time()
            while time.time() - t_c < 2.0 and min(len(v) for v in by.values()) < a.per:
                r = st.get()
                if r is None:
                    break
                if r[1] != 256 or r[2] not in pos_of:
                    nbad += r[1] == 256
                    continue
                p = (pos_of[r[2]] + (d < a.active_us)) % len(by)
                by[p].append((r[0], r[3]))
            for p, v in by.items():
                if not v:
                    continue
                ms = [x[0] for x in v]
                m_ = float(np.mean(ms))
                t = p * T + d
                w.writerow([round(T, 2), round(e, 3), round(d, 2), p, (a.a, a.b)[p], round(t, 2),
                            round(m_, 3), round(float(np.std(ms)), 3), len(ms),
                            max(x[1] for x in v), a.active_us])
                tl[p][0].append(t)
                tl[p][1].append(m_)
            if time.time() - last_draw > 0.5 or i == len(delays) - 1:
                last_draw = time.time()
                fh.flush()
                for p, ln in lines.items():
                    ln.set_data(*tl[p])
                ax.relim()
                ax.autoscale_view(scalex=False)
                el = time.time() - t0
                hdr.set_text("delay %.1f us   %d/%d   %.1f min, ~%.1f min left   "
                             "unexpected tags %d" % (d, i + 1, len(delays), el / 60,
                                                     el / (i + 1) * (len(delays) - i - 1) / 60,
                                                     nbad))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("stopped")
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

    print("wrote %s" % a.out)
    if plt.fignum_exists(fig.number):
        png = a.out.rsplit(".", 1)[0] + ".png"
        fig.savefig(png, dpi=120)
        print("wrote %s -- close the window to exit" % png)
        plt.ioff()
        plt.show()


if __name__ == "__main__":
    main()
