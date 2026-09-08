#!/usr/bin/env python3
"""Sweep the genlock delay and plot the five ROI means the FPGA sends as a block.

    python host/block_sweep.py COM6 --expo 5 --step 100 --live

WHAT THIS DOES NOT DO, WHICH IS THE POINT.

It does not decide which frame is which. The FPGA transmitted the sequence, samples the
top-left pixel back off its own output, and groups five consecutive ROI means into one
line that STARTS on the frame carrying TLP 255. Position in the line is the frame index,
by construction. This script reads five numbers and plots five numbers.

No phase counter, no rotation, no anchor, no majority vote, no re-binning, no rejection
of inconvenient delays. Every one of those existed in the per-frame tool to reconstruct
information the FPGA already had, and every one of them was a source of error: a seam at
a frame boundary, a plot that disagreed with its own CSV, points that moved while being
looked at.

The only host-side arithmetic is averaging repeats of the same delay, and that is shown
rather than hidden -- --raw plots every individual block instead.

Line format, 37 bytes:   B=aaa,bbb,ccc,ddd,eee,ffff,v,dddddd<CR><LF>
    aaa    frame 0 -- the one transmitted with top-left pixel 255
    bbb..eee   frames 1..4, in order, all transmitted black
    ffff   frame counter of frame 0
    v      1 = all five had npx == 256
    dddddd THE DELAY IN FORCE FOR THIS BLOCK, reported by the FPGA in 10 ns ticks --
           not the value this script wrote. A block spanning a delay change is
           dropped in fabric, so what arrives had one delay for all five frames.
"""
import argparse
import collections
import csv
import re
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial missing:  pip install pyserial")

SYNC, OP_W, OP_R = 0xA5, 0x57, 0x52
R_IMPCYC, R_IMPLVL = 0x11, 0x12
R_ROICTL, R_EXPO_LO, R_EXPO_HI = 0x17, 0x1A, 0x1B
R_DLY0, R_DLY1, R_DLY2, R_IMPRGB = 0x1C, 0x1D, 0x1E, 0x1F
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01}
# Built by concatenation so the source carries no backslash escapes. bytes([13, 10])
# is the CRLF the FPGA sends, and REQUIRING it is what stops a half-arrived line from
# matching on its fixed-width fields and being read twice.
BLOCK = re.compile(b"B=" + b"([0-9A-F]{3})," * 4
                   + b"([0-9A-F]{3}),([0-9A-F]{4}),([01]),([0-9A-F]{6})"
                   + bytes([13, 10]))
EXPO_UNIT_US = 0.375
TICK_US = 0.01


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
    wr(ser, R_DLY2, (ticks >> 16) & 0xFF)      # high byte commits


def collect(ser, nblocks, timeout=6.0):
    """Return [(m0..m4, fcnt, allok, delay_us)] -- whole blocks only.

    The DELAY COMES BACK FROM THE FPGA, it is not the value this script wrote. A block
    assembled across a delay change is dropped in fabric, so anything that arrives had
    one delay in force for all five of its frames.
    """
    out = []
    buf = bytearray()
    dl = time.time() + timeout
    while len(out) < nblocks and time.time() < dl:
        chunk = ser.read(ser.in_waiting or 1)
        if not chunk:
            continue
        buf += chunk
        ms = list(BLOCK.finditer(buf))
        if ms:
            for m in ms:
                out.append((int(m.group(1), 16), int(m.group(2), 16),
                            int(m.group(3), 16), int(m.group(4), 16),
                            int(m.group(5), 16), int(m.group(6), 16),
                            int(m.group(7)), int(m.group(8), 16) * TICK_US))
            buf = buf[ms[-1].end():]
        elif len(buf) > 4096:
            buf = buf[-64:]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--step", type=float, default=100.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--colour", "--color", dest="colour", default="red",
                    choices=sorted(COLOURS))
    ap.add_argument("--level", type=int, default=255)
    ap.add_argument("--blocks", type=int, default=12,
                    help="blocks captured per delay point (each is 5 frames)")
    ap.add_argument("--settle", type=int, default=2,
                    help="blocks discarded after each delay change")
    ap.add_argument("--raw", action="store_true",
                    help="plot EVERY block as a point instead of the per-delay mean")
    ap.add_argument("--out", default="block_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--endtoend", action="store_true",
                    help="lay the five frames END TO END on one 5*T axis (frame k at "
                         "k*T + delay) instead of overlaying them on 0..T")
    ap.add_argument("--check", action="store_true",
                    help="run the delay-0 ordering check and exit, without sweeping")
    ap.add_argument("--force", action="store_true",
                    help="sweep even if the pre-flight ordering check fails")
    a = ap.parse_args()

    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))

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

    wr(ser, R_IMPCYC, 5)
    wr(ser, R_IMPRGB, COLOURS[a.colour])
    wr(ser, R_IMPLVL, a.level & 0xFF)
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)       # commits
    wr(ser, R_ROICTL, 0x90)        # impulse_en | block stream (bit 4)
    time.sleep(0.4)

    # ---- PRE-FLIGHT: FRAME 0 AND FRAME 4 MUST AGREE AT DELAY 0 -----------------
    #
    # At delay 0 every frame is sampled at its own vsync, and frame 4's sample sits one
    # frame period before frame 0's. With the flash arriving about a frame late, both
    # land on the dark floor, so they must read within a few ADU of each other. They are
    # also ADJACENT IN THE SEQUENCE -- frame 4 is immediately followed by frame 0 -- so a
    # large gap between them cannot be light. It means the five numbers are not in the
    # order the line claims, which is the single failure this whole path keeps producing.
    #
    # Checked BEFORE the sweep because a mislabelled block produces a perfectly plausible
    # plot, and forty minutes of it is forty minutes wasted.
    set_delay(ser, 0)
    time.sleep(0.3)
    ser.reset_input_buffer()
    collect(ser, 2, timeout=8.0)
    chk = collect(ser, 20, timeout=12.0)
    if not chk:
        ser.close()
        sys.exit("PRE-FLIGHT FAILED: no blocks arrived at delay 0. Is the impulse "
                 "sequence running (ROICTL bit 7) and the camera streaming?")
    f = [sum(b[k] for b in chk) / len(chk) for k in range(5)]
    gap = abs(f[0] - f[4])
    tol = max(8.0, 0.02 * max(f))
    print("PRE-FLIGHT at delay 0, %d blocks:" % len(chk))
    print("   frame  0      1      2      3      4")
    print("   mean  %6.1f %6.1f %6.1f %6.1f %6.1f" % tuple(f))
    print("   |frame0 - frame4| = %.1f ADU   (tolerance %.1f)" % (gap, tol))
    if gap > tol:
        print("")
        print("PRE-FLIGHT FAILED. Frame 0 and frame 4 are adjacent in the sequence and")
        print("must agree at delay 0; they differ by %.1f ADU. The five numbers are not" % gap)
        print("in the order the block claims -- do not trust a sweep taken like this.")
        if not a.force:
            wr(ser, R_ROICTL, 0x80); ser.close()
            sys.exit("refusing to sweep (pass --force to override)")
        print("--force given: continuing anyway.")
    else:
        print("   PASS -- the block ordering is consistent.")
    print("")

    npts = int(T_us / a.step) + 1
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay sweep    0 .. {(npts-1)*a.step:.0f} us in {a.step:.0f} us "
          f"-> {npts} points")
    print(f"flash          {a.colour} at level {a.level}, 5-frame sequence")
    print(f"blocks/point   {a.blocks}  (+{a.settle} discarded)")
    print(f"estimated      {npts*(a.blocks+a.settle)*5/(1e6/T_us)/60:.1f} min\n")

    if a.check:
        wr(ser, R_ROICTL, 0x80)
        ser.close()
        return

    COLS = ["#c1322c", "#b5761a", "#1d7a44", "#1f5fb0", "#7a3fa0"]
    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(13, 6))
        lines = [ax.plot([], [], lw=1.2, marker="." if a.raw else None,
                         ls="none" if a.raw else "-", ms=2, color=COLS[k],
                         label=f"frame {k}" + ("  (TLP 255)" if k == 0 else ""))[0]
                 for k in range(5)]
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        if a.endtoend:
            # Frame k drawn at k*T + delay, so the five tile one 5-frame timeline.
            # The JOINS are the thing worth looking at: frame k's last sample and
            # frame k+1's first are 26 us apart in real time, so any step there is a
            # labelling error rather than light.
            ax.set_xlim(0, 5 * T_us)
            for kk in range(1, 5):
                ax.axvline(kk * T_us, color="#8892a0", lw=1, ls="--")
                ax.text(kk * T_us + 100, 1045, f"frame {kk}", fontsize=8,
                        color="#5c6672", va="top")
        else:
            ax.set_xlim(0, (npts - 1) * a.step)
        ax.set_ylim(0, 1060)
        ax.set_xlabel("time across the five-frame sequence (us)" if a.endtoend
                      else "genlock delay after the frame's own vsync (us)")
        ax.set_ylabel("ROI mean (10-bit ADU)")
        ax.legend(loc="upper right", fontsize=9, framealpha=0.9)
        ax.grid(alpha=0.3)
        ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us exposure "
                     f"-- starting")
        fig.canvas.manager.set_window_title("block_sweep -- live")
        fig.tight_layout()
        plt.show(block=False)
        live = (plt, fig, ax, lines)

    rows = []          # (delay_us, frame_index, mean)  -- nothing else
    nbad = 0
    nmismatch = 0
    t0 = time.time()
    try:
        for i in range(npts):
            d_us = i * a.step
            set_delay(ser, int(round(d_us / TICK_US)))
            ser.reset_input_buffer()
            collect(ser, a.settle, timeout=8.0)
            for blk in collect(ser, a.blocks, timeout=12.0):
                if not blk[6]:
                    nbad += 1
                    continue
                d_rep = blk[7]              # what the FPGA says was in force
                if abs(d_rep - d_us) > 0.005:
                    nmismatch += 1          # reported != requested: plot what it reports
                for k in range(5):
                    rows.append((d_rep, k, blk[k]))
            if live:
                plt, fig, ax, lines = live
                for k in range(5):
                    off = (k * T_us) if a.endtoend else 0.0
                    pts = [(x, v) for x, kk, v in rows if kk == k]
                    if a.raw:
                        lines[k].set_data([p[0] + off for p in pts],
                                          [p[1] for p in pts])
                    else:
                        agg = collections.defaultdict(list)
                        for x, v in pts:
                            agg[x].append(v)
                        xs = sorted(agg)
                        lines[k].set_data([x + off for x in xs],
                                          [sum(agg[x])/len(agg[x]) for x in xs])
                ax.set_title(f"{a.colour} flash, {expo_units*EXPO_UNIT_US:.1f} us "
                             f"exposure -- point {i+1}/{npts}"
                             + ("  [every block]" if a.raw else "  [mean per delay]"))
                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                if not plt.fignum_exists(fig.number):
                    print("\nplot closed -- stopping")
                    break
            if i % 10 == 0 or i == npts - 1:
                print(f"  {i+1:4d}/{npts}  delay {d_us:8.0f} us   "
                      f"{len(rows)//5:5d} blocks   {(time.time()-t0)/60:.1f} min")
    except KeyboardInterrupt:
        print("\ninterrupted -- writing what was captured")
    finally:
        wr(ser, R_ROICTL, 0x80)
        set_delay(ser, 0)
        ser.close()

    with open(a.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["delay_us", "frame", "mean"])
        w.writerows(rows)
    print(f"\nwrote {a.out}  ({len(rows)//5} blocks, {nbad} rejected for npx != 256)")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}  (the window you are looking at)")

    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()
