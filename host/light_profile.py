#!/usr/bin/env python3
"""Where in the frame does the light actually come out? Delay profile at one level.

    python host/light_profile.py COM6 --level 16 --live
    python host/light_profile.py COM6 --level 255 --colour g --step 50 --live

WHAT THIS ANSWERS. A fixed-delay measurement samples ONE narrow window of the
frame -- 250 us out of 16.7 ms is 1.5% of it. The projector is field-sequential:
red exists only during the red sub-field, somewhere in the frame. If that
sub-field does not overlap the sampling window, the measurement reads no red AT
ANY LEVEL, and it looks exactly like a projector that is not emitting.

That is not hypothetical. Alternating red 16 against red 0 at delay 0 was
plainly visible on the screen and measured -0.03 ADU against 0.11 of noise. The
light was there; the window was in the wrong place.

So this sweeps the window across the whole frame and asks where the light is.

BACKGROUND IS SUBTRACTED AT EVERY DELAY, NOT ONCE. The stray light reaching the
sensor varies with delay -- that is one of the clearest results of the whole
session, with black-frame readings moving by hundreds of ADU across a frame. A
single background reading taken at one delay and subtracted everywhere would
inject that structure into the answer. So each delay is measured TWICE, at the
commanded level and at zero, and the difference is what is plotted. The two
readings are taken seconds apart at the same delay, so anything slow -- ambient
light, projector warm-up -- cancels.

The transmitted pixel is checked on every capture; a level that did not reach
the wire is reported rather than averaged in.
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
                         COLOURS, LEDCOL, EXPO_UNIT_US, TICK_US,
                         R_IMPCYC, R_IMPLVL, R_IMPRGB, R_ROICTL,
                         R_EXPO_LO, R_EXPO_HI)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--level", type=int, default=16, help="level under test")
    ap.add_argument("--colour", "--color", dest="colour", default="red",
                    choices=sorted(COLOURS))
    ap.add_argument("--step", type=float, default=100.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=250.0, help="exposure, us")
    ap.add_argument("--frames", type=int, default=20, help="frames per reading")
    ap.add_argument("--out", default="light_profile.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    shift = {"red": 16, "green": 8, "blue": 0}.get(a.colour, 16)
    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    gl = rd(ser, 0x5D) or 0
    if not (1000 < T_us < 60000) or (gl & 3) != 3:
        ser.close(); sys.exit(f"period {T_us} us / genlock 0x{gl:02X} -- not ready")

    wr(ser, R_IMPCYC, 0xD5)          # SOLID: every frame lit, no sequence
    wr(ser, R_IMPRGB, COLOURS[a.colour])
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)
    wr(ser, R_ROICTL, 0xC0)
    time.sleep(0.4)

    npts = int(T_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us"
          f"   ({100.0*expo_units*EXPO_UNIT_US/T_us:.1f} % of the frame)")
    print(f"colour         {a.colour}, SOLID field, level {a.level} vs level 0")
    print(f"delay sweep    0 .. {(npts-1)*a.step:.0f} us in {a.step:.0f} us "
          f"-> {npts} points")
    print(f"estimated      {npts*2*(a.frames+8)/(1e6/T_us)/60:.1f} min\n")

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        ln, = ax.plot([], [], lw=1.4, marker=".", ms=3, color=LEDCOL[a.colour])
        ax.axhline(0, color="#888888", lw=1)
        ax.set_xlim(-T_us * 0.01, T_us * 1.01)
        ax.set_xlabel("genlock delay within the projected frame (us)")
        ax.set_ylabel(f"level {a.level} minus level 0  (ADU)")
        ax.grid(alpha=0.3)
        fig.subplots_adjust(left=0.075, right=0.985, top=0.88, bottom=0.11)
        hdr = fig.text(0.075, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        live = (plt, fig, ax, ln, hdr)

    fh = open(a.out, "w", newline="")
    w = csv.writer(fh)
    w.writerow(["delay_us", "mean_level", "mean_zero", "difference"])
    fh.flush()

    def read_at(lvl):
        wr(ser, R_IMPLVL, lvl)
        time.sleep(0.20)
        ser.reset_input_buffer()
        rows = collect(ser, a.frames, timeout=5.0)
        if not rows:
            return None, None
        tlp = collections.Counter(t for m, n, t, c in rows).most_common(1)[0][0]
        good = [m for m, n, t, c in rows if n == 256]
        return (sum(good) / len(good) if good else None), ((tlp >> shift) & 0xFF)

    pts, nmis = [], 0
    t0 = time.time()
    try:
        for i in range(npts):
            d = i * a.step
            tick = int(round(d / TICK_US))
            set_delay(ser, tick)
            got, ok = wait_delay(ser, tick)
            if not ok:
                print(f"  delay {d:.0f} us did not land (holds {got}) -- skipped")
                continue
            m1, t1 = read_at(a.level)
            m0, t0p = read_at(0)
            if m1 is None or m0 is None:
                continue
            if t1 != a.level or t0p != 0:
                nmis += 1
            diff = m1 - m0
            pts.append((d, diff))
            w.writerow([d, round(m1, 2), round(m0, 2), round(diff, 2)])
            fh.flush()

            if live:
                plt, fig, ax, ln, hdr = live
                ln.set_data([p[0] for p in pts], [p[1] for p in pts])
                ys = [p[1] for p in pts]
                lo, hi = min(ys), max(ys)
                pad = max(2.0, (hi - lo) * 0.15)
                ax.set_ylim(lo - pad, hi + pad)
                hdr.set_text(
                    f"{a.colour} level {a.level} minus level 0    exposure "
                    f"{expo_units*EXPO_UNIT_US:.0f} us    frame {T_us:.0f} us"
                    + chr(10)
                    + f"delay {d:6.0f} us ({i+1}/{npts})    "
                      f"this point {diff:+7.2f} ADU    "
                      f"range {lo:+.2f} .. {hi:+.2f}    pixel!=cmd {nmis}")
                try:
                    if not plt.fignum_exists(fig.number):
                        raise RuntimeError
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except Exception:
                    print("plot gone -- continuing, CSV still being written")
                    live = None
            if i % 10 == 0 or i == npts - 1:
                print(f"  {i+1:4d}/{npts}  delay {d:8.0f} us   "
                      f"diff {diff:+8.2f} ADU   {(time.time()-t0)/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- what was captured is written")
    finally:
        wr(ser, R_IMPLVL, 0)
        wr(ser, R_ROICTL, 0x80)
        set_delay(ser, 0)
        ser.close()
        fh.close()

    print(f"\nwrote {a.out}  ({len(pts)} delays)")
    print(f"   pixel != commanded  {nmis}")
    if pts:
        ys = [p[1] for p in pts]
        pk = max(pts, key=lambda p: p[1])
        print(f"   difference range    {min(ys):+.2f} .. {max(ys):+.2f} ADU")
        print(f"   LARGEST at delay    {pk[0]:.0f} us  ({pk[1]:+.2f} ADU)")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()
