"""Does a long exposure add up the light? Exposure-length test at a fixed start.

The impulse generator alternates --a (frame A) and --b (frame B), as in pair_sweep.py.
The exposure starts at --start us into the frame and its length steps through
--lengths. Each length's reading in each frame is plotted live against the prediction
from a 5 us pair_sweep CSV (--ref): the 5 us timeline's light, summed over the same
window, plus the dark pedestal. A reading below the prediction means the exposure is
not integrating over its whole length.

    python -u host/expo_lin.py COM5 --ref pair_sweep_255_0_2026-10-15.csv
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


def predictor(path):
    """5 us pair_sweep CSV -> f(start, length, position) = predicted ROI mean."""
    r = list(csv.DictReader(open(path)))
    T, e5 = float(r[0]["T_us"]), float(r[0]["expo_us"])
    t = np.array([float(x["t_us"]) for x in r])
    m = np.array([float(x["mean"]) for x in r])
    o = np.argsort(t)
    t, m = t[o], m[o]
    dark = float(np.median(np.sort(m)[: max(1, len(m) // 4)]))
    rate = np.clip(m - dark, 0, None) / e5           # ADU per us of exposure

    def f(start, length, pos):
        tt = (pos * T + start + np.arange(0, length, 1.0)) % (2 * T)
        return dark + float(np.interp(tt, t, rate, period=2 * T).sum())
    return f, dark


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM5")
    ap.add_argument("--display", default=None)
    ap.add_argument("--a", type=int, default=255)
    ap.add_argument("--b", type=int, default=0)
    ap.add_argument("--start", type=float, default=6697.16, help="exposure start, us into the frame")
    ap.add_argument("--lengths", type=float, nargs="+",
                    default=[5, 10, 20, 50, 100, 200, 300, 500, 750, 1000, 1500, 2172.375])
    ap.add_argument("--per", type=int, default=20, help="readings averaged per frame")
    ap.add_argument("--ref", help="5 us pair_sweep CSV to predict from")
    ap.add_argument("--out", default="expo_lin_%s.csv" % time.strftime("%Y-%m-%d_%H%M%S"))
    a = ap.parse_args()

    ser, T, mx = lc.preflight(a.port, a.display or lc.OFFLINE)
    pred, dark = predictor(a.ref) if a.ref else (None, None)
    pos_of = {a.a: 0, a.b: 1}

    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    plt.ion()
    fig, ax = plt.subplots(figsize=(11, 6))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.86, bottom=0.1)
    meas = {p: ([], []) for p in (0, 1)}
    pts = {p: ax.plot([], [], "o-", color="C%d" % p, label="measured, frame %s (sent %d)"
                      % ("AB"[p], (a.a, a.b)[p]))[0] for p in (0, 1)}
    if pred:
        L = np.geomspace(min(a.lengths), max(a.lengths), 200)
        for p in (0, 1):
            ax.plot(L, [min(pred(a.start, x, p), 1e6) for x in L], "--", color="C%d" % p,
                    lw=1, label="predicted from 5 us sweep, frame %s" % "AB"[p])
    ax.axhline(1023, color="0.5", lw=0.8, ls=":", label="1023 (pixel full scale)")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(10, 3000)
    ax.set_xlabel("exposure length (us), starting %.2f us into the frame" % a.start)
    ax.set_ylabel("ROI mean (ADU)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title("exposure-length test: %d / %d cycle" % (a.a, a.b), fontsize=10, pad=10)
    hdr = fig.text(0.08, 0.975, "", va="top", family="monospace", fontsize=9)

    lc.wr(ser, 0x13, 0x00)
    lc.wr(ser, R_IMPCYC, 0x02)
    lc.wr(ser, R_IMPRGB, 0x07)
    lc.wr(ser, R_IMPRGB2, 0x07)
    lc.wr(ser, R_IMPLVL2, a.a & 0xFF)
    lc.wr(ser, R_IMPLVL, a.b & 0xFF)
    lc.wr(ser, R_ROICTL, 0xC0)
    want = int(round(a.start / lc.TICK_US))
    lc.set_delay(ser, want)
    if not lc.wait_delay(ser, want)[1]:
        sys.exit("delay %.1f did not land" % a.start)

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["start_us", "length_req_us", "expo_us", "units", "position", "level", "mean",
                "sd", "n", "nhi_max", "predicted", "T_us"])
    st = Stream(ser)
    try:
        for Lr in a.lengths:
            if not plt.fignum_exists(fig.number):
                raise KeyboardInterrupt
            u = max(1, int(Lr / lc.EXPO_UNIT_US + 1e-6))
            if u > mx:
                print("%.1f us is over the limit (%d units), skipped" % (Lr, mx))
                continue
            e = u * lc.EXPO_UNIT_US
            ser.timeout = 0.05
            lc.wr(ser, R_EXPO_LO, u & 0xFF)
            lc.wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
            time.sleep(0.1)
            ser.timeout = 0
            ser.reset_input_buffer()
            st.buf, st.q = b"", []
            for _ in range(4):
                st.get()
            by, t_c = {0: [], 1: []}, time.time()
            while time.time() - t_c < 3.0 and min(len(v) for v in by.values()) < a.per:
                r = st.get()
                if r is None or r[1] != 256 or r[2] not in pos_of:
                    continue
                by[pos_of[r[2]]].append((r[0], r[3]))
            txt = []
            for p, v in by.items():
                if not v:
                    continue
                ms = [x[0] for x in v]
                m_ = float(np.mean(ms))
                pr = pred(a.start, e, p) if pred else float("nan")
                w.writerow([a.start, Lr, round(e, 3), u, p, (a.a, a.b)[p], round(m_, 3),
                            round(float(np.std(ms)), 3), len(ms), max(x[1] for x in v),
                            round(pr, 1), round(T, 2)])
                meas[p][0].append(e)
                meas[p][1].append(m_)
                pts[p].set_data(*meas[p])
                txt.append("frame %s: %.1f ADU (predicted %.0f)" % ("AB"[p], m_, pr))
            fh.flush()
            hdr.set_text("length %.3f us   %s" % (e, "   ".join(txt)))
            print("%9.3f us   %s" % (e, "   ".join(txt)))
            fig.canvas.draw_idle()
            fig.canvas.flush_events()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        fh.close()
        ser.timeout = 0.05
        time.sleep(0.05)
        lc.wr(ser, R_ROICTL, 0x00)
        lc.wr(ser, R_IMPCYC, 0x05)
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
