"""Live view of one window while the upcoming frame flips between two grey levels.

The impulse generator runs a 2-frame cycle with the previous frame held at --prev and
the upcoming frame toggling between the --levels every --hold seconds. Each reading
whose previous frame was --prev is plotted as it arrives, coloured by the level sent
in its own frame, with running means. Close the window to stop.

    python host/live_pair.py COM5 --window 6700:7100 --prev 0 --levels 0 255

--all plots every reading (the most recent --span of them in total), coloured by the
level sent in its frame. With one --level it is a plain A B A B flash, e.g. black white:

    python host/live_pair.py COM5 --prev 0 --levels 255 --all --span 200

--cycle N runs an N-frame cycle instead: --prev at position 0, --levels[0] at the rest
(e.g. W K K K K is --cycle 5 --prev 255 --levels 0). Every reading is plotted by its
position after the odd frame, counted from the odd frame's tag.
"""
import argparse
import sys
import time
from collections import deque

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import lag_core as lc                                                   # noqa: E402
from frame_sweep import (R_ROICTL, R_EXPO_LO, R_EXPO_HI, R_IMPCYC,       # noqa: E402
                         R_IMPLVL, R_IMPRGB, R_IMPLVL2, R_IMPRGB2)
from gray_pair import Stream, wr_now                                    # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM5")
    ap.add_argument("--display", default=None)
    ap.add_argument("--window", default="6700:7100", metavar="START:END",
                    help="us into the frame: delay = START, exposure = END - START")
    ap.add_argument("--prev", type=int, default=0, help="level held in the previous frame")
    ap.add_argument("--levels", type=int, nargs="+", default=[0, 255],
                    help="upcoming-frame levels to toggle between")
    ap.add_argument("--hold", type=float, default=2.0, help="seconds on each level")
    ap.add_argument("--span", type=int, default=600,
                    help="readings kept per level (with --all: in total)")
    ap.add_argument("--all", action="store_true",
                    help="plot every reading, not only those after --prev")
    ap.add_argument("--cycle", type=int, default=2,
                    help="frames per cycle; over 2 plots each position (implies --all)")
    ap.add_argument("--odd-at", type=int, default=0,
                    help="with --cycle: the position the odd frame is DRAWN at, e.g. 1 shows "
                         "W K K K K as K W K K K (the same repeating sequence)")
    a = ap.parse_args()
    cyc = a.cycle > 2
    if cyc:
        a.all, a.levels = True, a.levels[:1]
        if a.levels[0] == a.prev:
            sys.exit("--prev and --levels must differ: the odd frame's tag anchors the cycle")

    ser, T, mx = lc.preflight(a.port, a.display or lc.OFFLINE)
    t_a, t_b = (float(v) for v in a.window.split(":"))
    u = min(mx, max(1, int((t_b - t_a) / lc.EXPO_UNIT_US)))
    e = u * lc.EXPO_UNIT_US

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()
    fig, (ax, axb) = plt.subplots(1, 2, figsize=(12, 5.6), width_ratios=[3, 1])
    fig.subplots_adjust(left=0.07, right=0.97, top=0.84, bottom=0.11, wspace=0.25)
    keys = (list(range(a.cycle)) if cyc else
            sorted(set(a.levels) | {a.prev}) if a.all else list(a.levels))
    sent = (lambda q: a.prev if q == a.odd_at else a.levels[0]) if cyc else (lambda q: q)
    recent = deque(maxlen=a.span)                    # --all: (k, mean, level)
    data = {lv: deque(maxlen=a.span) for lv in keys}
    pts = {lv: ax.plot([], [], ".-" if a.all else ".", ms=4 if a.all else 3, lw=0.4,
                       color="C%d" % i, label=("position %d: sent %d" % (lv, sent(lv)))
                       if cyc else "this frame %d" % lv)[0]
           for i, lv in enumerate(keys)}
    ax.set_xlabel("reading")
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    ax.set_title("%.0f .. %.0f us into the frame (exposure %.3f us), previous frame %d"
                 % (t_a, t_a + e, e, a.prev), fontsize=10, pad=10)
    if cyc:
        ax.set_title("%.0f .. %.0f us into the frame (exposure %.3f us), %s cycle, "
                     "last %d readings" % (t_a, t_a + e, e,
                                           " ".join(str(sent(q)) for q in keys), a.span),
                     fontsize=10, pad=10)
    elif a.all:
        ax.set_title("%.0f .. %.0f us into the frame (exposure %.3f us), %s flashing, "
                     "last %d readings" % (t_a, t_a + e, e,
                                           " / ".join(str(v) for v in keys), a.span),
                     fontsize=10, pad=10)
    bars = axb.bar([str(lv) for lv in keys], [0] * len(keys),
                   color=["C%d" % i for i in range(len(keys))])
    axb.set_xlabel("position in the cycle (%d = odd frame sent)" % a.odd_at if cyc
                   else "level sent in THIS frame")
    axb.set_ylabel("running mean (ADU)")
    axb.grid(alpha=0.3, axis="y")
    hdr = fig.text(0.07, 0.975, "", va="top", family="monospace", fontsize=9, linespacing=1.6)

    lc.wr(ser, R_EXPO_LO, u & 0xFF)
    lc.wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    want = int(round(t_a / lc.TICK_US))
    lc.set_delay(ser, want)
    if not lc.wait_delay(ser, want)[1]:
        sys.exit("delay %.1f did not land" % t_a)
    lc.wr(ser, 0x13, 0x00)                           # no SLI patterns underneath
    lc.wr(ser, R_IMPCYC, a.cycle & 0x0F)             # N-frame cycle, odd frame at bpos 0
    lc.wr(ser, R_IMPRGB, 0x07)
    lc.wr(ser, R_IMPRGB2, 0x07)
    lc.wr(ser, R_IMPLVL2, a.prev)
    lc.wr(ser, R_IMPLVL, a.levels[0])
    lc.wr(ser, R_ROICTL, 0xC0)                       # impulse + per-frame stream
    ser.timeout = 0
    ser.reset_input_buffer()
    st = Stream(ser)
    k, prev, nhi, pos = 0, None, 0, None
    li, t_sw, last_draw = 0, time.time(), 0.0
    try:
        while plt.fignum_exists(fig.number):
            if len(a.levels) > 1 and time.time() - t_sw > a.hold:
                li = (li + 1) % len(a.levels)
                wr_now(ser, R_IMPLVL, a.levels[li])
                t_sw = time.time()
            r = st.get()
            if r is None:
                continue
            if cyc:                                   # position from the odd frame's tag
                pos = 0 if r[2] == a.prev else (None if pos is None else pos + 1)
                if pos is not None and pos >= a.cycle:
                    pos = None                            # lost the anchor: wait for the next
            if r[1] == 256 and cyc and pos is not None:
                recent.append((k, r[0], (pos + a.odd_at) % a.cycle))
                data = {q: [(x, y) for x, y, l_ in recent if l_ == q] for q in keys}
                nhi = max(nhi, r[3])
                k += 1
            elif r[1] == 256 and a.all and not cyc and r[2] in data:
                recent.append((k, r[0], r[2]))
                data = {lv: [(x, y) for x, y, l_ in recent if l_ == lv] for lv in keys}
                nhi = max(nhi, r[3])
                k += 1
            elif r[1] == 256 and not a.all and prev == a.prev and r[2] in data:
                data[r[2]].append((k, r[0]))
                nhi = max(nhi, r[3])
                k += 1
            prev = r[2]
            if time.time() - last_draw > 0.25:
                last_draw = time.time()
                txt, means = [], []
                for lv in keys:
                    if data[lv]:
                        x, y = zip(*data[lv])
                        pts[lv].set_data(x, y)
                        m_, s_ = float(np.mean(y)), float(np.std(y))
                    else:
                        pts[lv].set_data([], [])
                        m_ = s_ = float("nan")
                    means.append(m_)
                    txt.append(("pos %d (sent %3d)" % (lv, sent(lv)) if cyc else "this %3d" % lv)
                               + ": %8.2f ADU  sd %6.2f  n %d" % (m_, s_, len(data[lv])))
                for b, m_ in zip(bars, means):
                    b.set_height(0 if np.isnan(m_) else m_)
                ax.relim()
                ax.autoscale_view()
                fin = [m_ for m_ in means if not np.isnan(m_)]
                if fin:
                    axb.set_ylim(0, max(fin) * 1.15 + 1)
                head = ("%d-frame cycle %s" % (a.cycle, " ".join(str(sent(q)) for q in keys))
                        if cyc else
                        "levels %s flashing A B A B at the frame rate" % " / ".join(map(str, keys))
                        if a.all else "previous frame %d, upcoming level toggles every %.1f s"
                        % (a.prev, a.hold))
                hdr.set_text("%s   saturated px max %d\n%s" % (head, nhi, "\n".join(txt)))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
    except KeyboardInterrupt:
        pass
    finally:
        ser.timeout = 0.05
        time.sleep(0.05)
        lc.wr(ser, R_ROICTL, 0x00)
        lc.wr(ser, R_IMPCYC, 0x05)                   # back to the 5-frame default
        lc.wr(ser, R_IMPLVL, 0xFF)
        lc.wr(ser, R_IMPLVL2, 0xFF)
        lc.set_delay(ser, 0)
        ser.close()


if __name__ == "__main__":
    main()
