#!/usr/bin/env python3
"""One frame, one colour, several intensities: what light comes out, and WHEN.

    python host/level_delay.py COM6 --colour red --levels 255,128,64,32,16 --live
    python host/level_delay.py COM6 --colour blue --levels 255,64,16 --step 10 --live

THE QUESTION. A DMD makes grey by steering light, not by dimming the source -- so
if the LED were simply on for a fixed sub-field, the light in the gaps BETWEEN
sub-fields would be the same at every commanded intensity. Only the mirror-on
time would change.

It is not the same. At delay 0, where nothing should be on screen, the reading
depends on which colour is commanded -- red ~250, green ~450, blue ~700. That
says the LED drive itself follows the content, not just the mirrors.

This sweeps ONE SOLID FRAME (no five-frame sequence, nothing blinking) across the
whole frame period, repeated at descending intensities, and draws each intensity
as its own curve. If the between-sub-field light scales with commanded level, the
curves separate everywhere; if the LED sub-field is fixed and only the mirrors
change, they overlie each other except where the mirrors are on.

LEVEL 0 IS ALWAYS INCLUDED as the reference curve, drawn in black. It is the same
sweep with the LEDs given nothing to display, so the difference between it and
any other curve is what that intensity actually added.

Nothing here uses a marker or a position: the field is solid, every frame is
identical, and the mean is pooled across all frames at each delay. That removes
the whole class of labelling questions from this measurement.
"""
import argparse
import collections
import csv
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,      # noqa: E402
                         COLOURS, EXPO_UNIT_US, TICK_US,
                         R_IMPCYC, R_IMPLVL, R_IMPRGB, R_ROICTL,
                         R_EXPO_LO, R_EXPO_HI)
R_IMPLVL2, R_IMPRGB2 = 0x07, 0x08
HUE = {"red": (0.82, 0.14, 0.16), "green": (0.10, 0.56, 0.24),
       "blue": (0.12, 0.37, 0.82), "white": (0.35, 0.35, 0.35)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--colour", "--color", dest="colour", default="red",
                    choices=sorted(COLOURS))
    ap.add_argument("--levels", default="255,128,64,32,16",
                    help="comma-separated intensities to sweep, brightest first")
    ap.add_argument("--step", type=float, default=20.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--frames", type=int, default=15, help="frames per delay point")
    ap.add_argument("--no-zero", action="store_true",
                    help="skip the level-0 reference curve, halving the run when "
                         "only the lit response is wanted")
    ap.add_argument("--verify-every", type=int, default=1, metavar="N",
                    help="read the delay register back on every Nth point instead "
                         "of every one. The read-back poll is ~0.1 s and dominates "
                         "a fine sweep; at 1 us steps consecutive delays are nearly "
                         "identical and a stuck register shows as a flat run, so "
                         "checking periodically still catches it. Default 1.")
    ap.add_argument("--settle", type=int, default=4, help="frames discarded per point")
    ap.add_argument("--out", default="level_delay.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    levels = [int(x) for x in a.levels.split(",") if x.strip() != ""]
    if 0 not in levels and not a.no_zero:
        levels.append(0)                  # the reference, unless explicitly skipped
    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    if not (1000 < T_us < 60000) or (gl & 3) != 3:
        ser.close(); sys.exit(f"period {T_us} us / genlock 0x{gl:02X} -- not ready")

    # SOLID FIELD. 0xD5 points bpos at position 5, which a 5-frame cycle never
    # reaches, and inverts -- so every frame is lit and there is no sequence at
    # all. Both level registers are written anyway so nothing depends on which
    # frame the generator thinks is odd.
    wr(ser, R_IMPCYC, 0xD5)
    wr(ser, R_IMPRGB, COLOURS[a.colour])
    wr(ser, R_IMPRGB2, COLOURS[a.colour])
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)
    wr(ser, R_ROICTL, 0xC0)
    time.sleep(0.4)

    npts = int(T_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"colour         {a.colour}, SOLID field (no sequence, nothing blinking)")
    print(f"levels         {levels}")
    print(f"delay sweep    0 .. {(npts-1)*a.step:.0f} us in {a.step:.0f} us "
          f"-> {npts} points per level")
    per = 0.30 if a.verify_every <= 1 else 0.19
    print(f"estimated      ~{npts*len(levels)*per/60:.0f} min total   "
          f"({npts*len(levels)} points, delay verified every "
          f"{max(1, a.verify_every)})\n")

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        lines = {}
        base = HUE[a.colour]
        for lv in levels:
            if lv == 0:
                col, lw = "black", 1.6
            else:
                # brighter commanded level -> more saturated line, so the ordering
                # of the curves matches the ordering of the intensities.
                f = 0.25 + 0.75 * (lv / 255.0)
                col = tuple(1 - f * (1 - c) for c in base)
                lw = 1.3
            lines[lv], = ax.plot([], [], lw=lw, color=col, label=f"level {lv}")
        ax.set_xlim(-T_us * 0.01, T_us * 1.01)
        ax.set_xlabel("genlock delay within the projected frame (us)")
        ax.set_ylabel("ROI mean (ADU)")
        ax.grid(alpha=0.3)
        ax.legend(loc="upper right", fontsize=9, ncol=2)
        fig.subplots_adjust(left=0.07, right=0.985, top=0.90, bottom=0.11)
        hdr = fig.text(0.07, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        live = (plt, fig, ax, lines, hdr)

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["colour", "level", "delay_us", "mean", "tlp"])
    fh.flush()

    data = collections.defaultdict(dict)
    nbad = nmis = nskip = 0
    last_draw = [0.0]
    t0 = time.time()
    stop = False
    try:
        for lv in levels:
            if stop:
                break
            wr(ser, R_IMPLVL, lv)
            wr(ser, R_IMPLVL2, lv)
            time.sleep(0.25)
            for i in range(npts):
                d = i * a.step
                tick = int(round(d / TICK_US))
                set_delay(ser, tick)
                if i % max(1, a.verify_every) == 0:
                    got, ok = wait_delay(ser, tick)
                    if not ok:
                        print(f"  level {lv} delay {d:.0f}: did not land ({got})"
                              f" -- skipped")
                        nskip += 1
                        continue
                ser.reset_input_buffer()
                collect(ser, a.settle, timeout=4.0)
                rows = collect(ser, a.frames, timeout=6.0)
                if not rows:
                    continue
                good = [m for m, n, t, c in rows if n == 256]
                nbad += len(rows) - len(good)
                if not good:
                    continue
                tlp = collections.Counter(t for m, n, t, c in rows).most_common(1)[0][0]
                shift = {"red": 16, "green": 8, "blue": 0}[a.colour]
                if ((tlp >> shift) & 0xFF) != lv:
                    nmis += 1
                mean = sum(good) / len(good)
                data[lv][d] = mean
                w.writerow([a.colour, lv, d, round(mean, 2), "%06X" % tlp])
                fh.flush()

                # THROTTLE THE REDRAW. A fine sweep is 8326 points per colour;
                # redrawing on every one costs more than the capture does and
                # slows the sweep itself. Ten frames a second still reads as live
                # and the data is unaffected -- only how often it is painted.
                now = time.time()
                if live and (now - last_draw[0] >= 0.10 or i == npts - 1):
                    last_draw[0] = now
                    plt, fig, ax, lines, hdr = live
                    xs = sorted(data[lv])
                    lines[lv].set_data(xs, [data[lv][x] for x in xs])
                    allv = [v for dd in data.values() for v in dd.values()]
                    lo, hi = min(allv), max(allv)
                    pad = max(3.0, (hi - lo) * 0.12)
                    ax.set_ylim(lo - pad, hi + pad)
                    hdr.set_text(
                        f"{a.colour} SOLID field   exposure "
                        f"{expo_units*EXPO_UNIT_US:.2f} us   frame {T_us:.0f} us"
                        + chr(10)
                        + f"level {lv}   delay {d:6.0f} us ({i+1}/{npts})   "
                          f"mean {mean:7.2f}   npx!=256 {nbad}   pixel!=cmd {nmis}   "
                          f"{(time.time()-t0)/60:.1f} min")
                    try:
                        if not plt.fignum_exists(fig.number):
                            raise RuntimeError
                        fig.canvas.draw_idle()
                        fig.canvas.flush_events()
                    except Exception:
                        print("plot gone -- continuing, CSV still written")
                        live = None
                if i % 40 == 0 or i == npts - 1:
                    print(f"  level {lv:3d}  {i+1:5d}/{npts}  delay {d:8.0f} us   "
                          f"mean {mean:7.2f}   {(time.time()-t0)/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- everything captured is written")
    finally:
        wr(ser, R_IMPLVL, 0)
        wr(ser, R_IMPLVL2, 0)
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
        fh.close()

    print(f"\nwrote {a.out}")
    print(f"   npx != 256           {nbad}")
    print(f"   pixel != commanded   {nmis}")
    print(f"   delays not landing   {nskip}")
    for lv in levels:
        if data[lv]:
            v = list(data[lv].values())
            print(f"   level {lv:3d}: {len(v):5d} delays   {min(v):7.2f} .. {max(v):7.2f}"
                  f"   swing {max(v)-min(v):7.2f}")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()
