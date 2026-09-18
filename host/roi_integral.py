#!/usr/bin/env python3
"""A whole frame's light at the ROI, summed from short exposures that cannot clip.

    python -u host/roi_integral.py COM6 --live
    python -u host/roi_integral.py COM6 --rgb g --level 128 --live

WHY SUM SLICES. One exposure long enough to span every mirror in a frame fills the
10-bit sensor from the black level alone: with the blue filter in, a black screen
reads about 930 of 1023 over the 540 / 6175 us window, and a lit square clips most
of the ROI. So instead the frame is cut into short exposures -- 30 us each, stepped
30 us apart so they tile the frame edge to edge -- and the slices are added. Each
slice sits comfortably inside 10 bits; 278 of them add up to about 18 bits of range.

EVERY SLICE IS PAIRED WITH A BLACK SCREEN, and it is the difference that is summed.
Each exposure carries the sensor's fixed offset (~150 ADU) and whatever the black
screen leaks during that slice. Summed 278 times the offset alone would be ~42,000
ADU and bury the answer; subtracting black at the same delay removes both, and
because the two readings are taken seconds apart, slow drift cancels too.

THE PROJECTOR IS ONE FRAME BEHIND what it is sent, so after every redraw the first
frames still show the previous picture. --settle discards them.

STEP = EXPOSURE assumes the sensor integrates exactly what it is told. A fixed
overhead in the real integration time would make neighbouring slices overlap
slightly -- a constant scale on the total, which divides out of any ratio but is
worth knowing before quoting the absolute number.
"""
import argparse
import csv
import sys
import time
import tkinter as tk

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from align_roi import displays, set_mode                                # noqa: E402
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,        # noqa: E402
                         EXPO_UNIT_US, TICK_US, R_ROICTL, R_EXPO_LO, R_EXPO_HI)

COLOUR = {"r": "#ff0000", "g": "#00ff00", "b": "#0000ff", "w": "#ffffff"}
LINE = {"r": "#d0242a", "g": "#1a8f3c", "b": "#1f5fd0", "w": "#555555"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--expo", type=float, default=30.0, help="slice exposure, us")
    ap.add_argument("--step", type=float, default=None,
                    help="delay step, us (default = --expo, so slices tile the frame)")
    ap.add_argument("--rgb", default="b", choices=sorted(COLOUR),
                    help="channel the square is drawn in")
    ap.add_argument("--level", type=int, default=255, help="square level, 0-255")
    ap.add_argument("--x", type=int, default=388, help="square centre x (aligned ROI)")
    ap.add_argument("--y", type=int, default=502, help="square centre y (aligned ROI)")
    ap.add_argument("--size", type=int, default=32, help="square size, px")
    ap.add_argument("--frames", type=int, default=20, help="frames averaged per reading")
    ap.add_argument("--settle", type=int, default=4,
                    help="frames discarded after each redraw (projector is 1 frame late)")
    ap.add_argument("--verify-every", type=int, default=20, metavar="N")
    ap.add_argument("--out", default="roi_integral.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    step = a.step or a.expo

    # ---- display: 800x600@120, enforced (Windows has dropped it between runs) --
    dev = [d for d in displays() if (d[1], d[2]) != (0, 0)]
    if len(dev) != 1:
        sys.exit("expected exactly one non-primary display, found %d" % len(dev))
    dev = dev[0]
    if (dev[3], dev[4], dev[5]) != (800, 600, 120):
        print("display %dx%d@%d -> 800x600@120: %s"
              % (dev[3], dev[4], dev[5], set_mode(dev[0], 800, 600, 120)[1]))
        time.sleep(3)
        dev = [d for d in displays() if d[0] == dev[0]][0]
    _, DX, DY, DW, DH, _ = dev

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    if (gl & 3) != 3 or abs(1e6 / T - 120.0) > 1.0:
        ser.close()
        sys.exit("frame %.1f us, genlock 0x%02X -- need 120 Hz and genlock live" % (T, gl))
    wr(ser, 0x13, 0x00)                         # pass HDMI through
    wr(ser, R_ROICTL, 0x40)                     # ROI stream on
    u = max(1, int(round(a.expo / EXPO_UNIT_US)))
    wr(ser, R_EXPO_LO, u & 0xFF)
    wr(ser, R_EXPO_HI, (u >> 8) & 0xFF)
    expo = u * EXPO_UNIT_US
    delays = [i * step for i in range(int(T // step) + 1)]
    print("frame %.1f us   slice %.2f us   step %.2f us   %d slices"
          % (T, expo, step, len(delays)))
    print("square %dx%d %s=%d centred (%d,%d)\n"
          % (a.size, a.size, a.rgb, a.level, a.x, a.y))

    # ---- one Tk interpreter: matplotlib first, the screen as a Toplevel --------
    plt = live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                       gridspec_kw={"height_ratios": [3, 2]})
        ln_sq, = ax1.plot([], [], lw=1, color=LINE[a.rgb], label="square")
        ln_bk, = ax1.plot([], [], lw=1, color="#333333", label="black screen")
        ax1.set_ylabel("ROI mean per %.0f us slice (ADU)" % expo)
        ax1.grid(alpha=0.3); ax1.legend(loc="upper right", fontsize=9)
        ln_cum, = ax2.plot([], [], lw=1.4, color=LINE[a.rgb])
        ax2.set_xlabel("genlock delay (us)")
        ax2.set_ylabel("running sum of (square - black)")
        ax2.grid(alpha=0.3)
        ax2.set_xlim(0, T)
        fig.subplots_adjust(left=0.08, right=0.98, top=0.91, bottom=0.08, hspace=0.12)
        hdr = fig.text(0.08, 0.985, "", va="top", family="monospace", fontsize=9,
                       linespacing=1.6)
        try:
            fig.canvas.manager.window.wm_geometry("+40+40")
        except Exception:
            pass
        live = (plt, fig, ax1, ax2, ln_sq, ln_bk, ln_cum, hdr)

    root = tk.Toplevel() if plt is not None else tk.Tk()
    root.overrideredirect(True)
    root.geometry("%dx%d+%d+%d" % (DW, DH, DX, DY))
    root.attributes("-topmost", True)
    cv = tk.Canvas(root, bg="black", highlightthickness=0, bd=0)
    cv.pack(fill="both", expand=True)
    lv = max(0, min(255, a.level))
    hexcol = {"r": "#%02x0000" % lv, "g": "#00%02x00" % lv, "b": "#0000%02x" % lv,
              "w": "#%02x%02x%02x" % (lv, lv, lv)}[a.rgb]
    x0, y0 = a.x - a.size // 2, a.y - a.size // 2
    sq = cv.create_rectangle(x0, y0, x0 + a.size, y0 + a.size, fill=hexcol,
                             outline="", state="hidden")
    root.update()

    def reading(show_square):
        cv.itemconfigure(sq, state="normal" if show_square else "hidden")
        root.update()
        ser.reset_input_buffer()
        collect(ser, a.settle, timeout=3.0)
        rows = [r for r in collect(ser, a.frames, timeout=5.0, sat=True) if r[1] == 256]
        if not rows:
            return None, None
        return (sum(r[0] for r in rows) / len(rows),
                max((r[4] or 0) for r in rows))

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["delay_us", "square", "black", "diff", "sat_px_square", "sat_px_black"])
    xs, sqs, bks, cum = [], [], [], []
    total, nsat, t0 = 0.0, 0, time.time()
    try:
        for i, d in enumerate(delays):
            tick = int(round(d / TICK_US))
            set_delay(ser, tick)
            if i % max(1, a.verify_every) == 0:
                got, ok = wait_delay(ser, tick)
                if not ok:
                    print("  delay %.0f did not land (%s) -- skipped" % (d, got))
                    continue
            # ALTERNATE WHICH GOES FIRST. Always measuring the square first would
            # turn any order effect into a constant offset that 278 slices add up.
            if i % 2 == 0:
                s, ss = reading(True)
                b, bs = reading(False)
            else:
                b, bs = reading(False)
                s, ss = reading(True)
            if s is None or b is None:
                print("  delay %.0f: no frames -- skipped" % d)
                continue
            if ss or bs:
                nsat += 1
            diff = s - b
            total += diff
            xs.append(d); sqs.append(s); bks.append(b); cum.append(total)
            w.writerow([d, round(s, 3), round(b, 3), round(diff, 3), ss, bs])
            fh.flush()
            if live and (i % 3 == 0 or i == len(delays) - 1):
                plt, fig, ax1, ax2, ln_sq, ln_bk, ln_cum, hdr = live
                ln_sq.set_data(xs, sqs); ln_bk.set_data(xs, bks); ln_cum.set_data(xs, cum)
                ax1.set_xlim(0, T); ax1.relim(); ax1.autoscale_view(scalex=False)
                ax2.relim(); ax2.autoscale_view(scalex=False)
                hdr.set_text("%s=%d square, %.0f us slices tiled over one frame   "
                             "delay %.0f us (%d/%d)" % (a.rgb, a.level, expo, d, i + 1,
                                                       len(delays))
                             + chr(10)
                             + "square %.1f  black %.1f  diff %+.2f   running sum %.1f   "
                               "slices with saturated px %d   %.1f min"
                             % (s, b, diff, total, nsat, (time.time() - t0) / 60))
                try:
                    if not plt.fignum_exists(fig.number):
                        raise RuntimeError
                    fig.canvas.draw_idle(); fig.canvas.flush_events()
                except Exception:
                    print("plot gone -- continuing, CSV still written")
                    live = None
            if i % 25 == 0:
                print("  %5.0f us   square %7.2f  black %7.2f  diff %+7.2f  sum %9.1f"
                      % (d, s, b, diff, total))
    except KeyboardInterrupt:
        print("\ninterrupted -- everything so far is written")
    finally:
        wr(ser, R_ROICTL, 0x00); set_delay(ser, 0); ser.close(); fh.close()

    if xs:
        # THE BREAKDOWN, not just the raw total. The pedestal (the camera's fixed
        # offset) comes from the darkest black readings. Slices where neither
        # reading rises above it contain no mirror light, so whatever square-minus-
        # black they still show is a constant per-reading offset, not light -- the
        # first run found +0.35 per slice, 27% of the total once summed.
        import statistics as st
        n = len(xs)
        ped = sorted(bks)[max(0, n // 20)]
        dark = [sq - bk for sq, bk in zip(sqs, bks) if sq < ped + 1.5 and bk < ped + 1.5]
        off = st.median(dark) if len(dark) >= 10 else 0.0
        S = sum(v - ped for v in sqs)
        B = sum(v - ped for v in bks)
        print("")
        print("%d slices, %.2f us each   pedestal %.2f ADU" % (n, expo, ped))
        print("  black screen, summed           %9.1f" % B)
        print("  square screen, summed          %9.1f   (one exposure holds %.0f)"
              % (S, 1023.0 - ped))
        print("  square - black                 %9.1f" % total)
        print("  constant offset in dark slices %9.1f   (%+.3f x %d, from %d dark slices)"
              % (off * n, off, n, len(dark)))
        print("  MIRROR LIGHT FROM THE SQUARE   %9.1f" % (total - off * n))
        print("  brightest single slice         %9.1f   slices with saturated px %d"
              % (max(sqs), nsat))
    print("wrote %s" % a.out)
    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
    if live:
        print("close the plot window to exit.")
        live[0].ioff(); live[0].show()
    try:
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
