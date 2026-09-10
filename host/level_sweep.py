#!/usr/bin/env python3
"""Hold the delay fixed and sweep the commanded LEVEL: the projector's transfer curve.

    python host/level_sweep.py COM6 --delay 0 --leds r --live
    python host/level_sweep.py COM6 --delay 0 --leds rgb --step 4 --live

A DIFFERENT QUESTION FROM THE DELAY SWEEP. Sweeping delay asks "when is the light
on"; this asks "how much light comes out for a commanded level, and is that
relationship well behaved". They fail in different ways, and a projector that
looks fine in one can be badly non-monotonic in the other.

WHAT IT IS LOOKING FOR. The 5-frame sequence has one flash frame and four dark
ones (or the reverse under --invert). Only the flash frame's level is being
commanded -- the dark frames are commanded 0 at every point of this sweep. So:

  * the FLASH position should rise with the commanded level. Its shape is the
    projector's transfer curve, including whatever gamma and LED drive mapping
    sits in front of the panel.
  * the DARK positions should stay flat at the floor, because nothing about what
    they are told to display changes. IF THEY DO NOT -- if a band of commanded
    red lifts the background on frames that were commanded black -- that is the
    projector's own behaviour leaking between frames, and the level range over
    which it happens is the measurement.

That second case is the reason this script exists, so the dark positions are
plotted with the same prominence as the flash one rather than being treated as a
baseline to subtract.

LEVEL 0 HAS NO MARKER. The flash frame's transmitted pixel is (level,0,0), so at
level 0 the whole sequence is black and there is nothing to anchor the position
binning to. Those points are still captured -- with an arbitrary origin, flagged
in the summary -- because the level-0 floor is exactly the reference the rest of
the curve is measured against.

Line format, 21 bytes:  R=mmm,nnn,rrggbb,cc<CR><LF>  -- see roi_line.v.
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

# REUSED, NOT RETYPED. rd() in particular has a three-byte reply format that is
# easy to get subtly wrong from memory -- doing so once produced a parser that
# matched stray bytes and reported a confident 0 for the frame period.
sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from frame_sweep import (wr, rd, set_delay, wait_delay, collect,      # noqa: E402
                         COLOURS, LEDCOL, EXPO_UNIT_US, TICK_US,
                         R_IMPCYC, R_IMPLVL, R_IMPRGB, R_ROICTL,
                         R_EXPO_LO, R_EXPO_HI)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="genlock delay held fixed for the whole sweep, us")
    ap.add_argument("--leds", default="r",
                    help="which LEDs to sweep in turn: 'r', 'rgb', 'red,green' ...")
    ap.add_argument("--step", type=int, default=1, help="level step (default 1)")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--bright", type=int, default=2, choices=range(0, 5), metavar="P",
                    help="which sequence position carries the flash")
    ap.add_argument("--invert", action="store_true",
                    help="bright everywhere EXCEPT --bright")
    ap.add_argument("--solid", action="store_true",
                    help="NO SEQUENCE AT ALL: every frame lit at the commanded "
                         "level, so the field is steady and does not blink. This "
                         "is the plain transfer curve -- what comes out for what "
                         "was asked, with no frame-to-frame structure in it.")
    ap.add_argument("--frames", type=int, default=30,
                    help="camera frames captured per level")
    ap.add_argument("--settle", type=int, default=10,
                    help="frames discarded after each level change")
    ap.add_argument("--out", default="level_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    short = {"r": "red", "g": "green", "b": "blue", "w": "white"}
    spec = a.leds.lower()
    leds = ([short[c] for c in spec if c in short] if "," not in spec
            else [s.strip() for s in spec.split(",")])
    if not leds or any(c not in COLOURS for c in leds):
        sys.exit(f"--leds: don't understand {a.leds!r}; use r/g/b/w or names")

    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))
    levels = list(range(0, 256, max(1, a.step)))
    if levels[-1] != 255:
        levels.append(255)

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    if not (1000 < T_us < 60000):
        ser.close(); sys.exit(f"implausible frame period {T_us} us -- is video running?")
    gl = rd(ser, 0x5D) or 0
    if not (gl & 3) == 3:
        ser.close(); sys.exit(f"genlock 0x{gl:02X}: needs enabled AND live "
                              f"(bit0 and bit1) or no triggers fire")

    if a.solid:
        # EVERY FRAME LIT, and it needs no new hardware. level is
        #     ((phase == bpos) ^ inv) ? lvl : 0
        # so pointing bpos at position 5 -- which a 5-frame cycle running phases
        # 0..4 never reaches -- makes the comparison permanently false, and the
        # invert bit turns that into "always lit". 0xD5 = invert, bpos 5, cycle 5.
        wr(ser, R_IMPCYC, 0xD5)
    else:
        wr(ser, R_IMPCYC, (0x80 if a.invert else 0) | ((a.bright & 7) << 4) | 5)
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)       # commits
    want = int(round(a.delay / TICK_US))
    set_delay(ser, want)
    got, ok = wait_delay(ser, want)
    if not ok:
        wr(ser, R_ROICTL, 0x80); ser.close()
        sys.exit(f"delay write did not land: asked {want} ticks, register holds {got}")
    wr(ser, R_ROICTL, 0xC0)
    time.sleep(0.4)

    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay          {a.delay:.0f} us  (HELD FIXED, register reads {got} ticks)")
    print(f"LEDs           {', '.join(leds)}")
    print(f"levels         0 .. 255 step {a.step}  -> {len(levels)} points")
    print("sequence       SOLID -- every frame lit, no blinking" if a.solid else
          (f"sequence       5 frames, "
           + ("DARK" if a.invert else "flash") + f" frame at position {a.bright}"))
    print(f"estimated      {len(levels)*(a.frames+a.settle)/(1e6/T_us)/60:.1f} "
          f"min per LED\n")

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        ax.set_xlim(-4, 260)
        ax.set_ylim(0, 1080)
        ax.set_xlabel("top-left pixel READ BACK FROM THE CAMERA STREAM "
                      "(same telemetry line as the mean)")
        ax.set_ylabel("ROI mean (ADU)")
        ax.grid(alpha=0.3)
        fig.subplots_adjust(left=0.065, right=0.985, top=0.90, bottom=0.11)
        hdr = fig.text(0.065, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        live = (plt, fig, ax, hdr)

    # ---- PRE-FLIGHT: THE TLP MUST CHANGE, AND MUST BE THE COMMANDED VALUE ----
    #
    # Everything downstream assumes the camera stream reports the pixel that was
    # actually transmitted. If that field is stuck -- a dead readback, a frozen
    # sample point, a level write that never lands -- then a sweep still produces
    # a full CSV and a plausible plot, and every point in it is wrong in a way
    # nothing else can detect. So it is checked FIRST, against three levels that
    # must give three different answers, and a failure stops the run here rather
    # than being counted in a summary at the end.
    print("PRE-FLIGHT: does the top-left pixel in the CAMERA STREAM follow the "
          "commanded level?")
    probe_led = leds[0]
    probe_shift = {"red": 16, "green": 8, "blue": 0}.get(probe_led, 16)
    wr(ser, R_IMPRGB, COLOURS[probe_led])
    time.sleep(0.2)
    seen_probe = []
    for plv in (0, 128, 255):
        wr(ser, R_IMPLVL, plv)
        time.sleep(0.45)
        ser.reset_input_buffer()
        pr = collect(ser, 20, timeout=6.0)
        if not pr:
            wr(ser, R_ROICTL, 0x80); ser.close()
            sys.exit("PRE-FLIGHT FAILED: no camera frames arrived at all.")
        ptlp = collections.Counter(t for m, n, t, c in pr).most_common(1)[0][0]
        pgot = (ptlp >> probe_shift) & 0xFF
        print(f"   commanded {plv:3d}  ->  camera reports TLP {ptlp:06X}  "
              f"(channel value {pgot:3d})   {'ok' if pgot == plv else 'WRONG'}")
        seen_probe.append((plv, ptlp, pgot))
    distinct = len(set(t for _l, t, _g in seen_probe))
    wrong = [p for p in seen_probe if p[2] != p[0]]
    if distinct < 3 or wrong:
        wr(ser, R_ROICTL, 0x80); ser.close()
        if distinct < 3:
            sys.exit(f"PRE-FLIGHT FAILED: the TLP took only {distinct} distinct "
                     f"value(s) across commanded 0/128/255. It is NOT tracking the "
                     f"commanded level, so any sweep would be measuring a stuck "
                     f"readback. Refusing to run.")
        sys.exit(f"PRE-FLIGHT FAILED: the TLP changed but does not match what was "
                 f"commanded: {[(l, '%06X' % t, g) for l, t, g in wrong]}. "
                 f"Refusing to run.")
    print("   PASS -- three commands, three distinct correct pixels from the "
          "camera stream.\n")

    fh = open(a.out, "w", newline="")
    wcsv = csv.writer(fh)
    # THE TRANSMITTED PIXEL IS A COLUMN, not an assumption. It is the only
    # independent evidence that the commanded level actually reached the wire --
    # without it, "the projector did not respond" and "the level never changed"
    # produce identical CSVs. Cheap to carry and it makes the run self-checking.
    wcsv.writerow(["led", "level", "position", "tlp_r", "tlp_g", "tlp_b", "mean"])
    fh.flush()

    tot = collections.Counter()
    t0 = time.time()
    stop = False
    try:
        for led in leds:
            if stop:
                break
            wr(ser, R_IMPRGB, COLOURS[led])
            time.sleep(0.2)
            rows = []
            absn = [0]
            anchor = [None]
            last_cc = None
            if live:
                plt, fig, ax, hdr = live
                # The FLASH position is drawn solid and the four dark positions
                # dashed, so "did the background move" is answerable at a glance
                # without a legend.
                if a.solid:
                    lset = [ax.plot([], [], lw=1.6, marker=".", ms=3,
                                    color=LEDCOL[led])[0]]
                else:
                    lset = [ax.plot([], [], lw=1.4 if k == a.bright else 1.0,
                                    ls="-" if k == a.bright else "--",
                                    alpha=0.9 if k == a.bright else 0.65,
                                    color=LEDCOL[led])[0] for k in range(5)]
            for li, lvl in enumerate(levels):
                wr(ser, R_IMPLVL, lvl & 0xFF)
                ser.reset_input_buffer()
                collect(ser, a.settle, timeout=8.0)
                last_cc = None
                for mean, npx, tlp, cc in collect(ser, a.frames, timeout=12.0):
                    if npx != 256:
                        tot["npx"] += 1
                        continue
                    if last_cc is not None:
                        absn[0] += (cc - last_cc) & 0xFF
                        if ((last_cc + 1) & 0xFF) != cc:
                            tot["gaps"] += 1
                    last_cc = cc
                    if a.solid:
                        # Every frame is identical, so there is nothing to bin on
                        # and nothing that needs binning. One series.
                        pos = 0
                    elif (tlp == 0) if a.invert else (tlp != 0):
                        anchor[0] = absn[0]
                        pos = a.bright
                    elif anchor[0] is not None:
                        pos = (a.bright + absn[0] - anchor[0]) % 5
                    else:
                        pos = absn[0] % 5
                        tot["nomarker"] += 1
                    rows.append((lvl, pos, tlp, mean))

                # The MOST RECENT pair actually seen in the stream, for the
                # header. Taken from the same rows the plot is drawn from, so the
                # numbers on screen and the points on screen cannot disagree.
                _cur = [(t2, v2) for l2, _k2, t2, v2 in rows if l2 == lvl]
                cur_tlp = (collections.Counter(t for t, _v in _cur)
                           .most_common(1)[0][0]) if _cur else 0
                cur_mean = (sum(v for _t, v in _cur) / len(_cur)) if _cur else 0.0

                if live:
                    # X IS THE PIXEL THE CAMERA REPORTED, NOT THE COMMANDED LEVEL.
                    #
                    # Every telemetry line carries the transmitted top-left pixel
                    # AND the ROI mean FOR THE SAME FRAME, so plotting one against
                    # the other pairs them by construction. The axes cannot drift
                    # out of step with each other or with the projector, and
                    # nothing assumes the commanded level was what was actually on
                    # screen when that frame was exposed.
                    #
                    # The commanded level is still written to the CSV and still
                    # checked against the pixel -- it is just not what is drawn.
                    shift = {"red": 16, "green": 8, "blue": 0}.get(led, 16)
                    for k in range(len(lset)):
                        agg = collections.defaultdict(list)
                        for _x, kk, t, v in rows:
                            if a.solid or kk == k:
                                agg[(t >> shift) & 0xFF].append(v)
                        xs = sorted(agg)
                        lset[k].set_data(xs, [sum(agg[x])/len(agg[x]) for x in xs])
                    hdr.set_text(
                        f"LED {led:5s}  delay {a.delay:.0f} us HELD"
                        f"   exposure {expo_units*EXPO_UNIT_US:.2f} us"
                        f"   frame {T_us:.1f} us ({1e6/T_us:.2f} Hz)"
                        + chr(10)
                        + f"TLP FROM CAMERA {cur_tlp:06X}  "
                        f"R={(cur_tlp >> 16) & 0xFF:3d} "
                        f"G={(cur_tlp >> 8) & 0xFF:3d} "
                        f"B={cur_tlp & 0xFF:3d}"
                        f"       MEAN {cur_mean:7.1f}"
                        + chr(10)
                        + f"commanded {lvl}/255 ({li+1}/{len(levels)})"
                        f"   x = pixel read back from the camera stream"
                        f"   {len(rows)} frames"
                        f"   npx!=256 {tot['npx']}   gaps {tot['gaps']}"
                        f"   pixel!=cmd {tot['pixel mismatch']}"
                        + ("   SOLID field, every frame lit" if a.solid else
                           f"   solid = flash position {a.bright}, dashed = the "
                           f"four commanded-dark frames"))
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                    if not plt.fignum_exists(fig.number):
                        print("\nplot closed -- stopping")
                        stop = True
                        break
                # WRITE THIS LEVEL NOW. Holding a whole LED sweep in memory and
                # writing at the end means nothing reaches disk for minutes, so a
                # run cannot be inspected while it happens and an interruption
                # loses everything. One flush per level costs nothing.
                for l2, k2, t2, v2 in rows:
                    if l2 == lvl:
                        wcsv.writerow([led, l2, k2, (t2 >> 16) & 0xFF,
                                       (t2 >> 8) & 0xFF, t2 & 0xFF, v2])
                fh.flush()

                # CHECK THE PIXEL AGAINST THE COMMAND AS WE GO. The channel
                # under test must equal the commanded level; if it does not, the
                # write did not land and every point after it is meaningless.
                seen = set(t for l2, _k, t, _v in rows if l2 == lvl)
                ch = {"red": 16, "green": 8, "blue": 0}.get(led, 16)
                bad = [t for t in seen if ((t >> ch) & 0xFF) != lvl]
                if bad:
                    tot["pixel mismatch"] += 1
                    print("  !! %s level %d: transmitted pixel %s does not carry "
                          "the commanded level" % (led, lvl,
                                                   ", ".join("%06X" % t for t in sorted(bad)[:3])))
                if li % 8 == 0 or li == len(levels) - 1:
                    cur = [(t2, v2) for l2, _k, t2, v2 in rows if l2 == lvl]
                    if cur:
                        tt = collections.Counter(t for t, _v in cur).most_common(1)[0][0]
                        mv = sum(v for _t, v in cur) / len(cur)
                        print(f"  {led:5s} cmd {lvl:3d} -> TLP {tt:06X} "
                              f"(R={(tt >> 16) & 0xFF:3d} G={(tt >> 8) & 0xFF:3d} "
                              f"B={tt & 0xFF:3d})   mean {mv:6.1f}   "
                              f"{(time.time()-t0)/60:.1f} min")

            # rows were already written per level below; nothing to do here.
            tot["frames"] += len(rows)
    except KeyboardInterrupt:
        print("\ninterrupted -- everything captured so far is already written")
    finally:
        wr(ser, R_ROICTL, 0x80)
        wr(ser, R_IMPLVL, 255)
        set_delay(ser, 0)
        ser.close()
        fh.close()

    print(f"\nwrote {a.out}  ({tot['frames']} frames)")
    print(f"   npx != 256            {tot['npx']}")
    print(f"   trigger-ordinal gaps  {tot['gaps']}")
    print(f"   frames with no marker {tot['nomarker']}   (level 0: ORIGIN arbitrary)")
    print(f"   pixel != commanded    {tot['pixel mismatch']}   (levels whose "
          f"transmitted pixel did not match)")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")
    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()
