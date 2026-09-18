#!/usr/bin/env python3
"""Profile the projector's LEDs: sweep the genlock delay, one point per camera frame.

    python host/frame_sweep.py COM6 --expo 5 --endtoend --live
    python host/frame_sweep.py COM6 --leds rgb --passes 0 --endtoend --live

WHAT THE FPGA SENDS, AND WHAT IT DOES NOT.

    R=mmm,nnn,rrggbb,cc<CR><LF>        21 bytes, one per camera frame

        mmm     ROI mean, 10-bit
        nnn     pixels accumulated -- MUST be 100 (256)
        rrggbb  the TOP-LEFT PIXEL AS TRANSMITTED on the projected frame that was
                on screen when this frame's exposure was triggered
        cc      the TRIGGER ORDINAL, +1 per trigger issued

That is the whole camera-side output. It does not group frames and it does not
report the delay. All the grouping this script does is a few lines at the bottom
of the capture loop.

ONE LED AT A TIME, EACH IN ITS OWN COLOUR. --leds rgb sweeps red, then green,
then blue, driving one primary at a time so each LED is profiled separately, and
draws each sweep in that LED's colour. Position within the 5-frame sequence is
given by the x band under --endtoend, so colour is free to mean "which LED".

--passes REPEATS THE WHOLE CYCLE, drawing each new sweep on top of the old ones
rather than replacing them. That is what makes a long unattended run useful: the
spread between passes IS the repeatability, and a drift over an hour shows up as
lines that separate. --passes 0 runs until you close the plot window.

WRITTEN INCREMENTALLY. The CSV is flushed after every completed sweep, so a run
left going over lunch survives an interruption with everything up to that point
already on disk. A single sweep that fails is logged and the cycle continues --
an unattended profiler that aborts the whole night on one bad register write is
worse than one that records the gap.

THE DELAY IS NOT ASSUMED. The line does not carry it, so this script READS THE
DELAY REGISTER BACK (0x5A..0x5C) after every write and refuses to sweep against a
delay that is not in force. The readback is POLLED, not read once: those
registers come from a snapshot the camera republishes at ~10 Hz, so an immediate
read returns the PREVIOUS value and a single-shot check would reject every valid
write. See wait_delay().

WHAT IS CHECKED, AND REPORTED RATHER THAN COUNTED AND DISCARDED:
    npx != 256           the ROI drifted off the sensor; the mean is meaningless
    trigger-ordinal gaps a frame was lost -- by the FPGA, the sensor or the link
    frames with no marker an all-black sequence: grouping real, ORIGIN arbitrary
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
# The SECOND colour of the sequence -- the odd frame out. Host-writable, so
# RRWRR and friends need no rebuild: the four take (IMPLVL, IMPRGB) and the
# one at --bright takes (IMPLVL2, IMPRGB2).
R_IMPLVL2, R_IMPRGB2 = 0x07, 0x08
COLOURS = {"white": 0x07, "red": 0x04, "green": 0x02, "blue": 0x01}
# The line colour IS the LED being profiled. Chosen dark enough to stay legible
# where many passes overlap.
LEDCOL = {"red": "#d0242a", "green": "#1a8f3c", "blue": "#1f5fd0",
          "white": "#555555"}
EXPO_UNIT_US = 0.375
TICK_US = 0.01

# Built by concatenation so the source carries no backslash escapes, and the CRLF
# is REQUIRED -- that is what stops a half-arrived line from matching on its
# fixed-width fields and being counted twice.
#
# THE LINE GREW TWO FIELDS: ,hh,ll -- ROI pixels saturated (>=1020) and clamped
# low (<=3). They are OPTIONAL here, so this matches the 21-byte line from older
# bitstreams and the 27-byte line from newer ones. Note the CRLF anchor means a
# parser that does NOT know about them rejects the new line entirely -- it does
# not quietly read the first four fields.
LINE = re.compile(b"R=([0-9A-F]{3}),([0-9A-F]{3}),([0-9A-F]{6}),([0-9A-F]{2})"
                  + b"(?:,([0-9A-F]{2}),([0-9A-F]{2}))?"
                  + bytes([13, 10]))


def ck(s):
    return (256 - (s & 0xFF)) & 0xFF


def wr(ser, addr, val):
    ser.write(bytes([SYNC, OP_W, addr, val, ck(OP_W + addr + val)]))
    time.sleep(0.02)


def rd(ser, addr, window=0.6):
    # The reply is THREE bytes: addr, value, checksum. No sync prefix. Retyping
    # this from memory once produced a 4-byte parser that matched stray bytes and
    # returned a confident 0 for the frame period -- which reads exactly like a
    # board that is not running.
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
    wr(ser, R_DLY2, (ticks >> 16) & 0xFF)


def get_delay(ser):
    lo, mid, hi = rd(ser, 0x5A), rd(ser, 0x5B), rd(ser, 0x5C)
    if None in (lo, mid, hi):
        return None
    return lo | (mid << 8) | (hi << 16)


def wait_delay(ser, want, timeout=1.5):
    """Poll 0x5A..0x5C until the FPGA's snapshot shows the value just written.

    THE READBACK LAGS THE WRITE. Those registers are served out of the cam_stat
    snapshot, republished on a ~10 Hz tick, so a read taken immediately after a
    write returns the PREVIOUS delay for up to 100 ms. Measured: writing 12345
    then reading returned 0, and writing 0 then reading returned 57 -- the low
    byte of 12345, one step behind.

    Returns (value, matched). False means the write genuinely did not land, not
    that it was read too early.
    """
    t0 = time.time()
    got = None
    while time.time() - t0 < timeout:
        got = get_delay(ser)
        if got == want:
            return got, True
    return got, False


def collect(ser, nframes, timeout=6.0, sat=False):
    """Return up to nframes tuples of (mean, npx, tlp, tcnt).

    sat=True returns (mean, npx, tlp, tcnt, nhi, nlo) instead: the ROI's saturated
    and clamped-low pixel counts. Both are None from a bitstream too old to send
    them -- which is not the same as zero, and callers must not treat it so.
    """
    out, buf, t0 = [], b"", time.time()
    while len(out) < nframes and time.time() - t0 < timeout:
        buf += ser.read(4096)
        ms = list(LINE.finditer(buf))
        if ms:
            for m in ms:
                row = (int(m.group(1), 16), int(m.group(2), 16),
                       int(m.group(3), 16), int(m.group(4), 16))
                if sat:
                    row += ((int(m.group(5), 16) if m.group(5) else None),
                            (int(m.group(6), 16) if m.group(6) else None))
                out.append(row)
            buf = buf[ms[-1].end():]
        elif len(buf) > 8192:
            buf = buf[-64:]
    return out[:nframes]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="COM6")
    ap.add_argument("--step", type=float, default=100.0, help="delay step, us")
    ap.add_argument("--expo", type=float, default=5.0, help="exposure, us")
    ap.add_argument("--colour", "--color", dest="colour", default="red",
                    choices=sorted(COLOURS), help="single-LED mode (default red)")
    ap.add_argument("--leds", default=None,
                    help="sweep several LEDs in turn, e.g. 'rgb' or "
                         "'red,green,blue'. Overrides --colour.")
    ap.add_argument("--passes", type=int, default=1,
                    help="repeat the whole cycle N times, stacking each new sweep "
                         "on the plot. 0 = until the plot window is closed.")
    ap.add_argument("--level", type=int, default=255,
                    help="level of the FOUR frames that are not --bright")
    ap.add_argument("--level2", type=int, default=None,
                    help="level of the odd frame at --bright (default: same as "
                         "--level, i.e. a solid field)")
    ap.add_argument("--colour2", "--color2", dest="colour2", default=None,
                    choices=sorted(COLOURS),
                    help="colour of the odd frame at --bright (default: same as "
                         "--colour). RRWRR is --colour red --level 16 "
                         "--colour2 white --level2 255")
    ap.add_argument("--bright", type=int, default=0, choices=range(0, 5), metavar="P",
                    help="which position in the 5-frame sequence carries the flash "
                         "(or, with --invert, the single DARK frame)")
    ap.add_argument("--invert", action="store_true",
                    help="INVERT the sequence: bright everywhere except --bright, "
                         "so KKRKK becomes RRKRR. A dark notch in a lit field "
                         "measures the switching edges even when the field rails, "
                         "which a single flash cannot.")
    ap.add_argument("--frames", type=int, default=60,
                    help="camera frames captured per delay point")
    ap.add_argument("--settle", type=int, default=10,
                    help="frames discarded after each delay change")
    ap.add_argument("--endtoend", action="store_true",
                    help="lay the five positions END TO END on one 5*T axis "
                         "(position k at k*T + delay) instead of overlaying them")
    ap.add_argument("--baseline", default=None,
                    help="a CSV from an earlier sweep (typically KKKKK) to draw in "
                         "BLACK behind the live data. Everything measured here "
                         "sits on top of the projector's own content-independent "
                         "emission; showing it makes clear which features belong "
                         "to the light being commanded and which were already "
                         "there.")
    ap.add_argument("--out", default="frame_sweep.csv")
    ap.add_argument("--plot", help="also write a PNG")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    if a.leds:
        short = {"r": "red", "g": "green", "b": "blue", "w": "white"}
        spec = a.leds.lower()
        leds = ([short[c] for c in spec if c in short] if "," not in spec
                else [s.strip() for s in spec.split(",")])
        bad = [c for c in leds if c not in COLOURS]
        if bad or not leds:
            sys.exit(f"--leds: don't know {bad or spec!r}; use r/g/b/w or names")
    else:
        leds = [a.colour]

    expo_units = max(1, int(round(a.expo / EXPO_UNIT_US)))

    ser = serial.Serial(a.port, 115200, timeout=0.05)
    time.sleep(0.3)
    per = ((rd(ser, 0x4A) or 0) | ((rd(ser, 0x4B) or 0) << 8)
           | ((rd(ser, 0x4C) or 0) << 16))
    T_us = per * TICK_US
    if not (1000 < T_us < 60000):
        ser.close(); sys.exit(f"implausible frame period {T_us} us -- is video running?")
    gl = rd(ser, 0x5D) or 0
    if not (gl & 1):
        ser.close(); sys.exit("genlock is not enabled (0x5D bit0 = 0)")
    if not (gl & 2):
        ser.close(); sys.exit("genlock is enabled but NOT LIVE (0x5D bit1 = 0): the "
                              "projector vsync is not reaching the camera, so no "
                              "triggers will fire at all.")

    # IMPCYC 0x11: [2:0] cycle length, [6:4] commanded position, [7] invert.
    wr(ser, R_IMPCYC, (0x80 if a.invert else 0) | ((a.bright & 7) << 4) | 5)
    wr(ser, R_IMPLVL, a.level & 0xFF)
    # The odd frame's pair. Defaulting them to the SAME values as the four makes
    # an unspecified second colour a solid field rather than a surprise.
    lvl2 = a.level if a.level2 is None else a.level2
    wr(ser, R_IMPLVL2, lvl2 & 0xFF)

    def expect(mask, lvl):
        """The exact pixel a frame with this colour mask and level transmits."""
        return (((lvl if mask & 4 else 0) << 16)
                | ((lvl if mask & 2 else 0) << 8)
                | (lvl if mask & 1 else 0))
    wr(ser, R_EXPO_LO, expo_units & 0xFF)
    wr(ser, R_EXPO_HI, (expo_units >> 8) & 0xFF)       # commits
    wr(ser, R_ROICTL, 0xC0)        # impulse_en | per-frame stream
    time.sleep(0.4)

    npts = int(T_us / a.step) + 1
    per_sweep_min = npts * (a.frames + a.settle) / (1e6 / T_us) / 60
    print(f"frame period   {T_us:.1f} us   ({1e6/T_us:.2f} Hz)")
    print(f"exposure       {expo_units} units = {expo_units*EXPO_UNIT_US:.2f} us")
    print(f"delay sweep    0 .. {(npts-1)*a.step:.0f} us in {a.step:.0f} us "
          f"-> {npts} points")
    print(f"the four        {', '.join(leds)} at level {a.level}")
    print(f"the odd frame   {a.colour2 or 'same colour'} at level {lvl2}, "
          f"position {a.bright}")
    print(f"passes         {'until the window is closed' if a.passes == 0 else a.passes}")
    print(f"frames/point   {a.frames}  (+{a.settle} discarded)")
    print(f"estimated      {per_sweep_min:.1f} min per sweep, "
          f"{per_sweep_min*len(leds):.1f} min per pass\n")

    # ---- the baseline, loaded before the axes exist so it can be drawn first --
    base = None
    if a.baseline:
        try:
            with open(a.baseline, newline="") as bfh:
                bagg = collections.defaultdict(list)
                for r in csv.DictReader(bfh):
                    bagg[float(r["delay_us"])].append(float(r["mean"]))
            # POOLED ACROSS POSITIONS ON PURPOSE. A KKKKK baseline has no marker,
            # so its five positions are an arbitrary rotation -- they describe the
            # same thing and averaging them is the honest summary.
            base = {d: sum(v) / len(v) for d, v in bagg.items()}
            print(f"baseline       {a.baseline}: {len(base)} delays, "
                  f"{min(base.values()):.0f} .. {max(base.values()):.0f} ADU")
        except Exception as e:
            print(f"baseline       could not read {a.baseline}: {e}")
            base = None

    live = None
    if a.live:
        import matplotlib
        matplotlib.use("TkAgg")
        import matplotlib.pyplot as plt
        plt.ion()
        fig, ax = plt.subplots(figsize=(13, 6))
        ax.axhline(1023, color="#9a6614", lw=1, ls="--")
        if base:
            bx = sorted(base)
            nb = 5 if a.endtoend else 1
            for k in range(nb):
                off = (k * T_us) if a.endtoend else 0.0
                ax.plot([x + off for x in bx], [base[x] for x in bx],
                        lw=1.3, color="black", alpha=0.85, zorder=1,
                        label="baseline" if k == 0 else None)
        span = T_us * 5 if a.endtoend else T_us
        ax.set_xlim(-span * 0.01, span * 1.01)
        if a.endtoend:
            for k in range(1, 5):
                ax.axvline(k * T_us, color="#c8ccd2", lw=0.8)
            for k in range(5):
                ax.text((k + 0.5) * T_us, 1046, f"position {k}", ha="center",
                        va="top", fontsize=9, color="#6b7280")
        ax.set_ylim(0, 1080)
        ax.set_xlabel("position k at k*T + delay (us)" if a.endtoend
                      else "genlock delay after the projected vsync (us)")
        ax.set_ylabel("ROI mean (ADU)")
        ax.grid(alpha=0.3)
        # RESERVE THE TOP MARGIN EXPLICITLY. tight_layout() sizes the axes for the
        # title that exists WHEN IT RUNS -- none, because the header is written in
        # the update loop -- so it was being clipped by the window edge.
        fig.subplots_adjust(left=0.065, right=0.985, top=0.90, bottom=0.11)
        hdr = fig.text(0.065, 0.985, "", va="top", ha="left",
                       family="monospace", fontsize=9, linespacing=1.6)
        live = (plt, fig, ax, hdr)

    fh = open(a.out, "w", newline="")
    wcsv = csv.writer(fh)
    wcsv.writerow(["pass", "led", "delay_us", "position", "mean"])
    fh.flush()

    totals = collections.Counter()
    t_start = time.time()
    stop = False
    p = 0
    try:
        while not stop and (a.passes == 0 or p < a.passes):
            p += 1
            for led in leds:
                if stop:
                    break
                wr(ser, R_IMPRGB, COLOURS[led])
                m2 = COLOURS[a.colour2 if a.colour2 else led]
                wr(ser, R_IMPRGB2, m2)
                odd_px = expect(m2, lvl2)
                four_px = expect(COLOURS[led], a.level)
                # WHETHER A MARKER EXISTS AT ALL is decided here, once, and the
                # binning below obeys it. Printing a warning and then running the
                # marker test anyway is what put every frame of an all-black
                # sequence into one position: `tlp == odd_px` matches EVERY frame
                # when the two colours are the same, so all five collapse onto
                # --bright. A uniform sequence has no marker by definition; the
                # ordinal still separates the frames, it just cannot say which
                # one is position 0.
                has_marker = (odd_px != four_px)
                if not has_marker:
                    print(f"  !! every frame transmits the same pixel ({odd_px:06X}), "
                          f"so there is no marker. Binning on the trigger ordinal "
                          f"instead: the five bands are real, the ORIGIN is "
                          f"arbitrary.")
                print(f"  {led}: the four transmit {four_px:06X}, "
                      f"the odd frame transmits {odd_px:06X}")
                time.sleep(0.2)
                rows = []                    # (delay_us, position, mean)
                nbad = nseq = npool = 0
                last_cc = None
                absn = [0]                   # UNWRAPPED trigger count
                anchor = [None]              # absolute count at the last marker
                # One Line2D per (pass, led, position) so a new sweep DRAWS ON TOP
                # of the old ones instead of replacing them. Alpha below 1 makes
                # the overlap of repeated passes visible as density.
                if live:
                    plt, fig, ax, hdr = live
                    lset = [ax.plot([], [], lw=1.1, alpha=0.55,
                                    color=LEDCOL[led])[0] for _ in range(5)]

                for i in range(npts):
                    d_us = i * a.step
                    want = int(round(d_us / TICK_US))
                    set_delay(ser, want)
                    got, ok = wait_delay(ser, want)
                    if not ok:
                        print(f"  !! pass {p} {led}: delay {d_us:.0f} us did not "
                              f"land (register holds {got}); skipping this sweep")
                        totals["failed sweeps"] += 1
                        break
                    ser.reset_input_buffer()
                    collect(ser, a.settle, timeout=8.0)
                    # The register reads and the flush both interrupt the stream,
                    # so the ordinal legitimately jumps here. Restart the
                    # continuity check rather than reporting the seam as a fault.
                    last_cc = None
                    for mean, npx, tlp, cc in collect(ser, a.frames, timeout=12.0):
                        if npx != 256:
                            nbad += 1
                            continue
                        # UNWRAP THE ORDINAL FIRST. It is 8 bits and 256 is not a
                        # multiple of 5, so `cc % 5` shifts its origin at every
                        # wrap -- silently re-labelling every frame 2.1 s in.
                        if last_cc is not None:
                            absn[0] += (cc - last_cc) & 0xFF
                            if ((last_cc + 1) & 0xFF) != cc:
                                nseq += 1
                        last_cc = cc
                        # THE MARKER IS THE COMMANDED FLASH FRAME, so it is
                        # labelled with the position the flash was commanded on.
                        # Anchoring it to 0 would rotate every reading.
                        # THE MARKER IS THE ODD FRAME OUT, whichever sense the
                        # sequence is in. Normally that is the one lit frame
                        # (tlp != 0); inverted, it is the one DARK frame in a lit
                        # field (tlp == 0). Either way it is the position that
                        # was commanded, so the anchor rule is unchanged and only
                        # the test flips.
                        # MATCH THE ODD FRAME'S EXACT PIXEL, not "is it non-black".
                        #
                        # The old test was `tlp != 0`, which silently assumed the
                        # other four frames were BLACK. With two real colours in
                        # the sequence -- RRWRR being the case that exposed it --
                        # every frame is non-black, so every frame looked like the
                        # marker and the whole sweep collapsed into one position.
                        #
                        # Both colours are commanded here, so the pixel each frame
                        # must transmit is known exactly. Matching it identifies
                        # the odd frame with no assumption about the others.
                        if has_marker and tlp == odd_px:
                            anchor[0] = absn[0]
                            pos = a.bright
                        elif has_marker and anchor[0] is not None:
                            pos = (a.bright + absn[0] - anchor[0]) % 5
                        else:
                            # No marker at all -- a uniform sequence (all black,
                            # or all lit under --invert). The five frames are
                            # genuinely indistinguishable, so the ORIGIN is
                            # unknown; the grouping is still exact.
                            pos = absn[0] % 5
                            npool += 1
                        rows.append((d_us, pos, mean))

                    if live:
                        for k in range(5):
                            off = (k * T_us) if a.endtoend else 0.0
                            agg = collections.defaultdict(list)
                            for x, kk, v in rows:
                                if kk == k:
                                    agg[x].append(v)
                            xs = sorted(agg)
                            lset[k].set_data([x + off for x in xs],
                                             [sum(agg[x]) / len(agg[x]) for x in xs])
                        el = (time.time() - t_start) / 60
                        hdr.set_text(
                            f"LED {led:5s} level {a.level}  "
                            + ("dark frame" if a.invert else "flash") +
                            f" on position {a.bright}"
                            f"   exposure {expo_units*EXPO_UNIT_US:.2f} us"
                            f"   frame {T_us:.1f} us ({1e6/T_us:.2f} Hz)"
                            + chr(10)
                            + f"pass {p}"
                            + ("" if a.passes == 0 else f"/{a.passes}")
                            + f"   point {i+1}/{npts}   {el:.1f} min elapsed"
                            f"   npx!=256 {totals['npx']+nbad}"
                            f"   gaps {totals['gaps']+nseq}"
                            f"   failed sweeps {totals['failed sweeps']}")
                        fig.canvas.draw_idle()
                        fig.canvas.flush_events()
                        if not plt.fignum_exists(fig.number):
                            print("\nplot closed -- stopping")
                            stop = True
                            break

                # FLUSH AFTER EVERY SWEEP. A run left going over lunch must not
                # depend on reaching the end to have produced anything.
                wcsv.writerows([(p, led, d, k, v) for d, k, v in rows])
                fh.flush()
                totals["npx"] += nbad
                totals["gaps"] += nseq
                totals["nomarker"] += npool
                totals["frames"] += len(rows)
                print(f"  pass {p} {led:5s}: {len(rows):6d} frames   "
                      f"npx!=256 {nbad}   gaps {nseq}   "
                      f"{(time.time()-t_start)/60:.1f} min elapsed")
    except KeyboardInterrupt:
        print("\ninterrupted -- everything captured so far is already written")
    finally:
        wr(ser, R_ROICTL, 0x00)
        set_delay(ser, 0)
        ser.close()
        fh.close()

    print(f"\nwrote {a.out}  ({totals['frames']} frames over {p} pass(es))")
    print(f"   npx != 256            {totals['npx']}")
    print(f"   trigger-ordinal gaps  {totals['gaps']}")
    print(f"   frames with no marker {totals['nomarker']}   (all-black: ORIGIN arbitrary)")
    print(f"   failed sweeps         {totals['failed sweeps']}")

    if a.plot and live:
        live[1].savefig(a.plot, dpi=130)
        print(f"wrote {a.plot}")

    if live:
        print("\nplot left open -- CLOSE THE WINDOW to exit.")
        live[0].ioff()
        live[0].show()


if __name__ == "__main__":
    main()
