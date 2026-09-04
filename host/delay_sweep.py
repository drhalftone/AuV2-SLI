#!/usr/bin/env python3
"""Sweep the genlock delay and record ROI intensity across the whole 5-frame sequence.

    python host/delay_sweep.py                       # white, 50 us, full 5 frames
    python host/delay_sweep.py --colour red
    python host/delay_sweep.py --step 10 --frames 120   # finer, slower
    python host/delay_sweep.py --plot sweep.png

WHAT IT MEASURES. The exposure is pinned at one step width and walked across the
projected timeline; each point is the light collected in its own window. Because the
exposure EQUALS the step, the windows are contiguous and non-overlapping -- they tile
the sequence exactly once, with no gaps and no double counting, so each point is the
true energy in its own bin.

WHY IT ONLY SWEEPS ONE FRAME PERIOD. Genlock is 1:1 and every sample carries the
sequence phase it was TRIGGERED at, so a delay d on a phase-p frame samples absolute
time p*T + d. Sweeping d over one frame period and binning by phase therefore covers
all five frames -- 167 steps instead of 833, for the same coverage and five times the
speed. The redundancy is not lost either: --overlap sweeps past T so the same absolute
time is reached by two different (phase, delay) pairs, which is a direct check that
the phase model is right rather than an assumption.

THE TIME AXIS IS NOT THE FRAME START. Genlock fires on the RISING edge of ext_sync.
At a negative-vsync mode (800x600@120 is VPOL=0) that is the END of the vsync pulse,
so delay 0 already sits ~78.6 us into the frame. The offset is constant and is
reported in the header; subtract it before quoting a latency.

READ npx. Every sample must show 256 accumulated pixels or the mean means nothing.
"""
import argparse
import csv
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
R_MODEFORCE, R_ROICTL, R_EXPO_LO, R_EXPO_HI = 0x14, 0x17, 0x1A, 0x1B
R_DLY0, R_DLY1, R_DLY2, R_IMPRGB, R_IMPLVL = 0x1C, 0x1D, 0x1E, 0x1F, 0x12
R_IMPCYC = 0x11
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01}
LINE = re.compile(rb"R=([0-9A-F]{3}),([0-9A-F]{4}),([0-9A-F]{3}),([01]),([0-9A-F])\r\n")

EXPO_UNIT_US = 0.375        # exposure register LSB
TICK_US = 0.01              # genlock delay register LSB (10 ns)


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def rd(ser, addr, window=0.6):
    ser.reset_input_buffer()
    ser.write(bytes([SYNC, OP_R, addr, ck(OP_R + addr)]))
    buf = bytearray()
    dl = time.time() + window
    while time.time() < dl:
        buf += ser.read(ser.in_waiting or 1)
        for i in range(len(buf) - 2):
            if buf[i] == addr and ((buf[i] + buf[i + 1] + buf[i + 2]) & 0xFF) == 0:
                return buf[i + 1]
    return None


def set_delay(ser, ticks):
    wr(ser, R_DLY0, ticks & 0xFF)
    wr(ser, R_DLY1, (ticks >> 8) & 0xFF)
    wr(ser, R_DLY2, (ticks >> 16) & 0xFF)     # high byte commits


def collect(ser, nframes, timeout=4.0):
    """Read until nframes complete records have arrived. Returns [(mean,npx,phase)]."""
    out = []
    buf = bytearray()
    dl = time.time() + timeout
    while len(out) < nframes and time.time() < dl:
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            continue
        buf += chunk
        consumed = 0
        for m in LINE.finditer(buf):
            out.append((int(m.group(1), 16), int(m.group(3), 16), int(m.group(5), 16)))
            consumed = m.end()
        if consumed:
            buf = buf[consumed:]
        elif len(buf) > 4096:
            buf = buf[-64:]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--step", type=float, default=50.0, help="delay step, us (default 50)")
    ap.add_argument("--expo", type=float, default=None,
                    help="exposure, us (default: equal to --step, so bins tile exactly)")
    ap.add_argument("--colour", "--color", dest="colour", default="white",
                    choices=sorted(COLOURS))
    ap.add_argument("--level", type=int, default=255)
    ap.add_argument("--cycle", type=int, default=None, metavar="N",
                    help="frames per sequence (2..7). 2 = BRIGHT,K -- sweeps ~6x "
                         "faster than 5, because the span is N*T and the per-cycle "
                         "trigger rate is fps/N.")
    ap.add_argument("--frames", type=int, default=60,
                    help="frames captured per delay point (spread over 5 phases)")
    ap.add_argument("--settle", type=int, default=8,
                    help="frames discarded after each delay change")
    ap.add_argument("--overlap", type=float, default=0.0,
                    help="sweep this many EXTRA us past one frame period, so the same "
                         "absolute time is reached twice -- a check on the phase model")
    ap.add_argument("--out", default="delay_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--percycle", action="store_true",
                    help="trigger ONCE per 5-frame sequence, on the bright frame, and "
                         "sweep the delay across all five frames (ROICTL bit 5). No "
                         "phase binning, no trigger/frame pairing -- one reference edge.")
    ap.add_argument("--live", action="store_true",
                    help="plot the five per-frame curves as they stream in")
    a = ap.parse_args()

    expo_us = a.expo if a.expo is not None else a.step
    expo_units = max(1, int(round(expo_us / EXPO_UNIT_US)))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)

    per_ticks = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
                 | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per_ticks * TICK_US
    if not (1000 < T_us < 60000):
        ser.close(); sys.exit(f"implausible frame period {T_us} us -- is video running?")
    gl = rd(ser, 0x5D) or 0
    if not (gl & 1):
        ser.close(); sys.exit("genlock is not enabled (0x5D bit0 = 0)")

    if a.cycle is not None:
        if not (2 <= a.cycle <= 7):
            ser.close(); sys.exit('--cycle must be 2..7')
        wr(ser, R_IMPCYC, a.cycle)
        time.sleep(0.2)
    NCYC = rd(ser, R_IMPCYC)
    if NCYC is None or not (2 <= NCYC <= 7):
        NCYC = 5   # bitstream predates the register
    wr(ser, R_IMPRGB, COLOURS[a.colour])
    wr(ser, R_IMPLVL, a.level & 0xFF)
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)      # commits
    wr(ser, R_ROICTL, 0xE0 if a.percycle else 0xC0)    # impulse + stream (+div5)
    time.sleep(0.3)

    if a.percycle:
        # One trigger per sequence: the delay itself must cover all five frames,
        # because there is no longer a phase bin to fold them into.
        span_us = NCYC * T_us + a.overlap
    else:
        span_us = T_us + a.overlap
    npts = int(span_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay sweep    0 .. {span_us:.0f} us in {a.step:.0f} us  ->  {npts} points")
    if a.percycle:
        print(f"sequence       {NCYC} frames per cycle")
        print(f"trigger        ONCE per sequence, on the bright frame "
              f"({1e6/(NCYC*T_us):.1f} Hz)")
        print(f"coverage       {npts} bins across {NCYC*T_us/1000:.1f} ms, "
              f"all from ONE reference edge")
    else:
        print(f"phase binning  x{NCYC}  ->  {npts*NCYC} bins covering {NCYC*T_us/1000:.1f} ms")
    print(f"flash          {a.colour} at level {a.level}")
    print(f"estimated      {npts*(a.frames+a.settle)/ (1e6/T_us) / 60:.1f} min\n")

    rows = []
    bad_npx = 0
    t0 = time.time()

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        cols = ["#c1322c", "#b5761a", "#1d7a44", "#1f5fb0", "#7a3fa0"]
        fig, ax = plt.subplots(figsize=(13, 6))
        if a.percycle:
            lines = [ax.plot([], [], lw=1.0, color=cols[0],
                             label="one trigger per sequence")[0]]
            for k in range(1, NCYC):
                ax.axvline(k * T_us, color="#8892a0", lw=1, ls="--")
                ax.text(k * T_us + 80, 1040, f"frame {k}", fontsize=8,
                        color="#5c6672", va="top")
        else:
            lines = [ax.plot([], [], lw=1.2, color=cols[k],
                             label=f"frame {k}" + ("  (bright)" if k == 0 else ""))[0]
                     for k in range(5)]
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        ax.text(T_us, 1023, " 10-bit rail", color="#9a6614", fontsize=9,
                ha="right", va="bottom")
        ax.set_xlim(0, span_us)
        ax.set_ylim(0, 1060)
        ax.set_xlabel("genlock delay after that frame's own vsync (us)")
        ax.set_ylabel("ROI mean (10-bit ADU)")
        ax.grid(alpha=0.3)
        fig.canvas.manager.set_window_title("delay_sweep -- live")
        fig.tight_layout()
        plt.show(block=False)
        acc = [dict() for _ in range(1 if a.percycle else 5)]
        live = (plt, fig, ax, lines, acc)
    try:
        for i in range(npts):
            d_us = i * a.step
            set_delay(ser, int(round(d_us / TICK_US)))
            ser.reset_input_buffer()
            collect(ser, a.settle, timeout=8.0)        # discard the transition
            for mean, npx, ph in collect(ser, a.frames, timeout=12.0):
                if npx != 256:
                    bad_npx += 1
                    continue
                if a.percycle:
                    # one reference edge: absolute time IS the delay, and the
                    # reported phase is not used at all
                    rows.append((d_us, ph, d_us, mean))
                else:
                    if ph > 4:
                        continue
                    rows.append((d_us, ph, ph * T_us + d_us, mean))
            if live:
                plt, fig, ax, lines, acc = live
                for _, ph, _, mean in rows[-a.frames:]:
                    acc[0 if a.percycle else ph].setdefault(d_us, []).append(mean)
                nrail = 0
                ntot = 0
                for k in range(len(lines)):
                    xs = sorted(acc[k])
                    ys = [sum(acc[k][x]) / len(acc[k][x]) for x in xs]
                    lines[k].set_data(xs, ys)
                    nrail += sum(1 for y in ys if y >= 1015)
                    ntot += len(ys)
                ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us "
                             f"exposure -- point {i+1}/{npts}, "
                             f"{100.0*nrail/max(ntot,1):.0f}% of bins AT THE RAIL")
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                if not plt.fignum_exists(fig.number):
                    print("\nplot closed -- stopping")
                    break
            if i % 20 == 0 or i == npts - 1:
                el = time.time() - t0
                print(f"  {i+1:4d}/{npts}  delay {d_us:8.0f} us   "
                      f"{len(rows):6d} samples   {el/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- writing what was captured")
    finally:
        wr(ser, R_ROICTL, 0x80)
        set_delay(ser, 0)
        ser.close()

    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["delay_us", "phase", "abs_us", "mean"])
        w.writerows(rows)
    print(f"\nwrote {a.out}  ({len(rows)} samples, {bad_npx} rejected for npx != 256)")

    # fold to one value per absolute-time bin
    agg = {}
    for _, _, t, m in rows:
        agg.setdefault(round(t, 3), []).append(m)
    xs = sorted(agg)
    ys = [sum(agg[t]) / len(agg[t]) for t in xs]
    if not xs:
        return
    pk = max(range(len(ys)), key=lambda i: ys[i])
    print(f"peak  {ys[pk]:.1f} at {xs[pk]:.0f} us   floor {min(ys):.1f}   "
          f"contrast {(max(ys)-min(ys))/max(ys)*100:.1f}%")
    print(f"NOTE: t=0 is the genlock trigger edge, ~78.6 us into the frame at a "
          f"negative-vsync mode.")

    if a.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(13, 5))
        ax.plot(xs, ys, lw=0.9, color="#c1322c")
        for k in range(1, 5):
            ax.axvline(k * T_us, color="#8892a0", lw=1, ls="--")
            ax.text(k * T_us, max(ys), f" frame {k}", fontsize=8, color="#5c6672",
                    va="top")
        ax.set_xlabel("time after the bright frame's genlock trigger (us)")
        ax.set_ylabel("ROI mean (10-bit ADU)")
        ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us exposure, "
                     f"{a.step:.0f} us steps")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")


if __name__ == "__main__":
    main()
